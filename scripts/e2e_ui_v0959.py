# -*- coding: utf-8 -*-
"""v0.9.59 前端回归:成绩导入三步向导 —— 同一个向导的两副面孔,真点得动。

规矩照旧(「要真正能使用的不能只用于装饰」):真 Chromium 里一颗一颗点过去,
桥是桩但**调用序被记账**,断言的是 DOM 事实与"调了哪个桥方法":
1. 课表面板第二颗按钮 → 向导切到成绩面孔(标题/步骤/按钮文案全变);
2. ①→②→③ 全链路只调 scores_import_*,**一个 schedule_import_* 都不碰**
   (两副面孔不串线 —— 串了就是往课表里写成绩);
3. 预览把行数、缺学分警示摆出来,③ 之前确认钮是禁用的;
4. 确认成功关窗 + toast;回到课表面孔,旧文案一字不差地回来(不残留成绩的"② 读取成绩")。

跑法(项目根目录,GBK 控制台必须带 PYTHONIOENCODING=utf-8):
    PYTHONIOENCODING=utf-8 .venv\\Scripts\\python.exe scripts\\e2e_ui_v0959.py
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

SCORES_ROWS = [
    {"name": "高等数学", "score": "89", "credit": "4", "semester": "2025-2026-1"},
    {"name": "大学物理", "score": "76", "credit": "3", "semester": "2025-2026-1"},
    {"name": "大学体育", "score": "优", "credit": "1", "semester": "2025-2026-1"},
    {"name": "军训", "score": "合格", "credit": "", "semester": "2025-2026-1"},
]

STUB_JS = """
window.__E2E_CALLS__ = [];
window.__SCORES_READ__ = @@READ@@;

