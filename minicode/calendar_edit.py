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

删除与修订(v0.9.73):录错一条再也「没法改」就是逼同学手编 JSON —— `remove_*`
与 `revise_*` 两组 action 把写法补全,安全口径不变:
- **人工核对仍是唯一闸门**:remove/revise 同样两步走,核对单列出**条目全文**
  (含来源与原文引用),用户明确同意才动文件;
- **备份是回滚的唯一路,备份写不进去就不动数据**(删除是不可逆动作,宁可不改);
  备份落在 `kb/history/`(与 memory/history 同一思路,留最近 3 份);
  `load_kb` 只扫顶层 `*.json` 且只收 list 结构,历史目录不会被当知识库扫进去;
- **修订轨迹写进条目本身**(note 里记「字段: 旧→新;原因」),文件当前内容
  永远反映现实;revise 一次只改一条 —— 同一个 patch 打到多条上是事故形状;
- revise 的校验**报错不静默**(kind 写错、end 早于 start、改后撞名都直接拒绝):
  新增条目可以把不认识的 kind 落回 event,修订把好数据改坏才是真事故。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

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


# ---------------------------------------------------- 删除与修订(v0.9.73)

BACKUP_DIR = "kb/history"          # 与 memory/history 同一思路:备份留在数据旁边
BACKUP_KEEP = 3
_REVISE_FIELDS = ("name", "start", "end", "kind", "note", "quote",
                  "source", "credibility")


def _resolve_targets(targets, events: list) -> "tuple[list[tuple[int, dict]], list[str]]":
    """把选择器解析成 (下标, 条目);解析不了的如实列出,绝不猜。

    选择器:{"id": ...} 或 {"name": ..., "start": ...}(两者都给以 id 为准)。
    同一条目被重复选中只算一次。
    """
    resolved: list[tuple[int, dict]] = []
    missing: list[str] = []
    for i, t in enumerate(targets or [], start=1):
        if not isinstance(t, dict):
            missing.append(f"第 {i} 个选择器不是对象")
            continue
        tid = str(t.get("id") or "").strip()
        name = str(t.get("name") or "").strip()
        start = str(t.get("start") or "").strip()
        hit = None
        if tid:
            hit = next(((j, e) for j, e in enumerate(events)
                        if isinstance(e, dict)
                        and str(e.get("id") or "") == tid), None)
            what = f"id={tid}"
        elif name and start:
            hit = next(((j, e) for j, e in enumerate(events)
                        if isinstance(e, dict)
                        and str(e.get("name") or "") == name
                        and str(e.get("start") or "") == start), None)
            what = f"「{name} {start}」"
        else:
            missing.append(f"第 {i} 个选择器缺 id 或 name+start")
            continue
        if hit is None:
            missing.append(f"没有找到 {what} 的条目 —— 不猜,核对名字与日期再试")
        else:
            resolved.append(hit)
    seen: set[int] = set()
    uniq: list[tuple[int, dict]] = []
    for j, e in resolved:
        if j not in seen:
            seen.add(j)
            uniq.append((j, e))
    return uniq, missing


def _entry_lines(e: dict) -> list[str]:
    """核对单里的一条:全文摆开 —— 删错/改错一条真日历,代价远大于多念两行。"""
    span = str(e.get("start") or "") + (" ~ " + str(e.get("end")) if e.get("end") else "")
    lines = [f"- {e.get('name')} [{e.get('kind')}] {span}(id: {e.get('id')})"]
    if e.get("quote"):
        lines.append(f"  依据:{e['quote']}")
    lines.append(f"  来源:{e.get('source')}(可信度 {e.get('credibility')})")
    if e.get("note"):
        lines.append(f"  备注:{e['note']}")
    return lines


def _write_events(path, data: dict) -> "str | None":
    """写回校历文件;失败返回错误说明(文件保持原样)。"""
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                              encoding="utf-8")
    except OSError as e:
        return f"{type(e).__name__}: {e}"
    return None


def _backup(path) -> "str | None":
    """改写前备份到 kb/history/,只留最近 BACKUP_KEEP 份。

    备份是删除/修订**唯一的回滚路** —— 备份写不进去就返回 None,调用方
    必须拒绝改写(宁可不改,不做不可回滚的删除)。文件名带秒级时刻,
    同一秒内两次改写会撞名 —— 撞了加序号(与 unique_target 同一思路):
    每个核对点的"改写前状态"都是独立的回滚点,不许互相覆盖。
    """
    import shutil
    bdir = Path(path).parent / "history"
    try:
        bdir.mkdir(parents=True, exist_ok=True)
        base = Path(path).stem + "-" + time.strftime("%Y%m%d-%H%M%S")
        target = bdir / (base + ".json")
        n = 2
        while target.exists():
            target = bdir / f"{base}-{n}.json"
            n += 1
        shutil.copy2(path, target)
        olds = sorted(bdir.glob(Path(path).stem + "-*.json"))
        for old in olds[:-BACKUP_KEEP]:
            try:
                old.unlink()
            except OSError:
                pass                       # 清不掉的旧备份不挡正事
        return str(target)
    except OSError:
        return None


