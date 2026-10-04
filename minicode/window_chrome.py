"""系统标题栏:跟着主题染色,写着当前工作区名(p2)。

用户看图 p2 点名「第二个应用的最上面那一行与应用界面不符合」—— 深色界面顶上顶着
一条 Windows 默认的白标题栏,而且那行字写着「引路学长」,和左栏顶部那个「引路学长」
重复。拍板的方向是**保留系统标题栏,只改色与文字**(不做无边框):标题栏仍是系统
在画(拖拽、双击最大化、Win11 的圆角与投影都归系统管),我们只把颜色与它对齐,
文字换成"此刻在哪个工作区"。

**本机实测留痕(2026-09-24)**:开发机是 Windows 11 build 26100(24H2),这三条
属性按 SDK 都支持;但**这一台的实际接受情况没有验到** —— 智能体所在的 shell 挂在
非交互桌面站上,临时探针连 `RegisterClassW`/`CreateWindowExW` 都没返回(45 秒后被
杀掉),和同一天剪贴板 `OpenClipboard` 拿到 `GetLastError()=5` 是同一类限制。
所以 `apply()` 不返回"成功/失败"一个布尔,而是逐项报告哪几条被系统接了:
真机上跑一次,`msg` 里会自己说清楚(测试 `test_win10_refusal_is_reported_not_hidden`
钉的就是"被拒必须看得见"这条口径)。

三个 Windows 11 属性(Win10 不支持,拿不到就如实报告,只退回"深色模式"那一条):
  DWMWA_CAPTION_COLOR(35)  标题栏底色
  DWMWA_TEXT_COLOR(36)     标题文字
  DWMWA_BORDER_COLOR(34)   窗口描边
加上 DWMWA_USE_IMMERSIVE_DARK_MODE(20) —— 那是 Win10 1903+ 唯一能让标题栏变暗的开关;
再加 **38**:pywebview 自己在 winforms 后端每次都会按**系统**主题写它
(`winforms.py:333-341`,暗 2 / 亮 1,系统主题变了还会再写一次)。我们不写它,
它就一直按系统主题配 —— 应用深色、系统浅色时那三条颜色会被它压回去,
这就是用户看图 p4「深色界面顶上还是白的」的一份成因。v0.9.112 起跟着**应用**主题写。

底色不是另起一套:全部从 `frontend/index.html` 的 CSS 变量算出来 —— 标题栏就是
界面上那层 `--glass` 玻璃叠在页面底色上,描边是 `--line` 再叠一层;深色没有覆盖
`--bg`(页面靠暮色航拍照片铺底),所以它的底色取深色界面里真正在用的那块实底
`rgba(9,16,24,.93)`。`tests/test_window_chrome.py` 钉着这几个来源字符串 —— CSS 改了
而这里没跟着改,测试会红,不会出现"界面换了色、标题栏还是老色"。
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass

# --- DWMWA_* (Windows 11 SDK: dwmapi.h) -----------------------------------
IMMERSIVE_DARK_MODE = 20
BORDER_COLOR = 34
CAPTION_COLOR = 35
TEXT_COLOR = 36
# 38 不是 SDK 里那四条之一,但 **pywebview 自己在 winforms 后端每次都会写它**
# (`.venv/.../webview/platforms/winforms.py:333-341`:暗 2 / 亮 1,而且
# `on_system_theme_changed` 会再写一次)。它按的是**系统**主题,不是本应用的主题 ——
# 我们从不写这一条,于是系统处在浅色时它就把标题栏按亮色配一次,和我们设的
# caption/text/border 三家打架。用户看图 p4 说的「深色界面顶上还是白的」有这一份成因。
# 修法不是抢在 pywebview 之后跑,而是**跟着应用主题写同一个属性**:两个写家写同一套值。
TITLEBAR_MODE = 38
TITLEBAR_LIGHT = 1
TITLEBAR_DARK = 2

S_OK = 0
DWMWA_NAMES = {IMMERSIVE_DARK_MODE: "immersive-dark-mode",
               BORDER_COLOR: "border", CAPTION_COLOR: "caption",
               TEXT_COLOR: "text", TITLEBAR_MODE: "titlebar-mode"}


@dataclass(frozen=True)
class Chrome:
    """一套主题下标题栏该穿的三色(COLORREF:0x00BBGGRR)。"""
    caption: int
    text: int
    border: int
    dark: bool


def blend(fg: tuple[int, int, int], alpha: float,
          base: tuple[int, int, int]) -> tuple[int, int, int]:
    """半透明前景叠在不透明底色上(与 CSS `rgba()` 叠加同一个算法)。"""
    return tuple(round(a * alpha + b * (1 - alpha)) for a, b in zip(fg, base))   # type: ignore[return-value]


def colorref(rgb: tuple[int, int, int]) -> int:
    """RGB → COLORREF(DWM 要的是 0x00BBGGRR,红蓝是反的)。"""
    r, g, b = rgb
    return (b << 16) | (g << 8) | r


# 四个数全都抄自 frontend/index.html 的 CSS 变量(测试 test_css_sources_still_in_frontend
# 逐条钉住这些字符串还在界面里 —— 界面改了色而这里没跟,测试会红):
#   浅色:--bg #DCE8E4 打底,--glass rgba(252,253,251,.62) 是侧栏/顶栏那层玻璃,
#         文字 --text-1 #1C2B28,描边 --line rgba(40,80,72,.14)
LIGHT_BASE = (220, 232, 228)          # #DCE8E4
LIGHT_GLASS = (252, 253, 251)         # rgba(252,253,251,.62)
LIGHT_LINE = (40, 80, 72)             # rgba(40,80,72,.14)
#   深色:深色没有覆盖 --bg(底色仍是浅色,实际靠暮色航拍照片铺满),照片上真正的
#         暗界面用的是消息气泡那块实底 rgba(9,16,24,.93) —— 标题栏要贴的是它;
#         玻璃层 --glass rgba(13,22,33,.58),文字 --text-1 #EAF4F3,
#         描边 --line rgba(140,220,210,.16)
DARK_BASE = (9, 16, 24)               # html.dark .msg.assistant .body
DARK_GLASS = (13, 22, 33)             # rgba(13,22,33,.58)
DARK_LINE = (140, 220, 210)           # rgba(140,220,210,.16)

GLASS_ALPHA = {True: .58, False: .62}
LINE_ALPHA = {True: .16, False: .14}
TEXT_RGB = {True: (234, 244, 243), False: (28, 43, 40)}   # --text-1


def chrome_for(dark: bool) -> Chrome:
    """标题栏三色 = 界面上那层玻璃叠在底色上,再叠一条 --line 当描边。"""
    glass, base = (DARK_GLASS, DARK_BASE) if dark else (LIGHT_GLASS, LIGHT_BASE)
    line = DARK_LINE if dark else LIGHT_LINE
    caption = blend(glass, GLASS_ALPHA[dark], base)
    # 描边不是凭空调暗:就是 CSS 里那条 --line 叠在标题栏底色上的结果
    border = blend(line, LINE_ALPHA[dark], caption)
    return Chrome(colorref(caption), colorref(TEXT_RGB[dark]),
                  colorref(border), dark)


def _dwmapi():
    if getattr(ctypes, "windll", None) is None:
        return None
    try:
        return ctypes.windll.dwmapi
    except Exception:
        return None


# --- 子进程的模态错误框 ----------------------------------------------------
# SEM_FAILCRITICALERRORS(0x1):关掉了「应用程序无法正常启动(0xc0000142)」那一类
#   由加载器/系统弹出来的**模态**框 —— 模态的意思是:用户不点掉,整个程序就停在那儿。
# SEM_NOGPFAULTERRORBOX(0x2):同一形状的崩溃框( Wer 那个「程序已停止工作」)。
# 错误模式是**进程级并被子进程继承**的,所以在开机设一次,后面所有 git.exe / bash
# 起不来的时候都不许再弹到用户脸上 —— 该看得见的是界面里那句实话,不是一个系统框。
SEM_FAILCRITICALERRORS = 0x0001
SEM_NOGPFAULTERRORBOX = 0x0002


def silence_child_error_dialogs() -> dict:
    """开机调用一次:本机若不接受就如实回,绝不影响界面。

    → {ok, mode, msg}。`mode` 是系统返回的**旧**错误模式(能读到就有它,读不到给 -1)。
    """
    if getattr(ctypes, "windll", None) is None:      # 非 Windows(macOS/Linux 开发机)
        return {"ok": False, "mode": -1, "msg": "非 Windows,没有这类错误框"}
    want = SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX
    try:
        prev = ctypes.windll.kernel32.SetErrorMode(want)
        # SetErrorMode 返回旧值;旧值里没带我们要的这两位也算成功(现在带上了)
        return {"ok": True, "mode": int(prev), "msg": ""}
    except Exception as e:
        return {"ok": False, "mode": -1,
                "msg": f"{type(e).__name__}: {str(e)[:80]}"}


def _hwnd_of(window) -> int:
    """pywebview 窗口 → 原生 HWND(拿不到返回 0,调用方据此降级)。

    v0.9.94 兜底:原先只认 `native.Handle` 一条路 —— 但用户真机(图 p1,
    标题栏始终白底)显示这条在部分安装形态下拿不到有效句柄,apply() 如实
    报 failed 却没有任何人看见,染色静默失效。现在按序多找两层
    (`native.Form.Handle`),再不行用 Win32 `FindWindowW` 按**当前标题**
    找(标题正是我们 set 的当前工作区名,找错窗口的概率趋近于零,
    因为这名字是我们自己写上去的)。
    """
    try:
        native = getattr(window, "native", None)
        for obj in (native, getattr(native, "Form", None)):
            handle = getattr(obj, "Handle", None)
            if handle is not None:
                to_i64 = getattr(handle, "ToInt64", None)   # pythonnet 的 IntPtr
                return int(to_i64()) if callable(to_i64) else int(handle)
    except Exception:
        pass
    try:
        title = getattr(window, "title", None)
        if not title:
            return 0
        user32 = ctypes.windll.user32 if getattr(ctypes, "windll", None) else None
        if not user32:
            return 0
        hwnd = user32.FindWindowW(None, str(title))
        return int(hwnd) if hwnd else 0
    except Exception:
        return 0


def _set_attr(hwnd: int, attr: int, value: int) -> int:
    """DwmSetWindowAttribute 的一次调用,返回 HRESULT(0 = 成功)。"""
    dwm = _dwmapi()
    if dwm is None or not hwnd:
        return -1
    buf = ctypes.c_int(value)
    try:
        return dwm.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd), ctypes.c_uint(attr),
            ctypes.byref(buf), ctypes.sizeof(buf))
    except Exception:
        return -1


def _set_text(hwnd: int, title: str) -> bool:
    """改标题栏文字。SetWindowTextW 自己会把 WM_SETTEXT 送到 UI 线程,跨线程安全。"""
    if not hwnd:
        return False
    try:
        return bool(ctypes.windll.user32.SetWindowTextW(
            ctypes.c_void_p(hwnd), ctypes.c_wchar_p(title)))
    except Exception:
        return False


# ---------------------------------------------------------------- 无边框(frameless)那三件补票的事
#
# 2026-10-04 在这台机器上探针实测(不是查文档):
# - `frameless=True` 之后客户区**铺满整窗**(origin_offset 恒为 [0,0])—— 用户要的
#   "深色下顶栏不再白"由此根除:系统那条标题栏压根不存在了;
# - 但 FormBorderStyle=None 之后非客户区高度为 0,WM_NCHITTEST 一律回 HTCLIENT:
#   **系统边缘拖不动了**(打 WS_THICKFRAME 也不给边带,实测 rect 没变),
#   所以"拉边缩放"必须由界面自己发起、Python 侧 SetWindowPos 落实;
# - 不设 MaximizedBounds 时 `maximize()` 会**盖住任务栏**(实测 1920×1080,
#   而工作区是 1920×1032)。设了以后最大化正好 1920×1032 —— 这一条必须补。

SPI_GETWORKAREA = 0x0030
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
MIN_W, MIN_H = 720, 480        # 再小界面就要开始叠罗汉了(顶栏一行都放不下)
EDGES = ("n", "s", "e", "w", "ne", "nw", "se", "sw")


def work_area() -> list[int]:
    """屏幕去掉任务栏的那块 [左,上,右,下](读不到回空列表,调用方据此降级)。"""
    if getattr(ctypes, "windll", None) is None:
        return []
    try:
        from ctypes import wintypes as wt
        rect = wt.RECT()
        if not ctypes.windll.user32.SystemParametersInfoW(
                SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
            return []
        return [int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)]
    except Exception:
        return []


def maximize_bounds(window) -> dict:
    """给无边框的窗补上"最大化让出任务栏"(实测不补就会盖住 —— 这是那条的票)。

    只在拿得到原生 Form 时做;拿不到(网页版/别的后端)如实回 ok=False,不硬来。
    """
    form = getattr(window, "native", None) if window is not None else None
    if form is None:
        return {"ok": False, "msg": "窗口原生对象还没就绪"}
    wa = work_area()
    if not wa:
        return {"ok": False, "msg": "读不到工作区,最大化范围不改"}
    try:
        from System.Drawing import Rectangle
        form.MaximizedBounds = Rectangle(wa[0], wa[1], wa[2] - wa[0], wa[3] - wa[1])
    except Exception as exc:
        return {"ok": False, "msg": f"MaximizedBounds 没设上:{type(exc).__name__}"}
    return {"ok": True, "work_area": wa, "msg": ""}


def window_rect(window) -> list[int]:
    """窗口在屏幕上的 [左,上,右,下](拿不到回 [])—— 边缘缩放以它为尺子。"""
    hwnd = _hwnd_of(window) if window is not None else 0
    if not hwnd or getattr(ctypes, "windll", None) is None:
        return []
    try:
        from ctypes import wintypes as wt
        rect = wt.RECT()
        if not ctypes.windll.user32.GetWindowRect(ctypes.c_void_p(hwnd),
                                                  ctypes.byref(rect)):
            return []
        return [int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)]
    except Exception:
        return []


def _apply_edge(rect, edge: str, dx: int, dy: int,
                min_w: int = MIN_W, min_h: int = MIN_H) -> list[int]:
    """把"拖某条边/角"的位移算成新矩形 —— 纯算术,单独有针。

    撞到最小尺寸时**让被拖的那条边停在够得着的地方**(而不是把对边推走):
    同学往外拖到极限就该停住,往里拖不能把窗口翻过来。
    """
    left, top, right, bottom = [int(v) for v in rect[:4]]
    dx, dy = int(dx), int(dy)
    if "w" in edge:
        left = min(left + dx, right - min_w)
    if "e" in edge:
        right = max(right + dx, left + min_w)
    if "n" in edge:
        top = min(top + dy, bottom - min_h)
    if "s" in edge:
        bottom = max(bottom + dy, top + min_h)
    return [left, top, right, bottom]


def resize_edge(window, edge: str, dx: int, dy: int) -> dict:
    """界面边缘那几条 5px 的把手走这里:增量式(SetWindowPos 一次一动)。

    为什么是增量而不是绝对坐标:JS 那一侧只能可靠地知道"鼠标又走了多少",
    而窗口现在多大归系统说了算 —— 两边各记一份绝对位置迟早对不上,
    对不上的表现是"拖一下窗口跳一下"。
    """
    if edge not in EDGES:
        return {"ok": False, "msg": f"不认识的方向 {edge}(可用:{'、'.join(EDGES)})"}
    rect = window_rect(window)
    if not rect:
        return {"ok": False, "msg": "读不到窗口位置,缩放不动"}
    hwnd = _hwnd_of(window)
    new = _apply_edge(rect, edge, dx, dy)
    try:
        ok = ctypes.windll.user32.SetWindowPos(
            ctypes.c_void_p(hwnd), 0, new[0], new[1],
            new[2] - new[0], new[3] - new[1], SWP_NOZORDER | SWP_NOACTIVATE)
    except Exception as exc:
        return {"ok": False, "msg": f"{type(exc).__name__}"}
    return {"ok": bool(ok), "rect": new, "msg": "" if ok else "SetWindowPos 没成功"}


def is_maximized(window) -> bool:
    """界面据此决定 □ 的悬浮提示是"最大化"还是"还原"(不猜)。"""
    form = getattr(window, "native", None) if window is not None else None
    try:
        return str(getattr(form, "WindowState", "")) == "Maximized"
    except Exception:
        return False


GWL_STYLE = -16
WS_CAPTION = 0x00C00000


def style_of(window) -> int:
    """窗口的 Win32 样式位(读不到回 0)。"""
    hwnd = _hwnd_of(window) if window is not None else 0
    if not hwnd or getattr(ctypes, "windll", None) is None:
        return 0
    try:
        user32 = ctypes.windll.user32
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            user32.GetWindowLongPtrW.restype = ctypes.c_longlong
            return int(user32.GetWindowLongPtrW(ctypes.c_void_p(hwnd), GWL_STYLE)) \
                & 0xFFFFFFFF
        return int(user32.GetWindowLongW(ctypes.c_void_p(hwnd), GWL_STYLE)) & 0xFFFFFFFF
    except Exception:
        return 0


def is_borderless(window) -> bool:
    """这条窗口现在**真的**没有系统标题栏吗(按样式位读,不按配置里的布尔猜)。

    界面要不要摆自绘的最小/最大/关闭三颗,取决于窗口实际长什么样:
    配置写了 frameless 而某个后端没生效时,界面上再摆一套就是"两排关闭按钮"。
    """
    style = style_of(window)
    return bool(style) and not (style & WS_CAPTION)


def apply(window, dark: bool | None = None, title: str | None = None) -> dict:
    """给真窗口染色 + 改字;窗口还没起来就如实说不做什么。

    → {ok, applied:[…], failed:[…], msg}:Win10 上 caption/text/border 三条会被拒,
    那不是失败到看不见 —— 界面仍是深色的,只是标题栏退回系统默认。
    """
    hwnd = _hwnd_of(window) if window is not None else 0
    if not hwnd:
        return {"ok": False, "applied": [], "failed": [],
                "msg": "窗口还没就绪,标题栏暂不改"}
    applied, failed = [], []

    def attempt(label: str, attr: int, value: int) -> None:
        (applied if _set_attr(hwnd, attr, value) == S_OK else failed).append(label)

    if dark is not None:
        c = chrome_for(bool(dark))
        attempt(DWMWA_NAMES[IMMERSIVE_DARK_MODE], IMMERSIVE_DARK_MODE, int(c.dark))
        # 与 pywebview 那句 `DwmSetWindowAttribute(..., 38, 2|1)` 同一把尺子,
        # 只是值跟着**应用**的主题走而不是系统的主题。
        attempt(DWMWA_NAMES[TITLEBAR_MODE], TITLEBAR_MODE,
                TITLEBAR_DARK if c.dark else TITLEBAR_LIGHT)
        attempt(DWMWA_NAMES[BORDER_COLOR], BORDER_COLOR, c.border)
        attempt(DWMWA_NAMES[CAPTION_COLOR], CAPTION_COLOR, c.caption)
        attempt(DWMWA_NAMES[TEXT_COLOR], TEXT_COLOR, c.text)
    if title is not None:
        (applied if _set_text(hwnd, title) else failed).append("title")
    msg = ""
    if failed:
        msg = ("标题栏这几项系统不接受:" + "、".join(failed)
               + "(老 Windows 常见,界面本身不受影响)")
    return {"ok": not failed, "applied": applied, "failed": failed, "msg": msg}
