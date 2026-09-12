"""GUI 诊断启动器:记录 js_api 调用与事件推送,定位无响应问题。

用法: python scripts/debug_gui.py  (日志写 gui_debug.log)
"""
import functools
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import webview  # noqa: E402

from minicode import gui_bridge  # noqa: E402
from minicode.config import ensure_config  # noqa: E402
from minicode.paths import app_path  # noqa: E402

LOG = open(ROOT / "gui_debug.log", "a", encoding="utf-8")


def log(msg: str) -> None:
    LOG.write(msg + "\n")
    LOG.flush()


def main() -> None:
    config = ensure_config()
    bridge, ui, agent, session = gui_bridge.create_app(config, app_path())

    for name in ("get_info", "refresh_sessions", "send_task", "confirm_reply",
                 "new_task", "undo", "stop_task"):
        orig = getattr(bridge, name)

        @functools.wraps(orig)
        def wrapped(*a, _orig=orig, _name=name, **k):
            log(f"[bridge] {_name} 调入")
            r = _orig(*a, **k)
            log(f"[bridge] {_name} 完成")
            return r
        setattr(bridge, name, wrapped)

    orig_push = ui.push

    @functools.wraps(orig_push)
    def push_wrapped(type_, **k):
        log(f"[push] {type_}")
        return orig_push(type_, **k)
    ui.push = push_wrapped

    log("[gui] create_window 前")
    window = webview.create_window(
        "Gcode", str(app_path("frontend", "index.html")),
        js_api=bridge.public_api(), width=1280, height=820,
        background_color="#FDF6EC",
    )
    ui.attach(window)
    log("[gui] webview.start 前")
    webview.start()
    log("[gui] ended")


if __name__ == "__main__":
    main()
