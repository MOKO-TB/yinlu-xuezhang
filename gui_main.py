"""引路学长 GUI 入口:pywebview 窗口 + 共享核心引擎。

用法: python gui_main.py
点 X 会先问一句:最小化到托盘(任务继续在后台跑)还是退出应用;
在确认框里勾了「不再询问」以后按偏好直接执行。退出时关掉所有会话与
MCP 子进程,不留后台进程。托盘接管失败的老环境仍退回"点 X 直接退出"。
"""
import os
import time

import webview

from minicode import APP_NAME
from minicode import tray
from minicode import workspace as ws
from minicode.config import ensure_config
from minicode.gui_bridge import create_app
from minicode.paths import app_path, resource_path


def _webview2_controls(window):
    """列出可能挂着 CoreWebView2 的控件对象(不同 pywebview 版本位置不同)。

    实测 pywebview 5.x:window.native(BrowserForm).webview(WebView2);
    旧版在 window.native.browser(EdgeChrome).webview。
    """
    try:
        native = getattr(window, "native", None)
    except Exception:      # 属性本身抛异常(COM 未就绪)也当没有
        return
    browser = getattr(native, "browser", None)
    for obj in (getattr(native, "webview", None),
                getattr(browser, "webview", None),
                getattr(native, "web_view", None)):
        if obj is not None:
            yield obj


def apply_context_menu_settings(window) -> bool:
    """给 WebView2 打开原生右键菜单与编辑快捷键,返回是否设置成功。

    pywebview 只在 debug 模式开这两项(见 platforms/edgechromium.py):
      - AreDefaultContextMenusEnabled:关掉后右键没有任何菜单;
      - AreBrowserAcceleratorKeysEnabled:关掉后 Ctrl+C/V/X/A 全都不响应。
    正式版要的是"能复制粘贴",所以窗口就绪后自己把开关打开。

    注意:CoreWebView2 属性**只能在 UI 线程访问**(后台线程读它会抛
    "can only be accessed from the UI thread"),所以这里用控件的
    Invoke 把设置动作切回 UI 线程执行。
    """
    for view in _webview2_controls(window):
        state = {"ok": False}

        def _apply():
            core = getattr(view, "CoreWebView2", None)
            if core is None:
                return
            settings = core.Settings
            settings.AreDefaultContextMenusEnabled = True     # 右键:剪切/复制/粘贴/全选
            settings.AreBrowserAcceleratorKeysEnabled = True  # Ctrl+C / Ctrl+V / Ctrl+A
            state["ok"] = True

        try:
            invoke = getattr(view, "Invoke", None)
            if callable(invoke):
                try:
                    from System import Action
                    invoke(Action(_apply))      # 切到 UI 线程执行
                except Exception:
                    invoke(_apply)              # 无 CLR 环境/可自动转换时直接传
            else:                               # 非 WinForms 后端:直接试
                _apply()
        except Exception:
            continue
        if state["ok"]:
            return True
    return False


