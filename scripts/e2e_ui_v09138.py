# -*- coding: utf-8 -*-
"""v0.9.138 这一轮两件"看得懂输出"—— 真 Chromium 点开一遍:思考那块 + 数学公式。

为什么这一场必须是真浏览器:Python 侧能证明 `reasoning_delta` 推到了 JS、也能证明
`.th-body` 里写了字,但**用户看不看得见**取决于 CSS 那条 `.think.open .th-body{display:block}`
有没有人给上 `.open`。v0.9.137 之前的形状正是"字都写进了 DOM,而那块永远 display:none" ——
读源码看不出来,截图才看得出来(2026-10-09 用户就是拿截图指这件事的)。

要钉的十件事:
1. 第一条思考增量之后那块**在屏上摊开**(不是 hidden、也不是折叠着);
2. 思考正文真的看得见(不是只有"进行中…"那一行);
3. 实时那一行写的是「思考中 N 秒 · 约 X token/秒」——**带「约」字**,
   token 数在服务端回报 usage 之前只能按字符估,拿估算冒充实测是这一族最坏的骗法;
4. 那一行会往前走(计时器真在跑);
5. 点头部能折叠;
6. 再点能展开;
7. 用户手动开合过之后,正文开始输出时**不擅自**收起它;
8. 没碰过的情况下,答完自动收成一行,且那一行定稿成「已思考 …」;
9. 回合结束后计时器自己死掉(节点还在,字不再变 —— 僵尸计时器会一直往没人看的节点上写);
10. 全程零页面错误。

公式要钉的八件事(11-18):
11. 行内与块级都认成公式;12. KaTeX 真把它们画出来(`.katex` 出现);
13. 屏上不再是 `\\boxed` 这种源码;14. `$s_1$ 与 $s_2$` 中间那对下划线没被 markdown 抢成斜体;
15. **价格里的 `$` 不算公式**("$5 和 $10" 不许变成一条式子);
16. **行内代码与代码块里的 `$` 不算公式**(那是用户要照抄的原文);
17. 交给 KaTeX 之前原文就在 span 里,解不开的那条退回成源码可见 ——
    "渲染失败变成看不见公式"是这个功能最坏的一种坏法,所以宁可留源码;
18. 全程零页面错误。

跑法(项目根目录):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/e2e_ui_v09138.py
"""
from __future__ import annotations

import sys
import threading
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

from e2e_launch import launch          # noqa: E402  起浏览器这件事的一份真相

STUB_JS = r"""
window.__CALLS__ = [];
const base = {
  get_info: () => Promise.resolve({
    version: "0.9.138", app_name: "引路学长", permission_mode: "confirm",
    permission_modes: [{value: "confirm", label: "变更前确认"}],
    model: "test-model", active_provider: "test", workspace: "D:/demo/campus",
    workspace_name: "campus", workspace_is_default: true,
    providers: [{id: "test", name: "Test", model: "test-model", has_key: true}],
    api_key_configured: true, memory_count: 0, memory_user_count: 0,
    mcp_servers: [], skills: [], kb_count: 0, sandbox: null, campus_map: null,
    library_health: {}, paths: {app_dir: "D:/demo", writable: true},
    instructions: {ok: true, line: "", layers: []},
    session_metrics: {ok: false, selectable: []}}),
  get_activity: () => Promise.resolve({ok: true, summary: {}, timeline: {}, groups: []}),
  get_monitor_pref: () => Promise.resolve({ok: true, open: false, folded: {}}),
  set_monitor_pref: () => Promise.resolve({ok: true}),
  get_env_info: () => Promise.resolve({ok: true, is_repo: false, files: []}),
  refresh_sessions: () => Promise.resolve({ok: true, items: []}),
  get_recent_projects: () => Promise.resolve({ok: true, current: {}, default: {}, recent: []}),
  get_reasoning: () => Promise.resolve({visible: false, options: [], choice: "default"}),
  get_suggestions: () => Promise.resolve({suggestions: []}),
};
window.pywebview = {api: new Proxy(base, {
  get: (t, k) => (k in t) ? t[k]
    : ((...a) => { window.__CALLS__.push([k, ...a]); return Promise.resolve({ok: true}); })
})};
// 与真机同一条入口:一帧事件直接喂给 __gcodeEvent,不另写一份渲染路径。
window.__ev = (o) => window.__gcodeEvent(o);
window.__think = () => {
  const t = document.querySelector(".msg.assistant:last-of-type .think");
  const b = t && t.querySelector(".th-body");
  const h = t && t.querySelector(".th-time");
  return {
    exists: !!t, hidden: !t || t.hidden, open: !!t && t.classList.contains("open"),
    touched: !!(t && t.dataset.touched),
    body: b ? b.textContent : "",
    bodyShown: !!b && getComputedStyle(b).display !== "none",
    time: h ? h.textContent : ""
  };
};
"""

