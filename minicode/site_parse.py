"""校园站结构化解析:哪个站 → 读哪一帧 → 这是哪一类表 → 按那一类的文法拆。

为什么要单独有这一层(v0.9.24):v0.9.23 的确定性解析是**对着一套 DOM 写死的**
—— 强智「学期理论课表」的 `#kbtable`,加上"课名 / 教师 / 周次(周)[01-02节] / 地点"
这一种单元格文法。学校换一张表(考试安排、空教室、考场座位、馆藏书目),那条路
就整个哑掉,只能退回"把整页文字交给模型"。本模块把四件事拆开:

    取材 JS   : 把页面上**所有**表格拍成二维文本(+ 下拉框当前值)
    版式识别  : 这张表到底是不是一张课表(有没有星期列、有没有节次/周次)
    站点适配器: 这一站的单元格怎么写(强智是第一个适配器)
    通用退路  : 认得出多少算多少,认不出的行**原样留在 raw 里**

诚实性约束与 schedule_import / schedule 同一条口径:认不出的东西进 `raw`/`unparsed`
给人看,绝不猜 —— 不把"看着像地点的一行"当成地点,也不因为表格读不通就
编一张课表出来。课表这种东西,错一格比少一格更坏。
"""
from __future__ import annotations

import json
import re

# ---------------------------------------------------------------- 取材(一段 JS 通吃)

# 为什么读 innerText 而不是解析 HTML:innerText 只含**可见**文本,教务页那些
# display:none 的另一半单元格(单双周备用)天然被排除掉了。
# 为什么把所有表都拍下来而不是只找 #kbtable:通用退路要能在"这学校不是强智"
# 时仍然拿到素材 —— 挑哪一张表是 Python 侧的事,JS 只负责搬。
TABLE_JS = """(() => {
  const clean = (s) => (s || '').replace(/\\u00a0/g, ' ');
  const tables = Array.from(document.querySelectorAll('table')).slice(0, 20)
    .map((t) => ({
      id: t.id || '',
      cls: (t.className || '').slice(0, 80),
      rows: Array.from(t.rows).map((tr) => Array.from(tr.cells).map((c) => clean(c.innerText))),
    })).filter((t) => t.rows.length);
  const selects = {};
  Array.from(document.querySelectorAll('select')).forEach((s) => {
    if (!s.name || selects[s.name] !== undefined) return;
    const o = s.options[s.selectedIndex];
    selects[s.name] = o ? clean(o.value || o.text) : '';
  });
  return JSON.stringify({url: location.href, title: document.title,
                         tables, selects});
})()"""

# 强智那张表的专用取材(v0.9.23 原样保留):它顺带把学期/周次下拉读成固定字段名,
# 老路径与既有测试都吃这个形状,留着比"统一到 TABLE_JS 再挑"更稳。
GRID_JS = """(() => {
  const t = document.querySelector('#kbtable') || document.querySelector('table');
  if (!t) return null;
  const rows = [...t.querySelectorAll('tr')].map(tr =>
    [...tr.children].map(c => (c.innerText || '').replace(/\\u00a0/g, ' ')));
  const pick = (n) => { const s = document.querySelector('select[name="' + n + '"]');
                        return s ? (s.options[s.selectedIndex] || {}).value || '' : ''; };
  return JSON.stringify({term: pick('xnxq01id'), week: pick('zc'),
                         title: document.title, url: location.href, rows});
})()"""

TEXT_JS = "(() => { const b = document.body; return b ? (b.innerText || '') : ''; })()"

# ---------------------------------------------------------------- 单元格文法

