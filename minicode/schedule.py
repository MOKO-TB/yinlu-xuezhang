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
PREV_FILE = "schedule.prev.json"   # save 覆盖前的旧版(undo 的唯一依据)
DEFAULT_WEEKS = "1-25"          # 没写周次默认全学期(可改文件),不默认"全周无课"

_WEEKDAY_MAP = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}
_RANGE_RE = re.compile(r"(\d+)\s*[-~－—]\s*(\d+)")
_SINGLE_RE = re.compile(r"(?<![\d-])(\d{1,2})(?![\d-])")


def state_path() -> Path:
    return app_path(SCHEDULE_FILE)


def prev_path() -> Path:
    return app_path(PREV_FILE)


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


def _snapshot_prev() -> bool:
    """把当前 schedule.json 原样备份成 schedule.prev.json(覆盖前的 undo 点)。

    只留一份:导入是"整表换新版",留历史栈没人会去翻,反而把个人数据
    多一份副本。备份失败**不拦保存**(同学要的是课表存进去,不是备份)。
    """
    try:
        raw = state_path().read_text(encoding="utf-8")
    except OSError:
        return False                       # 本来就没有课表:没有可备份的东西
    if not raw.strip():
        return False
    try:
        state_path().parent.mkdir(parents=True, exist_ok=True)
        prev_path().write_text(raw, encoding="utf-8")
    except OSError:
        return False
    return True


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
    old = load()                                   # 比对与备份都要在覆盖之前拿旧版
    diff = compare(ok_rows, term_start, data=old) if old else None
    has_prev = _snapshot_prev()
    try:
        state_path().parent.mkdir(parents=True, exist_ok=True)
        state_path().write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as exc:
        return False, f"错误: 课表写不进去({type(exc).__name__})"
    msg = f"课表已存(共 {len(ok_rows)} 条课,第 1 周周一 = {term.isoformat()})。"
    if diff:
        msg += f" 与上一版比对:{diff['msg']}\n{describe_changes(diff)}"
    if dropped:
        msg += f" {len(dropped)} 条没通过校验被跳过: {'; '.join(dropped[:5])}"
    msg += (" 之后问「今天/明天有什么课」不用再看图;课有变动重新 save 覆盖即可。"
            + ("旧版已备份,发现不对可用 schedule(action=undo) 换回来。" if has_prev else "")
            + " 现在把结果(尤其变动清单和被跳过的条目)向同学确认一遍。")
    return True, msg


def clear() -> str:
    if not load():
        return "没有已登记的课表,不用清。"
    has_prev = _snapshot_prev()
    try:
        state_path().unlink(missing_ok=True)
    except OSError as exc:
        return f"错误: 删除课表失败({type(exc).__name__})"
    return ("课表已清除。" + ("清掉的这一版还留着备份,后悔了用 schedule(action=undo) 换回来。"
                          if has_prev else ""))


def undo() -> tuple[bool, str]:
    """把课表换回上一版(schedule.json ↔ schedule.prev.json 对调)。

    为什么对调而不是删掉备份:同学点"回滚"之后很可能改主意"那还是用新的",
    对调让这一步同样可逆,而且任何一版都不会因为一次误点而消失。
    """
    try:
        raw_prev = prev_path().read_text(encoding="utf-8")
    except OSError:
        return False, ("没有可回滚的上一版课表(只有覆盖保存/清除课表时才会留下备份)。")
    try:
        cur_raw = state_path().read_text(encoding="utf-8")
    except OSError:
        cur_raw = ""
    restored = _valid(raw_prev)
    if restored is None:
        return False, "错误: 上一版备份读不懂(坏文件不回滚,当前课表保持不动)。"
    try:
        state_path().write_text(raw_prev, encoding="utf-8")
        if cur_raw:
            prev_path().write_text(cur_raw, encoding="utf-8")
        else:
            prev_path().unlink(missing_ok=True)     # 回滚到"本来没有课表"
    except OSError as exc:
        return False, f"错误: 回滚写不进去({type(exc).__name__})"
    return True, (f"课表已换回上一版(共 {len(restored)} 条课,"
                  f"第 1 周周一 = {_term_of(raw_prev)})。"
                  " 再点一次 undo 可换回刚才那一版。")


def _valid(raw: str) -> list | None:
    """校验一份课表 JSON;能用作课表则返回 classes,否则 None。"""
    try:
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None
    if (isinstance(data, dict) and isinstance(data.get("classes"), list)
            and isinstance(data.get("term_start"), str)):
        return data["classes"]
    return None


def _term_of(raw: str) -> str:
    try:
        return str(json.loads(raw).get("term_start"))
    except Exception:
        return "?"


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


# ---------------------------------------------------------------- 变动比对

COMPARE_FIELDS = ("day", "periods", "location", "weeks", "start_time")
_FIELD_LABEL = {"day": "星期", "periods": "节次", "location": "地点",
                "weeks": "周次", "start_time": "钟点"}


