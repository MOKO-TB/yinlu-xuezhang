"""校园校历(kb/school-calendar.json):放假/考试/截止事件与倒计时。

日期是低频、可手工维护的事实,刻意与爬虫语料分开:维护者直接编辑
kb/school-calendar.json 就能更新,不必重跑爬虫。加载优先 exe 旁的文件
(打包后可就地改),没有再退回打包内置资源。

诚实性约束(与校园地图同一口径):给学生看的日期宁缺毋滥 —— 数据文件里
只应有**有官方来源**的日期;credibility: A=官方来源,B=待核实。工具输出
必须保留来源与可信度,数据里没有的日期一律引导"以教务处通知为准",绝不编。
"""
from __future__ import annotations

import json
import time
from datetime import date, timedelta
from pathlib import Path

from minicode.paths import app_path, resource_path

CALENDAR_FILE = "kb/school-calendar.json"
UPCOMING_DAYS = 120        # "接下来有什么"的默认 lookahead(约一学期)
YEARLY_LOOKAHEAD = 370     # 每年重复事件的 lookahead:一整圈,保证"下一次"总在视野里
DEFAULT_LIMIT = 8

KIND_LABELS = {
    "holiday": "放假", "exam": "考试", "term": "学期节点",
    "deadline": "报名/截止", "event": "校园活动",
}


def _candidate_paths() -> list[Path]:
    return [app_path(CALENDAR_FILE), resource_path(CALENDAR_FILE)]


def load_events() -> tuple[list[dict], str]:
    """加载校历事件列表。返回 (events, source);文件缺失/损坏返回 ([], 原因)。

    文件格式: {"notes": "维护说明…", "events": [{id,name,start,end,kind,note,source,credibility}]}
    """
    for path in _candidate_paths():
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return [], f"校历文件读不出来({type(exc).__name__})"
        events = data.get("events") if isinstance(data, dict) else data
        if not isinstance(events, list):
            return [], "校历文件格式不对(events 不是列表)"
        return [e for e in events if isinstance(e, dict)], str(path)
    return [], "未找到校历文件"


def _parse_date(value) -> date | None:
    try:
        return date.fromisoformat(str(value or "").strip())
    except ValueError:
        return None


def _shift_year(d: date, year: int) -> date:
    """把日期搬到另一年的同月同日;2/29 落到非闰年时收在 2/28,不抛异常。"""
    try:
        return d.replace(year=year)
    except ValueError:
        return d.replace(year=year, day=28)


def is_yearly(e: dict) -> bool:
    return str(e.get("repeat") or "").strip().lower() == "yearly"


def expand_repeats(events: list[dict], today: date) -> list[dict]:
    """把 repeat=yearly 的事件投影成"去年/今年/明年各一次"的具体日期。

    为什么要有这个:心理普查、运动会、社团招新这类事**每年都有,但没人每年去
    改校历文件**。校历里记一次月日,往后每年自动有个倒计时;真换了日期,改的
    还是那一条。投影出来的条目一律带 `projected` 与 `anchor`,输出时必须说明
    "这是按某年那条推的,不是今年的官方通知" —— 否则就是在替学校编日期。
    """
    out: list[dict] = []
    for e in events:
        start = _parse_date(e.get("start"))
        if not is_yearly(e):
            out.append(e)
            continue
        if start is None:
            out.append(e)          # 没锚点日期:交给 pending,不替它编一个
            continue
        end = _parse_date(e.get("end")) or start
        span = max(0, (end - start).days)
        for dy in (-1, 0, 1):
            s = _shift_year(start, today.year + dy)
            out.append({**e,
                        "start": s.isoformat(),
                        "end": (s + timedelta(days=span)).isoformat() if span else "",
                        "projected": True, "anchor": str(e.get("start"))})
    return out


def days_left(start: date, today: date) -> int:
    return (start - today).days


def _enrich(events: list[dict], today: date) -> list[dict]:
    """补上解析好的日期与倒计时字段(不修改原数据)。"""
    out = []
    for e in expand_repeats(events, today):
        start = _parse_date(e.get("start"))
        end = _parse_date(e.get("end")) or start
        item = {**e, "start_date": start, "end_date": end}
        if start:
            item["days_left"] = days_left(start, today)
        out.append(item)
    return out


def upcoming(events: list[dict], today: date, limit: int = DEFAULT_LIMIT,
             within_days: int = UPCOMING_DAYS,
             yearly_within: int | None = None) -> list[dict]:
    """还没开始的未来事件,按日期升序。

    `yearly_within`:每年重复的条目要不要用更宽的窗口。**默认不放宽**,由调用方明说。
    为什么:列表视图("接下来有什么")该让 5·25 这类每年例行的事常驻 —— 不放宽的话
    它在 9 月是 248 天后,超出 120 天窗口,于是**每年都有的事反而常年不出现**;
    但 7 天倒计时那类窄窗口**绝不能**放宽 —— 我们推出来的日子不是学校通知过的日子,
    拿它去"还有 3 天"地推人,就是在替学校编日期。
    """
    def _ok(e: dict) -> bool:
        if not e.get("start_date") or e["days_left"] < 0:
            return False
        cap = (max(within_days, yearly_within) if yearly_within and e.get("projected")
               else within_days)
        return e["days_left"] <= cap

    rows = [e for e in _enrich(events, today) if _ok(e)]
    rows.sort(key=lambda e: e["start_date"])
    return rows[:max(1, limit)]


def current(events: list[dict], today: date) -> list[dict]:
    """正在进行中(今天在 start..end 区间内)的事件。"""
    rows = [e for e in _enrich(events, today)
            if e.get("start_date") and e.get("end_date")
            and e["start_date"] <= today <= e["end_date"]]
    rows.sort(key=lambda e: e["start_date"])
    return rows


