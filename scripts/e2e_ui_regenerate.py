# -*- coding: utf-8 -*-
"""前端回归:回答下面那颗 ↻「重新生成」真的能把旧答复撤下来、把新答复接到新气泡里。

同学的原话是「答错了只能干看着」:后端有 `edit_resend`(换文本重发),但**没有"原样再
答一次"这个动作**,前端也没有入口。这一轮补的是 `GuiBridge.regenerate()` + 一颗按钮。
`scripts/check_frontend_js.py` 那种 `node --check` 只保证语法不成,而这条线上最容易断的
是三件运行时的事,所以全部真点击:

1. ↻ 与 👍/👎 并列在同一个 `.rate` 里(评过分的也照样能重答);
2. 桥回 `ok:false`(有任务在跑)时:**旧答复留在原地**,不把它悄悄摘掉再告诉用户失败了;
3. 桥回 `ok:true` 时:旧答复整条摘掉,随后的 `text_delta` 流进一条**新**气泡 ——
   这一条是本场的主案:`ensureAssistant()` 认的是模块级 `cur`,不重置它就会把新答案
   写进一个已经 `remove()` 下来的节点里,用户看到的是"点了没反应"。所有 e2e 全绿而
   界面空白一整轮,是这仓库真踩过的坑(v0.9.27 地图);
4. 出了第二条回答以后,第一条的 ↻ 关掉 —— 拿它重答会把最后一段一起丢掉。

跑法(项目根目录):
    PYTHONIOENCODING=utf-8 .venv\\Scripts\\python.exe scripts\\e2e_ui_regenerate.py
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
window.__REGEN_CALLS__ = 0;
window.__REGEN_RESULT__ = {ok: true, session: "s1"};
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
  send_task: () => Promise.resolve({ok: true, session: "s1"}),
  rate_message: () => Promise.resolve({ok: true}),
  /* 桥只回一句话,答什么由这场测试当场改 __REGEN_RESULT__ 决定。 */
  regenerate: () => {
    window.__REGEN_CALLS__ += 1;
    return Promise.resolve(window.__REGEN_RESULT__);
  },
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

        def answer(text):
            """走真输入框发问,再推两条真事件把这段答复流出来。"""
            page.fill("#input", f"问题:{text}")
            page.click("#btnSend")
            page.evaluate("t => window.__gcodeEvent({type:'text_delta', text: t})", text)
            page.evaluate("() => window.__gcodeEvent({type:'finish_stream'})")
            page.wait_for_timeout(220)

        def count(sel):
            return page.evaluate(f"() => document.querySelectorAll('{sel}').length")

        def clickable(sel):
            return page.evaluate(
                """() => { const b = document.querySelector('%s');
                           return !!b && !b.disabled; }""" % sel)

        def click_act(btn_sel, which="last"):
            """真点击最后一条消息上的某颗动作按钮。

            整条动作栏平时 `visibility:hidden`,悬停到那条消息上才显形
            (CSS `.acts{visibility:hidden}` + `.msg:hover .acts`)—— 所以先悬停再点。
            这不是绕开可见性检查:真用户就是悬停之后才点得到。
            """
            msg = page.locator(".msg.assistant").last if which == "last" \
                else page.locator(".msg.assistant").first
            msg.hover()
            msg.locator(btn_sel).click()
            page.wait_for_timeout(180)

        # ---- 1) 按钮在场,且与 👍/👎 同在一组
        answer("第一版答复")
        check(count(".msg.assistant") == 1, "① 答复真的成了一条气泡")
        check(count(".rate .rt-re") == 1, "① ↻ 与 👍/👎 并列在同一个 .rate 里")
        check(clickable(".msg.assistant .rt-re"), "① 刚答完的这条可以重答")

        # ---- 2) 桥拒绝:旧答复必须留在原地
        page.evaluate("() => { window.__REGEN_RESULT__ = {ok:false, msg:'当前会话有任务正在进行中'}; }")
        click_act(".rt-re")
        check(page.evaluate("window.__REGEN_CALLS__") == 1, "② 点下去真叫到了桥的 regenerate")
        check(count(".msg.assistant") == 1 and count(".msg.assistant .body") == 1,
              "② 桥说失败时旧答复没被摘掉(不能先删再报错)")
        check(clickable(".msg.assistant .rt-re"), "② 失败后按钮不卡在 pending,还能再点")

        # ---- 3) 桥接受:旧的整条撤下,新答复进**新**气泡
        page.evaluate("() => { window.__REGEN_RESULT__ = {ok:true, session:'s1'}; }")
        click_act(".rt-re")
        check(count(".msg.assistant") == 0, "③ 旧答复从对话里撤下了")
        check(count(".msg.user") == 1, "③ 那句提问留着:重答不是撤回问题")
        page.evaluate("t => window.__gcodeEvent({type:'text_delta', text: t})", "第二版答复")
        page.evaluate("() => window.__gcodeEvent({type:'finish_stream'})")
        page.wait_for_timeout(260)
        check(count(".msg.assistant") == 1, "③ 新答复成了一条**新**气泡(不是写进已摘掉的节点)")
        body = page.evaluate("() => document.querySelector('.msg.assistant .body').textContent")
        check("第二版答复" in body and "第一版答复" not in body,
              f"③ 屏幕上是新那一版:{body[:24]}")

        # ---- 4) 第二条回答出来以后,第一条的 ↻ 关掉
        answer("第三版答复")
        check(count(".msg.assistant") == 2, "④ 又答了一轮,现在两条")
        first_ok = page.evaluate(
            """() => { const a = document.querySelectorAll('.msg.assistant');
                       return !a[0].querySelector('.rt-re').disabled; }""")
        last_ok = page.evaluate(
            """() => { const a = document.querySelectorAll('.msg.assistant');
                       return !a[a.length-1].querySelector('.rt-re').disabled; }""")
        check(first_ok is False and last_ok is True,
              f"④ 只有最后一条能重答(第一条 {first_ok} / 最后一条 {last_ok})")

        # ---- 5) 点过 👎 的那条照样能重答(不满意才更要重答)
        click_act(".rt-dn")
        calls = page.evaluate("window.__REGEN_CALLS__")
        click_act(".rt-re")
        check(page.evaluate("window.__REGEN_CALLS__") == calls + 1,
              "⑤ 评过 👎 之后 ↻ 仍然点得动")

        page.close()
        browser.close()

    real_errors = [e for e in errors if "favicon" not in e
                   and "Failed to load resource" not in e]
    check(not real_errors, f"控制台无报错(拿到 {len(real_errors)} 条){real_errors[:3]}")

    shutdown()
    print()
    if failures:
        for f in failures:
            print("FAIL:", f)
        print(f"\n{len(failures)} 项不通过")
        return 1
    print("PASS: ↻ 五条路径都通 —— 拒了不删旧的、成了换新气泡、只开最后一条、评过分也能重答")
    return 0


if __name__ == "__main__":
    sys.exit(main())
