"""今日简报:每天一回,把"今天跟你有关的事"聚成一张卡片。

课表、天气、待办、校历原本各推各的,同学一天收到好几条提醒,却拼不出
今天的全貌。本模块在启动看门狗第一轮把它们**聚合成一次推送**(proactive
kind="briefing"):
- 课表:今天几节课、最早一节几点在哪(没登记钟点的课只报节次,绝不编时间),
  外加今天有几处要换教学楼(地点判不出是哪栋楼的课不参与,也不硬凑一个数);
- 天气:只读首页 chip 那份缓存,绝不为简报当场等网络(没缓存就省略这段);
- 待办:临期与逾期的件数加最急一件;
- 校历:7 天内最近的一件倒计时。

诚实性与边界:四项都只列**真查到的**,全空返回 None(不推空卡);
简报推成功后,本轮不再单独推 课表/待办/校历 三条(内容重复,显示两遍是打扰);
官网新通知/新版本/课前到点 属事件类推送,与简报互不影响。
"""
from __future__ import annotations

from datetime import date

_WEEKDAY = "一二三四五六日"


# ---------------------------------------------------------------- 四段数据

def _schedule_bits(today: date, detail: list[str]) -> tuple[str, str] | None:
    """(标题短签, 简报短语) —— 没登记课表返回 None,简报里没有课程段。"""
    from minicode import schedule as _schedule
    data = _schedule.load()
    if not data:
        return None
    wk = _schedule.week_of(today, data)
    head = f"周{_WEEKDAY[today.weekday()]}" + (f" · 第 {wk} 周" if wk else "")
    rows = _schedule.classes_on(today, data)
    if not rows:
        short = "今天没课"
    else:
        first = rows[0]
        if first.get("start_time"):
            name_part = f"{first['start_time']} {first['name']}"
        elif first.get("periods"):
            name_part = f"{first['name']}(第 {first['periods']} 节)"
        else:
            name_part = first["name"]                     # 没钟点没节次:只报名字
        loc = f" · {first['location']}" if first.get("location") else ""
        short = f"今天 {len(rows)} 节课,最早 {name_part}{loc}"
    detail.append("【今天日程】" + _schedule.format_day(today, data))
    from minicode import walk as _walk
    risk = _walk.day_risks(today, data)
    if risk.get("items"):
        short += f",{len(risk['items'])} 处要换教学楼"
        for it in risk["items"][:3]:
            detail.append("· 换楼:" + _walk.item_text(it))
        if risk.get("items_more"):
            detail.append(f"· 另有 {risk['items_more']} 处没列出")
    if risk.get("clashes"):
        detail.append(f"· 另有 {len(risk['clashes'])} 处同一节排了两门课(课表本身撞了)")
    for s in (risk.get("skipped") or [])[:2]:
        detail.append(f"· {s['name']} 的地点判不出是哪栋楼:{s['why']}")
    return head, short


def _weather_bits(today: date, detail: list[str]) -> str | None:
    """只读缓存的 chip 数据;没有缓存返回 None(不为简报等网络)。"""
    from minicode import weather as _weather
    card = _weather.weather_card(today)
    if not card:
        return None
    short = f"{card['icon']} {card['text']}"
    if card.get("umbrella"):
        short += ",记得带伞"
    tip = f" {card['tip']}" if card.get("tip") else ""
    detail.append(f"【天气】{card['icon']} {card['text']}{tip}")
    return short


def _reminders_bits(today: date, detail: list[str]) -> str | None:
    from minicode import reminders as _reminders
    near = _reminders.due_soon(today=today)
    if not near:
        return None

    def _when(it: dict, clock_too: bool = False) -> str:
        d = int(it.get("days_left", 0))
        base = f"已过截止 {-d} 天" if d < 0 else ("今天截止" if d == 0 else f"还有 {d} 天")
        # 卡片短句只在"就是今天"时带钟点(省字);明细永远带 —— 用户记下的
        # "明天 09:00" 丢了就白记了。没记钟点的条目两处都不出现时间。
        clock = str(it.get("due_at") or "")[11:16]
        if clock and (clock_too or d == 0):
            return f"{base} {clock}"
        return base

    top = near[0]
    short = f"待办 {len(near)} 件临期({top['title']} {_when(top)})"
    detail.append("【临期待办】\n" + "\n".join(
        f"- {it['title']}(截止 {_when(it, clock_too=True)}){it.get('note') or ''}"
        for it in near[:5]))
    return short


def _calendar_bits(today: date, detail: list[str]) -> str | None:
    from minicode import proactive as _proactive
    alerts = _proactive.calendar_alerts(today=today)
    if not alerts:
        return None
    e = alerts[0]
    days = int(e.get("days_left") or 0)
    label = "就是今天" if days == 0 else f"还有 {days} 天"
    short = f"{e['name']}{label}"

    def _line(a: dict) -> str:
        d = int(a.get("days_left") or 0)
        lbl = "就是今天" if d == 0 else f"还有 {d} 天"
        return f"- {a['name']}({a['start']},{lbl})"

    detail.append("【校历临近】\n" + "\n".join(_line(a) for a in alerts))
    return short


# ---------------------------------------------------------------- 聚合出口

def build(today: date | None = None) -> dict | None:
    """聚合今日简报。返回 {text, ask};四段全空返回 None(不推空卡)。"""
    today = today or date.today()
    detail: list[str] = []
    parts: list[str] = []

    sched = _schedule_bits(today, detail)
    if sched:
        head, short = sched
        parts.append(head)
        parts.append(short)
    for bit in (_weather_bits(today, detail),
                _reminders_bits(today, detail),
                _calendar_bits(today, detail)):
        if bit:
            parts.append(bit)
    if not parts:
        return None
    text = " · ".join(parts)
    ask = ("帮我把今天的安排理一理:\n" + "\n".join(detail) +
           "\n按时间顺序说说:先做什么、几点从宿舍出门比较稳、别漏带什么。"
           if detail else
           "帮我把今天的安排理一理,顺便看看有没有我该提前准备的事。")
    return {"text": f"今日简报:{text}", "ask": ask, "date": today.isoformat()}
