"""个人截止日期提醒(reminders.json):学长能"记住并主动催"的事。

数据在应用目录(跟 notice-watch.json 同级),文件即状态、手工可编辑:
    {"next_id": 3, "items": [{"id": "r1", "title": "交奖学金材料",
                               "due": "2026-10-08", "due_at": "2026-10-08T17:00",
                               "note": "...", "created_at": "...", "done": false}]}

模型通过 reminder 工具增删改查(用户说「提醒我周五交材料」就记一条),
看门狗(proactive.start_loop)每次运行把临期/逾期的一次性推送出来。

两条时间线,v0.9.29 起各管各的:
- **只有日期**(`due`):按天催 —— 临期(≤3 天)与逾期在每轮看门狗里推一条,
  到日子了也不会知道几点,这是课表/教务数据本身就没给的东西,不假装更精确;
- **还带钟点**(`due_at`):走课前提醒同一条 5 分钟细轮询,到点前 20 分钟催一次,
  错过了且还在补发窗口内则开机补一条 —— 和"快上课了"共用一份按天账本。

日期只认能确证的写法(2026-10-08 / 2026/10/8 / 10-08 补当前年);口语相对时间
(「周五」「下周三」)解析不了就如实存成无日期事项,只列清单不参与倒计时,
绝不猜一个日期出来。**钟点同理**:「下午3点」「17:00」认,光一句「3点」不认
(12 小时制猜错就是差 12 小时),更不会因为"截止一般在下午"就补一个 17:00。

v0.9.56 起支持**循环待办**(`repeat`):「每周」与「每N天」两种规则,必须配一个
能确定的具体日期作锚点(`anchor`)。**存盘的 due/due_at 永远是"下一次"要发生的那次**
—— 这份文件里没有"上一次"的概念;错过且补发窗口也关了的那一班,由看门狗细轮询
(`proactive._tick` 里的 `roll_repeats`)滚到下一次,照校历 `repeat:"yearly"` 的先例
带 `projected` 与 `anchor`。两条诚实边界:①**只往未来滚** —— 用户亲口定的规则,
推出来的**未来**班次照常到点催(这正是功能本身),**过去**的班次绝不补发
(校历那条"自己推的日子不催人"的老规矩,在这里收窄成"过去的不补");
②「每月」这类认不准的规则一律不猜(大小月不是固定天数),先按一次性提醒记并说明原因。
"""
from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from minicode.paths import app_path

REMINDERS_FILE = "reminders.json"
DUE_SOON_DAYS = 3            # 距截止 ≤3 天算"临期"
DUE_LEAD_MINUTES = 20        # 带钟点的待办:提前 20 分钟催(与课前提醒同一节奏)
LATE_WINDOW_MINUTES = 60     # 过期多久之内补一条"错过的"还来得及,超过不打扰
MAX_ITEMS = 200
REPEAT_WEEKLY = "weekly"     # repeat 的规范形态:每周一次;每 N 天一次存成 "every:N"

_DATE_PATTERNS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d")


def state_path() -> Path:
    return app_path(REMINDERS_FILE)


def load_items() -> list[dict]:
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return [i for i in data["items"] if isinstance(i, dict)]
    except (OSError, json.JSONDecodeError):
        pass
    return []