def _identity(row: dict) -> tuple:
    """一门课的身份键 = 课名 + 教师(空白去掉,大小写不管)。

    为什么用课名+教师而不是整行哈希:调课/换教室时同学认识的还是"那门课",
    比对要报的是"高等数学换到 B101 了",不是"删了一条又加了一条"。
    """
    squish = lambda v: re.sub(r"\s+", "", str(v or "")).casefold()
    return (squish(row.get("name")), squish(row.get("teacher")))


def _cmp_value(field: str, value):
    """字段归一成"可比"的形式。归一不了就退回原串(绝不因认不出而判成相同)。"""
    raw = str(value if value is not None else "").strip()
    if field == "day":
        d = _norm_day(raw)
        return d if d is not None else raw
    if field in ("periods", "weeks"):
        if not raw:
            return "" if field == "periods" else frozenset(parse_weeks(DEFAULT_WEEKS) or set())
        span = parse_weeks(raw)                       # "1-16" 与 "1-8,9-16" 等价
        return frozenset(span) if span else re.sub(r"\s+", "", raw)
    if field == "start_time":
        hm = _parse_hm(raw)
        return f"{hm[0]:02d}:{hm[1]:02d}" if hm else raw
    return re.sub(r"\s+", "", raw)


def _similarity(old: dict, new: dict) -> int:
    """两条同课条目的相似度 = 有几个字段一致(配对时挑最像的那条)。"""
    return sum(1 for f in COMPARE_FIELDS
               if _cmp_value(f, old.get(f)) == _cmp_value(f, new.get(f)))


def _pair_group(olds: list, news: list) -> tuple[list, list, list]:
    """同一门课(同身份键)的旧条目与新条目配对 → [(old, new), ...] + 剩下谁。

    先按"星期+节次"完全相同配对(那几乎肯定是同一条记录),剩下的贪心配对:
    越像的越先配 —— 这样"周三第 3-4 节换到第 5-6 节"报成 1 条变动,
    而不是"取消 1 条 + 新增 1 条"让同学以为课少了。
    """
    pairs: list[tuple] = []
    left_o, left_n = list(olds), list(news)
    for o in list(left_o):
        hit = next((n for n in left_n
                    if _cmp_value("day", o.get("day")) == _cmp_value("day", n.get("day"))
                    and _cmp_value("periods", o.get("periods"))
                    == _cmp_value("periods", n.get("periods"))), None)
        if hit is not None:
            pairs.append((o, hit))
            left_o.remove(o)
            left_n.remove(hit)
    while left_o and left_n:
        best = max(((o, n, _similarity(o, n)) for o in left_o for n in left_n),
                   key=lambda t: t[2])
        if best[2] == 0:
            break                       # 一点不像:宁可报成一取消一新增,不硬凑
        pairs.append((best[0], best[1]))
        left_o.remove(best[0])
        left_n.remove(best[1])
    return pairs, left_o, left_n


def compare(classes, term_start: str = "", data: dict | None = None) -> dict:
    """新课表 vs 本机已存课表 → 新增/取消/变动/不变(整表覆盖前先让同学看清差在哪)。

    `data` 传 None 时读本机文件;向导在第②步就调这个函数(那时还没落盘),
    所以比对**不写任何文件**。weeks/periods 按解析后的周次集合比,
    "1-16" 与 "1-8,9-16" 不算变动;认不出的写法退回原串比较。
    """
    if data is None:
        data = load()
    old_rows = [r for r in ((data or {}).get("classes") or []) if isinstance(r, dict)]
    new_rows = [r for r in (_normalize_class(x) for x in (classes or []))
                if r is not None]
    old_by: dict[tuple, list] = {}
    new_by: dict[tuple, list] = {}
    for r in old_rows:
        old_by.setdefault(_identity(r), []).append(r)
    for r in new_rows:
        new_by.setdefault(_identity(r), []).append(r)

    added: list[dict] = []
    removed: list[dict] = []
    changed: list[dict] = []
    unchanged = 0
    for key in set(old_by) | set(new_by):
        olds, news = old_by.get(key, []), new_by.get(key, [])
        if not olds:
            added.extend(news)
            continue
        if not news:
            removed.extend(olds)
            continue
        pairs, left_o, left_n = _pair_group(olds, news)
        removed.extend(left_o)
        added.extend(left_n)
        for o, n in pairs:
            fields = {f: {"old": o.get(f, ""), "new": n.get(f, "")}
                      for f in COMPARE_FIELDS
                      if _cmp_value(f, o.get(f)) != _cmp_value(f, n.get(f))}
            if fields:
                changed.append({"name": n.get("name", ""), "teacher": n.get("teacher", ""),
                                "fields": fields, "old": o, "new": n})
            else:
                unchanged += 1
    old_term = str((data or {}).get("term_start") or "").strip()
    new_term = str(term_start or "").strip()
    term_diff = {"old": old_term, "new": new_term,
                 "changed": bool(new_term and new_term != old_term)}

    counts = {"added": len(added), "removed": len(removed),
              "changed": len(changed), "unchanged": unchanged,
              "new_courses": len({k[0] for k in new_by}),
              "old_courses": len({k[0] for k in old_by})}
    out = {"ok": True, "has_existing": bool(data), "registered": bool(data),
           "added": added, "removed": removed, "changed": changed,
           "unchanged": unchanged, "counts": counts,
           "term_start": term_diff, "can_undo": prev_path().exists(),
           "msg": _diff_msg(counts, term_diff, bool(data))}
    return out


