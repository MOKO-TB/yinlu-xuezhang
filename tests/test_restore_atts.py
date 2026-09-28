"""恢复会话时的附件标签复原(v0.9.69,§6 #5 挂账的"历史会话附件可视化")。

留档里没有结构化附件字段,消息开头那段 [附件] 清单就是唯一事实(文件即状态);
restore_user_atts 只认它,并回磁盘核一遍 —— 文件在才给缩略图与真实大小,
不在就老实标 missing,路径跑出工作区的一律按"不在"处理,绝不照着读文件。
"""

from pathlib import Path

import pytest

from minicode.attachments import build_content, save_attachment
from minicode.gui_bridge import restore_user_atts

Image = pytest.importorskip("PIL.Image", reason="缩略图测试需要 Pillow")


def _img(path: Path, size=(80, 60)):
    Image.new("RGB", size, (120, 180, 160)).save(path)
    return path


def _ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / "uploads").mkdir(parents=True)
    return ws


def test_restore_image_gets_preview_and_real_size(tmp_path):
    ws = _ws(tmp_path)
    a = save_attachment(ws, _img(tmp_path / "拍题.png"))
    atts = restore_user_atts(ws, build_content("看看这张", [a], vision=False))
    assert len(atts) == 1
    assert atts[0]["name"] == "拍题.png"
    assert atts[0]["kind"] == "image"
    assert isinstance(atts[0]["size"], int) and atts[0]["size"] > 0
    # 压缩管线对不带透明的图会重编码成 JPEG,所以这里只钉"data URL 且是图片"
    assert atts[0]["preview"].startswith("data:image/")
    assert "missing" not in atts[0]


def test_restore_missing_file_is_honest(tmp_path):
    """文件被删了:标签照摆(名字与当时记下的大小),但绝不补一张编出来的缩略图。"""
    ws = _ws(tmp_path)
    content = ("[附件] 用户上传了以下文件,已保存到工作区:\n"
               "- uploads/丢了.png(9 KB)(图片:当前模型不支持看图,仅作文件引用;"
               "需要读出图里的文字时,可对下面这个路径调 ocr_image 工具——它能不能用、"
               "准不准由它自己说)\n\n正文")
    atts = restore_user_atts(ws, content)
    assert len(atts) == 1
    assert atts[0]["missing"] is True
    assert "preview" not in atts[0] and "size" not in atts[0]
    assert atts[0]["size_text"] == "9 KB"
    assert atts[0]["kind"] == "image"
    assert atts[0]["name"] == "丢了.png"


def test_restore_refuses_paths_outside_workspace(tmp_path):
    """清单里的路径跑出工作区(绝对路径/穿越):按"不在"处理,绝不照着路径去读。"""
    ws = _ws(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("机密", encoding="utf-8")
    for bad in (secret.as_posix(), "uploads/../../secret.txt"):
        content = ("[附件] 用户上传了以下文件,已保存到工作区:\n"
                   f"- {bad}(1 KB)\n\n正文")
        atts = restore_user_atts(ws, content)
        assert atts[0]["missing"] is True, bad
        assert "size" not in atts[0] and "preview" not in atts[0], bad


def test_restore_handles_vision_parts_and_plain_text(tmp_path):
    """视觉消息的留档是 content parts(清单在第一个 text 段里);没附件的返回空。"""
    ws = _ws(tmp_path)
    a = save_attachment(ws, _img(tmp_path / "b.png"))
    parts = build_content("看看", [a], vision=True)
    assert isinstance(parts, list)
    atts = restore_user_atts(ws, parts)
    assert len(atts) == 1 and atts[0]["preview"].startswith("data:image/")
    assert restore_user_atts(ws, "没有附件的一句话") == []
    assert restore_user_atts(ws, None) == []