def pending(events: list[dict]) -> list[dict]:
    """没有日期(或日期不合法)的条目:只展示名字,不猜日期。"""
    return [e for e in events if _parse_date(e.get("start")) is None]


def _fmt_event(e: dict, today: date) -> str:
    kind = KIND_LABELS.get(str(e.get("kind") or ""), "事件")
    cred = str(e.get("credibility") or "").strip() or "未标注"
    name = str(e.get("name") or "(未命名)")
    start, end = e.get("start_date"), e.get("end_date")
    span = str(e.get("start") or "?")
    if end and start and end != start:
        span = f"{e['start']} ~ {e['end']}"
    left = e.get("days_left")
    when = f"{span}(还有 {left} 天)" if left and left > 0 else (
        f"{span}(进行中)" if left == 0 else span)
    lines = [f"- {name} [{kind}]"
             + (" [每年重复]" if e.get("projected") else "") + f" {when}"]
    if e.get("projected"):
        lines.append(f"  口径:这条按校历里 {e.get('anchor')} 那次的月日推出来的,"
                     "今年具体哪天还没有官方通知 —— 说日子时带上这层意思。")
    if e.get("note"):
        lines.append(f"  说明: {e['note']}")
    if e.get("source"):
        lines.append(f"  来源: {e['source']}(可信度 {cred})")
    return "\n".join(lines)


def format_for_model(events: list[dict], today: date, limit: int = DEFAULT_LIMIT) -> str:
    """给模型的校历文本: upcoming + 待定条目 + 诚实性约束。"""
    if not events:
        return ("校历数据为空(kb/school-calendar.json 没有条目)。"
                "用户问放假/考试时间时,如实说「校历里还没录入,以学校教务处通知为准」,不要编日期。")
    ups = upcoming(events, today, limit=limit, yearly_within=YEARLY_LOOKAHEAD)
    now = current(events, today)
    pends = pending(events)
    blocks = []
    if now:
        blocks.append("正在进行中:\n" + "\n".join(_fmt_event(e, today) for e in now))
    if ups:
        blocks.append("接下来的安排(按日期升序):\n" + "\n".join(_fmt_event(e, today) for e in ups))
    else:
        blocks.append("接下来 UPCOMING 天内没有已录入的安排。".replace("UPCOMING", str(UPCOMING_DAYS)))
    if pends:
        names = "、".join(str(e.get("name") or "?") for e in pends[:10])
        blocks.append(f"以下事项在校历里登记了但**还没有日期**: {names} —— "
                      f"被问到时如实说「日期还没录入,以教务处通知为准」,不要推测。")
    return ("\n".join(blocks)
            + "\n\n(以上是校历文件的全部已知信息;来源与可信度随条目给出,"
              "转述时保留;校历里没有的日期不要编。)")


def calendar_payload(limit: int = 3, today: date | None = None) -> list[dict]:
    """首页倒计时 chips 的数据:最近的至多 limit 条未来事件。"""
    events, _src = load_events()
    today = today or date.today()
    out = []
    for e in upcoming(events, today, limit=limit, yearly_within=YEARLY_LOOKAHEAD):
        out.append({
            "name": str(e.get("name") or ""),
            "start": str(e.get("start") or ""),
            "kind": str(e.get("kind") or ""),
            "days_left": int(e.get("days_left") or 0),
            "note": str(e.get("note") or "")[:60],
            # 投影出来的日子不是今年的官方通知:首页 chip 要能区分这两件事
            "projected": bool(e.get("projected")),
            "anchor": str(e.get("anchor") or ""),
        })
    return out


def campus_calendar(ctx, action: str = "upcoming", query: str = "",
                    limit: int = DEFAULT_LIMIT,
                    today: date | None = None) -> str:
    """campus_calendar 工具处理器(today 仅供测试注入,工具调用不传)。"""
    events, _src = load_events()
    today = today or date.today()
    action = (action or "upcoming").strip().lower()
    if action == "query":
        keyword = (query or "").strip().lower()
        if not keyword:
            return "错误: action=query 需要给 query 关键词(如: 国庆 / 四六级 / 考试周)。"
        hits = [e for e in events
                if keyword in str(e.get("name") or "").lower()
                or keyword in str(e.get("note") or "").lower()
                or keyword in str(e.get("kind") or "").lower()]
        if not hits:
            return (f"校历里没有与「{query}」相关的条目。"
                    "如实告诉用户该日期还没录入、以教务处通知为准,不要编日期。")
        return format_for_model(hits, today, limit=max(1, int(limit)))
    # upcoming(默认):正在进行 + 未来
    return format_for_model(events, today, limit=max(1, int(limit)))


def calendar_prompt_section() -> str:
    """系统提示词里的校历段(桌面与网页版共用;不带时间,不破坏 KV Cache 前缀)。"""
    return (
        "\n# 校历(放假 / 考试 / 截止)\n"
        "- 手上有校历数据(kb/school-calendar.json,带来源与可信度)。用户问"
        "「什么时候放假 / 四六级什么时候考 / 还有几天到 XX」→ 先调 campus_calendar"
        "(query 按关键词查,upcoming 看接下来的安排),不要凭印象报日期。\n"
        "- 校历里没有的日期(如期中期末考试周、寒暑假起止)如实说「校历还没录入,"
        "以学校教务处通知为准」,绝不要编具体日期。\n"
        "- 用户问绩点/加权平均 → 把成绩整理成列表用 gpa_calc 算,并说明"
        "「以学校教务处的绩点算法为准」。\n"
    )
