# -*- coding: utf-8 -*-
"""v0.9.161 前端回归:面板拖宽 + 「后台命令」只读弹窗拆除。

两件都小,但各有一条要钉死的形状:

1. **拖宽改的是一个变量**:`--sp-w` 同时喂给网格列与地图让位 —— 钉法是
   拖动后断言**网格第 4 列的计算宽度**真的变了、localStorage 记了账、
   重开窗口还是那个宽度、双击回 300。夹在中间的钳制(260~560)也要试:
   往左猛拖不该把面板拖成一根线。
2. **拆除要拆干净**:#termOverlay 不在 DOM、旧 JS 块不残留
   (`termLoad`/`termSetOpen` 未定义),而终端页(同一本后台账)照常活着。

跑法(项目根目录):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/e2e_ui_v09161.py
"""
from __future__ import annotations

import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from e2e_launch import launch          # noqa: E402

STUB_JS = """
window.__CALLS__ = [];
const base = {
  get_info: () => Promise.resolve({
    version: "0.9.161", app_name: "引路学长", permission_mode: "accept_all",
    permission_modes: [{value: "accept_all", label: "自动放行"}],
    model: "test-model", active_provider: "test", workspace: "D:/demo/campus",
    workspace_name: "campus", workspace_is_default: true,
    providers: [{id: "test", name: "Test", model: "test-model", has_key: true}],
    api_key_configured: true, memory_count: 1, memory_user_count: 0,
    mcp_servers: [], skills: [], kb_count: 0, sandbox: null, campus_map: null,
    library_health: {}, paths: {app_dir: "D:/demo", writable: true},
    session_metrics: {ok: false, selectable: []}
  }),
  get_monitor_pref: () => Promise.resolve({ok: true, open: false, folded: {}}),
  set_monitor_pref: () => Promise.resolve({ok: true}),
  refresh_sessions: () => Promise.resolve({ok: true}),
  get_recent_projects: () => Promise.resolve({ok: true, current: {}, default: {}, recent: []}),
  get_reasoning: () => Promise.resolve({visible: false, options: [], choice: "default"}),
  get_suggestions: () => Promise.resolve({suggestions: []}),
  get_slash_commands: () => Promise.resolve({ok: true, commands: []}),
  get_countdowns: () => Promise.resolve({ok: true, countdowns: []}),
  get_plan: () => Promise.resolve({ok: true, has_plan: false, steps: [], total: 0,
                                   done: 0, ready: [], blocked: [], failed: [],
                                   running: [], dispatch_text: ""}),
  get_session_metrics: () => Promise.resolve({ok: false}),
  read_clipboard: () => Promise.resolve({text: "", image: false, source: "empty"}),
  get_activity: () => Promise.resolve({ok: true, empty: true, groups: []}),
  get_env_info: () => Promise.resolve({ok: true}),
  get_terminal: () => Promise.resolve({ok: true, jobs: [
      {id: "j1", command: "echo hi", running: false, exit_code: 0, killed: false,
       chars: 3, dropped: 0, elapsed: 1, tail: "hi"}],
    buffer_chars: 4000, note: "只留尾部"}),
};
window.pywebview = {api: new Proxy(base, {
  get: (t, k) => (k in t) ? t[k]
    : ((...a) => { window.__CALLS__.push([k, ...a]); return Promise.resolve({ok: true}); })
})};
"""

COL4 = """() => {
  const cols = getComputedStyle(document.querySelector('.app'))
    .gridTemplateColumns.split(' ');
  return parseInt(cols[cols.length - 1], 10);
}"""


def _serve(directory: Path):
    import threading

    class _QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = partial(_QuietHandler, directory=str(directory))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}/index.html", httpd.shutdown


def main() -> int:
    from playwright.sync_api import sync_playwright

    url, shutdown = _serve(FRONTEND)
    failures: list[str] = []
    ran = 0

    def check(cond, message):
        nonlocal ran
        ran += 1
        print(("  ✓ " if cond else "  ✗ ") + message)
        if not cond:
            failures.append(message)

    with sync_playwright() as p:
        browser = launch(p)
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.add_init_script(STUB_JS)
        page.goto(url)
        page.wait_for_timeout(700)

        # ① 旧弹窗拆干净,终端页活着
        check(page.evaluate("() => document.getElementById('termOverlay')") is None,
              "#termOverlay 不在 DOM")
        check(page.evaluate("() => typeof window.termLoad") == "undefined"
              and page.evaluate("() => typeof window.termSetOpen") == "undefined",
              "旧 JS 块(termLoad/termSetOpen)不残留")
        page.click("#btnTerminal")
        page.wait_for_function(
            "() => document.querySelectorAll('.term-dock .t-cmd').length === 1")
        check(page.locator('.term-dock #tpCmd').count() == 1,
              "终端坞(同一本后台账)照常活着,输入行在场")

        # ② 拖宽:顶栏「切换面板」开底座(v0.9.162 新钮,顺手验),再按住把手往左拖
        page.click("#btnPanelToggle")
        page.wait_for_selector(".app.act-open")
        res = page.locator(".sp-resizer")
        check(res.count() == 1, "拖宽把手在场")
        box = res.bounding_box()
        before = page.evaluate(COL4)
        page.mouse.move(box["x"] + 3, box["y"] + 300)
        page.mouse.down()
        page.mouse.move(1400 - 420, box["y"] + 300, steps=5)
        page.mouse.up()
        page.wait_for_timeout(120)
        after = page.evaluate(COL4)
        check(after > before + 50, f"拖动后第 4 列变宽({before} → {after})")
        saved = page.evaluate("() => parseInt(localStorage.getItem('sp.width'), 10)")
        check(saved == after, f"松手记了账(localStorage {saved} == 计算宽 {after})")

        # ③ 钳制两边都要试:往左猛拖不超过 560,往右猛拖不低于 260
        box = res.bounding_box()
        page.mouse.move(box["x"] + 3, box["y"] + 300)
        page.mouse.down()
        page.mouse.move(700, box["y"] + 300, steps=3)   # 1400-700=700 → 钳到上限
        page.mouse.up()
        page.wait_for_timeout(120)
        hi = page.evaluate(COL4)
        check(hi == 560, f"往左猛拖被钳在上限 560(实际 {hi})")

        box = res.bounding_box()
        page.mouse.move(box["x"] + 3, box["y"] + 300)
        page.mouse.down()
        page.mouse.move(1300, box["y"] + 300, steps=3)  # 1400-1300=100 → 钳到下限
        page.mouse.up()
        page.wait_for_timeout(120)
        lo = page.evaluate(COL4)
        check(lo == 260, f"往右猛拖被钳在下限 260(实际 {lo})")

        # ④ 重开窗口:同一份偏好,宽度还在
        page2 = ctx.new_page()
        page2.goto(url, wait_until="load")
        page2.wait_for_timeout(500)
        page2.click("#btnActivity")
        page2.wait_for_selector(".app.act-open")
        page2.wait_for_timeout(150)
        w2 = page2.evaluate(COL4)
        check(w2 == lo, f"重开窗口还是那个宽度({w2} == {lo})")
        page2.close()

        # ⑤ 双击回 300
        res = page.locator(".sp-resizer")
        res.dblclick()
        page.wait_for_timeout(120)
        check(page.evaluate(COL4) == 300, "双击回到 300px")

        check(not errors, f"无页面错误(实际:{errors[:2] or '无'})")
        browser.close()
    shutdown()

    print(f"\n共 {ran} 项,失败 {len(failures)} 项")
    for f in failures:
        print("  ✗", f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
