"""定向爬取指定微信公众号的肇庆校区内容(搜狗微信搜索 + 文章内链 BFS)。

链路(为什么必须这么绕):
    1) 搜狗微信搜索(type=2)按多个关键词+分页拿结果卡片。卡片只给
       /link?url=... 跳转链(直接 httpx 打开会 302 到 antispider),
       必须用真浏览器(crawl4ai)过中间页。
    2) 中间页里嵌着一次性签名链 mp.weixin.qq.com/s?src=11&...&signature=...
       —— 用一次就失效,所以不能给用户点。
    3) 文章正文里常有编辑手工插的**永久链接**
       mp.weixin.qq.com/s?__biz=..&mid=..&idx=..&sn=..,
       这种可以直接 httpx 打开(实测 200),而且页面 <meta og:url> 给出
       本文的规范永久链接 → 以它为前沿做 BFS,既能扩量又能拿到可点链接。
    4) 账号历史页(/mp/profile_ext)、合集页(appmsgalbum)都要登录,不可用;
       所以发现只能靠搜狗 + 文章互链。

纯函数(解析/链接归一/相关性)与网络流程分离,便于单测。

用法:
    .venv/Scripts/python.exe scripts/crawl_wx_account.py --dry-run --only 肇庆
    .venv/Scripts/python.exe scripts/crawl_wx_account.py --max-articles 40
    .venv/Scripts/python.exe scripts/crawl_wx_account.py        # 全量
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import random
import re
import sys
import time
from collections import deque
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_CONFIG = ROOT / "scripts" / "wx_account.json"
PAGE_TIMEOUT = 60000
CONSUME_DELAY = 0.9        # 中间页 → 消费签名链的间隔要短(链接时效性)
CONTENT_LIMIT = 4000       # 单条入库正文截断
MIN_CONTENT = 60           # 正文低于此长度视为失败页
BFS_DELAY = 0.6            # 直连永久链接之间的间隔(对 mp 要温和)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
SCRIPT_RE = re.compile(r"<script.*?</script>", re.S | re.I)
# 文章正文里的永久链接(可能被 HTML/JS 双重转义)。反斜杠不能排除:
# 真实页面里是 \x26amp; 形态,排除反斜杠会把链接截断成半截。
PERM_RE = re.compile(r"https?://mp\.weixin\.qq\.com/s\?__biz=[^\s\"'<>]+")
SIG_RE = re.compile(r"https://mp\.weixin\.qq\.com/s\?src=[^\s\"'<>]+")


def _log(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------- 文本工具

def _plain(html: str) -> str:
    return WS_RE.sub(" ", TAG_RE.sub(" ", html)).strip()


def clean_url(url: str) -> str:
    """还原被 HTML/JS 转义的链接,并去掉尾随杂质。"""
    u = (url.replace("\\x26amp;", "&").replace("&amp;", "&")
            .replace("\\x3d", "=").replace("\\u0026", "&"))
    u = u.split('"')[0].split("'")[0].split("\\")[0].split("#")[0]
    return u.strip().rstrip("),.").replace("http://mp.weixin.qq.com",
                                           "https://mp.weixin.qq.com")


def parse_canonical(url: str) -> tuple[str, str, str, str, str]:
    """从永久链接里取 (biz, mid, idx, sn, chksm);不完整返回空元组。

    chksm 是**必需**的:实测 s?__biz=&mid=&idx=&sn=(缺 chksm)只返回空壳页
    (约 17KB、无正文),带 chksm 才是完整文章(3MB+)。所以入库链接必须
    五要素齐全,少一个都不给用户点。
    """
    u = clean_url(url)
    if "mp.weixin.qq.com/s?" not in u:
        return ("", "", "", "", "")

    def g(key: str) -> str:
        m = re.search(rf"[?&]{key}=([^&]*)", u)
        return m.group(1) if m else ""

    # chksm 必须正好 64 位十六进制、sn 必须 32 位。从 HTML 里挖出来时常带
    # 尾巴(实测抓到 76 位的 chksm),多一个字符就打不开(只返回 18KB 空壳页)。
    sn = g("sn")[:32]
    chksm = g("chksm")[:64]
    if not re.fullmatch(r"[0-9a-fA-F]{32}", sn):
        sn = ""
    if not re.fullmatch(r"[0-9a-fA-F]{64}", chksm):
        chksm = ""
    biz, mid, idx = g("__biz"), g("mid"), g("idx")
    if not (biz and mid and idx and sn and chksm):
        return ("", "", "", "", "")
    return (biz, mid, idx, sn, chksm)


def article_key(biz: str, mid: str, idx: str) -> str:
    """文章唯一键:微信一篇文章由 (biz, mid, idx) 唯一确定。"""
    return f"{biz}|{mid}|{idx}" if (biz and mid and idx) else ""


def canonical_url(biz: str, mid: str, idx: str, sn: str, chksm: str) -> str:
    """用户可点的规范永久链接(五要素齐全才打得开,见 parse_canonical)。"""
    return (f"https://mp.weixin.qq.com/s?__biz={biz}&mid={mid}&idx={idx}"
            f"&sn={sn}&chksm={chksm}")


def title_fingerprint(title: str) -> str:
    """标题指纹:去掉空白/零宽字符/emoji 后小写,用于跨来源去重。"""
    t = re.sub(r"[\u200b-\u200f\ufeff\s]+", "", title or "")
    return t.lower()


# ---------------------------------------------------------------- 日期

def _fmt_date(ts: str) -> str:
    try:
        return time.strftime("%Y-%m-%d", time.localtime(int(ts)))
    except (ValueError, OSError, OverflowError):
        return ""


def _date_from_text(text: str) -> str:
    m = re.search(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})", text)
    if not m:
        return ""
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


def parse_published(text: str, html: str = "") -> str:
    """发布时间的多种来源:epoch(最准) → 可见文本 → 页面 var ct。"""
    m = re.search(r"timeConvert\('(\d{9,13})'\)", html or "")
    if m:
        got = _fmt_date(m.group(1))
        if got:
            return got
    got = _date_from_text(text or "")
    if got:
        return got
    m = re.search(r'var\s+ct\s*=\s*"(\d{9,13})"', html or "")
    if m:
        return _fmt_date(m.group(1))
    m = re.search(r'var\s+publish_time\s*=\s*"(\d{9,13})"', html or "")
    return _fmt_date(m.group(1)) if m else ""


# ---------------------------------------------------------------- 搜狗搜索页

def parse_sogou_results(html: str) -> list[dict]:
    """搜狗微信搜索结果卡片:标题/跳转链/摘要/公众号/发布时间。

    公众号名在 span.all-time-y2(旧版是 a.account,两个都兼容)。
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    for li in soup.select("ul.news-list > li"):
        a = li.select_one("h3 a") or li.select_one("a[href*='/link']")
        if a is None:
            continue
        href = a.get("href", "")
        if href.startswith("/link"):
            href = "https://weixin.sogou.com" + href
        if "weixin.sogou.com/link" not in href:
            continue
        title = WS_RE.sub("", TAG_RE.sub("", a.decode_contents() or ""))
        info = li.select_one("p.txt-info") or li.select_one(".txt-info")
        digest = _plain(info.decode_contents() or "") if info else ""
        acc_el = (li.select_one("span.all-time-y2")
                  or li.select_one("a.account") or li.select_one(".account"))
        account = acc_el.get_text(strip=True).strip("| ") if acc_el else ""
        time_el = li.select_one("span.s2")
        raw_time = time_el.get_text(strip=True) if time_el else ""
        raw_time = SCRIPT_RE.sub("", raw_time)
        published = parse_published(raw_time, str(time_el) if time_el else "")
        if title:
            out.append({"title": title, "href": href, "digest": digest,
                        "account": account, "published": published})
    return out


