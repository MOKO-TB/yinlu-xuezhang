"""交互式可视化三件套:图表 / 表格 / 清单(v0.9.185)。

这三只不是"生成网页":它们把**结构化数据**推给界面,由界面画成原生控件。
好处是模型写不出可执行代码、回放不依赖一份 HTML、每张卡都能按文本渲染;
代价是形状固定(柱/折线/饼、表、清单),所以规格在 `widgets.py` 里先收口。

注册表里这些工具的**先后顺序**由 `registry.build_default_registry()` 决定,
并由 `tests/test_registry_split.py` 逐字钉住 —— 别在这里自己排。
"""
from __future__ import annotations

import uuid

from minicode import widgets
from minicode.tools.base import Tool


def _push(ctx, spec: dict) -> tuple[str, str]:
    """把规格推给界面;没有图形通道时**如实说没展示**,不假装画过了。"""
    ui = getattr(ctx, "ui", None)
    push = getattr(ui, "push", None)
    if not callable(push):
        return "", "这个界面没有图形通道"
    widget_id = "w" + uuid.uuid4().hex[:10]
    try:
        push("ui_widget", widget_id=widget_id, widget=spec)
    except Exception as exc:
        return "", f"图形通道推不出去({type(exc).__name__})"
    return widget_id, ""


def _finish(ctx, spec: dict, what: str) -> str:
    _widget_id, err = _push(ctx, spec)
    if err:
        return (f"{err}:这张{what}没展示出来。请改用文字或普通表格把结论说清,"
                "不要让用户以为界面里已经有一张图。")
    source = f"数据来源:{spec['note']}" if spec.get("note") else ""
    return (f"已展示{what}《{spec['title']}》。用户可以在卡片上交互;"
            "你的正文只写结论和读法,不要再把同一份原始数据整段贴一遍。"
            + (f" {source}" if source else ""))


def show_chart(ctx, title: str = "", chart_type: str = "bar",
               labels=None, series=None, note: str = "") -> str:
    spec, err = widgets.normalize_chart({
        "title": title, "chart_type": chart_type,
        "labels": labels, "series": series, "note": note})
    if err:
        return f"没画:{err}"
    return _finish(ctx, spec, "图表")


def show_table(ctx, title: str = "", columns=None, rows=None,
               note: str = "") -> str:
    spec, err = widgets.normalize_table({
        "title": title, "columns": columns, "rows": rows, "note": note})
    if err:
        return f"没画:{err}"
    return _finish(ctx, spec, "表格")


def show_checklist(ctx, title: str = "", items=None,
                   note: str = "") -> str:
    spec, err = widgets.normalize_checklist({
        "title": title, "items": items, "note": note})
    if err:
        return f"没画:{err}"
    return _finish(ctx, spec, "清单")


def tools() -> list[Tool]:
    """可视化那一族。"""
    return [
        Tool(
            name="show_chart",
            description=(
                "把一组数据画成**可交互图表**(柱状/折线/饼图),直接出现在对话里:"
                "用户可以悬停看数值、点图例切换系列。**什么时候用**:数据有 2 个以上"
                "可比项,或用户说「看看/对比/趋势/画个图」时;只有两三个数字就直接说,"
                "不必画。**怎么填**:labels 是横轴/扇区名(短、中文),series 每个系列"
                "的 values 数量必须与 labels 一致;饼图只能一个系列。**边界**:labels "
                f"最多 {widgets.MAX_LABELS} 项、series 最多 {widgets.MAX_SERIES} 个;"
                "超过会被拒绝,请先汇总。调用后正文只写结论和读法,**不要**再把同一份"
                "原始数据整段贴一遍。note 写数据来源(哪个文件/哪次查询),别让用户"
                "以为图是凭空画的。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "一句话说清这张图画的是什么"},
                    "chart_type": {"type": "string", "enum": ["bar", "line", "pie"],
                                   "description": "bar=柱状(默认);line=折线(趋势);pie=饼图(占比,只能一个系列)"},
                    "labels": {"type": "array", "items": {"type": "string"},
                               "description": "横轴/扇区名,至少 2 项,最多 16 项"},
                    "series": {"type": "array", "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "系列名(图例上显示)"},
                            "values": {"type": "array", "items": {"type": "number"},
                                       "description": "与 labels 一一对应的数值"},
                        },
                        "required": ["name", "values"],
                    }, "description": "一到四个系列;每个系列的 values 长度必须等于 labels"},
                    "note": {"type": "string", "description": "数据来源/口径,一句话"},
                },
                "required": ["title", "labels", "series"],
            },
            handler=show_chart,
            read_only=True,
        ),
        Tool(
            name="show_table",
            description=(
                "把结构化数据做成**可排序/可搜索的表格**卡片。**什么时候用**:列少、"
                "行多,用户要逐条看或找某一项时;两三行直接说就行。columns 是列名"
                "(短、中文),rows 是二维数组,每行长度必须与 columns 一致。**边界**:"
                f"最多 {widgets.MAX_COLS} 列、{widgets.MAX_ROWS} 行;超过会被拒绝,"
                "请先汇总。表格里的数字按数值排序(写成数字或数字字符串都行),其余按文本;"
                "note 写数据来源。调用后正文只写结论,不要再贴一遍原始表格。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "表格标题"},
                    "columns": {"type": "array", "items": {"type": "string"},
                                "description": "列名,最多 8 列"},
                    "rows": {"type": "array", "items": {
                        "type": "array",
                        "items": {"type": ["string", "number"]},
                    }, "description": "二维数组;每行长度必须等于 columns 数"},
                    "note": {"type": "string", "description": "数据来源/口径,一句话"},
                },
                "required": ["title", "columns", "rows"],
            },
            handler=show_table,
            read_only=True,
        ),
        Tool(
            name="show_checklist",
            description=(
                "把一组可选项做成**可勾选的清单**卡片;用户勾完可以一键把选择发回对话。"
                "**什么时候用**:让用户从若干件事/若干项里挑,而选项本身需要解释时。"
                "items 每项是 {label, note};label 是一眼能认出的短句,note 解释这一项"
                f"意味着什么。**边界**:最多 {widgets.MAX_ITEMS} 项,label 不能重复。"
                "调用后正文只写一句「勾好点发送」,不要替用户预选,也不要把清单再贴一遍。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "清单标题"},
                    "items": {"type": "array", "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string", "description": "选项短句"},
                            "note": {"type": "string", "description": "这一项意味着什么"},
                        },
                        "required": ["label"],
                    }, "description": "一到三十项"},
                    "note": {"type": "string", "description": "这组选项从哪来/怎么用,一句话"},
                },
                "required": ["title", "items"],
            },
            handler=show_checklist,
            read_only=True,
        ),
    ]