def remove_preview_text(targets) -> str:
    """删除核对单:列出**条目全文**与没找到的选择器,用户同意才能 remove_commit。"""
    data, _path = load_events_file()
    events = (data or {}).get("events") or []
    hits, missing = _resolve_targets(targets, events)
    lines: list[str] = []
    if hits:
        lines.append(f"共 {len(hits)} 条将从校历删除,请逐条核对(删了就没了,"
                     "靠备份回滚):")
        for _j, e in hits:
            lines += _entry_lines(e)
        lines.append("确认无误请明确说「确认删除」;")
    else:
        lines.append("没有匹配的条目。")
    lines += [f"⚠ {m}" for m in missing]
    return "\n".join(lines)


def remove_commit(targets) -> dict:
    """把**已核对**的条目从校历删除;备份写不进去就拒绝。再解析一遍(文件即状态)。"""
    data, path = load_events_file()
    if data is None:
        return {"ok": False, "msg": "还没有可修改的校历文件 —— 先录入过才有得删。"}
    events = data.get("events") or []
    hits, missing = _resolve_targets(targets, events)
    if not hits:
        return {"ok": False, "msg": "没有匹配的条目:"
                + (";".join(missing) if missing else "选择器为空")}
    bak = _backup(path)
    if bak is None:
        return {"ok": False, "msg": "错误: 备份写不进去,数据没动 —— "
                                    "删除必须可回溯,先解决 kb/history 的写权限。"}
    drop = {j for j, _e in hits}
    data["events"] = [e for j, e in enumerate(events) if j not in drop]
    err = _write_events(path, data)
    if err:
        return {"ok": False, "msg": f"错误: 写不进去({err}) —— 文件保持原样。"}
    return {"ok": True, "removed": [str(e.get("name")) for _j, e in hits],
            "backup": bak, "missing": missing,
            "msg": f"已删除 {len(hits)} 条,备份在 {bak}。"}


def revise_preview_text(targets, patch) -> str:
    """修订核对单:当前条目全文 + 拟改字段(旧 → 新)+ 校验问题。"""
    data, _path = load_events_file()
    events = (data or {}).get("events") or []
    hits, missing = _resolve_targets(targets, events)
    lines: list[str] = []
    if len(hits) > 1:
        lines.append("匹配到多条,一次只改一条 —— 请用 id 明确指定哪一条:")
        for _j, e in hits:
            lines += _entry_lines(e)
    elif hits:
        merged, problems = _revise_merge(hits[0][1], patch, events, hits[0][0])
        lines.append("当前这一条(改前):")
        lines += _entry_lines(hits[0][1])
        if merged is not None:
            lines.append("修订后:")
            lines += _entry_lines(merged)
        lines += [f"⚠ {p}" for p in problems]
        if merged is not None and not problems:
            lines.append("确认无误请明确说「确认修订」。")
    else:
        lines.append("没有匹配的条目。")
    lines += [f"⚠ {m}" for m in missing]
    return "\n".join(lines)


