# -*- coding: utf-8 -*-
"""前端回归:会话内搜索(Ctrl+F)与补齐的那几把快捷键。

侧栏能搜历史会话,当前这条长对话反而搜不了;而"搜一次把消息上的按钮搜坏了"是这类
功能最容易犯的错 —— 用 innerHTML 包 <mark> 就会:每重写一次,⧉ 复制 / ✎ 编辑重发 /
↻ 重新生成 的监听器全丢,界面看着还在、点了没反应。所以本场除了搜得到、跳得动,
**还专门验搜完之后那颗 ↻ 仍点得动**。

钉这些:
1. Ctrl+F 打开搜索条并聚焦;Esc 关掉并把高亮清干净;
2. 命中计数与循环跳转(2 条命中 → 1/2 → 2/2 → 回到 1/2);
3. 命中在**收起的**「💭 工作过程」里时,跳过去要先把那一块展开(否则滚到一片
   display:none 的位置,用户看到的是"跳了个空");
4. 少于 2 字不搜,并说明为什么(满屏单字 highlight 只会晃眼);
5. 搜完之后 ↻ 仍可用(防 innerHTML 重写那一类回归);
6. Ctrl+Shift+K 聚焦输入框、Ctrl+Shift+] 切到侧栏下一条会话(参数必须是文件名 ——
   序号会在两次渲染之间错位,这是 v0.9.3x 就记过的坑)。

跑法(项目根目录):
    PYTHONIOENCODING=utf-8 .venv\\Scripts\\python.exe scripts\\e2e_ui_find.py
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
# `scripts/` 也要在 path 上:起浏览器那件事只有一份真相(`e2e_launch.py`),
# 不再在这一支里抄一遍退路(v0.9.150 收的第 1/29 支,见 §6 53(e))。
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from e2e_launch import launch          # noqa: E402

STUB_JS = """
window.__RESUME__ = [];
window.__REGEN_CALLS__ = 0;
const base = {
  get_info: () => Promise.resolve({
    version: "0.9.41", app_name: "引路学长",
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
  send_task: () => Promise.resolve({ok: true, session: "s1"}),
  rate_message: () => Promise.resolve({ok: true}),
  regenerate: () => { window.__REGEN_CALLS__ += 1; return Promise.resolve({ok: true}); },
  resume_session: (which) => { window.__RESUME__.push(String(which));
                               return Promise.resolve({ok: true}); },
};
window.pywebview = {api: new Proxy(base, {
  get: (t, k) => (k in t) ? t[k] : ((...a) => Promise.resolve({ok: true}))
})};
"""

SESSIONS = {"items": [
    {"index": 1, "file": "session_20260926_0900.jsonl", "time": "2026-09-26 09:00",
     "n_messages": 12, "last_user_text": "当前这条", "workspace": "", "current": True},
    {"index": 2, "file": "session_20260925_2011.jsonl", "time": "2026-09-25 20:11",
     "n_messages": 300, "last_user_text": "另一条长的", "workspace": ""},
]}


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
    errors: list[str] = []

    def check(cond, message):
        if not cond:
            failures.append(message)
        print(("  ok   " if cond else "  FAIL ") + message)

    stub = STUB_JS
    with sync_playwright() as p:
        browser = launch(p)
        page = browser.new_page(viewport={"width": 1280, "height": 860})
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.add_init_script(stub)
        page.goto(url)
        page.wait_for_timeout(900)

        def emit(payload):
            page.evaluate("e => window.__gcodeEvent(e)", payload)

        def count_text():
            return page.text_content("#findCount").strip()

        # 造三段内容:两条正文各含一次"绩点",一条工具卡片含"蓝桥"
        emit({"type": "text_delta", "text": "绩点规则在第一屏"})
        emit({"type": "finish_stream"})
        emit({"type": "tool_start", "name": "grep", "summary": "在课件里找 蓝桥 这一词"})
        emit({"type": "tool_done", "name": "grep", "result": "命中三处"})
        emit({"type": "text_delta", "text": "再算绩点要按必修口径"})
        emit({"type": "finish_stream"})
        page.evaluate("s => window.__gcodeEvent({type:'sessions', items: s.items})",
                      json.loads(json.dumps(SESSIONS)))
        page.wait_for_timeout(300)

        check(page.locator(".msg.assistant").count() >= 2, "① 对话里真有两段正文可搜")

        # ---- 1) Ctrl+F 打开并聚焦
        page.keyboard.press("Control+f")
        page.wait_for_timeout(180)
        check(page.locator("#findBar.show").count() == 1, "① Ctrl+F 把搜索条打开了")
        check(page.evaluate("() => document.activeElement.id") == "findInput",
              "① 焦点直接落在搜索框里")

        # ---- 2) 计数与循环
        page.fill("#findInput", "绩点")
        page.wait_for_timeout(220)
        check(count_text() == "1 / 2", f"② 两处命中,报的是 1 / 2(实报 {count_text()})")
        check(page.evaluate("() => CSS.highlights.has('chat-find')"),
              "② 高亮是挂在 CSS.highlights 上的(不改 DOM)")
        page.keyboard.press("Enter")
        page.wait_for_timeout(200)
        check(count_text() == "2 / 2", f"② Enter 跳到第二条:{count_text()}")
        page.keyboard.press("Enter")
        page.wait_for_timeout(200)
        check(count_text() == "1 / 2", f"② 再 Enter 绕回第一条:{count_text()}")

        # ---- 3) 收起的工作过程里的命中:先展开再跳
        # 先把所有过程区强行收起,做出"用户平时看到的样子"(答完后它们本该是收的;
        # 这里不依赖折叠时机,只守"跳转能不能把要看的那块打开")
        page.evaluate("() => document.querySelectorAll('.trace').forEach"
                      "(t => t.classList.remove('open'))")
        page.wait_for_timeout(120)
        closed_before = page.evaluate(
            "() => document.querySelectorAll('.trace.open').length") == 0
        page.fill("#findInput", "蓝桥")
        page.wait_for_timeout(320)
        check(closed_before, "③ 搜之前那一块是收起的(前提成立)")
        check(count_text() == "1 / 1", f"③ 工具卡片里的命中也算:{count_text()}")
        check(page.evaluate("() => document.querySelectorAll('.trace.open').length") == 1,
              "③ 命中在收起的「工作过程」里时会自动展开(不展开就是滚到看不见的位置)")
        page.keyboard.press("Enter")
        page.wait_for_timeout(400)
        check(page.evaluate("() => document.querySelectorAll('.trace.open').length") == 1,
              "③ 跳过去时把「工作过程」展开了(不展开就是滚到看不见的位置)")

        # ---- 4) 少于 2 字不搜,并说明原因
        page.fill("#findInput", "绩")
        page.wait_for_timeout(200)
        note = page.text_content("#findNote")
        check("再打一两个字" in note, f"④ 单字不搜且告诉用户为什么:{note.strip()[:40]}")
        check(page.evaluate("() => !CSS.highlights.has('chat-find')"),
              "④ 不搜的时候高亮层是空的,不留下一次搜的残留")

        # ---- 5) 搜完之后消息上的按钮还活着
        page.fill("#findInput", "绩点")
        page.wait_for_timeout(200)
        page.keyboard.press("Escape")
        page.wait_for_timeout(180)
        check(page.locator("#findBar.show").count() == 0, "⑤ Esc 关掉了搜索条")
        check(page.evaluate("() => !CSS.highlights.has('chat-find')"),
              "⑤ 关掉时把高亮一起清了(关不掉的高亮比没有更烦人)")
        msg = page.locator(".msg.assistant").last
        msg.hover()
        msg.locator(".rt-re").click()
        page.wait_for_timeout(260)
        check(page.evaluate("window.__REGEN_CALLS__") == 1,
              "⑤ 搜过一遍之后 ↻ 仍点得动(用 innerHTML 包 mark 就会在这里死)")

        # ---- 6) 补齐的快捷键
        page.evaluate("() => document.getElementById('input').blur()")
        page.keyboard.press("Control+Shift+k")
        page.wait_for_timeout(160)
        check(page.evaluate("() => document.activeElement.id") == "input",
              "⑥ Ctrl+Shift+K 聚焦输入框(单列 Ctrl+K 是模型管理,不抢)")
        page.keyboard.press("Control+Shift+]")
        page.wait_for_timeout(240)
        check(page.evaluate("window.__RESUME__") == ["session_20260925_2011.jsonl"],
              f"⑥ Ctrl+Shift+] 切到下一条,传的是文件名:{page.evaluate('window.__RESUME__')}")

        # ---- 7) 换会话后搜索条自己关掉:DOM 整个重建,旧 Range 指向已摘掉的节点
        page.keyboard.press("Control+f")
        page.wait_for_timeout(140)
        page.fill("#findInput", "绩点")
        page.wait_for_timeout(220)
        check(page.evaluate("() => CSS.highlights.has('chat-find')"), "⑦ 换会话前高亮层在")
        page.evaluate("() => window.__gcodeEvent({type:'resumed', messages: []})")
        page.wait_for_timeout(320)
        check(page.locator("#findBar.show").count() == 0,
              "⑦ 恢复另一条会话后搜索条自己收掉(留着会跳不动)")
        check(page.evaluate("() => !CSS.highlights.has('chat-find')"),
              "⑦ 指向旧节点的高亮一起清掉,不留一张挂在空气上的图")
        page.close()
        browser.close()

    check(not [e for e in errors if "favicon" not in e], f"无 pageerror:{errors[:2]}")
    shutdown()
    print()
    if failures:
        for f in failures:
            print("FAIL:", f)
        print(f"\n{len(failures)} 项不通过")
        return 1
    print("PASS: 搜得到、跳得动、收起的过程会展开、单字不搜、搜完按钮没死、三把新键各就各位")
    return 0


if __name__ == "__main__":
    sys.exit(main())