_DASH_RE = re.compile(r"^-{3,}$")
_WEEK_LINE_RE = re.compile(r"[（(]周[)）]")
_PERIOD_RE = re.compile(r"\[(\d{1,2}(?:\s*-\s*\d{1,2})+)\s*节\]")
_PERIOD_TEXT_RE = re.compile(r"第?\s*(\d{1,2}(?:\s*[-,、]\s*\d{1,2})+)\s*(?:[-~]?节)?")
_BELL_RE = re.compile(r"(\d{1,2})[:：](\d{2})\s*[-~－]\s*(\d{1,2})[:：](\d{2})")
_DAY_RE = re.compile(r"^\s*(星期|礼拜)?\s*([一二三四五六日天])\s*$")
_TIME_ONLY_RE = re.compile(r"^\s*(\d{1,2}[:：]\d{2})\s*$")
_DAY_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}
# 地点行只能"认特征",认不出就留给 raw —— 猜错教室比空着更坏
_PLACE_RE = re.compile(r"(楼|室|馆|中心|园区|校区|实验楼|教学楼|一教|二教|三教|栋|楼区)")


def _cell_entries(text: str) -> list[list[str]]:
    """一个单元格 → 若干课程条目(短横线串是多门课之间的分隔)。"""
    chunks: list[list[str]] = []
    cur: list[str] = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if _DASH_RE.match(line):
            if cur:
                chunks.append(cur)
                cur = []
            continue
        cur.append(line)
    if cur:
        chunks.append(cur)
    return chunks


def _norm_periods(raw: str) -> str:
    """'01-02' / '01-08' → '1-2' / '1-8';'01-02-03-04' → '1-4'。

    恰好两个数字时按**起-止区间**解释 —— 教务把跨 8 节的课写成 [01-08节],
    若按"列举了两个节次"处理会得到 "1,8",等于把一连 8 节课说成第 1、8 两节。
    三个以上数字才是逐个列举(不连续时退化成逗号表,宁缺不猜)。
    """
    nums = [int(n) for n in re.findall(r"\d{1,2}", raw)]
    if not nums:
        return ""
    if len(nums) == 2:
        a, b = sorted(nums)
        return str(a) if a == b else (f"{a},{b}" if b - a > 12 else f"{a}-{b}")
    ordered = sorted(set(nums))
    if ordered == list(range(ordered[0], ordered[-1] + 1)):
        return f"{ordered[0]}-{ordered[-1]}" if len(ordered) > 1 else str(ordered[0])
    return ",".join(str(n) for n in ordered)


def _norm_weeks(line: str) -> str:
    """'1-6(周)' / '4,6,8,10(周)' → 交给 schedule.parse_weeks 的原样周次串。"""
    core = _WEEK_LINE_RE.split(line)[0]
    return re.sub(r"\s+", "", core)


def _weekday_header(rows) -> tuple[dict, int]:
    """前几行里找"星期一~星期日"那一行 → {列号: 1-7}, 行号。找不到给空。"""
    for ri, row in enumerate(list(rows or [])[:3]):
        got = {}
        for ci, cell in enumerate(row):
            m = _DAY_RE.match(str(cell).strip())
            if m:
                got[ci] = _DAY_NUM[m.group(2)]
        if len(got) >= 5:
            return got, ri
    return {}, -1


def _bell_of(first_cell: str) -> str:
    """节次行里的第一个时间点(08:00-09:40 → 08:00);没有就空,绝不编。"""
    m = _BELL_RE.search(str(first_cell or ""))
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else ""


def _row_bells(rows, header_row: int) -> dict:
    """每个数据行的起始钟点(按行号存),给"格子里没写时间"的表补第一列的时间。"""
    out = {}
    for ri, row in enumerate(list(rows or [])[header_row + 1:], start=header_row + 1):
        cells = [str(c or "") for c in (row or [])]
        bell = _bell_of(cells[0] if cells else "")
        if bell:
            out[ri] = bell
    return out


def _dedupe(classes: list[dict]) -> list[dict]:
    """同一门"连上 8 节"的课会在每个大节行里重复出现:按内容去重,保留最早那条。

    先出现的行就是最早的大节,所以"保留第一条"= 保留真正的第一次上课钟点。
    """
    seen: set[tuple] = set()
    out: list[dict] = []
    for c in classes:
        key = (c["name"], c["day"], c["periods"], c["weeks"], c["location"], c["teacher"])
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


# ---------------------------------------------------------------- 适配器 1:强智

