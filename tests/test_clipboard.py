"""v0.9.39 p1:输入框右键「粘贴」改走本机剪贴板 —— 不再弹那条授权横条。

钉四件事:
1. CF_DIB 补文件头的偏移量算准(算错的 BMP 不报错,只是整幅错位);
2. `read_clipboard()` 的返回形状就是 `add_dropped_attachments` 吃的那一种
   ({name, b64}),前端不需要再假造 File 绕一圈 FileReader;
3. 读不到 / 平台不支持 / 图片过大 各有各的说法,不拿"空"冒充"读不到";
4. 前端确实优先走本机桥,`navigator.clipboard` 只留在退路上;
   外加一道全局对账:前端写出来的每个 `pywebview.api.X` 必须在门面上存在。
   (这道对账当场揪出 `get_skill_body` —— 桥里有、门面漏转发,桌面版点技能
   卡片只会得到一句 TypeError 提示,老测试一个都没红。)
"""
from __future__ import annotations

import base64
import io
import re
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from minicode import clipboard  # noqa: E402
from minicode.attachments import MAX_ATTACHMENT_BYTES  # noqa: E402


def _dib(width: int = 3, height: int = 2, bpp: int = 32,
         clr_used: int | None = None) -> bytes:
    """造一段 BITMAPINFOHEADER + 像素(没有文件头,和 CF_DIB 的形状一致)。"""
    bytes_per_line = ((width * bpp + 31) // 32) * 4
    size = 40 + bytes_per_line * height
    bi_clr_used = (1 << bpp) if (bpp <= 8 and clr_used is None) else (clr_used or 0)
    header = struct.pack(
        "<IiiHHIIiiII", 40, width, height, 1, bpp, 0, size, 0, 0,
        bi_clr_used, 0)
    return header + bytes(bytes_per_line * height)


# ------------------------------------------------------------- BMP 偏移与降级


def test_bmp_header_offsets_are_exact():
    for bpp, clr_used, want_offset in ((32, None, 54), (24, None, 14 + 40),
                                       (8, None, 14 + 40 + 1024),
                                       (8, 2, 14 + 40 + 8),
                                       (1, None, 14 + 40 + 4 * 2)):
        dib = _dib(bpp=bpp, clr_used=clr_used)
        bmp = clipboard.bmp_from_dib(dib)
        assert bmp[:2] == b"BM"
        total = struct.unpack("<I", bmp[2:6])[0]
        offset = struct.unpack("<I", bmp[10:14])[0]
        assert offset == want_offset, f"{bpp}bpp clr_used={clr_used}"
        assert total == 14 + len(dib)
        assert bmp[14:] == dib                 # 主体一个字节都不动


def test_bmp_file_is_readable_by_pillow(monkeypatch):
    """偏移算错的 BMP **不报错**,只是整幅错位 —— 所以拿真解码器验一次。

    夹具直接用 Pillow 自己写出的 BMP 去掉文件头,保证"我们补回去的那个头"
    解出来的像素与它原来的图逐字节相同(没有 PIL 的机器如实 skip)。
    """
    try:
        from PIL import Image
    except Exception:
        pytest.skip("这台机器没有 Pillow:走的是 BMP 退路,PNG 那一条验不了")
    buf = io.BytesIO()
    Image.new("RGB", (3, 2), (10, 120, 240)).save(buf, "BMP")
    bmp = buf.getvalue()
    assert struct.unpack("<I", bmp[14:18])[0] == 40, "Pillow 不写标准头,夹具换了"
    rebuilt = clipboard.bmp_from_dib(bmp[14:])
    with Image.open(io.BytesIO(rebuilt)) as im:
        assert im.convert("RGB").getpixel((0, 0)) == (10, 120, 240)
    data, ext = clipboard.dib_to_file(bmp[14:])
    assert ext == "png" and data[:8] == b"\x89PNG\r\n\x1a\n"
    monkeypatch.setitem(sys.modules, "PIL", None)     # 没装 Pillow 的机器
    data2, ext2 = clipboard.dib_to_file(bmp[14:])
    assert (ext2, data2[:2]) == ("bmp", b"BM")


def test_bmp_refuses_headers_it_does_not_understand():
    with pytest.raises(ValueError) as e:
        clipboard.bmp_from_dib(b"\x28\x00\x00\x00")     # 4 字节,连头都不全
    assert "太短" in str(e.value)
    core = struct.pack("<IhhHH", 12, 3, -2, 24, 0) + b"\x00" * 32
    with pytest.raises(ValueError) as e:
        clipboard.bmp_from_dib(core)
    assert "BITMAPINFOHEADER" in str(e.value)             # 旧核心头:不猜,照实说


def test_dib_prefers_png_and_falls_back_to_bmp(monkeypatch):
    dib = _dib()
    data, ext = clipboard.dib_to_file(dib)
    if ext == "png":                            # 本机装了 Pillow(requirements 第 5 行)
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        monkeypatch.setitem(sys.modules, "PIL", None)      # import PIL 直接 ImportError
        data2, ext2 = clipboard.dib_to_file(dib)
        assert (ext2, data2[:2]) == ("bmp", b"BM")
    else:
        assert data[:2] == b"BM"
    assert len(data) < MAX_ATTACHMENT_BYTES


# ---------------------------------------------------------------- 返回的形状


def test_text_and_image_are_reported_apart(monkeypatch):
    monkeypatch.setattr(clipboard, "_read_windows", lambda: ("学长你好", []))
    r = clipboard.read_clipboard()
    assert r["ok"] and r["source"] == clipboard.TEXT
    assert r["text"] == "学长你好" and r["files"] == []

    monkeypatch.setattr(clipboard, "_read_windows", lambda: (None, [_dib()]))
    r = clipboard.read_clipboard()
    assert r["ok"] and r["source"] == clipboard.IMAGE and r["text"] == ""
    assert len(r["files"]) == 1
    item = r["files"][0]
    assert set(item) == {"name", "b64"}         # add_dropped_attachments 的形状
    assert item["name"].startswith("粘贴图片-")
    assert item["name"].endswith((".png", ".bmp"))
    got = base64.b64decode(item["b64"], validate=True)
    assert got[:2] in (b"BM", b"\x89P")         # 真是能落盘的字节,不是占位


def test_text_wins_when_both_are_present(monkeypatch):
    """剪贴板里既有文字又有图(微信复制常见):先把字给出去,图另说。

    一次粘贴只该落一个结果 —— 两个都塞会既打字又挂附件,不是用户点的意思。
    """
    monkeypatch.setattr(clipboard, "_read_windows",
                        lambda: ("一段文字", [_dib()]))
    r = clipboard.read_clipboard()
    assert r["source"] == clipboard.TEXT and r["files"] == []


def test_empty_and_unavailable_are_different_sentences(monkeypatch):
    """「读不到」与"里面没东西"是两句话 —— 混了就会拿 unavailable 去弹浏览器横条。

    这台机器上真撞上过:`_read_windows` 打开成功了但剪贴板里两种格式都没有,
    老写法 `(None, [])` 与"压根没打开"同一个形状,于是对用户说"被别的程序占着"。
    """
    monkeypatch.setattr(clipboard, "_read_windows", lambda: None)
    r = clipboard.read_clipboard()
    assert not r["ok"] and r["source"] == clipboard.UNAVAILABLE
    assert r["msg"]                                   # 说得出为什么读不到

    for got in (("", []), (None, [])):               # 打开了,里面确实没有
        monkeypatch.setattr(clipboard, "_read_windows", lambda g=got: g)
        r = clipboard.read_clipboard()
        assert r["source"] == clipboard.EMPTY, got
        assert "没有" in r["msg"] and "占用" not in r["msg"]


def test_non_windows_platform_says_so(monkeypatch):
    monkeypatch.setattr(clipboard.sys, "platform", "darwin")
    r = clipboard.read_clipboard()
    assert r["source"] == clipboard.UNAVAILABLE and "Ctrl+V" in r["msg"]


def test_oversized_image_is_skipped_with_the_size_named(monkeypatch):
    monkeypatch.setattr(clipboard, "_read_windows", lambda: (None, [_dib(64, 64)]))
    monkeypatch.setattr(clipboard, "MAX_ATTACHMENT_BYTES", 4)
    r = clipboard.read_clipboard()
    assert not r["ok"] and r["files"] == []
    assert "太大" in r["msg"] and "MB" in r["msg"]


def test_read_windows_returns_none_off_windows(monkeypatch):
    """真去调 ctypes 的那一段在非 Windows 上必须整段跳过(不是抛异常)。"""
    monkeypatch.setattr(clipboard.sys, "platform", "darwin")
    assert clipboard._read_windows() is None


# ------------------------------------------------------------ 前端确实换了路


def _html() -> str:
    return (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")


def test_paste_prefers_the_native_bridge():
    html = _html()
    paste = html.split('act === "paste"', 1)[1].split("hideCtxMenu();", 1)[0]
    assert "pywebview.api.read_clipboard" in paste
    # 浏览器剪贴板只能待在"桥不在/桥读不到"的退路里
    legacy = paste.split("const legacyPaste", 1)
    assert len(legacy) == 2, "navigator.clipboard 没被收进 legacyPaste 里"
    assert "navigator.clipboard.readText" in legacy[1]
    assert "navigator.clipboard" not in legacy[0]


def test_frontend_only_calls_api_that_exist():
    """前端每个 `pywebview.api.X` 都得在门面上 —— 漏一条 = 点一下只弹个 TypeError。"""
    from minicode.gui_bridge import PublicApi

    used = set(re.findall(r"pywebview\.api\.([A-Za-z_0-9]+)", _html()))
    assert used, "前端一个桥方法都没扫到 = 这个测试在空转"
    missing = sorted(n for n in used if not hasattr(PublicApi, n))
    assert not missing, f"门面漏转发: {missing}"


def test_web_viewers_cannot_read_the_hosts_clipboard():
    """网页版白名单里没有 read_clipboard:访客不能让服务器读它的剪贴板。"""
    from minicode.web_profile import PublicFacade

    facade = PublicFacade(object())
    assert "read_clipboard" not in facade.allowed_methods()


# ------------------------------------------------------------------ 桥与冻结


def test_facade_forwards_read_clipboard():
    import inspect

    from minicode.gui_bridge import PublicApi

    class Recorder:
        def read_clipboard(self):
            return {"ok": True, "source": "text", "text": "hi"}

    api = PublicApi(Recorder())
    assert api.read_clipboard() == {"ok": True, "source": "text", "text": "hi"}
    assert list(inspect.signature(PublicApi.read_clipboard).parameters) == ["self"]


def test_frozen_targets_include_the_clipboard_module():
    """漏收 = 装好的包里右键粘贴静默退回浏览器授权条(功能"在"但一直不可用)。"""
    text = (ROOT / "scripts" / "check_frozen_version.py").read_text(encoding="utf-8")
    assert "minicode.clipboard" in text