def save_items(items: list[dict]) -> None:
    try:
        state_path().parent.mkdir(parents=True, exist_ok=True)
        state_path().write_text(
            json.dumps({"next_id": _next_id(items), "items": items},
                       ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def _next_id(items: list[dict]) -> int:
    return max([int(i["id"][1:]) for i in items
                if str(i.get("id", "")).startswith("r")
                and str(i.get("id", "")).lstrip("r").isdigit()] or [0]) + 1


def parse_due(raw) -> tuple[str, str]:
    """把用户给的日期解析成 (ISO 日期, 原文);解析不了返回 ("", 原文)。

    支持 2026-10-08 / 2026/10/8 / 2026.10.8 与 10-08、10/8(补当前年)。
    """
    text = str(raw or "").strip()
    if not text:
        return "", ""
    norm = text.replace("年", "-").replace("月", "-").replace("日", "")
    norm = re.sub(r"[./]", "-", norm)
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", norm)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3])).isoformat(), text
        except ValueError:
            return "", text
    m = re.match(r"^(\d{1,2})-(\d{1,2})$", norm)
    if m:
        try:
            return date(date.today().year, int(m[1]), int(m[2])).isoformat(), text
        except ValueError:
            return "", text
    return "", text


# 限定词 + 数字(「下午3点」)、纯数字钟点(「14:00」)、裸数字点(「14点」)。
# 顺序要紧:带限定词的写法先吃掉,否则「下午3点」会被裸分支截成「3点」。
_CN_CLOCK = re.compile(
    r"(?:凌晨|早上|上午|中午|下午|傍晚|晚上)\s*\d{1,2}\s*(?:点钟?|时)(?:半|\d{1,2}分?)?"
    r"|\d{1,2}\s*[:：]\s*\d{1,2}"
    r"|\d{1,2}\s*(?:点钟?|时)(?:半|\d{1,2}分?)?")

_DIGIT = re.compile(r"^(\d{1,2})\s*[:：]\s*(\d{1,2})$")
_WORD = re.compile(r"^(凌晨|早上|上午|中午|下午|傍晚|晚上)?\s*(\d{1,2})\s*"
                   r"(?:点钟?|时)(半|\d{1,2}分?)?$")
# 日期写在句子中间时(parse_due 的整串锚定不吃)兜底抽一次:四位年在前,月日在后。
_DATE_IN_TEXT = re.compile(
    r"\d{4}\s*[-/.年]\s*\d{1,2}\s*[-/.月]\s*\d{1,2}\s*日?"
    r"|\d{1,2}\s*[-/.月]\s*\d{1,2}\s*日?")
_PM = ("下午", "傍晚", "晚上")
_AM = ("凌晨", "早上", "上午")


def parse_clock(raw) -> str:
    """把一个钟点写法拍成 `HH:MM`;**说不准就回 ""**,绝不替他挑一个时间。

    认:`14:00` / `14：00`(全角冒号)/ `14点` / `下午3点` / `3点半` / `晚上8点40`
    / `中午12点`。
    不认:光一句「3点」—— 没有上/下午就没有唯一答案,猜错等于提前或晚整整 12 小时
    催,比不催更糟。数字带冒号(`3:00`)按 24 小时制读,那是电子钟的通用写法。
    """
    text = str(raw or "").strip().replace(" ", "")
    if not text:
        return ""
    m = _DIGIT.match(text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        return f"{h:02d}:{mi:02d}" if 0 <= h <= 23 and 0 <= mi <= 59 else ""
    m = _WORD.match(text)
    if not m:
        return ""
    qual, hour = m.group(1), int(m.group(2))
    minutes = 0
    if m.group(3):
        tail = m.group(3).rstrip("分")
        if tail == "半":
            minutes = 30
        elif tail.isdigit() and int(tail) <= 59:
            minutes = int(tail)
        else:
            return ""
    if qual in _PM:                          # 下午1点=13:00,晚上8点=20:00
        if not 1 <= hour <= 11:
            return ""                        # 「下午13点」「晚上12点」是录错了
        hour += 12
    elif qual in _AM:
        if not 0 <= hour <= 11:
            return ""
    elif qual == "中午":
        if hour not in (11, 12):
            return ""
    elif not 13 <= hour <= 23:               # 没限定词的裸数字:只认 24 小时制的后半段
        return ""
    return f"{hour:02d}:{minutes:02d}"


def parse_when(raw, at: str = "") -> tuple[str, str, str]:
    """自由文本 → (ISO 日期, `HH:MM`, 解析不了的原文)。日期与钟点各自独立判定。

    `at` 是给模型的第二条通道:「due=2026-10-08, at=17:00」比把两样挤在一个串里
    更不容易解析错。**没有日期就没有钟点** —— 只剩一个"17:00"而不知道是哪天,
    定时无从谈起,这时宁可只存进清单,也别拿今天/明天去凑。

    两段互不牵连:钟点认不出(「2026-10-08 3点」这种歧义写法)不该顺手把认得出的
    日期一起丢掉,所以匹配到钟点形状就一律摘掉再解析日期部分。
    """
    text = str(raw or "").strip()
    if not text:
        return "", "", ""
    text = re.sub(r"(?<=\d)[Tt](?=\d)", " ", text)     # 2026-10-08T17:00 那个 T 会粘住日期
    clock = ""
    m = _CN_CLOCK.search(text)
    if m:
        clock = parse_clock(m.group(0))
        text = (text[:m.start()] + " " + text[m.end():]).strip()
    iso, rest = parse_due(text)
    if not iso:
        m = _DATE_IN_TEXT.search(text)                 # 「10月8日前交材料」这种夹带写法
        if m:
            iso, rest = parse_due(m.group(0))
    if not clock and at:
        clock = parse_clock(at)
    return (iso, (clock if iso else ""),
            "" if iso else (rest or str(raw or "").strip()))


_WEEKLY_WORDS = ("每周", "每星期", "每一周", "weekly")
_DAILY_WORDS = ("每天", "每日", "daily")
_EVERY_N_DAYS = re.compile(r"^每?隔?(\d+)天(?:一次)?$")


def parse_repeat(raw) -> str:
    """「每周/每天/每N天」→ 规范形态(`weekly` / `every:N`);认不出回 ""(不猜)。

    循环必须有确凿的规则才存,与日期/钟点同一条诚实边界:**「每月」不认**
    (大小月不是固定天数,硬转成 30 天就是编),「每两周」也不认(要说成
    「每14天」);认不出时 `add` 会存成一次性提醒并把这句话讲给用户听。
    """
    text = str(raw or "").strip().replace(" ", "")
    if not text:
        return ""
    if text.lower() in _WEEKLY_WORDS:
        return REPEAT_WEEKLY
    if text.lower() in _DAILY_WORDS:
        return "every:1"
    if text.isdigit():
        n = int(text)
        return f"every:{n}" if 1 <= n <= 365 else ""
    m = _EVERY_N_DAYS.match(text)
    if m:
        n = int(m.group(1))
        return f"every:{n}" if 1 <= n <= 365 else ""
    return ""


def repeat_days(repeat) -> int:
    """规范 repeat 的周期天数:weekly→7,every:N→N;别的(含空)→0(不循环)。"""
    text = str(repeat or "").strip()
    if text == REPEAT_WEEKLY:
        return 7
    if text.startswith("every:"):
        try:
            n = int(text.split(":", 1)[1])
        except ValueError:
            return 0
        return n if 1 <= n <= 365 else 0
    return 0


def add(title: str, due: str = "", note: str = "", at: str = "",
        repeat: str = "") -> dict:
    items = load_items()
    if len(items) >= MAX_ITEMS:
        return {"ok": False, "msg": f"提醒清单已满({MAX_ITEMS} 条),先清理已完成的"}
    iso, clock, raw = parse_when(due, at=at)
    rep = parse_repeat(repeat)
    item = {"id": f"r{_next_id(items)}", "title": str(title or "").strip()[:120],
            "due": iso, "due_at": (f"{iso}T{clock}" if iso and clock else ""),
            "due_raw": raw if not iso else "",
            # 循环规则必须配得上确凿的锚点日期才存:光有「每周」没有日期,
            # 下一次从哪天起算无从谈起 —— 存成一次性提醒,回话里说明原因。
            "repeat": rep if (rep and iso) else "",
            "anchor": iso if (rep and iso) else "",
            "note": str(note or "").strip()[:200],
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"), "done": False}
    if not item["title"]:
        return {"ok": False, "msg": "事项内容不能为空"}
    items.append(item)
    save_items(items)
    # repeat 回**实际存进条目的那一条**(可能为空):想要「每周」但没给锚点日期时,
    # 存的是一次性提醒,回话必须照实说,不能照搬解析成功的规则。
    return {"ok": True, "item": item, "repeat": item["repeat"],
            "repeat_raw": str(repeat or "").strip()}


def finish(item_id: str, now: "datetime | None" = None) -> dict:
    items = load_items()
    for item in items:
        if item.get("id") == str(item_id).strip():
            nxt = _advance_series(item, now or datetime.now())
            if nxt is not None:
                # 循环待办:完成的是"这一次",不是整个系列 —— 排下一次、
                # 不标 done。要停掉整个循环用 delete(回话里会说清这件事)。
                _apply_next(item, nxt)
                save_items(items)
                return {"ok": True, "item": item, "advanced": True}
            item["done"] = True
            save_items(items)
            return {"ok": True, "item": item}
    return {"ok": False, "msg": f"没有这条提醒:{item_id}"}


def delete(item_id: str) -> dict:
    items = load_items()
    kept = [i for i in items if i.get("id") != str(item_id).strip()]
    if len(kept) == len(items):
        return {"ok": False, "msg": f"没有这条提醒:{item_id}"}
    save_items(kept)
    return {"ok": True}


def list_items(include_done: bool = False) -> list[dict]:
    items = load_items()
    return [i for i in items if include_done or not i.get("done")]


def due_soon(items: list[dict] | None = None, today: date | None = None,
             within_days: int = DUE_SOON_DAYS) -> list[dict]:
    """临期与逾期(未完成):逾期排最前,按紧急度排序;无日期的不参与。"""
    today = today or date.today()
    out = []
    for item in (items if items is not None else list_items()):
        if item.get("done") or not item.get("due"):
            continue
        try:
            due = date.fromisoformat(str(item["due"]))
        except ValueError:
            continue
        days = (due - today).days
        if days <= within_days:
            out.append({**item, "days_left": days})
    out.sort(key=lambda x: x["days_left"])       # 逾期(负数)在最前
    return out


def _due_at(item: dict) -> "datetime | None":
    """把 `due_at` 读成 datetime;没写、写坏了统一给 None(不参与定时)。"""
    text = str(item.get("due_at") or "")
    try:
        return datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def _due_key(item: dict, when: datetime) -> str:
    """到点提醒的稳定去重键:带上具体到期时刻,所以**改了时间就会重新催**。

    到点(`upcoming_due`)与补发(`late_due`)**共用这一个键**:同一件事先弹了
    "快到点了"就不该再弹一条"已过期",反过来也一样 —— 和课表那边 `_note_key`
    是同一套做法。键里带 id,所以两条内容不同、时间相同的提醒各催各的。
    """
    return f"{item.get('id', '')}|{when.isoformat(timespec='minutes')}"


def _due_note(item: dict, when: datetime, minutes: int, late: bool) -> dict:
    """一条到点/过期提醒的正文。只有 payload 里真有的字段会出现在句子里。"""
    hm = when.strftime("%H:%M")
    title = str(item.get("title") or "").strip()
    extra = f"({item['note']})" if item.get("note") else ""
    if late:
        text = (f"已过点:{title} 截止 {hm}"
                f"(过了约 {minutes} 分钟){extra}")
        ask = f"{title} 的截止时间 {hm} 已经过了约 {minutes} 分钟,现在补救还来得及吗?"
        head = "待办已过期"
    else:
        text = f"快到点了:{title} 截止 {hm}(还有约 {minutes} 分钟){extra}"
        ask = f"{title} 在 {hm} 截止,还有约 {minutes} 分钟,我现在该做什么?"
        head = "待办到点"
    return {"text": text, "ask": ask, "title": head,
            "id": item.get("id", ""), "name": title, "time": hm,
            "minutes_left": -minutes if late else minutes,
            "key": _due_key(item, when)}


def _pick_due(now: datetime, lead_minutes: int,
              late: bool) -> "tuple[dict, datetime, int] | None":
    """挑**一条**该此刻说话的待办:到点前 lead_minutes 内,或过期 late 窗口内。

    只给最近的一条,理由和课表那边一样:同一时段堆三四条会变成刷屏,而刷屏的
    下场是整条通知通道被用户关掉。
    """
    best = None
    for item in list_items():
        if item.get("done"):
            continue
        when = _due_at(item)
        if when is None:
            continue
        delta = round((when - now).total_seconds() / 60)
        if late:
            ok = 0 < -delta <= lead_minutes
        else:
            ok = 0 < delta <= lead_minutes
        if ok and (best is None or abs(delta) < abs(best[2])):
            best = (item, when, abs(delta))
    return best


def upcoming_due(now: "datetime | None" = None,
                 lead_minutes: int = DUE_LEAD_MINUTES) -> dict | None:
    """到点提醒:有没有哪件事在 `lead_minutes` 分钟内到期(形状与课前提醒一致)。

    诚实性:只认**日期和钟点都写清楚**的条目。只有日期的走 `due_soon` 按天催,
    这里绝不把"10-08"读成"10-08 的某个下午" —— 那正是用户最讨厌的假精确。
    """
    now = now or datetime.now()
    picked = _pick_due(now, lead_minutes, late=False)
    return _due_note(picked[0], picked[1], picked[2], late=False) if picked else None


def late_due(now: "datetime | None" = None,
             within_minutes: int = LATE_WINDOW_MINUTES) -> dict | None:
    """开机补发:刚过期还在窗口内的一件事(与到点提醒共用去重键)。"""
    now = now or datetime.now()
    picked = _pick_due(now, within_minutes, late=True)
    return _due_note(picked[0], picked[1], picked[2], late=True) if picked else None


# ------------------------------------------------------- 循环待办(v0.9.56 A3)
#
# 先例与口径都来自校历那套 repeat:"yearly"(v0.9.32):投影条目带 projected 与
# anchor,"我们推的日子"与"确认过的日子"在数据上就能分清。差别在权威来源:
# 校历的下一次是**替学校猜的**(学校今年还没通知,所以绝不参与倒计时推送),
# 循环待办的下一次是**用户亲口定的规则**("每周三交周记")—— 未来班次照常催
# 才是功能本身;老规矩在这里收窄成"**过去**的班次不补发"。

def _parse_iso(text) -> "date | None":
    try:
        return date.fromisoformat(str(text or "").strip()[:10])
    except ValueError:
        return None


def next_occurrence(item: dict, after: datetime) -> "datetime | None":
    """这条循环提醒在 `after` 之后的第一次出现(严格晚于 after);算不出回 None。

    从锚点(anchor,缺了退回当前 due)按周期**直接数格子**,不逐班 +1 天地链 ——
    学长三个星期没开机,得到的也是"下一个该出现的那次",不会补出三个幽灵班次。
    """
    days = repeat_days(item.get("repeat"))
    if days <= 0:
        return None
    anchor = _parse_iso(item.get("anchor") or item.get("due"))
    if anchor is None:
        return None
    clock = _due_at(item)
    base = datetime(anchor.year, anchor.month, anchor.day,
                    clock.hour if clock else 0, clock.minute if clock else 0)
    delta = (after - base).total_seconds()
    k = 0 if delta < 0 else int(delta // (days * 86400)) + 1
    return base + timedelta(days=days * k)


def _past_reschedule(item: dict, now: datetime) -> bool:
    """这条是不是"错过且补发窗口也关了"(该滚到下一次了)。

    带钟点的:过了 60 分钟补发窗口才滚 —— 窗口内 `late_due` 还可能补一条,
    先滚了就把补发吃掉;只有日期的:整天翻过去就算(按天催没有"几点"可言)。
    """
    due_dt = _due_at(item)
    if due_dt is not None:
        return due_dt < now - timedelta(minutes=LATE_WINDOW_MINUTES)
    d = _parse_iso(item.get("due"))
    return d is not None and d < now.date()


def _apply_next(item: dict, nxt: datetime) -> None:
    """把一条循环待办写到它的下一次(记 anchor、标 projected,不碰 repeat 规则)。"""
    item.setdefault("anchor", str(item.get("due") or ""))
    had_clock = _due_at(item) is not None
    item["due"] = nxt.date().isoformat()
    item["due_at"] = nxt.strftime("%Y-%m-%dT%H:%M") if had_clock else ""
    item["projected"] = True


def _advance_series(item: dict, now: datetime) -> "datetime | None":
    """循环待办「完成这一次」之后的下一次:严格晚于 max(现在, 当前截止)。

    提前完成(周三的周记周一就交了)也从**当前截止**往后数,不然会把还没
    发生的这一次跳过去;拖到过期才完成则从现在往后数,绝不存一个过去的日子。
    """
    if not repeat_days(item.get("repeat")):
        return None
    after = now
    due_dt = _due_at(item)
    if due_dt is not None:
        after = max(after, due_dt)
    else:
        d = _parse_iso(item.get("due"))
        if d is not None:
            after = max(after, datetime(d.year, d.month, d.day))
    return next_occurrence(item, after)


def roll_repeats(now: "datetime | None" = None) -> int:
    """把错过且补发窗口也关了的循环待办滚到下一次;返回滚动了几条。

    由 `proactive._tick` 每 5 分钟调一次(以及 `reminder` 工具的 list 动作,
    让不开看门狗的终端/网页用法也拿得到新账)。写盘只在真滚动过时发生。
    """
    now = now or datetime.now()
    items = load_items()
    rolled = 0
    for item in items:
        if item.get("done") or not repeat_days(item.get("repeat")):
            continue
        if not _past_reschedule(item, now):
            continue
        nxt = next_occurrence(item, now)
        if nxt is None or nxt <= now:
            continue
        _apply_next(item, nxt)
        rolled += 1
    if rolled:
        save_items(items)
    return rolled


def _fmt_item(item: dict) -> str:
    if item.get("days_left") is not None and item.get("due"):
        days = item["days_left"]
        when = ("已过截止 " + str(-days) + " 天") if days < 0 else (
            "就是今天" if days == 0 else f"还有 {days} 天")
    else:
        when = item.get("due") or item.get("due_raw") or "未定日期"
    clock = str(item.get("due_at") or "")[11:16]
    if clock:
        when += f" {clock}"
    note = f"({item['note']})" if item.get("note") else ""
    rep = str(item.get("repeat") or "")
    tag = ("(每周)" if rep == REPEAT_WEEKLY
           else f"(每 {rep.split(':', 1)[1]} 天)" if rep.startswith("every:") else "")
    return (f"[{item['id']}] {item['title']}{tag}(截止 {when}){note}"
            + ("⏰" if clock else ""))


def _repeat_note(result: dict) -> str:
    """add 成功后关于循环规则的那句回话:存成了什么、或为什么没存。"""
    rep = str(result.get("repeat") or "")
    if rep:
        anchor = result["item"].get("anchor") or result["item"].get("due")
        rule = "每周一次" if rep == REPEAT_WEEKLY \
            else f"每 {rep.split(':', 1)[1]} 天一次"
        return (f"循环:{rule},锚点 {anchor}。到点照常催,"
                "完成后这条会自动排到下一次;要停掉整个循环用 delete。")
    wanted = str(result.get("repeat_raw") or "")
    if not wanted:
        return ""
    return (f"(循环规则「{wanted}」没存上 —— 要么没认出来(支持「每周」「每N天」,"
            "「每月」不认),要么没有一个能确定的具体日期作锚点;这次先按一次性提醒记了,"
            "日期确认后可以删掉重加一次。)")


def reminder(ctx, action: str = "list", title: str = "", due: str = "",
             note: str = "", item_id: str = "", at: str = "",
             repeat: str = "") -> str:
    """reminder 工具处理器:待办/截止日期的增删查。"""
    action = (action or "list").strip().lower()
    if action == "add":
        result = add(title, due=due, note=note, at=at, repeat=repeat)
        if not result.get("ok"):
            return f"错误: {result.get('msg')}"
        item = result["item"]
        if item.get("due_at"):
            tail = (f"到点前 {DUE_LEAD_MINUTES} 分钟会主动催一次"
                    "(按你写的钟点,不会自己补一个时间)。")
        elif item.get("due"):
            tail = f"临期({DUE_SOON_DAYS} 天内)会按天催。"
        else:
            tail = ("(没有可解析的日期,只进清单不参与倒计时——"
                    "确认到具体日期后可以先删掉再重加)")
        tail = _repeat_note(result) + tail
        return f"已记下提醒 [{item['id']}]:{item['title']}" + (
            f"(截止 {item['due_at'].replace('T', ' ')})" if item.get("due_at")
            else (f"(截止 {item['due']})" if item.get("due") else "")) + f"。{tail}"
    if action == "list":
        # 开看门狗时 _tick 每 5 分钟会滚循环待办;这里再滚一遍,是不开看门狗的
        # 终端/网页用法也能拿到"下一次"的账。没有要滚的就一次盘也不写。
        roll_repeats()
        items = list_items()
        if not items:
            return "提醒清单是空的。用户提到「X 月 X 日要交/要做」时,用 action=add 记一条。"
        urgent = due_soon(items)
        lines = [f"共 {len(items)} 条未完成"
                 + (f",其中 {len(urgent)} 条临期/逾期" if urgent else "") + ":"]
        lines += ["- " + _fmt_item(i) for i in urgent]
        lines += ["- " + _fmt_item(i) for i in items if i not in urgent]
        return "\n".join(lines)
    if action == "finish":
        result = finish(item_id)
        if not result.get("ok"):
            return f"错误: {result.get('msg')}"
        if result.get("advanced"):
            item = result["item"]
            clock = str(item.get("due_at") or "")[11:16]
            return (f"已完成这一次:{item['title']}。"
                    f"下一次:{item.get('due') or '?'}"
                    + (f" {clock}" if clock else "")
                    + "(循环继续,要停掉整个循环用 delete。)")
        return f"已完成:{result['item']['title']}"
    if action == "delete":
        result = delete(item_id)
        return "已删除该提醒。" if result.get("ok") else f"错误: {result.get('msg')}"
    return ("错误: 未知 action。可用: add(记提醒,title 必填,due 给日期、"
            "at 给钟点如 17:00/下午5点,两者都确凿才会到点催;循环的事再给"
            "repeat=「每周」或「每N天」,必须配确凿日期作锚点)/list(看清单)/"
            "finish(标记完成;循环待办是完成这一次、自动排下一次)/delete(删除),"
            "finish/delete 要带 item_id。")
