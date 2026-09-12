"""用已登录的小红书会话按关键词搜索笔记,抓正文+链接写入问答知识库。

与 crawl_xhs_topics.py 的区别:详情 URL 显式带 xsec_source=pc_search
(留空时小红书会拒绝返回笔记正文,这是旧脚本"详情失败"的根因),
并用 __INITIAL_STATE__.note.noteDetailMap 作为正文主解析路径。

登录态来自 .automation/xhs-storage-state.json(由 crawl_xhs_topics.py
--login 扫码后导出),本脚本不碰密码、不弹窗。

用法:
    .venv/Scripts/python.exe scripts/crawl_xhs_notes.py --dry-run --limit 3 --only 肇庆
    .venv/Scripts/python.exe scripts/crawl_xhs_notes.py
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:                                              # 直接运行(scripts 在 sys.path)
    from crawl_wx_topics import _archive_md, _log  # noqa: E402
except ImportError:                               # 作为包导入(pytest)
    from scripts.crawl_wx_topics import _archive_md, _log  # noqa: E402

DEFAULT_CONFIG = ROOT / "scripts" / "xhs_topics.json"
AUTOMATION_DIR = ROOT / ".automation"
STORAGE_STATE = AUTOMATION_DIR / "xhs-storage-state.json"
SEARCH_API = "search/notes"
NAV_TIMEOUT = 60_000
PAGE_DELAY = 2.5
CREDIBILITY = "B-社区经验"
NOTE_ID_RE = re.compile(r"^[0-9a-f]{24}$")


# ---------------------------------------------------------------- 解析

def _fix_state_json(raw: str) -> str:
    """把 __INITIAL_STATE__ 的 JS 残留清成合法 JSON。"""
    raw = re.sub(r"\bundefined\b", "null", raw)
    raw = re.sub(r"new (?:Map|Set)\(", "[", raw)
    raw = raw.replace("])", "]")
    return raw


def _ms_to_date(ms) -> str:
    try:
        return datetime.fromtimestamp(int(ms) / 1000,
                                      tz=timezone(timedelta(hours=8))
                                      ).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return ""


def parse_search_items(payload: dict) -> list[dict]:
    """search/notes 响应 → 笔记卡片(只要 24 位十六进制 id 的笔记)。"""
    out: list[dict] = []
    for it in (payload or {}).get("data", {}).get("items") or []:
        card = it.get("note_card") or it.get("noteCard") or {}
        nid = str(card.get("note_id") or card.get("noteId") or it.get("id") or "")
        if not NOTE_ID_RE.fullmatch(nid):
            continue
        user = card.get("user") or {}
        inter = card.get("interact_info") or card.get("interactInfo") or {}
        out.append({
            "id": nid,
            "title": (card.get("display_title") or card.get("displayTitle")
                      or card.get("title") or "").strip(),
            "author": (user.get("nickname") or user.get("nick_name") or "").strip(),
            "likes": str(inter.get("liked_count") or inter.get("likedCount") or ""),
            "xsec_token": it.get("xsec_token") or card.get("xsec_token") or "",
        })
    return out


def _note_from_obj(note: dict) -> dict:
    inter = note.get("interactInfo") or note.get("interact_info") or {}
    user = note.get("user") or {}
    return {
        "title": (note.get("title") or "").strip(),
        "desc": (note.get("desc") or "").strip(),
        "author": (user.get("nickname") or user.get("nickName") or "").strip(),
        "time": _ms_to_date(note.get("time")),
        "ip": note.get("ipLocation") or note.get("ip_location") or "",
        "likes": str(inter.get("likedCount") or inter.get("liked_count") or ""),
        "collects": str(inter.get("collectedCount") or inter.get("collected_count") or ""),
        "comments": str(inter.get("commentCount") or inter.get("comment_count") or ""),
        "tags": [t.get("name", "") for t in
                 (note.get("tagList") or note.get("tag_list") or [])],
        "images": len(note.get("imageList") or note.get("image_list") or []),
    }


def _balanced_json(text: str, start: int) -> str:
    """从 start 处的 { 或 [ 起,配平括号取出完整 JSON 片段(跳过字符串内括号)。"""
    opener = text[start]
    if opener not in "{[":
        return ""
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return ""


def _notes_from_map(ndm, note_id: str = "") -> dict:
    items = ndm.values() if isinstance(ndm, dict) else \
        (ndm if isinstance(ndm, list) else [])
    for obj in items:
        note = (obj or {}).get("note") or obj or {}
        if not isinstance(note, dict):
            continue
        nid = note.get("noteId") or note.get("note_id") or ""
        if note_id and nid and nid != note_id:
            continue
        if note.get("desc") or note.get("title") or nid:
            return _note_from_obj(note)
    return {}


def parse_detail_state(html: str, note_id: str = "") -> dict:
    """详情页 __INITIAL_STATE__ → 笔记正文(HTML 兜底路径)。

    不做整份 JSON 解析:页面里有多处 JS 构造器残留(new Map() 等),
    整份 parse 会在无关 store(如 AiNoteDetailStore)上炸掉。
    改为定位 "noteDetailMap" 后配平括号,只解析目标片段。
    """
    m = re.search(r"window\.__INITIAL_STATE__\s*=\s*(.*?)</script>", html or "", re.S)
    if not m:
        return {}
    raw = m.group(1)
    for hit in re.finditer(r'"noteDetailMap"\s*:\s*', raw):
        seg = _balanced_json(raw, hit.end())
        if not seg:
            continue
        try:
            ndm = json.loads(seg)
        except json.JSONDecodeError:      # 片段内仍有构造器残留时再清洗一次
            try:
                ndm = json.loads(_fix_state_json(seg))
            except json.JSONDecodeError:
                continue
        note = _notes_from_map(ndm, note_id)
        if note:
            return note
    return {}


# 浏览器内直接读页面状态:比解析 HTML 稳(不受序列化/构造器残留影响)
READ_NOTE_JS = """
(noteId) => {
  const s = window.__INITIAL_STATE__;
  const box = s && s.note && s.note.noteDetailMap;
  if (!box) return null;
  let items = [];
  if (box instanceof Map) items = Array.from(box.values());
  else if (Array.isArray(box)) items = box;
  else items = Object.values(box);
  for (const it of items) {
    const n = (it && it.note) || it;
    if (!n) continue;
    const nid = n.noteId || n.note_id || '';
    if (noteId && nid && nid !== noteId) continue;
    const it2 = n.interactInfo || n.interact_info || {};
    const pick = (a, b) => (a === undefined || a === null ? b : a);
    return {
      title: n.title || '',
      desc: n.desc || '',
      author: (n.user && (n.user.nickname || n.user.nickName)) || '',
      time: n.time || 0,
      ip: n.ipLocation || n.ip_location || '',
      likes: String(pick(it2.likedCount, pick(it2.liked_count, ''))),
      collects: String(pick(it2.collectedCount, pick(it2.collected_count, ''))),
      comments: String(pick(it2.commentCount, pick(it2.comment_count, ''))),
      tags: ((n.tagList || n.tag_list || []).map(t => t && t.name).filter(Boolean)),
      images: (n.imageList || n.image_list || []).length
    };
  }
  return null;
}
"""


async def read_note_live(page, note_id: str) -> dict:
    """在页面里执行 JS 读状态;返回与 _note_from_obj 对齐的结构。"""
    try:
        raw = await page.evaluate(READ_NOTE_JS, note_id)
    except Exception:
        return {}
    if not isinstance(raw, dict) or not (raw.get("desc") or raw.get("title")):
        return {}
    raw["time"] = _ms_to_date(raw.get("time"))
    raw["tags"] = raw.get("tags") or []
    raw["images"] = raw.get("images") or 0
    return raw


def _bigrams(s: str) -> set[str]:
    s = re.sub(r"\s+", "", s or "")
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) >= 2 else {s}


def filter_relevant(cards: list[dict], keyword: str) -> list[dict]:
    """只要标题与关键词有二元组重叠的笔记,挡住搜索被降级时的无关推荐。"""
    need = _bigrams(keyword)
    return [c for c in cards if need & _bigrams(c.get("title", ""))]


def kb_entry(note: dict, card: dict, topic: str, keyword: str) -> dict:
    """笔记 → kb 条目;正文首行即链接,kb 摘要才能在 160 字内稳定带出地址。"""
    url = f"https://www.xiaohongshu.com/explore/{card['id']}"
    title = (note.get("title") or card.get("title") or "小红书笔记").strip()
    parts = [f"小红书笔记:{title}", f"笔记地址:{url}"]
    head = []
    if note.get("author") or card.get("author"):
        head.append(f"作者:{note.get('author') or card.get('author')}")
    if note.get("likes") or card.get("likes"):
        head.append(f"{note.get('likes') or card.get('likes')}赞")
    if note.get("collects"):
        head.append(f"{note['collects']}收藏")
    if note.get("time"):
        head.append(f"发布:{note['time']}")
    if note.get("ip"):
        head.append(f"IP属地:{note['ip']}")
    if head:
        parts.append("(" + " | ".join(head) + ")")
    if note.get("desc"):
        parts.append(note["desc"])
    if note.get("tags"):
        parts.append("标签:" + " ".join(f"#{t}" for t in note["tags"][:10]))
    parts.append(f"主题:{topic};来源:小红书搜索「{keyword}」前列笔记,社区经验,引用需注明待核实。")
    return {
        "id": f"xhs-{card['id']}",
        "title": title,
        "content": "\n".join(parts),
        "url": url,
        "source": "小红书",
        "tags": [topic, "小红书笔记", keyword, "社区经验"],
        "credibility": CREDIBILITY,
        "publish_at": note.get("time", ""),
    }


# ---------------------------------------------------------------- 抓取

async def crawl_keyword(page, kwcfg: dict, limit: int, dry_run: bool,
                        out_dir: Path, kb: list[dict]) -> tuple[int, int]:
    kw, topic = kwcfg["keyword"], kwcfg["topic"]
    _log(f"  [关键词] {kw} → 主题 {topic}")
    search_payload: dict = {}

    async def on_response(resp):
        if SEARCH_API in resp.url:
            try:
                search_payload.update(await resp.json())
            except Exception:
                pass

    page.on("response", on_response)
    ok = fail = 0
    try:
        url = ("https://www.xiaohongshu.com/search_result?keyword="
               + quote_plus(kw) + "&source=web_explore_feed")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
        except Exception as e:
            _log(f"    搜索页打开失败: {type(e).__name__}")
            return 0, 1
        for _ in range(45):
            if search_payload:
                break
            await asyncio.sleep(1)
        all_cards = parse_search_items(search_payload)
        if not all_cards:
            _log("    搜索 0 条(登录态失效或风控,请重跑 crawl_xhs_topics.py --login)")
            return 0, 1
        cards = filter_relevant(all_cards, kw)[:limit]
        _log(f"    搜索结果 {len(all_cards)} 条,与关键词相关 {len(cards)} 条"
             f"(取前 {limit})")
        if not cards:
            return 0, 1
        for card in cards:
            detail_url = f"https://www.xiaohongshu.com/explore/{card['id']}"
            if card.get("xsec_token"):
                # xsec_source 必须带值:留空时详情页拒绝返回正文
                detail_url += (f"?xsec_token={card['xsec_token']}"
                               "&xsec_source=pc_search")
            note: dict = {}
            try:
                await page.goto(detail_url, wait_until="commit",
                                timeout=NAV_TIMEOUT)
                # 导航中读页面会抛"context destroyed",必须逐次容错重试,
                # 不能让它冒泡出循环(否则首轮就误判详情失败)
                for _ in range(18):
                    note = await read_note_live(page, card["id"])
                    if not note:
                        try:
                            note = parse_detail_state(await page.content(),
                                                      card["id"])
                        except Exception:
                            note = {}
                    if note.get("desc") or note.get("title"):
                        break
                    await asyncio.sleep(1.0)
            except Exception as e:
                _log(f"    ! 详情异常 {type(e).__name__}")
            if not note:
                _log(f"    - 详情失败: {card['title'][:26]}")
                fail += 1
                continue
            entry = kb_entry(note, card, topic, kw)
            if dry_run:
                _log(f"    + {entry['title'][:34]} | {len(entry['content'])}字 | "
                     f"赞{note.get('likes') or '?'} | {entry['url']}")
            else:
                kb[:] = [e for e in kb if e["id"] != entry["id"]]
                kb.append(entry)
                _archive_md(out_dir, entry, entry["url"])
                _log(f"    + {entry['title'][:34]} | {len(entry['content'])}字")
            ok += 1
            await asyncio.sleep(PAGE_DELAY)
    finally:
        page.remove_listener("response", on_response)
    return ok, fail


async def run(cfg: dict, only: str, limit: int, dry_run: bool) -> int:
    from playwright.async_api import async_playwright

    if not STORAGE_STATE.exists():
        _log(f"✗ 缺少登录态 {STORAGE_STATE.name};先跑 crawl_xhs_topics.py --login")
        return 1
    keywords = cfg["keywords"]
    if only:
        needles = [x.strip() for x in only.split(",") if x.strip()]
        keywords = [k for k in keywords if any(n in k["keyword"] for n in needles)]
    kb_path = ROOT / cfg["output_kb"]
    out_dir = ROOT / cfg["out_dir"]
    kb = json.loads(kb_path.read_text(encoding="utf-8")) if kb_path.exists() else []
    limit = limit or int(cfg.get("max_notes_per_keyword", 5))
    ok = fail = 0
    t0 = time.time()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, channel="msedge")
        ctx = await browser.new_context(storage_state=str(STORAGE_STATE),
                                        locale="zh-CN",
                                        viewport={"width": 1400, "height": 900})
        page = await ctx.new_page()
        try:
            for kwcfg in keywords:
                o, f = await crawl_keyword(page, kwcfg, limit, dry_run, out_dir, kb)
                ok += o
                fail += f
                if kwcfg is not keywords[-1]:
                    await asyncio.sleep(PAGE_DELAY)
        finally:
            await browser.close()

    if not dry_run:
        kb_path.parent.mkdir(parents=True, exist_ok=True)
        kb_path.write_text(json.dumps(kb, ensure_ascii=False, indent=1),
                           encoding="utf-8")
    _log(f"完成: 成功 {ok} 篇,失败 {fail} 次,用时 {time.time() - t0:.0f}s"
         + ("(dry-run 未入库)" if dry_run else f",库共 {len(kb)} 条于 {kb_path.name}"))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="按关键词爬小红书笔记(复用登录态)")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--only", default="", help="只抓含该子串的关键词,逗号分隔")
    ap.add_argument("--limit", type=int, default=0, help="每关键词最多几条")
    ap.add_argument("--dry-run", action="store_true", help="只列结果不入库")
    args = ap.parse_args()
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    return asyncio.run(run(cfg, args.only, args.limit, args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
