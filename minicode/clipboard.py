"""读系统剪贴板 —— 给输入框右键菜单那一项「粘贴」用,不走浏览器的授权横条。

为什么要自己读(p1):`navigator.clipboard.read()` / `readText()` 在 WebView2 里
会弹出「http://127.0.0.1:25460 想要 查看复制到剪贴板的文本和图像 / 阻止 / 允许」。
那是自家桌面应用要粘自己剪贴板里的东西,却以"某个网站想读你剪贴板"的面目出现 ——
用户看到的是违和感,不是安全边界。Ctrl+V 那条路走的是 `paste` 事件里的
`e.clipboardData`,浏览器本来就不弹条,所以只有右键菜单这一处需要换成本机读。

只碰三个 Win32 格式:`CF_UNICODETEXT`(13) 文本、`CF_DIB`(8) 位图。
非 Windows、打开失败、格式不认识 → `source="unavailable"`,由前端退回原来的
异步剪贴板路径(该弹还是得弹,那时候它确实是浏览器行为)。
不装任何新依赖:只用标准库 `ctypes`,位图转 PNG 用包里已有的 Pillow
(`requirements.txt` 第 5 行);没装或读不出就照实给 `.bmp`,不假装是 PNG。

**本机实测留痕(2026-09-24)**:在智能体自己那个 shell 里 `OpenClipboard` 直接
失败,`GetLastError()=5`(拒绝访问)、`GetClipboardOwner()=0`,连 PowerShell 的
`Get-Clipboard` 都报「所请求的剪贴板操作失败」—— 那个会话挂在非交互的桌面站里,
拿不到剪贴板,不是本模块的 bug。pywebview 窗口跑在登录桌面上,那儿才有剪贴板;
真拿不到的时候本模块返回 `unavailable`,前端照实退回浏览器那条 —— 最坏情况就是
维持现状(那条横条还在),不会因此粘不上。
"""
from __future__ import annotations

import base64
import io
import struct
import sys
import time

from minicode.attachments import MAX_ATTACHMENT_BYTES

CF_UNICODETEXT = 13        # 文本(UTF-16 LE,以 NUL 结尾)
CF_DIB = 8                 # 位图主体:BITMAPINFOHEADER + 像素,缺 14 字节文件头
DIB_INFOHEADER = 40        # 只认标准 BITMAPINFOHEADER;BITMAPCOREHEADER(12) 不猜

TEXT = "text"
IMAGE = "image"
EMPTY = "empty"
UNAVAILABLE = "unavailable"


def _stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def _windll(name: str):
    """拿 kernel32/user32;非 Windows 或拿不到就返回 None(交上层降级)。"""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        return getattr(ctypes, "windll", None) and getattr(ctypes.windll, name)
    except Exception:
        return None


def _bind(ctypes, user32, kernel32) -> None:
    """把返回句柄/尺寸的三个函数钉成指针宽度。

    不设 restype 时 ctypes 按 C int(32 位)截断 `GetClipboardData` 的 HGLOBAL,
    64 位进程上拿到的是半个句柄 —— 后面 GlobalLock 直接读到别的内存。
    """
    user32.OpenClipboard.restype = ctypes.c_bool
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = ctypes.c_bool
    user32.IsClipboardFormatAvailable.restype = ctypes.c_bool
    user32.IsClipboardFormatAvailable.argtypes = [ctypes.c_uint]
    user32.GetClipboardData.restype = ctypes.c_void_p
    user32.GetClipboardData.argtypes = [ctypes.c_uint]
    kernel32.GlobalSize.restype = ctypes.c_size_t
    kernel32.GlobalSize.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]


def _locked_open(user32, ctypes) -> bool:
    """剪贴板常被别的进程临时占着(输入法、远程桌面),试三次、每次隔 40 ms。

    只等 120 ms 量级:这是点一下右键之后的同步动作,让用户对着菜单转圈不可接受。
    """
    for i in range(3):
        try:
            if user32.OpenClipboard(None):
                return True
        except Exception:
            return False
        time.sleep(0.04)
    return False


def _bytes_at(kernel32, ctypes, handle) -> bytes:
    size = kernel32.GlobalSize(handle) or 0
    if not size:
        return b""
    ptr = kernel32.GlobalLock(handle)
    if not ptr:
        return b""
    try:
        return ctypes.string_at(ptr, size)
    finally:
        kernel32.GlobalUnlock(handle)


