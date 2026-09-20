"""学生个人课表(schedule.json):存下来后,学长随时答"今天有什么课/明天第一节"。

schedule-helper 技能只负责把课表**解析**清楚;没有存储时,每次问"今天有什么课"
都要模型重新从聊天记录里翻,上下文一压缩就丢。本模块把解析结果(连同
"第 1 周周一是哪天"这个锚点)存进应用目录 —— 文件即状态、手工可编辑,
与 reminders.json 同一惯例:

    {"term_start": "2026-09-07", "updated_at": "2026-09-20 08:00:00",
     "classes": [{"name": "高等数学", "day": 1, "periods": "1-2",
                   "start_time": "08:00", "weeks": "1-16(单周)",
                   "location": "思齐楼B203", "teacher": "..."}]}

周次推算:周次 = (今天 - 第1周周一) // 7 + 1;weeks 支持 "1-16"、
"1-8,11-16"、"1-16(单周)/(双周)" 这类写法。
诚实性约束(与校历同一口径):钟点只说课表里有的 —— 没写 start_time 的课
只报第几节,绝不编"8:00 上课"(作息时间表以教务处为准)。
"""
from __future__ import annotations

import json
import re
import time
from datetime import date, timedelta
from pathlib import Path

from minicode.paths import app_path

SCHEDULE_FILE = "schedule.json"
DEFAULT_WEEKS = "1-25"          # 没写周次默认全学期(可改文件),不默认"全周无课"

_WEEKDAY_MAP = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}
_RANGE_RE = re.compile(r"(\d+)\s*[-~－—]\s*(\d+)")
_SINGLE_RE = re.compile(r"(?<![\d-])(\d{1,2})(?![\d-])")


def state_path() -> Path:
    return app_path(SCHEDULE_FILE)


# ---------------------------------------------------------------- 加载/保存

def load() -> dict | None:
    """读课表;没有/坏了返回 None(绝不因一个坏文件拦任何功能)。"""
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (isinstance(data, dict) and isinstance(data.get("classes"), list)
            and isinstance(data.get("term_start"), str)):
        return data
    return None


def save(term_start: str, classes: list) -> tuple[bool, str]:
    """校验并覆盖保存。返回 (ok, 给人看的结果说明)。

    单条课里 day/name 不合法就丢弃该条并说明,不整批失败 ——
    模型解析 30 门课错 1 条是常态,别让它重录全部。
    """
    term = _parse_date(term_start)
    if term is None:
        return False, ("错误: term_start 必须是「第 1 周周一」的日期(如 2026-09-07),"
                       "先向同学问清开学第一周是几月几号再存。")
    if not isinstance(classes, list) or not classes:
        return False, "错误: classes 不能为空(先按 schedule-helper 技能把课表整理成条目)。"
    ok_rows, dropped = [], []
    for i, raw in enumerate(classes):
        row = _normalize_class(raw)
        if row is None:
            dropped.append(f"第 {i + 1} 条({str(raw.get('name') if isinstance(raw, dict) else raw)[:20]})")
        else:
            ok_rows.append(row)
    if not ok_rows:
        return False, ("错误: 没有任何一条课通过校验(day 需要 1-7 或 周一~周日,"
                       "name 不能为空)。请修正后重存。")
    data = {"term_start": term.isoformat(),
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "classes": ok_rows}
    try:
        state_path().parent.mkdir(parents=True, exist_ok=True)
        state_path().write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as exc:
        return False, f"错误: 课表写不进去({type(exc).__name__})"
    msg = f"课表已存(共 {len(ok_rows)} 门课,第 1 周周一 = {term.isoformat()})。"
    if dropped:
        msg += f" {len(dropped)} 条没通过校验被跳过: {'; '.join(dropped[:5])}"
    msg += (" 之后问「今天/明天有什么课」不用再看图;课有变动重新 save 覆盖即可。"
            " 现在把结果向同学确认一遍(尤其被跳过的条目)。")
    return True, msg


