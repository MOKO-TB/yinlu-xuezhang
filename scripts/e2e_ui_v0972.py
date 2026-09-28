# -*- coding: utf-8 -*-
"""v0.9.72 前端回归:通知点击直达 —— open_panel 事件与首屏落点。

钉四件事:①推 open_panel 事件(schedule)真的打开课表面板;②切到
mood 打开心情面板;③白名单外的名字(后端 parse 已降级,这里是前端
openPanel 的兜底)不炸也不乱开;④get_info 带的 activate_panel 在 boot
后自动落面板(--activate 自己启动的那条路)。payload 形状照后端真值。

跑法(项目根目录,GBK 控制台必须带 PYTHONIOENCODING=utf-8):
    PYTHONIOENCODING=utf-8 .venv\\Scripts\\python.exe scripts\\e2e_ui_v0972.py
"""
from __future__ import annotations

import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

STUB_JS = """
window.pywebview = {
  api: {
    get_info: () => Promise.resolve({
      version: "0.9.72", app_name: "引路学长",
      permission_mode: "confirm",
      permission_modes: [{value: "confirm", label: "变更前确认"}],
      session_key: "s1", model: "test-model", active_provider: "test",
      providers: [{id: "test", name: "Test", model: "test-model", has_key: true}],
      api_key_configured: true, workspace: "D:/demo/campus",
      workspace_name: "campus", workspace_is_default: true,
      memory_count: 0, memory_user_count: 0, mcp_servers: [],
      activate_panel: "schedule",
      sandbox: {}, vision: false, campus_map: {buildings: []}, library_health: {},
      skills: [], kb_count: 0,
      session_metrics: {ok: false, selectable: []}
    }),
    send_task: () => Promise.resolve({ok: true, session: "s1"}),
    get_schedule_view: () => Promise.resolve({ok: false}),
    set_notice_watch: () => Promise.resolve({ok: true}),
    get_recent_projects: () => Promise.resolve({ok: true, current: {}, default: {}, recent: []}),
    get_reasoning: () => Promise.resolve({visible: false, options: [], choice: "default"}),
    get_suggestions: () => Promise.resolve({suggestions: []}),
    get_countdowns: () => Promise.resolve({ok: true, countdowns: []}),
    get_session_metrics: () => Promise.resolve({ok: false}),
    refresh_sessions: () => Promise.resolve({ok: true}),
    rate_message: () => Promise.resolve({ok: true}),
    compact_now: () => Promise.resolve({ok: true}),
    get_weather: () => Promise.resolve({ok: false}),
    mood_review: () => Promise.resolve({ok: false}),
  }
};
"""

FAILS: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  ✓ " if ok else "  ✗ ") + what)
    if not ok:
        FAILS.append(what)


def _serve(directory: Path):
    class _QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = partial(_QuietHandler, directory=str(directory))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}/index.html", httpd.shutdown


def _launch(p):
    try:
        return p.chromium.launch()
    except Exception:
        import os
        base = os.environ.get("LOCALAPPDATA", "")
        for d in sorted(Path(base, "ms-playwright").glob("chromium-*/chrome-win*/chrome.exe"),
                        reverse=True):
            return p.chromium.launch(executable_path=str(d), headless=True)
        raise


def main() -> int:
    from playwright.sync_api import sync_playwright

    js_errors: list[str] = []
    url, stop = _serve(FRONTEND)
    try:
        with sync_playwright() as p:
            browser = _launch(p)
            page = browser.new_page()
            page.on("pageerror", lambda e: js_errors.append(str(e)))
            page.on("console", lambda m: js_errors.append(m.text)
                    if m.type == "error" and "Failed to load resource" not in m.text
                    else None)
            page.add_init_script(STUB_JS)
            page.goto(url, wait_until="load")
            page.wait_for_function("() => document.getElementById('input') !== null")
            page.wait_for_timeout(250)

            # ---- 1. get_info 带的落点:boot 后自动打开课表面板 ----
            check(page.locator("#scheduleOverlay").evaluate(
                "el => el.classList.contains('show')"),
                "get_info.activate_panel=schedule → boot 后课表面板已打开")

            # 关掉它,验事件路径
            page.click("#btnScheduleClose")
            page.wait_for_timeout(120)
            check(not page.locator("#scheduleOverlay").evaluate(
                "el => el.classList.contains('show')"), "关闭后面板收起")

            # ---- 2. 激活事件:mood 落点 ----
            page.evaluate("e => window.__gcodeEvent(e)",
                          {"type": "open_panel", "panel": "mood"})
            page.wait_for_timeout(150)
            check(page.locator("#moodOverlay").evaluate(
                "el => el.classList.contains('show')"),
                "open_panel mood → 心情面板打开")
            page.click("#btnMoClose")
            page.wait_for_timeout(120)
            check(not page.locator("#moodOverlay").evaluate(
                "el => el.classList.contains('show')"), "心情面板收起")

            # ---- 3. 激活事件:schedule 落点 ----
            page.evaluate("e => window.__gcodeEvent(e)",
                          {"type": "open_panel", "panel": "schedule"})
            page.wait_for_timeout(150)
            check(page.locator("#scheduleOverlay").evaluate(
                "el => el.classList.contains('show')"),
                "open_panel schedule → 课表面板打开(事件路径)")
            page.click("#btnScheduleClose")
            page.wait_for_timeout(120)

            # ---- 3. 白名单外的名字:不炸、不乱开面板 ----
            page.evaluate("e => window.__gcodeEvent(e)",
                          {"type": "open_panel", "panel": "registry"})
            page.wait_for_timeout(150)
            check(not page.locator("#scheduleOverlay").evaluate(
                "el => el.classList.contains('show')")
                and not page.locator("#moodOverlay").evaluate(
                    "el => el.classList.contains('show')"),
                "白名单外的落点安静忽略(后端 parse 其实已降级)")

            # ---- 4. focus(无落点):只弹回窗口,不开任何面板 ----
            page.evaluate("e => window.__gcodeEvent(e)",
                          {"type": "open_panel", "panel": None})
            page.wait_for_timeout(120)
            check(not page.locator("#moodOverlay").evaluate(
                "el => el.classList.contains('show')"), "focus 不开面板")

            check(not js_errors, f"零 JS 错误(实测 {js_errors[:3]})")
            browser.close()
    finally:
        stop()
    print()
    if FAILS:
        print(f"{len(FAILS)} 项没过:")
        for f in FAILS:
            print("  - " + f)
        return 1
    print("全绿 8 项。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
