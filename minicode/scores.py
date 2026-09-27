"""成绩单存档(scores.json)与解析:H(成绩导入)的数据层,绩点计算的真数据入口。

为什么要有它:gpa_calc 到现在都是"每次让同学手贴一遍成绩"的无源之水。教务系统
里明明躺着一份带学分的成绩单,导入一次就够 —— 之后「我绩点多少」「哪门课拖了
绩点」都从存档算,不再求人贴数据。

隐私口径与课表导入完全同一条(HANDOFF §6 #15):登录是人的事,学长只在内存里
拿页面文本,解析完就丢;scores.json 里只有成绩本身,不碰登录态。成绩比课表更私密
(它进 `.gitignore`、打包清理清单、迁移包清单三处),但**默认进迁移包** ——
换电脑时它是同学自己要带走的东西(与课表同一条)。

诚实边界:
- **表头认列,不按位置猜**:一张表要有「成绩/总评」和「课程/学分」这些表头字样
  才当成绩表处理;认不出列名就是认不出,绝不把第 3 列硬当成绩。
- **分数原样存**:85、89.5、「优」、「及格」都照字面落盘,折算是 `gpa_calc` 的事
  (它带自己的口径说明);我们不在存档这一层做任何折算。
- **缺学分的行照存**:绩点计算会跳过它并如实报数,存档不替同学删数据。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from minicode.paths import app_path

SCORES_FILE = "scores.json"
MAX_ROWS = 500
MODEL_TEXT_CHARS = 16000          # 兜底交给模型的页面文字上限(与课表导入同量级)

# 表头认列的关键字:一张表至少要同时有「成绩类」与「课程/学分类」字样才当成绩表。
_COL_COURSE = ("课程", "课名")
_COL_SCORE = ("成绩", "总评")
_COL_CREDIT = ("学分",)


def state_path() -> Path:
    return app_path(SCORES_FILE)


def load() -> dict | None:
    """读成绩存档;没有/坏了/形状不对回 None(调用方按"没导入过"说话)。"""
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
        return None
    rows = [r for r in data["rows"] if isinstance(r, dict)]
    if not rows:
        return None
    return {**data, "rows": rows}


def save(rows: list, source: str = "教务导入") -> tuple[bool, str]:
    """确认后才写;整表覆盖(覆盖前调用方必须已经给用户看过预览)。"""
    clean = _normalize_rows(rows)
    if not clean:
        return False, ("错误: 解析不出任何有效的成绩行(每行至少要有课程名和成绩)。"
                       "没有写文件 —— 本机原有存档保持不动。")
    if len(clean) > MAX_ROWS:
        return False, f"错误: 成绩行数 {len(clean)} 超过上限 {MAX_ROWS},先核对页面再导。"
    try:
        state_path().parent.mkdir(parents=True, exist_ok=True)
        state_path().write_text(json.dumps(
            {"rows": clean, "source": str(source or "")[:40],
             "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")},
            ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as exc:
        return False, f"错误: 写不进去({type(exc).__name__}) —— 本机原有存档保持不动。"
    return True, (f"已导入 {len(clean)} 条成绩到本机存档(scores.json,不上传)。"
                  "之后问绩点/加权平均都从这份存档算。")


def clear() -> str:
    try:
        state_path().unlink()
        return "已清空成绩存档。"
    except FileNotFoundError:
        return "本来就没有成绩存档。"
    except OSError as exc:
        return f"错误: 删不掉({type(exc).__name__})。"


def _normalize_rows(rows) -> list[dict]:
    out: list[dict] = []
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        score = str(raw.get("score") or "").strip()
        if not name or not score:
            continue
        out.append({"name": name[:60], "score": score[:20],
                    "credit": str(raw.get("credit") or "").strip()[:10],
                    "semester": str(raw.get("semester") or "").strip()[:30]})
    return out


def summarize(rows) -> dict:
    """确认页的一眼摘要:多少条、几个学期、哪些行缺学分(绩点会跳过它们)。"""
    items = list(rows or [])
    semesters = sorted({str(r.get("semester") or "").strip()
                        for r in items if str(r.get("semester") or "").strip()})
    no_credit = sorted({str(r.get("name") or "") for r in items
                        if not str(r.get("credit") or "").strip()})
    no_num = sorted({str(r.get("name") or "") for r in items
                     if not re.match(r"^\d+(\.\d+)?$", str(r.get("score") or "").strip())
                     and str(r.get("score") or "").strip() not in ("优", "良", "中",
                                                                   "及格", "合格")})
    return {"entries": len(items), "semesters": semesters,
            "no_credit": no_credit[:10], "odd_score": no_num[:10]}


def view() -> str:
    """给模型的存档清单(每行:课程 成绩 学分 学期),带绩点工具的指路。"""
    data = load()
    if not data:
        return ("还没有成绩存档。要导入:点「📅 课表」面板的「从教务导入成绩」"
                "(登录由同学本人在弹出的浏览器里完成);或让同学直接贴成绩文字,"
                "整理后用 gpa_calc 算。")
    rows = data["rows"]
    head = (f"成绩存档共 {len(rows)} 条(来源 {data.get('source') or '未知'},"
            f"更新于 {data.get('updated_at') or '?'}):")
    lines = [f"- {r['name']} 成绩 {r['score']}"
             + (f" × {r['credit']} 学分" if r.get("credit") else "(无学分)")
             + (f" · {r['semester']}" if r.get("semester") else "")
             for r in rows[:80]]
    tail = ("(绩点/加权平均直接调 gpa_calc 并把上面这些行整理成 courses 传入;"
            "算出来的结果要带「以学校教务处绩点算法为准」那句口径。"
            "存档里没有的成绩不要编。)")
    return "\n".join([head] + lines + [tail])


def courses_for_gpa(rows=None) -> list[dict]:
    """存档行 → gpa_calc 吃的 courses 形状(原样透传,折算归 gpa_calc 管)。"""
    data = load() if rows is None else {"rows": list(rows or [])}
    if not data:
        return []
    return [{"name": r.get("name"), "score": r.get("score"),
             "credit": r.get("credit"), "semester": r.get("semester")}
            for r in data["rows"] if isinstance(r, dict)]


# ---------------------------------------------------------------- 成绩表解析

def _find_header(rows: list) -> "tuple[int, dict[str, int]] | None":
    """在一行里认出成绩表的列位:「成绩/总评」+「课程」两个字样都出现才认。

    学分/学期列**有就认、没有不拦** —— 有的学校成绩页就是没有学分列;
    「成绩」这个表头字样本身就够特异,配上课程名不会误伤选课表/通知表。
    认不出回 None —— 表头认列是硬门槛,防止把任意表格硬凑成成绩。
    """
    for i, row in enumerate(rows[:8]):
        if not isinstance(row, list):
            continue
        cells = [str(c or "").strip() for c in row]
        col: dict[str, int] = {}
        for idx, text in enumerate(cells):
            if any(k in text for k in _COL_COURSE) and "name" not in col:
                col["name"] = idx
            if any(k in text for k in _COL_SCORE) and "score" not in col:
                col["score"] = idx
            if any(k in text for k in _COL_CREDIT) and "credit" not in col:
                col["credit"] = idx
            if ("学期" in text or "学年" in text) and "semester" not in col:
                col["semester"] = idx
        if "name" in col and "score" in col:
            return i, col
    return None


def _pick(cells: list, idx) -> str:
    """按列号取单元格;越界/没这一列都给空串(表头认了列,数据行不一定齐)。"""
    return cells[idx] if isinstance(idx, int) and 0 <= idx < len(cells) else ""


def parse_score_table(page) -> dict:
    """site_parse 那种整页 dump(或裸 rows)→ 成绩行。认不出就是认不出,绝不硬凑。

    只在**列名**上做文章:课程/成绩/学分/学期四列按表头字样对号;数据行里
    课程名与成绩至少要有一个,否则跳过(表尾的"平均分/共 N 条"这类行自己会掉)。
    """
    tables = []
    if isinstance(page, dict):
        tables = [t for t in (page.get("tables") or []) if isinstance(t, dict)]
    elif isinstance(page, list):
        tables = [{"rows": page}]
    best: dict | None = None
    for t in tables:
        rows = t.get("rows") if isinstance(t.get("rows"), list) else []
        hit = _find_header(rows)
        if hit is None:
            continue
        hidx, col = hit
        i_name, i_score = col.get("name"), col.get("score")
        i_credit, i_sem = col.get("credit"), col.get("semester")
        got: list[dict] = []
        for row in rows[hidx + 1:]:
            if not isinstance(row, list):
                continue
            cells = [str(c or "").strip() for c in row]
            name, score = _pick(cells, i_name), _pick(cells, i_score)
            if (not name or not score or len(name) < 2
                    or any(k in name for k in ("平均", "合计", "共 "))):
                continue
            got.append({"name": name[:60], "score": score[:20],
                        "credit": _pick(cells, i_credit)[:10],
                        "semester": _pick(cells, i_sem)[:30]})
        if got and (best is None or len(got) > len(best["rows"])):
            best = {"ok": True, "rows": got}
    if best is None:
        return {"ok": False, "rows": [],
                "msg": ("这一页没有认出成绩表(要有「课程/成绩/学分」这样的表头)。"
                        "请确认已经打开成绩查询页、选好学期并点了「查询」。")}
    return {"ok": True, "rows": best["rows"], "summary": summarize(best["rows"])}


# ---------------------------------------------------------------- 模型兜底

MODEL_SYSTEM_SCORES = (
    "你把教务系统页面上的成绩文字整理成 JSON 成绩列表。只输出 JSON,不要解释。"
    "每条:{\"name\":\"课程名\",\"score\":\"分数或等级(优/良/中/及格),照原文\","
    "\"credit\":\"学分\",\"semester\":\"学年学期,没有就空\"}。"
    "页面上没有的信息一律留空字符串,绝不编造;不及格、缺考也照原文收。")


def parse_scores_with_model(text: str, complete) -> dict:
    """确定性解析失败时的兜底:页面文字交给模型。`complete` 注入,同课表导入。"""
    body = (text or "")[:MODEL_TEXT_CHARS]
    if not body.strip():
        return {"ok": False, "msg": "页面没有可读文字", "rows": []}
    try:
        raw = complete(MODEL_SYSTEM_SCORES, body)
    except Exception as exc:
        return {"ok": False, "msg": f"模型解析失败:{type(exc).__name__}", "rows": []}
    m = re.search(r"\[.*\]", raw or "", re.S)
    if not m:
        return {"ok": False, "msg": "模型没给出成绩列表", "rows": []}
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"ok": False, "msg": "模型给的 JSON 读不懂", "rows": []}
    rows = _normalize_rows(data if isinstance(data, list) else [])
    return {"ok": bool(rows), "msg": "" if rows else "模型说没找到成绩",
            "rows": rows,
            "summary": summarize(rows) if rows else {},
            "notes": (["这份结果出自模型整理,请对着教务页核一遍"] if rows else [])}


# ---------------------------------------------------------------- 工具处理器

def scores_tool(ctx, action: str = "view") -> str:
    """scores 工具:看存档 / 从存档算绩点 / 清空。导入走面板三步向导,不归工具管。"""
    action = (action or "view").strip().lower()
    if action == "view":
        return view()
    if action == "gpa":
        from minicode import gpa
        courses = courses_for_gpa()
        if not courses:
            return "还没有成绩存档,先用「📅 课表」面板的「从教务导入成绩」导一次。"
        return gpa.gpa_calc(None, courses=courses)
    if action == "clear":
        return clear()
    return ("错误: 未知 action。可用: view(看存档)/gpa(按存档算绩点,"
            "两种常见算法 + 「以学校教务处为准」口径)/clear(清空存档)。"
            "导入成绩走「📅 课表」面板的「从教务导入成绩」三步向导。")