def _diff_msg(c: dict, term: dict, has_old: bool) -> str:
    """给模型和界面共用的一行人话。没有旧课表时不报"新增 N 条"(首次导入)。"""
    if not has_old:
        return (f"本机还没有课表,这是首次导入(共 {c['added'] + c['unchanged'] + c['changed']}"
                f" 条、约 {c['new_courses']} 门)。")
    bits = []
    if c["added"]:
        bits.append(f"新增 {c['added']} 条")
    if c["changed"]:
        bits.append(f"变动 {c['changed']} 条")
    if c["removed"]:
        bits.append(f"取消 {c['removed']} 条")
    if not bits:
        msg = f"与本机课表完全一致({c['unchanged']} 条逐字相同)"
    else:
        if c["unchanged"]:
            bits.append(f"不变 {c['unchanged']} 条")
        msg = ";".join(bits)
    if term["changed"]:
        msg += f";第 1 周周一 {term['old']} → {term['new']}"
    return msg + "。"


def _slot_label(row: dict) -> str:
    days = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "日"}
    d = days.get(row.get("day"), "?")
    bits = [f"周{d}", str(row.get("periods") or "?") + "节"]
    if row.get("start_time"):
        bits.append(str(row["start_time"]))
    if row.get("location"):
        bits.append(str(row["location"]))
    return " ".join(bits)


def describe_changes(res: dict, limit: int = 8) -> str:
    """compare() 结果 → 给模型看的明细行(每门课一行,超长截断)。

    为什么要明细:光有"变动 3 条"模型还是会向同学反问"哪三条",
    不如一次把旧→新摆出来,让它照着念。
    """
    lines: list[str] = []
    for ch in res.get("changed") or []:
        who = ch["name"] + (f"({ch['teacher']})" if ch.get("teacher") else "")
        fs = ";".join(f"{_FIELD_LABEL.get(k, k)} {v['old'] or '未填'}→{v['new'] or '未填'}"
                      for k, v in (ch.get("fields") or {}).items())
        lines.append(f"· 变动 {who}:{fs}")
    for r in res.get("added") or []:
        lines.append(f"· 新增 {r.get('name')}:{_slot_label(r)}"
                     + (f" {r['weeks']}周" if r.get("weeks") else ""))
    for r in res.get("removed") or []:
        lines.append(f"· 取消 {r.get('name')}:{_slot_label(r)}")
    if not lines:
        return "· 与本机课表逐条一致,没有变动。"
    head = "\n".join(lines[:limit])
    return head + (f"\n· …另有 {len(lines) - limit} 处没列出(向导里能看全)"
                   if len(lines) > limit else "")


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


def _week_overlay(days: list[date]) -> dict:
    """把待办 DDL 与校历事件按天摊到这一周(面板一格一格显示用)。

    两处刻意收窄:
    - 待办只认**写了具体日期**的件(`due` 是 ISO)。"下周要交"那种只有 `due_raw`
      文字的不参与 —— 摊到哪天都是猜,宁可不显示。
    - 校历跨多日的事件(考试周、假期、运动会)**只标首日**并带 `span` 区间说明,
      否则同一件事会在七天里重复七遍,把面板刷成一块公告栏。

    函数级导入 `reminders`/`calendar`:两者都不回头依赖本模块,但这样面板一次调用
    取齐三样,与 `briefing.py` 的写法一致。读失败当没有这两行,不影响课表本身。
    """
    from minicode import calendar, reminders
    first, last = min(days), max(days)
    due_map: dict[str, list] = {d.isoformat(): [] for d in days}
    event_map: dict[str, list] = {d.isoformat(): [] for d in days}
    overdue = 0
    try:
        for item in reminders.list_items():
            due = _parse_date(item.get("due"))
            if due is None:
                continue
            if due.isoformat() in due_map:
                due_map[due.isoformat()].append(
                    {"id": item.get("id"), "title": str(item.get("title") or "")[:60]})
            elif due < first:
                overdue += 1    # 逾期的不摊到某天头上装"那天的事",单独报个总数
    except Exception:
        pass
    try:
        events, _src = calendar.load_events()
        for e in events:
            start = _parse_date(e.get("start"))
            if start is None:
                continue
            end = _parse_date(e.get("end")) or start
            if end < first or start > last:
                continue
            anchor = max(start, first)
            if anchor.isoformat() not in event_map:
                continue
            event_map[anchor.isoformat()].append({
                "name": str(e.get("name") or "")[:40],
                "kind": str(e.get("kind") or ""),
                "span": (f"{start.isoformat()} ~ {end.isoformat()}"
                         if end > start else "")})
    except Exception:
        pass
    return {"due_map": due_map, "event_map": event_map, "overdue": overdue}