def clear() -> str:
    try:
        state_path().unlink(missing_ok=True)
    except OSError as exc:
        return f"错误: 删除课表失败({type(exc).__name__})"
    return "课表已清除。"


# ---------------------------------------------------------------- 解析辅助

def _parse_date(value) -> date | None:
    try:
        return date.fromisoformat(str(value or "").strip())
    except ValueError:
        return None


def _norm_day(value):
    """day 归一成 1-7(周一=1)。认 1/7、"3"、"周三"、"日"。"""
    s = str(value if value is not None else "").strip()
    if s.isdigit() and 1 <= int(s) <= 7:
        return int(s)
    s2 = s.replace("星期", "").replace("周", "").replace("礼拜", "")
    return _WEEKDAY_MAP.get(s2)


def parse_weeks(spec) -> set[int] | None:
    """把 "1-16" / "1-8,11-16" / "1-16(单周)" / [1,3] 解析成周次集合。

    单/双周按**周次数字**的奇偶;解析不出来返回 None(调用方按全学期兜底)。
    """
    if isinstance(spec, (list, tuple)):
        nums = {int(w) for w in spec if str(w).strip().isdigit()}
        return nums or None
    text = str(spec or "").strip()
    if not text:
        text = DEFAULT_WEEKS
    parity = None
    if "单" in text:
        parity = "odd"
    elif "双" in text:
        parity = "even"
    core = re.sub(r"[((（\[].*?[))）\]]", "", text)      # 去掉括注再拆
    weeks: set[int] = set()
    for part in re.split(r"[,，、;；]", core):
        part = part.strip()
        if not part:
            continue
        m = _RANGE_RE.search(part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a > b:
                a, b = b, a
            weeks.update(range(max(1, a), min(60, b) + 1))
        else:
            m2 = _SINGLE_RE.search(part)
            if m2:
                n = int(m2.group(1))
                if 1 <= n <= 60:
                    weeks.add(n)
    if not weeks:
        return None
    if parity == "odd":
        weeks = {w for w in weeks if w % 2 == 1}
    elif parity == "even":
        weeks = {w for w in weeks if w % 2 == 0}
    return weeks or None


def _normalize_class(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    day = _norm_day(raw.get("day"))
    if not name or day is None:
        return None
    weeks = parse_weeks(raw.get("weeks"))
    row = {"name": name[:60], "day": day,
           "periods": str(raw.get("periods") or "").strip()[:40],
           "start_time": str(raw.get("start_time") or "").strip()[:5],
           "weeks": str(raw.get("weeks") or "").strip()[:80] or DEFAULT_WEEKS,
           "location": str(raw.get("location") or "").strip()[:60],
           "teacher": str(raw.get("teacher") or "").strip()[:40]}
    if weeks is None and row["weeks"]:
        row["weeks"] = DEFAULT_WEEKS      # 解析不动周次:按全学期上课,漏报好于误报
    return row


# ---------------------------------------------------------------- 查询

def week_of(today: date | None = None, data: dict | None = None) -> int | None:
    """今天是本学期第几周;假期/开学前返回 None。"""
    data = data if data is not None else load()
    if not data:
        return None
    term = _parse_date(data.get("term_start"))
    if term is None:
        return None
    today = today or date.today()
    idx = (today - term).days // 7 + 1
    return idx if idx >= 1 else None


def _week_set(row: dict, data: dict) -> set[int] | None:
    weeks = parse_weeks(row.get("weeks"))
    if weeks is not None:
        return weeks
    # 原文带(单周)/(双周)但整体解析失败时,退到"全学期"
    return parse_weeks(DEFAULT_WEEKS)


def classes_on(day: date, data: dict | None = None) -> list[dict]:
    """某天的课(按周次与星期过滤),排序:有钟点的在前,按钟点→节次。"""
    data = data if data is not None else load()
    if not data:
        return []
    wk = week_of(day, data)
    if wk is None:
        return []
    rows = []
    for row in data.get("classes") or []:
        if row.get("day") != day.weekday() + 1:
            continue
        weeks = _week_set(row, data)
        if weeks and wk in weeks:
            rows.append({**row, "week": wk})
    def _key(r):
        m = re.search(r"\d+", str(r.get("periods") or ""))
        return (0 if r.get("start_time") else 1,
                str(r.get("start_time") or "99:99"),
                int(m.group()) if m else 99)
    rows.sort(key=_key)
    return rows


def _fmt_class(row: dict) -> str:
    bits = [f"{row['start_time']} " if row.get("start_time") else ""]
    line = f"{row['name']}"
    tail = " · ".join(x for x in (
        (row.get("periods") + "节") if row.get("periods") else "",
        row.get("location") or "",
    ) if x)
    return f"- {bits[0]}{line}" + (f"({tail})" if tail else "")


def format_day(day: date, data: dict | None = None) -> str:
    """"周三(第 8 周):两节课…" 一行式日程(提示词与工具共用)。"""
    data = data if data is not None else load()
    wk = week_of(day, data)
    wd = "一二三四五六日"[day.weekday()]
    label = f"{day.isoformat()}(周{wd}" + (f" · 第 {wk} 周" if wk else "") + ")"
    rows = classes_on(day, data)
    if not rows:
        return f"{label} 没有课" if (data and wk) else f"{label} 未排课/不在学期内"
    return f"{label} 共 {len(rows)} 门:\n" + "\n".join(_fmt_class(r) for r in rows)


def next_class_note(today: date | None = None) -> dict | None:
    """看门狗文案:今天有课报今天第一节,今天没课但明天有就报明天第一节。

    没存课表 / 学期都没课上 → None(不推,不打扰)。
    """
    data = load()
    if not data:
        return None
    today = today or date.today()
    for day, head in ((today, "今天"), (today + timedelta(days=1), "明天")):
        rows = classes_on(day, data)
        if not rows:
            continue
        first = rows[0]
        time_part = f"{first['start_time']} " if first.get("start_time") else ""
        loc = f" · {first['location']}" if first.get("location") else ""
        text = (f"课表:{head}第一节课 {time_part}{first['name']}"
                f"{('(' + first['periods'] + '节' + loc + ')') if (first.get('periods') or loc) else ''}")
        ask = (f"帮我把{head}的课表列全,再提醒我第一节要带什么、"
               "提前多久从宿舍出发比较稳。")
        return {"text": text, "ask": ask, "day": day.isoformat(), "head": head}
    return None


# ---------------------------------------------------------------- 面板视图 / 课前提醒

def _parse_hm(value) -> tuple[int, int] | None:
    """把 "8:00"/"08:00" 解析成 (时, 分);认不出返回 None(绝不猜)。"""
    m = re.match(r"^\s*(\d{1,2})[:：](\d{1,2})\s*$", str(value or ""))
    if not m:
        return None
    hh, mm = int(m.group(1)), int(m.group(2))
    return (hh, mm) if 0 <= hh <= 23 and 0 <= mm <= 59 else None


def view(today: date | None = None) -> dict:
    """课表面板的数据:今日/明日 + 本周(周一~周日)网格。

    没登记课表时只回 registered=False(面板据此显示引导,不显示空表)。
    """
    data = load()
    if not data:
        return {"registered": False}
    today = today or date.today()
    wk = week_of(today, data)

    def _pack(day: date) -> dict:
        rows = classes_on(day, data)
        return {"date": day.isoformat(),
                "weekday": "一二三四五六日"[day.weekday()],
                "week": week_of(day, data),
                "classes": rows}

    monday = today - timedelta(days=today.weekday())
    grid = {str(i + 1): _pack(monday + timedelta(days=i)) for i in range(7)}
    return {"registered": True,
            "term_start": data.get("term_start"),
            "updated_at": data.get("updated_at"),
            "week": wk,
            "today": _pack(today),
            "tomorrow": _pack(today + timedelta(days=1)),
            "week_grid": grid}


def upcoming_class(now=None, lead_minutes: int = 20) -> dict | None:
    """课前到点提醒:今天节次里,距开课上不到 lead_minutes 分钟的一节。

    诚实性:只认课表里**确实写了 start_time** 的课 —— 没钟点的课无法定时,
    交给 day 级的 next_class_note,绝不拿"第 1 节"猜成 8:00。
    返回带稳定 key 供看门狗去重(同一节课只提醒一次)。
    """
    from datetime import datetime
    now = now or datetime.now()
    data = load()
    if not data:
        return None
    rows = classes_on(now.date(), data)
    best = None
    for r in rows:
        hm = _parse_hm(r.get("start_time"))
        if hm is None:
            continue
        start = now.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
        minutes_left = round((start - now).total_seconds() / 60)
        if 0 < minutes_left <= lead_minutes and (best is None or minutes_left < best["_m"]):
            best = {**r, "_m": minutes_left, "_start": start}
    if best is None:
        return None
    loc = f" · {best['location']}" if best.get("location") else ""
    text = (f"快上课了:{best['name']} {best['start_time']} 开始"
            f"{loc}(还有约 {best['_m']} 分钟)")
    ask = f"{best['name']}这节课在{best.get('location') or '哪'}上,离现在还有时间,我需要提前带什么?"
    return {"text": text, "ask": ask,
            "name": best["name"], "time": best["start_time"],
            "location": best.get("location", ""), "minutes_left": best["_m"],
            "key": f"{now.date().isoformat()}T{best['start_time']}:{best['name']}"}



# ---------------------------------------------------------------- 提示词与工具

def schedule_prompt_section() -> str:
    """系统提示词的个人课表段(桌面版专用;网页版不注入——课表是个人的)。"""
    data = load()
    if not data:
        return ("\n# 个人课表\n"
                "- 同学还没登记课表。他贴来课表(文字/截图)时按 schedule-helper 技能整理,"
                "并**问清开学第一周的周一日期**,用 schedule(action=save)存下来,"
                "之后「今天有什么课」这类问题不必再看图。截图看不清就如实请他复制成文字。\n")
    wk = week_of()
    today = date.today()
    lines = [f"\n# 个人课表(已登记,存本机)",
             f"- 第 1 周周一 = {data.get('term_start')}" +
             (f";今天(周{'一二三四五六日'[today.weekday()]})是第 {wk} 周" if wk else ";今天不在学期内")]
    lines.append("- 今天日程:\n" + format_day(today, data))
    lines.append("- 明天日程:\n" + format_day(today + timedelta(days=1), data))
    lines.append("- 「今天/明天有什么课、第一节几点」直接照上面的登记内容回答;"
                 "课表里没写钟点的课只报第几节,不编具体时间(作息以教务处为准)。")
    lines.append("- 同学说课表变了(调课/换教室)→ 更新后重新 schedule(action=save) 覆盖;"
                 "要清空用 action=clear。别把整张课表原文贴回回答里,答当前问题即可。")
    return "\n".join(lines) + "\n"


def schedule(ctx, action: str = "save", term_start: str = "",
             classes=None, when: str = "") -> str:
    """schedule 工具处理器:save(存课表)/show(看某天)/clear(清空)。"""
    action = (action or "save").strip().lower()
    if action == "save":
        ok, msg = save(term_start, classes or [])
        return msg if not ok else msg
    if action == "show":
        data = load()
        if not data:
            return ("还没有登记课表。用户发来课表时先按 schedule-helper 技能整理,"
                    "问清第 1 周周一日期,再 action=save 存下来。")
        if when:
            day = _parse_date(when)
            if day is None:
                return "错误: when 需要具体日期(2026-09-22 这种写法)。"
        else:
            day = date.today()
        return format_day(day, data)
    if action == "clear":
        return clear() if load() else "没有已登记的课表,不用清。"
    return "错误: 未知 action。可用: save(存课表)/show(看某天课)/clear(清空)。"
