"""附件测试:落盘去重、图片压缩、多模态消息构造。"""
from pathlib import Path

import pytest

from minicode import attachments as att
from minicode.attachments import (
    Attachment, attachment_note, build_content, display_text, human_size,
    image_data_url, parse_attachment_note, preview_data_url, save_attachment,
    save_attachments, strip_attachment_note,
)

PIL = pytest.importorskip("PIL.Image", reason="图片压缩测试需要 Pillow")


def _img(path: Path, size=(80, 60), mode="RGBA", color=(200, 30, 30, 180)):
    from PIL import Image
    Image.new(mode, size, color).save(path)
    return path


def _noisy_jpeg(path: Path, size=(2400, 1800)):
    """随机噪声图:压缩率低,用来验证逐级降质量/缩边长。"""
    import os
    from PIL import Image
    im = Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3))
    im.save(path, quality=100)
    return path


def test_human_size():
    assert human_size(512) == "512 B"
    assert human_size(2048) == "2 KB"
    assert human_size(3 * 1024 * 1024) == "3.0 MB"


def test_save_attachment_copies_and_dedupes(tmp_path):
    src = tmp_path / "报告.txt"
    src.write_text("hi", encoding="utf-8")
    ws = tmp_path / "ws"
    ws.mkdir()

    a1 = save_attachment(ws, src)
    a2 = save_attachment(ws, src)
    assert a1.path == "uploads/报告.txt"
    assert a2.path == "uploads/报告-1.txt"          # 重名自动加序号
    assert (ws / a1.path).read_text(encoding="utf-8") == "hi"
    assert a1.kind == "file" and a1.mime == "application/octet-stream"


def test_save_attachment_detects_image(tmp_path):
    src = _img(tmp_path / "a.png")
    ws = tmp_path / "ws"
    ws.mkdir()
    a = save_attachment(ws, src)
    assert a.kind == "image" and a.mime == "image/png"


def test_save_attachment_rejects_oversize(tmp_path, monkeypatch):
    monkeypatch.setattr(att, "MAX_ATTACHMENT_BYTES", 4)
    src = tmp_path / "big.bin"
    src.write_bytes(b"0123456789")
    with pytest.raises(ValueError, match="上限"):
        save_attachment(tmp_path / "ws", src)


# ---------------------------------------------------------------- 拖拽(按字节落盘)

