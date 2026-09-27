"""校历补录助手(v0.9.62):把「手编 JSON」降成「贴通知 → 抽取 → 核对 → 入库」。

为什么要有它:路线图 #14 的口径一直是"拿到教务处校历后往 events 里补(JSON 手编即可)"
—— 手编的门槛让考试周/寒暑假这些数据空了一整个学期,也卡住了 A5 考试闭环。本模块
把同学(或模型替同学)整理好的候选条目做**结构校验 + 核对单 + 安全入库**。

三条诚实边界,全部是校历模块既有口径的延伸:
- **引用原文是准入门槛**:每条候选必须带 `quote` —— 通知原文里支撑这个日期的那一句。
  没有引用的日期 = 编造的日期,校历宁可不收(preview 里点名列出被拒的条目)。
- **可信度跟着来源走**:source 是网址 → 按 kb.crawl_credibility(域名白名单)定级;
  source 是"用户提供的通知"这类文字 → 一律 `B-待核实`。绝不因为"模型抽出来的"就给 A。
- **重复不入**:与现有 events 同名同日的、同批次内重复的,一律跳过并说明 ——
  校历文件是别人维护的数据,本模块只追加,绝不改写已有条目。

人工核对在对话里发生:`preview` 生成核对单(模型必须完整念给用户、得到明确同意),
`commit` 再校验一遍才写 `kb/school-calendar.json`(应用数据目录那份;安装版没有就
新建 —— 它会遮蔽包内那份,与 calendar.load_events 的回退顺序一致)。
"""
from __future__ import annotations

import json
import re

from minicode.paths import app_path

KINDS = ("holiday", "exam", "term", "deadline", "event")
DEFAULT_CREDIBILITY = "B-待核实"


def _slug(name: str, start: str, taken: set[str]) -> str:
    """条目 id:名字清洗 + 锚点日期,撞了加序号(与 tool_name_for 同一思路)。"""
    base = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", str(name or "").strip()).strip("-")
    base = (base or "event") + "-" + str(start or "无日期")
    out, n = base, 2
    while out in taken:
        out = f"{base}-{n}"
        n += 1
    return out


def _credibility_of(source: str, explicit: str) -> str:
    """可信度:显式给的就尊重;source 是网址按域名白名单定级;其余一律待核实。"""
    text = str(explicit or "").strip()
    if text:
        return text
    m = re.search(r"https?://([^/\s]+)", str(source or ""))
    if m:
        from minicode.kb import crawl_credibility
        return crawl_credibility(m.group(1))
    return DEFAULT_CREDIBILITY


def validate(entries, existing_events: list) -> "tuple[list[dict], list[str]]":
    """校验候选条目 → (合格清单, 问题清单)。**只挑捡,绝不改写已有条目。**

    每条必须:名字、start(ISO 日期)、quote(通知原文引用)三样齐全;end 可选但
    不得早于 start;kind 不认识就落 "event" 并说明。与现有 events 同名同日的、
    同批次内重复的,跳过并点名 —— 追加是本模块唯一的写法。
    """
    clean: list[dict] = []
    problems: list[str] = []
    taken: set[str] = {str(e.get("id") or "") for e in existing_events if isinstance(e, dict)}
    seen: set[tuple[str, str]] = {
        (str(e.get("name") or ""), str(e.get("start") or ""))
        for e in existing_events if isinstance(e, dict)}
    for i, raw in enumerate(entries or [], start=1):
        if not isinstance(raw, dict):
            problems.append(f"第 {i} 条不是对象,跳过")
            continue
        name = str(raw.get("name") or "").strip()
        start = str(raw.get("start") or "").strip()
        quote = str(raw.get("quote") or "").strip()
        if not name or not start or not quote:
            problems.append(f"第 {i} 条({name or '未命名'})缺 "
                            + "、".join(k for k, v in (("name", name), ("start", start),
                                                       ("quote(通知原文引用)", quote)) if not v)
                            + " —— 没有引用原文的日期不收")
            continue
        try:
            from datetime import date
            d_start = date.fromisoformat(start)
        except ValueError:
            problems.append(f"第 {i} 条({name})start 不是 ISO 日期:{start}")
            continue
        end = str(raw.get("end") or "").strip()
        if end:
            try:
                if date.fromisoformat(end) < d_start:
                    problems.append(f"第 {i} 条({name})end 早于 start,跳过")
                    continue
            except ValueError:
                problems.append(f"第 {i} 条({name})end 不是 ISO 日期:{end}")
                continue
        kind = str(raw.get("kind") or "event").strip()
        if kind not in KINDS:
            problems.append(f"第 {i} 条({name})kind「{kind}」不认识,按 event 收"
                            f"(可用:{'/'.join(KINDS)})")
            kind = "event"
        key = (name, start)
        if key in seen:
            problems.append(f"第 {i} 条({name} {start})与已有/本批条目重复,跳过")
            continue
        seen.add(key)
        source = str(raw.get("source") or "").strip()
        entry = {"id": _slug(name, start, taken),
                 "name": name[:60], "start": start,
                 "end": end or "", "kind": kind,
                 "quote": quote[:200],
                 "source": source[:200] or "用户提供",
                 "credibility": _credibility_of(source, str(raw.get("credibility") or ""))}
        taken.add(entry["id"])
        if raw.get("note"):
            entry["note"] = str(raw["note"]).strip()[:200]
        clean.append(entry)
    return clean, problems


