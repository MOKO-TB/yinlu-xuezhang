# -*- coding: utf-8 -*-
"""v0.9.97 前端回归:顶栏「🧠 记忆」那张「记一条 / 整理」的卡(`#memoryOverlay`)。

这一轮把那颗按钮从「点开就直接走整理」改成一张能**主动记一条**的卡。界面最容易骗人的
就是"看着有数字",所以这一场断的事都是**那个数是不是桥给的**:

1. 卡上那行计数来自 `get_info` 带回的两级条数,前端不自己数;
2. 空内容点「记到本项目」**一次都不敲桥**(桥那边"没内容可记"是拒绝,不该弹一张错误框);
3. 写的那句话**逐字**过桥,归属由按钮定(project / user),前端不替他决定存哪级;
4. 记完那行计数换成**桥返回的新数**(前端不自增 —— 去重与合并都发生在桥里,前端 +1 一定骗人),
   输入框清空;「整理」仍走 `consolidate_memory`(all),「关闭」一次桥都不敲。

stub 的 `remember` / `consolidate_memory` 返回的是 `Bridge._memory_counts()` 那个形状
(`{ok, msg, project, user}`);键名对不对得上由 `tests/test_memory_dream.py` 的端到端针管着,
这里只管「界面有没有照桥给的数显示」。

跑法(项目根目录):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/e2e_ui_v0997.py
"""
from __future__ import annotations

import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # 判据里有 ✓

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from e2e_launch import launch          # noqa: E402  起浏览器这件事的一份真相

SENTENCE = "我讨厌用表格讲数据 / 这个项目跑测试别加 -n auto"
PLACEHOLDER = f"要记住的一句话,如:{SENTENCE}"