def _log_warning(text: str) -> None:
    try:
        with open(app_path("gui_warnings.log"), "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
    except Exception:
        pass


def _enable_native_context_menu() -> None:
    """在 GUI 线程启动后轮询窗口,把右键菜单/快捷键开关打开(最多等 10 秒)。

    成功后再复查一次:pywebview 自己的 on_webview_ready 回调与本函数存在极小的
    竞态窗口,晚 1.5 秒重设一遍(幂等)最稳妥。
    """
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            windows = getattr(webview, "windows", None) or []
            if windows and apply_context_menu_settings(windows[0]):
                time.sleep(1.5)
                try:
                    apply_context_menu_settings(windows[0])
                except Exception:
                    pass
                if os.environ.get("YINLUXS_CTX_DEBUG"):
                    _log_warning("原生右键菜单与编辑快捷键已开启(验证标记)")
                return
        except Exception as e:      # 平台差异/属性变更都不该影响正常使用
            _log_warning(f"打开原生右键菜单失败: {type(e).__name__}: {e}")
            return
        time.sleep(0.25)
    _log_warning("等待 WebView2 就绪超时,原生右键菜单未开启(不影响其他功能)")


def install_tray(ui) -> bool:
    """窗口原生对象就绪后接管关闭动作:点 X 变成"最小化到托盘 / 退出应用"。

    与右键菜单同一个套路 —— `window.native`(BrowserForm)要等 WebView2 起来
    才有,所以这里也轮询最多 10 秒。接管失败就退回老行为(X 直接退出),
    只在 tray.log 留一行原因,绝不能因为托盘起不来而让窗口打不开。
    """
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            windows = getattr(webview, "windows", None) or []
            if windows and tray.install(windows[0], ui) is not None:
                return True
        except Exception as e:
            tray.log(f"接管关窗失败: {type(e).__name__}: {e}")
            return False
        time.sleep(0.25)
    tray.log("等待窗口就绪超时,未接管关闭动作(X 仍等于退出)")
    return False


def _reveal_window() -> None:
    """兜底:本该进托盘却起不来托盘 → 把窗口显示出来。

    "进程在跑、界面永远叫不出来"比开机多一个窗口糟得多,所以宁显不藏。
    """
    try:
        windows = getattr(webview, "windows", None) or []
        if windows:
            windows[0].show()
    except Exception as e:
        tray.log(f"显示窗口失败: {type(e).__name__}: {e}")


def post_startup(ui) -> None:
    """webview.start 的回调(独立线程):补开右键菜单 + 接管关窗 + 自启进托盘。"""
    _enable_native_context_menu()
    install_tray(ui)
    # 开机自启带 --background:窗口建的时候就是 hidden,这里只验托盘在不在。
    if tray.starts_hidden() and not tray.start_in_tray():
        _reveal_window()


def main() -> None:
    config = ensure_config()  # 未配置也照常开窗,界面内给出警示
    # 工作区:上次选的那个(workspaces.json 里的 current)优先,没有/失效才用
    # 默认工作区(= exe 目录,也就是改造前的写死值)
    workspace = ws.startup_workspace(app_path())
    bridge, ui, agent, _session = create_app(config, workspace)
    # 前端是打包内置资源(_MEIPASS);缺失时在窗口内给出明确错误而不是 404
    frontend = resource_path("frontend", "index.html")
    api = bridge.public_api()  # 门面:只暴露方法,防自动化客户端遍历内部对象
    if frontend.exists():
        window = webview.create_window(
            APP_NAME,
            str(frontend),
            js_api=api,
            width=1280, height=820,
            background_color="#FDF6EC",
            # 开机自启(--background)建窗口时先不显示,等托盘就绪;手动双击照常弹出。
            # 上面那条「前端资源缺失」的分支**永远显示**:报错看不见比多一个窗口更糟。
            hidden=tray.starts_hidden(),
        )
    else:
        window = webview.create_window(
            APP_NAME, html=(
                "<body style='font-family:sans-serif;background:#FDF6EC;color:#3D2C1E;"
                "padding:40px'><h2>⚠ 前端资源缺失</h2>"
                f"<p>未找到: {frontend}</p>"
                "<p>分发包不完整,请重新解压 zip(需包含 _internal/frontend 目录)。</p>"
                "</body>"),
            js_api=api,
            width=1280, height=820,
            background_color="#FDF6EC",
        )
    ui.attach(window)
    # 主动推荐看门狗(官网新通知/校历临近):daemon 线程,延迟 15 秒开跑,
    # 任何异常都在模块里吞掉 —— 这是个锦上添花的功能,绝不阻塞/拖垮窗口
    from minicode import proactive
    proactive.start_loop(bridge)
    proactive.start_class_loop(bridge)   # 课前到点提醒:独立的 5 分钟细轮询

    try:
        # func 在 GUI 线程启动后于独立线程执行:补开 WebView2 右键菜单 + 接管关窗
        webview.start(post_startup, (ui,), debug=False)
    finally:
        # 关掉所有会话与 MCP 子进程,不留孤儿(多会话后台任务也一并停)
        try:
            bridge.close()
        except Exception:
            agent.close()


if __name__ == "__main__":
    main()