REASON_A = "我需要先把这道题摆清楚:有四色水杯,上下两层各四个。"
REASON_B = "上层对挑战者可见,所以下层可以重标成恒等排列再讨论。"


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

        # ---- 1-3. 思考增量进来:那块摊开、正文看得见、那一行带「约」----
        page.evaluate("(t) => __ev({type:'reasoning_delta', text: t})", REASON_A)
        page.wait_for_timeout(120)
        page.evaluate("(t) => __ev({type:'reasoning_delta', text: t})", REASON_B)
        page.wait_for_timeout(120)
        th = page.evaluate("() => __think()")
        check(th["exists"] and not th["hidden"], "思考中那块在屏上(不是 hidden)")
        check(th["open"] and th["bodyShown"],
              "思考中那块是**摊开**的,正文可见(旧形状:字在 DOM 里却 display:none)")
        check(REASON_A[:8] in th["body"] and REASON_B[:8] in th["body"],
              f"两段思考正文都看得见(实测 {th['body'][:24]}…)")
        check(th["time"].startswith("思考中") and "秒" in th["time"]
              and "约" in th["time"] and "token/秒" in th["time"],
              f"实时那一行是「思考中 N 秒 · 约 X token/秒」(实测:{th['time']})")

        # ---- 4. 那一行会往前走 ----
        # 比的是**秒数这个数**,不是整串字:秒数被 max(1, round(…)) 夹过,
        # 等 1.2 秒可能两次都落在"1 秒"上(我自己就是这么把它跑成一红 —— 判据错,不是产品错)。
        def think_secs() -> float:
            import re
            m = re.search(r"(\d+(?:\.\d+)?)", page.evaluate("() => __think()['time']"))
            return float(m.group(1)) if m else -1

        s0 = think_secs()
        page.wait_for_timeout(2000)
        s1 = think_secs()
        check(s1 > s0, f"计时器真在跑(思考秒数 {s0} → {s1})")

        # ---- 5/6. 点头部折叠再展开 ----
        page.evaluate("() => document.querySelector('.msg.assistant:last-of-type "
                      ".think .th-head').click()")
        page.wait_for_timeout(80)
        th = page.evaluate("() => __think()")
        check((not th["open"]) and (not th["bodyShown"]) and th["touched"],
              "点一下头部能折叠(且记下'被人手动开合过')")
        page.evaluate("() => document.querySelector('.msg.assistant:last-of-type "
                      ".think .th-head').click()")
        page.wait_for_timeout(80)
        th = page.evaluate("() => __think()")
        check(th["open"] and th["bodyShown"], "再点一下又展开,正文重新可见")

        # ---- 7. 手动开合过 → 正文开始输出时不擅自收起 ----
        page.evaluate("(t) => __ev({type:'text_delta', text: t})", "答案是 8。")
        page.wait_for_timeout(120)
        th = page.evaluate("() => __think()")
        check(th["open"] and th["bodyShown"],
              "用户自己点开着的时候,开始输出正文不会把它收走")
        check(th["time"].startswith("已思考"),
              f"思考结束那一行定稿成「已思考 …」(实测:{th['time']})")
        page.evaluate("() => __ev({type:'finish_stream'})")
        page.wait_for_timeout(120)

        # ---- 8. 没碰过的新一轮:答完自动收成一行 ----
        page.evaluate("(t) => __ev({type:'reasoning_delta', text: t})", REASON_A)
        page.wait_for_timeout(120)
        check(page.evaluate("() => __think()['open']"), "新一轮里那块默认还是摊开的")
        page.evaluate("(t) => __ev({type:'text_delta', text: t})", "重答一次。")
        page.wait_for_timeout(120)
        th = page.evaluate("() => __think()")
        check((not th["open"]) and (not th["bodyShown"]) and (not th["touched"]),
              "没人碰过就自动收成一行(不占地方)")
        page.evaluate("() => __ev({type:'finish_stream'})")
        page.wait_for_timeout(120)

        # ---- 9. 回合结束后计时器自己死 ----
        done = page.evaluate("() => __think()['time']")
        page.wait_for_timeout(1300)
        check(page.evaluate("() => __think()['time']") == done,
              f"回合结束后那一行不再变(僵尸计时器已清:{done})")

        check(not errors, f"整场零页面错误(实测 {errors[:2]})")

        # ================= 数学公式(KaTeX)=================
        # 这一族验的是"输出到底是渲染过的公式,还是 LaTeX 源码",以及两种
        # "看着像公式"的东西**不该**被当成公式:价格里的 $ 与代码块里的 $。
        page.evaluate("() => __ev({type:'cleared'})")
        page.wait_for_timeout(120)
        page.evaluate("(t) => __ev({type:'text_delta', text: t})",
                      "几个最坏情况:$V(W)=6$ 与 $s_1$ 和 $s_2$ 都要成式。\n\n"
                      "$$\\boxed{W=\\max(7,8,7)=8}$$\n")
        page.evaluate("() => __ev({type:'finish_stream'})")
        page.wait_for_timeout(2500)          # 第一次要等 vendor/katex 拉下来
        math = page.evaluate("""() => {
          const body = document.querySelector('.msg.assistant:last-of-type .body');
          const kx = body ? body.querySelectorAll('.katex') : [];
          return { spans: body ? body.querySelectorAll('span.math').length : 0,
                   rendered: kx.length,
                   text: body ? body.textContent : '',
                   em: body ? body.querySelectorAll('em').length : 0 };
        }""")
        check(math["spans"] >= 3,
              f"三条公式都认成了公式(实测 {math['spans']} 个 span.math)")
        check(math["rendered"] >= 3,
              f"KaTeX 真把它们画出来了(实测 {math['rendered']} 个 .katex)")
        check("\\boxed" not in math["text"] and "max" in math["text"],
              "屏上不再是 \\boxed 这种源码,而是渲染后的式子")
        check(math["em"] == 0,
              f"$s_1$ 与 $s_2$ 之间那对下划线没被 markdown 吃成斜体(<em> {math['em']} 个)")

        # ---- 两种"不该当公式"的 ----
        cur = page.evaluate("(t) => renderMd(t)", "报名费 $5 和材料费 $10 都要交。")
        check("span class=\"math" not in cur,
              f"价格里的 $ 没被当成公式(实测片段:{cur.strip()[:36]})")
        code = page.evaluate("(t) => renderMd(t)",
                             "示例 `$x_1$` 与代码块:\n\n```\n$V(W)=6$\n```\n")
        check("span class=\"math" not in code,
              "行内代码与代码块里的 $ 不算公式(那是用户要照抄的原文)")

        # ---- 拉不起来/解不开时留原文,不留空白 ----
        raw = page.evaluate("(t) => renderMd(t)", "$\\frac{a}{b}$ 与 $x$")
        check("span class=\"math" in raw and "frac{a}{b}" in raw,
              "公式在交给 KaTeX 之前,原文就在 span 里(渲染失败时屏上就是它)")
        page.evaluate("(t) => __ev({type:'text_delta', text: t})",
                      "$\\notacommand{x}$ 这条解不开。")
        page.evaluate("() => __ev({type:'finish_stream'})")
        page.wait_for_timeout(700)
        badt = page.evaluate("""() => {
          const b = document.querySelector('.msg.assistant:last-of-type .body');
          return b ? b.textContent : '';
        }""")
        check("notacommand" in badt,
              "解不开的公式退回成源码可见(不是一片空白,也不是一句报错)")

        check(not errors, f"公式那一段之后仍然零页面错误(实测 {errors[:2]})")
        browser.close()
    shutdown()
    print()
    if failures:
        print(f"FAIL: {len(failures)}/{ran} 项不对 → " + "; ".join(failures))
        return 1
    print(f"PASS: v0.9.138 思考那块 + 数学公式 {ran} 项全对(真 Chromium 点过)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
