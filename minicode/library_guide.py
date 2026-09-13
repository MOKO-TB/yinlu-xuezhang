"""图书馆导览模块(新生入馆教学):把「馆藏楼层 + 馆舍空间 + 借还流程」做成固定工具。

和 library.py 的分工:
- library.py 负责**查具体某本书**(133 万条书目,走 SQLite);
- 本模块负责**讲图书馆怎么用**(楼层/书库分布、馆舍空间、借还流程、检索入口),
  数据来自 skills/library-guide/data/library-facts.json —— 随技能一起进包,
  所以**打包版没有 1.3 GB 的 library.db 也能用**(查书工具会另外提示)。

数据是机器生成的真实数据 + 官方来源人工维护的事实。**开放时间、借阅规则、
座位预约没有官方来源**,模块一律回「以图书馆官方为准」并给入口,绝不编造
(facts.unverified 里逐条记了原因)。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from minicode.paths import app_path, resource_path

REL_DIR = ("skills", "library-guide", "data")
FILE_NAME = "library-facts.json"

_CACHE: dict[str, tuple[float, dict]] = {}


def facts_candidates(workspace: Path | None = None) -> list[Path]:
    """数据包位置:工作区优先,其次 exe 旁 / 只读资源目录(兼容打包形态)。"""
    out: list[Path] = []
    if workspace:
        out.append(Path(workspace).joinpath(*REL_DIR, FILE_NAME))
    out.append(app_path(*REL_DIR, FILE_NAME))
    out.append(resource_path(*REL_DIR, FILE_NAME))
    seen, uniq = set(), []
    for p in out:
        key = str(p).lower()
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


def facts_path(workspace: Path | None = None) -> Path | None:
    for p in facts_candidates(workspace):
        if p.is_file():
            return p
    return None


def load_facts(workspace: Path | None = None) -> dict | None:
    """按 mtime 缓存:数据包更新后自动重读。"""
    path = facts_path(workspace)
    if path is None:
        return None
    key = str(path)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    hit = _CACHE.get(key)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    _CACHE[key] = (mtime, data)
    return data


def _code_of(query: str) -> str:
    """把查询词归到中图法大类字母:认首字母(I247.5 → I),或按类名匹配(文学 → I)。"""
    q = (query or "").strip()
    if not q:
        return ""
    m = re.search(r"[A-Za-z]", q)
    if m and m.start() == 0:
        return m.group(0).upper()
    return ""


def resolve_class(facts: dict, query: str) -> dict | None:
    """按大类字母或类名找到 class_floor_map 里的条目。"""
    code = _code_of(query)
    table = facts.get("class_floor_map") or []
    if code:
        for row in table:
            if row.get("code") == code:
                return row
    q = (query or "").strip()
    if q:
        for row in table:
            if q in str(row.get("name") or "") or q in str(row.get("code") or ""):
                return row
    return None


def _fmt_floor(row: dict) -> str:
    floors = "、".join(f"{f}({n:,} 册)" for f, n in (row.get("floors") or [])) or "—"
    lines = [f"{row['code']} {row['name']}:主要在 {floors}"]
    locs = row.get("locations") or []
    if locs:
        lines.append("  具体书库:" + ";".join(
            f"{l['name'].split('-', 1)[-1].strip()}({l['items']:,} 册)" for l in locs[:4]))
    return "\n".join(lines)


def _fmt_flow(facts: dict) -> str:
    chans = facts.get("official_channels") or []
    lines = ["入馆借还流程(教学):",
             "1. 先查有没有:用 library_search 查本地馆藏,或在师生图书服务平台检索,记下索书号与在架数。",
             "2. 按楼层找书:用中图法大类→楼层表定位楼层,再到具体书库按索书号顺序找。",
             "3. 借还操作:自助借还机或服务台凭校园卡办理,借阅记录在图书馆借还书系统查。",
             "4. 扩展用法:电子书、数据库资源、馆际互借/文献传递。"]
    if chans:
        lines.append("官方入口:")
        lines += [f"  - {c['name']}:{c['url']}({c.get('use', '')})" for c in chans]
    lines.append("可借册数、借期天数、续借次数等**没有官方来源**,以图书馆系统/读者手册为准,不要给具体数字。")
    return "\n".join(lines)


def _fmt_space(facts: dict) -> str:
    lines = ["馆舍与空间(官方来源):"]
    for s in (facts.get("spaces") or []):
        lines.append(f"  - {s['name']}:{s['fact']}(来源:{s.get('source', '')})")
    return "\n".join(lines)


def _fmt_stats(facts: dict) -> str:
    cat = facts.get("catalog") or {}
    live = facts.get("live_snapshot") or {}
    lines = [f"馆藏规模:书目 {cat.get('total_records', 0):,} 条"
             f"(纸本 {cat.get('paper', 0):,} / 电子书 {cat.get('ebook', 0):,}),"
             f"馆藏单册 {cat.get('item_records', 0):,} 册,"
             f"覆盖 {cat.get('class_numbers', 0):,} 个中图法分类号。"]
    if live:
        lines.append(f"单册状态({live.get('as_of', '')}):在架 {live.get('on_shelf', 0):,} / "
                     f"借出 {live.get('borrowed', 0)}(逾期 {live.get('overdue', 0)}) / "
                     f"丢失 {live.get('lost', 0)}。这是抓取快照,不是实时。")
    dist = facts.get("class_distribution") or []
    if dist:
        top = "、".join(f"{d['name']}({d['total']:,})" for d in dist[:5])
        lines.append(f"藏书最多的五类:{top}。")
    return "\n".join(lines)


def _fmt_overview(facts: dict) -> str:
    lines = [_fmt_stats(facts), "", "可问:某类书在几楼(topic=floor)、馆里有什么(topic=space)、"
                                    "第一次怎么借书(topic=flow)。"]
    return "\n".join(lines)


TOPICS = ("all", "floor", "space", "flow", "stats", "unverified")


def guide(ctx, topic: str = "all", query: str = "") -> str:
    """工具处理器:返回图书馆导览的固定回答。"""
    facts = load_facts(getattr(ctx, "workspace", None))
    if facts is None:
        return ("错误: 图书馆导览数据包不存在(skills/library-guide/data/library-facts.json)。"
                "请确认技能目录完整,或用 scripts/library_opac/make_guide_facts.py 重新生成。")
    topic = (topic or "all").strip().lower()
    if topic not in TOPICS:
        topic = "all"

    if topic == "unverified" or (query and "未核" in query):
        items = facts.get("unverified") or []
        if not items:
            return "本模块的数据均已核实。"
        return ("以下内容**没有官方来源,不得编造数字**,一律以图书馆官方为准:\n" +
                "\n".join(f"- {it['item']}\n    原因:{it.get('why', '')}\n    补法:{it.get('how', '')}"
                          for it in items))

    if topic == "floor":
        row = resolve_class(facts, query)
        if row is None:
            table = facts.get("class_floor_map") or []
            listing = "\n".join(f"  {r['code']} {r['name']}" for r in
                                sorted(table, key=lambda x: x["code"]))
            got = f"没认出「{query}」属于哪个中图法大类。" if query else ""
            return f"{got}请给中图法大类字母或类名(如 I / 文学 / TP312)。各大类:\n{listing}"
        return _fmt_floor(row)

    if topic == "space":
        return _fmt_space(facts)
    if topic == "flow":
        return _fmt_flow(facts)
    if topic == "stats":
        return _fmt_stats(facts)

    # all:概览 + 完整楼层表
    table = facts.get("class_floor_map") or []
    body = "\n".join(_fmt_floor(r) for r in table)
    return ("图书馆导览(数据源:图书馆 OPAC 全量抓取 + 官方来源;抓取日 "
            f"{(facts.get('meta') or {}).get('catalog_fetched_at', '')})\n\n"
            + _fmt_overview(facts) + "\n\n中图法大类→楼层:\n" + body)


def library_guide_prompt_section(workspace: Path | None = None) -> str:
    """给系统提示词:让模型知道有这个固定模块可用。"""
    facts = load_facts(workspace)
    if facts is None:
        return ""
    cat = facts.get("catalog") or {}
    return (
        "\n# 图书馆导览模块(新生入馆教学)\n"
        f"已装载图书馆导览数据(馆藏 {cat.get('total_records', 0):,} 条书目、"
        "中图法大类→楼层书库映射、馆舍空间与借还流程,随技能打包)。\n"
        "用户问「某类书在几楼 / 某本索书号 X 在哪 / 图书馆几层有什么 / 第一次怎么借书 / "
        "图书馆怎么用」时,调用 library_guide 工具(topic=floor|space|flow|stats|all,\n"
        "floor 可带 query 传中图法大类字母或类名);查**具体某本书**仍用 library_search。\n"
        "开馆时间、可借册数/借期、座位预约**没有官方来源**,一律答「以图书馆官方为准」"
        "并给官方入口,**不要编造**;权威做法见技能 library-guide。\n")
