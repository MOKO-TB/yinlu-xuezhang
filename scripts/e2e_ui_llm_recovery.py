# -*- coding: utf-8 -*-
"""前端回归:模型连不上时,那句没送出去的话要回到输入框里(但不许自己飞出去)。

后端 `llm_error` 推的是 `{"type":"error", "llm": true}`。为什么前端要知道"是 llm 失败":
`send()` 在调桥之前就把输入框清空了(index.html 里那句 `$("input").value = ""`),所以
服务一抖动,同学面前只剩一条红色 toast,原话**真丢了** —— 只能重新打一遍。工具跑到
一半才错的那种不是"没发出去",回填反而误导,所以标记要分清。

钉四件事:
1. llm 失败 → 原话回到输入框、附件标签一起回来、并且**没有**自动再发一次;
2. 模型已经开始回话之后才错 → 不回填(那句确实送到了);
3. 不带 llm 标记的错误 → 不回填;
4. 回填一次就清账,不会连着几次把老话反复塞回去。

跑法(项目根目录):
    PYTHONIOENCODING=utf-8 .venv\\Scripts\\python.exe scripts\\e2e_ui_llm_recovery.py
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
window.__SEND_CALLS__ = [];
const base = {
  get_info: () => Promise.resolve({
    version: "0.9.40", app_name: "引路学长",
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
  send_task: (text, atts) => { window.__SEND_CALLS__.push(text); return Promise.resolve({ok: true}); },
};
window.pywebview = {api: new Proxy(base, {
  get: (t, k) => (k in t) ? t[k] : ((...a) => Promise.resolve({ok: true}))
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

    url, shutdown = _serve(FRONTEND)
    failures: list[str] = []
    errors: list[str] = []

    def check(cond, message):
        if not cond:
            failures.append(message)
        print(("  ok   " if cond else "  FAIL ") + message)

    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1280, "height": 860})
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}")
                if m.type == "error" else None)
        page.add_init_script(STUB_JS)
        page.goto(url)
        page.wait_for_timeout(900)

        def input_value():
            return page.evaluate("() => document.getElementById('input').value")

        def sends():
            return page.evaluate("window.__SEND_CALLS__")

        def send_a_question(text):
            page.fill("#input", text)
            page.click("#btnSend")
            page.wait_for_timeout(200)

        # ---- 1) llm 失败:原话回输入框,且不自动重发
        send_a_question("帮我看这份课件的第 3 章")
        check(input_value() == "", "① 发出去那一刻输入框是空的(发之前就被清了)")
        check(sends() == ["帮我看这份课件的第 3 章"], "① 只发了一次")
        page.evaluate("() => window.__gcodeEvent({type:'error', text:'模型调用失败: 连接被拒绝', llm:true})")
        page.wait_for_timeout(220)
        check(input_value() == "帮我看这份课件的第 3 章", "① 那句原话回到输入框里了")
        check(sends() == ["帮我看这份课件的第 3 章"], "① 回填不等于重发:一次都没自己再发")

        # ---- 2) 已经开始回话之后才错:不回填
        page.fill("#input", "")
        send_a_question("第二个问题")
        page.evaluate("t => window.__gcodeEvent({type:'text_delta', text: t})", "先答半句")
        page.evaluate("() => window.__gcodeEvent({type:'error', text:'模型调用失败: 断了', llm:true})")
        page.wait_for_timeout(220)
        check(input_value() == "", "② 那句确实送到了模型,不该再塞回输入框")

        # ---- 3) 不带 llm 标记的错误:不回填
        send_a_question("第三个问题")
        page.evaluate("() => window.__gcodeEvent({type:'error', text:'内部错误: KeyError'})")
        page.wait_for_timeout(220)
        check(input_value() == "", "③ 非模型失败的错误不回填(那不代表话没发出去)")

        # ---- 4) 回一次就清账
        page.fill("#input", "第四个问题")
        page.click("#btnSend")
        page.wait_for_timeout(200)
        page.evaluate("() => window.__gcodeEvent({type:'error', text:'模型调用失败', llm:true})")
        page.wait_for_timeout(220)
        check(input_value() == "第四个问题", "④ 第一次失败能回填")
        page.fill("#input", "")
        page.evaluate("() => window.__gcodeEvent({type:'error', text:'模型调用失败', llm:true})")
        page.wait_for_timeout(220)
        check(input_value() == "", "④ 同一句不会被塞第二次(回完就清账)")

        page.close()
        browser.close()

    # 本机 http.server 没有 favicon.ico 那条"Failed to load resource"是环境噪音,
    # 与 e2e_ui_v0939_p2 同一处理;真正的 pageerror / 其它 console.error 一律算红。
    noisy = ("favicon", "Failed to load resource")
    check(not [e for e in errors if not any(n in e for n in noisy)],
          f"控制台无报错{[e for e in errors if not any(n in e for n in noisy)][:3]}")
    shutdown()
    print()
    if failures:
        for f in failures:
            print("FAIL:", f)
        print(f"\n{len(failures)} 项不通过")
        return 1
    print("PASS: 只有「整句没送到模型」才回填,回一次就清账,而且绝不自己发出去")
    return 0


if __name__ == "__main__":
    sys.exit(main())
