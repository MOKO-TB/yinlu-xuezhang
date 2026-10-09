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
    # 写方向(复制图片进剪贴板)。这几条原型与读方向同一个理由:
    # GlobalAlloc / SetClipboardData 返回的是 HGLOBAL 指针,不设 restype 时
    # ctypes 按 32 位 int 截断,64 位进程上拿到半个句柄 —— 后果不是报错而是
    # 粘上一块别的内存里的东西(或直接崩)。
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    kernel32.GlobalFree.restype = ctypes.c_void_p
    user32.EmptyClipboard.restype = ctypes.c_bool
    user32.EmptyClipboard.argtypes = []
    user32.SetClipboardData.restype = ctypes.c_void_p
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]


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


GMEM_MOVEABLE = 0x0002


def png_to_dib(data: bytes) -> bytes:
    """PNG(或任何 Pillow 认得的图)→ CF_DIB 的字节:BITMAPINFOHEADER + 像素。

    CF_DIB 不含那 14 字节 BMP 文件头(读侧 `bmp_from_dib` 就是给它补头的,这里反着来)。

    **刻意选最保守的那一种形状:24bpp、自底向上、biHeight 取正、每行补到 4 字节。**
    理由不是好看:32bpp 带 alpha 与"负 biHeight(自顶向下)"这两种新形状,在
    GDI/WinForms/Office 那一族消费者上支持得不一致 —— 有的直接拒收,粘出来是空的;
    而这类失败**不在我们这一侧报错**(`SetClipboardData` 照样返回成功),用户要到
    几秒后在别的软件里 Ctrl+V 才发现。分享卡片本来就是**不透明**的一张图,
    没有需要保留的 alpha,所以没必要为它冒这个险。
    这一条是 2026-10-09 在本机量不出来时按"取最不会错的那形"定的:那个会话里
    `OpenClipboard` 回 error 5(模块开头那段实测留痕说的就是它),第三方粘不粘得出来
    当场测不到 —— 等能在真桌面上测的那天,测出来若与此不符,以实测为准改回来。
    """
    from PIL import Image          # 运行时依赖里已有;缺了就交给调用方如实降级
    with Image.open(io.BytesIO(data)) as im:
        im = im.convert("RGB")
        w, h = im.size
        stride = ((w * 3) + 3) // 4 * 4
        raw = im.tobytes("raw", "BGR")
        if stride != w * 3:                      # 行尾补零到 4 字节对齐
            pad = b"\x00" * (stride - w * 3)
            raw = b"".join(raw[y * w * 3:(y + 1) * w * 3] + pad for y in range(h))
        else:
            raw = b"".join(raw[y * w * 3:(y + 1) * w * 3] for y in range(h))
        body = b"".join(raw[i:i + stride] for i in range((h - 1) * stride, -1, -stride))
    header = struct.pack("<IiiHHIIiiII", DIB_INFOHEADER, w, h, 1, 24,
                         0, len(body), 0, 0, 0, 0)
    return header + body


def write_image(data: bytes, name: str = "") -> dict:
    """把一张图放进 Windows 剪贴板(CF_DIB)。→ {ok, msg}。

    三条不是显然的规矩:
    1. **SetClipboardData 成功之后那块内存就归系统了**,绝不能再 GlobalFree ——
        free 了会粘成垃圾或直接崩,而且崩在用户去粘贴的那一瞬间,离这里隔了几秒;
       只有失败路径才自己 free。
    2. 打不开剪贴板 / 非 Windows 一律 `ok=False` 带一句人话,前端照实退回
       「图已存到某路径」那条老路 —— 复制失败不该让人以为图没生成。
    3. 先 EmptyClipboard 再写:不清就把新图叠在旧内容后面,粘出来的还是上一张。
    """
    if sys.platform != "win32":
        return {"ok": False, "msg": "这个平台走不了本机剪贴板,图已存在工作区里"}
    try:
        dib = png_to_dib(data)
    except Exception as e:
        return {"ok": False, "msg": f"这张图转不成位图({type(e).__name__})"}
    user32, kernel32 = _windll("user32"), _windll("kernel32")
    if user32 is None or kernel32 is None:
        return {"ok": False, "msg": "拿不到系统剪贴板接口"}
    import ctypes
    _bind(ctypes, user32, kernel32)
    if not _locked_open(user32, ctypes):
        return {"ok": False, "msg": "剪贴板正被别的程序占用,再点一次"}
    handle = None
    try:
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(dib))
        if not handle:
            return {"ok": False, "msg": "剪贴板内存没申请到"}
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return {"ok": False, "msg": "剪贴板内存锁不住"}
        try:
            ctypes.memmove(ptr, dib, len(dib))
        finally:
            kernel32.GlobalUnlock(handle)
        if not user32.EmptyClipboard():
            return {"ok": False, "msg": "清不掉剪贴板里的旧内容,没敢覆盖"}
        if not user32.SetClipboardData(CF_DIB, handle):
            return {"ok": False, "msg": "放进剪贴板失败,图仍在工作区里"}
        handle = None                      # 从这一行起那块内存归系统所有
        return {"ok": True,
                "msg": ("图片已复制" + (f"({name})" if name else "")) + ",直接 Ctrl+V 粘贴"}
    except Exception as e:
        return {"ok": False, "msg": f"复制图片没成功({type(e).__name__})"}
    finally:
        if handle:
            try:
                kernel32.GlobalFree(handle)
            except Exception:
                pass
        try:
            user32.CloseClipboard()
        except Exception:
            pass


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
