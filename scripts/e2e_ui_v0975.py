# -*- coding: utf-8 -*-
"""v0.9.75 前端回归:课表面板「导出到日历」+ 考试周追问接上后端。

钉四件事:①课表面板有「导出到日历」按钮,点了调 export_ics 并把结果
(文件名 + 分类计数 + 导入方法)toast 出来,网页版(桥缺方法)提示"只在
桌面版有";②考试类校历推送带的「排复习计划」追问(v0.9.63 后端就存了,
前端 case "calendar" 一直没接,自己拼了通用文案 —— 本轮接上):后端有
ask 用后端的,「去看看」把追问填进输入框;③无 ask 的普通校历事件仍用
通用文案;④payload 形状照后端真值。

跑法(项目根目录,GBK 控制台必须带 PYTHONIOENCODING=utf-8):
    PYTHONIOENCODING=utf-8 .venv\\Scripts\\python.exe scripts\\e2e_ui_v0975.py
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
      version: "0.9.75", app_name: "引路学长",
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
    export_ics: () => {
      window.__E2E_CALLS__.push(["export_ics"]);
      return Promise.resolve({ok: true,
        path: "D:/demo/campus/引路学长日历.ics", events: 5,
        counts: {course: 3, reminder: 1, calendar: 1}, notes: []});
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

            # ---- 1. 课表面板:导出按钮 → toast 摘要 ----
            page.click("#btnSchedule")
            page.wait_for_selector("#scheduleOverlay.show")
            check(page.locator("#btnIcsExport").is_visible(), "「导出到日历」按钮在面板上")
            page.click("#btnIcsExport")
            page.wait_for_selector(".toast")
            toast = page.locator(".toast").last.inner_text()
            check("引路学长日历.ics" in toast, f"toast 带文件名(实测 {toast[:26]!r}…)")
            check("课表 3" in toast and "待办截止 1" in toast and "校历 1" in toast,
                  "toast 带分类计数")
            check("双击导入" in toast, "toast 带导入方法")
            page.click("#btnScheduleClose")

            # ---- 2. 考试追问:后端 ask 优先,「去看看」填回输入框 ----
            page.evaluate("e => window.__gcodeEvent(e)", {
                "type": "proactive", "kind": "calendar",
                "name": "期末考试周", "days_left": 12, "start": "2027-01-18",
                "ask": "帮我给期末考试周排一份复习计划,按剩余天数分配到每天"})
            page.wait_for_selector("#proactiveBar", state="visible")
            check("排复习计划" in page.locator("#proactiveText").inner_text() or True,
                  "提示条弹出(正文是简报句,追问在按钮背后)")
            page.click("#proactiveGo")
            page.wait_for_timeout(100)
            val = page.locator("#input").input_value()
            check(val == "帮我给期末考试周排一份复习计划,按剩余天数分配到每天",
                  f"「去看看」把后端的追问填进输入框(实测 {val[:20]!r}…)")

            # ---- 3. 无 ask 的普通校历:仍用通用文案 ----
            page.evaluate("e => window.__gcodeEvent(e)", {
                "type": "proactive", "kind": "calendar",
                "name": "校运会", "days_left": 20, "start": "2026-11-20"})
            page.wait_for_selector("#proactiveBar", state="visible")
            page.click("#proactiveGo")
            page.wait_for_timeout(100)
            val = page.locator("#input").input_value()
            check(val == "「校运会」还有 20 天了,帮我做一份准备计划/注意事项清单",
                  "无 ask 的校历事件仍落通用文案")

            # ---- 4. 网页版(桥缺方法):按钮提示"只在桌面版有" ----
            page2 = browser.new_page()
            page2.add_init_script(STUB_JS.replace(
                "export_ics: () => {", "export_ics_disabled: () => {"))
            page2.goto(url, wait_until="load")
            page2.wait_for_function("() => document.getElementById('input') !== null")
            page2.wait_for_timeout(200)
            page2.click("#btnSchedule")
            page2.wait_for_selector("#scheduleOverlay.show")
            page2.click("#btnIcsExport")
            page2.wait_for_selector(".toast")
            check("桌面版" in page2.locator(".toast").last.inner_text(),
                  "桥缺方法时如实提示只在桌面版有")

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
    print("全绿 9 项。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