def _entry_qiangzhi(lines: list[str], day: int, bell: str) -> tuple[dict | None, str | None]:
    """强智一格里的课程条目 → (课表记录, 失败原因)。两者恰有一个非空。"""
    anchor = next((i for i, l in enumerate(lines) if _WEEK_LINE_RE.search(l)), None)
    if anchor is None:
        return None, f"没有「周次(周)」这一行:{' / '.join(lines)[:60]}"
    pm = _PERIOD_RE.search(lines[anchor])
    if not pm:
        return None, f"周次行里读不到节次:{lines[anchor][:60]}"
    name = " ".join(lines[:max(anchor - 1, 0)]).strip()
    teacher = lines[anchor - 1].strip() if anchor >= 2 else ""
    if not name:                        # 只有一行在周次之前:那行就是课名,教师未知
        name, teacher = " ".join(lines[:anchor]).strip(), ""
    location = ""
    for extra in lines[anchor + 1:]:
        if not _WEEK_LINE_RE.search(extra):
            location = extra
            break
    if not name:
        return None, "读不出课程名"
    return {"name": name[:60], "day": day, "periods": _norm_periods(pm.group(1)),
            "start_time": bell, "weeks": _norm_weeks(lines[anchor])[:80],
            "location": location[:60], "teacher": teacher[:40], "raw": []}, None


def parse_qiangzhi_grid(grid: dict) -> dict:
    """强智「学期理论课表」网格 → 课表记录(带 kind 标注)。

    返回 {"kind", "adapter", "classes", "term", "week", "notes", "unparsed", "ok", "msg"}。
    认不出表头(没有星期一~星期日那一行)时 ok=False 并说明为什么 ——
    这时候该换适配器或走模型兜底,而不是硬凑一份课表出来。
    """
    rows = (grid or {}).get("rows") or []
    day_of, header_row = _weekday_header(rows)
    if not day_of:
        return _no_kind(grid, "认不出课表的星期列(页面结构可能和强智不一样)")
    classes: list[dict] = []
    unparsed: list[str] = []
    for row in list(rows)[header_row + 1:]:
        cells = [str(c or "") for c in (row or [])]
        bell = _bell_of(cells[0] if cells else "")
        for ci, day in day_of.items():
            if ci >= len(cells):
                continue
            for entry in _cell_entries(cells[ci]):
                rec, why = _entry_qiangzhi(entry, day, bell)
                if rec is None:
                    if entry:
                        unparsed.append(why)
                    continue
                classes.append(rec)
    classes = _dedupe(classes)
    return _finish("timetable", "qiangzhi-xskb", classes, grid, unparsed,
                   week_note=(grid or {}).get("week"))


# ---------------------------------------------------------------- 适配器 2:通用表格

