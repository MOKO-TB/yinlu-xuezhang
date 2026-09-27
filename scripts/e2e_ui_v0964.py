# -*- coding: utf-8 -*-
"""v0.9.64 前端回归:@ 文件引用补全 —— Codex/Cursor 同款交互,真点真选真插。

钉四件事:①输入 @ 弹出菜单,数据来自桥的真返回(项数与文案逐字对上);
②菜单开着时 Enter/↑↓ 被补全吃掉,不触发发送;③选中后路径真进输入框、
@token 被替换、菜单关闭;④Esc 能关菜单,桥没有该方法的网页版形态不炸。

跑法(项目根目录,GBK 控制台必须带 PYTHONIOENCODING=utf-8):
    PYTHONIOENCODING=utf-8 .venv\\Scripts\\python.exe scripts\\e2e_ui_v0964.py
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
window.__FILES__ = ["README.md", "minicode/agent.py", "minicode/mcp.py",
                    "minicode/calendar_edit.py", "tests/test_agent.py"];

window.pywebview = {
  api: {
    get_info: () => Promise.resolve({
      version: "0.9.64", app_name: "引路学长",
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
    workspace_search_files: (query, limit) => {
      window.__E2E_CALLS__.push(["workspace_search_files", query, limit]);
      const q = String(query || "").toLowerCase();
      return Promise.resolve({ok: true, items: window.__FILES__
        .filter(f => f.toLowerCase().includes(q)).slice(0, limit || 20)});
    },
    send_task: (text) => {
      window.__E2E_CALLS__.push(["send_task", text]);
      return Promise.resolve({ok: true, session: "s1"});
    },
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

            # ---- 1. 输入 @:菜单弹出,数据来自桥 ----
            ta.click()
            page.keyboard.type("帮我看看 ")
            page.keyboard.type("@")
            page.wait_for_function(
                "() => document.querySelectorAll('#atMenu .at-item').length === 5")
            items = page.locator("#atMenu .at-item")
            check(items.count() == 5, f"空 @ 列出全部 5 个文件(实测 {items.count()})")
            check(page.locator("#atMenu .at-item").first.inner_text()
                  .startswith("README.md"), "第一项是排序后的浅层文件")

            # ---- 2. 过滤:继续输入收窄 ----
            page.keyboard.type("minicode")
            page.wait_for_function(
                "() => document.querySelectorAll('#atMenu .at-item').length === 3")
            check(True, "继续输入 minicode 过滤到 3 项")

            # ---- 3. 键盘选择:↑↓ 换选、Enter 选中且不发送 ----
            page.keyboard.press("ArrowDown")
            page.keyboard.press("Enter")
            page.wait_for_timeout(120)
            value = ta.input_value()
            check("minicode/mcp.py" in value, f"选中项路径进了输入框(value={value!r})")
            check(page.locator("#atMenu").is_hidden(), "选择后菜单关闭")
            check("send_task" not in calls(), "补全吃掉了 Enter,没有触发发送")

            # ---- 4. Esc 关菜单 ----
            ta.fill("")
            page.keyboard.type("@")
            page.wait_for_selector("#atMenu .at-item")
            page.keyboard.press("Escape")
            page.wait_for_timeout(80)
            check(page.locator("#atMenu").is_hidden(), "Esc 关菜单")
            check(calls()[-1] != "send_task", "Esc 没有触发发送")

            # ---- 5. 网页版形态:桥没有该方法 → 不弹菜单也不炸 ----
            page.evaluate("() => { delete pywebview.api.workspace_search_files; }")
            ta.fill("")
            page.keyboard.type("@")
            page.wait_for_timeout(250)
            check(page.locator("#atMenu").is_hidden(), "桥没有该方法时安静退化")

            browser.close()
    finally:
        stop()
    print()
    if FAILS:
        print(f"{len(FAILS)} 项没过:")
        for f in FAILS:
            print("  - " + f)
        return 1
    print("全绿 9 项。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
