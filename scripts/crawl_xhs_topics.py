"""爬小红书搜索笔记,生成带链接的大学生问答知识库(需登录态)。

与 crawl_bili_topics.py / crawl_wx_topics.py 同族:按"学生会问什么"组织。
小红书对未登录请求全墙(搜索页登录墙+详情页302+搜索引擎无索引),所以本
脚本必须带登录态跑:

    .venv/Scripts/python.exe scripts/crawl_xhs_topics.py --login   # 首次:弹窗扫码
    .venv/Scripts/python.exe scripts/crawl_xhs_topics.py           # 日常:增量爬取
    .venv/Scripts/python.exe scripts/crawl_xhs_topics.py --only 美食,肇庆 --dry-run

登录态存 .automation/xhs-profile/(playwright persistent context),导出的
cookie 存 .automation/xhs-storage-state.json,两者都在 .gitignore,绝不入库。

技术要点(踩坑记录):
- 游客也有 web_session cookie,判断登录必须看官方 /api/sns/web/v2/user/me;
- 风控对"开过的"自动化浏览器配置标记很重(页面无限 Loading),所以登录用
  persistent context,爬取改用**全新会话 + 注入 storage_state**(实测可用);
- 搜索结果不解析 DOM,直接监听页面自己的
  GET so.xiaohongshu.com/api/sns/web/v2/search/notes 响应拿结构化 JSON;
- 笔记正文监听 POST edith.xiaohongshu.com/api/sns/web/v1/feed 响应,
  兜底从页面 __INITIAL_STATE__.note.noteDetailMap 提取(需清理
  undefined/new Map 两类 JS 残留才能 JSON 解析);
- 结果页/详情页的 xsec_token 都是短时效的,入库只存裸链接
  https://www.xiaohongshu.com/explore/<id>。

结果:
    crawled/www.xiaohongshu.com/school-qa/*.md  归档(frontmatter)
    kb/xhs-notes.json                           知识库(credibility=B-社区经验)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import quote_plus

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from minicode.tools.crawl import _slug  # noqa: E402

DEFAULT_CONFIG = ROOT / "scripts" / "xhs_topics.json"
AUTOMATION_DIR = ROOT / ".automation"
PROFILE_DIR = AUTOMATION_DIR / "xhs-profile"
STORAGE_STATE = AUTOMATION_DIR / "xhs-storage-state.json"
SEARCH_API = "search/notes"
FEED_API = "web/v1/feed"
ME_API = "web/v2/user/me"
NAV_TIMEOUT = 60_000
WAIT_API_TIMEOUT = 45_000
PAGE_DELAY = 2.5

sys.path.insert(0, str(ROOT / "scripts"))
from crawl_wx_topics import _archive_md, _log  # noqa: E402


def load_kb_file(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def write_kb(path: Path, kb: list[dict], cfg: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(kb, ensure_ascii=False, indent=1), encoding="utf-8")

CREDIBILITY = "B-社区经验"


def _fix_state_json(raw: str) -> str:
    """把 __INITIAL_STATE__ 里的 JS 残留清成合法 JSON。

    实测组合(顺序敏感):裸 undefined → 空容器构造器 → 其余 new Map/Set(
    → 残留的 ) 全部来自构造器调用(合法 JSON 里没有括号)。
    """
    raw = re.sub(r"\bundefined\b", "null", raw)
    raw = raw.replace("new Map([])", "[]").replace("new Set([])", "[]")
    raw = re.sub(r"new (?:Map|Set)\(", "[", raw)
    raw = raw.replace("])", "]")
    return raw


def _ms_to_date(ms) -> str:
    try:
        return datetime.fromtimestamp(int(ms) / 1000,
                                      tz=timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return ""


def parse_search_items(payload: dict) -> list[dict]:
    """search/notes 响应 → 笔记卡片列表(容错解析)。"""
    items = (payload or {}).get("data", {}).get("items") or []
    out = []
    for it in items:
        card = it.get("note_card") or it.get("noteCard") or {}
        nid = card.get("note_id") or card.get("noteId") or it.get("id") or ""
        if not re.fullmatch(r"[0-9a-f]{24}", str(nid)):
            continue
        user = card.get("user") or {}
        interact = card.get("interact_info") or card.get("interactInfo") or {}
        out.append({
            "id": str(nid),
            "title": (card.get("display_title") or card.get("displayTitle")
                      or card.get("title") or "(无标题)").strip(),
            "author": (user.get("nickname") or user.get("nick_name") or "").strip(),
            "likes": str(interact.get("liked_count") or interact.get("likedCount")
                         or ""),
            "type": card.get("type") or "",
            "xsec_token": it.get("xsec_token") or card.get("xsec_token") or "",
        })
    return out


def parse_feed_payload(payload: dict, note_id: str) -> dict:
    """web/v1/feed 响应 → 单条笔记正文结构。"""
    for it in (payload or {}).get("data", {}).get("items") or []:
        note = it.get("note_card") or {}
        if (note.get("note_id") or note.get("noteId")) == note_id:
            interact = note.get("interact_info") or {}
            user = note.get("user") or {}
            return {
                "title": (note.get("title") or "").strip(),
                "desc": (note.get("desc") or "").strip(),
                "author": (user.get("nickname") or "").strip(),
                "time": _ms_to_date(note.get("time")),
                "ip": note.get("ip_location") or "",
                "likes": str(interact.get("liked_count") or ""),
                "collects": str(interact.get("collected_count") or ""),
                "comments": str(interact.get("comment_count") or ""),
                "tags": [t.get("name", "") for t in note.get("tag_list") or []],
                "images": len(note.get("image_list") or []),
            }
    return {}


def parse_initial_state(html: str) -> dict:
    """兜底:从详情页 HTML 挖 __INITIAL_STATE__.note.noteDetailMap。"""
    m = re.search(r"window\.__INITIAL_STATE__\s*=\s*(.*?)</script>", html, re.S)
    if not m:
        return {}
    try:
        data = json.loads(_fix_state_json(m.group(1)))
    except json.JSONDecodeError:
        return {}
    ndm = (data.get("note") or {}).get("noteDetailMap") or {}
    for _, obj in ndm.items():
        note = (obj or {}).get("note") or {}
        if note.get("noteId") or note.get("note_id") or note.get("desc"):
            interact = note.get("interactInfo") or {}
            return {
                "title": (note.get("title") or "").strip(),
                "desc": (note.get("desc") or "").strip(),
                "author": ((note.get("user") or {}).get("nickname") or "").strip(),
                "time": _ms_to_date(note.get("time")),
                "ip": note.get("ipLocation") or "",
                "likes": str(interact.get("likedCount") or ""),
                "collects": str(interact.get("collectedCount") or ""),
                "comments": str(interact.get("commentCount") or ""),
                "tags": [t.get("name", "") for t in note.get("tagList") or []],
                "images": len(note.get("imageList") or []),
            }
    return {}


def _kb_entry(note: dict, card: dict, topic: str, keyword: str) -> dict:
    title = note.get("title") or card["title"]
    tags = [t for t in note.get("tags") or [] if t][:5]
    parts = [f"小红书笔记:《{title}》"]
    if note.get("desc"):
        parts.append(f"正文:{note['desc'][:600]}")
    meta = []
    if note.get("author") or card.get("author"):
        meta.append(f"作者:{note.get('author') or card['author']}")
    stats = ",".join(f"{k}{v}" for k, v in
                     [("赞 ", note.get("likes") or card.get("likes")),
                      ("藏 ", note.get("collects")), ("评 ", note.get("comments"))] if v)
    if stats:
        meta.append(stats)
    if note.get("ip"):
        meta.append(f"IP属地:{note['ip']}")
    if note.get("images"):
        meta.append(f"{note['images']}图")
    if meta:
        parts.append("(" + " | ".join(meta) + ")")
    if note.get("time"):
        parts.append(f"发布:{note['time']}")
    parts.append(f"笔记地址:https://www.xiaohongshu.com/explore/{card['id']}")
    content = "\n".join(parts)
    return {
        "id": f"xhs-{card['id']}",
        "title": (note.get("title") or "").strip(),
        "content": content,
        "url": f"https://www.xiaohongshu.com/explore/{card['id']}",
        "source": "小红书",
        "tags": [topic, "小红书笔记", keyword, "社区经验"],
        "credibility": CREDIBILITY,
        "publish_at": note.get("time", ""),
    }


# ------------------------------------------------------------------ playwright

async def _is_logged_in(ctx) -> bool:
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    try:
        result = await page.evaluate(
            "fetch('https://www.xiaohongshu.com/api/sns/web/v2/user/me',"
            "{credentials:'include'}).then(r=>r.json())")
        data = (result or {}).get("data") or {}
        return bool(data.get("is_login"))
    except Exception:
        return False


async def ensure_login() -> bool:
    """persistent context 弹窗扫码;官方接口确认真实登录。"""
    from playwright.async_api import async_playwright

    AUTOMATION_DIR.mkdir(exist_ok=True)
    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            str(PROFILE_DIR), channel="msedge", headless=False,
            viewport={"width": 1400, "height": 900})
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            if await _is_logged_in(ctx):
                _log("✓ 已有登录态,无需重新登录。")
            else:
                await page.goto("https://www.xiaohongshu.com/explore",
                                wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
                await asyncio.sleep(2)
                for sel in [".login-btn", "button:has-text('登录')",
                            "#login-btn", "text=登录"]:
                    try:
                        await page.locator(sel).first.click(timeout=2500)
                        break
                    except Exception:
                        continue
                _log("浏览器已弹出,请在窗口里用小红书 App 扫码登录(5 分钟内),"
                     "成功后自动继续;不要手动关窗口。")
                deadline = time.time() + 300
                logged = False
                while time.time() < deadline:
                    await asyncio.sleep(5)
                    if await _is_logged_in(ctx):
                        logged = True
                        break
                if not logged:
                    _log("✗ 超时未检测到登录。")
                    return False
                _log("✓ 登录成功!")
            await ctx.storage_state(path=str(STORAGE_STATE))
            _log(f"登录态已导出: {STORAGE_STATE.name}")
            return True
        finally:
            await ctx.close()


async def crawl_keyword(page, kwcfg: dict, limit: int, dry_run: bool,
                        out_dir: Path, kb: list[dict]) -> tuple[int, int]:
    """一个关键词:搜索→取前 limit 条→逐条详情。返回 (成功, 失败)。"""
    from playwright.async_api import TimeoutError as PWTimeout

    kw, topic = kwcfg["keyword"], kwcfg["topic"]
    _log(f"  [关键词] {kw} → 主题 {topic}")
    search_payload: dict = {}
    feed_payloads: dict[str, dict] = {}

    async def on_response(resp):
        url = resp.url
        try:
            if "search/notes" in url:
                search_payload.update(await resp.json())
            elif FEED_API in url:
                body = await resp.json()
                for it in (body.get("data") or {}).get("items") or []:
                    nc = it.get("note_card") or {}
                    nid = nc.get("note_id") or it.get("id") or ""
                    if nid:
                        feed_payloads[nid] = body
        except Exception:
            pass

    page.on("response", on_response)
    ok = fail = 0
    try:
        url = ("https://www.xiaohongshu.com/search_result?keyword="
               + quote_plus(kw) + "&source=web_explore_feed")
        await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
        for _ in range(60):
            if search_payload:
                break
            await asyncio.sleep(1)
        cards = parse_search_items(search_payload)[:limit]
        if not cards:
            _log(f"    搜索 0 条(登录态失效或风控,重跑 --login)")
            return 0, 1
        _log(f"    搜索结果 {len(parse_search_items(search_payload))} 条,取前 {len(cards)} 条")
        for card in cards:
            token = card.get("xsec_token") or ""
            detail_url = "https://www.xiaohongshu.com/explore/" + card["id"]
            if token:
                detail_url += "?xsec_token=" + token + "&xsec_source="
            note = parse_feed_payload(feed_payloads.get(card["id"], {}),
                                      card["id"])
            try:
                await page.goto(detail_url, wait_until="commit",
                                timeout=NAV_TIMEOUT)
                # 详情页 feed API 可能不触发,轮询 INITIAL_STATE 注入完成
                for _ in range(12):
                    note = parse_feed_payload(feed_payloads.get(card["id"], {}),
                                              card["id"])
                    if note:
                        break
                    probe = parse_initial_state(await page.content())
                    if probe:
                        note = probe
                        break
                    await asyncio.sleep(1.0)
            except PWTimeout:
                pass
            if not note:
                note = parse_initial_state(await page.content())
            if not note or not (note.get("desc") or note.get("title")
                                or card.get("title") != "(无标题)"):
                _log(f"    - 详情失败: {card['title'][:28]}")
                fail += 1
                continue
            entry = _kb_entry(note, card, topic, kw)
            _log(f"    + {entry['title'][:40]} | {entry['content'][:0]}"
                 f"{len(entry['content'])}字 | 赞{note.get('likes') or '?'}")
            if not dry_run:
                kb[:] = [e for e in kb if e["id"] != entry["id"]]
                kb.append(entry)
                _archive_md(out_dir, entry, entry["url"])
            ok += 1
            await asyncio.sleep(PAGE_DELAY)
    finally:
        page.remove_listener("response", on_response)
    return ok, fail


async def run(cfg: dict, only: str, limit: int, dry_run: bool) -> int:
    from playwright.async_api import async_playwright

    keywords = cfg["keywords"]
    if only:
        needles = [x.strip() for x in only.split(",") if x.strip()]
        keywords = [k for k in keywords if any(n in k["keyword"] for n in needles)]
        if not keywords:
            _log(f"没有匹配的关键词: {only}")
            return 1
    if not STORAGE_STATE.exists():
        _log(f"✗ 未找到登录态文件 {STORAGE_STATE.name}。先跑 --login 扫码。")
        return 1

    kb_path = ROOT / cfg["output_kb"]
    out_dir = ROOT / cfg["out_dir"]
    kb = load_kb_file(kb_path)
    limit = limit or int(cfg.get("max_notes_per_keyword", 5))
    ok = fail = 0

    async with async_playwright() as pw:
        # 小红书 2026-09 起对 headless 全墙("安全限制"页),必须用有头浏览器。
        # 环境变量 XHS_HEADLESS=1 可临时切回 headless(无桌面环境/打包版)。
        headless = os.environ.get("XHS_HEADLESS", "0") == "1"
        browser = await pw.chromium.launch(headless=headless, channel="msedge")
        ctx = await browser.new_context(
            viewport={"width": 1400, "height": 900})
        page = await ctx.new_page()
        try:
            for kwcfg in keywords:
                o, f = await crawl_keyword(page, kwcfg, limit, dry_run,
                                           out_dir, kb)
                ok += o
                fail += f
                await asyncio.sleep(PAGE_DELAY)
        finally:
            await browser.close()

    if not dry_run:
        write_kb(kb_path, kb, cfg)
    _log(f"完成: 成功 {ok} 篇,失败 {fail} 次"
         + ("(dry-run 未入库)" if dry_run else f",库共 {len(kb)} 条"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="爬小红书搜索笔记生成知识库")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--login", action="store_true", help="弹出浏览器扫码登录")
    ap.add_argument("--only", default="", help="只抓匹配关键词,逗号分隔")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if args.login:
        return 0 if asyncio.run(ensure_login()) else 1
    return asyncio.run(run(cfg, args.only, args.limit, args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
