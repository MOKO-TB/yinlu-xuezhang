"""按"用户会问什么"的关键词爬 B 站搜索页,生成带视频链接的问答知识库。

与 crawl_school_topics.py(爬学校官网文章)互补:学生问"怎么备考/怎么入党/
奖学金怎么申请"这类经验问题时,官网文章不如视频直观。这里用 crawl4ai
渲染 search.bilibili.com/all?keyword=xx 的结果页,解析每张视频卡片
(标题/链接/UP主/时长/播放量/发布日期),按主题写入 kb/bili-videos.json,
search_knowledge 检索命中后模型回答时直接附视频链接,用户不用再等现场爬取。

过滤规则:只收链接为 bilibili.com/video/BV* 的普通视频。直播/付费课
(cheese)/广告(cm.bilibili.com)的卡片链接不是 /video/ 开头,自然排除。

归档:每条视频一个 frontmatter .md + index.json,与 crawl_school_topics
的归档结构一致,便于人工核对与重建。

用法:
    .venv/Scripts/python.exe scripts/crawl_bili_topics.py                # 全部关键词
    .venv/Scripts/python.exe scripts/crawl_bili_topics.py --only 考研    # 模糊匹配关键词
    .venv/Scripts/python.exe scripts/crawl_bili_topics.py --limit 3      # 每个关键词最多 3 条
    .venv/Scripts/python.exe scripts/crawl_bili_topics.py --dry-run      # 只列结果不入库

配置见 scripts/bili_topics.json;换关键词/主题改配置即可,不用改代码。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote_plus, urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:                    # 支持从任意目录调用
    sys.path.insert(0, str(ROOT))

DEFAULT_CONFIG = ROOT / "scripts" / "bili_topics.json"
SEARCH_URL = "https://search.bilibili.com/all?keyword={kw}"
PAGE_DELAY = 1.2                     # B 站风控比学校站群严,页间多等一会儿
VIDEO_URL_RE = re.compile(r"^https?://(?:www\.)?bilibili\.com/video/(BV[0-9A-Za-z]+)")
SCHOOL_TOPIC = "广应科肇庆校区"
# 校名白名单:同城其他学校也会带「肇庆/校区」字样,词面相似度拦不住
SCHOOL_MUST_MATCH = ("广应科", "广州应用科技学院", "应用科技学院", "广应科学院")


def _log(msg: str) -> None:
    print(msg, flush=True)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def parse_search_html(html: str) -> list[dict]:
    """解析 B 站搜索结果页 HTML → 视频卡片列表(保持页面顺序,已滤广告/直播/课程)。

    站点改版时这里最容易碎:标题锚点是 h3 的**父级** a(不是子级),字段都在
    bili-video-card__* 系列类名下;UP主在 a.bili-video-card__info--owner,
    播放量/弹幕在 bili-video-card__stats--item,时长在 ...__stats__duration。
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html or "", "html.parser")
    out: list[dict] = []
    seen_bv: set[str] = set()
    for card in soup.select("div.bili-video-card"):
        h3 = card.select_one("h3.bili-video-card__info--tit")
        if h3 is None:
            continue
        anchor = h3.find_parent("a", href=True) or h3.find("a", href=True)
        href = _abs((anchor.get("href") if anchor else "") or "")
        m = VIDEO_URL_RE.match(href)
        if not m:
            continue
        bv = m.group(1)
        if bv in seen_bv:
            continue
        seen_bv.add(bv)
        title = _clean(anchor.get("title") if anchor and anchor.get("title")
                       else h3.get_text(" ", strip=True))
        if not title:
            continue
        owner_el = card.select_one("a.bili-video-card__info--owner")
        owner = ""
        if owner_el is not None:
            author = owner_el.select_one("span.bili-video-card__info--author")
            owner = _clean(author.get_text(" ", strip=True)) if author else ""
        if not owner:                     # 改版后 author 不在 owner a 里时的兜底
            author_el = card.select_one("span.bili-video-card__info--author")
            owner = _clean(author_el.get_text(" ", strip=True)) if author_el else ""
        duration_el = card.select_one(".bili-video-card__stats__duration")
        stats = [_clean(s.get_text(" ", strip=True))
                 for s in card.select(".bili-video-card__stats--item")]
        date = ""
        date_el = card.select_one("span.bili-video-card__info--date")
        if date_el is not None:
            date = _clean(date_el.get_text(" ", strip=True)).lstrip("·").strip()
        play = stats[0] if stats else ""
        out.append({"bv": bv, "url": f"https://www.bilibili.com/video/{bv}",
                    "title": title, "owner": owner, "duration": _clean(
                        duration_el.get_text(" ", strip=True)) if duration_el else "",
                    "play": play, "date": date})
    return out


