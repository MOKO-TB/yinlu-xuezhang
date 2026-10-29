"""Office 文档的**文字预览与块定位**(v0.9.185)。

不是版式渲染:Word 按段落/表格,PowerPoint 按幻灯片/文本形状,Excel 按工作表/
行/单元格。每个块带 `data-block`,批注脚本把它回传;Agent 再用 python-docx /
python-pptx / openpyxl 按块定位改工作副本。

为什么不做"看起来和 Office 一样":那要引入一套排版引擎,而这里真正需要的
是"让用户指到哪一段/哪一页/哪一行"。文字预览够指认,且不需要执行任何东西。
所有文档文字都 `html.escape`;超出上限**明说截断**,不静默少显示。
"""
from __future__ import annotations

import html
from pathlib import Path

MAX_DOCX_PARAGRAPHS = 400
MAX_DOCX_TABLES = 20
MAX_TABLE_ROWS = 60
MAX_TABLE_COLS = 8
MAX_PPTX_SLIDES = 60
MAX_PPTX_SHAPES = 30
MAX_XLSX_SHEETS = 10
MAX_XLSX_ROWS = 200
MAX_XLSX_COLS = 12
MAX_CELL = 300
MAX_TEXT = 4000

OFFICE_CSS = """
body{margin:0;background:#fff;color:#1a2b36;font:14px/1.75 "Microsoft YaHei",system-ui,sans-serif;padding:18px 22px}
h1,h2,h3{line-height:1.4;margin:18px 0 8px}
.office-title{font-size:13px;color:#6b7c8c;border-bottom:1px solid #e3e9f0;
  padding-bottom:8px;margin-bottom:14px}
.docx p{margin:0 0 8px;white-space:pre-wrap}
.docx table,.xlsx table{border-collapse:collapse;width:100%;margin:10px 0;font-size:12.5px}
td,th{border:1px solid #e3e9f0;padding:5px 7px;vertical-align:top;text-align:left}
th{background:#f4f8fb}
[data-block]{border-radius:4px}
[data-block]:hover{background:#eef8f6;outline:1px solid #9bd9d0}
.slide{border:1px solid #e3e9f0;border-radius:10px;padding:12px 14px;margin:0 0 12px}
.slide h3{margin:0 0 8px;font-size:15px}
.slide .shape{margin:0 0 8px;white-space:pre-wrap}
.sheet{margin:0 0 16px}
.sheet h3{margin:0 0 6px;font-size:15px}
.muted{color:#6b7c8c;font-size:12px}
.cmp-changed{background:#fff3c4!important;outline:2px solid #e8b84a!important}
"""


def _esc(value) -> str:
    return html.escape(str(value or ""), quote=True)


def _clip(value, limit: int = MAX_CELL) -> str:
    s = str(value or "")
    return s if len(s) <= limit else s[:limit] + "…"


def _note(parts: list[str]) -> str:
    return "文字预览（不是版式）" + ("；" + "；".join(parts) if parts else "")


