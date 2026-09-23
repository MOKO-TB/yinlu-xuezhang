"""走堂冲突预警(v0.9.35):同一天两节课之间换了楼,把这件事按事实说出来。

三块本机数据一直是各干各的 —— 课表里有教室、地图索引里有建筑、本机作息表里有
钟点,谁也没碰过谁。本模块只做一件事:把它们对上,并且**只说对得上的那部分**。

口径(改之前先读这里,每一条都有对着它的测试):

1. **楼名只认 `campus.resolve` 唯一命中**的那一种。写成「A栋」(索引里学思苑A 和
   集贤苑A座都认)、写成「田径场」(登记为场地不是楼)、写成「线上」(索引里根本
   没有),一律进 `skipped` 并说清为什么判不了 —— 猜错一栋楼,提示就把人往反方向带。
2. **绝不报米数、绝不报"走路几分钟"**。`frontend/campus-index.json` 是手绘示意图,
   没有比例尺,像素距离换不成真实距离。能给的只有:换了哪两栋、中间隔几节、
   填过作息表时那两节课的钟点(数字出自同学自己抄进来的 `period-times.json`)。
3. **"赶不赶"不替用户下结论** —— 我们没有依据去定"低于几分钟算危险"这种数。
   输出只给两件事实:`gap_periods`(中间空几节,0 就是节次上紧接着)和
   `minutes`(填了作息表才有)。列表按分钟从小到大、再按节次排,判断留给人。
4. **没填作息表时,一个分钟数字都不会出现**,并且固定补一句提醒:节次上紧接着
   不等于来不及(第 4 节到第 5 节中间可能正好是午休)。这跟空闲时段看板
   「没填作息表绝不编钟点」是同一条规矩。
5. 相邻线索只用**双向都认**的那几对(索引实测有 4 对只有一边写了对方),
   单向的绝不当作"就在旁边"说。
6. 两节课占同一节(课表本身撞了)另列 `clashes` —— 那是排课问题不是走路问题,
   混进换楼里会把两件事都说糊。

纯计算:不落盘、不联网、不新增任何运行时文件(所以打包清理清单不涉及本模块)。
"""
from __future__ import annotations

from datetime import date, timedelta

from minicode import campus, schedule

MAX_ITEMS = 8                 # 一天最多列几处换楼;再多是噪声(面板与回话同一口径)
MAX_SKIPPED = 6               # 判不了的课最多点名几条,剩下的只报数量
WEEKDAY_CN = "一二三四五六日"

_NO_INDEX = "地图建筑索引不在,这次判断不了换楼"


# ---------------------------------------------------------------- 地点 → 楼

def _index() -> dict:
    """地图索引。文件缺失/坏了都当成"没有索引",绝不能拖垮课表面板。"""
    try:
        idx = campus.load_index()
    except Exception:
        return {}
    return idx if campus.buildings(idx) else {}


def campus_index() -> dict:
    """公开的取索引入口:课表面板一次刷新要把七天都算一遍,只读一次索引。"""
    return _index()


def place_of(location, index: dict | None = None) -> dict:
    """把课表里的地点文字解析成地图索引里的一栋楼;认不出就说明为什么认不出。

    `kind` 五种:`building`(唯一命中,能比) / `venue`(索引登记为场地、道路、水系)
    / `ambiguous`(俗称对应多栋) / `miss`(索引里没有) / `blank`(课表没写地点)。
    只有 `building` 参与换楼判定。
    """
    text = str(location or "").strip()
    if not text:
        return {"kind": "blank", "label": "", "why": "这门课没写地点"}
    index = _index() if index is None else index
    if not campus.buildings(index):
        return {"kind": "no_index", "label": text, "why": _NO_INDEX}
    try:
        got = campus.resolve(index, text)
    except Exception:
        return {"kind": "error", "label": text, "why": "地名解析没跑通,这次不判断"}
    status = got.get("status")
    if status == "hit":
        b = got.get("building") or {}
        return {"kind": "building", "label": str(b.get("name") or text),
                "no": int(b.get("no") or 0), "building": b, "why": ""}
    if status == "ambiguous":
        names = "、".join(str(c.get("name") or "") for c in (got.get("candidates") or [])[:4])
        return {"kind": "ambiguous", "label": text,
                "why": f"地点「{text}」在地图索引里对应多栋({names}),不替它认"}
    if status == "not_building":
        p = got.get("place") or {}
        kind = str(p.get("type") or "非建筑")
        name = str(p.get("name") or text)
        return {"kind": "venue", "label": name, "place": p, "why":
                f"「{name}」在地图索引里登记为{kind},不是楼,换不换楼比不了"}
    return {"kind": "miss", "label": text, "why": f"地图索引里没有「{text}」这处地点"}