def _entry_generic(lines: list[str], day: int, bell: str) -> tuple[dict | None, str | None]:
    """不认学校的通用课表格:能对上特征的就填,对不上的**原样留在 raw**。

    与强智那套的区别只在"锚点":这里不强求「(周)」行,而是逐行贴标签 ——
      含 [1-2节] 或 "第1-2节" → 节次;含 1-16周/单周 → 周次;
      纯 HH:MM → 钟点;含 楼/室/馆… → 地点;教师行只在"课名之后、还缺教师"时收。
    贴不上标签的行一律进 raw 给人看,**不猜**是课名还是备注。
    """
    if not lines:
        return None, "空格子"
    periods = weeks = location = start_time = teacher = ""
    rest: list[str] = []
    for line in lines:
        if not periods:
            m = _PERIOD_RE.search(line) or (
                _PERIOD_TEXT_RE.search(line) if re.search(r"节", line) else None)
            if m:
                periods = _norm_periods(m.group(1))
                # 「第3-4节」这类行常顺手带周次:"第3-4节 1-16周"
                if not weeks:
                    wmline = _WEEK_LINE_RE.search(line) or re.search(r"\d+\s*-\s*\d+\s*周", line)
                    if wmline:
                        weeks = _norm_weeks(line[wmline.start():]) + (
                            "(单周)" if "单周" in line else "(双周)" if "双周" in line else "")
                        line = ""
                if not start_time:
                    tm = re.search(r"(\d{1,2}[:：]\d{2})", line)
                    if tm:
                        start_time = tm.group(1).replace("：", ":")
                continue
        if not weeks and (re.search(r"\d+\s*[-,、]\s*\d+", line)
                          and re.search(r"周", line)):
            weeks = _norm_weeks(re.sub(r"^.*?(?=\d)", "", line, count=1))[:80]
            if "单周" in line and "单" not in weeks:
                weeks += "(单周)"
            elif "双周" in line and "双" not in weeks:
                weeks += "(双周)"
            continue
        if not start_time and _TIME_ONLY_RE.match(line):
            start_time = _TIME_ONLY_RE.match(line).group(1).replace("：", ":")
            continue
        if not location and _PLACE_RE.search(line):
            location = line
            continue
        rest.append(line)
    if not rest:
        return None, f"认不出课程名:{' / '.join(lines)[:60]}"
    name = rest[0]
    if len(rest) > 1:
        cand = rest[1]
        # "教师行"的特征:短、不含数字、不含地点词 —— 认不准就当没读出来(留 raw)
        if (not teacher and len(cand) <= 12 and not re.search(r"\d", cand)
                and not _PLACE_RE.search(cand)):
            teacher = cand
            rest = rest[:1] + rest[2:]
        else:
            rest = rest[1:]
    else:
        rest = []
    raw = [x for x in rest if x]
    if not name:
        return None, "读不出课程名"
    return {"name": name[:60], "day": day, "periods": periods,
            "start_time": start_time or bell, "weeks": weeks, "location": location[:60],
            "teacher": teacher[:40], "raw": raw[:6]}, None


def parse_generic_grid(grid) -> dict:
    """任意一张"横星期、纵节次"的表格 → 课表记录(通用退路,认多少算多少)。

    `grid` 可以是 {"rows": [...]} 也可以是整张页面 dump(见 parse_page)。
    没有星期列 → kind=None,让调用方如实说"这不是课表"。
    """
    rows = _rows_of(grid)
    day_of, header_row = _weekday_header(rows)
    if not day_of:
        return _no_kind(grid if isinstance(grid, dict) else {},
                        "这张表里没有星期列(可能是考试安排/书目/通知,不是课表)")
    bells = _row_bells(rows, header_row)
    classes: list[dict] = []
    unparsed: list[str] = []
    for ri, row in enumerate(list(rows)[header_row + 1:], start=header_row + 1):
        cells = [str(c or "") for c in (row or [])]
        bell = bells.get(ri, "")
        for ci, day in day_of.items():
            if ci >= len(cells):
                continue
            for entry in _cell_entries(cells[ci]):
                rec, why = _entry_generic(entry, day, bell)
                if rec is None:
                    if entry and (entry[0] if entry else "").strip():
                        unparsed.append(why)
                    continue
                classes.append(rec)
    classes = _dedupe(classes)
    res = _finish("timetable", "generic-table", classes,
                  grid if isinstance(grid, dict) else {}, unparsed,
                  week_note=(grid or {}).get("week") if isinstance(grid, dict) else None)
    if res["ok"]:
        res["notes"].append("这张表不是按强智文法认出来的(通用退路):"
                            "带 raw 的条目请同学自己核一遍,学长没认出来的行都原样留着。")
    return res


# ---------------------------------------------------------------- 版式识别与派发

def _rows_of(page) -> list:
    """把各种取材结果归一成"二维文本网格"。

    吃三种形状:v0.9.23 的强智 dump({"term","week","rows"} 原样保留)、
    TABLE_JS 的整页 dump({"tables":[{id,cls,rows}]})、以及裸的 [[...],[...]]。
    """
    if isinstance(page, list):
        return page
    if not isinstance(page, dict):
        return []
    if page.get("rows"):
        return page["rows"]
    best = _pick_table(page.get("tables") or [])
    return (best or {}).get("rows") or []


