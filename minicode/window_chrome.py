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
加上 DWMWA_USE_IMMERSIVE_DARK_MODE(20) —— 那是 Win10 1903+ 唯一能让标题栏变暗的开关。

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

S_OK = 0
DWMWA_NAMES = {IMMERSIVE_DARK_MODE: "immersive-dark-mode",
               BORDER_COLOR: "border", CAPTION_COLOR: "caption",
               TEXT_COLOR: "text"}


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
                return int(handle)
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
