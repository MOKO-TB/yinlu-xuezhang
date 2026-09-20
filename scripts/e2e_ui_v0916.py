# -*- coding: utf-8 -*-
"""v0.9.16 前端回归:主动推荐条(官网新通知 / 校历临近)。

    新 看门狗推 proactive 事件 → 输入框上方出现金色推荐条
    新 「去看看」把带 URL 的解读请求回填输入框;「不再提醒」调 set_notice_watch(false)
    新 校历临近提醒:「还有 N 天」+ 回填准备计划请求;✕ 本次忽略
跑法(项目根目录):
    .venv\\Scripts\\python.exe scripts\\e2e_ui_v0916.py
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
      version: "0.9.16", app_name: "引路学长",
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
    set_notice_watch: (enabled) => {
      window.__E2E_CALLS__.push(["set_notice_watch", enabled]);
      return Promise.resolve({ok: true, enabled: enabled}); },
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
            check(page.evaluate(
                "() => document.getElementById('proactiveBar').style.display"
                ) == "none", "初始不显示推荐条")

            # ---- 1. 官网新通知推送 ----
            _push(page, {"type": "proactive", "kind": "notices", "total": 2, "items": [
                {"title": "2026年下半年全国计算机等级考试考生须知",
                 "url": "https://www.gzasc.edu.cn/jwc/info/1066/6852.htm",
                 "source": "教务处通知"},
                {"title": "关于评奖评优工作的通知",
                 "url": "https://www.gzasc.edu.cn/xsc/info/1324/17300.htm",
                 "source": "学生处通知"},
            ]})
            page.wait_for_function(
                "() => document.getElementById('proactiveBar').style.display === 'flex'")
            text = page.locator("#proactiveText").inner_text()
            check("官网有 2 条新通知" in text and "评奖评优" in text,
                  f"通知条文案含条数与标题({text[:40]}…)")
            check(page.evaluate(
                "() => document.getElementById('proactiveMute').style.display")
                == "inline", "通知类提醒带「不再提醒」")

            # 去看看 → 输入框回填带 URL 的解读请求
            page.click("#proactiveGo")
            value = page.input_value("#input")
            check("解读学校官网刚发的新通知" in value
                  and "6852.htm" in value and "逐条" in value,
                  "「去看看」回填逐条解读请求(带标题与网址)")
            check(page.evaluate(
                "() => document.getElementById('proactiveBar').style.display")
                == "none", "点击去看看后推荐条收起")

            # ---- 2. 校历临近提醒 ----
            _push(page, {"type": "proactive", "kind": "calendar",
                         "name": "四六级笔试(CET-4/6)", "days_left": 23,
                         "start": "2026-12-12"})
            page.wait_for_function(
                "() => document.getElementById('proactiveBar').style.display === 'flex'")
            text2 = page.locator("#proactiveText").inner_text()
            check("还有 23 天" in text2 and "2026-12-12" in text2,
                  f"校历提醒文案带倒计时与日期({text2})")
            check(page.evaluate(
                "() => document.getElementById('proactiveMute').style.display")
                == "none", "校历提醒没有「不再提醒」(那只管官网通知)")

            # ---- 3. 不再提醒(set_notice_watch(false)) ----
            _push(page, {"type": "proactive", "kind": "notices", "total": 1, "items": [
                {"title": "关于校园卡系统升级维护的通知",
                 "url": "https://www.gzasc.edu.cn/jwc/info/1066/6860.htm",
                 "source": "教务处通知"}]})
            page.wait_for_function(
                "() => document.getElementById('proactiveBar').style.display === 'flex'")
            page.click("#proactiveMute")
            page.wait_for_function(
                "() => window.__E2E_CALLS__.some(c => c[0] === 'set_notice_watch')")
            check(page.evaluate(
                "() => window.__E2E_CALLS__.find(c => c[0]==='set_notice_watch')[1]"
                ) is False, "「不再提醒」调用 set_notice_watch(false)")
            check(page.evaluate(
                "() => document.getElementById('proactiveBar').style.display")
                == "none", "关闭后推荐条收起")

            # ---- 4. ✕ 本次忽略 ----
            _push(page, {"type": "proactive", "kind": "calendar",
                         "name": "中秋节", "days_left": 6, "start": "2026-09-25"})
            page.wait_for_function(
                "() => document.getElementById('proactiveBar').style.display === 'flex'")
            page.click("#proactiveClose")
            page.wait_for_function(
                "() => document.getElementById('proactiveBar').style.display === 'none'")
            check(True, "✕ 本次忽略,不再打扰")

            browser.close()
    finally:
        shutdown()

    print()
    if failures:
        print(f"共 {len(failures)} 项失败:")
        for f in failures:
            print("  ✗ " + f)
        return 1
    print("v0.9.16 前端回归:全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
