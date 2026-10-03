"""v0.9.39 p2:系统标题栏跟着主题走,那行字写的是当前工作区。

v0.9.112 补一条:除了 SDK 那四条,还要写 pywebview 自己每回都在写的
DWMWA 38 —— 它按**系统**主题取值,不跟着应用主题走就会和我们的三色打架。

拍板的方向是**保留系统标题栏,只改色与文字**(不做无边框):窗口仍是系统在画,
拖拽/双击最大化/Win11 的圆角投影都归它管。这里钉的是"改的那两样"到底改了没有:

1. 三色是界面上那层玻璃算出来的,不是另调一套 —— 每个常数的来源字符串必须
   还在 `frontend/index.html` 里(CSS 改了色而这里没跟,测试红,不会"界面换了
   标题栏还是老色");
2. `apply()` 逐项报告系统接受/拒绝了哪几项:Win10 只认深色模式那一条,拒绝
   要在返回值里看得见,不能静默"成功";
3. 非 Windows / 窗口没起来 / 老系统 = 降级,绝不抛异常(它在前端那个
   try/catch 后面,抛了只会变成"点了没反应");
4. 标题用的是顶栏那颗 📁 胶囊同一份口径(`ws.display_name`),开机建窗口时
   就已经是这个名字,不会再闪一下「引路学长」;
5. 网页版不能拿它去染宿主的标题栏。
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from minicode import window_chrome as wc  # noqa: E402

HTML = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")


# --------------------------------------------------------------------- 算色


def test_blend_is_the_css_alpha_compositing():
    """rgba(252,253,251,.62) 叠在 #DCE8E4 上 = 240,245,242(手算一遍钉住公式)。"""
    assert wc.blend((252, 253, 251), .62, (220, 232, 228)) == (240, 245, 242)
    assert wc.blend((10, 20, 30), 1.0, (200, 200, 200)) == (10, 20, 30)
    assert wc.blend((10, 20, 30), 0.0, (200, 200, 200)) == (200, 200, 200)


def test_colorref_is_bgr_not_rgb():
    """DWM 要 0x00BBGGRR:写成 RGB 会得到同一种"很像但不对"的颜色,没人报错。"""
    assert wc.colorref((0x11, 0x22, 0x33)) == 0x00332211
    assert wc.colorref((234, 244, 243)) == 0xF3F4EA   # #EAF4F3 → 红蓝换位


def test_chrome_three_colors_are_derived_from_the_css_constants():
    c = wc.chrome_for(False)
    caption = wc.blend(wc.LIGHT_GLASS, wc.GLASS_ALPHA[False], wc.LIGHT_BASE)
    border = wc.blend(wc.LIGHT_LINE, wc.LINE_ALPHA[False], caption)
    assert c.caption == wc.colorref(caption)
    assert c.border == wc.colorref(border)
    assert c.text == wc.colorref(wc.TEXT_RGB[False])
    assert c.dark is False


def test_dark_chrome_is_darker_than_light_chrome():
    """不是要它多准,是要它别调反:深色标题栏压暗,浅色提亮。"""
    lum = lambda n: 0.299 * (n & 0xFF) + 0.587 * ((n >> 8) & 0xFF) + 0.114 * ((n >> 16) & 0xFF)
    dark, light = wc.chrome_for(True), wc.chrome_for(False)
    assert lum(dark.caption) < 60 < 150 < lum(light.caption)
    # 文字与底色必须反向,否则那行字看不见(深浅恰好都踩过这个坑的那种错)
    assert lum(dark.text) - lum(dark.caption) > 100
    assert lum(light.text) - lum(light.caption) < -100
    assert dark.caption != light.caption


# ------------------------------------------------------------- 防漂:来源核对


@pytest.mark.parametrize("token", [
    "--bg:#DCE8E4",                          # LIGHT_BASE
    "rgba(252,253,251,.62)",                 # LIGHT_GLASS / GLASS_ALPHA[False]
    "rgba(40,80,72,.14)",                    # LIGHT_LINE  / LINE_ALPHA[False]
    "#1C2B28",                               # TEXT_RGB[False]
    "rgba(13,22,33,.58)",                    # DARK_GLASS  / GLASS_ALPHA[True]
    "rgba(140,220,210,.16)",                 # DARK_LINE   / LINE_ALPHA[True]
    "#EAF4F3",                               # TEXT_RGB[True]
    "rgba(9,16,24,.93)",                     # DARK_BASE(深色没覆盖 --bg,取在用的实底)
])
def test_css_sources_still_in_frontend(token):
    """标题栏每一个数都能追回界面里的一行 CSS;对不上就是有人改了色没改这里。"""
    assert token in HTML, f"CSS 里已经没有 {token},window_chrome 的取色口径要重新对"