def view(today: date | None = None) -> dict:
    """课表面板的数据:今日/明日 + 本周网格 + 每天的空档与待办/校历叠加。

    没登记课表时只回 registered=False(面板据此显示引导,不显示空表)。
    """
    data = load()
    if not data:
        # 没课表也要报有没有备份:清错了的那一回,「换回上一版」是唯一的退路
        return {"registered": False, "can_undo": prev_path().exists()}
    today = today or date.today()
    from minicode import walk        # 延迟导入:walk 顶层依赖本模块,放在文件头会成环
    wk = week_of(today, data)
    times = load_period_times()
    walk_index = walk.campus_index()     # 一次刷新只读一遍地图索引
    monday = today - timedelta(days=today.weekday())
    span = [monday + timedelta(days=i) for i in range(7)]
    overlay = _week_overlay(span)

    def _pack(day: date) -> dict:
        iso = day.isoformat()
        slot = free_slots(day, data, times)
        risk = walk.day_risks(day, data, times, walk_index)
        return {"date": iso,
                "weekday": "一二三四五六日"[day.weekday()],
                "week": week_of(day, data),
                "classes": classes_on(day, data),
                # 空档:面板与 schedule(action=free) 同一份算法,不会出现两处说法不一致
                "gaps": slot["gaps"], "all_day_free": slot["all_day_free"],
                "in_term": slot["in_term"], "unknown": slot["unknown"],
                "class_count": slot["class_count"], "note": slot["note"],
                # 换楼/同节冲突同样只由后端算:面板自己比字符串就会跟回话口径漂开
                "walks": risk["items"], "clashes": risk["clashes"],
                "walk_skipped": risk["skipped"],
                "walk_index_ready": risk["index_ready"],
                "items": overlay["due_map"].get(iso, []),
                "events": overlay["event_map"].get(iso, [])}

    return {"registered": True,
            "term_start": data.get("term_start"),
            "updated_at": data.get("updated_at"),
            "can_undo": prev_path().exists(),
            "week": wk,
            # 面板顶部那句话的口径开关:填过作息表才允许说"几点到几点有空"
            "clock": bool(times.get("total")),
            # 地图索引缺了就没法判断换楼:顶层给一次,面板不必七天各提示一遍
            "walk_index_ready": bool((walk_index or {}).get("buildings")),
            "period_times": {"filled": bool(times.get("total")),
                             "total": times.get("total") or 0,
                             "source": times.get("source") or ""},
            "overdue": overlay["overdue"],
            "today": _pack(today),
            "tomorrow": _pack(today + timedelta(days=1)),
            "week_grid": {str(i + 1): _pack(day) for i, day in enumerate(span)}}


def _note_key(day: date, start_time: str, name: str) -> str:
    """课前提醒的稳定去重键。

    到点提醒(`upcoming_class`)与开机补发(`late_class_note`)**共用同一个键**:
    同一节课先弹了"快上课了"就不该再弹"错过的提醒",反过来也一样。
    """
    return f"{day.isoformat()}T{start_time}:{name}"


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
            "key": _note_key(now.date(), best["start_time"], best["name"])}



LATE_WINDOW_MINUTES = 60   # 开课后多久之内补提醒还算来得及,超过就不打扰


def late_class_note(now=None, within_minutes: int = LATE_WINDOW_MINUTES) -> dict | None:
    """开机补发:今天**已经开课、且开课不超过 within_minutes 分钟**的那节课。

    为什么需要:课前提醒的去重集以前只在内存里,一重启就空 —— 于是"8:50 才开机
    错过 8:40 那条"和"重启后同一节课又弹一次"会同时发生。

    两条诚实性:①只认课表里**确实写了 `start_time`** 的课;②课表里**没有结束钟点**,
    所以绝不宣称"这节课还在上",只说"已开课约 N 分钟"(这个我们说得准),而且
    **只给最近的一节** —— 一早上三节都没赶上,不会连着弹三条刷屏。
    """
    from datetime import datetime
    now = now or datetime.now()
    data = load()
    if not data:
        return None
    best = None
    for r in classes_on(now.date(), data):
        hm = _parse_hm(r.get("start_time"))
        if hm is None:
            continue
        start = now.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
        since = round((now - start).total_seconds() / 60)
        if 0 < since <= within_minutes and (best is None or since < best["_since"]):
            best = {**r, "_since": since}
    if best is None:
        return None
    loc = f" · 教室 {best['location']}" if best.get("location") else ""
    now_hm = now.strftime("%H:%M")
    text = (f"错过的提醒:{best['name']} {best['start_time']} 已开课"
            f"(现在 {now_hm},过了约 {best['_since']} 分钟){loc}")
    ask = (f"{best['name']} {best['start_time']} 已经开课了,现在 {now_hm},"
           "我现在过去还来得及吗?需要带什么?")
    return {"text": text, "ask": ask, "title": "错过的提醒",
            "name": best["name"], "time": best["start_time"],
            "location": best.get("location", ""),
            "minutes_since": best["_since"],
            "key": _note_key(now.date(), best["start_time"], best["name"])}


