# -*- coding: utf-8 -*-
"""Office 文字预览与块批注(v0.9.185)。

不做版式渲染,只回答"用户指到了哪一段/哪一页/哪一行":生成的 HTML 带
`data-block`,批注脚本回传它,Agent 再用 python-docx/pptx/openpyxl 按块改
工作副本。这里钉四件事:
1. 文档文字必须 escape(Word 里的 `<script>` 不能变成可执行标签);
2. docx/pptx/xlsx 都能生成块 id,且说明里写明"文字预览(不是版式)";
3. Office 批注必须带 block,工作副本是原文件的副本,原文件不动;
4. 采用工作版走同一套备份/替换,Office 也成立。
"""
from __future__ import annotations

import json
from pathlib import Path

from docx import Document
from openpyxl import Workbook
from pptx import Presentation
from pptx.util import Inches

from minicode import office_preview, panel
from minicode.gui_ui import _panel_arg
from minicode.llm import AssistantTurn
from test_gui_bridge import FakeLLM, make, wait_until


def _write_docx(path: Path) -> None:
    doc = Document()
    doc.add_heading("期中复盘", level=1)
    doc.add_paragraph("高数 92")
    doc.add_paragraph("<script>alert(1)</script>")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "科目"
    table.cell(0, 1).text = "分数"
    table.cell(1, 0).text = "高数"
    table.cell(1, 1).text = "92"
    doc.save(str(path))


def _write_pptx(path: Path) -> None:
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(5), Inches(2))
    box.text_frame.text = "绩点趋势"
    prs.save(str(path))


