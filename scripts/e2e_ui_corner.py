# -*- coding: utf-8 -*-
"""四角对角线缩放的真浏览器验证(v0.9.193)。

修的 bug:原 CSS 把 `left:0;right:0` 一起设给了 ne/se,过度约束下 left 赢过
right —— **ne 渲染在左上角把 nw 盖住、se 渲染在左下角把 sw 盖住**,表现是
"左上拖动触发的是 ne(方向错)、右侧两角没有角条(退回单边)"。用户原话:
"不能把鼠标放到应用的四个角进行缩放"。

断言四件事:①四角的 `elementFromPoint` 都落在**正确的** `.rz-c` 上;
②四个角条摆在四个真正的角上(不是叠在左边);③逐角模拟按下-拖动-抬起,
`win_resize` 收到的方向与该角一致且 dx/dy 都非零(对角线 = 两条边同时动);
④上边中点仍是 "n"(边的形状没被波及)。光标样式顺带验。
真机 Win32 那半(`window_chrome.resize_edge` → SetWindowPos)是纯算术,
由 tests/test_window_chrome.py 的 `_apply_edge` 针负责,这里不重复。

桩的边界:`win_state` 回 `borderless:true`(真机无边框时 `#rz` 才放开 ——
不回的话把手整层 hidden,那是桩的假象不是产品),`win_resize` 只记账不真动窗口。

跑法(项目根目录):
    PYTHONIOENCODING=utf-8 .venv\\Scripts\\python.exe scripts\\e2e_ui_corner.py
"""
from __future__ import annotations

import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

STUB = r"""
window.__rzCalls = [];
window.pywebview = { api: new Proxy({}, { get: (t, k) => {
  if (k === "win_state") {
    return () => Promise.resolve({ ok: true, borderless: true, maximized: false });
  }
  if (k === "win_resize") {
    return (edge, dx, dy) => {
      window.__rzCalls.push([edge, dx, dy]);
      return Promise.resolve({ ok: true });
    };
  }
  return () => Promise.resolve({ ok: true });
} }) };
"""


def serve():
    class H(SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(FRONTEND), **k)

        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(H))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main() -> int:
    srv = serve()
    port = srv.server_address[1]
    from playwright.sync_api import sync_playwright
    from e2e_launch import launch
    fails: list[str] = []

    def check(name: str, cond: bool):
        print(("  ✓ " if cond else "  ✗ ") + name)
        if not cond:
            fails.append(name)

    with sync_playwright() as p:
        b = launch(p)
        page = b.new_page(viewport={"width": 1200, "height": 800})
        page.add_init_script(STUB)
        page.goto("http://127.0.0.1:%d/index.html" % port)
        page.wait_for_function(
            "() => { const r = document.querySelector('.rz');"
            " return r && !r.hidden; }")
        page.wait_for_timeout(200)

        # ① 四角命中:elementFromPoint 落在**正确的**角条上
        hits = page.evaluate("""() => {
            const w = window.innerWidth, h = window.innerHeight;
            const probe = (x, y) => {
                const el = document.elementFromPoint(x, y);
                return el ? el.dataset.e || el.className : "(null)";
            };
            return { nw: probe(2, 2), ne: probe(w - 2, 2),
                     sw: probe(2, h - 2), se: probe(w - 2, h - 2),
                     n: probe(w / 2, 2) };
        }""")
        check("左上/右上/左下/右下各命中自己的角条", hits ==
              {"nw": "nw", "ne": "ne", "sw": "sw", "se": "se", "n": "n"})

        # ② 四个角条摆在四个真正的角上(修前 ne/se 叠在左边,rect 0x0 或同位)
        rects = page.evaluate("""() => {
            const w = window.innerWidth, h = window.innerHeight;
            const out = {};
            document.querySelectorAll('.rz-c').forEach(c => {
                const r = c.getBoundingClientRect();
                out[c.dataset.e] = [Math.round(r.left), Math.round(r.top),
                                    Math.round(r.width), Math.round(r.height)];
            });
            return out;
        }""")
        check("四个角条各就各位(14×14)", rects == {
            "nw": [0, 0, 14, 14], "ne": [1186, 0, 14, 14],
            "sw": [0, 786, 14, 14], "se": [1186, 786, 14, 14]})
        cursors = page.evaluate("""() => {
            const out = {};
            document.querySelectorAll('.rz-c').forEach(c => {
                out[c.dataset.e] = getComputedStyle(c).cursor;
            });
            return out;
        }""")
        check("光标是对角线形状", cursors == {
            "nw": "nwse-resize", "ne": "nesw-resize",
            "sw": "nesw-resize", "se": "nwse-resize"})

        # ③ 逐角拖:方向与角一致,dx/dy 都非零(对角线 = 两条边同时动)
        for name, (mx, my) in (("nw", (3, 3)), ("ne", (1196, 3)),
                               ("sw", (3, 796)), ("se", (1196, 796))):
            page.evaluate("() => { window.__rzCalls = []; }")
            page.mouse.move(mx, my)
            page.mouse.down()
            page.mouse.move(mx + 40, my + 25, steps=3)
            page.mouse.up()
            page.wait_for_timeout(120)
            calls = page.evaluate("() => window.__rzCalls")
            ok = (calls and calls[0][0] == name
                  and all(c[0] == name for c in calls)
                  and all(c[1] != 0 and c[2] != 0 for c in calls))
            check("拖 %s 角 → win_resize 收到的全是 %s 且 dx/dy 非零" % (name, name), ok)

        # ④ 边没被波及:上边中点仍是 n
        page.evaluate("() => { window.__rzCalls = []; }")
        page.mouse.move(600, 2)
        page.mouse.down()
        page.mouse.move(600, 30, steps=3)
        page.mouse.up()
        page.wait_for_timeout(120)
        calls = page.evaluate("() => window.__rzCalls")
        check("上边中点仍是 n(边不波及)", bool(calls)
              and all(c[0] == "n" for c in calls))
        b.close()
    srv.shutdown()
    print("PASS" if not fails else "FAIL: %s" % fails)
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