def _read_windows():
    """一次打开剪贴板把文本与位图都取走。

    → `None` 表示**压根没打开成功**(平台不支持或被别的程序占着);
    → 打开了就返回 `(text, dibs)`,哪怕里面什么都没有。
    这两件事必须分开:后者是"剪贴板是空的",前者才是"读不到"。混成一句
    谎话的成本很实在 —— 前端会拿 unavailable 去退回浏览器那条会弹横条的路。
    """
    user32, kernel32 = _windll("user32"), _windll("kernel32")
    if user32 is None or kernel32 is None:
        return None
    import ctypes
    _bind(ctypes, user32, kernel32)
    if not _locked_open(user32, ctypes):
        return None
    text, dibs = None, []
    try:
        if user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            h = user32.GetClipboardData(CF_UNICODETEXT)
            if h:
                raw = _bytes_at(kernel32, ctypes, h)
                text = raw.decode("utf-16-le", "ignore").split("\x00", 1)[0]
        if user32.IsClipboardFormatAvailable(CF_DIB):
            h = user32.GetClipboardData(CF_DIB)
            if h:
                dibs = [b for b in (_bytes_at(kernel32, ctypes, h),) if b]
    except Exception:
        return None
    finally:
        try:
            user32.CloseClipboard()
        except Exception:
            pass
    return text, dibs


def bmp_from_dib(dib: bytes) -> bytes:
    """给 CF_DIB 补上 14 字节 BITMAPFILEHEADER,变成能存的 .bmp。

    像素偏移要跳过调色板:`clrUsed == 0` 且 ≤8bpp 时是 2**bpp 项、每项 4 字节,
    补错偏移量图片会整幅错位(而不是报错,所以这里宁可算准)。
    """
    if len(dib) < DIB_INFOHEADER:
        raise ValueError("剪贴板位图数据太短,读不出尺寸")
    bi_size = int.from_bytes(dib[:4], "little")
    if bi_size != DIB_INFOHEADER:
        raise ValueError(f"暂时只认标准位图头(BITMAPINFOHEADER),遇到 {bi_size} 字节")
    bpp = int.from_bytes(dib[14:16], "little")
    clr_used = int.from_bytes(dib[32:36], "little")
    if clr_used == 0 and bpp <= 8:
        clr_used = 1 << bpp
    offset = 14 + bi_size + 4 * clr_used
    total = 14 + len(dib)
    return b"BM" + struct.pack("<IHHI", total, 0, 0, offset) + dib


def dib_to_file(dib: bytes) -> tuple[bytes, str]:
    """CF_DIB → (字节, 扩展名)。有 Pillow 就转 PNG,没有/转不动就照给 BMP。"""
    bmp = bmp_from_dib(dib)
    try:
        from PIL import Image
        with Image.open(io.BytesIO(bmp)) as im:
            out = io.BytesIO()
            im.save(out, "PNG")
            return out.getvalue(), "png"
    except Exception:
        return bmp, "bmp"


def read_clipboard() -> dict:
    """→ {ok, source, text, files, msg};files 的形状与 add_dropped_attachments
    吃的一模一样({name, b64}),前端拿到直接交回本机落 uploads/,不再经 FileReader。
    """
    got = _read_windows()
    if got is None:
        if sys.platform != "win32":
            return _result(UNAVAILABLE, msg="这个平台读不了系统剪贴板,请用 Ctrl+V")
        return _result(UNAVAILABLE, msg="剪贴板正被别的程序占用,再点一次粘贴")
    text, dibs = got
    if text:
        # 一次粘贴只落一个结果:有文字就打字,不顺手挂上附件(微信复制图片时
        # 两个格式常同时在),也省掉一次根本用不上的位图转换。
        return _result(TEXT, text=text)
    files, skipped = [], []
    for dib in dibs:
        try:
            data, ext = dib_to_file(dib)
        except ValueError as e:
            skipped.append(str(e))
            continue
        if len(data) > MAX_ATTACHMENT_BYTES:
            skipped.append(f"图片太大({len(data) // 1024 // 1024} MB),"
                           f"超过单附件上限,请改用拖拽或直接 Ctrl+V")
            continue
        files.append({"name": f"粘贴图片-{_stamp()}.{ext}",
                      "b64": base64.b64encode(data).decode("ascii")})
    msg = "; ".join(skipped)
    if files:
        return _result(IMAGE, files=files, msg=msg)
    if skipped:
        return _result(UNAVAILABLE, msg=msg)
    return _result(EMPTY, msg="剪贴板里既没有文本也没有图片")


def _result(source: str, text: str = "", files: list | None = None,
            msg: str = "") -> dict:
    return {"ok": source in (TEXT, IMAGE), "source": source,
            "text": text, "files": files or [], "msg": msg}
