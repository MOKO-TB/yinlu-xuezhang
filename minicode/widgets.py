"""交互式可视化的规格与校验(v0.9.185)。

这里画的是**原生组件**,不是模型写 HTML/JS 再塞 iframe:数据进,SVG/表格/
清单出。和 `panel.py` 分家很清楚 —— 那份是"模型写的一份网页,只能看不能动",
这一份是"数据画成的控件,能排序/筛选/勾选",但一切文字都走 textContent,
不执行任何代码,也不给外部网络。

三条边界:
- 规格在**进界面之前**收口:形状不对就退回给模型,不画半张坏图;
- 上限是硬拒绝,不是静默截断(截断会让用户以为看到的是全部);
- 数值必须是有限数,饼图必须是单系列非负值 —— 这两条画不出来时很容易
  画成一张"看起来正常"的假图。
"""
from __future__ import annotations

import math

MAX_TITLE = 80
MAX_NOTE = 200
MAX_LABELS = 16
MAX_SERIES = 4
MAX_ROWS = 200
MAX_COLS = 8
MAX_CELL = 120
MAX_ITEMS = 30
CHART_TYPES = ("bar", "line", "pie")


def _text(value, limit: int) -> str:
    s = " ".join(str(value or "").split())
    return s if len(s) <= limit else s[:limit] + "…"


def _num(value) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _cell(value):
    """表格单元格:数字原样留着(前端才能按数值排序),其余转成短文本。"""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return value
    if value is None:
        return ""
    return _text(value, MAX_CELL)


def normalize_chart(raw) -> tuple[dict | None, str]:
    if not isinstance(raw, dict):
        return None, "图表参数必须是对象"
    title = _text(raw.get("title"), MAX_TITLE)
    if not title:
        return None, "图表要有 title(一句话说清画的是什么)"
    chart_type = str(raw.get("chart_type") or "bar").strip().lower()
    if chart_type not in CHART_TYPES:
        return None, f"chart_type 只能是 {'/'.join(CHART_TYPES)}"
    labels_raw = raw.get("labels")
    if not isinstance(labels_raw, list) or len(labels_raw) < 2:
        return None, "labels 至少两项(只有一项不构成比较)"
    if len(labels_raw) > MAX_LABELS:
        return None, f"labels 最多 {MAX_LABELS} 项(超过就不是一眼能看完的图)"
    labels = [_text(v, 24) for v in labels_raw]
    if any(not v for v in labels):
        return None, "labels 里不能有空项"
    series_raw = raw.get("series")
    if not isinstance(series_raw, list) or not series_raw:
        return None, "series 至少要有一个系列"
    if len(series_raw) > MAX_SERIES:
        return None, f"series 最多 {MAX_SERIES} 个"
    if chart_type == "pie" and len(series_raw) != 1:
        return None, "饼图只能有一个系列"
    series: list[dict] = []
    names: set[str] = set()
    for i, item in enumerate(series_raw):
        if not isinstance(item, dict):
            return None, f"第 {i + 1} 个系列不是对象"
        name = _text(item.get("name") or f"系列{i + 1}", 24)
        if name in names:
            return None, f"系列名「{name}」重复了(图例分不出点哪个)"
        names.add(name)
        values_raw = item.get("values")
        if not isinstance(values_raw, list) or len(values_raw) != len(labels):
            return None, f"系列「{name}」的 values 数量要与 labels 一致"
        values = [_num(v) for v in values_raw]
        if any(v is None for v in values):
            return None, f"系列「{name}」里有不是有限数字的值"
        if chart_type == "pie" and any(v < 0 for v in values):
            return None, "饼图的数值不能是负数"
        series.append({"name": name, "values": values})
    return {"kind": "chart", "title": title, "chart_type": chart_type,
            "labels": labels, "series": series,
            "note": _text(raw.get("note"), MAX_NOTE)}, ""


def normalize_table(raw) -> tuple[dict | None, str]:
    if not isinstance(raw, dict):
        return None, "表格参数必须是对象"
    title = _text(raw.get("title"), MAX_TITLE)
    if not title:
        return None, "表格要有 title"
    columns_raw = raw.get("columns")
    if not isinstance(columns_raw, list) or not columns_raw:
        return None, "columns 不能为空"
    if len(columns_raw) > MAX_COLS:
        return None, f"columns 最多 {MAX_COLS} 列"
    columns = [_text(v, 20) for v in columns_raw]
    if any(not v for v in columns):
        return None, "columns 里不能有空列名"
    if len(set(columns)) != len(columns):
        return None, "列名不能重复(排序时点哪一列就分不清了)"
    rows_raw = raw.get("rows")
    if not isinstance(rows_raw, list) or not rows_raw:
        return None, "rows 不能为空"
    if len(rows_raw) > MAX_ROWS:
        return None, f"rows 最多 {MAX_ROWS} 行(再多请在工具外先汇总)"
    rows: list[list] = []
    for i, row in enumerate(rows_raw):
        if not isinstance(row, list) or len(row) != len(columns):
            return None, f"第 {i + 1} 行的列数与 columns 不一致"
        rows.append([_cell(v) for v in row])
    return {"kind": "table", "title": title, "columns": columns, "rows": rows,
            "note": _text(raw.get("note"), MAX_NOTE)}, ""


def normalize_checklist(raw) -> tuple[dict | None, str]:
    if not isinstance(raw, dict):
        return None, "清单参数必须是对象"
    title = _text(raw.get("title"), MAX_TITLE)
    if not title:
        return None, "清单要有 title"
    items_raw = raw.get("items")
    if not isinstance(items_raw, list) or not items_raw:
        return None, "items 不能为空"
    if len(items_raw) > MAX_ITEMS:
        return None, f"items 最多 {MAX_ITEMS} 项"
    items: list[dict] = []
    labels: set[str] = set()
    for i, item in enumerate(items_raw):
        if isinstance(item, str):
            item = {"label": item}
        if not isinstance(item, dict):
            return None, f"第 {i + 1} 项不是对象或字符串"
        label = _text(item.get("label"), 80)
        if not label:
            return None, f"第 {i + 1} 项没有 label"
        if label in labels:
            return None, f"清单项「{label}」重复了(勾选时分不出是哪个)"
        labels.add(label)
        items.append({"label": label,
                      "note": _text(item.get("note"), MAX_NOTE)})
    return {"kind": "checklist", "title": title, "items": items,
            "note": _text(raw.get("note"), MAX_NOTE)}, ""
