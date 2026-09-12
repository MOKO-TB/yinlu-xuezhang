"""按关键词爬抖音搜索页,生成带视频链接的问答知识库(与 B 站爬虫互补)。

抖音游客态搜索会撞验证码中间页,所以走 playwright 独立配置
(.automation/dy-profile),首次跑 --login 扫码一次,登录态持久保存;
之后 headless 跑搜索。解析靠 RENDER_DATA(初始状态 JSON,URL编码):
    视频 id/标题 desc/作者 nickname/统计数据都在里面,比解析混淆
    class 的 DOM 稳定;DOM 里 a[href*=/video/] 兜底。
只收 /video/<纯数字id> 的普通视频,直播/合集/话题页排除。

结果:
    kb/douyin-videos.json   知识库条目(id=douyin-<vid>,credibility=B)
    crawled/www.douyin.com/school-qa-videos/*.md + index.json

用法:
    .venv/Scripts/python.exe scripts/crawl_douyin_topics.py --login  # 首次扫码
    .venv/Scripts/python.exe scripts/crawl_douyin_topics.py          # 全部关键词
    .venv/Scripts/python.exe scripts/crawl_douyin_topics.py --only 肇庆,学习计划
    .venv/Scripts/python.exe scripts/crawl_douyin_topics.py --dry-run --limit 3
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote_plus, unquote

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_CONFIG = ROOT / "scripts" / "douyin_topics.json"
PROFILE_DIR = ROOT / ".automation" / "dy-profile"
LOGIN_URL = "https://www.douyin.com/"
SEARCH_URL = "https://www.douyin.com/search/{kw}?type=video"
PAGE_DELAY = 2.0          # 关键词间隔:抖音风控比 B 站严,慢即是快
PAGE_WAIT = 8.0           # 搜索页渲染等待(RENDER_DATA 注入完成)
LOGIN_WAIT_SECONDS = 300
MIN_TITLE_LEN = 6
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def _log(msg: str) -> None:
    print(msg, flush=True)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


# ---------------------------------------------------------------- 解析

def parse_render_data(html: str) -> list[dict]:
    """从 RENDER_DATA 初始状态解视频列表。抖音把初始数据 URL 编码后
    塞在 <script id="RENDER_DATA"> 里,比 DOM 混淆 class 稳定。"""
    m = re.search(r'<script id="RENDER_DATA"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return []
    data = unquote(m.group(1))
    items: list[dict] = []
    seen: set[str] = set()
    # 每个 videoId...desc...statistics 组合视为一条;desc 出现在 item 附近
    for m2 in re.finditer(r'"desc":"([^"]{4,300})"', data):
        start = m2.start()
        chunk = data[max(0, start - 3000):start + 3000]
        vid_m = (re.search(r'"video_id":"([0-9a-z]{15,32})"', chunk)
                 or re.search(r'"vid":"([0-9a-z]{15,32})"', chunk))
        if not vid_m:
            continue
        vid = vid_m.group(1)
        if vid in seen:
            continue
        nick_m = re.search(r'"nickname":"([^"]{1,40})"', chunk)
        stat = re.search(r'"digg_count":(\d+)', chunk)
        comment = re.search(r'"comment_count":(\d+)', chunk)
        title = _clean(m2.group(1).encode().decode("unicode_escape", errors="ignore"))
        if len(title) < MIN_TITLE_LEN:
            continue
        seen.add(vid)
        items.append({
            "vid": vid,
            "url": f"https://www.douyin.com/video/{vid}",
            "title": title[:120],
            "owner": _clean(nick_m.group(1)) if nick_m else "",
            "digg": int(stat.group(1)) if stat else 0,
            "comments": int(comment.group(1)) if comment else 0,
        })
    return items


def parse_dom_fallback(html: str) -> list[dict]:
    """RENDER_DATA 缺失时的 DOM 兜底:a[href*=/video/]+img alt 标题。"""
    items: list[dict] = []
    seen: set[str] = set()
    for m in re.finditer(r'href="//www\.douyin\.com/video/(\d+)"', html):
        vid = m.group(1)
        if vid in seen:
            continue
        start = html.rfind("<a ", 0, m.start())
        card = html[start:m.start()] + html[m.start():m.start() + 3000]
        alt = re.search(r'alt="([^"]{6,120})"', card)
        if not alt:
            continue
        seen.add(vid)
        items.append({
            "vid": vid,
            "url": f"https://www.douyin.com/video/{vid}",
            "title": _clean(alt.group(1))[:120],
            "owner": "",
            "digg": 0,
            "comments": 0,
        })
    return items


def parse_search_html(html: str) -> list[dict]:
    """DOM 配对解析:每个 /video/ href 与最近的 img alt(标题)配对。

    RENDER_DATA 方案不可靠:侧栏推荐模块的 desc 会和正卡 vid 错位,
    且搜索被降级时 RENDER_DATA 里根本没有结果数据。"""
    items: list[dict] = []
    seen: set[str] = set()
    for m in re.finditer(r'href="//www\.douyin\.com/video/(\d{15,25})"', html):
        vid = m.group(1)
        if vid in seen:
            continue
        window = html[m.start():m.start() + 2500]
        alt = re.search(r'alt="([^"]{6,200})"', window)
        if not alt:                      # alt 在 href 之前:回看最近的 alt
            back = html[max(0, m.start() - 2500):m.start()]
            alt = list(re.finditer(r'alt="([^"]{6,200})"', back))
            alt = alt[-1] if alt else None
        if not alt:
            continue
        seen.add(vid)
        title = _clean(alt.group(1)
                       .replace("&quot;", '"').replace("&amp;", "&"))[:120]
        items.append({
            "vid": vid,
            "url": f"https://www.douyin.com/video/{vid}",
            "title": title,
            "owner": "",
            "digg": 0,
            "comments": 0,
        })
    return items


def _bigrams(s: str) -> set[str]:
    s = re.sub(r"\s+", "", s)
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) >= 2 else {s}


def filter_relevant(items: list[dict], keyword: str) -> list[dict]:
    """只留标题与关键词有二元组重叠的条目。

    抖音搜索被风控时会静默降级成推荐流(页面正常但结果全是无关热门视频),
    不过滤就会把垃圾数据灌进知识库。"""
    need = _bigrams(keyword)
    out = []
    for v in items:
        if need & _bigrams(v["title"]):
            out.append(v)
    return out


def video_entry(video: dict, topic: str, keyword: str) -> dict:
    lines = [f"视频推荐:{video['title']}",
             f"观看地址:{video['url']}"]
    if video.get("owner"):
        stats = []
        if video.get("digg"):
            stats.append(f"{video['digg']}赞")
        if video.get("comments"):
            stats.append(f"{video['comments']}评论")
        lines.append(f"作者:{video['owner']}" + (f"({','.join(stats)})" if stats else ""))
    lines.append(f"主题:{topic};来源:抖音搜索「{keyword}」前列结果,适合直接观看。")
    return {
        "id": f"douyin-{video['vid']}",
        "title": video["title"],
        "content": "\n".join(lines),
        "url": video["url"],
        "source": "抖音",
        "tags": [topic, "抖音视频", keyword, "视频推荐"],
        "credibility": "B-视频经验",
    }


def _write_video_kb(path: Path, entries: list[dict]) -> int:
    """按 id 去重合并(与 crawl_bili_topics 同款逻辑)。"""
    existing: list[dict] = []
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                existing = [e for e in data if isinstance(e, dict)]
        except (OSError, json.JSONDecodeError):
            existing = []
    by_id = {e.get("id"): e for e in existing}
    written = 0
    for e in entries:
        old = by_id.get(e["id"])
        if old is not None:
            merged = list(dict.fromkeys(list(old.get("tags") or []) + e["tags"]))
            old.update({k: v for k, v in e.items() if k != "id"})
            old["tags"] = merged
        else:
            by_id[e["id"]] = e
            existing.append(e)
        written += 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(existing, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return written


_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.S)


def _rebuild_archive_index(archive_dir: Path, site: str = "") -> int:
    rows: list[dict] = []
    for f in sorted(archive_dir.glob("*.md")):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        meta: dict[str, str] = {}
        m = _FRONTMATTER_RE.match(text)
        if m:
            for line in m.group(1).splitlines():
                key, _, value = line.partition(":")
                meta[key.strip()] = value.strip()
        rows.append({"topic": meta.get("topic", ""), "keyword": meta.get("keyword", ""),
                     "title": meta.get("title", ""), "url": meta.get("url", ""),
                     "owner": meta.get("owner", ""), "file": f.name})
    rows.sort(key=lambda r: (r["topic"], r["keyword"], r["file"]))
    archive_dir.mkdir(parents=True, exist_ok=True)
    (archive_dir / "index.json").write_text(
        json.dumps({"site": site, "rebuilt_at": time.strftime("%Y-%m-%d %H:%M"),
                    "count": len(rows), "pages": rows}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    return len(rows)


# ---------------------------------------------------------------- 登录与抓取

def _has_sessionid(cookies: list[dict]) -> bool:
    return any(c.get("name") == "sessionid" and c.get("value") for c in cookies)


def _is_captcha_page(html: str) -> bool:
    return ("验证码" in html or "captcha" in html) and len(html) < 50000


async def _new_context(pw, headless: bool):
    return await pw.chromium.launch_persistent_context(
        str(PROFILE_DIR), channel="msedge", headless=headless,
        viewport={"width": 1440, "height": 900},
        user_agent=UA)


async def ensure_login() -> bool:
    """弹有头窗口扫码;sessionid cookie 出现即登录成功。"""
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        ctx = await _new_context(pw, headless=False)
        try:
            cookies = await ctx.cookies("https://www.douyin.com")
            if _has_sessionid(cookies):
                _log("✓ 已有登录态,无需重新登录。")
                return True
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto(LOGIN_URL, wait_until="domcontentloaded",
                            timeout=90000)
            _log("浏览器窗口已打开抖音,请在窗口里登录(5 分钟内),"
                 "成功后自动继续。")
            deadline = time.time() + LOGIN_WAIT_SECONDS
            while time.time() < deadline:
                await asyncio.sleep(5)
                if _has_sessionid(await ctx.cookies("https://www.douyin.com")):
                    _log("✓ 登录成功!登录态已保存到 .automation/dy-profile/,"
                         "以后爬取不用再登录。")
                    return True
            _log("超时未检测到登录,可重跑 --login 再试。")
            return False
        finally:
            await ctx.close()


async def _fetch_search(page, keyword: str) -> str:
    """打开搜索页;若撞验证码,保持窗口打开等用户手动通过(最多 3 分钟)。"""
    url = SEARCH_URL.format(kw=quote_plus(keyword))
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        _log(f"    !! 打开搜索页失败: {type(e).__name__}: {e}")
        return ""
    for attempt in range(2):
        await asyncio.sleep(PAGE_WAIT)
        html = await page.content()
        if not _is_captcha_page(html):
            return html
        if attempt == 0:
            _log("    ⚠ 撞到验证码。请在弹出的浏览器窗口里完成验证"
                 "(滑块/点选),最多等 3 分钟…")
            deadline = time.time() + 180
            while time.time() < deadline:
                await asyncio.sleep(5)
                html = await page.content()
                if not _is_captcha_page(html):
                    _log("    ✓ 验证已通过,继续。")
                    break
            else:
                return ""
    return ""


async def crawl_keyword(page, kw_cfg: dict, limit: int, dry_run: bool,
                        archive_dir: Path) -> list[dict]:
    keyword, topic = kw_cfg["keyword"], kw_cfg.get("topic") or "综合"
    _log(f"  [关键词] {keyword} → 主题 {topic}")
    html = await _fetch_search(page, keyword)
    if not html:
        return []
    videos = filter_relevant(parse_search_html(html), keyword)[:limit]
    if not videos:
        _log("    ⚠ 解析到结果但无一与关键词相关(疑似被降级成推荐流),本词跳过。")
        return []
    _log(f"    解析出 {len(videos)} 条相关视频(截取前 {limit})")
    if dry_run:
        for v in videos:
            _log(f"      - {v['title'][:40]} | {v['url']} | {v['owner']} | {v['digg']}赞")
        return []
    entries: list[dict] = []
    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M")
    for i, v in enumerate(videos, start=1):
        entries.append(video_entry(v, topic, keyword))
        safe_title = re.sub(r'[\\/:*?"<>|\s]+', "-", v["title"])[:40]
        fname = f"{topic}-{i:03d}-{v['vid']}-{safe_title}.md"
        (archive_dir / fname).write_text(
            f"---\nurl: {v['url']}\ntitle: {v['title']}\ntopic: {topic}\n"
            f"keyword: {keyword}\nowner: {v['owner']}\ndigg: {v['digg']}\n"
            f"crawled_at: {stamp}\n---\n\n# {v['title']}\n\n"
            f"- 作者:{v['owner'] or '未知'}\n- 点赞:{v['digg']}\n"
            f"- 观看地址:{v['url']}\n", encoding="utf-8")
    return entries


async def run(cfg: dict, only: str, limit: int, dry_run: bool) -> int:
    from playwright.async_api import async_playwright

    keywords = cfg["keywords"]
    if only:
        needles = [s.strip() for s in only.split(",") if s.strip()]
        keywords = [k for k in keywords
                    if any(n in k["keyword"] for n in needles)]
        if not keywords:
            _log(f"没有匹配的关键词: {only}")
            return 1
    limit = limit or int(cfg.get("max_videos_per_keyword", 5))
    kb_path = ROOT / cfg["output_kb"]
    archive_dir = ROOT / cfg.get("out_dir", "crawled/www.douyin.com/school-qa-videos")
    t0 = time.time()
    total = 0
    _log(f"开始爬取抖音(关键词 {len(keywords)} 个,每关键词上限 {limit} 条)")

    async with async_playwright() as pw:
        ctx = await _new_context(pw, headless=False)   # 风控原因,不做无头
        try:
            cookies = await ctx.cookies("https://www.douyin.com")
            if not _has_sessionid(cookies):
                _log("✗ 未登录。先跑: scripts/crawl_douyin_topics.py --login")
                return 1
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            for idx, kw_cfg in enumerate(keywords):
                entries = await crawl_keyword(page, kw_cfg, limit, dry_run,
                                              archive_dir)
                if entries:
                    written = _write_video_kb(kb_path, entries)
                    total += written
                    _log(f"    → {kb_path.name}:写入/更新 {written} 条")
                if idx != len(keywords) - 1:
                    await asyncio.sleep(PAGE_DELAY)
        finally:
            await ctx.close()

    if not dry_run:
        archived = _rebuild_archive_index(archive_dir, cfg["site"])
        _log(f"\n完成:共写入知识库 {total} 条,用时 {time.time() - t0:.0f}s。"
             f"文件 {kb_path.relative_to(ROOT)},归档 {archived} 篇于 "
             f"{archive_dir.relative_to(ROOT)}/(含 index.json)")
    return 0


async def main() -> int:
    ap = argparse.ArgumentParser(description="按关键词爬抖音搜索页,生成视频知识库")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--login", action="store_true", help="弹出浏览器扫码登录")
    ap.add_argument("--only", default="",
                    help="只抓匹配的条目,逗号分隔多个子串")
    ap.add_argument("--limit", type=int, default=0,
                    help="每个关键词最多几条(默认取配置)")
    ap.add_argument("--dry-run", action="store_true", help="只列结果,不入库")
    args = ap.parse_args()
    if args.login:
        ok = await ensure_login()
        return 0 if ok else 1
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    return await run(cfg, args.only, args.limit, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