def _name_keys(b: dict) -> set[str]:
    keys = set()
    for n in [str(b.get("name") or "")] + [str(x) for x in (b.get("aliases") or [])]:
        k = campus.normalize(n)
        if k:
            keys.add(k)
    return keys


def _mentions(a: dict, b: dict) -> bool:
    """a 的 nearby 里有没有点到 b(按索引自己的归一化规则比,不做模糊匹配)。"""
    keys = _name_keys(b)
    for n in (a.get("nearby") or []):
        if campus.normalize(str(n)) in keys:
            return True
    return False


def adjacency(a: dict, b: dict) -> str:
    """两栋楼的相邻关系:both(互为相邻) / one_way(只有一边写了) / none(没记录)。

    只有 `both` 允许被说成"挨着"。索引里 36 栋全都带 nearby,但实测有 4 对是单向的
    (学思苑G↔H、大礼堂↔S5致用楼、学术交流中心↔集贤苑D、游泳馆→田径场),
    还有 1 条指向索引外的名字(田径场)—— 拿单向记录当"就在旁边"是替用户下结论。
    """
    ab, ba = _mentions(a, b), _mentions(b, a)
    if ab and ba:
        return "both"
    return "one_way" if (ab or ba) else "none"


# ---------------------------------------------------------------- 节次与钟点

def _label(lo: int, hi: int) -> str:
    return f"第 {lo} 节" if lo == hi else f"第 {lo}-{hi} 节"


def _ranges(nums) -> list[tuple[int, int]]:
    got = sorted({int(n) for n in (nums or [])})
    out: list[tuple[int, int]] = []
    for n in got:
        if out and n == out[-1][1] + 1:
            out[-1] = (out[-1][0], n)
        else:
            out.append((n, n))
    return out


def _ranges_label(nums) -> str:
    return "、".join(_label(a, b) for a, b in _ranges(nums)) or "节次不明"


def _hm(text) -> int | None:
    """'10:10' → 610 分钟。认不出返回 None(不猜)。"""
    got = schedule._parse_hm(text)
    return None if got is None else got[0] * 60 + got[1]


def _clock_end(times: dict, n: int) -> int | None:
    row = (times.get("periods") or {}).get(n) or {}
    return _hm(row.get("end"))


def _clock_start(times: dict, n: int) -> int | None:
    row = (times.get("periods") or {}).get(n) or {}
    return _hm(row.get("start"))


# ---------------------------------------------------------------- 一天

def _entries(day: date, data: dict, times: dict, index: dict) -> list[dict]:
    """当天每节课:节次跨度 + 解析出的地点。判不了地点的当场登记进 skipped。"""
    rows = schedule.classes_on(day, data)
    out: list[dict] = []
    for row in rows:
        periods = schedule._row_periods(row, times)
        entry = {"name": str(row.get("name") or "某节课"),
                 "location": str(row.get("location") or "").strip(),
                 "periods": set(periods or ()), "row": row}
        if not periods:
            entry["bad"] = "没写节次(或节次写法看不懂)"
            entry["raw_periods"] = str(row.get("periods") or "")
        else:
            entry["lo"], entry["hi"] = min(periods), max(periods)
            entry["place"] = place_of(entry["location"], index)
        out.append(entry)
    return out


