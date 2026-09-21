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


def view(today: date | None = None) -> dict:
    """课表面板的数据:今日/明日 + 本周(周一~周日)网格。

    没登记课表时只回 registered=False(面板据此显示引导,不显示空表)。
    """
    data = load()
    if not data:
        # 没课表也要报有没有备份:清错了的那一回,「换回上一版」是唯一的退路
        return {"registered": False, "can_undo": prev_path().exists()}
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
            "can_undo": prev_path().exists(),
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
    lines.append("- save 会自动报出与上一版的差(新增/变动/取消),把这几条**点名**念给同学听;"
                 "只想先看差、先不落盘用 action=diff;存错了用 action=undo 换回上一版。")
    return "\n".join(lines) + "\n"


def schedule(ctx, action: str = "save", term_start: str = "",
             classes=None, when: str = "") -> str:
    """schedule 工具处理器:save(存)/show(看某天)/diff(比对不写)/undo(回滚)/clear(清空)。"""
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
    return "错误: 未知 action。可用: save(存课表)/show(看某天课)/clear(清空)。"