def filter_relevant(videos: list[dict], topic: str) -> list[dict]:
    """校内主题按校名硬过滤。

    搜「广州应用科技学院肇庆校区」会混进同城其他学校(广州华商学院肇庆校区、
    广州交校等),它们共享「肇庆/校区」词面,通用相似度过滤无效,
    对校名做白名单最稳。"""
    if topic != SCHOOL_TOPIC:
        return videos
    return [v for v in videos if any(p in v["title"] for p in SCHOOL_MUST_MATCH)]


def _abs(href: str) -> str:
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return "https://www.bilibili.com" + href
    return href


def video_entry(video: dict, topic: str, keyword: str) -> dict:
    """视频卡片 → kb 条目。content 首行必须带链接:kb.format_results 的
    摘要截断从正文开头开始,链接放正文里才能在 160 字速览中稳定出现。"""
    lines = [f"视频推荐:{video['title']}({video['duration'] or '时长未知'})",
             f"观看地址:{video['url']}"]
    if video.get("owner"):
        lines.append(f"UP主:{video['owner']}" + (f"({video['play']}播放)" if video.get("play") else ""))
    if video.get("date"):
        lines.append(f"发布:{video['date']}")
    lines.append(f"主题:{topic};来源:B站搜索「{keyword}」前列结果,适合直接观看学习。")
    return {
        "id": f"bili-{video['bv']}",
        "title": video["title"],
        "content": "\n".join(lines),
        "url": video["url"],
        "source": "哔哩哔哩",
        "tags": [topic, "B站视频", keyword, "视频推荐"],
        "credibility": "B-视频经验",       # 非 A:UP主个人经验,回答时按待核实口径注明
        "publish_at": video.get("date") or "",
    }


def _write_video_kb(path: Path, entries: list[dict]) -> tuple[int, int]:
    """按 id 去重合并进视频知识库(重跑同关键词只更新,不覆盖别的关键词)。"""
    existing: list[dict] = []
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                existing = [e for e in data if isinstance(e, dict)]
        except (OSError, json.JSONDecodeError):
            existing = []
    by_id = {e.get("id"): e for e in existing}
    written = skipped = 0
    for e in entries:
        old = by_id.get(e["id"])
        if old is not None:
            merged_tags = list(dict.fromkeys(list(old.get("tags") or []) + e["tags"]))
            old.update({k: v for k, v in e.items() if k != "id"})
            old["tags"] = merged_tags
        else:
            by_id[e["id"]] = e
            existing.append(e)
        written += 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(existing, ensure_ascii=False, indent=1), encoding="utf-8")
    return written, skipped


_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.S)


def _rebuild_archive_index(archive_dir: Path, site: str = "") -> int:
    """按目录实际 .md frontmatter 重建 index.json(与 crawl_school_topics 一致)。"""
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


# ---------------------------------------------------------------- 抓取

async def _fetch_search(crawler, keyword: str, run_cfg) -> tuple[str, str]:
    """抓一个关键词的搜索结果页,返回 (html, title);失败返回空串。"""
    url = SEARCH_URL.format(kw=quote_plus(keyword))
    try:
        result = await crawler.arun(url=url, config=run_cfg)
    except Exception as e:
        _log(f"    !! 抓取失败 {url} ({type(e).__name__}: {e})")
        return "", ""
    if not getattr(result, "success", False):
        _log(f"    !! 抓取失败 {url} ({getattr(result, 'error_message', '') or '未知错误'})")
        return "", ""
    html = getattr(result, "html", "") or ""
    meta = getattr(result, "metadata", None)
    title = str((meta or {}).get("title") or "")
    return html, title


