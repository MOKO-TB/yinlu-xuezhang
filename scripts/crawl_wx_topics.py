"""按关键词爬微信公众号文章(经搜狗微信搜索),生成校园生活知识库。

链路(crawl4ai 渲染 + httpx 跟进,两层都必须真浏览器拿跳转页):
    1. crawl4ai 渲染 weixin.sogou.com/weixin?type=2&query=<kw> 搜索结果页
       (httpx 直接抓搜索页会命中 antispider,crawl4ai 真浏览器可过)
    2. 从结果卡片拿 /link?url=... 跳转链 + 标题/摘要/公众号名
    3. crawl4ai 打开跳转链,得到"微信公众平台"中间页,页面里嵌着
       带签名的 mp.weixin.qq.com/s?src=11&timestamp=...&signature=... 真实链接
       (此链接一次性使用,且需尽快消费,过期/复用都会返回"环境异常")
    4. httpx 立刻 GET 该链接(带 UA/Referer),拿到文章完整 HTML,
       解析 #activity-name 标题、#js_name 公众号、#js_content 正文
    5. 入库 kb/wx-articles.json(credibility=A,带来源网址/公众号/发布时间)

小红书未登录全被墙(搜索页登录墙/笔记页 302),公众号文章是校园周边
生活类内容(食堂美食/公交专线/景点攻略)的优质替代源,且内容多为官方
发布,比小红书个人笔记更可靠。

用法:
    .venv/Scripts/python.exe scripts/crawl_wx_topics.py            # 全部关键词
    .venv/Scripts/python.exe scripts/crawl_wx_topics.py --only 美食
    .venv/Scripts/python.exe scripts/crawl_wx_topics.py --limit 3  # 每词最多3篇
    .venv/Scripts/python.exe scripts/crawl_wx_topics.py --dry-run  # 只列结果
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_CONFIG = ROOT / "scripts" / "wx_topics.json"
PAGE_DELAY = 2.0          # 搜狗对频次敏感,词与词之间必须歇
CONSUME_DELAY = 0.8       # 中间页到正文消费的间隔要短(链接时效性)
CONTENT_LIMIT = 4000      # 单条入库正文截断
MIN_CONTENT = 120         # 正文低于此字数不入库(导航页/失败页)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def _log(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------- 解析

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def _plain(html: str) -> str:
    return WS_RE.sub(" ", TAG_RE.sub(" ", html)).strip()


def parse_sogou_results(html: str) -> list[dict]:
    """搜狗搜索结果卡片:标题/跳转链/摘要/公众号名/发布时间。"""
    from bs4 import BeautifulSoup
    s = BeautifulSoup(html, "html.parser")
    out = []
    for li in s.select("ul.news-list > li"):
        a = li.select_one("h3 a") or li.select_one("a[href*='/link']")
        if a is None:
            continue
        href = a.get("href", "")
        if href.startswith("/link"):
            href = "https://weixin.sogou.com" + href
        if "weixin.sogou.com/link" not in href:
            continue
        title = TAG_RE.sub("", a.decode_contents() or "").strip()
        info = li.select_one(".txt-info")
        digest = _plain(info.decode_contents() or "") if info else ""
        account = li.select_one(".account")
        account_name = account.get_text(strip=True).strip("| ") if account else ""
        span_time = li.select_one(".s2")
        published = span_time.get_text(strip=True) if span_time else ""
        if title:
            out.append({"title": title, "href": href, "digest": digest,
                        "account": account_name, "published": published})
    return out


def extract_real_link(intermediate_html: str) -> str:
    """从搜狗跳转中间页提取一次性 mp.weixin 签名链接。"""
    m = re.search(r"https://mp\.weixin\.qq\.com/s\?src=[^\s\"']+", intermediate_html)
    if not m:
        return ""
    return m.group(0).replace("\\x26amp;", "&").replace("&amp;", "&")


def extract_permanent_link(article_html: str) -> str:
    """尝试从文章页提取本文永久短链(mp.weixin.qq.com/s/xxx)。

    搜狗给的签名链接是一次性的;文章页 JS 里 msg_link/sn 多为空
    (未登录态),第一个 /s/ 短链常是"争议提示"横幅的固定链接,不能
    直接用——只有当短链恰好出现在 msg_link 变量里才算本文的。
    拿不到返回空串,调用方退回搜狗搜索入口链接(永久可点)。
    """
    m = re.search(
        r"var\s+msg_link\s*=\s*\"(https?://mp\.weixin\.qq\.com/s/[A-Za-z0-9_\-]+)\"",
        article_html)
    return m.group(1) if m else ""


def parse_wx_article(html: str) -> dict:
    """微信公众号文章页:标题/作者公众号/发布时间/正文文本。"""
    from bs4 import BeautifulSoup
    s = BeautifulSoup(html, "html.parser")
    title_el = s.select_one("#activity-name") or s.select_one("h1.rich_media_title")
    account_el = s.select_one("#js_name") or s.select_one(".profile_nickname")
    time_el = s.select_one("#publish_time")
    cont = s.select_one("#js_content") or s.select_one("div.rich_media_content")
    title = title_el.get_text(strip=True) if title_el else ""
    account = account_el.get_text(strip=True) if account_el else ""
    published = time_el.get_text(strip=True) if time_el else ""
    if not cont:
        return {"title": title, "account": account, "published": published,
                "content": ""}
    # 段落拼接:块级元素换行,保持可读结构
    for br in cont.find_all("br"):
        br.replace_with("\n")
    text = cont.get_text("\n", strip=True)
    text = WS_RE.sub(" ", text.replace("\n", "\n")).strip()
    lines = [ln.strip() for ln in text.splitlines()]
    content = "\n".join(ln for ln in lines if ln)
    return {"title": title, "account": account, "published": published,
            "content": content}


# ---------------------------------------------------------------- 抓取

async def _crawl(crawler, url: str, cfg) -> str:
    r = await crawler.arun(url=url, config=cfg)
    return r.html or ""


def _fetch_once(link: str) -> str:
    """一次性消费签名链接:httpx 单次 GET(复用会触发环境异常)。"""
    import httpx
    try:
        with httpx.Client(follow_redirects=True, timeout=25.0,
                          headers={"User-Agent": UA, "Referer": "https://weixin.sogou.com/",
                                   "Accept-Language": "zh-CN,zh;q=0.9"}) as c:
            r = c.get(link)
            return r.text if r.status_code == 200 else ""
    except Exception:
        return ""


# ---------------------------------------------------------------- 入库

def _entry_id(title: str, account: str) -> str:
    import hashlib
    key = f"{account}|{title}".encode("utf-8")
    return "wx-" + hashlib.md5(key).hexdigest()[:12]


def _kb_entry(e: dict, topic: str, keyword: str, link: str) -> dict:
    content = e["content"][:CONTENT_LIMIT]
    return {
        "id": _entry_id(e["title"], e["account"]),
        "title": e["title"],
        "content": (
            f"{e['title']}\n"
            f"来源公众号:{e['account']}\n"
            f"{content}\n"
            f"原文链接:{link}"
        ),
        "url": link,
        "source": "微信公众号",
        "tags": [topic, "微信公众号文章", keyword, "文章推荐"],
        "credibility": "A",
        "publish_at": e.get("published", ""),
        "_account": e.get("account", ""),
        "_content": content,
    }


def _archive_md(out_dir: Path, e: dict, link: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"{e['id']}.md"
    p.write_text(
        "---\n"
        f"title: {e['title']}\n"
        f"account: {e.get('account','')}\n"
        f"publish_at: {e.get('published','')}\n"
        f"url: {link}\n"
        "---\n\n"
        f"{e.get('content','')}\n",
        encoding="utf-8",
    )


def _rebuild_index(out_dir: Path, entries: list[dict]) -> None:
    import json as _json
    items = []
    for e in entries:
        items.append({"id": e["id"], "title": e["title"],
                      "account": e.get("_account", ""),
                      "publish_at": e.get("publish_at", ""),
                      "url": e["url"], "file": f"{e['id']}.md"})
    (out_dir / "index.json").write_text(
        _json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- 主流程

async def run(args) -> int:
    cfg_path = Path(args.config)
    site_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    keywords = site_cfg.get("keywords", [])
    if args.only:
        needles = [s.strip() for s in args.only.split(",") if s.strip()]
        keywords = [k for k in keywords
                    if any(n in k["keyword"] for n in needles)]
        if not keywords:
            _log(f"没有匹配的关键词: {args.only}")
            return 1
    limit = args.limit or int(site_cfg.get("max_articles_per_keyword", 3))
    kb_path = ROOT / site_cfg.get("output_kb", "kb/wx-articles.json")
    out_dir = ROOT / site_cfg.get("out_dir", "crawled/wx/school-qa")

    try:
        from crawl4ai import (AsyncWebCrawler, BrowserConfig, CacheMode,
                              CrawlerRunConfig)
    except ImportError:
        _log("错误: 未安装 crawl4ai")
        return 1

    # 已有库(增量更新)
    kb: list[dict] = []
    if kb_path.exists() and not args.dry_run:
        try:
            kb = json.loads(kb_path.read_text(encoding="utf-8"))
        except Exception:
            kb = []
    _log(f"开始爬微信公众号({len(keywords)} 个关键词,每词上限 {limit} 篇)")

    bcfg = BrowserConfig(headless=True, verbose=False)
    rcfg = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, page_timeout=60000,
                            wait_until="domcontentloaded",
                            delay_before_return_html=4.0, verbose=False)
    ok = fail = 0
    async with AsyncWebCrawler(config=bcfg) as crawler:
        for kw_cfg in keywords:
            kw = kw_cfg["keyword"]
            topic = kw_cfg.get("topic", "校园周边生活")
            _log(f"  [关键词] {kw} → 主题 {topic}")
            url = "https://weixin.sogou.com/weixin?type=2&query=" + quote(kw)
            html = await _crawl(crawler, url, rcfg)
            if "antispider" in html or len(html) < 5000:
                _log("    搜索页被拦(antispider),跳过该关键词,歇 20s")
                time.sleep(20)
                continue
            cards = parse_sogou_results(html)
            _log(f"    搜索结果 {len(cards)} 条")
            got = 0
            for card in cards:
                if got >= limit:
                    break
                # 1) 跟进跳转链拿中间页
                mid_html = await _crawl(crawler, card["href"], rcfg)
                real = extract_real_link(mid_html)
                if not real:
                    _log(f"    - 中间页无签名链接: {card['title'][:30]}")
                    fail += 1
                    continue
                await asyncio.sleep(CONSUME_DELAY)
                # 2) 一次性消费签名链接
                art_html = _fetch_once(real)
                if len(art_html) < 50000 or "环境异常" in art_html:
                    _log(f"    - 正文获取失败: {card['title'][:30]}")
                    fail += 1
                    continue
                art = parse_wx_article(art_html)
                if not art["content"] or len(art["content"]) < MIN_CONTENT:
                    _log(f"    - 正文太短,跳过: {card['title'][:30]}")
                    fail += 1
                    continue
                if not art["title"]:
                    art["title"] = card["title"]
                if not art["account"]:
                    art["account"] = card.get("account", "")
                # 入库 URL:优先本文永久短链;拿不到则用搜狗搜索入口链接
                # (签名链接是一次性的,直接给用户点会"环境异常")
                perm = extract_permanent_link(art_html)
                if perm:
                    link = perm
                else:
                    from urllib.parse import quote as _q
                    link = ("https://weixin.sogou.com/weixin?type=2&query="
                            + _q(card["title"][:20]))
                entry = _kb_entry(art, topic, kw, link)
                if not args.dry_run:
                    kb = [e for e in kb if e["id"] != entry["id"]]
                    kb.append(entry)
                    _archive_md(out_dir, entry, link)
                got += 1
                ok += 1
                _log(f"    + {art['title'][:44]} | {art['account']} | "
                     f"{len(art['content'])}字 | 本文永久链接:{'有' if perm else '无(存搜狗入口)'}")
                await asyncio.sleep(1.2)
            if got == 0:
                _log("    该关键词 0 篇成功")
            time.sleep(PAGE_DELAY)

    if args.dry_run:
        _log(f"dry-run 完成: 成功 {ok} 篇,失败 {fail} 次(未入库)")
        return 0

    kb_path.parent.mkdir(parents=True, exist_ok=True)
    kb_path.write_text(json.dumps(kb, ensure_ascii=False, indent=1),
                       encoding="utf-8")
    _rebuild_index(out_dir, kb)
    _log(f"完成: 新抓取 {ok} 篇(失败 {fail}),知识库共 {len(kb)} 条 → {kb_path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="按关键词爬微信公众号文章")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--only", default="",
                    help="只抓匹配的条目,逗号分隔多个子串,如: --only 美食,景点")
    ap.add_argument("--limit", type=int, default=0,
                    help="每个关键词最多几篇(默认取配置)")
    ap.add_argument("--dry-run", action="store_true", help="只列结果,不入库")
    return asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
