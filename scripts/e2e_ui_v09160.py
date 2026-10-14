# -*- coding: utf-8 -*-
"""v0.9.160 前端回归:侧聊页(内置第五页,origin=sidechat 的后台会话)。

这一页最容易被骗的地方是**事件路由**:侧聊线程的事件带着自己的 session 键,
必须画进侧聊页、且**绝不漏进主聊天区** —— 路由挂在"后台会话不进当前视图"
那道闸之前,顺序错了就是互相串台。所以这场一半的断言都在盯两条流的分界:

1. 页面形状:第六个 tab、空态文案、＋ 开线程走 sidechat_new;
2. 发话:输入逐字过 sidechat_send,用户气泡当场落;
3. 流式:text_delta 进侧聊的气泡、finish_stream 定稿成 Markdown;
4. **分界**:侧聊事件喂进去之后,主聊天区(#chat)一个子节点都不多;
5. 事件里没见过的 session 键(主会话)照样走原闸,不进侧聊页。

跑法(项目根目录):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/e2e_ui_v09160.py
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
    version: "0.9.160", app_name: "引路学长", permission_mode: "accept_all",
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
  // ---- 侧聊 ----
  sidechat_threads: () => Promise.resolve({ok: true, threads: [
    {file: "session_old.jsonl", key: "", title: "上次问的聚类", busy: false,
     n_messages: 4, time: "2026-10-10 18:00"}]}),
  sidechat_new: () => { window.__CALLS__.push(["sidechat_new"]);
    return Promise.resolve({ok: true, key: "scNEW", file: "", title: ""}); },
  sidechat_read: (t) => Promise.resolve({ok: true,
    key: t === "session_old.jsonl" ? "scOLD" : "scNEW", file: t,
    busy: false, messages: []}),
  sidechat_send: (target, text) => { window.__CALLS__.push(["sidechat_send", target, text]);
    return Promise.resolve({ok: true, key: "scNEW", file: ""}); },
};
window.pywebview = {api: new Proxy(base, {
  get: (t, k) => (k in t) ? t[k]
    : ((...a) => { window.__CALLS__.push([k, ...a]); return Promise.resolve({ok: true}); })
})};
"""


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
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.add_init_script(STUB_JS)
        page.goto(url)
        page.wait_for_timeout(700)

        # ① 底座里的第六页(先拉开面板:收着的时候 tab 根本不可见)
        page.click("#btnActivity")
        page.wait_for_selector(".app.act-open")
        tabs = page.locator("#spTabs .sp-tab")
        check(tabs.count() == 6, f"内置页 6 格(v0.9.172+ 加了扩展页,实际 {tabs.count()})")
        page.locator('#spTabs .sp-tab[title="侧聊"]').click()
        page.wait_for_timeout(300)
        check(page.locator('.sp-page[data-sp="sidechat"]').is_visible(),
              "侧聊页打开")
        check(page.locator('#scThread option[value="session_old.jsonl"]').count() == 1,
              "线程清单来自 sidechat_threads(旧线程在)")

        # ② ＋ 开新线程:走 sidechat_new,气泡区清空
        page.click("#scNew")
        page.wait_for_timeout(250)
        calls = page.evaluate("window.__CALLS__")
        check(["sidechat_new"] in calls, "＋ 走的是 sidechat_new")

        # ③ 发话:输入逐字过桥,用户气泡当场落
        chat_children = page.evaluate(
            "() => document.getElementById('chat').children.length")
        page.fill("#scText", "帮我列三个标题")
        page.click("#scSend")
        page.wait_for_timeout(200)
        calls = page.evaluate("window.__CALLS__")
        check(["sidechat_send", "", "帮我列三个标题"] in calls
              or any(c[0] == "sidechat_send" and c[2] == "帮我列三个标题"
                     for c in calls),
              "输入逐字过 sidechat_send")
        bubbles = page.locator("#scBody .sc-u")
        check(bubbles.count() == 1 and "帮我列三个标题" in bubbles.first.text_content(),
              "用户气泡当场落在侧聊页里")

        # ④ 流式:text_delta 画进气泡,finish_stream 定稿
        page.evaluate("""() => {
          window.__gcodeEvent({type: "text_delta", session: "scNEW", text: "三个标题:"});
          window.__gcodeEvent({type: "text_delta", session: "scNEW", text: "甲/乙/丙"});
          window.__gcodeEvent({type: "finish_stream", session: "scNEW"});
        }""")
        page.wait_for_timeout(150)
        out = page.locator("#scBody .sc-a").last.text_content()
        check("三个标题:甲/乙/丙" in (out or ""), "text_delta 画进侧聊气泡")
        # 主聊天区一个子节点都不多 —— 分界就是这一条
        chat_after = page.evaluate(
            "() => document.getElementById('chat').children.length")
        check(chat_after == chat_children,
              f"侧聊事件不漏进主聊天区({chat_children} → {chat_after})")

        # ⑤ busy 流转:Send 在忙时拒发
        page.evaluate("""() => {
          window.__gcodeEvent({type: "busy", session: "scNEW", value: true});
        }""")
        page.wait_for_timeout(100)
        page.fill("#scText", "忙时这句")
        page.click("#scSend")
        page.wait_for_timeout(150)
        calls = page.evaluate("window.__CALLS__")
        check(not any(c[0] == "sidechat_send" and c[2] == "忙时这句" for c in calls),
              "忙时不发(与主会话同一套判据)")

        # ⑥ 陌生的 session 键(主会话的事件)不进侧聊页
        before = page.locator("#scBody .sc-b").count()
        page.evaluate("""() => {
          window.__gcodeEvent({type: "text_delta", session: "s1", text: "主会话的话"});
        }""")
        page.wait_for_timeout(120)
        after = page.locator("#scBody .sc-b").count()
        check(before == after, "主会话的事件不进侧聊页")

        # ⑦ openPanel side: 直达
        page.evaluate('openPanel("side:sidechat")')
        page.wait_for_timeout(150)
        check(page.locator('.sp-page[data-sp="sidechat"]').is_visible(),
              'openPanel("side:sidechat") 直达')

        check(not errors, f"无页面错误(实际:{errors[:2] or '无'})")
        browser.close()
    shutdown()

    print(f"\n共 {ran} 项,失败 {len(failures)} 项")
    for f in failures:
        print("  ✗", f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