async def crawl_keyword(crawler, run_cfg, kw_cfg: dict, limit: int,
                        dry_run: bool, archive_dir: Path) -> list[dict]:
    keyword, topic = kw_cfg["keyword"], kw_cfg.get("topic") or "综合"
    _log(f"  [关键词] {keyword} → 主题 {topic}")
    html, page_title = await _fetch_search(crawler, keyword, run_cfg)
    if not html:
        return []
    videos = filter_relevant(parse_search_html(html), topic)[:limit]
    if not videos:
        _log(f"    页面「{page_title}」无与校名相关的视频,本词跳过。")
        return []
    _log(f"    页面「{page_title}」解析出 {len(videos)} 条相关视频(截取前 {limit})")
    if dry_run:
        for v in videos:
            _log(f"      - {v['title'][:44]} | {v['url']} | {v['owner']} | "
                 f"{v['duration']} | {v['play']}")
        return []
    entries: list[dict] = []
    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M")
    for i, v in enumerate(videos, start=1):
        entries.append(video_entry(v, topic, keyword))
        safe_title = re.sub(r"[\\/:*?\"<>|\s]+", "-", v["title"])[:40]
        fname = f"{topic}-{i:03d}-{v['bv']}-{safe_title}.md"
        (archive_dir / fname).write_text(
            f"---\nurl: {v['url']}\ntitle: {v['title']}\ntopic: {topic}\n"
            f"keyword: {keyword}\nowner: {v['owner']}\nduration: {v['duration']}\n"
            f"play: {v['play']}\npublish_at: {v['date']}\ncrawled_at: {stamp}\n---\n\n"
            f"# {v['title']}\n\n- UP主:{v['owner']}\n- 时长:{v['duration'] or '未知'}"
            f"\n- 播放:{v['play'] or '未知'}\n- 发布:{v['date'] or '未知'}\n"
            f"- 观看地址:{v['url']}\n", encoding="utf-8")
    return entries


async def main() -> int:
    ap = argparse.ArgumentParser(description="按关键词爬 B 站搜索页,生成视频知识库")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--only", default="",
                    help="只抓匹配的条目,逗号分隔多个子串,如: --only 四级,教资")
    ap.add_argument("--limit", type=int, default=0,
                    help="每个关键词最多几条(默认取配置 max_videos_per_keyword)")
    ap.add_argument("--dry-run", action="store_true", help="只列结果,不入库不归档")
    args = ap.parse_args()

    site_cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    keywords = site_cfg["keywords"]
    if args.only:
        needles = [s.strip() for s in args.only.split(",") if s.strip()]
        keywords = [k for k in keywords
                    if any(n in k["keyword"] for n in needles)]
        if not keywords:
            _log(f"没有匹配的关键词: {args.only}")
            return 1
    limit_default = int(site_cfg.get("max_videos_per_keyword", 5))

    try:
        from crawl4ai import (AsyncWebCrawler, BrowserConfig, CacheMode,
                              CrawlerRunConfig)
    except ImportError:
        _log("错误: 未安装 crawl4ai。请执行 pip install -U crawl4ai && crawl4ai-setup")
        return 1

    run_cfg = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, page_timeout=60000,
                               wait_until="domcontentloaded", verbose=False)
    archive_dir = ROOT / site_cfg.get(
        "out_dir", f"crawled/{site_cfg['host']}/school-qa-videos")
    kb_path = ROOT / site_cfg["output_kb"]
    t0 = time.time()
    total = 0
    _log(f"开始爬取 B 站(关键词 {len(keywords)} 个,每关键词上限 "
         f"{args.limit or limit_default} 条)")

    async with AsyncWebCrawler(config=BrowserConfig(headless=True)) as crawler:
        for kw_cfg in keywords:
            limit = args.limit or limit_default
            entries = await crawl_keyword(crawler, run_cfg, kw_cfg, limit,
                                          args.dry_run, archive_dir)
            if entries:
                written, _ = _write_video_kb(kb_path, entries)
                total += written
                _log(f"    → {kb_path.name}:写入/更新 {written} 条")
            if kw_cfg is not keywords[-1]:
                await asyncio.sleep(PAGE_DELAY)

    if not args.dry_run:
        archived = _rebuild_archive_index(archive_dir, site_cfg["site"])
        _log(f"\n完成:共写入知识库 {total} 条,用时 {time.time() - t0:.0f}s。"
             f"文件 {kb_path.relative_to(ROOT)},归档 {archived} 篇于 "
             f"{archive_dir.relative_to(ROOT)}/(含 index.json)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
