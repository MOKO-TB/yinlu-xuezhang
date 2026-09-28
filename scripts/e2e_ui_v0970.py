# -*- coding: utf-8 -*-
"""v0.9.70 前端回归:侧栏 MCP 健康行。

README 一直承诺「侧栏底部会显示 MCP 服务器名(工具数)」,但 v0.9.70 核实
前端一处都没消费 get_info.mcp_servers(v0.9.39 入口栏改版前后丢的)。这轮
把那行真做出来:配置了的都在、连上的带工具数、没连上的写「未连上」、
停用的写「停用」;热重载后 mcp_summary 事件立即刷新,没有服务器时整行不出现。

跑法(项目根目录,GBK 控制台必须带 PYTHONIOENCODING=utf-8):
    PYTHONIOENCODING=utf-8 .venv\\Scripts\\python.exe scripts\\e2e_ui_v0970.py
"""
from __future__ import annotations

import json
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MCP_SERVERS = [
    {"id": "fetch", "label": "网页抓取(fetch)", "enabled": True,
     "connected": True, "n_tools": 2, "server": "fetch-server",
     "transport": "stdio"},
    {"id": "fs", "label": "本地文件夹(filesystem)", "enabled": True,
     "connected": False, "n_tools": 0, "server": "", "transport": "stdio"},
    {"id": "off", "label": "时间与时区(time)", "enabled": False,
     "connected": False, "n_tools": 0, "server": "", "transport": "stdio"},
]

STUB_JS = """
window.__V970_MCP__ = @@MCP@@;
window.pywebview = {
  api: {
    get_info: () => Promise.resolve({
      version: "0.9.70", app_name: "引路学长",
      permission_mode: "confirm",
      permission_modes: [{value: "confirm", label: "变更前确认"}],
      session_key: "s1", model: "test-model", active_provider: "test",
      providers: [{id: "test", name: "Test", model: "test-model", has_key: true}],
      api_key_configured: true, workspace: "D:/demo/campus",
      workspace_name: "campus", workspace_is_default: true,
      memory_count: 0, memory_user_count: 0, mcp_servers: window.__V970_MCP__,
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
  }
};
""".replace("@@MCP@@", json.dumps(MCP_SERVERS, ensure_ascii=False))

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

            line = page.locator("#mcpLine")
            check(line.is_visible(), "配置了 MCP 时侧栏健康行出现")
            txt = line.inner_text()
            check("网页抓取(fetch)(2 工具)" in txt,
                  f"连上的写工具数(实测 {txt!r})")
            check("本地文件夹(filesystem)(未连上)" in txt,
                  "没连上的如实写「未连上」")
            check("时间与时区(time)(停用)" in txt,
                  "停用的写「停用」")

            # 热重载后的事件刷新:同一份 _mcp_summary 推过来就重画
            page.evaluate(
                "e => window.__gcodeEvent(e)",
                {"type": "mcp_summary", "session": "s1", "items": [
                    {"id": "fs", "label": "本地文件夹(filesystem)", "enabled": True,
                     "connected": True, "n_tools": 5, "server": "fs-server",
                     "transport": "stdio"}]})
            page.wait_for_timeout(120)
            txt2 = line.inner_text()
            check("本地文件夹(filesystem)(5 工具)" in txt2
                  and "fetch" not in txt2,
                  "mcp_summary 事件后整行按新清单重画")

            # 没有服务器:整行不出现(不摆一个空占位)
            page.evaluate("e => window.__gcodeEvent(e)",
                          {"type": "mcp_summary", "session": "s1", "items": []})
            page.wait_for_timeout(120)
            check(line.is_hidden(), "清空后整行收起,不摆空占位")

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
    print("全绿 6 项。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
