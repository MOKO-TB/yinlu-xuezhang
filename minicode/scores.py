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


STATS_ROW_LIMIT = 80          # 面板最多摊给你看的行数(数字是全量算的,不受这个限制)


def stats(semester: str = "") -> dict:
    """「我的成绩」那一屏的数:门数/总学分/平均分/绩点,可按学期筛。

    为什么单独有这一条:界面要的是**四个数**,而 gpa_calc 给的是一篇给人读的说明。
    两者必须同源 —— 这里直接吃 `gpa._as_score/_gpa5/_gpa4` 那三个函数,面板与工具
    不许各算一套口径(算出两个绩点是比"没有绩点"更坏的事)。

    诚实边界:等级成绩按常见折算(优=95…),缺学分或非数字成绩的行**不参与加权**
    并如实报 `skipped`;绩点两式都只是国内常见算法,以学校教务处为准(那句在 `note`)。
    """
    from minicode import gpa
    data = load()
    rows = [r for r in ((data or {}).get("rows") or []) if isinstance(r, dict)]
    all_sem = sorted({str(r.get("semester") or "").strip()
                      for r in rows if str(r.get("semester") or "").strip()})
    want = str(semester or "").strip()
    scope = [r for r in rows if not want
             or str(r.get("semester") or "").strip() == want]
    scored: list[tuple[float, float | None]] = []
    skipped = 0
    for r in scope:
        score = gpa._as_score(str(r.get("score") or "").strip())
        credit = gpa._as_credit(r.get("credit"))
        if score is None or not (0 <= score <= 100):
            skipped += 1
            continue
        scored.append((score, credit))
    total_credit = sum(c for _s, c in scored if c)
    with_credit = [(s, c) for s, c in scored if c]
    acc = sum(s * c for s, c in with_credit)
    shown = [{"name": str(r.get("name") or "")[:60],
              "score": str(r.get("score") or "")[:20],
              "credit": str(r.get("credit") or "")[:10],
              "semester": str(r.get("semester") or "")[:30]} for r in scope[:STATS_ROW_LIMIT]]
    note = ("等级成绩按常见折算(优=95 / 良=85 / 中=75 / 及格=65);绩点两式都只是国内"
            "常见算法,评奖学金/保研请以学校教务处的算法为准。")
    if not rows:
        return {"ok": False, "empty": True, "entries": 0, "courses": 0,
                "credits": 0.0, "avg": None, "weighted": None,
                "gpa5": None, "gpa4": None, "semesters": [], "semester": want,
                "skipped": 0, "rows": [], "rows_truncated": False,
                "note": ("还没有成绩存档 —— 点「从教务导入成绩」,"
                         "登录由你本人在弹出的窗口里完成。")}
    return {
        "ok": True, "empty": False,
        "entries": len(scope),
        "courses": len({str(r.get("name") or "").strip() for r in scope}),
        "credits": round(total_credit, 2),
        "avg": (round(sum(s for s, _c in scored) / len(scored), 2) if scored else None),
        "weighted": (round(acc / total_credit, 2)
                     if with_credit and total_credit > 0 else None),
        "gpa5": (round(sum(gpa._gpa5(s) * c for s, c in with_credit) / total_credit, 2)
                 if with_credit and total_credit > 0 else None),
        "gpa4": (round(sum(gpa._gpa4(s) * c for s, c in with_credit) / total_credit, 2)
                 if with_credit and total_credit > 0 else None),
        "semesters": all_sem, "semester": want,
        "counted": len(scored), "skipped": skipped,
        "rows": shown, "rows_truncated": len(scope) > len(shown),
        "note": note,
        "source": str((data or {}).get("source") or ""),
        "updated_at": str((data or {}).get("updated_at") or ""),
    }



# ---------------------------------------------------------------- 成绩表解析

# 「成绩标识/成绩说明」这类列不是分数本身 —— 认错了会把一列文字当成绩存下去。
_SCORE_BLOCK = ("标识", "说明", "备注", "类型", "是否")
_HEADER_SCAN = 14          # 表头最多在前这么多行里找(正方那页顶上几行是学生信息+大标题)
_SEM_RE = re.compile(r"\d{4}\s*[-–~至]\s*\d{4}")


def _rank_name(text: str) -> int:
    t = str(text or "").strip()
    if t in ("课程名称", "课程名", "科目名称", "课程"):
        return 3
    if any(k in t for k in _COL_COURSE) or "科目" in t:
        return 2
    return 0


def _rank_score(text: str) -> int:
    t = str(text or "").strip()
    if not t or any(b in t for b in _SCORE_BLOCK):
        return 0
    if t in ("成绩", "总评", "总评成绩", "最终成绩"):
        return 3
    if any(k in t for k in _COL_SCORE):
        return 2
    return 0


def _rank_credit(text: str) -> int:
    t = str(text or "").strip()
    if t == "学分":
        return 3
    if any(k in t for k in _COL_CREDIT):
        return 2
    return 0