def test_no_magic_constants_left_the_css_side_of_the_blender():
    """四个取色常数必须真的被 chrome_for 用掉,不能只是摆在那儿好看。"""
    src = Path(wc.__file__).read_text(encoding="utf-8")
    body = src[src.index("def chrome_for"):]
    for name in ("LIGHT_GLASS", "LIGHT_BASE", "LIGHT_LINE", "DARK_GLASS",
                 "DARK_BASE", "DARK_LINE", "TEXT_RGB", "GLASS_ALPHA", "LINE_ALPHA"):
        assert name in body, f"chrome_for 没有用到 {name} —— 它已经不参与算色了"


# ------------------------------------------------------------------ 降级路径


class _FakeWindow:
    def __init__(self, handle):
        self.native = types.SimpleNamespace(Handle=handle)


class _BoomWindow:
    @property
    def native(self):
        raise RuntimeError("COM 还没起来")


def test_hwnd_of_tolerates_windows_that_are_not_ready():
    assert wc._hwnd_of(_FakeWindow(1234)) == 1234
    assert wc._hwnd_of(_FakeWindow(None)) == 0
    assert wc._hwnd_of(types.SimpleNamespace()) == 0     # 没有 native
    assert wc._hwnd_of(_BoomWindow()) == 0               # 读属性就炸
    assert wc._hwnd_of(None) == 0


def test_apply_without_a_window_says_so_instead_of_crashing():
    got = wc.apply(None, dark=True, title="campus")
    assert got == {"ok": False, "applied": [], "failed": [],
                   "msg": "窗口还没就绪,标题栏暂不改"}


def test_apply_on_a_machine_without_windll_degrades(monkeypatch):
    """非 Windows(ctypes 根本没有 windll)也不能抛:它在桥线程上。"""
    monkeypatch.setattr(wc, "ctypes", types.SimpleNamespace())
    got = wc.apply(_FakeWindow(7), dark=True, title="campus")
    assert got["ok"] is False and not got["applied"]
    assert set(got["failed"]) == {"immersive-dark-mode", "titlebar-mode",
                                  "border", "caption", "text", "title"}


# ------------------------------------------------------------- apply 报账口径


@pytest.fixture
def recorder(monkeypatch):
    """把两个 Win32 调用换成记账的假件,顺带按需扮演"系统只接受某几项"。"""
    calls = {"attr": [], "text": [], "accept": {20, 38, 34, 35, 36},
             "title_ok": True}

    def _set_attr(hwnd, attr, value):
        calls["attr"].append((hwnd, attr, value))
        return 0 if attr in calls["accept"] else -1

    def _set_text(hwnd, title):
        calls["text"].append((hwnd, title))
        return calls["title_ok"]

    monkeypatch.setattr(wc, "_set_attr", _set_attr)
    monkeypatch.setattr(wc, "_set_text", _set_text)
    return calls


def _failed(got):
    return set(got["failed"])


def test_apply_sends_every_attribute_it_claims_to(recorder):
    got = wc.apply(_FakeWindow(99), dark=True, title="campus")
    # 38 是 pywebview 自己在 winforms 后端每回都写的那一条(按**系统**主题)。
    # 我们不写它 = 系统浅色时它把标题栏按亮色配一次,和 caption/text/border 打架
    # —— 用户看图 p4「深色界面顶上还是白的」有这一份成因。写它不是抢跑,是同一套值。
    assert [a for _, a, _ in recorder["attr"]] == [20, 38, 34, 35, 36]
    assert [h for h, _, _ in recorder["attr"]] == [99] * 5
    c = wc.chrome_for(True)
    assert recorder["attr"][0][2] == 1                 # immersive dark = 1
    assert recorder["attr"][1][2] == wc.TITLEBAR_DARK  # 38:暗 = 2
    assert recorder["attr"][2][2] == c.border
    assert recorder["attr"][3][2] == c.caption
    assert recorder["attr"][4][2] == c.text
    assert recorder["text"] == [(99, "campus")]
    assert got["ok"] and _failed(got) == set()
    assert set(got["applied"]) == {"immersive-dark-mode", "titlebar-mode",
                                   "border", "caption", "text", "title"}


def test_the_light_theme_writes_the_light_value(recorder):
    """浅色要写 1,不是"没写"。留着 pywebview 那句按系统主题的 1 也算对,
    但应用切到浅色而系统仍是深色时,不写就会顶着一块深色标题栏。"""
    wc.apply(_FakeWindow(7), dark=False, title="x")
    got = dict((a, v) for _, a, v in recorder["attr"])
    assert got[wc.TITLEBAR_MODE] == wc.TITLEBAR_LIGHT
    assert got[wc.IMMERSIVE_DARK_MODE] == 0


