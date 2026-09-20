# -*- coding: utf-8 -*-
"""v0.9.14 前端回归:真实浏览器里验证本轮的知识库成长闭环与校园小工具。

    新 首页倒计时 chips:校历数据(get_countdowns)渲染成「倒计时」行,点一下回填提问
    新 消息级 👍/👎 反馈:助手消息悬停操作栏新增两颗按钮,点击调 rate_message 并置灰
    保 纯工具调用消息仍然没有操作栏(不给评)
    新 桥接 shim:bridge-web.js 的 METHODS 含 rate_message / get_countdowns(静态断言)

跑法(项目根目录):
    .venv\\Scripts\\python.exe scripts\\e2e_ui_v0914.py
退出码 0 = 通过;1 = 有断言失败。
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

COUNTDOWNS = [
    {"name": "中秋节", "start": "2026-09-25", "kind": "holiday",
     "days_left": 6, "note": "9月25日至27日放假,共3天。"},
    {"name": "国庆节", "start": "2026-10-01", "kind": "holiday",
     "days_left": 12, "note": "10月1日至7日放假调休。"},
]

STUB_JS = ("""
window.__E2E_CALLS__ = [];
window.__stubReady = true;
const CALLS = window.__E2E_CALLS__;
function log(){ CALLS.push([...arguments]); return Promise.resolve({ok:true}); }
window.pywebview = {
  api: {
    get_info: () => Promise.resolve({
      version: "0.9.14", app_name: "引路学长",
      permission_mode: "confirm",
      permission_modes: [{value: "confirm", label: "变更前确认"}],
      session_key: "s1", model: "deepseek-v4-flash", active_provider: "gzasc",
      providers: [{id: "gzasc", name: "广州应用科技学院",
                   model: "deepseek-v4-flash", has_key: true}],
      api_key_configured: true, workspace: "D:/demo/yhbz",
      workspace_name: "yhbz", workspace_is_default: false,
      memory_count: 0, memory_user_count: 0, mcp_servers: [],
      sandbox: {}, vision: false, campus_map: null, library_health: {},
      skills: [], kb_count: 0, session_metrics: {ok: false}
    }),
    get_suggestions: () => Promise.resolve({suggestions: ["最近有什么活动"]}),
    get_countdowns: () => { CALLS.push(["get_countdowns"]);
      return Promise.resolve({ok: true, countdowns: __INJ_COUNTDOWNS}); },
    rate_message: (rating, question, answer) => {
      CALLS.push(["rate_message", rating, question, answer]);
      return Promise.resolve({ok: true}); },
    send_task: (t) => { CALLS.push(["send_task", t]);
      return Promise.resolve({ok: true, session: "s1"}); },
    refresh_sessions: () => Promise.resolve({ok: true}),
    get_recent_projects: () => Promise.resolve({ok: true, current: {}, default: {}, recent: []}),
    get_reasoning: () => Promise.resolve({visible: false, options: [], choice: "default"}),
    get_session_metrics: () => Promise.resolve({ok: false}),
  }
};
""").replace("__INJ_COUNTDOWNS", json.dumps(COUNTDOWNS, ensure_ascii=False))


def _serve(directory: Path):
    class _QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = partial(_QuietHandler, directory=str(directory))
    # 本机 Windows 的临时端口范围从 ~6470 起,随机分配几乎必撞 Chromium 的
    # unsafe port 黑名单(6000-7999 一带)—— 所以在安全段里**显式**逐个绑定。
    port = None
    for candidate in range(18442, 18520):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", candidate), handler)
            port = candidate
            break
        except OSError:
            continue
    assert port is not None, "18442-18519 里找不到可绑定的端口"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}/index.html", httpd.shutdown


def _push(page, event: dict):
    page.evaluate(f"window.__gcodeEvent({json.dumps(event, ensure_ascii=False)})")


def _launch(p):
    """launch(headless) 用的是 headless-shell;本机只装了完整版内核时退回 executable_path。"""
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

    def check(cond, message):
        if not cond:
            failures.append(message)
        print(("  ✓ " if cond else "  ✗ ") + message)

    try:
        with sync_playwright() as p:
            browser = _launch(p)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.add_init_script(STUB_JS)
            page.goto(url, wait_until="load")
            page.wait_for_function(
                "() => document.getElementById('sessions') !== null")

            # ---- 1. 首页倒计时 chips ----
            page.wait_for_function(
                "() => document.querySelectorAll('.cd-chips .chip-cd').length === 2")
            texts = page.eval_on_selector_all(
                ".cd-chips .chip-cd", "els => els.map(e => e.textContent)")
            check("中秋节 · 还有 6 天" in texts[0],
                  f"倒计时 chip 显示事件与天数({texts[0]})")
            check("国庆节 · 还有 12 天" in texts[1],
                  f"第二条倒计时正确({texts[1]})")
            check(page.locator(".cd-chips .chip-cd").first.get_attribute("title")
                  .startswith("2026-09-25"),
                  "chip 悬停提示带日期")

            # 点倒计时 chip → 输入框回填对它的提问
            page.click(".cd-chips .chip-cd")
            value = page.input_value("#input")
            check("中秋节" in value, f"点倒计时 chip 回填提问({value})")

            # ---- 2. 👍/👎 消息反馈 ----
            page.fill("#input", "宿舍几点断电")
            page.click("#btnSend")
            _push(page, {"type": "text_delta", "session": "s1",
                         "text": "23:00 断电,记得提前充电。"})
            _push(page, {"type": "finish_stream", "session": "s1"})
            page.wait_for_selector(".msg.assistant .body")
            check(page.locator(".msg.assistant .acts .rt-up").count() == 1
                  and page.locator(".msg.assistant .acts .rt-dn").count() == 1,
                  "带正文的助手消息有 👍/👎 按钮")

            page.hover(".msg.assistant")
            page.click(".msg.assistant .rt-up")
            page.wait_for_function(
                "() => window.__E2E_CALLS__.some(c => c[0] === 'rate_message')")
            check(page.evaluate(
                "() => { const c = window.__E2E_CALLS__.find(c => c[0]==='rate_message');"
                " return c[1] === 1 && c[2] === '宿舍几点断电'"
                " && c[3].includes('23:00 断电'); }"),
                "rate_message 带评分、对应的问题与答案摘要")
            check(page.evaluate(
                "() => document.querySelector('.msg.assistant')"
                "?.classList.contains('rated') ?? false"),
                "评分后消息标记为已评")
            # 已评按钮 pointer-events:none(真实点击本就点不中);用 JS 派发验证守卫
            page.evaluate(
                "() => document.querySelector('.msg.assistant .rt-up').click()")
            calls = page.evaluate(
                "() => window.__E2E_CALLS__.filter(c => c[0]==='rate_message').length")
            check(calls == 1, "已评消息重复点击不会再次上报")

            # ---- 3. 第二条消息点 👎 ----
            page.fill("#input", "图书馆几点闭馆")
            page.click("#btnSend")
            _push(page, {"type": "text_delta", "session": "s1",
                         "text": "22:30 闭馆。"})
            _push(page, {"type": "finish_stream", "session": "s1"})
            page.wait_for_function(
                "() => document.querySelectorAll('.msg.assistant').length === 2")
            page.hover(".msg.assistant:nth-last-child(1)")
            page.click(".msg.assistant:nth-last-child(1) .rt-dn")
            page.wait_for_function(
                "() => window.__E2E_CALLS__.filter(c => c[0]==='rate_message').length === 2")
            check(page.evaluate(
                "() => { const c = window.__E2E_CALLS__."
                "filter(c => c[0]==='rate_message')[1];"
                " return c[1] === -1 && c[2] === '图书馆几点闭馆'; }"),
                "第二条消息点 👎 记录 -1 与对应问题")

            # ---- 4. 历史里的纯工具调用消息不给操作栏(不能评) ----
            # 直播流的工具卡片不产生 assistant 消息块;trace-only 消息出现在
            # renderHistory(恢复会话)里,所以这里推 resumed 事件验证。
            _push(page, {"type": "resumed", "session": "s1", "messages": [
                {"role": "user", "text": "帮我查点东西"},
                {"role": "assistant", "text": "", "tools": "search_knowledge"},
                {"role": "tool", "text": "共命中 3 条…"},
                {"role": "assistant", "text": "答案是 23:00 断电。"},
            ]})
            page.wait_for_function(
                "() => document.querySelectorAll('.msg.assistant').length === 2")
            verdicts = page.evaluate(
                "() => [...document.querySelectorAll('.msg.assistant')].map(m =>"
                " ({traceOnly: m.classList.contains('trace-only'),"
                "  acts: !!m.querySelector('.acts')}))")
            check(verdicts[0]["traceOnly"] and not verdicts[0]["acts"],
                  f"纯工具调用消息没有操作栏(不可评)({verdicts[0]})")
            check(not verdicts[1]["traceOnly"] and verdicts[1]["acts"]
                  and page.locator(".msg.assistant .rt-up").count() == 1,
                  "历史恢复后有正文的助手消息仍带 👍/👎")

            # ---- 5. bridge-web.js 的 METHODS 静态断言 ----
            shim = (ROOT / "frontend" / "bridge-web.js").read_text(encoding="utf-8")
            check('"rate_message", "rating", "question", "answer"' in shim
                  and '"get_countdowns"' in shim,
                  "网页版 shim 已同步 rate_message / get_countdowns")

            browser.close()
    finally:
        shutdown()

    print()
    if failures:
        print(f"共 {len(failures)} 项失败:")
        for f in failures:
            print("  ✗ " + f)
        return 1
    print("v0.9.14 前端回归:全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
