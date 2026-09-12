"""引路学长 GUI 入口:pywebview 窗口 + 共享核心引擎 + 系统托盘。

用法: python gui_main.py
"""
from __future__ import annotations

import threading

import webview

from minicode import APP_NAME
from minicode.config import ensure_config
from minicode.gui_bridge import create_app
from minicode.paths import app_path, resource_path


def _start_tray(window) -> object | None:
    """系统托盘:右下角图标,菜单 = 显示主窗口 / 退出;失败不阻塞应用。"""
    try:
        import pystray
        from PIL import Image

        image = Image.open(resource_path("assets", "gcode.ico"))

        def on_show(icon, item):
            try:
                window.show()
                window.restore()
            except Exception:
                pass

        def on_quit(icon, item):
            icon.stop()
            window.destroy()

        icon = pystray.Icon(
            "yinlu-xuezhang", image, APP_NAME,
            pystray.Menu(
                pystray.MenuItem("显示主窗口", on_show, default=True),
                pystray.MenuItem("退出", on_quit),
            ),
        )
        try:
            icon.run_detached()      # Windows 支持分离运行
            icon.visible = True
        except Exception:
            threading.Thread(target=icon.run, daemon=True).start()
        return icon
    except Exception:
        return None


def main() -> None:
    config = ensure_config()  # 未配置也照常开窗,界面内给出警示
    workspace = app_path()  # 双击启动时 cwd 即 exe 目录
    bridge, ui, _agent, _session = create_app(config, workspace)

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

    tray = _start_tray(window)
    if tray is not None:
        window.events.closed += tray.stop  # 关窗时同步收起托盘图标

    webview.start(debug=False)


if __name__ == "__main__":
    main()