def test_save_dropped_bytes_writes_and_dedupes(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    a1 = att.save_dropped_bytes(ws, "笔记.txt", "内容".encode("utf-8"))
    a2 = att.save_dropped_bytes(ws, "笔记.txt", b"x")
    assert a1.path == "uploads/笔记.txt"
    assert a2.path == "uploads/笔记-1.txt"
    assert (ws / a1.path).read_text(encoding="utf-8") == "内容"
    assert a1.kind == "file"


def test_save_dropped_bytes_detects_image(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    png = _img(tmp_path / "a.png").read_bytes()
    a = att.save_dropped_bytes(ws, "截图.png", png)
    assert a.kind == "image" and a.mime == "image/png"


def test_save_dropped_bytes_rejects_oversize(tmp_path, monkeypatch):
    monkeypatch.setattr(att, "MAX_ATTACHMENT_BYTES", 4)
    with pytest.raises(ValueError, match="上限"):
        att.save_dropped_bytes(tmp_path / "ws", "big.bin", b"0123456789")


def test_save_dropped_bytes_sanitizes_name(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    a = att.save_dropped_bytes(ws, r"..\..\evil.txt", b"hi")
    assert a.name == "evil.txt"                       # 只取文件名,路径成分被剥掉
    assert (ws / "uploads" / "evil.txt").is_file()



def test_save_attachments_collects_errors(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    good = tmp_path / "ok.txt"
    good.write_text("x", encoding="utf-8")
    ok, errors = save_attachments(ws, [str(good), str(tmp_path / "缺失.txt")])
    assert [a.name for a in ok] == ["ok.txt"]
    assert len(errors) == 1 and "不是文件" in errors[0]


def test_compress_image_keeps_png_alpha(tmp_path):
    src = _img(tmp_path / "a.png", size=(100, 100))
    data, mime = att.compress_image(src)
    assert mime == "image/png"
    assert data[:8] == b"\x89PNG\r\n\x1a\n"


def test_compress_image_resizes_big_jpeg(tmp_path):
    src = _img(tmp_path / "big.jpg", size=(3000, 1500), mode="RGB",
               color=(120, 120, 120))
    from PIL import Image
    import io
    data, mime = att.compress_image(src, max_edge=800)
    assert mime == "image/jpeg"
    with Image.open(io.BytesIO(data)) as im:
        assert max(im.size) <= 800
        assert im.size[0] / im.size[1] == pytest.approx(2.0, rel=0.02)


def test_compress_image_shrinks_under_byte_limit(tmp_path):
    src = _noisy_jpeg(tmp_path / "noise.jpg")
    assert src.stat().st_size > 1024 * 1024          # 原图确实超过 1MB
    data, mime = att.compress_image(src, max_edge=1536, max_bytes=1024 * 1024)
    assert mime == "image/jpeg"
    assert len(data) <= 1024 * 1024


def test_compress_image_gif_passthrough(tmp_path):
    from PIL import Image
    p = tmp_path / "a.gif"
    Image.new("P", (30, 30)).save(p)
    data, mime = att.compress_image(p)
    assert mime == "image/gif"
    assert data == p.read_bytes()                    # 动图不重编码


def test_compress_image_tolerates_broken_file(tmp_path):
    p = tmp_path / "broken.png"
    p.write_bytes(b"not an image at all")
    data, mime = att.compress_image(p)               # 不抛异常,原样返回
    assert data == b"not an image at all"


def test_data_urls(tmp_path):
    src = _img(tmp_path / "a.png")
    url = image_data_url(src)
    assert url.startswith("data:image/png;base64,")
    prev = preview_data_url(src)
    assert prev.startswith("data:image/")
    assert image_data_url(tmp_path / "nope.png") is None


# ------------------------------------------------------------------ 消息体

def _att(path, kind="image", size=1234):
    return Attachment(name=Path(path).name, path=str(path), size=size,
                      kind=kind, mime="image/png" if kind == "image" else "")


def test_attachment_note_mentions_vision_state(tmp_path):
    atts = [_att("uploads/a.png", "image"), _att("uploads/b.pdf", "file")]
    note_off = attachment_note(atts, vision=False)
    note_on = attachment_note(atts, vision=True)
    assert "uploads/a.png" in note_off and "uploads/b.pdf" in note_off
    assert "不支持看图" in note_off and "已随消息提供" in note_on
    # 看不了图时不能只说"不行",要给一条真能走的路(工具自己会报能不能用)
    assert "ocr_image" in note_off and "ocr_image" not in note_on
    assert attachment_note([], vision=True) == ""


def test_build_content_plain_when_no_attachments():
    assert build_content("你好", [], vision=True) == "你好"
    assert build_content("你好", None, vision=False) == "你好"


def test_build_content_text_only_without_vision(tmp_path):
    src = _img(tmp_path / "a.png")
    out = build_content("看看这张图", [_att(src)], vision=False)
    assert isinstance(out, str)
    assert out.startswith("[附件]") and "看看这张图" in out


def test_build_content_multimodal_with_vision(tmp_path):
    (tmp_path / "uploads").mkdir()
    src = _img(tmp_path / "uploads" / "a.png")
    out = build_content("看看这张图", [_att(src)], vision=True)
    assert isinstance(out, list)
    assert out[0]["type"] == "text" and "看看这张图" in out[0]["text"]
    assert out[1]["type"] == "image_url"
    assert out[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_build_content_falls_back_when_image_unreadable(tmp_path):
    out = build_content("图没了", [_att(tmp_path / "missing.png")], vision=True)
    assert isinstance(out, str) and "图没了" in out


def test_display_text_strips_note():
    raw = attachment_note([_att("uploads/a.png")], vision=False) + "\n\n真正的问题"
    assert display_text(raw) == "真正的问题"
    assert display_text([{"type": "text", "text": "纯文本段"}]) == "纯文本段"
    assert strip_attachment_note("没有附件前缀") == "没有附件前缀"
    assert display_text(None) == ""


# ---------------------------------------------------- [附件] 清单读回(v0.9.69)

def test_parse_attachment_note_roundtrip():
    """写出去的清单要能原样读回来:生成器与解析器是同一条格式的一进一出。"""
    atts = [_att("uploads/拍题.png"), _att("uploads/notes.pdf", "file")]
    parsed = parse_attachment_note(attachment_note(atts, vision=False))
    assert [p["path"] for p in parsed] == ["uploads/拍题.png", "uploads/notes.pdf"]
    assert all(p["size_text"] == "1 KB" for p in parsed)


def test_parse_attachment_note_filename_with_parens():
    """文件名里的括号不能把路径切错:锚点是 human_size 的大小,不是第一个左括号。"""
    note = ("[附件] 用户上传了以下文件,已保存到工作区:\n"
            "- uploads/报告(1).png(12 KB)(图片已随消息提供给模型查看)\n\n正文")
    parsed = parse_attachment_note(note)
    assert len(parsed) == 1
    assert parsed[0]["path"] == "uploads/报告(1).png"
    assert parsed[0]["size_text"] == "12 KB"


def test_parse_attachment_note_rejects_non_note_and_noise_lines():
    assert parse_attachment_note("普通一句话") == []
    assert parse_attachment_note(None) == []
    assert parse_attachment_note("") == []
    note = ("[附件] 用户上传了以下文件,已保存到工作区:\n"
            "- uploads/a.png(1 KB)\n"
            "需要文件内容时,请用 read_file / bash 等工具处理上述路径;\n"
            "不要臆测文件内容。\n\n正文")
    assert [p["path"] for p in parse_attachment_note(note)] == ["uploads/a.png"]
