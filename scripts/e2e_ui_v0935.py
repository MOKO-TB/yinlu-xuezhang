# -*- coding: utf-8 -*-
"""v0.9.35 前端回归:课表面板的「⚠ 要换楼」那一行点下去**真能框出两栋楼**。

用户给这轮立的规矩是「是要真正能使用的不能只用于装饰」,所以这里验的三件事全部
在真 Chromium 里跑,而不是单测(单测只证明 walk 算得对,证明不了点按钮有后果):

1. **行由真后端算出来**:payload 走真 `schedule.view(today)`(临时 APP_DIR + 真
   `campus-index.json`),前端只照着画 —— 楼名、分钟数、"判不出是哪栋楼"那几句
   全部来自 `minicode/walk.py`,页面里没有任何第二套判定。
2. **点 🗺 真发生事情**:地图面板弹开、`.cm-box` 恰好两个、副标题写出这两栋的名字、
   课表遮罩收掉。少一条就是装饰。
3. **诚实的边界仍在**:没填作息表时页面上一处分钟数字都不许出现;判不出楼的课
   要明说是哪几门;地图索引里没有的楼名要明说"没登记",不许悄悄少画一栋。

跑法(项目根目录,需要 PYTHONIOENCODING=utf-8 否则中文断言在 GBK 控制台炸):
    PYTHONIOENCODING=utf-8 .venv\\Scripts\\python.exe scripts\\e2e_ui_v0935.py
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import threading
from datetime import date, timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TODAY_COL = str(date.today().isoweekday())        # 周网格 1=周一 … 7=周日


# --------------------------------------------------------------- 后端真视图

def build_payloads():
    """返回 (没填作息表的视图, 填了作息表的视图) —— 都出自 schedule.view() 本体。"""
    from minicode import paths, schedule

    tmp = Path(tempfile.mkdtemp(prefix="yx-v0935-"))
    paths.APP_DIR = tmp / "app"
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    classes = [
        # 今天两节紧接、分处两栋真楼 → 面板必须长出 ⚠ 那一行
        {"name": "高等数学", "day": TODAY_COL, "periods": "1-2", "weeks": "1-16",
         "location": "J1至善楼101"},
        {"name": "大学英语", "day": TODAY_COL, "periods": "3-4", "weeks": "1-16",
         "location": "S5致用楼205"},
        # 第三节在「田径场」:索引里登记为场地、不是楼 → 只能明说判不出,不参与换楼
        {"name": "体育课", "day": TODAY_COL, "periods": "5-6", "weeks": "1-16",
         "location": "田径场"},
    ]
    assert schedule.save(monday.isoformat(), classes)[0]
    plain = schedule.view(today)
    assert schedule.save_period_times(
        [{"start": "08:00", "end": "08:45"}, {"start": "08:55", "end": "09:40"},
         {"start": "09:50", "end": "10:35"}, {"start": "10:45", "end": "11:30"}],
        "夹具作息")[0]
    clocked = schedule.view(today)
    # 自检:后端确实算出了这一处换楼(否则下面的断言全是在验一张空面板)
    walks = plain["today"]["walks"]
    assert len(walks) == 1 and walks[0]["prev"]["building"] == "J1至善楼", plain["today"]
    assert len(clocked["today"]["walks"]) == 1 and clocked["today"]["walks"][0]["minutes"] == 10
    return plain, clocked


PLAIN, CLOCKED = build_payloads()

STUB_JS = """
window.__E2E_CALLS__ = [];
window.__SC_PLAIN__ = @@PLAIN@@;
window.__SC_CLOCKED__ = @@CLOCKED@@;
window.__schedule__ = window.__SC_PLAIN__;
window.__CAMPUS_MAP__ = @@MAP@@;
window.pywebview = {
  api: {
    get_info: () => Promise.resolve({
      version: "0.9.35", app_name: "引路学长",
      permission_mode: "confirm",
      permission_modes: [{value: "confirm", label: "变更前确认"}],
      session_key: "s1", model: "test-model", active_provider: "test",
      providers: [{id: "test", name: "Test", model: "test-model", has_key: true}],
      api_key_configured: true, workspace: "D:/demo/campus",
      workspace_name: "campus", workspace_is_default: true,
      memory_count: 0, memory_user_count: 0, mcp_servers: [],
      sandbox: {}, vision: false, campus_map: window.__CAMPUS_MAP__, library_health: {},
      skills: [], kb_count: 0,
      session_metrics: {ok: false, selectable: []}
    }),
    get_schedule_view: () => Promise.resolve({ok: true, schedule: window.__schedule__}),
    schedule_save_times: (rowsJson) => {
      window.__E2E_CALLS__.push(["schedule_save_times", rowsJson]);
      window.__schedule__ = window.__SC_CLOCKED__;
      return Promise.resolve({ok: true, msg: "作息表已存",
                              schedule: window.__SC_CLOCKED__});
    },
    set_notice_watch: () => Promise.resolve({ok: true}),
    open_url: () => Promise.resolve({ok: true}),
    reveal_file: () => Promise.resolve({ok: true}),
    get_activity: () => Promise.resolve({ok: true, empty: true, groups: [],
                                         summary: {calls: 0, errors: 0, running: 0}}),
    get_plan: () => Promise.resolve({ok: true, has_plan: false, steps: [], total: 0,
                                     done: 0, ready: [], blocked: [], failed: [],
                                     running: [], dispatch_text: ""}),
    get_recent_projects: () => Promise.resolve({ok: true, current: {}, default: {}, recent: []}),
    get_reasoning: () => Promise.resolve({visible: false, options: [], choice: "default"}),
    get_suggestions: () => Promise.resolve({suggestions: []}),
    get_countdowns: () => Promise.resolve({ok: true, countdowns: []}),
    get_session_metrics: () => Promise.resolve({ok: false}),
    refresh_sessions: () => Promise.resolve({ok: true}),
    send_task: () => Promise.resolve({ok: true, session: "s1"}),
    rate_message: () => Promise.resolve({ok: true}),
    compact_now: () => Promise.resolve({ok: true}),
  }
};
"""


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
    from minicode import campus
    from playwright.sync_api import sync_playwright

    stub = (STUB_JS
            .replace("@@PLAIN@@", json.dumps(PLAIN, ensure_ascii=False))
            .replace("@@CLOCKED@@", json.dumps(CLOCKED, ensure_ascii=False))
            .replace("@@MAP@@", json.dumps(campus.frontend_payload(), ensure_ascii=False)))
    url, shutdown = _serve(FRONTEND)
    failures: list[str] = []
    console: list[str] = []
    missing: list[str] = []

    def check(cond, message):
        if not cond:
            failures.append(message)
        print(("  ✓ " if cond else "  ✗ ") + message)

    try:
        with sync_playwright() as p:
            browser = _launch(p)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on("pageerror", lambda e: console.append(f"pageerror: {e}"))
            page.on("console", lambda m: console.append(f"{m.type}: {m.text}")
                    if m.type == "error" else None)
            page.on("response", lambda r: r.status >= 400
                    and missing.append(f"{r.status} {r.url.split('/')[-1][:40]}")
                    or None)
            page.add_init_script(stub)
            page.goto(url, wait_until="load")
            page.wait_for_function("() => document.getElementById('btnSchedule') !== null")

            # ---- 1. 周网格:今天那一格长出「⚠ N 处换楼」,和后端条数一致 ----
            page.click("#btnSchedule")
            page.wait_for_selector("#scTabs button[data-tab='week']")
            page.click("#scTabs button[data-tab='week']")
            page.wait_for_selector(".sc-col[data-day]")
            chip = page.locator(f".sc-col[data-day='{TODAY_COL}'] .sc-walkchip")
            check(chip.count() == 1, "周网格今天那一格长出走堂徽章(有换楼才画,没换楼不长)")
            check(chip.inner_text().strip() == f"⚠ {len(PLAIN['today']['walks'])} 处换楼",
                  f"徽章上的数字就是后端算出来的那一条:{chip.inner_text().strip()}")
            other = page.locator(".sc-col[data-day='2'] .sc-walkchip").count() \
                if TODAY_COL != "2" else None
            check(other in (0, None), "没换楼的那一天格子里不长徽章(不是摆了个'0 处'占位)")

            # ---- 2. 下钻看当天:两栋楼名 + 判不出的那门课,一处分钟数字都没有 ----
            page.click(f".sc-col[data-day='{TODAY_COL}']")
            page.wait_for_selector(".sc-walk")
            row = page.locator(".sc-walk").first
            check("J1至善楼" in row.inner_text() and "S5致用楼" in row.inner_text(),
                  f"那一行写的是后端解析出的两栋楼:{row.inner_text().strip()}")
            check("只按节次" in row.inner_text(), "没填作息表 → 行上明说「只按节次」")
            body = page.locator("#scBody").inner_text()
            check(not re.search(r"\d+\s*分钟", body),
                  "整个面板一处「N 分钟」都没有(没作息表就不许出现分钟数)")
            for word in ("来不及", "危险", "要迟到", "肯定赶"):
                check(word not in body, f"面板不替用户下结论:没有「{word}」这种字样")
            check("体育课" in body and "判不出是哪栋楼" in body,
                  "地点在索引里是场地不是楼的课,面板点名说它没参与判断")

            # ---- 3. 点 🗺:地图真弹开并把两栋一起框出来(这一条是"能不能用"的分界) ----
            page.locator(".sc-walkmap").first.click()
            page.wait_for_selector("#mapPanel.open")
            check(page.locator("#mapPanel").get_attribute("class").find("open") >= 0,
                  "点 🗺 之后地图面板弹开")
            check(page.locator("#scheduleOverlay").get_attribute("class").find("show") < 0,
                  "课表遮罩收掉了(不然地图被压在下面,等于没打开)")
            page.wait_for_function("() => document.querySelectorAll('.cm-box').length === 2")
            boxes = page.locator(".cm-box").count()
            check(boxes == 2, f"地图上恰好框出两栋(实测 {boxes} 个框)")
            sub = page.locator("#mapSub").inner_text()
            check("J1至善楼" in sub and "S5致用楼" in sub,
                  f"副标题报出框的就是这两栋:{sub}")
            labels = page.evaluate("() => [...document.querySelectorAll('.cm-box-label')]"
                                   ".map(t => t.textContent)")
            check(labels == ["J1至善楼", "S5致用楼"], f"两个框上的楼名跟着画出来了:{labels}")

            # ---- 4. 填了作息表:同一行立刻长出真分钟数(数字来自本机作息表) ----
            page.evaluate("() => { window.__schedule__ = window.__SC_CLOCKED__; }")
            page.click("#btnSchedule")
            page.wait_for_selector(".sc-walk")
            check("中间 10 分钟" in page.locator(".sc-walk").first.inner_text(),
                  "存过作息表后同一行给出「中间 10 分钟」(第 2 节下课→第 3 节上课)")

            # ---- 5. 索引里没有的楼名:明说找不到,不悄悄少画一栋 ----
            got = page.evaluate("(names) => window.CampusMap.showPlaces(names)",
                                ["J1至善楼", "根本不存在的楼"])
            notice = page.locator("#mapNotice").inner_text()
            check(got is True and "根本不存在的楼" in notice and "只框出了找到的那几栋" in notice,
                  f"有一栋没登记时地图明说(而不是安静地少画):{notice.strip()[:60]}")
            check(page.locator(".cm-box").count() == 1, "没登记的那栋没被瞎框")
            gone = page.evaluate("() => window.CampusMap.showPlaces(['地图上没有的楼A','地图上没有的楼B'])")
            check(gone is False and "不猜位置" in page.locator("#mapNotice").inner_text(),
                  "一栋都找不到时如实拒绝并说明不猜位置")

            # ---- 6. 页面不许有 JS 错(白屏类回归) ----
            bad = [c for c in console
                   if "Failed to load resource" not in c and "favicon" not in c]
            check(not bad, f"整场跑下来控制台没有 JS 报错:{bad[:3]}")
            check(not missing, f"静态服务上没有 404 的资源(缺文件会在这儿露出来):{missing[:4]}")
            browser.close()
    finally:
        shutdown()

    print()
    if failures:
        print(f"共 {len(failures)} 项失败:")
        for f in failures:
            print("  ✗ " + f)
        return 1
    print("v0.9.35 前端回归:全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