def _clashes(entries: list[dict]) -> list[dict]:
    """同一节上排了两门课 —— 课表本身撞了。与换楼分开列,各说各的话。"""
    out: list[dict] = []
    for i in range(len(entries)):
        for j in range(i + 1, len(entries)):
            a, b = entries[i], entries[j]
            if not a.get("periods") or not b.get("periods"):
                continue
            shared = a["periods"] & b["periods"]
            if not shared:
                continue
            out.append({"kind": "clash", "periods": sorted(shared),
                        "label": _ranges_label(shared),
                        "a": {"name": a["name"], "location": a["location"]},
                        "b": {"name": b["name"], "location": b["location"]}})
    return out


def _walks(entries: list[dict], times: dict) -> list[dict]:
    """时间上**紧挨着**的两节课之间要不要换楼(按节次排序后逐对比较)。

    必须先在全部有节次的课里取相邻两节,再判这两栋:中间夹着一节"判不出是哪栋楼"
    的课时,两头那两节根本不是"下课接着上课" —— 直接拿它们配对会把中间那节课
    算成空档,给出的分钟数就是假的。
    """
    seq = sorted((e for e in entries if e.get("periods")),
                 key=lambda e: (e["lo"], e["hi"], e["name"]))
    total = times.get("total") or 0
    out: list[dict] = []
    for prev, cur in zip(seq, seq[1:]):
        place, cur_place = prev.get("place") or {}, cur.get("place") or {}
        if place.get("kind") != "building" or cur_place.get("kind") != "building":
            continue                        # 有一边判不出楼 → 这一对比不了(原因在 skipped 里)
        if cur["lo"] <= prev["hi"]:
            continue                        # 同一节上撞了,那事归 clashes 说
        if place["no"] == cur_place["no"]:
            continue                        # 同一栋楼换教室,不算走堂
        gap = cur["lo"] - prev["hi"] - 1
        minutes = None
        if total and prev["hi"] <= total and cur["lo"] <= total:
            end = _clock_end(times, prev["hi"])
            start = _clock_start(times, cur["lo"])
            if end is not None and start is not None and start >= end:
                minutes = start - end
        out.append({
            "kind": "walk",
            "gap_periods": gap, "no_gap": gap == 0, "minutes": minutes,
            "clock": bool(minutes is not None),
            "from_period": prev["hi"], "to_period": cur["lo"],
            "prev": {"name": prev["name"], "location": prev["location"],
                     "building": place["label"], "span": _label(prev["lo"], prev["hi"]),
                     "no": place["no"]},
            "curr": {"name": cur["name"], "location": cur["location"],
                     "building": cur_place["label"], "span": _label(cur["lo"], cur["hi"]),
                     "no": cur_place["no"]},
            "adjacency": adjacency(place["building"], cur_place["building"]),
        })
    out.sort(key=lambda it: (not it["no_gap"],
                             it["minutes"] if it["minutes"] is not None else 10_000,
                             it["from_period"]))
    return out


def _skipped(entries: list[dict]) -> list[dict]:
    out: list[dict] = []
    for e in entries:
        if e.get("bad"):
            out.append({"name": e["name"], "location": e["location"],
                        "why": e["bad"]})
            continue
        place = e.get("place") or {}
        if place.get("kind") != "building":
            out.append({"name": e["name"], "location": e["location"],
                        "why": place.get("why") or "地点解析不出建筑"})
    return out