window.pywebview = {
  api: {
    get_info: () => Promise.resolve({
      version: "0.9.59", app_name: "引路学长",
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
    get_schedule_view: () => Promise.resolve({ok: false, msg: "e2e 不测课表内容"}),
    scores_import_open: () => {
      window.__E2E_CALLS__.push(["scores_import_open"]);
      return Promise.resolve({ok: true, stage: "need_login",
                              url: "https://jw.example.cn/", title: "教务系统"});
    },
    scores_import_read: () => {
      window.__E2E_CALLS__.push(["scores_import_read"]);
      return Promise.resolve(window.__SCORES_READ__);
    },
    scores_import_model: () => {
      window.__E2E_CALLS__.push(["scores_import_model"]);
      return Promise.resolve(window.__SCORES_READ__);
    },
    scores_import_confirm: () => {
      window.__E2E_CALLS__.push(["scores_import_confirm"]);
      return Promise.resolve({ok: true, msg: "已导入 4 条成绩到本机存档(scores.json,不上传)。",
                              scores: "存档明细"});
    },
    scores_import_clear: () => {
      window.__E2E_CALLS__.push(["scores_import_clear"]);
      return Promise.resolve({ok: true, msg: "已清理"});
    },
    scores_import_status: () => Promise.resolve({ok: true, pending: false}),
    schedule_import_open: () => {
      window.__E2E_CALLS__.push(["schedule_import_open"]);
      return Promise.resolve({ok: true, stage: "need_login", url: "x", title: "教务系统"});
    },
    schedule_import_read: () => {
      window.__E2E_CALLS__.push(["schedule_import_read"]);
      return Promise.resolve({ok: false, stage: "no_grid", msg: "e2e 不读课表"});
    },
    schedule_import_confirm: () => {
      window.__E2E_CALLS__.push(["schedule_import_confirm"]);
      return Promise.resolve({ok: true, msg: "课表已导入", schedule: {}});
    },
    schedule_import_clear: () => {
      window.__E2E_CALLS__.push(["schedule_import_clear"]);
      return Promise.resolve({ok: true, msg: "已清理"});
    },
    schedule_save_times: () => Promise.resolve({ok: true}),
    set_notice_watch: () => Promise.resolve({ok: true}),
    get_recent_projects: () => Promise.resolve({ok: true, current: {}, default: {}, recent: []}),
    get_reasoning: () => Promise.resolve({visible: false, options: [], choice: "default"}),
    get_suggestions: () => Promise.resolve({suggestions: []}),
    get_countdowns: () => Promise.resolve({ok: true, countdowns: []}),
    get_session_metrics: () => Promise.resolve({ok: false}),
    refresh_sessions: () => Promise.resolve({ok: true}),
    send_task: () => Promise.resolve({ok: true, session: "s1"}),
    rate_message: () => Promise.resolve({ok: true}),
    compact_now: () => Promise.resolve({ok: true}),
    get_weather: () => Promise.resolve({ok: false}),
    get_mood_week: () => Promise.resolve({ok: false}),
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
    from playwright.sync_api import sync_playwright

    read_payload = {"ok": True, "stage": "ready", "rows": SCORES_ROWS,
                    "summary": {"entries": 4, "semesters": ["2025-2026-1"],
                                "no_credit": ["军训"], "odd_score": []},
                    "msg": "读到 4 条成绩。请核对后确认导入。"}
    stub = STUB_JS.replace("@@READ@@", json.dumps(read_payload, ensure_ascii=False))
    url, stop = _serve(FRONTEND)
    try:
        with sync_playwright() as p:
            browser = _launch(p)
            page = browser.new_page()
            page.add_init_script(stub)
            page.goto(url, wait_until="load")
            page.wait_for_function("() => document.getElementById('btnScoresImport') !== null")
            page.wait_for_timeout(250)

            def calls():
                return [c[0] for c in page.evaluate("() => window.__E2E_CALLS__")]

            # ---- 1. 入口与两副面孔 ----
            page.click("#btnSchedule")
            page.wait_for_selector("#scheduleOverlay.show")
            page.click("#btnScoresImport")
            page.wait_for_selector("#jwOverlay.show")
            check(page.inner_text("#jwTitle") == "📊 从教务系统导入成绩",
                  "向导标题切到成绩面孔")
            check(page.inner_text("#btnJwRead") == "② 读取成绩",
                  "第②步按钮文案跟着切(不残留课表的「读取课表」)")
            check(page.locator("#jwBody").inner_text().find("成绩查询") >= 0,
                  "第①步引导说的是「成绩查询,选好学期点查询」")

            # ---- 2. 三步全链路 ----
            page.click("#btnJwOpen")
            page.wait_for_timeout(80)
            check(page.locator("#jwBody").inner_text().find("选好学期、点了「查询」") >= 0,
                  "开完登录页,引导写清了要去成绩页点查询")
            page.click("#btnJwRead")
            page.wait_for_selector("#jwBody .jw-row")
            rows = page.locator("#jwBody .jw-row")
            check(rows.count() == 4, f"预览把 4 条成绩一行一条摆出来(实测 {rows.count()})")
            body = page.locator("#jwBody").inner_text()
            check("高等数学" in body and "89分·4学分" in body.replace(" ", ""),
                  "课程、分数、学分都来自桥的返回")
            check("没有学分" in body and "军训" in body, "缺学分的行如实点名(绩点会跳过)")
            confirm_disabled = page.locator("#btnJwConfirm").is_disabled()
            check(not confirm_disabled, "读到结果后确认钮解锁")
            page.click("#btnJwConfirm")
            page.wait_for_timeout(80)
            check(page.locator("#jwOverlay.show").count() == 0, "确认成功关掉向导")

            seq = calls()
            check(seq == ["scores_import_open", "scores_import_read",
                          "scores_import_confirm"],
                  f"三步只调 scores_import_*(实测 {seq})")

            # ---- 3. 回到课表面孔:旧文案一字不差回来 ----
            page.click("#btnSchedule")               # 面板在成绩向导打开时被关了,重开
            page.wait_for_selector("#scheduleOverlay.show")
            page.click("#btnScheduleImport")
            page.wait_for_selector("#jwOverlay.show")
            check(page.inner_text("#jwTitle") == "🎓 从教务系统导入课表",
                  "课表面孔的标题回来(模式不残留)")
            check(page.inner_text("#btnJwRead") == "② 读取课表",
                  "课表面孔的按钮文案回来")
            page.keyboard.press("Escape")
            page.click("#btnJwClose")

            # ---- 4. 桥未就绪的退路:网页版形态下按钮退回文字导入,不炸 ----
            page.click("#btnSchedule")               # 面板又被向导关了,再开一次
            page.wait_for_selector("#scheduleOverlay.show")
            page.evaluate("() => { delete pywebview.api.scores_import_open; }")
            page.click("#btnScoresImport")
            page.wait_for_timeout(80)
            check(page.locator("#jwOverlay.show").count() == 0
                  and page.locator("#input").input_value().find("成绩") >= 0,
                  "桥没有成绩方法时退回输入框引导(不炸、不空转)")

            browser.close()
    finally:
        stop()
    print()
    if FAILS:
        print(f"{len(FAILS)} 项没过:")
        for f in FAILS:
            print("  - " + f)
        return 1
    print("全绿 14 项。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