# ---------------------------------------------------------------- 作息表与空闲时段

# 为什么要同学自己填一次作息表:课表里每门课只有 `start_time`(还常常没有)和
# "第 1-2 节"这种节次,**没有下课时刻**。本模块头上那条硬规矩(课表没写钟点就
# 绝不编"8:00 上课")同样管着空档 —— 想说"下午两点到四点有空",必须有一份来源
# 清楚的作息表。没填过也照常能用,只是只能说"第 3-4 节没课":照样能约人,不直觉。
PERIOD_TIMES_FILE = "period-times.json"
MAX_PERIODS = 12          # 作息表最多录到第 12 节(晚课也够了),再多是录错了


def period_times_path() -> Path:
    return app_path(PERIOD_TIMES_FILE)


def _fmt_hm(hm: tuple[int, int]) -> str:
    return f"{hm[0]:02d}:{hm[1]:02d}"


def load_period_times() -> dict:
    """作息表 → {"periods": {节次: {"start","end"}}, "total": 连续到第几节, "source": 说明}。

    没填过 / 文件坏了 / 内容不像话都回**空表**而不是报错:空档功能本就该以节次
    口径工作,有作息表只是把说法升级成钟点。`total` 只取从第 1 节起的连续一段 ——
    中间断了(只填了 1-3 和 6-8)就不猜第 4-5 节的钟点,那两节宁可不说时间。
    """
    try:
        raw = json.loads(period_times_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"periods": {}, "total": 0, "source": ""}
    rows = raw.get("periods") if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        return {"periods": {}, "total": 0, "source": ""}
    out: dict[int, dict] = {}
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        try:
            n = int(str(row.get("period") or (i + 1)).strip())
        except ValueError:
            continue
        start, end = _parse_hm(row.get("start")), _parse_hm(row.get("end"))
        if not (1 <= n <= MAX_PERIODS and start and end and end > start):
            continue
        out[n] = {"start": _fmt_hm(start), "end": _fmt_hm(end)}
    total = 0
    while total + 1 in out:
        total += 1
    return {"periods": out, "total": total,
            "source": str(raw.get("source") or "") if isinstance(raw, dict) else ""}