def day_risks(day: date | None = None, data: dict | None = None,
              times: dict | None = None, index: dict | None = None) -> dict:
    """某天有几处要换楼、分别在什么位置。返回结构与 schedule.free_slots 同构。

    三条不算错的口径(与空闲时段看板同源):
    - 没登记课表 → 什么都不算,只说"还没有课表";
    - 那天不在学期内 → `in_term=False`,不列条目(节次都对不上,谈不上换楼);
    - 地点判不出楼的课 → 进 `skipped` 如实说明,不参与判定,也不假装没这门课。
    """
    day = day or date.today()
    data = schedule.load() if data is None else data
    times = schedule.load_period_times() if times is None else times
    index = _index() if index is None else index
    wd = WEEKDAY_CN[day.weekday()]
    out: dict = {"date": day.isoformat(), "weekday": wd,
                 "week": schedule.week_of(day, data), "registered": bool(data),
                 "in_term": False, "clock": bool(times.get("total")),
                 "class_count": 0, "items": [], "clashes": [], "skipped": [],
                 "skipped_more": 0, "index_ready": bool(campus.buildings(index)),
                 "note": ""}
    if not data:
        out["note"] = "还没有登记课表"
        return out
    wk = schedule.week_of(day, data)
    if wk is None:
        out["note"] = "那天不在本学期内(开学前或假期),不判断换楼"
        return out
    out["in_term"] = True
    entries = _entries(day, data, times, index)
    out["class_count"] = len(entries)
    out["items"] = _walks(entries, times)
    out["clashes"] = _clashes(entries)
    skipped = _skipped(entries)
    out["skipped"] = skipped[:MAX_SKIPPED]
    out["skipped_more"] = max(0, len(skipped) - MAX_SKIPPED)
    listed = out["items"][MAX_ITEMS:]
    out["items"] = out["items"][:MAX_ITEMS]
    out["items_more"] = len(listed)
    if not out["items"]:
        out["note"] = ("那天没有需要先换楼的连堂课" if not skipped else
                       "能判断的课里没有换楼" + (f";另有 {len(skipped)} 门判不了" if skipped else ""))
    return out


def worst_item(day_res: dict) -> dict | None:
    """某天最紧的一处(items 已按'最该先看'排好序,所以取第一条)。"""
    items = day_res.get("items") or []
    return items[0] if items else None


# ---------------------------------------------------------------- 说法

def item_text(it: dict) -> str:
    prev, cur = it["prev"], it["curr"]
    text = (f"{prev['span']}「{prev['name']}」在{prev['building']} → "
            f"{cur['span']}「{cur['name']}」在{cur['building']}")
    tail: list[str] = []
    if it["clock"]:
        tail.append(f"两节之间 {it['minutes']} 分钟")
    if it["gap_periods"]:
        tail.append(f"中间空 {it['gap_periods']} 节")
    if it["adjacency"] == "both":
        tail.append("地图索引里这两栋互为相邻")
    return text + (f"({'、'.join(tail)})" if tail else "")


def format_risks(res: dict, limit: int = MAX_ITEMS) -> str:
    """给模型/回答看的当天说法。"""
    head = f"{res['date']}(周{res['weekday']}" + (
        f" · 第 {res['week']} 周" if res.get("week") else "") + ")"
    if not res.get("registered"):
        return f"{head} 还没有登记课表,判断不了换楼 —— 先按 schedule-helper 存课表。"
    if not res.get("in_term"):
        return f"{head} 不在本学期内(开学前或假期),不判断换楼。"
    if not res.get("index_ready"):
        return f"{head} {_NO_INDEX}(地图面板能用吗?不行的话把 frontend/campus-index.json 装回去)。"
    lines: list[str] = []
    items = res.get("items") or []
    if not items:
        lines.append(f"{head} {res.get('note') or '没有要换楼的连堂课'}。")
    else:
        lines.append(f"{head} 有 {len(items)} 处要换楼"
                     + (f"(按最该先看的排前面,另有 {res['items_more']} 处没列出)"
                        if res.get("items_more") else ":"))
        for it in items[:limit]:
            lines.append("- " + item_text(it))
        if not res.get("clock"):
            lines.append("(没填作息表,只能说节次、说不到几点 —— 而且**节次上紧接着不等于来不及**,"
                         "第 4 节到第 5 节中间可能正好是午休。填一次作息表就能给出分钟数。)")
    clashes = res.get("clashes") or []
    if clashes:
        lines.append(f"另有 {len(clashes)} 处**同一节排了两门课**(那是课表本身撞了,不是走路问题):")
        for c in clashes[:3]:
            lines.append(f"- {c['label']}:「{c['a']['name']}」与「{c['b']['name']}」")
    skipped = res.get("skipped") or []
    if skipped:
        lines.append("这几门没参与判断:")
        for s in skipped[:MAX_SKIPPED]:
            lines.append(f"- {s['name']}({s['location'] or '没写地点'}):{s['why']}")
        if res.get("skipped_more"):
            lines.append(f"- …另有 {res['skipped_more']} 门同理没列出")
    return "\n".join(lines)


