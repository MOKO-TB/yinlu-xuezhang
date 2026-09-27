"""把本机日程导出成 .ics 日历文件(RFC 5545,纯标准库,零新依赖)。

为什么要有它:课表/待办/校历的提醒都长在学长身上 —— 学长没在跑,提醒就不存在。
v0.9.26 的开机自启把"学长没跑"缩到"开机自启没开"这一种情况,这一步把兜底做穿:
导出一个 .ics 文件让同学导进 Windows 日历/手机,到点响的是**系统**,
学长在不在、电脑开没开都一样(v0.9.42 方案 §A5 的 .ics 兜底半件)。

三条诚实边界,与生成它的三个模块同一条线:
- **没有真实钟点就不写钟点**:课的钟点只认 `schedule.row_clock`(课表 + 作息表
  算出来的事实);没填作息表的课按**全天事件**导(那天有这节课,但没有精确时刻),
  绝不替它编一个 45 分钟 —— 回话里会点名哪几门降了级、怎么补。
- **自己推的日子不外发**:校历里 `repeat:"yearly"` 的投影条目(按往年月日推的,
  学校今年还没通知)一律不导 —— 写进系统日历就成了"每天照着过的事实",
  比聊天里一句话更难收回(与 `calendar_alerts` 过滤投影同一口径)。
- **无日期的事项不导**:连日期都没有的待办进了日历也没处放,回话里如实报数。

一处真相:课表读 `schedule.load()`、待办读 `reminders.list_items()`、
校历读 `calendar.load_events()`、作息表读 `schedule.load_period_times()` ——
本模块只做**格式转换**,不新增任何判断口径。UID 按"种类|标题|首次时刻|RRULE"
确定性生成:同一份日程重复导出 UID 不变,日历软件按 UID 去重/更新,
反复导入不会越堆越多。
"""
from __future__ import annotations

import hashlib
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ICS_FILE_NAME = "引路学长日历.ics"
ALL_DAY_FALLBACK = True      # 没钟点的课降级成全天事件(回话里明说),而不是跳过

_BYDAY = {1: "MO", 2: "TU", 3: "WE", 4: "TH", 5: "FR", 6: "SA", 7: "SU"}


# ---------------------------------------------------------------- 文本层(RFC 5545)

def _esc(text) -> str:
    """TEXT 值转义:反斜杠/分号/逗号/换行,照 RFC 5545 §3.3.11。"""
    return (str(text or "")
            .replace("\\", "\\\\")
            .replace(";", "\\;")
            .replace(",", "\\,")
            .replace("\r", "")
            .replace("\n", "\\n"))


def _fold(line: str) -> list[str]:
    """超过 75 **字节**的行折成 continuation(后续行以空格开头)。

    按**字节**折而不是按字符 —— 中文一条就超,UTF-8 一个汉字 3 字节,
    在多字节中间下刀会让某些导入端读到坏字符,所以回退到字符边界。
    """
    data = line.encode("utf-8")
    if len(data) <= 75:
        return [line]
    out: list[str] = []
    first = True
    while data:
        limit = 75 if first else 74
        cut = min(limit, len(data))
        while cut < len(data) and (data[cut] & 0xC0) == 0x80:
            cut -= 1                  # 不劈开多字节字符
        out.append(("" if first else " ") + data[:cut].decode("utf-8"))
        data = data[cut:]
        first = False
    return out


def _dt(value: datetime) -> str:
    """浮动的本地时刻(无时区后缀):导入端按本机时区理解,正是同学想要的。"""
    return value.strftime("%Y%m%dT%H%M%S")


def _d(value: date) -> str:
    return value.strftime("%Y%m%d")


def _stamp() -> str:
    """DTSTAMP 必须 UTC(RFC 5545 §3.8.7.2),与浮动的事件时刻不冲突。"""
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def _uid(event: dict) -> str:
    raw = f"{event['kind']}|{event['title']}|{_when_key(event)}|{event.get('rrule') or ''}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest() + "@yinluxs.local"


def _when_key(event: dict) -> str:
    start = event["start"]
    return _d(start) if event.get("all_day") else _dt(start)


