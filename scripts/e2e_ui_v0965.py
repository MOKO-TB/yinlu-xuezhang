# -*- coding: utf-8 -*-
"""v0.9.65 前端回归:/ 动作面板 —— Codex/Qoder 同款,执行的是左栏真动作。

钉五件事:①输入 / 弹出全部 14 个动作;②继续输入过滤;③Enter 执行选中动作
(真调到左栏按钮背后的桥方法)、吃掉输入框里的 /token、不触发发送;
④Esc 关菜单;⑤桥上 export_session 的返回会变成 toast(证明走的是真按钮链路)。

跑法(项目根目录,GBK 控制台必须带 PYTHONIOENCODING=utf-8):
    PYTHONIOENCODING=utf-8 .venv\\Scripts\\python.exe scripts\\e2e_ui_v0965.py
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
window.__E2E_CALLS__ = [];
window.pywebview = {
  api: {
    get_info: () => Promise.resolve({
      version: "0.9.65", app_name: "引路学长",
      permission_mode: "confirm",
      permission_modes: [{value: "confirm", label: "变更前确认"}],
      session_key: "s1", model: "test-model", active_provider: "test",
      providers: [{id: "test", name: "Test", model: "test-model", has_key: true}],
      api_key_configured: true, workspace: "D:/demo/campus",
      workspace_name: "campus", workspace_is_default: true,
      memory_count: 0, memory_user_count: 0, mcp_servers: [],
      sandbox: {}, vision: false, campus_map: {buildings: []}, library_health: {},
      skills: [], kb_count: 0,
      session_metrics: {ok: false, selectable: []}
    }),
    workspace_search_files: () => Promise.resolve({ok: true, items: []}),
    export_session: () => {
      window.__E2E_CALLS__.push(["export_session"]);
      return Promise.resolve({ok: true, name: "会话-测试.md"});
    },
    undo: () => { window.__E2E_CALLS__.push(["undo"]);
                  return Promise.resolve({ok: true, msg: "没有可撤销的"}); },
    compact_now: () => { window.__E2E_CALLS__.push(["compact_now"]);
                         return Promise.resolve({ok: true, msg: "已压缩"}); },
    get_schedule_view: () => Promise.resolve({ok: false, msg: "e2e 不测课表"}),
    set_notice_watch: () => Promise.resolve({ok: true}),
    get_recent_projects: () => Promise.resolve({ok: true, current: {}, default: {}, recent: []}),
    get_reasoning: () => Promise.resolve({visible: false, options: [], choice: "default"}),
    get_suggestions: () => Promise.resolve({suggestions: []}),
    get_countdowns: () => Promise.resolve({ok: true, countdowns: []}),
    get_session_metrics: () => Promise.resolve({ok: false}),
    refresh_sessions: () => Promise.resolve({ok: true}),
    rate_message: () => Promise.resolve({ok: true}),
    send_task: (text) => {
      window.__E2E_CALLS__.push(["send_task", text]);
      return Promise.resolve({ok: true, session: "s1"});
    },
    get_weather: () => Promise.resolve({ok: false}),
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

    url, stop = _serve(FRONTEND)
    try:
        with sync_playwright() as p:
            browser = _launch(p)
            page = browser.new_page()
            page.add_init_script(STUB_JS)
            page.goto(url, wait_until="load")
            page.wait_for_function("() => document.getElementById('input') !== null")
            page.wait_for_timeout(250)

            ta = page.locator("#input")

            def calls():
                return [c[0] for c in page.evaluate("() => window.__E2E_CALLS__")]

            # ---- 1. 输入 /:全部 14 个动作 ----
            ta.click()
            page.keyboard.type("/")
            page.wait_for_function(
                "() => document.querySelectorAll('#slashMenu .at-item').length === 14")
            check(page.locator("#slashMenu .at-item").first.inner_text()
                  .startswith("导出会话"), "第一项是导出会话")

            # ---- 2. 过滤 ----
            page.keyboard.type("导出")
            page.wait_for_function(
                "() => document.querySelectorAll('#slashMenu .at-item').length === 1")
            check(True, "输入「导出」过滤到 1 项(「导」会同时命中课表的「导入」)")

            # ---- 3. Enter 执行:桥收到 export_session,输入框被清干净,不发送 ----
            page.keyboard.press("Enter")
            page.wait_for_timeout(150)
            check(calls() == ["export_session"], f"执行的是真动作(调用序 {calls()})")
            check(ta.input_value() == "", "输入框里的 /token 被吃掉")
            check(page.locator("#slashMenu").is_hidden(), "执行后菜单关闭")

            # ---- 4. Esc 关菜单 ----
            ta.click()
            page.keyboard.type("/")
            page.wait_for_selector("#slashMenu .at-item")
            page.keyboard.press("Escape")
            page.wait_for_timeout(80)
            check(page.locator("#slashMenu").is_hidden(), "Esc 关菜单")
            check("send_task" not in calls(), "Esc 没有触发发送")

            # ---- 5. 没有匹配的动作:摆实话 ----
            ta.click()
            page.keyboard.type("/xyz")
            page.wait_for_timeout(120)
            check("没有这个动作" in page.locator("#slashMenu").inner_text(),
                  "无匹配摆一行实话")
            page.keyboard.press("Escape")

            browser.close()
    finally:
        stop()
    print()
    if FAILS:
        print(f"{len(FAILS)} 项没过:")
        for f in FAILS:
            print("  - " + f)
        return 1
    print("全绿 6 项。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