def _revise_merge(entry: dict, patch, events: list, self_idx: int) -> "tuple[dict | None, list[str]]":
    """把 patch 合并进条目,返回 (合并结果, 问题清单)。**报错不静默**:修订把
    好数据改坏是真事故,kind 不认识、日期不合法、改后撞名都直接拒绝。"""
    problems: list[str] = []
    if not isinstance(patch, dict) or not patch:
        return None, ["patch 必须是对象且至少给一个要改的字段"]
    reason = str(patch.get("reason") or "").strip()
    if not reason:
        return None, ["必须给 reason(一句为什么改)—— 无来由的修订没法核对"]
    unknown = [k for k in patch if k not in _REVISE_FIELDS and k != "reason"]
    if unknown:
        return None, [f"patch 里有改不了的字段:{'、'.join(unknown)}"
                      f"(可改:{'/'.join(_REVISE_FIELDS)})"]
    merged = dict(entry)
    for f in _REVISE_FIELDS:
        if f in patch:
            merged[f] = str(patch[f]).strip()
    for f in ("start", "end"):
        if f in merged and merged[f]:
            try:
                from datetime import date
                date.fromisoformat(str(merged[f]))
            except ValueError:
                problems.append(f"{f} 不是 ISO 日期:{merged[f]}")
    if not problems and merged.get("end") and merged.get("start"):
        from datetime import date
        if date.fromisoformat(str(merged["end"])) < date.fromisoformat(str(merged["start"])):
            problems.append(f"end({merged['end']})早于 start({merged['start']})")
    if merged.get("kind") not in KINDS:
        problems.append(f"kind「{merged.get('kind')}」不认识(可用:{'/'.join(KINDS)})")
    if not merged.get("name") or not merged.get("start"):
        problems.append("name 与 start 不能改空")
    if not problems:
        key = (str(merged.get("name")), str(merged.get("start")))
        for j, e in enumerate(events):
            if j == self_idx or not isinstance(e, dict):
                continue
            if (str(e.get("name") or ""), str(e.get("start") or "")) == key:
                problems.append(f"改后与已有条目撞名同日:{key[0]} {key[1]} —— "
                                "要么换日期/名字,要么先删掉那条")
                break
    if "source" in patch and "credibility" not in patch:
        merged["credibility"] = _credibility_of(str(patch["source"]), "")
    if problems:
        return None, problems
    changed = ";".join(f"{f}: {entry.get(f) or '(空)'}→{merged[f]}"
                       for f in _REVISE_FIELDS
                       if f in patch and str(entry.get(f) or "") != str(merged.get(f) or ""))
    trail = f"{time.strftime('%Y-%m-%d')} 修订:{changed};原因:{reason[:80]}"
    merged["note"] = (trail + (";" + str(entry.get("note")) if entry.get("note") else ""))[:300]
    return merged, []


def revise_commit(targets, patch) -> dict:
    """把**已核对**的修订写回;一次一条,备份写不进去就拒绝。再解析再校验(文件即状态)。"""
    data, path = load_events_file()
    if data is None:
        return {"ok": False, "msg": "还没有可修改的校历文件 —— 先录入过才有得改。"}
    events = data.get("events") or []
    hits, missing = _resolve_targets(targets, events)
    if len(hits) != 1:
        return {"ok": False, "msg": "一次只改一条 —— 匹配到 "
                + (f"{len(hits)} 条" if hits else "0 条")
                + (";" + ";".join(missing) if missing else "")
                + "。请用 id 明确指定。"}
    merged, problems = _revise_merge(hits[0][1], patch, events, hits[0][0])
    if merged is None or problems:
        return {"ok": False, "msg": "修订没通过校验:" + ";".join(problems or ["未知"])}
    bak = _backup(path)
    if bak is None:
        return {"ok": False, "msg": "错误: 备份写不进去,数据没动 —— "
                                    "修订必须可回溯,先解决 kb/history 的写权限。"}
    events[hits[0][0]] = merged
    data["events"] = events
    err = _write_events(path, data)
    if err:
        return {"ok": False, "msg": f"错误: 写不进去({err}) —— 文件保持原样。"}
    return {"ok": True, "backup": bak,
            "msg": (f"已修订「{merged.get('name')}」(备份在 {bak});"
                    "修订轨迹记在条目备注里,倒计时与提醒按新日期走。")}


def calendar_edit_tool(ctx, action: str = "preview", entries=None,
                       targets=None, patch=None) -> str:
    """calendar_edit 工具处理器:preview/remove_preview/revise_preview 生成核对单,
    *_commit 在用户明确同意后才写文件。"""
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
    if action == "remove_preview":
        return remove_preview_text(targets)
    if action == "remove_commit":
        result = remove_commit(targets)
        if not result.get("ok"):
            return f"错误: {result.get('msg')}"
        tail = ("\n没找到的选择器:\n" + "\n".join(f"- {m}" for m in result["missing"])
                if result.get("missing") else "")
        return (f"{result['msg']}\n已删除:\n"
                + "\n".join(f"- {n}" for n in result.get("removed") or [])
                + f"\n(改写前的完整文件备份在 {result.get('backup')},可整份回滚)"
                + tail)
    if action == "revise_preview":
        return revise_preview_text(targets, patch)
    if action == "revise_commit":
        result = revise_commit(targets, patch)
        if not result.get("ok"):
            return f"错误: {result.get('msg')}"
        return result["msg"]
    return ("错误: 未知 action。可用: preview / commit(新增,先念核对单)/ "
            "remove_preview / remove_commit(删除,核对单列条目全文)/ "
            "revise_preview / revise_commit(修订,一次一条,必须给 reason)。")
