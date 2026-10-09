# -*- coding: utf-8 -*-
"""卡顿取证:一段真回答流进来时,时间到底花在哪。

为什么要有这一场:用户报"使用中有明显的卡顿感",而这句话有好几个可能的病灶
(每增量一次 JS 往返 / markdown 整段重排 / 每次渲染都滚到底 / 每次增量都去折叠
所有 trace)。凭印象挑一个去优化,大概率是把第二贵的当第一贵。

这一场把真前端(同一份 index.html、同一条 `__gcodeEvent` 入口)开在真 Chromium 里,
量四个数:

  A. 一发一往返:600 条 text_delta 每条一次 page.evaluate —— 对应今天
     `GuiUI.push` 每个增量一次 `window.evaluate_js` 的形状;
  B. 一次往返喂 600 条:同样的内容,循环放在 JS 里面 —— 差值就是"往返本身"的代价;
  C. 增量渲染的分项耗时:renderMd / toBottom / foldAllTraces 各占多少;
  D. 越答越慢的那条曲线:前 50 条 vs 后 50 条增量的单条耗时 —— 整段重排是
     O(n²) 的,这一段量出来的是"答到后面为什么开始卡"。

跑法(项目根目录):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/e2e_ui_lag.py
"""
from __future__ import annotations

import sys
import threading
import time
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

STUB_JS = r"""
window.__CALLS__ = [];
const base = {
  get_info: () => Promise.resolve({
    version: "0.9.139", app_name: "引路学长", permission_mode: "confirm",
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
window.__ev = (o) => window.__gcodeEvent(o);
/* 最后一条助手消息:不能用 `.msg.assistant:last-of-type` —— `:last-of-type` 说的是
   "同类型兄弟里的最后一个",占位/工具卡那些 div 排在后面时它就不匹配了,量出来的
   会是另一条消息(我自己先被它骗过一次:600 条增量之后读到"37 字")。 */
window.__last = () => {
  const a = document.querySelectorAll(".msg.assistant");
  return a.length ? a[a.length - 1] : null;
};
/* 分项计时不靠 monkey-patch:index.html 是经典脚本,`function renderMd(){}` 建的是
   全局词法绑定,`window.renderMd = …` 盖不住它 —— 所以这里直接按名字调用被测函数,
   量"调用它一次要多久",而不是偷偷换掉它。 */
window.__bench = (name, arg, reps) => {
  const fn = window[name];
  if (typeof fn !== "function") return {missing: name};
  const t0 = performance.now();
  for (let i = 0; i < reps; i++) fn(arg);
  return {ms: performance.now() - t0, reps: reps, name: name};
};
"""

N = 600
# 一条真回答的形状:中文 + 列表 + 代码块 + 表格,越接近真实用量越能暴露整段重排的代价。
PIECE = "这一项要注意的地方不少,来看下面这段:\n- 第一点\n- 第二点\n"


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
    with sync_playwright() as pw:
        browser = launch(pw)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(STUB_JS)
        page.goto(url)
        page.wait_for_selector("#ver", timeout=15000)
        page.wait_for_function("() => !!window.__gcodeEvent")

        def start_answer():
            page.evaluate("() => window.__gcodeEvent({type:'user', text:'帮我算一下'})")

        # ── A. 一发一往返(今天的形状:每个增量一次 evaluate_js)
        start_answer()
        t0 = time.perf_counter()
        for i in range(N):
            page.evaluate("(o) => window.__ev(o)", {"type": "text_delta", "text": PIECE})
        a_wall = (time.perf_counter() - t0) * 1000.0
        page.evaluate("() => window.__ev({type:'finish_stream'})")

        # ── B. 一次往返喂完 600 条(批处理的形状)
        start_answer()
        t0 = time.perf_counter()
        page.evaluate("""([n, piece]) => { for (let i = 0; i < n; i++)
                          window.__ev({type:'text_delta', text: piece}); }""", [N, PIECE])
        b_wall = (time.perf_counter() - t0) * 1000.0
        page.evaluate("() => window.__ev({type:'finish_stream'})")
        page.wait_for_timeout(200)

        # ── C. 三个嫌疑犯各自的单价(在 B 那一轮那份**长回答**上量)
        final_text = page.evaluate(
            "() => { const m = window.__last(); return m ? (m.dataset.raw || '') : ''; }")
        bench = page.evaluate("""([t]) => ({
            renderMd: __bench('renderMd', t, 30),
            toBottom: __bench('toBottom', undefined, 30),
            foldAllTraces: __bench('foldAllTraces', undefined, 30),
        })""", [final_text])

        # ── D. 越答越慢:同一套 renderMd 在不同长度下的单价
        curve = page.evaluate("""([t]) => {
            const lens = [200, 1000, 3000, 6000, 9000];
            return lens.map(n => ({n: n, one: __bench('renderMd', t.slice(0, n), 10).ms / 10}));
        }""", [final_text + PIECE * 200])
        text_len = len(final_text)

        # ── E. 攒批那一半在前端真的接得住:`__gcodeEvents` 必须在场且把整批落到屏上。
        #       缺这只函数的坏法是不报错的 —— push 里 evaluate_js 抛异常被那层
        #       try/except 吞掉,于是流式正文一个字都不出现。
        batched = page.evaluate("""([piece]) => {
            window.__gcodeEvent({type:'user', text:'再问一句'});
            window.__gcodeEvents([{type:'text_delta', text: piece},
                                  {type:'text_delta', text: '收尾那半句'}]);
            window.__gcodeEvent({type:'finish_stream'});
            const m = window.__last();
            return m ? (m.dataset.raw || '') : '';
        }""", [PIECE])
        ok_batch = ("收尾那半句" in batched) and (PIECE in batched)
        print("E 一批事件落到屏上      :" + ("✓" if ok_batch else "✗ __gcodeEvents 没接住"))
        if not ok_batch:
            errors.append("__gcodeEvents 没把整批渲染出来")

        print(f"回答正文长度:{text_len} 字")
        print(f"A 一发一往返 {N} 条      :{a_wall:8.1f} ms  ({a_wall/N:.2f} ms/条)")
        print(f"B 一次往返喂 {N} 条      :{b_wall:8.1f} ms  ({b_wall/N:.2f} ms/条)")
        if a_wall > 0:
            print(f"  → 往返本身的代价      :{a_wall-b_wall:8.1f} ms"
                  f"  (占 A 的 {(a_wall-b_wall)/a_wall*100:.0f}%)")
        print("C 单价(调用一次要多久,长度 %d 字):" % text_len)
        for key in ("renderMd", "toBottom", "foldAllTraces"):
            got = bench.get(key) or {}
            if got.get("missing"):
                print(f"    {key:14s}:这个名字在全局拿不到(不是 function)")
            else:
                print(f"    {key:14s}:{got['ms']/got['reps']:.3f} ms")
        print("D renderMd 单价随长度:")
        base = curve[0]["one"] or 0.001
        for row in curve:
            print(f"    {row['n']:5d} 字:{row['one']:.3f} ms   ({row['one']/base:.1f}×)")
        print("页面错误:" + ("无" if not errors else " / ".join(errors[:3])))
        browser.close()
    shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
