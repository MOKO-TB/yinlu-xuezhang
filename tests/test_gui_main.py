"""GUI 入口辅助函数:WebView2 原生右键菜单与编辑快捷键开关。"""
from gui_main import apply_context_menu_settings


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
