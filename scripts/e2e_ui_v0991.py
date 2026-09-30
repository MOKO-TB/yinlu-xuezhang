# -*- coding: utf-8 -*-
"""v0.9.91 前端回归:长命令跑到一半,「此刻」那一行真的写着它跑到哪了。

这一轮改的是**看见**:前台 `bash` 自己起进程、两条管道各一个读线程,每 15 秒向界面
报一行"已跑多久 · 已输出多少字 · 最后一行"。后端那半有 `tests/test_bash_progress.py`
钉着;这一场只回答界面那半的四个问题(源码断言证不了这些):

1. 桥递来的 `now.progress` 真被画进 `#actNowProgress`,而且写的是**后端那句原文**
   (界面不自己拼字数、不自己判"算不算慢");
2. `tool_progress` 事件确实驱动了一次刷新 —— 少了这条接线,那一行永远停在旧值;
3. 那一步结束后(`live_now()` 给 None),整行跟着消失,不留一个"还在跑"的样子;
4. 后端没给进度时那一行**不出现**(不写"刚开始"或转圈凑数)。

两份载荷都出自**真** `GuiUI.tool_start/tool_progress/tool_done` + 真
`activity.session_activity`,不是手填的字典 —— 后端换了字段名这场就红。

跑法(项目根目录):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/e2e_ui_v0991.py
"""
from __future__ import annotations

import json
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    # 这台机器的控制台默认 GBK,而下面要 print 的判据里有 ✓ —— 裸跑会崩在半路,
    # 那种崩长得像"页面坏了",所以脚本自己先把输出钉成 UTF-8。
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from e2e_launch import launch          # noqa: E402  起浏览器这件事的一份真相

STUB_JS = """
window.__ACTIVITY__ = @@RUNNING@@;
window.__CALLS__ = [];
const base = {
  get_info: () => Promise.resolve({
    version: "0.9.91", app_name: "引路学长", permission_mode: "confirm",
    permission_modes: [{value: "confirm", label: "变更前确认"}],
    model: "test-model", active_provider: "test", workspace: "D:/demo/campus",
    workspace_name: "campus", workspace_is_default: true,
    providers: [{id: "test", name: "Test", model: "test-model", has_key: true}],
    api_key_configured: true, memory_count: 0, memory_user_count: 0,
    mcp_servers: [], skills: [], kb_count: 0, sandbox: null, campus_map: null,
    library_health: {}, paths: {app_dir: "D:/demo", writable: true},
    session_metrics: {ok: false, selectable: []}
  }),
  /* get_activity 也记账:最后一段要数的就是"收起来之后还被不被敲"。 */
  get_activity: () => { window.__CALLS__.push(["get_activity"]);
    return Promise.resolve(window.__ACTIVITY__); },
  /* 偏好给"收起":这一场要**点一下才开**,开机就是开的会让第一次点击变成"关掉"。 */
  get_monitor_pref: () => Promise.resolve({ok: true, open: false, folded: {}}),
  set_monitor_pref: () => Promise.resolve({ok: true, open: false, folded: {}}),
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
};
/* 没列出来的方法记一笔账再回 {ok: true}:断言查的是"该出现的那个名字出现了",
   所以名字打错/换掉照样红,兜底不会把错的方法名蒙过去。 */
window.pywebview = {api: new Proxy(base, {
  get: (t, k) => (k in t) ? t[k]
    : ((...a) => { window.__CALLS__.push([k, ...a]); return Promise.resolve({ok: true}); })
})};
"""


def _payloads():
    """三份载荷都由真后端算出来:跑到一半的、已经结束的、在跑但没给进度的。"""
    from minicode.activity import session_activity
    from minicode.gui_ui import GuiUI
    from minicode.tools.base import Tool

    bash = Tool(name="bash", description="", parameters={}, handler=lambda **k: "")
    ui = GuiUI()
    ui.tool_start(bash, {"command": "python -m pytest -q"})
    ui.tool_progress("已跑 45 秒(上限 300s) · 已输出 2048 字 · "
                     "最后一行: tests/test_activity.py ..........")
    running = session_activity([], workspace=ROOT, live=ui.live_rows(),
                               now=ui.live_now(), busy=True)
    ui.tool_done(bash, "退出码: 0")
    done = session_activity([], workspace=ROOT, live=ui.live_rows(),
                            now=ui.live_now(), busy=True)
    # 第三种情形:确实在跑,但后端没报过任何进度(短命令)—— 那一行整条不该出现
    ui2 = GuiUI()
    ui2.tool_start(bash, {"command": "echo hi"})
    no_progress = session_activity([], workspace=ROOT, live=ui2.live_rows(),
                                   now=ui2.live_now(), busy=True)
    return running, done, no_progress