def _rank_sem(text: str) -> int:
    t = str(text or "").strip()
    if any(k in t for k in ("学期", "学年")):
        return 2
    return 0


def _cols_of(cells: list) -> dict:
    """一行表头 → {列: 列号}。同一类有多个候选时取**最具体**的那个,并列取最左的。

    为什么要打分:教务表的表头常是「课程编号 / 课程名称 / 课程性质」连着出现,
    「成绩 / 成绩标识 / 绩点」连着出现 —— 按"含关键字就认"会认到第一串里最不像的那列。
    """
    want = {"name": _rank_name, "score": _rank_score,
            "credit": _rank_credit, "semester": _rank_sem}
    col: dict[str, int] = {}
    for key, rank in want.items():
        best_idx, best_rank = None, 0
        for idx, text in enumerate(cells):
            r = rank(str(text or ""))
            if r > best_rank:
                best_idx, best_rank = idx, r
        if best_idx is not None:
            col[key] = best_idx
    return col


def _find_header(rows: list) -> "tuple[int, dict[str, int], int] | None":
    """在一行里认出成绩表的列位:「成绩」+「课程」两个字样都出现才认。

    先试单行表头,再试**相邻两行合并**(表头跨两行/单元格竖排的那种)——
    合并只在单行认不出时才做,认得出就不动它,免得给正常表多猜一层。
    回 `(表头行号, {列: 列号}, 表头占几行)`;认不出回 None —— 表头认列是硬门槛,
    防止把任意表格硬凑成成绩。学分/学期列**有就认、没有不拦**。
    """
    limit = min(len(rows), _HEADER_SCAN)
    plain: list[list[str]] = []
    for i in range(limit):
        row = rows[i]
        if not isinstance(row, list):
            plain.append([])
            continue
        cells = [str(c or "").strip() for c in row]
        plain.append(cells)
        col = _cols_of(cells)
        if "name" in col and "score" in col:
            return i, col, 1
    for i in range(limit - 1):
        a, b = plain[i], plain[i + 1]
        if not a or not b:
            continue
        merged = [(a[k] if k < len(a) else "") + (b[k] if k < len(b) else "")
                  for k in range(max(len(a), len(b)))]
        col = _cols_of(merged)
        if "name" in col and "score" in col:
            return i, col, 2
    return None


def _pick(cells: list, idx) -> str:
    """按列号取单元格;越界/没这一列都给空串(表头认了列,数据行不一定齐)。"""
    return cells[idx] if isinstance(idx, int) and 0 <= idx < len(cells) else ""


_HEADER_WORDS = ("成绩", "总评", "总评成绩", "最终成绩", "课程名称", "课程名", "科目名称")


def _looks_like_header(cells: list) -> bool:
    """数据区里又冒出一行表头(冻结表头/竖排表头的第二行)—— 不能当课程存下去。"""
    col = _cols_of(cells)
    if "name" in col and "score" in col:
        return True
    # 竖排表头下一半(「名称 / 总评」这种)单看既不像表头也不像数据,但它绝不是分数。
    return any(str(c or "").strip() in _HEADER_WORDS for c in cells)


def _rows_from(body: list, col: dict, semester_hint: str = "") -> list[dict]:
    got: list[dict] = []
    for row in body:
        if not isinstance(row, list):
            continue
        cells = [str(c or "").strip() for c in row]
        if _looks_like_header(cells):
            continue
        name, score = _pick(cells, col.get("name")), _pick(cells, col.get("score"))
        if (not name or not score or len(name) < 2
                or any(k in name for k in ("平均", "合计", "共 "))):
            continue
        got.append({"name": name[:60], "score": score[:20],
                    "credit": _pick(cells, col.get("credit"))[:10],
                    "semester": (_pick(cells, col.get("semester"))
                                 or semester_hint)[:30]})
    return got


def _header_echo(rows: list) -> str:
    """把一张认不出的表的第一行内容原样摊出来给人看 —— 下一轮就不用再猜长什么样。"""
    for row in rows or []:
        if not isinstance(row, list):
            continue
        cells = [str(c or "").strip() for c in row if str(c or "").strip()]
        if len(cells) >= 2:
            return " / ".join(cells[:9])[:90]
    return ""


def semester_hint_of(page) -> str:
    """页面筛学期的下拉框当前值(如「2024-2025-1」)—— 表里没有学期列时用它补。

    这不是猜:值就写在这张页的下拉框上,同学筛的是哪个学期,读到的就是哪个学期。
    认法只按形状(四个数字-四个数字),对不上就留空,不硬塞。
    """
    selects = (page or {}).get("selects") if isinstance(page, dict) else None
    for _k, v in (selects or {}).items():
        text = str(v or "").strip()
        if _SEM_RE.search(text):
            return text[:30]
    return ""