def week_risks(start: date | None = None, data: dict | None = None,
               times: dict | None = None, index: dict | None = None) -> dict:
    """周一~周日每天的换楼。面板与工具共用这一份,免得两处说法不一致。"""
    data = schedule.load() if data is None else data
    times = schedule.load_period_times() if times is None else times
    index = _index() if index is None else index
    start = start or date.today()
    monday = start - timedelta(days=start.weekday())
    days = [day_risks(monday + timedelta(days=i), data, times, index) for i in range(7)]
    return {"term_start": (data or {}).get("term_start"),
            "week": schedule.week_of(monday, data) if data else None,
            "monday": monday.isoformat(),
            "clock": bool(times.get("total")),
            "index_ready": bool(campus.buildings(index)),
            "days": days}


def format_week_risks(res: dict) -> str:
    """周概览:每天只说最紧的一处(每天列五条就成了噪声)。"""
    if not res.get("week"):
        return "课表:本周不在学期内(开学前/假期),谈不上换楼。"
    if not res.get("index_ready"):
        return f"地图建筑索引不在,{_NO_INDEX}。"
    lines = [f"本周(第 {res['week']} 周,周一 {res['monday']})换楼提示"
             + ("(填过作息表,给了分钟数)" if res.get("clock")
                else "(没填作息表,只按节次说)")]
    quiet = 0
    for day in res["days"]:
        it = worst_item(day)
        if it is None:
            quiet += 1
            continue
        more = len(day.get("items") or []) - 1
        lines.append(f"- 周{day['weekday']} {day['date']}:" + item_text(it)
                     + (f";这天还有 {more} 处换楼" if more > 0 else ""))
    if quiet:
        lines.append(f"(其余 {quiet} 天没有要换楼的课)")
    return "\n".join(lines)


def upcoming_lines(days: int = 2, data: dict | None = None,
                   times: dict | None = None, index: dict | None = None,
                   today: date | None = None) -> list[str]:
    """今天起 N 天里"节次上紧接着就要换楼"的条目,给每轮提示词注入用。

    只挑 `no_gap` 的那些 —— 中间还空着一节的话,时间是自己的人家的事,注入进去
    只会变成噪声。没有任何一条时返回空列表(调用方据此**一个字节都不注入**)。
    """
    data = schedule.load() if data is None else data
    if not data:
        return []
    times = schedule.load_period_times() if times is None else times
    index = _index() if index is None else index
    today = today or date.today()
    out: list[str] = []
    for offset in range(max(1, min(days, 7))):
        res = day_risks(today + timedelta(days=offset), data, times, index)
        for it in res.get("items") or []:
            if not it["no_gap"]:
                continue
            head = "今天" if offset == 0 else ("明天" if offset == 1 else f"周{res['weekday']}")
            out.append(f"- {head} {it['prev']['span']}→{it['curr']['span']}:"
                       f"{it['prev']['building']} → {it['curr']['building']}"
                       + (f"(中间 {it['minutes']} 分钟)" if it["clock"] else "(只按节次,没编分钟)"))
    return out