def _pick_table(tables: list) -> dict:
    """挑最像课表的一张表:有星期列优先,其次行列最多的一张。"""
    scored = []
    for t in tables or []:
        rows = (t or {}).get("rows") or []
        day_of, _ = _weekday_header(rows)
        area = sum(len(r or []) for r in rows)
        scored.append((len(day_of), area, t))
    if not scored:
        return {}
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)   # 同分时稳定排序取最早一张
    top = scored[0]
    return top[2] if top[0] or top[1] else {}


def parse_page(page) -> dict:
    """整页 dump → 课表记录。先按强智文法试,不行再走通用退路。

    返回的 `kind` 是给调用方判"这是不是课表"用的:timetable / None。
    **认不出课表不等于读不到页面** —— 那种情况照样如实报 kind=None,
    让向导说"这不是课表,我不替你存",而不是硬塞一份课表出来。
    """
    rows = _rows_of(page)
    if not rows:
        return _no_kind(page if isinstance(page, dict) else {},
                        "页面上没有读到表格")
    grid = _as_qiangzhi_grid(page)
    res = parse_qiangzhi_grid(grid)
    if res["ok"]:
        return res
    gen = parse_generic_grid(page)
    if gen["ok"]:
        return gen
    return res if res["unparsed"] else gen        # 两条都空:把强智那条(带原因)报回去


def _as_qiangzhi_grid(page) -> dict:
    """把任意取材形状凑成强智 parse 吃的那个 dict(rows + 下拉框里的学期/周次)。"""
    if isinstance(page, dict) and page.get("rows") is not None:
        return page
    selects = (page or {}).get("selects") or {} if isinstance(page, dict) else {}
    return {"rows": _rows_of(page),
            "term": selects.get("xnxq01id") or selects.get("xn") or "",
            "week": selects.get("zc") or selects.get("week") or "",
            "url": (page or {}).get("url", "") if isinstance(page, dict) else "",
            "title": (page or {}).get("title", "") if isinstance(page, dict) else ""}


ROWS_CAP = 40          # 认不出是课表时,原样回给调用方的最多行数


def _rows_dump(rows) -> tuple[list, bool]:
    """把一张认不出的表拍成"二维文本"给人/模型看:一行都不丢,但也不归类别。

    为什么要带这个:本模块的目的不只是课表 —— 考试安排、空教室、馆藏书目这类
    校园表也要能被结构化读出来。认不出星期列**不等于读不到内容**,这时候把原表
    如实交回去,比返回一个空 classes 有用得多(也省得模型再去猜)。
    """
    rows = list(rows or [])
    out = []
    for r in rows[:ROWS_CAP]:
        cells = [re.sub(r"\s*\n\s*", " / ", str(c or "")).strip()[:60]
                 for c in (r or [])]
        if any(cells):
            out.append(cells)
    return out, len(rows) > ROWS_CAP


def _no_kind(page: dict, why: str) -> dict:
    rows, truncated = _rows_dump(_rows_of(page))
    return {"ok": False, "kind": None, "adapter": "", "msg": why,
            "classes": [], "rows": rows, "rows_truncated": truncated,
            "term": (page or {}).get("term", "") if isinstance(page, dict) else "",
            "week": (page or {}).get("week", "") if isinstance(page, dict) else "",
            "notes": [], "unparsed": [], "raw": []}


def _finish(kind: str, adapter: str, classes: list, page, unparsed: list,
            week_note: str = "") -> dict:
    notes: list[str] = []
    if week_note:
        notes.append(f"页面当前只筛了第 {week_note} 周 —— 导入前请把「周次」切回(全部)"
                     "再读一次,否则读到的不是整学期。")
    no_bell = sum(1 for c in classes if not c["start_time"])
    if no_bell:
        notes.append(f"{no_bell} 门课没读到上课钟点(格子里没有时间行),这些课只报第几节,不编时间。")
    raw = [{"name": c["name"], "day": c["day"], "lines": c["raw"]}
           for c in classes if c.get("raw")]
    classes = [{k: v for k, v in c.items() if k != "raw"} for c in classes]
    classes.sort(key=lambda c: (c["day"], c["periods"], c["name"]))
    rows_dump = ([], False) if classes else _rows_dump(_rows_of(page))
    return {"ok": bool(classes), "kind": kind if classes else None, "adapter": adapter,
            "msg": "" if classes else "课表格里没解析出任何一条课",
            "classes": classes, "rows": rows_dump[0], "rows_truncated": rows_dump[1],
            "term": (page or {}).get("term", "") if isinstance(page, dict) else "",
            "week": (page or {}).get("week", "") if isinstance(page, dict) else "",
            "notes": notes, "unparsed": sorted(set(unparsed))[:8], "raw": raw[:12]}