def parse_score_table(page, *, semester_hint: str = "") -> dict:
    """site_parse 那种整页 dump(或裸 rows)→ 成绩行。认不出就是认不出,绝不硬凑。

    只在**列名**上做文章:课程/成绩/学分/学期四列按表头字样对号;数据行里
    课程名与成绩至少要有一个,否则跳过(表尾的"平均分/共 N 条"这类行自己会掉)。
    两处宽容是给真机形状的:①表头跨两行;②表头与表体是**两张表**
    (冻结表头的网格就是这么拆开的)—— 这时候往后找一张自己没有表头、
    列数又对得上的表当表体,对不上就不认。
    """
    tables = []
    if isinstance(page, dict):
        tables = [t for t in (page.get("tables") or []) if isinstance(t, dict)]
        semester_hint = semester_hint or semester_hint_of(page)
    elif isinstance(page, list):
        tables = [{"rows": page}]
    best: dict | None = None
    seen: list[str] = []
    for ti, t in enumerate(tables):
        rows = t.get("rows") if isinstance(t.get("rows"), list) else []
        hit = _find_header(rows)
        if hit is None:
            echo = _header_echo(rows)
            if echo:
                seen.append(echo)
            continue
        hidx, col, span = hit
        got = _rows_from(rows[hidx + span:], col, semester_hint)
        if not got:
            need = max(list(col.values()) + [0]) + 1
            for t2 in tables[ti + 1: ti + 4]:
                rows2 = t2.get("rows") if isinstance(t2.get("rows"), list) else []
                if not rows2 or _find_header(rows2) is not None:
                    continue          # 自己有表头的表不该被当成别人的表体
                widest = max((len(r) for r in rows2 if isinstance(r, list)), default=0)
                if widest < need:
                    continue
                got = _rows_from(rows2, col, semester_hint)
                if got:
                    break
        if got and (best is None or len(got) > len(best["rows"])):
            best = {"ok": True, "rows": got}
    if best is None:
        tip = (" 学长在这一页看到的表头长这样:" + " ｜ ".join(seen[:4]) + "。") if seen else ""
        return {"ok": False, "rows": [], "seen": seen[:6],
                "msg": ("这一页没有认出成绩表(要有「课程/成绩」这样的表头)。"
                        "请确认已经打开成绩查询页、选好学期并点了「查询」。" + tip)}
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

def trend(ctx=None) -> str:
    """按学期算加权平均分,≥2 个学期时画成折线图(W3,v0.9.105)。

    折算与加权沿用 gpa 那一套(等级 95/85/75/65,缺学分/非数字的行跳过并
    如实报数);学期顺序按存档里的出现顺序 —— 教务导出本来就是时间序。
    画图走 render_chart 同一条路,零新依赖,SVG 落工作区 charts/。
    """
    from minicode import gpa
    data = load()
    if not data:
        return ("还没有成绩存档。要导入:点「📅 课表」面板的「从教务导入成绩」"
                "(登录由同学本人在弹出的浏览器里完成)。")
    groups: "dict[str, list]" = {}
    order: "list[str]" = []
    for r in (data.get("rows") or []):
        if not isinstance(r, dict):
            continue
        sem = str(r.get("semester") or "").strip() or "未标注学期"
        if sem not in groups:
            groups[sem] = []
            order.append(sem)
        groups[sem].append(r)
    points, skipped = [], 0
    for sem in order:
        total_credit, weighted = 0.0, 0.0
        for r in groups[sem]:
            score = gpa._as_score(str(r.get("score") or "").strip())
            try:
                credit = float(str(r.get("credit") or "").strip())
            except (TypeError, ValueError):
                credit = None
            if score is None or not credit or credit <= 0:
                skipped += 1
                continue
            total_credit += credit
            weighted += score * credit
        if total_credit > 0:
            points.append({"label": sem,
                           "value": round(weighted / total_credit, 2)})
    head = (f"按学期加权平均分(等级按 {gpa.GRADE_MAP} 折算,"
            f"以学校教务处绩点算法为准)"
            + (f";{skipped} 行缺学分或成绩无效已跳过" if skipped else "") + ":")
    lines = [f"- {p['label']}: {p['value']}" for p in points]
    if len(points) < 2:
        if not points:
            return head + "\n(没有可算的行:成绩与学分都要有效才参与加权。)"
        return (head + "\n" + "\n".join(lines)
                + "\n只有一个学期有有效成绩,画不出趋势 —— 导入更多学期后再来。")
    if ctx is None:
        return head + "\n" + "\n".join(lines)
    from minicode import charts
    out = charts.chart_file(ctx, "line", points,
                            title="成绩趋势(按学期加权平均分)",
                            source="scores.json · 以学校教务处绩点算法为准",
                            name="成绩趋势")
    return head + "\n" + "\n".join(lines) + "\n\n" + out


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
    if action == "trend":
        return trend(ctx)
    if action == "clear":
        return clear()
    return ("错误: 未知 action。可用: view(看存档)/gpa(按存档算绩点,"
            "两种常见算法 + 「以学校教务处为准」口径)/trend(按学期加权平均分,"
            "≥2 学期画折线图)/clear(清空存档)。"
            "导入成绩走「📅 课表」面板的「从教务导入成绩」三步向导。")
