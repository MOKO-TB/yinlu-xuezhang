"""GUI 入口辅助函数:WebView2 原生右键菜单与编辑快捷键开关,以及无头旗标那道闸。"""
import sys

import pytest
from gui_main import apply_context_menu_settings, guard_headless_flags


class _Settings:
    def __init__(self):
        self.AreDefaultContextMenusEnabled = False
        self.AreBrowserAcceleratorKeysEnabled = False


class _Core:
    def __init__(self):
        self.Settings = _Settings()


class _View:
    def __init__(self):
        self.CoreWebView2 = _Core()


class _Browser:
    def __init__(self):
        self.webview = _View()


class _Native:
    def __init__(self):
        self.browser = _Browser()


class _Window:
    def __init__(self):
        self.native = _Native()


def test_apply_context_menu_settings_enables_both_switches():
    """pywebview 正式版默认关掉右键菜单和 Ctrl+C/V,这里必须都打开。"""
    window = _Window()
    assert apply_context_menu_settings(window) is True
    settings = window.native.browser.webview.CoreWebView2.Settings
    assert settings.AreDefaultContextMenusEnabled is True
    assert settings.AreBrowserAcceleratorKeysEnabled is True


def test_apply_context_menu_settings_without_webview_is_safe():
    class Empty:
        pass

    class NoNative:
        native = None

    assert apply_context_menu_settings(Empty()) is False
    assert apply_context_menu_settings(NoNative()) is False


def test_apply_context_menu_settings_swallows_errors():
    """属性链里抛异常(COM 未就绪等)时返回 False,不能把 GUI 线程带崩。"""
    class Boom:
        @property
        def native(self):
            raise RuntimeError("COM not ready")

    assert apply_context_menu_settings(Boom()) is False


def test_apply_context_menu_settings_on_native_webview_shape():
    """pywebview 5.x 的形态:window.native(BrowserForm).webview.CoreWebView2。"""

    class Window:
        class native:
            webview = _View()

    assert apply_context_menu_settings(Window()) is True
    assert Window.native.webview.CoreWebView2.Settings.AreDefaultContextMenusEnabled is True


def test_apply_context_menu_settings_uses_ui_thread_invoke():
    """有 Invoke 时必须走它(UI 线程),否则 WebView2 会拒绝跨线程访问。"""

    class View:
        def __init__(self):
            self.CoreWebView2 = _Core()
            self.invoked = False

        def Invoke(self, action):
            self.invoked = True
            action()

    class Native:
        def __init__(self):
            self.webview = View()

    class Window:
        native = Native()

    assert apply_context_menu_settings(Window()) is True
    assert Window.native.webview.invoked is True
    assert Window.native.webview.CoreWebView2.Settings.AreDefaultContextMenusEnabled is True


# ------------------------------------------------ 无头旗标在图形入口必须说出口
# 同一句话在 引路学长-CLI.exe 上是「一份 JSON + 六个语义化退出码」,在图形版与
# 单文件版上以前是**静默开一个永不退出的窗口** —— 脚本作者只会判断成"卡住了",
# 而这和"任务没做成"长得一模一样。这一族针钉的是"参数层面就拒,并且指出该用哪只"。

@pytest.mark.parametrize("argv", [
    ["--json", "做个表"],
    ["--output-format", "json", "做个表"],
    ["--output-format=bogus", "做个表"],      # 拼错的值也一样拦,不静默退回窗口
])
def test_guard_returns_the_args_exit_code(argv):
    assert guard_headless_flags(argv) == 2


def test_guard_silent_on_arguments_it_does_not_own(capsys):
    """托盘/协议激活那些旗标不归它管:误拦会让双击打开的同学变成"程序没反应"。"""
    assert guard_headless_flags([]) is None
    assert guard_headless_flags(["--background"]) is None
    assert guard_headless_flags(["--activate", "yinlu://open?panel=jobs"]) is None
    assert capsys.readouterr() == ("", "")     # 一个字的多余输出都不许有


def test_guard_points_at_the_cli_exe_on_stderr(capsys):
    assert guard_headless_flags(["--json", "x"]) == 2
    out, err = capsys.readouterr()
    assert out == ""                            # 诊断是 stderr 的,stdout 留给数据
    assert "引路学长-CLI.exe" in err and "--json" in err


def test_main_refuses_before_any_window_is_created(monkeypatch, capsys):
    """入口接线针:拦截必须发生在**开窗口之前**。桩里一被调用就直接红 ——
    真窗口在 CI 上一开就没人关得掉,所以这条同时也是环境安全针。"""
    import gui_main

    def _boom(*a, **k):
        raise AssertionError("开窗口了 —— 旗标没在最早那一步被拦住")

    monkeypatch.setattr(gui_main.webview, "create_window", _boom)
    monkeypatch.setattr(gui_main.webview, "start", _boom)
    monkeypatch.setattr(sys, "argv", ["引路学长.exe", "--json", "做个表"])
    with pytest.raises(SystemExit) as got:
        gui_main.main()
    assert got.value.code == 2
    assert "引路学长-CLI.exe" in capsys.readouterr().err