def render(events: list[dict]) -> str:
    """事件列表 → .ics 全文(CRLF 行尾 + 75 字节折行,导入端要什么给什么)。"""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0",
             "PRODID:-//yinlu-xuezhang//yinluxs ics//CN",
             "CALSCALE:GREGORIAN",
             "X-WR-CALNAME:" + _esc("引路学长")]
    for e in events:
        lines += ["BEGIN:VEVENT", "UID:" + _uid(e),
                  "DTSTAMP:" + _stamp(),
                  "SUMMARY:" + _esc(e["title"])]
        if e.get("all_day"):
            lines.append("DTSTART;VALUE=DATE:" + _d(e["start"]))
            end = e.get("end") or e["start"]
            # 全天 DTEND 是**排他**的:跨天事件要写到最后一日的下一天(RFC 5545 §3.6.1)
            lines.append("DTEND;VALUE=DATE:" + _d(end + timedelta(days=1)))
        else:
            lines.append("DTSTART:" + _dt(e["start"]))
            lines.append("DTEND:" + _dt(e["end"]))
        if e.get("rrule"):
            lines.append("RRULE:" + e["rrule"])
        if e.get("location"):
            lines.append("LOCATION:" + _esc(e["location"]))
        if e.get("description"):
            lines.append("DESCRIPTION:" + _esc(e["description"]))
        if e.get("alarm"):
            lines += ["BEGIN:VALARM", "ACTION:DISPLAY",
                      "TRIGGER:" + e["alarm"],
                      "DESCRIPTION:" + _esc(e["title"]),
                      "END:VALARM"]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    out: list[str] = []
    for line in lines:
        out.extend(_fold(line))
    return "\r\n".join(out) + "\r\n"


# ---------------------------------------------------------------- 数据层(只做转换)

def _week_runs(weeks: "set[int]") -> "list[tuple[int, int]]":
    """{1,2,3,5,6} → [(1,3),(5,6)]:连续周并成一段(RRULE),断档拆开,不假装连上。"""
    runs: list[tuple[int, int]] = []
    for w in sorted(weeks):
        if runs and w == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], w)
        else:
            runs.append((w, w))
    return runs


def _course_events(today: date) -> "tuple[list[dict], list[str]]":
    """课表 → 事件。带作息表钟点的按时刻,否则降级全天;周次断档拆成多个 RRULE 段。"""
    from minicode import schedule as _schedule
    notes: list[str] = []
    data = _schedule.load()
    if not data or not data.get("classes"):
        return [], ["课表还没录入,课的部分是空的(问学长「把课表截图发你」可以录)"]
    try:
        term = date.fromisoformat(str(data.get("term_start") or ""))
    except ValueError:
        return [], ["课表的开学日期(第 1 周周一)没录对,课导不出来 —— 到课表面板补一次"]
    times = _schedule.load_period_times()
    events: list[dict] = []
    downgraded: list[str] = []
    for row in data["classes"]:
        weeks = _schedule.parse_weeks(row.get("weeks")) or set()
        runs = _week_runs(weeks)
        if not runs:
            continue
        clock = _schedule.row_clock(row, times)
        where = str(row.get("location") or "")
        for lo, hi in runs:
            first = term + timedelta(days=(lo - 1) * 7 + (int(row["day"]) - 1))
            rrule = (f"FREQ=WEEKLY;COUNT={hi - lo + 1}"
                     if hi > lo else None)
            if clock:
                start_hm, end_hm = clock
                h1, m1 = int(start_hm[:2]), int(start_hm[3:5])
                h2, m2 = int(end_hm[:2]), int(end_hm[3:5])
                events.append({
                    "kind": "course", "title": str(row["name"]),
                    "start": datetime(first.year, first.month, first.day, h1, m1),
                    "end": datetime(first.year, first.month, first.day, h2, m2),
                    "all_day": False, "rrule": rrule,
                    "location": where,
                    "description": "来源:本机课表" + (f" · {row['teacher']}"
                                                     if row.get("teacher") else ""),
                    "alarm": "-PT30M"})
            else:
                events.append({
                    "kind": "course", "title": str(row["name"]),
                    "start": first, "end": None, "all_day": True,
                    "rrule": rrule, "location": "",
                    "description": "全天标记:那天有这节课,钟点见学长课表(没填作息表,"
                                   "这里不编一个时间)", "alarm": None})
                if str(row["name"]) not in downgraded:
                    downgraded.append(str(row["name"]))
    if downgraded:
        shown = "、".join(downgraded[:5]) + ("等" if len(downgraded) > 5 else "")
        notes.append(f"{len(downgraded)} 门课没填作息表,按全天事件导(那天有课,"
                     f"但没有精确钟点):{shown} —— 课表面板「填作息表」后重新导一次就有钟点")
    return events, notes