def _write_xlsx(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "成绩"
    ws.append(["姓名", "分数"])
    ws.append(["张三", 92])
    wb.save(str(path))


def test_docx_preview_has_blocks_and_escapes_document_text(tmp_path):
    path = tmp_path / "复盘.docx"
    _write_docx(path)
    body, note = office_preview.render_office(path)
    assert body and "文字预览" in note
    assert "期中复盘" in body and "高数 92" in body
    assert 'data-block="p2"' in body and 'data-block="t1-r1-c1"' in body
    assert "&lt;script&gt;" in body and "<script>alert" not in body


def test_pptx_and_xlsx_previews_have_slide_and_sheet_blocks(tmp_path):
    pptx = tmp_path / "汇报.pptx"
    _write_pptx(pptx)
    body, note = office_preview.render_office(pptx)
    assert body and "文字预览" in note
    assert 'data-block="slide-1"' in body and "绩点趋势" in body

    xlsx = tmp_path / "名单.xlsx"
    _write_xlsx(xlsx)
    body, note = office_preview.render_office(xlsx)
    assert body and "文字预览" in note
    assert 'data-block="sheet-1!r1"' in body and "成绩" in body
    assert "张三" in body and "92" in body


def test_office_preview_modes_and_block_annotation(config, tmp_path):
    path = tmp_path / "复盘.docx"
    _write_docx(path)
    llm = FakeLLM([AssistantTurn(text="改好了")])
    bridge, _window, _ = make(config, tmp_path, llm)

    r = bridge.panel_preview("复盘.docx", "preview")
    assert r["ok"] and r["mode"] == "preview"
    assert 'data-block="p2"' in r["html"] and "ann-select" not in r["html"]

    r = bridge.panel_preview("复盘.docx", "annotate")
    assert r["ok"] and r["mode"] == "annotate"
    assert 'data-block="p2"' in r["html"] and "ann-select" in r["html"]

    r = bridge.panel_annotate("复盘.docx", json.dumps({
        "mode": "text", "text": "高数 92", "block": "p2",
        "selector": "p:nth-child(2)", "tag": "p",
        "instruction": "改成 95，并注明补考"}))
    assert r["ok"], r
    assert r["working"] == "annotations/复盘.working.docx"
    working = tmp_path / "annotations" / "复盘.working.docx"
    assert working.exists()
    assert path.read_bytes() == working.read_bytes()
    assert "p2" in r["text"] and "python-docx" in r["text"]
    assert wait_until(lambda: not bridge._busy)
    user = [m for m in bridge.session.messages if m.get("role") == "user"]
    assert "p2" in user[-1]["content"] and "annotations/复盘.working.docx" in user[-1]["content"]


def test_office_annotation_requires_a_block(tmp_path):
    path = tmp_path / "复盘.docx"
    _write_docx(path)
    from minicode.gui_bridge import create_app
    from minicode.config import Config, Provider

    cfg = Config(providers=[Provider(id="p", name="P", base_url="https://x/v1",
                                      api_key="k", model="m")],
                 active_provider_id="p", mcp_servers=[], sandbox={})
    bridge, _ui, _s, _a = create_app(cfg, tmp_path)
    r = bridge.panel_annotate("复盘.docx", json.dumps({
        "mode": "text", "text": "高数 92", "instruction": "改"}))
    assert r["ok"] is False and "请点一段文字" in r["msg"]


def test_office_adopt_backs_up_and_replaces_the_original(tmp_path):
    from minicode.gui_bridge import create_app
    from minicode.config import Config, Provider

    path = tmp_path / "复盘.docx"
    _write_docx(path)
    ann = tmp_path / "annotations"
    ann.mkdir()
    working = ann / "复盘.working.docx"
    working.write_bytes(path.read_bytes())
    doc = Document(str(working))
    doc.paragraphs[1].text = "高数 95（补考）"
    doc.save(str(working))

    cfg = Config(providers=[Provider(id="p", name="P", base_url="https://x/v1",
                                      api_key="k", model="m")],
                 active_provider_id="p", mcp_servers=[], sandbox={})
    bridge, _ui, _s, _a = create_app(cfg, tmp_path)
    r = bridge.panel_adopt("复盘.docx", "annotations/复盘.working.docx")
    assert r["ok"], r
    new = Document(str(path))
    assert "高数 95（补考）" in [p.text for p in new.paragraphs]
    backup = Document(str(tmp_path / r["backup"]))
    assert "高数 92" in [p.text for p in backup.paragraphs]


def _plain_bridge(tmp_path):
    from minicode.config import Config, Provider
    from minicode.gui_bridge import create_app
    cfg = Config(providers=[Provider(id="p", name="P", base_url="https://x/v1",
                                      api_key="k", model="m")],
                 active_provider_id="p", mcp_servers=[], sandbox={})
    bridge, _ui, _s, _a = create_app(cfg, tmp_path)
    return bridge


def test_panel_compare_highlights_changed_office_blocks(tmp_path):
    bridge = _plain_bridge(tmp_path)
    src = tmp_path / "复盘.docx"
    _write_docx(src)
    ann = tmp_path / "annotations"
    ann.mkdir()
    work = ann / "复盘.working.docx"
    work.write_bytes(src.read_bytes())
    doc = Document(str(work))
    doc.paragraphs[1].text = "高数 95（补考）"
    doc.save(str(work))
    r = bridge.panel_compare("复盘.docx", "annotations/复盘.working.docx")
    assert r["ok"], r
    assert r["diff"]["kind"] == "blocks"
    assert r["diff"]["counts"]["changed"] == 1
    ids = [c["id"] for c in r["diff"]["changes"]]
    assert "p2" in ids
    assert "cmp-changed" in r["original_html"]
    assert 'data-block="p2"' in r["original_html"]
    assert "cmp-changed" in r["working_html"]
    assert "ann-select" not in r["original_html"], "对比视图不能带批注脚本"
    assert "高数 95（补考）" in r["diff"]["changes"][0]["new"]


def test_panel_compare_text_diff_for_html(tmp_path):
    bridge = _plain_bridge(tmp_path)
    (tmp_path / "a.html").write_text(
        "<h1>标题</h1><p>第一版</p><p>保留</p>", encoding="utf-8")
    ann = tmp_path / "annotations"
    ann.mkdir()
    (ann / "a.working.html").write_text(
        "<h1>标题</h1><p>第二版</p><p>保留</p>", encoding="utf-8")
    r = bridge.panel_compare("a.html", "annotations/a.working.html")
    assert r["ok"] and r["diff"]["kind"] == "text"
    assert r["diff"]["counts"]["changed"] == 1
    assert "第二版" in r["diff"]["changes"][0]["new"]
    assert "ann-select" not in r["original_html"]


def test_panel_compare_missing_working_is_honest(tmp_path):
    bridge = _plain_bridge(tmp_path)
    (tmp_path / "a.html").write_text("<p>x</p>", encoding="utf-8")
    r = bridge.panel_compare("a.html", "annotations/nope.html")
    assert r["ok"] is False and "没有这一份" in r["msg"]


def test_public_api_can_compare():
    from minicode.gui_bridge import PublicApi
    assert callable(getattr(PublicApi, "panel_compare", None))


def test_office_is_an_artifact_but_still_not_an_image():
    assert panel.looks_like_artifact("复盘.docx")
    assert panel.looks_like_artifact("汇报.pptx")
    assert panel.looks_like_artifact("名单.xlsx")
    assert _panel_arg({"path": "复盘.docx"}) == "复盘.docx"
    assert _panel_arg({"path": "汇报.pptx"}) == "汇报.pptx"
    assert _panel_arg({"path": "名单.xlsx"}) == "名单.xlsx"
    assert _panel_arg({"path": "截图.png"}) == ""