def load_dump(text):
    """JS 回来的字符串 → dict(坏 JSON/空串一律 None,不猜)。"""
    if isinstance(text, dict):
        return text
    if not text:
        return None
    try:
        data = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------- 给模型的只读工具

NO_PAGE = ("错误:当前没有可读的页面 —— 先 browser_open 打开那个网址,"
           "(教务这类要登录的页面请让用户自己在弹出的窗口里登录)。")
PAGE_MAX_CLASSES = 40


def page_parse(ctx, url_like: str = "", reason: str = "") -> str:
    """page_parse 工具:把**当前浏览器页面**里的表格结构化成 JSON 文字。

    为什么要有这个工具:之前"确定性解析"只有教务导入向导那一条路能用,
    模型自己读到别的校园表(考试安排、空教室、书目)只能靠眼睛看整页文字。
    现在它可以直接要一份结构化结果 —— 而且这条路**只读**,不写任何文件。
    认不出来的行照样原样列出来给它,让它照着问同学,而不是自己补。
    """
    from minicode import browser as brs
    try:
        hits = brs.read_frames(TABLE_JS, url_like=url_like or "")
    except brs.BrowserUnavailable as exc:
        return f"错误:{exc}"
    except Exception as exc:
        if "没有打开" in str(exc) or "no page" in str(exc).lower():
            return NO_PAGE
        return f"错误:读页面失败({type(exc).__name__}: {exc})"
    if not hits:
        return NO_PAGE
    for hit in hits:
        page = load_dump(hit.get("value"))
        if not page or not _rows_of(page):
            continue
        res = parse_page(page)
        return _render_tool_result(res, hit.get("url", ""))
    return ("错误:这一页(含所有内嵌帧)里没有读到任何表格。"
            "可以用 browser_snapshot 看元素,或 browser_eval 自己取文字。")


def _render_tool_result(res: dict, url: str) -> str:
    head = [f"页面: {url or '(未知)'}",
            f"版式: {res.get('kind') or '认不出是课表'}(适配器 {res.get('adapter') or '-'})"]
    tail = []
    if res.get("notes"):
        tail.append("提示: " + " ".join(res["notes"]))
    if res.get("unparsed"):
        tail.append("没认出来的单元格: " + " | ".join(res["unparsed"]))
    if res.get("raw"):
        tail.append("逐条未归类的行(挂在课程名下,务必人工核,别替它编): "
                    + json.dumps(res["raw"], ensure_ascii=False)[:1200])
    if res.get("kind") != "timetable":
        head.append("说明:这张表不是「横星期、纵节次」的课表 —— 没替你存。"
                    "下面按原样把表格行列给你,照着讲给同学;要哪一列请对着行问,别自己补。")
        rows = res.get("rows") or []
        body = json.dumps(rows, ensure_ascii=False, indent=1) if rows else "[]"
        if res.get("rows_truncated"):
            tail.append(f"(原表只列前 {ROWS_CAP} 行)")
        return "\n".join(head) + "\n" + body + ("\n" + "\n".join(tail) if tail else "")
    rows = (res.get("classes") or [])[:PAGE_MAX_CLASSES]
    body = json.dumps(rows, ensure_ascii=False, indent=1) if rows else "[]"
    more = ("" if len((res.get("classes") or [])) <= PAGE_MAX_CLASSES
            else f"\n(只列前 {PAGE_MAX_CLASSES} 条,共 {len(res['classes'])} 条)")
    return "\n".join(head) + "\n" + body + more + ("\n" + "\n".join(tail) if tail else "")
