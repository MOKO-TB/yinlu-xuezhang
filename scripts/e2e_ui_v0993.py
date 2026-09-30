# -*- coding: utf-8 -*-
"""v0.9.93 前端回归:并行子代理内部的每一步,挂在监控栏 `task` 那一行下面看得见。

批量模式当年把每路的 `tool_start`/`tool_done` 整个吞掉(几路 `text_delta` 会在
同一个聊天区里穿插成乱码,这条规矩是对的),代价是"分别处理问题"变成了黑箱。
这一轮开的是一条**只记账不刷屏**的通道,所以这一场要断的就是这两面:

1. 时间线里 `task` 行下面真的挂出了子步,带「第 N 路[档位]」标签、真实时刻与用时;
2. 失败那一路在子行上就看得出失败(不是只在最后那份合并报告里猜);
3. 仍在跑的那一路显示为"进行中"并带着它自己的长命令进度;
4. 子行**不可点** —— 那一步的原文在主会话里根本不存在,给一个点开是空的行比不给更坏;
5. 溢出时那句「另有 N 步没列出」在(少列行不少报);
6. 「此刻」写的是最深那一层(第 3 路的 read_file),不是没用的"task 在跑";
7. 聊天区仍然只有一条 `task` 卡片 —— 上面那些不能以刷屏为代价。

载荷都出自**真** `GuiUI` + 真 `Session` + 真 `activity.session_activity`,不是手填字典
(后端换了字段名这场就红)。

跑法(项目根目录):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/e2e_ui_v0993.py
"""
from __future__ import annotations