def _serve(directory: Path):
    class _QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = partial(_QuietHandler, directory=str(directory))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}/index.html", httpd.shutdown


def main() -> int:
    from playwright.sync_api import sync_playwright

    running, done, no_progress = _payloads()
    assert running.get("now"), "前提没成立:真 GuiUI 没给出『此刻』那一行"
    assert running["now"]["progress"], "前提没成立:那一行里没有进度字段"

    stub = STUB_JS.replace("@@RUNNING@@", json.dumps(running, ensure_ascii=False))
    url, shutdown = _serve(FRONTEND)
    failures: list[str] = []

    def check(cond, message):
        print(("  ✓ " if cond else "  ✗ ") + message)
        if not cond:
            failures.append(message)

    with sync_playwright() as p:
        browser = launch(p)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.add_init_script(stub)
        page.goto(url)
        page.wait_for_selector("#ver", timeout=15000)

        # ---- 1. 打开「任务监控」:此刻那一行写的是后端那句原文 ----
        page.click("#btnActivity")
        page.wait_for_selector(".app.act-open", timeout=5000)
        page.wait_for_selector("#actNowProgress", timeout=5000)
        got = page.inner_text("#actNowProgress")
        check(running["now"]["progress"] in got,
              f"「此刻」那行就是后端 progress 的原文(界面不自己拼):{got!r}")
        check("2048 字" in got, "字数是后端算的那个,不是前端自己数的")

        # ---- 2. 推一条新进度:事件要驱动一次刷新,不然那行停在旧值 ----
        newer = json.loads(json.dumps(running))
        newer["now"]["progress"] = "已跑 60 秒(上限 300s) · 已输出 3900 字 · 最后一行: 3 passed"
        page.evaluate("(p) => { window.__ACTIVITY__ = p;"
                      " window.__gcodeEvent({type: 'tool_progress', text: p.now.progress }); }",
                      newer)
        page.wait_for_timeout(700)      # activitySchedule(300) 的防抖 + 取数
        check("3900 字" in page.inner_text("#actNowProgress"),
              "tool_progress 事件确实驱动了一次刷新(不接这条,那一行永远停在旧值)")

        # ---- 3. 那一步结束:整行消失,不留"还在跑"的样子 ----
        page.evaluate("(p) => { window.__ACTIVITY__ = p;"
                      " window.__gcodeEvent({type: 'tool_done', ok: true}); }", done)
        page.wait_for_timeout(700)
        check(page.locator("#actNowProgress").count() == 0,
              "步一结束进度就跟着消失(live_now 给 None → 那一行不再画)")

        # ---- 4. 后端没给进度时那一行不出现(短命令不写"刚开始"凑数) ----
        page.evaluate("(p) => { window.__ACTIVITY__ = p;"
                      " window.__gcodeEvent({type: 'tool_start', name: 'bash'}); }", no_progress)
        page.wait_for_timeout(700)
        check(page.locator("#actNowProgress").count() == 0,
              "没有进度时那一行整条不出现")
        check(no_progress.get("now") and not no_progress["now"]["progress"],
              "前提:这一份载荷确实在跑、但没有任何进度可读")

        # ---- 5. 关掉那一栏就不再敲桥(关着的时候统计是白费) ----
        before = page.evaluate("() => (window.__CALLS__ || []).length")
        page.click("#btnActivity")
        page.wait_for_selector(".app.act-open", state="detached", timeout=5000)
        page.evaluate("() => window.__gcodeEvent({type: 'tool_progress', text: '再敲一次试试'})")
        page.wait_for_timeout(700)
        check(page.evaluate("() => (window.__CALLS__ || []).length") == before,
              "收起那一栏之后,进度事件不再敲桥(没人看的时候统计是白费)")

        check(not errors, f"整场点完,控制台没有一条报错:{errors[:3]}")
        browser.close()
    shutdown()

    if failures:
        print(f"\nFAIL: {len(failures)} 项不对")
        for f in failures:
            print(" - " + f)
        return 1
    print("\nPASS: 长命令的进度从真 GuiUI 一路画到「此刻」那一行,"
          "结束就跟着消失,收起就不再敲桥")
    return 0


if __name__ == "__main__":
    sys.exit(main())