def test_win10_refusal_is_reported_not_hidden(recorder):
    """Win10 只认 20:另外三条被拒得在 msg 里看得见,别对用户说"改好了"。"""
    recorder["accept"] = {20}
    got = wc.apply(_FakeWindow(1), dark=True, title="campus")
    assert got["ok"] is False
    assert _failed(got) == {"titlebar-mode", "border", "caption", "text"}
    assert "immersive-dark-mode" in got["applied"]
    for word in ("border", "caption", "text"):
        assert word in got["msg"]
    assert "界面本身不受影响" in got["msg"]


def test_title_can_be_the_only_thing_asked_for(recorder):
    """切工作区但主题没报过:只改字,不猜颜色(猜错就是明暗颠倒那一眼)。"""
    got = wc.apply(_FakeWindow(1), title="实验室")
    assert recorder["attr"] == []
    assert recorder["text"] == [(1, "实验室")]
    assert got["applied"] == ["title"] and got["ok"]


def test_failed_title_reaches_the_report(recorder):
    recorder["title_ok"] = False
    got = wc.apply(_FakeWindow(1), title="实验室")
    assert got["ok"] is False and _failed(got) == {"title"}


# --------------------------------------------------------------------- 接线


def _bridge_src() -> str:
    return (ROOT / "minicode" / "gui_bridge.py").read_text(encoding="utf-8")


def test_titlebar_and_topbar_use_the_same_workspace_name():
    """标题栏那行字与 `get_info()["workspace_name"]` 必须是同一个调用。

    写的是「引路学长 · 当前工作区」:用户看图 p3 点名深色界面左上角那个名字没了,
    拍板要"引路学长 + 当前工作区"。工作区名这一段**必须**与顶栏胶囊同一个
    `ws.display_name(...)`,两边各写一套迟早对不上。
    """
    src = _bridge_src()
    assert '"workspace_name": ws.display_name(self.workspace, self.app_dir)' in src
    assert 'title=f"{APP_NAME} · {ws.display_name(self.workspace, self.app_dir)}"' in src


def test_switching_workspace_retitles_the_titlebar():
    """切工作区时标题栏跟着换(否则顶上写着 A 项目、界面在 B 项目里干活)。"""
    src = _bridge_src()
    body = src[src.index("def _apply_workspace"):]
    body = body[:body.index("\n    def ")]
    assert "self._apply_chrome()" in body, "切工作区没重涂标题栏"


def test_facade_forwards_set_chrome():
    import inspect

    from minicode.gui_bridge import PublicApi

    class Recorder:
        def set_chrome(self, dark):
            return {"ok": True, "dark": dark}

    assert PublicApi(Recorder()).set_chrome(True) == {"ok": True, "dark": True}
    assert list(inspect.signature(PublicApi.set_chrome).parameters) == ["self", "dark"]


def test_web_viewers_cannot_repaint_the_hosts_titlebar():
    from minicode.web_profile import PublicFacade

    assert "set_chrome" not in PublicFacade(object()).allowed_methods()


def test_frontend_reports_every_theme_change_to_the_bridge():
    """开机那一次也要报:否则深色界面配一条白标题栏,正是用户点名的那一眼。"""
    body = HTML[HTML.index("function applyTheme"):]
    body = body[:body.index("\n}\n")]
    assert "pywebview.api.set_chrome(dark)" in body
    # 桥不在(网页版)时不能因为这句报错把主题切换整个打断
    assert "catch" in body


def test_startup_window_opens_with_the_workspace_name():
    """建窗口时就写工作区名:先闪一下「引路学长」再换,和左栏那行重复。"""
    src = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    assert "title = ws.display_name(workspace, app_path())" in src
    start = src.index("if frontend.exists():")
    first = src[start:src.index("else:", start)]
    assert "webview.create_window(\n            title," in first
    assert "APP_NAME," not in first
    # 前端资源缺失那条分支仍然写应用名:包坏了要一眼知道是谁坏了
    assert "APP_NAME, html=(" in src


def test_frozen_targets_include_the_chrome_module():
    """漏收 = 装好的包里桥那句 import 炸掉,而它被前端的 catch 吞了 = 改了个寂寞。"""
    text = (ROOT / "scripts" / "check_frozen_version.py").read_text(encoding="utf-8")
    assert "minicode.window_chrome" in text
    assert '"set_chrome"' in text