def save_period_times(periods=None, source: str = "") -> tuple[bool, str]:
    """存作息表(课表面板那个表单走这里)。返回 (是否写入, 给人看的话)。

    刻意**不做**"按常见作息帮你生成":那等于替同学编下课时间。填了不对的随时
    再改就行 —— 文件是普通 JSON,`period-times.json` 也在打包清理清单里(本机作息)。
    传空列表 = 清掉作息表,空档退回节次口径。
    """
    rows: list[dict] = []
    for i, row in enumerate(periods or []):
        if not isinstance(row, dict):
            return False, f"错误: 第 {i + 1} 行不是节次对象。"
        try:
            n = int(str(row.get("period") or (i + 1)).strip())
        except ValueError:
            return False, f"错误: 第 {i + 1} 行的节次不是数字。"
        start, end = _parse_hm(row.get("start")), _parse_hm(row.get("end"))
        if not start or not end:
            return False, (f"错误: 第 {n} 节的起止时刻看不懂(要 08:00 这种"
                           "「时:分」写法)。没填进来,本机作息表保持原样。")
        if end <= start:
            return False, f"错误: 第 {n} 节下课时刻({row.get('end')})不晚于上课时刻。"
        rows.append({"period": n, "start": _fmt_hm(start), "end": _fmt_hm(end)})
    rows.sort(key=lambda r: r["period"])
    seen = {r["period"] for r in rows}
    if len(seen) != len(rows):
        return False, "错误: 有重复的节次,没填进来(同一节只该有一条起止)。"
    for n in range(1, (rows[-1]["period"] if rows else 0) + 1):
        if n not in seen:
            return False, (f"错误: 第 {n} 节空着,作息表得从第 1 节连续往后填"
                           "(中间断了就不算得准钟点空档)。")
    try:
        period_times_path().parent.mkdir(parents=True, exist_ok=True)
        period_times_path().write_text(json.dumps(
            {"updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
             "source": str(source or "本人按教务处作息表填写"),
             "periods": rows},
            ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as exc:
        return False, f"错误: 作息表写不进去({type(exc).__name__}),本机文件保持原样。"
    if not rows:
        return True, "作息表已清掉,空档回到按节次说。"
    return True, (f"作息表已存:第 1-{rows[-1]['period']} 节,"
                  f"{rows[0]['start']} 第一节上课、{rows[-1]['end']} 最后一节下课。"
                  "之后问空档会带钟点。")


def _parse_periods(spec) -> set[int] | None:
    """"1-2" / "3" / "5-6节" / "1,3,5" → 节次集合;认不出返回 None(调用方如实说算不准)。

    不照 `parse_weeks` 复用是因为口径不同:节次没有单双周,也不认"1-16"里那种
    超过一天的跨度 —— 越界(第 0 节、第 13 节)直接判成认不出,而不是夹到边界内,
    免得把录错的课表当成"第 1-12 节全是课"。
    """
    if isinstance(spec, (list, tuple)):
        parts = [str(x) for x in spec]
    else:
        text = str(spec or "").strip()
        if not text:
            return None
        parts = re.split(r"[,，、;；]", re.sub(r"[((（\[].*?[))）\]]", "", text))
    nums: set[int] = set()
    for part in parts:
        # 「3-4节」「第 3 节」都是课表里的原生写法:两头的花花肠子先剥掉
        part = part.strip().rstrip("节").lstrip("第").strip()
        if not part:
            continue
        m = _RANGE_RE.search(part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a > b:
                a, b = b, a
            if not (1 <= a and b <= MAX_PERIODS):
                return None
            nums.update(range(a, b + 1))
            continue
        m2 = re.fullmatch(r"(\d{1,2})", part)
        if not m2:
            return None
        n = int(m2.group(1))
        if not 1 <= n <= MAX_PERIODS:
            return None
        nums.add(n)
    return nums or None


def _row_periods(row: dict, times: dict) -> set[int] | None:
    """这门课占的是第几节。节次写歪了但填了作息表时,用 start_time 反查一节课。

    反查只认**时刻完全相等**(08:00 对 08:00):"差五分钟"就判成不匹配,因为那是
    两节不同的课,猜错会把整天的空档全算歪。
    """
    got = _parse_periods(row.get("periods"))
    if got:
        return got
    hm = _parse_hm(row.get("start_time"))
    if hm and times.get("total"):
        for n in range(1, times["total"] + 1):
            if times["periods"][n]["start"] == _fmt_hm(hm):
                return {n}
    return None


def _gap(from_n: int, to_n: int, times: dict) -> dict:
    """一段连续空档。填了作息表就带钟点,否则只有节次(`clock` 标着这条差异)。"""
    total = times.get("total") or 0
    label = f"第 {from_n} 节" if from_n == to_n else f"第 {from_n}-{to_n} 节"
    out = {"from": from_n, "to": to_n, "label": label,
           "periods": to_n - from_n + 1, "clock": False, "start": "", "end": ""}
    if total and to_n <= total:
        out.update({"clock": True,
                    "start": times["periods"][from_n]["start"],
                    "end": times["periods"][to_n]["end"]})
    return out


def free_slots(day: date, data: dict | None = None, times: dict | None = None) -> dict:
    """某天哪些时段没课。**面板与 schedule(action=free) 共用这一份算法**。

    三条不算错的口径(都是本轮定下来的,改动前先看这里):
    - 不在学期内(`week_of` 给 None,含开学前与假期)→ `in_term=False` 且不说空档。
      "没查到课"和"整天空着"是两件事,前者什么都推不出。
    - 有课但节次看不懂 → 整天空档**一条都不报**(`gaps=[]` + `unknown`),因为那门
      课正好可能落在你以为空着的那几节里。宁可回"算不准",不给一个好看的假答案。
    - 没填作息表:只在**已排课的首尾之间**找空档(全天共几节没人知道,报"最后一节
      之后还空着"是猜);填了才从第 1 节起到最后一节全展开,并给钟点。
    """
    data = data if data is not None else load()
    times = times if times is not None else load_period_times()
    wd = "一二三四五六日"[day.weekday()]
    out: dict = {"date": day.isoformat(), "weekday": wd,
                 "week": week_of(day, data), "registered": bool(data),
                 "in_term": False, "class_count": 0, "gaps": [],
                 "unknown": [], "all_day_free": False, "clock": bool(times.get("total")),
                 "note": ""}
    if not data:
        out["note"] = "还没有登记课表"
        return out
    wk = week_of(day, data)
    if wk is None:
        out["note"] = "那天不在本学期内(开学前或假期),不说空闲"
        return out
    out["in_term"] = True
    rows = classes_on(day, data)
    out["class_count"] = len(rows)
    if not rows:
        out["all_day_free"] = True
        if times.get("total"):
            gap = _gap(1, times["total"], times)
            out["gaps"] = [gap]
            out["note"] = f"那天没课({gap['start']}~{gap['end']} 都没课)"
        else:
            out["note"] = "那天没课(没填作息表,只能说没课、说不到几点)"
        return out
    occupied: set[int] = set()
    for row in rows:
        got = _row_periods(row, times)
        if not got:
            out["unknown"].append(str(row.get("name") or "某节课"))
            continue
        occupied |= got
    if out["unknown"]:
        out["note"] = (f"{len(out['unknown'])} 节课没写节次({ '、'.join(out['unknown'][:3]) })"
                       ",空档算不准 —— 补上节次或填作息表才准")
        return out
    lo = 1 if times.get("total") else min(occupied)
    hi = times["total"] if times.get("total") else max(occupied)
    gaps: list[dict] = []
    run: list[int] = []
    for n in range(lo, hi + 1):
        if n in occupied:
            if run:
                gaps.append(_gap(run[0], run[-1], times))
            run = []
        else:
            run.append(n)
    if run:
        gaps.append(_gap(run[0], run[-1], times))
    out["gaps"] = gaps
    if gaps:
        out["note"] = ""
    else:
        span = f"第 {lo} 节" if lo == hi else f"第 {lo}-{hi} 节"
        out["note"] = (f"那天 {len(rows)} 节课,{span} 排满了,没有空档"
                       if hi >= lo else "那天没有可用的节次区间")
    return out


def week_gaps(start: date | None = None, data: dict | None = None,
              times: dict | None = None) -> dict:
    """周一~周日每天的空档。`start` 给本周任意一天即可(内部换算到周一)。"""
    data = data if data is not None else load()
    times = times if times is not None else load_period_times()
    start = start or date.today()
    monday = start - timedelta(days=start.weekday())
    days = [free_slots(monday + timedelta(days=i), data, times) for i in range(7)]
    return {"term_start": (data or {}).get("term_start"),
            "week": week_of(monday, data) if data else None,
            "monday": monday.isoformat(),
            "clock": bool(times.get("total")),
            "days": days}


def format_week_gaps(res: dict, limit: int = 3) -> str:
    """给模型/回答看的周概览。每天最多列最长的 `limit` 段,长段优先 ——
    "空 1 节" 那种课间缝不算安排空间,列出来只会淹掉真有空的那天。"""
    if not res.get("week"):
        return ("课表:本周不在学期内(开学前/假期),没法说哪天有空 —— "
                "这不是\"整周空闲\",别说成有空。")
    lines = [f"本周(第 {res['week']} 周,周一 {res['monday']})空档"
             + ("(按填过的作息表给了钟点)" if res.get("clock")
                else "(没填作息表,只按节次说;要说\"几点到几点\"得先填一次作息表)")]
    for day in res["days"]:
        if not day.get("in_term"):
            lines.append(f"- 周{day['weekday']}: 不在学期内,不说空闲")
            continue
        if day.get("all_day_free"):
            bits = day.get("gaps") or []
            lines.append(f"- 周{day['weekday']} {day['date']}: 全天没课"
                         + (f"({bits[0]['start']}~{bits[0]['end']})" if bits else ""))
            continue
        if day.get("unknown"):
            lines.append(f"- 周{day['weekday']} {day['date']}: {day['note']}")
            continue
        gaps = sorted(day.get("gaps") or [], key=lambda g: -g["periods"])[:limit]
        if not gaps:
            lines.append(f"- 周{day['weekday']} {day['date']}: 没有空档"
                         f"({day['class_count']} 节课排满)")
            continue
        bits = "、".join(g["label"] + (f"({g['start']}~{g['end']})" if g["clock"] else "")
                        for g in gaps)
        lines.append(f"- 周{day['weekday']} {day['date']}: {bits}"
                     + (" 等" if len(day.get("gaps") or []) > limit else ""))
    return "\n".join(lines)


def format_free_slots(day_res: dict) -> str:
    """单天空档的一行式说法(action=free 传了具体日期时走这里)。"""
    head = f"{day_res['date']}(周{day_res['weekday']}" + (
        f" · 第 {day_res['week']} 周" if day_res.get("week") else "") + ")"
    if not day_res.get("registered"):
        return f"{head} 还没有登记课表,查不了空档 —— 先按 schedule-helper 存课表。"
    if not day_res.get("in_term"):
        return f"{head} 不在本学期内(开学前或假期),不报空闲。"
    if day_res.get("unknown"):
        return f"{head} {day_res['note']}。"
    if day_res.get("all_day_free"):
        bits = day_res.get("gaps") or []
        return f"{head} 全天没课" + (
            f"({bits[0]['start']}~{bits[0]['end']})" if bits else
            "(没填作息表,说不到几点到几点)") + "。"
    gaps = day_res.get("gaps") or []
    if not gaps:
        return f"{head} {day_res['note']}。"
    return (f"{head} 共 {day_res['class_count']} 节课,空档: " + "、".join(
        g["label"] + (f"({g['start']}~{g['end']})" if g["clock"] else "")
        for g in gaps) + ("" if day_res.get("clock") else " —— 只按节次,没编钟点"))


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
    lines.append("- save 会自动报出与上一版的差(新增/变动/取消),把这几条**点名**念给同学听;"
                 "只想先看差、先不落盘用 action=diff;存错了用 action=undo 换回上一版。")
    lines.append("- 本周空档(学长算好的,不是心算):\n"
                 + format_week_gaps(week_gaps(), limit=2))
    from minicode import walk        # 延迟导入:walk 顶层依赖本模块,放在文件头会成环
    walks = walk.upcoming_lines(days=2, data=data)
    if walks:
        lines.append("- 要换教学楼的课(只列节次上紧接着的;分钟数只有填过本机作息表才给):\n"
                     + "\n".join(walks))
        lines.append("- 被问「来不及吗/要不要早点走」只照上面那几行说事实(哪两栋、中间几分钟),"
                     "**绝不替他估「走一趟要几分钟」**——校园地图是手绘示意图、没有比例尺。"
                     "要说位置就用 map_highlight_building 把这两栋一起框出来,让他自己看。")
    lines.append("- 「我这周什么时候有空」「周三能约吗」直接照上面那几行回答;"
                 "上面标着没填作息表时只能说\"第几节到第几节空着\",**不要**报成几点到几点,"
                 "也别把\"不在学期内\"的那天说成空闲。同学想要钟点口径,让他把教务处作息表贴过来"
                 "(或去课表面板点「填作息表」),schedule(action=save_times) 存一次即可。")
    return "\n".join(lines) + "\n"


def schedule(ctx, action: str = "save", term_start: str = "",
             classes=None, when: str = "", periods=None, source: str = "") -> str:
    """schedule 工具处理器:save(存)/show(看某天)/free(看空档)/risk(看换楼的课)/
    diff(比对不写)/undo(回滚)/times(看作息表)/save_times(存作息表)/clear(清空)。"""
    action = (action or "save").strip().lower()
    if action == "save":
        ok, msg = save(term_start, classes or [])
        return msg if not ok else msg
    if action == "diff":
        data = load()
        if not data:
            return ("本机还没有课表,谈不上比对 —— 直接 action=save 存下来就是首次导入。")
        if not (classes or []):
            return "错误: diff 需要把要对比的 classes 一起传进来。"
        res = compare(classes, term_start, data=data)
        return ("比对结果(未写文件):" + res["msg"] + "\n" + describe_changes(res))
    if action == "undo":
        return undo()[1]
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
    if action == "free":
        spec = str(when or "").strip().lower()
        if spec in ("", "week", "本周"):
            return format_week_gaps(week_gaps())
        if spec in ("next", "next_week", "下周"):
            return format_week_gaps(week_gaps(date.today() + timedelta(days=7)))
        day = _parse_date(spec)
        if day is None:
            return ('错误: free 的 when 要具体日期(2026-09-23)、"week"(本周)或'
                    '"next"(下周)。')
        return format_free_slots(free_slots(day))
    if action == "risk":
        from minicode import walk        # 延迟导入:walk 顶层依赖本模块,放在文件头会成环
        spec = str(when or "").strip().lower()
        if spec in ("", "week", "本周"):
            return walk.format_week_risks(walk.week_risks())
        if spec in ("next", "next_week", "下周"):
            return walk.format_week_risks(walk.week_risks(date.today() + timedelta(days=7)))
        if spec in ("today", "今天"):
            day = date.today()
        elif spec in ("tomorrow", "明天"):
            day = date.today() + timedelta(days=1)
        else:
            day = _parse_date(spec)
            if day is None:
                return ('错误: risk 的 when 要具体日期(2026-09-23)、"今天"/"明天"、'
                        '"week"(本周)或 "next"(下周)。')
        return walk.format_risks(walk.day_risks(day))
    if action == "times":
        times = load_period_times()
        if not times.get("total"):
            return ("本机没有作息表:空档只能按节次说(\"第 3-4 节没课\"),说不到几点到几点。"
                    "同学想听钟点就把教务处作息表贴过来,用 action=save_times 存一次(一次性)。")
        bits = "、".join(f"第{n}节 {times['periods'][n]['start']}-{times['periods'][n]['end']}"
                         for n in range(1, times["total"] + 1))
        return (f"本机作息表(第 1-{times['total']} 节,{times['source'] or '本人填写'}):"
                f"{bits}")
    if action == "save_times":
        rows = [{"period": r.get("period") or (i + 1),
                 "start": r.get("start"), "end": r.get("end")}
                for i, r in enumerate(periods or []) if isinstance(r, dict)]
        if len(rows) != len(periods or []):
            return "错误: periods 每项要写成 {period?, start, end}(如 {start:\"08:00\", end:\"08:45\"})。"
        ok, msg = save_period_times(rows, source)
        return msg if ok else "存作息表没成功:" + msg
    return ("错误: 未知 action。可用: save(存课表)/show(看某天课)/free(查空档,"
            "when 传日期或 week)/risk(看要换教学楼的课,when 同上)/times(看作息表)/"
            "save_times(存作息表)/diff(比对不写)/undo(回滚)/clear(清空)。")