import json
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # 判据里有 ✓

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
for p in (str(ROOT), str(ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

from e2e_launch import launch          # noqa: E402  起浏览器这件事的一份真相

ARGS = {"type": ["explore", "review"],
        "task": ["找登录相关代码", "审查权限模块"]}

STUB_JS = """
window.__ACTIVITY__ = @@RUNNING@@;
window.__CALLS__ = [];
const base = {
  get_info: () => Promise.resolve({
    version: "0.9.93", app_name: "引路学长", permission_mode: "accept_all",
    permission_modes: [{value: "confirm", label: "变更前确认"}],
    model: "test-model", active_provider: "test", workspace: "D:/demo/campus",
    workspace_name: "campus", workspace_is_default: true,
    providers: [{id: "test", name: "Test", model: "test-model", has_key: true}],
    api_key_configured: true, memory_count: 0, memory_user_count: 0,
    mcp_servers: [], skills: [], kb_count: 0, sandbox: null, campus_map: null,
    library_health: {}, paths: {app_dir: "D:/demo", writable: true},
    session_metrics: {ok: false, selectable: []}
  }),
  get_activity: () => { window.__CALLS__.push(["get_activity"]);
    return Promise.resolve(window.__ACTIVITY__); },
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
window.pywebview = {api: new Proxy(base, {
  get: (t, k) => (k in t) ? t[k]
    : ((...a) => { window.__CALLS__.push([k, ...a]); return Promise.resolve({ok: true}); })
})};
"""


def _real_session():
    """一份真会话:一条用户发言 + 一次 `task` 调用 + 它的回话。"""
    from minicode.llm import AssistantTurn, ToolCall
    from minicode.session import Session

    s = Session(system_prompt="系统提示词", log_dir=None)
    s.add_user("并行看两处")
    s.add_assistant(AssistantTurn(tool_calls=[ToolCall(
        id="b1", name="task", arguments=json.dumps(ARGS, ensure_ascii=False))]))
    s.add_tool_result("b1", "task", "[并行子代理完成 · 2 个]\n两路都回来了")
    return s


def _running_ui():
    """真 GuiUI:父行 `task` 开着,下面挂三步(两步已结束含一步失败,一步仍在跑)。"""
    from minicode import gui_ui
    from minicode.tools.base import Tool

    ui = gui_ui.GuiUI()
    task = Tool(name="task", description="", parameters={}, handler=lambda **k: "")
    ui.tool_start(task, ARGS)
    ui.live_child_begin("第1路[explore]", "explore·grep", "上限")
    ui.live_child_end("第1路[explore]", True)
    ui.live_child_begin("第2路[review]", "review·read_file", "notes/perm.py")
    ui.live_child_end("第2路[review]", False)          # 这一路失败了,要看得见
    ui.live_child_begin("第1路[explore]", "explore·bash", "python -m pytest -q")
    ui.live_child_progress("第1路[explore]",
                           "已跑 45 秒 · 已输出 5120 字 · 最后一行: tests/test_a.py ..")
    return ui, task


def _payload(ui, messages):
    from minicode import activity
    return activity.session_activity(messages, workspace=ROOT, live=ui.live_rows(),
                                     now=ui.live_now(), busy=True)


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

    session = _real_session()
    ui, task = _running_ui()
    running = _payload(ui, session.messages)
    kids_of_task = next(r for r in running["timeline"]["turns"][0]["items"]
                        if r["tool"] == "task")["children"]
    assert len(kids_of_task) == 3, kids_of_task

    # 第二份载荷:又多跑了一步(用来验 child_step 事件确实驱动刷新)
    ui.live_child_end("第1路[explore]", True)
    ui.live_child_begin("第2路[review]", "review·grep", "权限")
    deeper = _payload(ui, session.messages)
    dropped = json.loads(json.dumps(deeper))
    tk = next(r for r in dropped["timeline"]["turns"][0]["items"] if r["tool"] == "task")
    tk["children_dropped"] = 7

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
        page.click("#btnActivity")
        page.wait_for_selector(".app.act-open", timeout=5000)
        page.wait_for_selector(".act-kids .act-row", timeout=5000)

        # ---- 1. 子步挂出来了,带路标签与真实时刻 ----
        kids = page.locator(".act-kids .act-row")
        check(kids.count() == 3, f"`task` 行下面挂出 3 步(实测 {kids.count()})")
        first = kids.nth(0).inner_text()
        check("第1路[explore]" in first and "上限" in first,
              f"第一行写的是「哪一路 + 它做了什么」:{first!r}")
        check("用时 1s" in first or "用时 0s" in first,
              f"子步有真实用时(不是刷新那一刻凑的):{first!r}")

        # ---- 2. 失败那一路看得见 ----
        second = kids.nth(1).inner_text()
        check("第2路[review]" in second and "失败" in second,
              f"失败的那一路在子行上就标出来:{second!r}")

        # ---- 3. 仍在跑的那一路:转圈 + 它自己的长命令进度 ----
        third = kids.nth(2).inner_text()
        check("进行中" in third or "跑" in third, f"没结束的那步有状态字:{third!r}")
        check("已跑 45 秒" in third and "pytest" in third,
              f"长命令的进度落在**它自己那一行**上,不盖住别的行:{third!r}")
        check(page.locator(".act-kids .act-row.running").count() == 1,
              "仍在跑的那一步有 running 的样子(转圈圆点)")

        # ---- 4. 子行不可点:点开是空的东西比不给更坏 ----
        clickable = page.evaluate(
            "() => [...document.querySelectorAll('.act-kids .act-row')]"
            "  .filter(r => !r.classList.contains('plain')).length")
        check(clickable == 0, "子步一律标成不可点(它没有可展开的原文)")

        # ---- 5.「此刻」写的是最深那一层 ----
        now = page.inner_text("#actBody")
        check("第1路[explore]" in now and "在 task 这一步里" in now,
              "「此刻」下钻到最深一层,并写明属于哪一路、套在哪个父步里")

        # ---- 6. child_step 事件驱动刷新:新跑的一步要自己冒出来 ----
        page.evaluate("(p) => { window.__ACTIVITY__ = p;"
                      " window.__gcodeEvent({type: 'child_step', state: 'start',"
                      " route: '第2路[review]', name: 'review·grep'}); }", deeper)
        page.wait_for_timeout(900)
        body = page.inner_text("#actBody")
        check("权限" in body, "推一条 child_step 之后面板确实重画了(不接这条就停在旧值)")

        # ---- 7. 溢出要说清丢了几个,而不是静默少列 ----
        page.evaluate("(p) => { window.__ACTIVITY__ = p;"
                      " window.__gcodeEvent({type: 'child_step', state: 'end'}); }", dropped)
        page.wait_for_timeout(900)
        check("另有 7 步没列出" in page.inner_text("#actBody"),
              "超出每一路的步数上限时如实报丢了几个")

        # ---- 8. 聊天区仍然只有一条 task 卡片(安静不能被弄松) ----
        check(page.locator(".tool-card").count() <= 1,
              "子步只进监控栏,不往聊天区叠卡片")

        check(not errors, f"整场点完,控制台没有一条报错:{errors[:3]}")
        browser.close()
    shutdown()

    if failures:
        print(f"\nFAIL: {len(failures)} 项不对")
        for f in failures:
            print(" - " + f)
        return 1
    print("\nPASS: 并行子代理的每一步都挂在父行下面 —— 安静不等于黑箱")
    return 0


if __name__ == "__main__":
    sys.exit(main())
