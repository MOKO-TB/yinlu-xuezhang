# -*- coding: utf-8 -*-
"""v0.9.17 前端回归:临期待办/新版本推送 + mermaid 图表渲染 + 通知解读入库引导。

跑法(项目根目录):
    .venv\\Scripts\\python.exe scripts\\e2e_ui_v0917.py
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

STUB_JS = """
window.__E2E_CALLS__ = [];
window.__stubReady = true;
window.pywebview = {
  api: {
    get_info: () => Promise.resolve({
      version: "0.9.17", app_name: "引路学长",
      permission_mode: "confirm",
      permission_modes: [{value: "confirm", label: "变更前确认"}],
      session_key: "s1", model: "deepseek-v4-flash", active_provider: "gzasc",
      providers: [{id: "gzasc", name: "广州应用科技学院",
                   model: "deepseek-v4-flash", has_key: true}],
      api_key_configured: true, workspace: "D:/demo/yhbz",
      workspace_name: "yhbz", workspace_is_default: false,
      memory_count: 0, memory_user_count: 0, mcp_servers: [],
      sandbox: {}, vision: false, campus_map: null, library_health: {},
      skills: [], kb_count: 0,
      session_metrics: {ok: false, selectable: []}
    }),
    set_notice_watch: () => Promise.resolve({ok: true}),
    open_url: (url) => { window.__E2E_CALLS__.push(["open_url", url]);
      return Promise.resolve({ok: true}); },
    send_task: () => Promise.resolve({ok: true, session: "s1"}),
    get_suggestions: () => Promise.resolve({suggestions: []}),
    get_countdowns: () => Promise.resolve({ok: true, countdowns: []}),
    rate_message: () => Promise.resolve({ok: true}),
    compact_now: () => Promise.resolve({ok: true}),
    refresh_sessions: () => Promise.resolve({ok: true}),
    get_recent_projects: () => Promise.resolve({ok: true, current: {}, default: {}, recent: []}),
    get_reasoning: () => Promise.resolve({visible: false, options: [], choice: "default"}),
    get_session_metrics: () => Promise.resolve({ok: false}),
  }
};
"""


def _serve(directory: Path):
    class _QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = partial(_QuietHandler, directory=str(directory))
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
                "() => document.getElementById('proactiveBar') !== null")

            # ---- 1. 临期待办推送 ----
            _push(page, {"type": "proactive", "kind": "reminders", "items": [
                {"id": "r2", "title": "交奖学金材料", "due": "2026-09-21",
                 "days_left": 1, "note": "交到学工办"},
                {"id": "r1", "title": "英语课前展示", "due": "2026-09-19",
                 "days_left": -1, "note": ""},
            ]})
            page.wait_for_function(
                "() => document.getElementById('proactiveBar').style.display === 'flex'")
            text = page.locator("#proactiveText").inner_text()
            check("临近截止" in text and "交奖学金材料" in text,
                  f"待办条文案含条数与标题({text[:36]}…)")
            page.click("#proactiveGo")
            value = page.input_value("#input")
            check("没做完" in value and "已过截止 1 天" in value and "学工办" in value,
                  "「去看看」回填逐项安排请求(含逾期标注与备注)")

            # ---- 2. 新版本推送:按钮变「去下载」,点击走 open_url ----
            _push(page, {"type": "proactive", "kind": "update",
                         "version": "0.9.18", "notes": "新增主动推荐与提醒",
                         "url": "https://gitee.com/inkzero/momo/releases/v0.9.18"})
            page.wait_for_function(
                "() => document.getElementById('proactiveBar').style.display === 'flex'")
            check(page.locator("#proactiveGo").inner_text() == "去下载",
                  "新版本提醒按钮为「去下载」")
            check("v0.9.18" in page.locator("#proactiveText").inner_text(),
                  "版本提醒文案带新版本号")
            page.click("#proactiveGo")
            page.wait_for_function(
                "() => window.__E2E_CALLS__.some(c => c[0] === 'open_url')")
            check(page.evaluate(
                "() => window.__E2E_CALLS__.find(c => c[0]==='open_url')[1]"
                ).endswith("releases/v0.9.18"), "「去下载」调用 open_url 打开发布页")

            # ---- 3. 通知解读请求带「存知识库候选」引导 ----
            _push(page, {"type": "proactive", "kind": "notices", "total": 1, "items": [
                {"title": "关于校园卡系统升级维护的通知",
                 "url": "https://www.gzasc.edu.cn/jwc/info/1066/6860.htm",
                 "source": "教务处通知"}]})
            page.wait_for_function(
                "() => document.getElementById('proactiveBar').style.display === 'flex'")
            page.click("#proactiveGo")
            value = page.input_value("#input")
            check("知识库候选" in value,
                  "通知解读请求带「存知识库候选」引导(闭环衔接)")

            # ---- 4. mermaid 图表:finish 后真渲染成 SVG ----
            text = ("大学四年路线:\n```mermaid\ngraph TD\n"
                    "  A[大一 打基础] --> B[大二 定方向]\n"
                    "  B --> C[大三 冲刺]\n```\n每一步都有该做的事。")
            _push(page, {"type": "cleared", "session": "s1"})
            _push(page, {"type": "text_delta", "session": "s1", "text": text})
            _push(page, {"type": "finish_stream", "session": "s1"})
            page.wait_for_selector(".msg.assistant .mermaid svg", timeout=15000)
            check(page.locator(".msg.assistant .mermaid svg").count() == 1,
                  "mermaid 代码块最终渲染成 SVG 图表")
            check(page.locator(".msg.assistant .mermaid svg .node").count() == 3,
                  "图表三个节点全部画出")
            browser.close()
    finally:
        shutdown()

    print()
    if failures:
        print(f"共 {len(failures)} 项失败:")
        for f in failures:
            print("  ✗ " + f)
        return 1
    print("v0.9.17 前端回归:全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