def _render_docx(path: Path, highlight=None,
                 blocks_out: list | None = None) -> tuple[str, str]:
    from docx import Document

    hi = highlight or set()
    doc = Document(str(path))
    out = ['<div class="document docx">',
           '<div class="office-title">Word 文字预览（不是版式） · 段落/表格可批注</div>']
    truncated: list[str] = []
    paragraphs = list(doc.paragraphs)
    shown = 0
    for i, p in enumerate(paragraphs, start=1):
        text = p.text or ""
        if not text.strip():
            continue
        if i > MAX_DOCX_PARAGRAPHS:
            truncated.append(f"段落只显示到第 {MAX_DOCX_PARAGRAPHS} 个")
            break
        style = ""
        try:
            style = (p.style.name or "").lower() if p.style else ""
        except Exception:
            style = ""
        tag = "p"
        if "heading" in style or "标题" in style:
            level = "1" if "1" in style else ("2" if "2" in style else "3")
            tag = "h" + level
        block_id = f"p{i}"
        if blocks_out is not None:
            blocks_out.append((block_id, _clip(text, MAX_TEXT)))
        cls = ' class="cmp-changed"' if block_id in hi else ""
        out.append(f'<{tag} data-block="{block_id}"{cls}>'
                   f'{_esc(_clip(text, MAX_TEXT))}</{tag}>')
        shown += 1
    if shown == 0:
        out.append('<p class="muted">这份文档没有可读的段落文字。</p>')
    tables = list(doc.tables)
    for ti, table in enumerate(tables[:MAX_DOCX_TABLES], start=1):
        out.append(f'<table data-block="t{ti}">')
        rows = list(table.rows)
        for ri, row in enumerate(rows[:MAX_TABLE_ROWS], start=1):
            out.append("<tr>")
            for ci, cell in enumerate(list(row.cells)[:MAX_TABLE_COLS], start=1):
                block_id = f"t{ti}-r{ri}-c{ci}"
                cell_text = _clip(cell.text, MAX_CELL)
                if blocks_out is not None:
                    blocks_out.append((block_id, cell_text))
                cls = ' class="cmp-changed"' if block_id in hi else ""
                out.append(f'<td data-block="{block_id}"{cls}>'
                           f'{_esc(cell_text)}</td>')
            out.append("</tr>")
        out.append("</table>")
        if len(rows) > MAX_TABLE_ROWS:
            truncated.append(f"表格 {ti} 只显示前 {MAX_TABLE_ROWS} 行")
    if len(tables) > MAX_DOCX_TABLES:
        truncated.append(f"只显示前 {MAX_DOCX_TABLES} 个表格")
    out.append("</div>")
    return "".join(out), _note(truncated)


def _render_pptx(path: Path, highlight=None,
                 blocks_out: list | None = None) -> tuple[str, str]:
    from pptx import Presentation

    hit = highlight or set()
    prs = Presentation(str(path))
    out = ['<div class="document pptx">',
           '<div class="office-title">PowerPoint 文字预览（不是版式） · 每页/文本形状可批注</div>']
    truncated: list[str] = []
    slides = list(prs.slides)
    for si, slide in enumerate(slides[:MAX_PPTX_SLIDES], start=1):
        out.append(f'<section class="slide" data-block="slide-{si}">')
        out.append(f'<h3 data-block="slide-{si}">第 {si} 页</h3>')
        shapes = list(slide.shapes)
        shown = 0
        for sh_i, shape in enumerate(shapes[:MAX_PPTX_SHAPES], start=1):
            try:
                text = (shape.text_frame.text
                        if getattr(shape, "has_text_frame", False) else "")
            except Exception:
                text = ""
            if not text.strip():
                continue
            block_id = f"slide-{si}-shape-{sh_i}"
            text = _clip(text, MAX_TEXT)
            if blocks_out is not None:
                blocks_out.append((block_id, text))
            cls = ' class="cmp-changed"' if block_id in hit else ""
            out.append(f'<div class="shape" data-block="{block_id}"{cls}>'
                       f'{_esc(text)}</div>')
            shown += 1
        if shown == 0:
            out.append('<p class="muted">这一页没有可读文本（可能是图片/图表）。</p>')
        if len(shapes) > MAX_PPTX_SHAPES:
            truncated.append(f"第 {si} 页只显示前 {MAX_PPTX_SHAPES} 个形状")
        out.append("</section>")
    if not slides:
        out.append('<p class="muted">这份演示文稿没有幻灯片。</p>')
    if len(slides) > MAX_PPTX_SLIDES:
        truncated.append(f"只显示前 {MAX_PPTX_SLIDES} 页")
    out.append("</div>")
    return "".join(out), _note(truncated)


