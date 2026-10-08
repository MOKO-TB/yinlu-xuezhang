"""引路学长 GUI 入口:pywebview 窗口 + 共享核心引擎。

用法: python gui_main.py
点 X 会先问一句:最小化到托盘(任务继续在后台跑)还是退出应用;
在确认框里勾了「不再询问」以后按偏好直接执行。退出时关掉所有会话与
MCP 子进程,不留后台进程。托盘接管失败的老环境仍退回"点 X 直接退出"。
"""
import os
import sys
import time

import webview

from minicode import APP_NAME
from minicode import headless
from minicode import tray
from minicode import workspace as ws
from minicode.app_setup import apply_retention
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

        def _apply(view=view, state=state):   # 绑住这一轮的控件(B023)
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
        # 文件名写**字面量**是有意的:个人数据那道闸扫的是 app_path 里的字符串字面量
        # (常量只认同模块内的赋值),换成 tray.WARN_LOG_FILE 就扫不到,这份文件会静默
        # 脱离"写了就必须登记"的管束。两边是否同一个名字由 tests/test_tray.py 钉住。
        path = app_path("gui_warnings.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
        tray.cap_log(path)      # v0.9.66 给两份裸日志补过盖,这份漏了:以前它无限长
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
    # 无边框窗的"最大化让出任务栏"要在窗口起来之后补(实测不设会盖住任务栏,
    # 那时候人连开始菜单都点不到 —— 比不能最大化坏得多)。
    from minicode import window_chrome
    bounds = window_chrome.maximize_bounds(getattr(ui, "window", None))
    if not bounds["ok"] and bounds["msg"]:
        tray.log("最大化范围没设上:" + bounds["msg"])
    # 开机自启带 --background:窗口建的时候就是 hidden,这里只验托盘在不在。
    if tray.starts_hidden() and not tray.start_in_tray():
        _reveal_window()
    _start_activate_listener(ui)
    # 自启指向核对(v0.9.98,§6 #16 剩):重装/挪位置后快捷方式还指旧路径,
    # 勾看着是开、开机却不响。核对要冷启动一次 PowerShell,丢给守护线程。
    import threading
    threading.Thread(target=tray.check_stale_startup, args=(ui,),
                     daemon=True, name="startup-stale-check").start()


def _start_activate_listener(ui) -> None:
    """激活监听(v0.9.72):点通知拉起的第二个进程 POST 过来 → 窗口弹回 +
    落到对应面板。push 从监听线程走 —— 与看门狗线程同一条路(GuiUI.push
    本来就被后台线程用);没有面板落点的激活只弹回窗口,不硬开面板。"""
    def _on_activate(parsed: dict) -> None:
        _reveal_window()
        panel = parsed.get("panel")
        if panel:
            ui.push("open_panel", panel=panel)

    from minicode import activate
    r = activate.start_listener(_on_activate, log=lambda m: tray.log(m))
    if not r.get("ok") and r.get("msg"):
        tray.log(r["msg"])


def guard_headless_flags(argv: list[str]) -> int | None:
    """图形版/单文件版收到无头旗标就**当场说清该用哪只 exe**,返回退出码。

    以前 `引路学长.exe --json "…"` 是**静默开窗**:同一句话在 CLI 上回一份 JSON 加
    六个语义化退出码,在这里却是一个永不退出的窗口 —— 脚本作者只会判断成"卡住了",
    而这恰好和"任务没做成"长得一模一样(与 v0.9.120 那条 stdout 契约同一个坏法)。
    参数层面就拒,绝不假装"开了窗口也算跑了"。判定只写在 `headless.headless_flags_in`
    一处,免得两个入口各认一套旗标写法。
    """
    flags = headless.headless_flags_in(argv)
    if not flags:
        return None
    print(f"{APP_NAME} 的图形版与单文件版不跑无头模式(收到 {' '.join(flags)})。\n"
          "   一次命令跑完就退出、输出给机器读的那条路只在 **引路学长-CLI.exe** 上:\n"
          '     引路学长-CLI.exe "你的任务" --json\n'
          "   想打开图形界面,请不带这些旗标直接运行本程序。", file=sys.stderr)
    return headless.EXIT_ARGS


def main() -> None:
    rc = guard_headless_flags(sys.argv[1:])
    if rc is not None:
        sys.exit(rc)
    # 通知点击直达(v0.9.72):--activate 由协议命令拉起 —— 先试着交给在跑的
    # 实例(送达即退,绝不双开);没人在跑才自己启动,并把落点带进首屏
    # (get_info 一次性取走,前端 boot 完成后落面板)。
    from minicode import activate
    act_uri = activate.pending_uri_arg()
    if act_uri is not None:
        if activate.handoff(act_uri):
            return
        activate.set_pending_panel(activate.parse_uri(act_uri).get("panel"))
    config = ensure_config()  # 未配置也照常开窗,界面内给出警示
    # 子进程起不来时**不许**弹到用户脸上:git.exe / bash 这类控制台子进程在桌面版里
    # 初始化失败时,Windows 会弹一个模态的「应用程序无法正常启动(0xc0000142)」框 ——
    # 点掉之前整个窗口卡住,而且用户报的是"每次打开任务监控都来一次"。错误模式是
    # **进程级、子进程继承**的,所以必须在起任何子进程(MCP 服务器在下面那一步)之前设。
    from minicode import window_chrome
    quiet = window_chrome.silence_child_error_dialogs()
    if not quiet["ok"] and quiet["msg"]:
        tray.log("静默子进程错误框没做成:" + quiet["msg"])
    # 工作区:上次选的那个(workspaces.json 里的 current)优先,没有/失效才用
    # 默认工作区(= exe 目录,也就是改造前的写死值)
    workspace = ws.startup_workspace(app_path())
    bridge, ui, agent, _session = create_app(config, workspace)
    # 留档保留策略(v0.9.66 写了函数与测试,却从没接上启动路径;这里与 CLI 同一个函数,
    # 说的也是同一句话)。清理只在启动做一次,跑着的时候绝不动用户的留档。
    apply_retention(config, ui, current=bridge.session.log_path)
    # 前端是打包内置资源(_MEIPASS);缺失时在窗口内给出明确错误而不是 404
    frontend = resource_path("frontend", "index.html")
    api = bridge.public_api()  # 门面:只暴露方法,防自动化客户端遍历内部对象
    # 标题栏写"此刻在哪个工作区"(p2):左栏顶部已经有「引路学长」了,顶上再写一遍
    # 只是重复。用与 `get_info()["workspace_name"]` 同一条 `ws.display_name` 口径,
    # 开机那一闪和之后桥里改的字才是同一个名字。
    title = ws.display_name(workspace, app_path())
    if frontend.exists():
        window = webview.create_window(
            title,
            str(frontend),
            js_api=api,
            width=1280, height=820,
            background_color="#FDF6EC",
            # 无边框(p5/p6):系统那条标题栏整个不要了 —— 用户报的"深色模式顶上还是
            # 一条白的"由此根除(实测 frameless 后客户区铺满整窗,origin_offset 恒 [0,0])。
            # 代价有两条,都已在界面上补票:①系统不给可拖的边带(实测 WM_NCHITTEST 一律
            # HTCLIENT),所以拉边缩放由界面边缘发起(win_resize);②拖标题栏靠
            # .pywebview-drag-region(easy_drag=False:否则整窗任何一处按下都能拖,
            # 选中文字会变成挪窗口)。
            frameless=True,
            easy_drag=False,
            shadow=False,
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
    # 托盘菜单里那叠「最近会话」的取法交给托盘(v0.9.114,图 p8/p9):
    # 托盘不持有桥,只拿这一个 callable;存模块级是因为桥比托盘先起来(见 tray.set_recent_provider)。
    tray.set_recent_provider(bridge.recent_for_tray)
    # 主动推荐看门狗(官网新通知/校历临近):daemon 线程,延迟 15 秒开跑,
    # 任何异常都在模块里吞掉 —— 这是个锦上添花的功能,绝不阻塞/拖垮窗口
    from minicode import proactive
    proactive.start_loop(bridge)
    proactive.start_tick_loop(bridge)    # 到点提醒:课前 + 待办到点,同一条 5 分钟细轮询

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