def preview_text(clean: list[dict], problems: list[str]) -> str:
    """核对单:模型必须把它完整念给用户、得到明确同意才能 commit。"""
    if not clean:
        lines = ["没有可入库的条目。"]
    else:
        lines = [f"共 {len(clean)} 条待入校历,请逐条核对:"]
        for e in clean:
            span = e["start"] + (" ~ " + e["end"] if e.get("end") else "")
            lines.append(f"- {e['name']} [{e['kind']}] {span}")
            lines.append(f"  依据:{e['quote']}")
            lines.append(f"  来源:{e['source']}(可信度 {e['credibility']})")
    lines += [f"⚠ {p}" for p in problems]
    if clean:
        lines.append("确认无误请明确说「确认入库」;有错的条目现在提出来,修正后重新预览。")
    return "\n".join(lines)


def load_events_file() -> "tuple[dict | None, str]":
    """读应用数据目录那份校历(安装版没有就当空,commit 时新建)。"""
    from minicode.calendar import CALENDAR_FILE
    path = app_path(CALENDAR_FILE)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("events"), list):
            return data, str(path)
    except (OSError, json.JSONDecodeError):
        pass
    return None, str(path)


def commit(clean: list[dict]) -> dict:
    """把**已核对**的条目追加进校历文件。再校验一遍,追加是唯一写法。"""
    if not clean:
        return {"ok": False, "msg": "没有要入库的条目。"}
    data, path = load_events_file()
    if data is None:
        data = {"notes": "校历数据维护说明:events 里每个条目 {id, name, start, end, kind,"
                         "note, source, credibility}。日期只录有官方来源的;来源与可信度随条目走。",
                "events": []}
    existing = data.get("events") or []
    fresh, problems = validate(clean, existing)
    if not fresh:
        return {"ok": False,
                "msg": "没有可入库的条目(全部与现有重复或校验失败):" +
                       (";".join(problems) if problems else "空")}
    data["events"] = list(existing) + fresh
    try:
        from minicode.calendar import CALENDAR_FILE
        target = app_path(CALENDAR_FILE)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                          encoding="utf-8")
    except OSError as e:
        return {"ok": False, "msg": f"错误: 写不进去({type(e).__name__}) —— 文件保持原样。"}
    return {"ok": True, "added": len(fresh), "problems": problems,
            "names": [e["name"] for e in fresh],
            "msg": (f"已入库 {len(fresh)} 条到 {target.name}"
                    + (f";另有 {len(problems)} 条被拦下(见预览)" if problems else "")
                    + "。来源与原文引用随条目存档,随时可手工修订。")}


def calendar_edit_tool(ctx, action: str = "preview", entries=None) -> str:
    """calendar_edit 工具处理器:preview 生成核对单,commit 才写文件。"""
    action = (action or "preview").strip().lower()
    if action == "preview":
        from minicode.calendar import load_events
        existing, _src = load_events()
        clean, problems = validate(entries, existing)
        return preview_text(clean, problems)
    if action == "commit":
        from minicode.calendar import load_events
        existing, _src = load_events()
        clean, problems = validate(entries, existing)
        result = commit(clean)
        if not result.get("ok"):
            return f"错误: {result.get('msg')}"
        tail = ""
        if result.get("problems"):
            tail = "\n被拦下的条目:\n" + "\n".join(f"- {p}" for p in result["problems"])
        return (f"{result['msg']}\n入库明细:\n"
                + "\n".join(f"- {n}" for n in result.get("names") or []) + tail)
    return ("错误: 未知 action。可用: preview(校验并生成核对单,先念给用户听)/"
            "commit(用户明确同意后才调,追加入库)。")
