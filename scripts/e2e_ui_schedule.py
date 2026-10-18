# -*- coding: utf-8 -*-
"""v0.9.184 课表面板的真浏览器验证(界面那一半的闸)。

跑法(项目根目录,**要带 utf-8**):

    PYTHONIOENCODING=utf-8 .venv\\Scripts\\python.exe scripts\\e2e_ui_schedule.py

**为什么要有这一支**:用户报"导入了课程表和成绩,但显示出现了问题"。后端那一份
`schedule.view()` 用**他机器上真实的 schedule.json** 跑过,数据是全的(今天 0 节、
明天 2 节、本周七天有课、成绩绩点算得出来)—— 也就是说这句话的真伪只存在于渲染器里,
Python 层看不见。所以这里把**那份真 payload** 原样喂给页面(桩只替 `pywebview.api`,
不替任何渲染逻辑),四个页签逐个验,并且**把 console/pageerror 收下来当判据** ——
"面板一片空白"这种坏法,只有浏览器自己看得见。
"""
from __future__ import annotations

import json
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 出包那一场必须对着包内那份前端再跑一遍(这一件的缺陷**全在界面上**):
#   python scripts/e2e_ui_schedule.py --frontend dist/引路学长/_internal/frontend
# (v0.9.138 的教训:只验源码那份等于没验。)
FRONTEND = ROOT / "frontend"
if "--frontend" in sys.argv:
    FRONTEND = Path(sys.argv[sys.argv.index("--frontend") + 1]).resolve()
VIEW = ROOT / "_tmp_segsweep" / "sched_view.json"
STATS = ROOT / "_tmp_segsweep" / "scores_view.json"

STUB_TMPL = """
window.__E2E_CALLS__ = [];
function __spy(name, ret) {
  return function () { window.__E2E_CALLS__.push(name); return Promise.resolve(ret); };
}
window.__API__ = {
  get_schedule_view: __spy("get_schedule_view", %s),
  get_score_stats: __spy("get_score_stats", %s),
  schedule_save_times: __spy("schedule_save_times", {ok: true}),
  schedule_undo: __spy("schedule_undo", {ok: true}),
};
window.pywebview = { api: new Proxy({}, {
  get: function (_t, k) {
    if (window.__API__[k]) return window.__API__[k];
    return function () {
      window.__E2E_CALLS__.push(String(k));
      return Promise.resolve({});
    };
  }})};
window.__stubReady = true;
"""


def _serve(directory: Path):
    class _QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass
    handler = partial(_QuietHandler, directory=str(directory))
    httpd = None
    for candidate in range(18700, 18780):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", candidate), handler)
            break
        except OSError:
            continue
    assert httpd is not None, "找不到可绑定的端口"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}/index.html", httpd.shutdown


def main() -> int:
    from playwright.sync_api import sync_playwright
    from e2e_launch import launch

    stub = STUB_TMPL % (VIEW.read_text(encoding="utf-8").strip(),
                        json.dumps({"ok": True, "stats": json.loads(
                            STATS.read_text(encoding="utf-8"))}, ensure_ascii=False))
    url, shutdown = _serve(FRONTEND)
    failures: list[str] = []
    tally = {"n": 0}
    errs: list[str] = []

    def check(cond, message):
        tally["n"] += 1
        if not cond:
            failures.append(message)
        print(("  \u2713 " if cond else "  \u2717 ") + message)

    try:
        with sync_playwright() as p:
            browser = launch(p)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on("pageerror", lambda e: errs.append("pageerror: %s" % e))
            page.on("console", lambda m: errs.append("console.%s: %s"
                                                     % (m.type, m.text))
                    if m.type in ("error",) else None)
            page.add_init_script(stub)
            page.goto(url, wait_until="load")
            page.wait_for_function("() => document.getElementById('scheduleOverlay') !== null")

            page.locator("#btnSchedule").click()
            page.wait_for_function(
                "() => document.getElementById('scheduleOverlay').classList.contains('show')")
            body = page.locator("#schedBody")
            check("周日" in body.inner_text() and "2026-10-18" in body.inner_text(),
                  "「今天」显出那一天的抬头(周几 + 日期) —— 空白面板的第一号症状")
            check(("没课" in body.inner_text()) or ("🌴" in body.inner_text()),
                  "今天 0 节课时要有一句人话,不能整块空着")
            check("第 7 周" in page.locator("#scWeek").inner_text(),
                  "抬头那行报得出周次")

            page.locator("#scTabs button[data-tab=tomorrow]").click()
            page.wait_for_function("() => /大学英语/.test(document.getElementById('schedBody').innerText)")
            check("大学英语" in body.inner_text() and "复变函数" in body.inner_text(),
                  "「明天」把两节课列出来")
            check("14:30" in body.inner_text(), "写了钟点的课要把钟点亮出来")

            page.locator("#scTabs button[data-tab=week]").click()
            page.wait_for_function("() => document.querySelectorAll('#schedBody .sc-col').length === 7")
            check(len(page.locator("#schedBody .sc-col").all()) == 7, "「本周」是七天网格")
            check(len(page.locator("#schedBody .sc-cell").all()) >= 6,
                  "本周格子里真的排着课,不是一排破折号")

            page.locator("#scTabs button[data-tab=scores]").click()
            page.wait_for_function(
                "() => document.getElementById('schedBody').innerText.length > 20")
            txt = body.inner_text()
            check(any(ch.isdigit() for ch in txt), "「成绩」那一档有数字(绩点/门数/学分)")
            check("scores.json" in txt, "成绩那一档说清数据只在本机")

            page.locator("#btnScheduleClose").click()
            check(not page.locator("#scheduleOverlay").evaluate("e => e.classList.contains('show')"),
                  "关闭按钮真的关掉")
            page.screenshot(path=str(ROOT / "_tmp_segsweep" / "e2e_schedule.png"))
    finally:
        shutdown()

    real = [e for e in errs if "favicon" not in e and "ERR_FILE_NOT_FOUND" not in e]
    print("\n控制台/异常:", len(real))
    for e in real[:8]:
        print("   ", e[:160])
    if real:
        failures.append("页面里有 JS 异常/报错(%d 条)—— 面板空白最可能就是它" % len(real))
    print("\n判定 %d 条,失败 %d 条" % (tally["n"], len(failures)))
    print("RESULT: PASS" if not failures else "RESULT: FAIL " + json.dumps(
        failures, ensure_ascii=False))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
