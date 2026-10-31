# -*- coding: utf-8 -*-
"""嵌套子代理树形时间线的真浏览器验证(v0.9.192,计划书阶段 5)。

载荷直接喂 `renderActivity(data)` —— 数据形状出自 test_activity/test_subagent_steps
里钉的那份(activity.timeline 的输出:行带 children,子行带 lane/depth/note)。
断言的是"界面上真能用":孙层按 depth 缩进、有折叠把手、点一下收起/展开、
父级放弃的后代行显示那句「父级已放弃等待」;**单层载荷一个把手都不多**——
旧形状逐字一致是这一格的验收之一。

桩的边界:`pywebview.api` 整只换掉,boot 一概 {ok:true}(boot 完整性由
e2e_ui_init.py 管);这一支只验渲染半边,数据侧由 tests/test_subagent_steps.py 负责。

跑法(项目根目录):
    PYTHONIOENCODING=utf-8 .venv\\Scripts\\python.exe scripts\\e2e_ui_tree.py
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
window.pywebview = { api: new Proxy({}, { get: () => () => Promise.resolve({ ok: true }) }) };
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

    # 单层:与旧形状逐字一致 —— 有子行、没有折叠把手
    single = {
        "ok": True, "groups": [], "summary": {}, "changes": [],
        "timeline": {"calls": 1, "turns": [{"q": "派一路侦察", "items": [{
            "key": "t:1", "tool": "task", "verb": "派子代理",
            "summary": "派 1 路", "status": "ok", "badge": "",
            "children": [
                {"tool": "explore·grep", "verb": "搜索", "route": "第1路",
                 "title": "上限", "status": "ok", "badge": "",
                 "time": "10:00:01", "dur_text": "3s", "progress": "",
                 "lane": "L2", "depth": 1, "children": [], "children_dropped": 0},
                {"tool": "explore·read", "verb": "读文件", "route": "第1路",
                 "title": "walk.py", "status": "ok", "badge": "",
                 "time": "10:00:02", "dur_text": "2s", "progress": "",
                 "lane": "L3", "depth": 1, "children": [], "children_dropped": 0},
            ],
            "children_dropped": 0}]}]},
    }
    # 嵌套:孙层 + 一个"父级已放弃,后代仍在跑"的行
    nested = {
        "ok": True, "groups": [], "summary": {}, "changes": [],
        "timeline": {"calls": 1, "turns": [{"q": "派嵌套侦察", "items": [{
            "key": "t:1", "tool": "task", "verb": "派子代理",
            "summary": "派 1 路(嵌套)", "status": "ok", "badge": "",
            "children": [
                {"tool": "explore·grep", "verb": "搜索", "route": "第1路",
                 "title": "上限", "status": "running", "badge": "",
                 "time": "10:00:01", "dur_text": "", "progress": "",
                 "lane": "L2", "depth": 1,
                 "note": "父级已放弃等待,后代仍在运行",
                 "children": [
                     {"tool": "explore·read", "verb": "读文件", "route": "第1路",
                      "title": "walk.py", "status": "running", "badge": "",
                      "time": "10:00:02", "dur_text": "", "progress": "",
                      "lane": "L3", "depth": 2, "children": [],
                      "children_dropped": 0}],
                 "children_dropped": 0}],
            "children_dropped": 0}]}]},
    }

    with sync_playwright() as p:
        b = launch(p)
        page = b.new_page()
        page.add_init_script(STUB)
        page.goto("http://127.0.0.1:%d/index.html" % port)
        page.wait_for_function("() => typeof window.renderActivity === 'function'")
        page.wait_for_timeout(200)

        page.evaluate("(d) => window.renderActivity(d)", single)
        check("单层:子行照旧渲染", page.evaluate(
            "() => document.querySelectorAll('.act-kid').length === 2"))
        check("单层:一个折叠把手都不多", page.evaluate(
            "() => document.querySelectorAll('.act-toggle').length === 0"))

        page.evaluate("(d) => window.renderActivity(d)", nested)
        check("嵌套:孙层行渲染出来了", page.evaluate(
            "() => [...document.querySelectorAll('.act-kid')]"
            ".some(r => r.textContent.indexOf('walk.py') >= 0)"))
        check("嵌套:折叠把手出现", page.evaluate(
            "() => document.querySelectorAll('.act-toggle').length === 1"))
        check("父级放弃的那一行带着实话", page.evaluate(
            "() => [...document.querySelectorAll('.act-kid')]"
            ".some(r => r.textContent.indexOf('父级已放弃等待') >= 0)"))
        check("孙层按 depth 缩进", page.evaluate("""() => {
            const kid = [...document.querySelectorAll('.act-kid')]
                .find(r => r.textContent.indexOf('上限') >= 0);
            const grand = [...document.querySelectorAll('.act-kid')]
                .find(r => r.textContent.indexOf('walk.py') >= 0);
            return (parseInt(grand.style.marginLeft) || 0)
                > (parseInt(kid.style.marginLeft) || 0);
        }"""))
        # 面板默认收起,元素在视口外 —— 用 JS 触发点击,断言的是 DOM 事实
        page.evaluate("() => document.querySelector('.act-toggle').click()")
        page.wait_for_timeout(80)
        # textContent 不感知 display:none —— 收起/展开断言的是那个子盒的 computed display
        check("点把手收起孙层", page.evaluate("""() => {
            // 直接子行里含 walk.py 的那个盒 = 最内层;外层盒的 textContent 会穿透
            const boxes = [...document.querySelectorAll('.act-kids')]
                .filter(b => [...b.children].some(c =>
                    c.classList.contains('act-kid')
                    && c.textContent.indexOf('walk.py') >= 0));
            const grand = boxes[boxes.length - 1];
            return !!grand && getComputedStyle(grand).display === 'none';
        }"""))
        page.evaluate("() => document.querySelector('.act-toggle').click()")
        page.wait_for_timeout(80)
        check("再点展开", page.evaluate("""() => {
            const boxes = [...document.querySelectorAll('.act-kids')]
                .filter(b => [...b.children].some(c =>
                    c.classList.contains('act-kid')
                    && c.textContent.indexOf('walk.py') >= 0));
            const grand = boxes[boxes.length - 1];
            return !!grand && getComputedStyle(grand).display !== 'none';
        }"""))
        b.close()
    srv.shutdown()
    print("PASS" if not fails else "FAIL: %s" % fails)
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
