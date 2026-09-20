# -*- coding: utf-8 -*-
"""v0.9.15 前端回归:上下文窗口面板(明细 + 真压缩按钮)。

    新 点任务栏「上下文」弹出面板:占用百分比、进度条(带自动压缩线)、
       系统提示词/工具定义/Skill/对话消息 四类占比
    新 「压缩上下文」按钮真的调 compact_now(忙时后端拒绝,按钮有压缩中态)
    新 压缩后 session_metrics 事件把面板数字刷新
跑法(项目根目录):
    .venv\\Scripts\\python.exe scripts\\e2e_ui_v0915.py
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

METRICS = {
    "provider_id": "gzasc", "selected_key": "gzasc::deepseek-v4-flash",
    "model": "deepseek-v4-flash", "capabilities": ["文本", "工具调用"],
    "supports_vision": False, "known": True, "context_window": 327680,
    "context_tokens": 26000, "consumed": 0, "compact_ratio": 0.7,
    "selectable": [],
    "context_breakdown": {
        "window": 327680, "used": 26000, "compact_at": 0.7,
        "buckets": [
            {"key": "system", "label": "系统提示词", "tokens": 3200},
            {"key": "tools", "label": "工具定义", "tokens": 5800},
            {"key": "skills", "label": "Skill", "tokens": 900, "count": 14},
            {"key": "messages", "label": "对话消息", "tokens": 16100},
        ],
    },
}

STUB_JS = ("""
window.__E2E_CALLS__ = [];
window.__stubReady = true;
const CALLS = window.__E2E_CALLS__;
const METRICS = __INJ_METRICS__;
window.pywebview = {
  api: {
    get_info: () => Promise.resolve({
      version: "0.9.15", app_name: "引路学长",
      permission_mode: "confirm",
      permission_modes: [{value: "confirm", label: "变更前确认"}],
      session_key: "s1", model: "deepseek-v4-flash", active_provider: "gzasc",
      providers: [{id: "gzasc", name: "广州应用科技学院",
                   model: "deepseek-v4-flash", has_key: true}],
      api_key_configured: true, workspace: "D:/demo/yhbz",
      workspace_name: "yhbz", workspace_is_default: false,
      memory_count: 0, memory_user_count: 0, mcp_servers: [],
      sandbox: {}, vision: false, campus_map: null, library_health: {},
      skills: [], kb_count: 0, session_metrics: METRICS
    }),
    get_session_metrics: () => Promise.resolve(Object.assign({ok: true}, METRICS)),
    compact_now: () => { CALLS.push(["compact_now"]);
      return new Promise(res => setTimeout(() => res({ok: true}), 500)); },
    get_suggestions: () => Promise.resolve({suggestions: []}),
    get_countdowns: () => Promise.resolve({ok: true, countdowns: []}),
    rate_message: () => Promise.resolve({ok: true}),
    send_task: () => Promise.resolve({ok: true, session: "s1"}),
    refresh_sessions: () => Promise.resolve({ok: true}),
    get_recent_projects: () => Promise.resolve({ok: true, current: {}, default: {}, recent: []}),
    get_reasoning: () => Promise.resolve({visible: false, options: [], choice: "default"}),
  }
};
""").replace("__INJ_METRICS__", json.dumps(METRICS, ensure_ascii=False))


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
                "() => document.getElementById('tmContext') !== null"
                " && document.getElementById('tmContext').textContent.length > 4")

            # ---- 1. 点「上下文」弹出面板 ----
            page.click("#tmContext")
            page.wait_for_selector("#ctxPanel.show")
            check("上下文窗口" in page.locator("#ctxPanel .ctx-head").inner_text(),
                  "面板标题与整体出现")
            check(page.locator("#ctxPanel .ctx-pct").inner_text() == "8%",
                  "占用百分比 = used/window(26000/327680 ≈ 8%)")
            labels = page.eval_on_selector_all(
                "#ctxPanel .ctx-row .lb", "els => els.map(e => e.textContent)")
            check(labels == ["系统提示词", "工具定义", "Skill (14个)", "对话消息"],
                  f"四类明细按序渲染({labels})")
            width = page.evaluate(
                "() => document.querySelector('#ctxPanel .ctx-fill').style.width")
            check(width == "8%", f"进度条宽度与占用一致({width})")
            mark = page.evaluate(
                "() => document.querySelector('#ctxPanel .ctx-mark').style.left")
            check(mark == "70%", f"自动压缩线在 70%({mark})")

            # ---- 2. 压缩按钮:真调 compact_now,有压缩中态与恢复 ----
            page.click("#btnCtxCompact")
            page.wait_for_function(
                "() => window.__E2E_CALLS__.some(c => c[0] === 'compact_now')")
            check(page.evaluate(
                "() => document.getElementById('btnCtxCompact').disabled"),
                "点击后按钮进入禁用(压缩中)状态")
            page.wait_for_function(
                "() => !document.getElementById('btnCtxCompact').disabled")
            check(True, "压缩完成后按钮恢复可点")

            # ---- 3. session_metrics 事件刷新面板数字 ----
            _push(page, {"type": "session_metrics", "session": "s1", **{
                **METRICS,
                "context_tokens": 65000,
                "context_breakdown": {**METRICS["context_breakdown"],
                                      "used": 65000,
                                      "buckets": [
                                          {"key": "system", "label": "系统提示词", "tokens": 3200},
                                          {"key": "tools", "label": "工具定义", "tokens": 5800},
                                          {"key": "skills", "label": "Skill", "tokens": 900, "count": 14},
                                          {"key": "messages", "label": "对话消息", "tokens": 55100},
                                      ]},
            }})
            page.wait_for_function(
                "() => document.querySelector('#ctxPanel .ctx-pct')"
                " && document.querySelector('#ctxPanel .ctx-pct').textContent === '20%'")
            check(True, "压缩后推送新指标,面板百分比即时更新(8% → 20%)")

            # ---- 4. 点别处收起面板 ----
            page.click("#chat", position={"x": 400, "y": 200})
            page.wait_for_function(
                "() => !document.getElementById('ctxPanel').classList.contains('show')")
            check(True, "点击面板外区域收起")

            browser.close()
    finally:
        shutdown()

    print()
    if failures:
        print(f"共 {len(failures)} 项失败:")
        for f in failures:
            print("  ✗ " + f)
        return 1
    print("v0.9.15 前端回归:全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