# 计数刻意用**不对称**的数(project 3 / user 1),并让桥返回的数与"前端自己 +1"必然不同:
# 前端只要是自己数的、或把两级加在一起,下面的断言就会红。
STUB_JS = """
window.__CALLS__ = [];
const base = {
  get_info: () => Promise.resolve({
    version: "0.9.97", app_name: "引路学长", permission_mode: "confirm",
    permission_modes: [{value: "confirm", label: "变更前确认"}],
    model: "test-model", active_provider: "test", workspace: "D:/demo/campus",
    workspace_name: "campus", workspace_is_default: true,
    providers: [{id: "test", name: "Test", model: "test-model", has_key: true}],
    api_key_configured: true, memory_count: 3, memory_user_count: 1,
    mcp_servers: [], skills: [], kb_count: 0, sandbox: null, campus_map: null,
    library_health: {}, paths: {app_dir: "D:/demo", writable: true},
    session_metrics: {ok: false, selectable: []}
  }),
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
  remember: (text, scope) => {
    window.__CALLS__.push(["remember", text, scope]);
    return Promise.resolve(scope === "user"
      ? {ok: true, msg: "已记录 1 条到用户级记忆", project: 3, user: 9}
      : {ok: true, msg: "已记录 1 条到本项目记忆", project: 8, user: 1});
  },
  consolidate_memory: (scope) => {
    window.__CALLS__.push(["consolidate_memory", scope]);
    return Promise.resolve({ok: true, msg: "整理完成", project: 4, user: 2});
  },
};
window.pywebview = {api: new Proxy(base, {
  get: (t, k) => (k in t) ? t[k]
    : ((...a) => { window.__CALLS__.push([k, ...a]); return Promise.resolve({ok: true}); })
})};
"""


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
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.add_init_script(STUB_JS)
        page.goto(url)
        page.wait_for_selector("#ver", timeout=15000)
        page.wait_for_function(
            "() => /本项目 3/.test(document.getElementById('btnMemory').title)",
            timeout=8000)

        def calls(name):
            return [c for c in page.evaluate("window.__CALLS__") if c and c[0] == name]

        def count_line():
            return page.inner_text("#memCount").strip()

        # ---- 1. 不点开也看得见:按钮的悬浮说明就是桥给的两级条数 ----
        title = page.get_attribute("#btnMemory", "title") or ""
        check("本项目 3" in title and "用户级 1" in title,
              f"顶栏按钮 title 报的是桥给的两级条数(实测「{title[:36]}…」)")

        # ---- 2. 点开是卡片,不是「直接开始整理」 ----
        page.click("#btnMemory")
        page.wait_for_selector("#memoryOverlay.show", timeout=5000)
        check(count_line() == "现在:本项目 3 条 · 用户级 1 条",
              f"卡上那行计数是 get_info 带回的数(实测「{count_line()}」)")
        check(not calls("consolidate_memory"),
              "点开卡片这一刻没有敲 consolidate_memory(老行为是一点开就走整理)")
        check(page.get_attribute("#memText", "placeholder") == PLACEHOLDER,
              "输入框的示例是一句真人话,不是空占位")
        check(page.locator("#memoryOverlay .btns button").count() == 4,
              f"卡片四个出口:关闭 / 整理 / 记到用户级 / 记到本项目"
              f"(实测 {page.locator('#memoryOverlay .btns button').count()} 个)")

        # ---- 3. 空内容一次都不敲桥,但要给人话 ----
        page.click("#memSaveProject")
        check(not calls("remember"), "空内容点「记到本项目」不敲桥")
        check("先写一句" in page.inner_text("#toasts"),
              "空内容给的是提示,不是静默失败")

        # ---- 4. 逐字过桥,归属由按钮定 ----
        page.fill("#memText", SENTENCE)
        page.click("#memSaveProject")
        page.wait_for_function(
            "() => window.__CALLS__.some(c => c[0] === 'remember')", timeout=5000)
        last = calls("remember")[-1]
        check(last[1] == SENTENCE, f"过桥的是原话、一字不改(实测「{last[1][:20]}…」)")
        check(last[2] == "project", "点「记到本项目」传的就是 project")
        check(count_line() == "现在:本项目 8 条 · 用户级 1 条",
              f"记完之后那行换成桥返回的数,不是前端自己 3+1(实测「{count_line()}」)")
        check(page.input_value("#memText") == "", "记完输入框清空,不留一句已经存过的话")

        # ---- 5. 另一颗按钮是另一种事实 ----
        page.fill("#memText", SENTENCE)
        page.click("#memSaveUser")
        page.wait_for_function(
            "() => window.__CALLS__.filter(c => c[0] === 'remember').length >= 2",
            timeout=5000)
        check(calls("remember")[-1][2] == "user", "点「记到用户级」传的是 user")
        check(count_line() == "现在:本项目 3 条 · 用户级 9 条",
              f"用户级那一次按桥返回改写两级(实测「{count_line()}」)")

        # ---- 6. 整理仍走 consolidate_memory,而且不顺手记一条 ----
        remember_before = len(calls("remember"))
        page.click("#memDream")
        page.wait_for_function(
            "() => window.__CALLS__.some(c => c[0] === 'consolidate_memory')", timeout=5000)
        check(calls("consolidate_memory")[-1][1] == "all",
              "「整理记忆」调的是 consolidate_memory(「all」)")
        check(len(calls("remember")) == remember_before, "点整理不会顺手记一条")
        check(count_line() == "现在:本项目 4 条 · 用户级 2 条",
              f"整理后的计数也是桥给的(实测「{count_line()}」)")
        check("本项目 4" in (page.get_attribute("#btnMemory", "title") or ""),
              "顶栏那颗按钮跟着改成同一个数(卡内外两个来源就等着漂)")

        # ---- 7. 关闭不敲桥,重开还是最近那个数 ----
        before = len(page.evaluate("window.__CALLS__"))
        page.click("#memClose")
        page.wait_for_timeout(300)
        check(page.evaluate(
            "() => !document.getElementById('memoryOverlay').classList.contains('show')"),
            "点「关闭」卡片收起来了")
        check(len(page.evaluate("window.__CALLS__")) == before,
              "关闭这一步一次桥都没敲(关窗不该顺手存点什么)")
        page.click("#btnMemory")
        page.wait_for_selector("#memoryOverlay.show", timeout=5000)
        check(count_line() == "现在:本项目 4 条 · 用户级 2 条",
              f"重开卡片显示的是最近一次桥给的数(实测「{count_line()}」)")

        # ---- 8. 整场零报错 ----
        check(not errors,
              f"整场点下来控制台一条报错都没有(实测 {len(errors)} 条:「{'; '.join(errors)[:70]}」)")
        browser.close()
    shutdown()

    print(f"\n{'PASS' if not failures else 'FAIL'}: e2e_ui_v0997 "
          f"{ran - len(failures)}/{ran} 项通过"
          + (f",{len(failures)} 项红" if failures else ""))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
