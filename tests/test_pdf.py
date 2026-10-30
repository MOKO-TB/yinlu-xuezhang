"""PDF 页级操作模块的口径测试。

造文件的两条路:pypdf 的 `add_blank_page` 造空白页;带文字层的最小 PDF 由本文件
手写字节、现算 xref(pypdf 只能读不能写文本,而 reportlab 那类不在依赖里)。
每支测试断的都是「产物用 pypdf 读回来真的对」,不是回话文案好看。
"""


import pytest
from pypdf import PdfReader, PdfWriter

from minicode.pdf import (PdfError, extract, extract_text, merge, parse_pages,
                          pdf_tool, rotate)


class _Ctx:
    def __init__(self, workspace):
        self.workspace = str(workspace)


# ---------------------------------------------------------------- 造文件

def _blank_pdf(path, pages=3):
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    with open(path, "wb") as f:
        w.write(f)
    return path


def _pdf_with_text_bytes(text: str) -> bytes:
    """最小单页 PDF,页面上有一行 Helvetica 文本。xref 按写入顺序现算。"""
    stream = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200]"
         b" /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"),
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF").encode()
    return out


def _text_pdf(path, text):
    path.write_bytes(_pdf_with_text_bytes(text))
    return path


@pytest.fixture
def ctx(tmp_path):
    return _Ctx(tmp_path)


# ---------------------------------------------------------------- 页码解析

def test_parse_pages_ranges_are_deduped_sorted_and_one_based():
    assert parse_pages("3,1,2-5,2", total=10) == [0, 1, 2, 3, 4]


def test_parse_pages_out_of_range_names_the_total(tmp_path):
    with pytest.raises(PdfError, match="共 3 页"):
        parse_pages("4", total=3)


def test_parse_pages_rejects_reversed_range_and_garbage():
    with pytest.raises(PdfError, match="写反"):
        parse_pages("5-2", total=10)
    with pytest.raises(PdfError, match="看不懂"):
        parse_pages("1;3", total=10)
    with pytest.raises(PdfError, match="没给"):
        parse_pages("", total=10)


# ---------------------------------------------------------------- merge

def test_merge_joins_pages_in_given_order(ctx, tmp_path):
    a = _text_pdf(tmp_path / "a.pdf", "AAA")
    b = _blank_pdf(tmp_path / "b.pdf", pages=2)
    c = _text_pdf(tmp_path / "c.pdf", "CCC")
    reply = merge(ctx, [a, b, c])
    assert "merged.pdf" in reply
    r = PdfReader(str(tmp_path / "merged.pdf"))
    assert len(r.pages) == 4
    # 顺序要真:第 1 页是 a 的字,第 4 页是 c 的字
    assert "AAA" in (r.pages[0].extract_text() or "")
    assert "CCC" in (r.pages[3].extract_text() or "")


def test_merge_needs_two_sources(ctx, tmp_path):
    one = _blank_pdf(tmp_path / "one.pdf", pages=1)
    # 走分发口(模型实际调的形状):PdfError 在这里必须转成人话,而不是抛栈
    assert "至少要给两份" in pdf_tool(ctx, "merge", sources=[str(one)])


def test_merge_refuses_existing_output_without_explicit_overwrite(ctx, tmp_path):
    a = _blank_pdf(tmp_path / "a.pdf", pages=1)
    b = _blank_pdf(tmp_path / "b.pdf", pages=1)
    c = _blank_pdf(tmp_path / "c.pdf", pages=3)
    pdf_tool(ctx, "merge", sources=[str(a), str(b)], out="out.pdf")
    before = len(PdfReader(str(tmp_path / "out.pdf")).pages)
    reply = pdf_tool(ctx, "merge", sources=[str(a), str(b)], out="out.pdf")
    assert "不覆盖" in reply
    assert len(PdfReader(str(tmp_path / "out.pdf")).pages) == before
    # 显式 overwrite 才放行;换一份更多页的源,覆盖结果必然不同
    pdf_tool(ctx, "merge", sources=[str(a), str(c)], out="out.pdf", overwrite=True)
    assert len(PdfReader(str(tmp_path / "out.pdf")).pages) == 4


# ---------------------------------------------------------------- extract / rotate

def test_extract_pulls_exactly_the_named_pages(ctx, tmp_path):
    src = _blank_pdf(tmp_path / "src.pdf", pages=5)
    reply = extract(ctx, src, "2,4")
    assert "第 2,4 页" in reply
    r = PdfReader(str(tmp_path / "src_p2,4.pdf"))
    assert len(r.pages) == 2


def test_rotate_writes_page_rotation_and_keeps_the_rest(ctx, tmp_path):
    src = _blank_pdf(tmp_path / "src.pdf", pages=3)
    reply = rotate(ctx, src, "2", 90)
    assert "第 2 页" in reply
    r = PdfReader(str(tmp_path / "src_rot90.pdf"))
    assert r.pages[1].rotation == 90
    assert r.pages[0].rotation == 0


def test_rotate_rejects_unknown_degrees(ctx, tmp_path):
    src = _blank_pdf(tmp_path / "src.pdf", pages=1)
    assert "只认" in pdf_tool(ctx, "rotate", src=str(src), pages="1", degrees=45)


# ---------------------------------------------------------------- text

def test_extract_text_pulls_the_real_text_layer(ctx, tmp_path):
    src = _text_pdf(tmp_path / "doc.pdf", "Hello PDF")
    reply = extract_text(ctx, src)
    assert "Hello PDF" in reply
    assert "第 1 页" in reply


def test_extract_text_on_blank_pages_points_to_ocr_instead_of_faking(ctx, tmp_path):
    src = _blank_pdf(tmp_path / "scan.pdf", pages=2)
    reply = extract_text(ctx, src)
    assert "没有文字层" in reply
    assert "ocr_image" in reply          # 指路必须真存在
    assert "Hello" not in reply


# ---------------------------------------------------------------- 拒绝路径

def test_encrypted_pdf_is_refused_without_guessing(ctx, tmp_path):
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    w.encrypt(user_password="secret")
    p = tmp_path / "locked.pdf"
    with open(p, "wb") as f:
        w.write(f)
    reply = pdf_tool(ctx, "text", src=p)
    assert "加了密" in reply and "不猜密码" in reply


def test_missing_and_non_pdf_sources_are_named(ctx, tmp_path):
    assert "找不到" in pdf_tool(ctx, "text", src=tmp_path / "nope.pdf")
    other = tmp_path / "pic.png"
    other.write_bytes(b"\x89PNG\r\n")
    assert "不是 PDF" in pdf_tool(ctx, "text", src=other)
    assert "ocr_image" in pdf_tool(ctx, "text", src=other)


def test_unknown_action_and_reader_failures_come_back_as_plain_words(ctx, tmp_path):
    assert "只认" in pdf_tool(ctx, "shred", src=tmp_path / "x.pdf")
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf at all")
    assert "没做成" in pdf_tool(ctx, "text", src=bad)