def extract_signature_link(html: str) -> str:
    """从搜狗中间页取一次性签名链(易变,只用于服务端消费)。"""
    m = SIG_RE.search(html or "")
    return clean_url(m.group(0)) if m else ""


# ---------------------------------------------------------------- 文章页

def _content_text(node) -> str:
    for br in node.find_all("br"):
        br.replace_with("\n")
    text = node.get_text("\n", strip=True)
    lines = [re.sub(r"[\u200b-\u200f\ufeff]+", "", ln).strip()
             for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def find_perm_links(html: str, biz: str = "") -> list[str]:
    """正文里的永久链接(编辑手工插的互链),可选按 biz 过滤。

    只在 #js_content 里找:<head> 的 og:url 是本文自己的链接,
    算进来会污染 BFS 前沿。
    """
    h = html or ""
    m = re.search(r'id="js_content"[^>]*>(.*)', h, re.S)
    scope = m.group(1) if m else h
    urls: list[str] = []
    for raw in PERM_RE.findall(scope):
        u = clean_url(raw)
        if biz and f"__biz={biz}" not in u:
            continue
        if parse_canonical(u)[0] and u not in urls:
            urls.append(u)
    return urls


def extract_video_ids(html: str) -> dict:
    """视频线索:视频号(data-mpvid)与腾讯视频(v.qq.com 内嵌)。

    只认 data-mpvid:页面 JS 模板里另有一个静态的 vid=wx0clsqx...,
    泛化匹配会让每篇文章都"含视频"(实测踩过这个坑)。
    """
    h = html or ""
    channels = re.findall(r'data-mpvid="([A-Za-z0-9_\-]{8,})"', h)
    tencent = re.findall(
        r"v\.qq\.com/(?:iframe/player\.html\?vid=|x/page/)([A-Za-z0-9_\-]{8,})",
        h)
    return {"channels": list(dict.fromkeys(channels)),
            "tencent": list(dict.fromkeys(tencent))}


def video_links(videos: dict) -> list[str]:
    """可点视频链接:只有腾讯视频有公开网页地址;视频号只能在原文里看。"""
    return [f"https://v.qq.com/x/page/{v}.html" for v in videos.get("tencent", [])]


def parse_article(html: str) -> dict:
    """微信公众号文章页:标题/公众号/时间/正文/身份/规范链接/互链/视频。"""
    from bs4 import BeautifulSoup
    s = BeautifulSoup(html or "", "html.parser")
    title_el = (s.select_one("#activity-name")
                or s.select_one("h1.rich_media_title"))
    account_el = s.select_one("#js_name") or s.select_one(".profile_nickname")
    time_el = s.select_one("#publish_time")
    cont = s.select_one("#js_content") or s.select_one("div.rich_media_content")

    title = title_el.get_text(strip=True) if title_el else ""
    account = account_el.get_text(strip=True) if account_el else ""
    published = parse_published(
        time_el.get_text(strip=True) if time_el else "", html)

    biz = _var(html, "biz")
    mid = _var(html, "mid")
    idx = _var(html, "idx")
    sn = _var(html, "sn")

    # 直连永久链接时 <meta og:url> 就是本文规范链接(带 sn+chksm);
    # 经搜狗签名链进来时该字段为空——这正是拿不到本文永久链接的根因。
    og = re.search(r'og:url"\s+content="([^"]*)"', html or "")
    canonical = clean_url(og.group(1)) if og and og.group(1).strip() else ""
    if not parse_canonical(canonical)[0]:
        canonical = ""

    return {
        "title": title,
        "account": account,
        "published": published,
        "content": _content_text(cont) if cont else "",
        "biz": biz, "mid": mid, "idx": idx, "sn": sn,
        "canonical": canonical,
        "links": find_perm_links(html, biz),
        "videos": extract_video_ids(html),
    }


def _var(html: str, name: str) -> str:
    m = re.search(rf'var\s+{name}\s*=\s*"([^"]*)"', html or "")
    return m.group(1) if m else ""


# ---------------------------------------------------------------- 过滤/标签

TOPIC_RULES = [
    ("新生入学", ["新生", "报到", "入学", "军训", "迎新", "专升本新生"]),
    ("交通出行", ["公交", "专线", "地铁", "出行", "高铁", "城际", "班车"]),
    ("校园生活", ["食堂", "美食", "宿舍", "饭堂", "超市", "快递", "生活"]),
    ("校区设施", ["图书馆", "教学楼", "实验室", "建设", "封顶", "落成",
                  "揭牌", "开工", "校区"]),
    ("招生录取", ["招生", "录取", "分数线", "报考"]),
    ("学业就业", ["就业", "招聘", "考研", "考试", "实习"]),
    ("校园活动", ["活动", "运动会", "晚会", "典礼", "比赛", "社团", "节"]),
]


def is_relevant(title: str, content: str, keywords: list[str]) -> bool:
    """是否是「肇庆校区」内容。

    关键词只放肇庆一带的地名(肇庆/大旺/四会/鼎湖/北岭/星湖)。
    早期把「广应科」「校区」「松田」也当关键词,结果该号每篇文章都能入库
    (实测 260 条里混杂了大量峨眉武术、征兵、西藏援建类校庆新闻)。
    现在收紧:标题命中地点词直接算;正文命中还需同时出现校区/学校语境。
    """
    if any(k and k in title for k in keywords):
        return True
    if any(k and k in content for k in keywords):
        return any(m in content for m in ("校区", "学校", "大学"))
    return False


def topic_of(title: str, content: str, default: str = "肇庆校区") -> str:
    text = f"{title} {content[:400]}"
    for topic, kws in TOPIC_RULES:
        if any(k in text for k in kws):
            return topic
    return default


# ---------------------------------------------------------------- 入库

def entry_id(key: str) -> str:
    return "wxgz-" + hashlib.md5(key.encode("utf-8")).hexdigest()[:12]


def kb_entry(art: dict, url: str, topic: str, credibility: str,
             tags_extra: list[str] | None = None) -> dict:
    content = (art.get("content") or "")[:CONTENT_LIMIT]
    vids = art.get("videos") or {}
    vlinks = video_links(vids)
    video_note = ""
    if vids.get("channels"):
        video_note = (f"文中含 {len(vids['channels'])} 个视频号视频"
                      f"(打开原文即可播放)")
    if vlinks:
        video_note = (video_note + "; " if video_note else "") + \
            "相关视频:" + " ".join(vlinks)
    body = (f"{art['title']}\n"
            f"来源公众号:{art.get('account','')}\n"
            f"发布:{art.get('published','')}\n"
            f"{content}\n"
            f"原文链接:{url}")
    if video_note:
        body += f"\n视频:{video_note}"
    if not parse_canonical(url)[0]:
        body += MISSING_NOTE
    tags = [topic, "微信公众号文章", "肇庆校区", art.get("account", "")]
    tags = [topic, "微信公众号文章", "肇庆校区", art.get("account", "")]
    tags += [t for t in (tags_extra or []) if t]
    return {
        "id": entry_id(article_key(art["biz"], art["mid"], art["idx"])
                       or title_fingerprint(art["title"])),
        "title": art["title"],
        "content": body,
        "url": url,
        "source": "微信公众号",
        "tags": [t for t in tags if t],
        "credibility": credibility,
        "publish_at": art.get("published", ""),
        "_account": art.get("account", ""),
        # 存文章身份:补全规范链接时按 (mid, idx) 精确匹配,免得标题相似取错
        "_biz": art.get("biz", ""),
        "_mid": art.get("mid", ""),
        "_idx": art.get("idx", ""),
        # 注意 bool(元组) 恒为真:必须取首元素判断
        "_canonical": bool(parse_canonical(url)[0]),
    }


def archive_md(out_dir: Path, entry: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{entry['id']}.md").write_text(
        "---\n"
        f"title: {entry['title']}\n"
        f"account: {entry.get('_account','')}\n"
        f"publish_at: {entry.get('publish_at','')}\n"
        f"source: {entry['source']}\n"
        f"credibility: {entry['credibility']}\n"
        f"url: {entry['url']}\n"
        "---\n\n"
        f"{entry.get('content','')}\n",
        encoding="utf-8")


def rebuild_index(out_dir: Path, entries: list[dict]) -> None:
    items = [{"id": e["id"], "title": e["title"],
              "account": e.get("_account", ""),
              "publish_at": e.get("publish_at", ""),
              "url": e["url"], "file": f"{e['id']}.md",
              "canonical": e.get("_canonical", False)} for e in entries]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.json").write_text(
        json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- 抓取

async def _crawl(crawler, rcfg, url: str) -> str:
    try:
        r = await crawler.arun(url=url, config=rcfg)
    except Exception:
        return ""
    return r.html or ""


def fetch_once(link: str) -> str:
    """一次性消费签名链 / 直连永久链接(httpx 单次 GET)。"""
    import httpx
    try:
        with httpx.Client(follow_redirects=True, timeout=25.0,
                          headers={"User-Agent": UA,
                                   "Referer": "https://weixin.sogou.com/",
                                   "Accept-Language": "zh-CN,zh;q=0.9"}) as c:
            r = c.get(link)
            return r.text if r.status_code == 200 else ""
    except Exception:
        return ""


def ok_article(html: str) -> bool:
    return (len(html or "") > 50000 and "环境异常" not in html
            and "id=\"js_content\"" in html)


def sogou_fallback_url(title: str) -> str:
    """拿不到永久链接时的兜底(永久可点,但多一步):搜狗微信搜本文标题。"""
    return ("https://weixin.sogou.com/weixin?type=2&query="
            + quote(title[:24]))


# ------------------------------------------------- 规范链接补全(百度)

BAIDU = "https://www.baidu.com/s?wd="
MISSING_NOTE = "\n(此条未解析到微信永久链接,链接为搜狗微信入口,多一步点击)"


def extract_canonical_candidates(html: str) -> list[str]:
    """从搜索结果页提取所有 mp.weixin 规范链接(chksm 齐全才留)。"""
    urls: list[str] = []
    for raw in re.findall(
            r"https?://mp\.weixin\.qq\.com/s\?[^\s\"'<>\\]{10,500}", html or ""):
        u = clean_url(raw.replace("&amp;", "&"))
        if parse_canonical(u)[0] and u not in urls:
            urls.append(u)
    return urls


def pick_canonical(cands: list[str], mid: str, idx: str = "") -> str:
    """按 (mid, idx) 精确匹配——标题相似的文章很多,只认身份对得上的。"""
    if not mid:
        return ""
    for u in cands:
        _biz, m, i, _sn, _ck = parse_canonical(u)
        if m == mid and (not idx or i == idx):
            return u
    return ""


class BaiduBlocked(Exception):
    """百度返回安全验证页(触发风控),调用方需退避。"""


def baidu_session():
    """带 cookie 的会话:先访问首页拿 BAIDUID,降低被风控概率。"""
    import httpx
    c = httpx.Client(follow_redirects=True, timeout=25.0,
                     headers={"User-Agent": UA,
                              "Accept-Language": "zh-CN,zh;q=0.9"})
    try:
        c.get("https://www.baidu.com/")
    except Exception:
        pass
    return c


def search_baidu(client, title: str) -> list[str]:
    """百度搜索标题,返回页面里的微信规范链接(百度收录了 sn+chksm)。

    实测只有百度会吐出 mp.weixin 的 sn+chksm 规范链接(360/头条/搜狗网页/
    必应都没有),但百度对机器查询很敏感:连几次就返回 1438 字节的
    「安全验证」页,所以必须慢、且能识别并退避。
    """
    try:
        r = client.get(BAIDU + quote(f'"{title}"'))
    except Exception:
        return []
    if r.status_code != 200 or len(r.text) < 5000 or "安全验证" in r.text:
        raise BaiduBlocked()
    return extract_canonical_candidates(r.text)
def relink_entry(entry: dict, new_url: str) -> None:
    """把兜底链接换成规范链接(正文里的链接行与提示行同步更新)。"""
    old = entry.get("url") or ""
    body = entry.get("content") or ""
    if old and old in body:
        body = body.replace(old, new_url)
    body = body.replace(MISSING_NOTE, "")
    entry["content"] = body
    entry["url"] = new_url
    entry["_canonical"] = True


def same_title(a: str, b: str) -> bool:
    """标题是否同一篇(允许一处包含另一处,但长度不能差太多)。"""
    fa, fb = title_fingerprint(a), title_fingerprint(b)
    if not fa or not fb:
        return False
    if fa == fb:
        return True
    if fa in fb or fb in fa:
        return min(len(fa), len(fb)) / max(len(fa), len(fb)) > 0.6
    return False


def verify_candidate(url: str, title: str) -> dict | None:
    """抓开候选链接,用文章页标题确认就是这一篇(避免取到相似标题的文章)。

    验证成功时顺带返回解析结果——里面带着 (biz, mid, idx),可以回填进条目,
    下次维护就不用再抓取验证了。
    """
    html = fetch_once(url)
    if not ok_article(html):
        return None
    art = parse_article(html)
    return art if same_title(art.get("title", ""), title) else None


def resolve_canonical_for(client, entry: dict) -> str:
    """给一条缺链接的条目找规范永久链接:百度搜标题 → 身份/标题双重校验。"""
    title = entry.get("title", "")
    cands = search_baidu(client, title)
    mid, idx = entry.get("_mid", ""), entry.get("_idx", "")
    if mid:
        hit = pick_canonical(cands, mid, idx)
        if hit:
            return hit
    for u in cands[:4]:                # 老数据没存 mid,靠抓取校验身份
        art = verify_candidate(u, title)
        if art:
            for field, key in (("_biz", "biz"), ("_mid", "mid"),
                               ("_idx", "idx")):
                if not entry.get(field) and art.get(key):
                    entry[field] = art[key]
            return u
    return ""


def backfill_identity(kb: list[dict]) -> int:
    """已有规范链接的条目,身份直接从链接里取。

    直连抓取的页面没有顶层 `var mid = ...`(实测只有搜狗签名链路径才有),
    所以身份信息以链接参数为准。
    """
    n = 0
    for e in kb:
        biz, mid, idx, _sn, _ck = parse_canonical(e.get("url") or "")
        if biz and not e.get("_mid"):
            e["_biz"], e["_mid"], e["_idx"] = biz, mid, idx
            n += 1
    return n


def normalize_links(kb: list[dict]) -> int:
    """把已有规范链接重建成标准形态(修掉历史上 chksm 被多抳的脏数据)。"""
    n = 0
    for e in kb:
        parts = parse_canonical(e.get("url") or "")
        if not parts[0]:
            continue
        clean = canonical_url(*parts)
        if clean != e["url"]:
            relink_entry(e, clean)
            n += 1
    return n


async def resolve_links(args) -> int:
    """给已有知识库里缺规范链接的条目补链接(不重新爬取)。

    可断点续跑:只处理缺规范链接的条目,中途被百度风控打断也不丢进度。
    """
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    kb_path = ROOT / cfg.get("output_kb", "kb/wx-gzasc.json")
    out_dir = ROOT / cfg.get("out_dir", "crawled/weixin.sogou.com/gzasc")
    delay = float(cfg.get("baidu_delay", 8.0))
    if not kb_path.exists():
        _log(f"知识库不存在: {kb_path}")
        return 1
    kb = json.loads(kb_path.read_text(encoding="utf-8"))
    fixed_urls = normalize_links(kb)
    if fixed_urls:
        _log(f"归一化已有链接 {fixed_urls} 条(chksm 补齐到 64 位)")
    filled = backfill_identity(kb)
    if filled:
        _log(f"从已有链接回填文章身份 {filled} 条")
    todo = [e for e in kb if not e.get("_canonical")]
    if args.limit:
        todo = todo[:args.limit]
    _log(f"待补链 {len(todo)} 条(库共 {len(kb)} 条),间隔约 {delay}s")
    client = baidu_session()
    fixed = miss = 0
    backoff = [60, 120, 300]           # 被风控后的退避阶梯
    for i, e in enumerate(todo, 1):
        url = ""
        for attempt in range(len(backoff) + 1):
            try:
                url = resolve_canonical_for(client, e)
                break
            except BaiduBlocked:
                if attempt >= len(backoff):
                    _log("  百度持续风控,保存进度后退出(可稍后重跑续上)")
                    url = ""
                    break
                wait = backoff[attempt]
                _log(f"  [风控] 等 {wait}s 再试 …")
                await asyncio.sleep(wait)
        if url:
            relink_entry(e, url)
            fixed += 1
            _log(f"  [{i}/{len(todo)}] OK {e['title'][:34]}")
        else:
            miss += 1
            _log(f"  [{i}/{len(todo)}] -- 未找到 {e['title'][:28]}")
        if i % 5 == 0:                 # 每条几条就落盘,被打断也不丢进度
            kb_path.write_text(json.dumps(kb, ensure_ascii=False, indent=1),
                               encoding="utf-8")
        await asyncio.sleep(delay * (0.8 + 0.4 * random.random()))
    kb_path.write_text(json.dumps(kb, ensure_ascii=False, indent=1),
                       encoding="utf-8")
    for e in kb:
        archive_md(out_dir, e)
    rebuild_index(out_dir, kb)
    canon = sum(1 for e in kb if e.get("_canonical"))
    _log(f"补链完成: 新补 {fixed}/{len(todo)};库共 {len(kb)} 条,"
         f"规范链接 {canon}/{len(kb)}")
    return 0


# ---------------------------------------------------------------- 主流程

async def fix_links(args) -> int:
    """只做链接归一化与身份回填(不联网、不跑百度)。"""
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    kb_path = ROOT / cfg.get("output_kb", "kb/wx-gzasc.json")
    out_dir = ROOT / cfg.get("out_dir", "crawled/weixin.sogou.com/gzasc")
    if not kb_path.exists():
        _log(f"知识库不存在: {kb_path}")
        return 1
    kb = json.loads(kb_path.read_text(encoding="utf-8"))
    fixed = normalize_links(kb)
    filled = backfill_identity(kb)
    kb_path.write_text(json.dumps(kb, ensure_ascii=False, indent=1),
                       encoding="utf-8")
    for e in kb:
        archive_md(out_dir, e)
    rebuild_index(out_dir, kb)
    canon = sum(1 for e in kb if e.get("_canonical"))
    _log(f"归一化 {fixed} 条,回填身份 {filled} 条;库共 {len(kb)} 条,"
         f"规范链接 {canon}/{len(kb)}")
    return 0


async def run(args) -> int:
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    targets = {t["name"]: t for t in cfg.get("target_accounts", [])}
    queries = cfg.get("queries", [])
    if args.only:
        needles = [s.strip() for s in args.only.split(",") if s.strip()]
        queries = [q for q in queries
                   if any(n in q["keyword"] for n in needles)]
        if not queries:
            _log(f"没有匹配的关键词: {args.only}")
            return 1
    rel_kws = cfg.get("relevance_keywords", [])
    biz = cfg.get("school_biz", "")
    max_articles = args.max_articles or int(cfg.get("max_articles", 120))
    max_bfs = int(cfg.get("max_bfs_pages", 200))
    pages = args.pages or int(cfg.get("max_pages_per_query", 5))
    q_delay = float(cfg.get("query_delay", 2.5))
    kb_path = ROOT / cfg.get("output_kb", "kb/wx-gzasc.json")
    out_dir = ROOT / cfg.get("out_dir", "crawled/weixin.sogou.com/gzasc")

    try:
        from crawl4ai import (AsyncWebCrawler, BrowserConfig, CacheMode,
                              CrawlerRunConfig)
    except ImportError:
        _log("错误: 未安装 crawl4ai")
        return 1

    _log(f"定向爬取 {list(targets)} 的肇庆校区内容"
         f"({len(queries)} 个关键词 × ≤{pages} 页,上限 {max_articles} 篇)")

    bcfg = BrowserConfig(headless=True, verbose=False)
    rcfg = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, page_timeout=PAGE_TIMEOUT,
                            wait_until="domcontentloaded",
                            delay_before_return_html=3.5, verbose=False)

    # ---------- 阶段 1:搜狗发现(只留目标公众号) ----------
    cards: dict[str, dict] = {}
    seen_href: set[str] = set()
    searches = block = 0
    async with AsyncWebCrawler(config=bcfg) as crawler:
        for q in queries:
            kw, topic = q["keyword"], q.get("topic", "肇庆校区")
            got = 0
            for page in range(1, pages + 1):
                url = ("https://weixin.sogou.com/weixin?type=2&ie=utf8&query="
                       + quote(kw) + f"&page={page}")
                html = await _crawl(crawler, rcfg, url)
                searches += 1
                if "antispider" in html or len(html) < 5000:
                    _log(f"  [{kw} p{page}] 被拦(antispider),歇 20s")
                    block += 1
                    await asyncio.sleep(20)
                    break
                items = parse_sogou_results(html)
                if not items:
                    break
                fresh = 0
                for it in items:
                    if it["account"] not in targets:
                        continue
                    fp = title_fingerprint(it["title"])
                    if fp in cards or it["href"] in seen_href:
                        continue
                    seen_href.add(it["href"])
                    it.update({"topic": topic, "query": kw, "fp": fp})
                    cards[fp] = it
                    fresh += 1
                got += fresh
                if fresh == 0 and page > 1:
                    break
                await asyncio.sleep(q_delay)
            _log(f"  [{kw}] 命中目标号 {got} 篇(累计 {len(cards)})")

        if not cards:
            _log("搜狗未发现目标公众号文章(可能被风控),稍后重试")
            return 1

        # ---------- 阶段 2:逐篇取正文(搜狗签名链) ----------
        arts: dict[str, dict] = {}
        seeds: list[str] = []
        picked = list(cards.values())[:max_articles]
        _log(f"阶段2:取 {len(picked)} 篇正文 …")
        ok = fail = 0
        for i, card in enumerate(picked, 1):
            mid_html = await _crawl(crawler, rcfg, card["href"])
            sig = extract_signature_link(mid_html)
            if not sig:
                fail += 1
                _log(f"  [{i}/{len(picked)}] 中间页无签名链: {card['title'][:26]}")
                await asyncio.sleep(1.5)
                continue
            await asyncio.sleep(CONSUME_DELAY)
            html = fetch_once(sig)
            if not ok_article(html):
                fail += 1
                _log(f"  [{i}/{len(picked)}] 正文失败: {card['title'][:26]}")
                await asyncio.sleep(1.5)
                continue
            art = parse_article(html)
            if not art["title"]:
                art["title"] = card["title"]
            if not art["account"]:
                art["account"] = card["account"]
            if not art["published"]:
                art["published"] = card["published"]
            key = article_key(art["biz"], art["mid"], art["idx"]) or card["fp"]
            art["query"] = card["query"]
            art["topic"] = card["topic"]
            arts[key] = art
            seeds += art["links"]
            ok += 1
            _log(f"  [{i}/{len(picked)}] + {art['title'][:34]} | "
                 f"{len(art['content'])}字 | 互链{len(art['links'])} | "
                 f"规范链{'有' if parse_canonical(art['canonical'])[0] else '无'}")
            await asyncio.sleep(1.0)

        _log(f"阶段2 完成:成功 {ok} 篇,失败 {fail} 次")

        # ---------- 阶段 3:BFS 永久链接(直连,可扩量+拿规范链) ----------
        queue = deque(u for u in dict.fromkeys(seeds)
                      if biz and f"__biz={biz}" in u or not biz)
        visited: set[str] = set()
        bfs_ok = 0
        _log(f"阶段3:BFS 前沿 {len(queue)} 条永久链接 …")
        while queue and bfs_ok < max_bfs:
            link = queue.popleft()
            biz_q, mid_q, idx_q, _sn, _chksm = parse_canonical(link)
            key_q = article_key(biz_q, mid_q, idx_q)
            if not key_q or key_q in visited:
                continue
            visited.add(key_q)
            html = fetch_once(link)
            if not ok_article(html):
                await asyncio.sleep(BFS_DELAY)
                continue
            art = parse_article(html)
            if not art["title"]:
                await asyncio.sleep(BFS_DELAY)
                continue
            art["query"] = ""
            art["topic"] = topic_of(art["title"], art["content"])
            if key_q not in arts:
                arts[key_q] = art
                bfs_ok += 1
                _log(f"  BFS + {art['title'][:36]} | {len(art['content'])}字")
            else:
                # 补上搜狗路径拿不到的规范链接
                if not arts[key_q].get("canonical"):
                    arts[key_q]["canonical"] = (art["canonical"] or link)
            queue.extend(u for u in art["links"]
                         if not biz or f"__biz={biz}" in u)
            await asyncio.sleep(BFS_DELAY)
        _log(f"阶段3 完成:BFS 新增 {bfs_ok} 篇,访问 {len(visited)} 条")

    # ---------- 阶段 4:相关性过滤 + 入库 ----------
    entries: list[dict] = []
    dropped = 0
    no_link = 0
    for key, art in arts.items():
        if not art.get("title") or not art.get("content"):
            dropped += 1
            continue
        if len(art["content"]) < MIN_CONTENT:
            dropped += 1
            continue
        if not is_relevant(art["title"], art["content"], rel_kws):
            dropped += 1
            continue
        # 规范链接优先(五要素齐全才能给用户点);拿不到才退回搜狗入口
        url = art.get("canonical") or ""
        if not parse_canonical(url)[0]:
            url = sogou_fallback_url(art["title"])
            no_link += 1
        cred = targets.get(art.get("account", ""), {}).get("credibility", "B")
        topic = art.get("topic") or topic_of(art["title"], art["content"])
        entries.append(kb_entry(art, url, topic, cred,
                                [art.get("query", "")]))

    if args.dry_run:
        _log("---- dry-run 结果 ----")
        for e in entries[:30]:
            _log(f"  {e['publish_at']} [{e['credibility']}] {e['title'][:40]}")
            _log(f"      {e['url'][:110]}")
        _log(f"dry-run: 入库候选 {len(entries)} 篇"
             f"(过滤 {dropped},无规范链 {no_link},搜索 {searches} 次,"
             f"被拦 {block})")
        return 0

    kb: list[dict] = []
    if kb_path.exists():
        try:
            kb = json.loads(kb_path.read_text(encoding="utf-8"))
        except Exception:
            kb = []
    old_ids = {e.get("id") for e in kb}
    merged = {e.get("id"): e for e in kb}
    added = 0
    for e in entries:
        if e["id"] not in old_ids:
            added += 1
        merged[e["id"]] = e
    out = list(merged.values())
    out.sort(key=lambda e: (e.get("publish_at") or "", e["title"]))
    kb_path.parent.mkdir(parents=True, exist_ok=True)
    kb_path.write_text(json.dumps(out, ensure_ascii=False, indent=1),
                       encoding="utf-8")
    for e in entries:
        archive_md(out_dir, e)
    rebuild_index(out_dir, out)
    canon = sum(1 for e in entries if e.get("_canonical"))
    _log(f"完成: 本次 {len(entries)} 篇(新增 {added}),规范链接 {canon}/"
         f"{len(entries)};库共 {len(out)} 条 → {kb_path}")
    if cfg.get("resolve_missing_via_baidu", True) and canon < len(entries):
        _log("自动补链:对缺规范链接的条目跑一次百度解析 …")
        await resolve_links(args)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="定向爬取公众号的肇庆校区内容")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--only", default="", help="只跑匹配的关键词,逗号分隔")
    ap.add_argument("--pages", type=int, default=0, help="每个关键词翻几页")
    ap.add_argument("--max-articles", type=int, default=0, help="最多取几篇正文")
    ap.add_argument("--dry-run", action="store_true", help="只列候选,不入库")
    ap.add_argument("--resolve-links", action="store_true",
                    help="不爬取,只给库里缺规范链接的条目补链接(百度)")
    ap.add_argument("--fix-links", action="store_true",
                    help="只做链接归一化与身份回填(不联网)")
    ap.add_argument("--limit", type=int, default=0, help="补链时最多处理几条")
    args = ap.parse_args()
    if args.fix_links:
        return asyncio.run(fix_links(args))
    return asyncio.run(resolve_links(args) if args.resolve_links
                       else run(args))


if __name__ == "__main__":
    sys.exit(main())