def _render_xlsx(path: Path, highlight=None,
                 blocks_out: list | None = None) -> tuple[str, str]:
    from openpyxl import load_workbook

    hit = highlight or set()
    wb = load_workbook(str(path), read_only=True, data_only=True)
    out = ['<div class="document xlsx">',
           '<div class="office-title">Excel 文字预览（不是版式） · 工作表/行/单元格可批注</div>']
    truncated: list[str] = []
    sheets = list(wb.worksheets)
    try:
        for si, ws in enumerate(sheets[:MAX_XLSX_SHEETS], start=1):
            out.append(f'<section class="sheet" data-block="sheet-{si}">')
            out.append(f'<h3 data-block="sheet-{si}">工作表 {si}：{_esc(ws.title)}</h3>')
            out.append("<table>")
            rows = 0
            for ri, row in enumerate(ws.iter_rows(min_row=1, max_row=MAX_XLSX_ROWS,
                                                  max_col=MAX_XLSX_COLS,
                                                  values_only=True), start=1):
                out.append(f'<tr data-block="sheet-{si}!r{ri}">')
                for ci, value in enumerate(row[:MAX_XLSX_COLS], start=1):
                    block_id = f"sheet-{si}!r{ri}-c{ci}"
                    cell_text = _clip(value, MAX_CELL)
                    if blocks_out is not None:
                        blocks_out.append((block_id, cell_text))
                    cls = ' class="cmp-changed"' if block_id in hit else ""
                    out.append(f'<td data-block="{block_id}"{cls}>'
                               f'{_esc(cell_text)}</td>')
                out.append("</tr>")
                rows += 1
            out.append("</table>")
            if rows >= MAX_XLSX_ROWS:
                truncated.append(f"工作表 {si} 只显示前 {MAX_XLSX_ROWS} 行")
            if rows == 0:
                out.append('<p class="muted">这个工作表是空的。</p>')
            out.append("</section>")
    finally:
        try:
            wb.close()
        except Exception:
            pass
    if not sheets:
        out.append('<p class="muted">这份工作簿没有工作表。</p>')
    if len(sheets) > MAX_XLSX_SHEETS:
        truncated.append(f"只显示前 {MAX_XLSX_SHEETS} 个工作表")
    out.append("</div>")
    return "".join(out), _note(truncated)


def render_office(path: Path, highlight=None,
                  blocks_out: list | None = None) -> tuple[str | None, str]:
    """返回 (可读 HTML 正文, 说明);读不出来时正文为 None,说明里写原因。

    `highlight` 是块 id 集合(对比视图用来标黄);`blocks_out` 传列表时把
    (块 id, 文本) 按渲染顺序收进去,供块级 diff 用 —— 这里的索引规则和
    HTML 里的 data-block 是同一份,不另写一套。
    """
    suffix = Path(path).suffix.lower()
    try:
        if suffix == ".docx":
            return _render_docx(Path(path), highlight, blocks_out)
        if suffix == ".pptx":
            return _render_pptx(Path(path), highlight, blocks_out)
        if suffix == ".xlsx":
            return _render_xlsx(Path(path), highlight, blocks_out)
    except Exception as exc:
        return None, f"这份 {suffix} 读不出来({type(exc).__name__}: {exc})"
    return None, f"不认识的文件类型:{suffix}"


def diff_blocks(old_blocks: list, new_blocks: list, limit: int = 40) -> dict:
    """块级差异:Office 的段落/单元格/形状按 data-block 对比。"""
    old = dict(old_blocks)
    new = dict(new_blocks)
    changes: list[dict] = []
    counts = {"changed": 0, "added": 0, "removed": 0}

    def add(kind: str, block_id: str, old_text: str = "",
            new_text: str = "") -> None:
        counts[kind] += 1
        if len(changes) < limit:
            changes.append({"type": kind, "id": block_id,
                            "old": str(old_text)[:300],
                            "new": str(new_text)[:300]})

    for block_id, old_text in old_blocks:
        if block_id in new:
            if old_text != new[block_id]:
                add("changed", block_id, old_text, new[block_id])
        else:
            add("removed", block_id, old_text, "")
    for block_id, new_text in new_blocks:
        if block_id not in old:
            add("added", block_id, "", new_text)
    total = sum(counts.values())
    summary = ("两版内容一样" if total == 0 else
               f"共 {total} 处块变化（改 {counts['changed']} / "
               f"增 {counts['added']} / 删 {counts['removed']}）")
    return {"kind": "blocks", "summary": summary, "changes": changes,
            "counts": counts, "truncated": max(0, total - limit)}
