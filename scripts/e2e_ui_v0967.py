# -*- coding: utf-8 -*-
"""v0.9.67 前端回归:功能导览向导 —— 同一弹层两副面孔,真点真翻真关。

钉四件事:①配好密钥的人(首启向导不弹)打开时看到的是**导览**(标题/步骤/按钮
文案全是导览那套,不是首启的);②首启向导正在弹时导览让位(场景 B 回归);
③翻到最后一步按钮变「知道了」,点掉发的是 dismiss_onboarding("tour") 而不是
"first_run";④「不再显示」按钮发的也是当前模式的 key。

跑法(项目根目录,GBK 控制台必须带 PYTHONIOENCODING=utf-8):
    PYTHONIOENCODING=utf-8 .venv\\Scripts\\python.exe scripts\\e2e_ui_v0967.py
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

# 场景 A:配好了密钥(首启向导不弹),导览该露面
TOUR_PAYLOAD = {
    "ok": True, "show": False,
    "steps": [{"key": "key", "title": "① 接上模型", "body": "x"}],
    "tour_show": True,
    "tour_steps": [
        {"key": "rail", "title": "① 左栏那排图标是全部入口", "body": "地图、课表、心情…"},
        {"key": "composer", "title": "② 输入框的三个小抄", "body": "打 @ 引用文件,打 / 弹动作"},
        {"key": "trust", "title": "③ 数据都在本机", "body": "换电脑用「💾 数据」迁移"},
    ],
    "key_configured": True, "paths": {"app_dir": "D:/demo"},
}
# 场景 B:没配密钥 —— 首启向导露面,导览让位
FIRST_PAYLOAD = {
    "ok": True, "show": True,
    "steps": [{"key": "key", "title": "① 接上模型", "body": "点模型名开模型管理"}],
    "tour_show": False, "tour_steps": [],
    "key_configured": False, "paths": {"app_dir": "D:/demo"},
}

STUB_JS = """
window.__E2E_CALLS__ = [];
window.__ONB__ = @@PAYLOAD@@;
window.pywebview = {
  api: {
    get_info: () => Promise.resolve({
      version: "0.9.67", app_name: "引路学长",
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
    get_onboarding: () => Promise.resolve(window.__ONB__),
    dismiss_onboarding: (key) => {
      window.__E2E_CALLS__.push(["dismiss_onboarding", key]);
      return Promise.resolve({ok: true, msg: ""});
    },
    workspace_search_files: () => Promise.resolve({ok: true, items: []}),
    get_schedule_view: () => Promise.resolve({ok: false}),
    set_notice_watch: () => Promise.resolve({ok: true}),
    get_recent_projects: () => Promise.resolve({ok: true, current: {}, default: {}, recent: []}),
    get_reasoning: () => Promise.resolve({visible: false, options: [], choice: "default"}),
    get_suggestions: () => Promise.resolve({suggestions: []}),
    get_countdowns: () => Promise.resolve({ok: true, countdowns: []}),
    get_session_metrics: () => Promise.resolve({ok: false}),
    refresh_sessions: () => Promise.resolve({ok: true}),
    rate_message: () => Promise.resolve({ok: true}),
    compact_now: () => Promise.resolve({ok: true}),
    get_weather: () => Promise.resolve({ok: false}),
  }
};
"""

FAILS: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  ✓ " if ok else "  ✗ ") + what)
    if not ok:
        FAILS.append(what)


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
    import json
    from playwright.sync_api import sync_playwright

    url, stop = _serve(FRONTEND)
    try:
        with sync_playwright() as p:
            browser = _launch(p)

            # ---- 场景 A:配好密钥 → 导览露面 ----
            page = browser.new_page()
            page.add_init_script(STUB_JS.replace(
                "@@PAYLOAD@@", json.dumps(TOUR_PAYLOAD, ensure_ascii=False)))
            page.goto(url, wait_until="load")
            page.wait_for_selector("#firstRun.show", timeout=15000)
            check(page.inner_text("#frTitle") == "🧭 三件上手小抄",
                  "标题是导览那套(不是首启的)")
            check("左栏那排图标" in page.inner_text("#frBody"), "第一步是导览内容")
            check(page.inner_text("#frStep") == "1 / 3", "显示 1/3")
            page.click("#btnFrNext")
            page.click("#btnFrNext")
            check(page.inner_text("#frStep") == "3 / 3"
                  and page.inner_text("#btnFrNext") == "知道了",
                  "最后一步按钮变「知道了」")
            page.click("#btnFrNext")
            page.wait_for_timeout(120)
            calls = page.evaluate("() => window.__E2E_CALLS__")
            check(calls == [["dismiss_onboarding", "tour"]],
                  f"读完发的是 tour 的 dismiss(实测 {calls})")
            check(page.locator("#firstRun.show").count() == 0, "读完关窗")
            page.close()

            # ---- 场景 B:没配密钥 → 首启向导露面、导览让位(回归) ----
            page2 = browser.new_page()
            page2.add_init_script(STUB_JS.replace(
                "@@PAYLOAD@@", json.dumps(FIRST_PAYLOAD, ensure_ascii=False)))
            page2.goto(url, wait_until="load")
            page2.wait_for_selector("#firstRun.show", timeout=15000)
            check(page2.inner_text("#frTitle") == "👋 第一次打开引路学长",
                  "首启场景标题照旧(导览没抢位)")
            check(page2.inner_text("#frStep") == "1 / 1", "首启自己的步骤数")
            page2.click("#btnFrDont")
            page2.wait_for_timeout(120)
            calls2 = page2.evaluate("() => window.__E2E_CALLS__")
            check(calls2 == [["dismiss_onboarding", "first_run"]],
                  f"「不再显示」发的是 first_run 的 key(实测 {calls2})")
            page2.close()

            browser.close()
    finally:
        stop()
    print()
    if FAILS:
        print(f"{len(FAILS)} 项没过:")
        for f in FAILS:
            print("  - " + f)
        return 1
    print("全绿 8 项。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
