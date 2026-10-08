# -*- coding: utf-8 -*-
"""v0.9.133 确认框那颗「以后都允许」—— 真 Chromium 点一遍。

Python 侧那 44 项针量到的是门的判断与桥的推送字段;**按钮在屏上到底出不出现、
点下去敲的是哪个回话值、它和「本轮总是允许」看起来是不是一样**,只有真浏览器知道。
这一场验的就是那三件事,外加「没读过」那句警示真的落在红字那一行。

要钉的七件事(每条都是界面**可能自己骗人**的那种):
1. 不给 `can_remember` 时那颗按钮必须不可见 —— 出现了却点不成就是样子货;
2. 给了就出现,而且 title 里那句「只记在这台机器上、只对这一个项目」必须在屏上
   (用户点之前就该知道这条授权存在哪、活多久);
3. 点它敲的是 `confirm_reply("remember")`,不是 "always"、也不是 "yes";
4. 它与「本轮总是允许」**手感必须不同**(虚线边框)—— 两颗长得一模一样的按钮,
   误点一次就把一次性的点头变成长期授权;
5. 老三颗照常:拒绝 / 本轮总是允许 / 允许 各敲各的回话值;
6. `alert` 那条升级警示(现在也包括「这份文件本会话没读过」)排第一行且带红字类;
7. 全程零页面错误。

跑法(项目根目录,需要 PYTHONIOENCODING=utf-8 否则中文断言在 GBK 控制台炸):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/e2e_ui_v09133.py
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

UNREAD_NOTE = ("⚠ 这一步要改的 notes.txt 本会话还没读过 —— 学长手上没有它现在的内容,"
               "要替换的那段文字是按印象写的概率不低(改错了会直接落进你的文件)。")

STUB_JS = r"""
window.__CALLS__ = [];
const base = {
  get_info: () => Promise.resolve({
    version: "0.9.133", app_name: "引路学长", permission_mode: "confirm",
    permission_modes: [{value: "confirm", label: "变更前确认"}],
    model: "test-model", active_provider: "test", workspace: "D:/demo/campus",
    workspace_name: "campus", workspace_is_default: true,
    providers: [{id: "test", name: "Test", model: "test-model", has_key: true}],
    api_key_configured: true, memory_count: 0, memory_user_count: 0,
    mcp_servers: [], skills: [], kb_count: 0, sandbox: null, campus_map: null,
    library_health: {}, paths: {app_dir: "D:/demo", writable: true},
    instructions: {ok: true, line: "", layers: []},
    session_metrics: {ok: false, selectable: []}}),
  get_activity: () => Promise.resolve({ok: true, summary: {}, timeline: {},
                                       groups: []}),
  get_monitor_pref: () => Promise.resolve({ok: true, open: false, folded: {}}),
  set_monitor_pref: () => Promise.resolve({ok: true}),
  get_env_info: () => Promise.resolve({ok: true, is_repo: false, files: []}),
  refresh_sessions: () => Promise.resolve({ok: true, items: []}),
  get_recent_projects: () => Promise.resolve({ok: true, current: {}, default: {},
                                              recent: []}),
  get_reasoning: () => Promise.resolve({visible: false, options: [], choice: "default"}),
  get_suggestions: () => Promise.resolve({suggestions: []}),
  confirm_reply: (choice) => {
    window.__CALLS__.push(["confirm_reply", choice]);
    return Promise.resolve({ok: true});
  },
};
window.pywebview = {api: new Proxy(base, {
  get: (t, k) => (k in t) ? t[k]
    : ((...a) => { window.__CALLS__.push([k, ...a]); return Promise.resolve({ok: true}); })
})};
// 直接把一帧推给前端的事件入口,免得为了弹一张确认框去跑一整轮真任务。
// 这跟真机走的是同一条函数(`window.__gcodeEvent`),不是另写一份渲染路径。
window.__ask = (extra) => window.__gcodeEvent(Object.assign({
  type: "confirm_request", tool: "edit_file", detail: "path=notes.txt",
  why: "替换文件里的一段文字", digest: "改 1 处", risks: [], alert: false,
  label: ""}, extra));
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

    def visible(sel) -> bool:
        return page.evaluate(
            "() => { const n = document.getElementById('%s');"
            "return !!n && getComputedStyle(n).display !== 'none'; }" % sel)

    def replies():
        return [c[1] for c in page.evaluate("window.__CALLS__")
                if c and c[0] == "confirm_reply"]

    with sync_playwright() as p:
        browser = launch(p)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.add_init_script(STUB_JS)
        page.goto(url)
        page.wait_for_selector("#ver", timeout=15000)

        # ---- 1. 没给 can_remember(以及旧版推送压根没这个字段)→ 按钮不出现 ----
        page.evaluate("() => __ask({})")
        page.wait_for_selector("#confirmOverlay.show", timeout=5000)
        check(not visible("btnRemember"),
              "不给 can_remember 时「以后都允许」不出现(缺字段按不给处理)")
        page.evaluate("() => document.getElementById('confirmOverlay')"
                      ".classList.remove('show')")

        # ---- 2. 给了 → 出现,且那句"记在哪、活多久"在屏上 ----
        page.evaluate("() => __ask({can_remember: true})")
        page.wait_for_selector("#confirmOverlay.show", timeout=5000)
        check(visible("btnRemember"), "can_remember=true 时那颗按钮真在屏上")
        tip = page.get_attribute("#btnRemember", "title") or ""
        check("这台机器" in tip and "项目" in tip,
              f"按钮的说明写清了记在哪(实测:{tip[:28]}…)")
        check(page.inner_text("#btnRemember").strip() == "以后都允许",
              "按钮文案是「以后都允许」,不是含糊的「记住」")

        # ---- 4. 与「本轮总是允许」的手感必须不同(虚线 vs 实线) ----
        style_r = page.evaluate(
            "() => getComputedStyle(document.getElementById('btnRemember')).borderStyle")
        style_a = page.evaluate(
            "() => getComputedStyle(document.getElementById('btnAlways')).borderStyle")
        check(style_r == "dashed" and style_a == "solid",
              f"两颗按钮手感不同(以后都允许 {style_r} / 本轮总是允许 {style_a})")

        # ---- 3. 点它敲的是 remember,框收起 ----
        page.click("#btnRemember")
        page.wait_for_timeout(150)
        check(replies() == ["remember"], f"点下去敲的是 confirm_reply('remember')(实测 {replies()})")
        check(not page.evaluate("() => document.getElementById('confirmOverlay')"
                               ".classList.contains('show')"),
              "点完确认框收起(留着就成了第二张等人点的卡)")

        # ---- 5. 老三颗照常,各敲各的 ----
        for sel, want in (("btnNo", "no"), ("btnAlways", "always"), ("btnYes", "yes")):
            page.evaluate("() => __ask({can_remember: true})")
            page.wait_for_selector("#confirmOverlay.show", timeout=5000)
            page.click("#" + sel)
            page.wait_for_timeout(120)
            check(replies()[-1] == want, f"{sel} 仍回 {want}")

        # ---- 6. 「没读过」那句警示排在第一行且是红字 ----
        page.evaluate("(note) => __ask({can_remember: true, alert: true,"
                      " risks: [note, '会改动磁盘上的文件']})", UNREAD_NOTE)
        page.wait_for_selector("#confirmOverlay.show", timeout=5000)
        first = page.evaluate(
            "() => { const n = document.querySelector('#confirmWhyRisk span');"
            "return n ? [n.textContent, n.className] : ['', '']; }")
        check("没读过" in (first[0] or ""), "警示第一行说的是「没读过」那句")
        check("confirm-risk" in (first[1] or ""), "那一行带红字类(不是混在普通风险里)")
        check(visible("btnRemember"),
              "升级警示与「以后都允许」可以同时出现(一句讲原因、一颗给选择)")

        check(not errors, f"整场零页面错误(实测 {errors[:2]})")
        browser.close()
    shutdown()
    print()
    if failures:
        print(f"FAIL: {len(failures)}/{ran} 项不对 → " + "; ".join(failures))
        return 1
    print(f"PASS: v0.9.133 确认框那颗按钮 {ran} 项全对(真 Chromium 点过)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