def _reminder_events(today: date) -> "tuple[list[dict], list[str]]":
    """待办 → 事件。截止是"一个点",用 30 分钟的标记块 + 到点响铃表达,说明里写清。"""
    from minicode import reminders as _reminders
    notes: list[str] = []
    events: list[dict] = []
    skipped = 0
    for item in _reminders.list_items():
        when = _reminders._due_at(item)
        if when is not None:
            rrule = None
            repeat = str(item.get("repeat") or "")
            if repeat == _reminders.REPEAT_WEEKLY:
                rrule = "FREQ=WEEKLY"
            elif repeat.startswith("every:"):
                rrule = f"FREQ=DAILY;INTERVAL={repeat.split(':', 1)[1]}"
            events.append({
                "kind": "reminder", "title": f"截止:{item['title']}",
                "start": when, "end": when + timedelta(minutes=30),
                "all_day": False, "rrule": rrule, "location": "",
                "description": "这是截止时刻的日历标记(块长 30 分钟不是要做半小时)"
                              + (f" · {item['note']}" if item.get("note") else ""),
                "alarm": "PT0S"})
        elif str(item.get("due") or ""):
            try:
                d = date.fromisoformat(str(item["due"]))
            except ValueError:
                skipped += 1
                continue
            events.append({"kind": "reminder", "title": f"截止:{item['title']}",
                           "start": d, "end": None, "all_day": True,
                           "rrule": None, "location": "",
                           "description": "只有日期的待办,按天标记(学长那边它按天催)",
                           "alarm": None})
        else:
            skipped += 1
    if skipped:
        notes.append(f"{skipped} 条待办没有可解析的日期,没导 —— 日期确认后重新导一次")
    return events, notes


def _calendar_events(today: date) -> "tuple[list[dict], list[str]]":
    """校历 → 全天事件。**投影条目一律不导**(自己推的日子不写进系统日历)。"""
    from minicode import calendar as _calendar
    events, source = [], ""
    rows, source = _calendar.load_events()
    seen: set[tuple[str, str]] = set()
    projected = 0
    for e in _calendar.current(rows, today) + _calendar.upcoming(rows, today):
        if e.get("projected"):
            projected += 1
            continue
        key = (str(e.get("name") or ""), str(e.get("start") or ""))
        if key in seen or not key[0]:
            continue
        seen.add(key)
        end = e.get("end_date") or e.get("start_date") or None
        desc = f"来源:{e.get('source') or '校历文件'}(可信度 {e.get('credibility') or '未标注'})"
        if e.get("note"):
            desc += f" · {e['note']}"
        events.append({"kind": "calendar", "title": str(e.get("name") or ""),
                       "start": e["start_date"], "end": end, "all_day": True,
                       "rrule": None, "location": "", "description": desc,
                       "alarm": None})
    if projected:
        notes = [f"校历里 {projected} 条是按往年推的投影日子(学校还没通知),"
                 "没有导 —— 导进系统日历就成了\"每年照着过\"的事实"]
    else:
        notes = []
    return events, notes


def collect(today: date | None = None) -> "tuple[list[dict], dict]":
    """汇总三路数据 → (事件列表, 报告)。报告里每一条 skipped/降级都带原因。"""
    today = today or date.today()
    events: list[dict] = []
    notes: list[str] = []
    counts: dict[str, int] = {}
    for name, getter in (("course", _course_events), ("reminder", _reminder_events),
                         ("calendar", _calendar_events)):
        got, why = getter(today)
        events.extend(got)
        counts[name] = len(got)
        notes.extend(why)
    events.sort(key=lambda e: (e["start"], e["kind"]))
    return events, {"counts": counts, "notes": notes}


def export(workspace: "Path", today: date | None = None) -> dict:
    """生成并写工作区里的 .ics;返回报告(路径/条数/诚实说明)。"""
    events, report = collect(today)
    path = Path(workspace) / ICS_FILE_NAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render(events), encoding="utf-8", newline="")
    except OSError as exc:
        return {"ok": False, "msg": f"写不进去:{type(exc).__name__}"}
    return {"ok": True, "path": str(path), "events": len(events), **report}


def export_ics_tool(ctx) -> str:
    """export_ics 工具处理器:导出 + 把报告拍成人话。"""
    result = export(ctx.workspace)
    if not result.get("ok"):
        return f"错误: {result.get('msg')}"
    counts = result.get("counts") or {}
    lines = [f"已导出 {result.get('events', 0)} 个事件到工作区「{ICS_FILE_NAME}」:"]
    detail = []
    if counts.get("course"):
        detail.append(f"课表 {counts['course']} 条")
    if counts.get("reminder"):
        detail.append(f"待办截止 {counts['reminder']} 条")
    if counts.get("calendar"):
        detail.append(f"校历 {counts['calendar']} 条")
    lines.append("- " + ("、".join(detail) if detail else "(本机还没有任何日程)"))
    lines += [f"- {n}" for n in result.get("notes") or []]
    lines.append("导入方法:双击文件 Windows 日历/Outlook 会收;发到手机用「日历」App 打开。"
                 "重复导出不会堆重(按 UID 更新)。")
    return "\n".join(lines)
