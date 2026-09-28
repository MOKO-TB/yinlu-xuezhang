# -*- coding: utf-8 -*-
"""v0.9.74 前端回归:对话型 slash 命令 —— / 面板两类混排、展开与发送。

钉五件事:①打 / 时命令项与动作项混排(命令带「命令」徽标);②继续输入过滤
命令(按 cmd/label/desc);③无参命令 Enter 后**真的发送了模板全文**(走
与手打同一条 send 链,桩捕获 send_task 的文本 == 后端表里的 prompt);
④带参命令(/翻译)Enter 只展开:输入框是去 $text 的模板、不自动发送;
⑤桥没有该方法(网页版)时只有动作面板,不炸。payload 形状照后端真值。

跑法(项目根目录,GBK 控制台必须带 PYTHONIOENCODING=utf-8):
    PYTHONIOENCODING=utf-8 .venv\\Scripts\\python.exe scripts\\e2e_ui_v0974.py
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

# 照后端真表取两条(形状与内容都来自 minicode.slash_commands)
from minicode.slash_commands import PROMPT_COMMANDS  # noqa: E402

_BY_CMD = {c["cmd"]: c for c in PROMPT_COMMANDS}
SUMMARIZE = _BY_CMD["总结"]
TRANSLATE = _BY_CMD["翻译"]
COMMANDS = [
    {"cmd": c["cmd"], "label": c["label"], "desc": c["desc"],
     "prompt": c["prompt"], "has_arg": "$text" in c["prompt"]}
    for c in (SUMMARIZE, TRANSLATE)
]

STUB_JS = """
window.__E2E_CALLS__ = [];
window.__V974_CMDS__ = @@CMDS@@;
window.pywebview = {
  api: {
    get_info: () => Promise.resolve({
      version: "0.9.74", app_name: "引路学长",
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
    get_slash_commands: () => Promise.resolve({ok: true, commands: window.__V974_CMDS__}),
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
""".replace("@@CMDS@@", json.dumps(COMMANDS, ensure_ascii=False))

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
            page.wait_for_timeout(300)      # 等 boot 里的 get_slash_commands 回来

            ta = page.locator("#input")

            def calls():
                return [c for c in page.evaluate("() => window.__E2E_CALLS__")]

            # ---- 1. 打 /:命令与动作混排,命令带徽标 ----
            ta.click()
            page.keyboard.type("/")
            page.wait_for_selector("#slashMenu .at-item")
            total = page.locator("#slashMenu .at-item").count()
            check(total >= 16, f"/ 面板含动作+命令共 {total} 项(动作 14 + 命令 2)")
            check(page.locator("#slashMenu .cmd-tag").count() == 2,
                  "两条命令都带「命令」徽标")

            # ---- 2. 过滤到命令:按 cmd 匹配 ----
            page.keyboard.type("总结")
            page.wait_for_function(
                "() => document.querySelectorAll('#slashMenu .at-item').length === 1")
            check(page.locator("#slashMenu .at-item").first.inner_text()
                  .find("总结这段对话") >= 0, "「总结」过滤出总结命令")

            # ---- 3. 无参命令 Enter = 真发送模板全文 ----
            page.keyboard.press("Enter")
            page.wait_for_timeout(150)
            sent = [c for c in calls() if c[0] == "send_task"]
            check(len(sent) == 1 and sent[0][1] == SUMMARIZE["prompt"],
                  f"send_task 收到模板全文(实测 {sent[0][1][:30] + '…' if sent else '无'})")
            check(ta.input_value() == "", "发送后输入框清空(与手打同一条链)")

            # ---- 4. 带参命令:只展开,不发送 ----
            page.keyboard.type("/翻译")
            page.wait_for_selector("#slashMenu .at-item")
            page.keyboard.press("Enter")
            page.wait_for_timeout(150)
            val = ta.input_value()
            check("$text" not in val and val.startswith("请把下面的内容翻译成中文"),
                  f"翻译命令展开成模板且去掉占位(实测 {val[:24]!r})")
            check(len([c for c in calls() if c[0] == "send_task"]) == 1,
                  "带参命令不自动发送")

            # ---- 5. 桥没有该方法(网页版):只有动作面板,不炸 ----
            # 注意:不能在本页 reload —— add_init_script 每次导航都会重跑,
            # 桩会带着方法回来;退化场景必须用"一开始就没有该方法"的新页面。
            page2 = browser.new_page()
            page2.on("pageerror", lambda e: js_errors.append(str(e)))
            page2.add_init_script(STUB_JS.replace(
                "get_slash_commands: () => Promise.resolve({ok: true, commands: window.__V974_CMDS__}),",
                ""))
            page2.goto(url, wait_until="load")
            page2.wait_for_function("() => document.getElementById('input') !== null")
            page2.wait_for_timeout(200)
            ta2 = page2.locator("#input")
            ta2.click()
            page2.keyboard.type("/")
            page2.wait_for_selector("#slashMenu .at-item")
            check(page2.locator("#slashMenu .cmd-tag").count() == 0,
                  "桥不可用时安静退化成只有动作面板")

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
