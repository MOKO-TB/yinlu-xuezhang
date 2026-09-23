# -*- coding: utf-8 -*-
"""v0.9.36 前端回归:走堂第二步 —— 整周那句汇总 + 本周最多的那一天,都是真数据真颜色。

上一轮(v0.9.35)证明的是「点 ⚠ 那一行真能框出两栋楼」;这一轮要证明的是新加的
两句**不是装饰**:

1. **周视图顶部那句「本周 N 天要换教学楼、共 M 处,周X最多(K 处)」由后端算**:
   数字逐个对着 `schedule.view()` 里的 `walk_week` 核,页面里没有第二套计数。
2. **最多那天的徽章确实变了颜色**,而且**只有那一天**变 —— 前端读的是
   `walk_week.worst_date`,自己没比过大小(比了就可能出现两天都"最多")。
3. **处数是 walk_total、不是"这一屏画了几行"**:今天排 6 门课 6 栋楼 = 5 处换楼,
   徽章说 5,下面的列表只画 4 行并补一句"另有 1 处没列出"。
4. **没换楼的那一周**:汇总句和徽章一个都不出现 —— 不摆「0 处」占位。
5. **诚实边界仍然守住**:没填作息表时整页一处「N 分钟」都没有,汇总句要自己
   声明「只按节次说,没编分钟」。

跑法(项目根目录,需要 PYTHONIOENCODING=utf-8 否则中文断言在 GBK 控制台炸):
    PYTHONIOENCODING=utf-8 .venv\\Scripts\\python.exe scripts\\e2e_ui_v0936.py
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

TODAY = date.today()
TODAY_COL = str(TODAY.isoweekday())                  # 周网格 1=周一 … 7=周日
OTHER_COL = "2" if TODAY_COL != "2" else "3"         # 另一个有换楼的星期
WEEKDAY_CN = "一二三四五六日"


# --------------------------------------------------------------- 后端真视图

def _view(classes):
    """把夹具写进一个临时 APP_DIR,再用**真** schedule.view() 出视图。"""
    from minicode import paths, schedule

    tmp = Path(tempfile.mkdtemp(prefix="yx-v0936-"))
    paths.APP_DIR = tmp / "app"
    monday = TODAY - timedelta(days=TODAY.weekday())
    assert schedule.save(monday.isoformat(), classes)[0]
    return schedule.view(TODAY)


def _cls(day, periods, location, name):
    return {"name": name, "day": day, "periods": periods, "weeks": "1-16",
            "location": location}


BUSY = _view([
    # 今天六节课六栋楼 → 五处换楼:徽章说 5,列表只画前 4 行 + "另有 1 处没列出"
    _cls(TODAY_COL, "1-2", "J1至善楼101", "高等数学"),
    _cls(TODAY_COL, "3-4", "S5致用楼205", "大学英语"),
    _cls(TODAY_COL, "5-6", "J2明德楼301", "数据结构"),
    _cls(TODAY_COL, "7-8", "S4笃行楼402", "计算机网络"),
    _cls(TODAY_COL, "9-10", "S1格致楼103", "操作系统"),
    _cls(TODAY_COL, "11-12", "S2S3博雅楼201", "软件工程"),
    # 另一天只有一处 → 汇总该说"2 天、共 6 处",最多的是今天(5 处)
    _cls(OTHER_COL, "1-2", "学思苑A101", "思想道德与法治"),
    _cls(OTHER_COL, "3-4", "图书馆", "形势与政策"),
])

QUIET = _view([
    _cls(TODAY_COL, "1-2", "J1至善楼101", "高等数学"),
    _cls(TODAY_COL, "3-4", "J1至善楼305", "大学英语"),        # 同一栋 → 不算换楼
])

# 自检:后端真算出了这些数(否则下面的断言全是在验一张空面板)
_w = BUSY["walk_week"]
assert _w["days"] == 2 and _w["total"] == 6, _w
assert _w["worst_date"] == TODAY.isoformat() and _w["worst_count"] == 5, _w
assert BUSY["today"]["walk_total"] == 5, BUSY["today"]["walk_total"]
assert BUSY["week_grid"][OTHER_COL]["walk_total"] == 1
assert QUIET["walk_week"]["days"] == 0 and QUIET["today"]["walk_total"] == 0

STUB_JS = """
window.__E2E_CALLS__ = [];
window.__SC_BUSY__ = @@BUSY@@;
window.__SC_QUIET__ = @@QUIET@@;
window.__schedule__ = window.__SC_BUSY__;
window.__CAMPUS_MAP__ = @@MAP@@;
window.pywebview = {
  api: {
    get_info: () => Promise.resolve({
      version: "0.9.36", app_name: "引路学长",
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
      return Promise.resolve({ok: true, msg: "作息表已存", schedule: window.__schedule__});
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
            .replace("@@BUSY@@", json.dumps(BUSY, ensure_ascii=False))
            .replace("@@QUIET@@", json.dumps(QUIET, ensure_ascii=False))
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

            # 每次重开课表都从「今天」那一档起步(见 openScheduleDlg),要回周视图就得再点一次;
            # 地图收回去后课表遮罩可能还开着,先关掉再点,否则它会拦住按钮。
            def open_week():
                if page.locator("#scheduleOverlay.show").count():
                    page.click("#btnScheduleClose")
                    page.wait_for_selector("#scheduleOverlay:not(.show)", state="attached")
                page.click("#btnSchedule")
                page.wait_for_selector("#scTabs button[data-tab='week']")
                page.click("#scTabs button[data-tab='week']")
                page.wait_for_selector(".sc-col[data-day]")

            # ---- 1. 周视图顶部那句汇总:每个数字都对得上后端 walk_week ----
            open_week()
            sum_el = page.locator(".sc-walksum")
            check(sum_el.count() == 1, "周视图顶部只有一句走堂汇总(不是每天各提示一遍)")
            line = sum_el.inner_text().strip()
            check(f"本周 {_w['days']} 天要换教学楼、共 {_w['total']} 处" in line,
                  f"天数与总处数取自后端:{line}")
            check(f"周{_w['worst_weekday']}最多({_w['worst_count']} 处)" in line,
                  f"最多那天及其处数取自后端:{line}")
            check("只按节次说,没编分钟" in line,
                  "没填作息表 → 这句自己声明没编分钟")
            check(not re.search(r"\d+\s*分钟", line),
                  "汇总句里一个分钟数字都没有")

            # ---- 2. 徽章:今天说 5 处(含没列出的那一处),而且只有今天加重颜色 ----
            chip = page.locator(f".sc-col[data-day='{TODAY_COL}'] .sc-walkchip")
            check(chip.count() == 1 and chip.inner_text().strip() == f"⚠ {_w['worst_count']} 处换楼",
                  f"今天徽章上的处数是 walk_total:{chip.inner_text().strip()}")
            check("max" in (chip.get_attribute("class") or ""),
                  "本周最多那天的徽章确实多了一档 .max(不是只写在 title 里)")
            colored = page.eval_on_selector_all(
                ".sc-walkchip.max",
                "els => els.map(e => e.closest('.sc-col').dataset.day)")
            check(colored == [TODAY_COL], f"全周只有这一天被加重:{colored}")
            other_chip = page.locator(f".sc-col[data-day='{OTHER_COL}'] .sc-walkchip")
            check(other_chip.inner_text().strip() == "⚠ 1 处换楼"
                  and "max" not in (other_chip.get_attribute("class") or ""),
                  f"少的那天照常报数、但不抢那档颜色:{other_chip.inner_text().strip()}")
            check("本周最多的一天" in (chip.get_attribute("title") or ""),
                  "徽章的悬浮说明里写清了为什么加重")

            # ---- 3. 那档颜色是真的可见差异,不是同名 class ----
            def fw(sel):
                return page.eval_on_selector(
                    sel, "e => getComputedStyle(e).fontWeight + '|' + getComputedStyle(e).backgroundColor")
            check(fw(f".sc-col[data-day='{TODAY_COL}'] .sc-walkchip")
                  != fw(f".sc-col[data-day='{OTHER_COL}'] .sc-walkchip"),
                  "加重徽章与普通徽章的实测样式不同(计算样式层面确实长得不一样)")

            # ---- 4. 下钻到当天:徽章说 5、列表只画 4 行 + 一句"另有 1 处没列出" ----
            page.click(f".sc-col[data-day='{TODAY_COL}']")
            page.wait_for_selector(".sc-walk")
            rows = page.locator(".sc-walk")
            check(rows.count() == 4, f"当天列表一屏最多画 4 行(实测 {rows.count()} 行)")
            more = page.locator(".sc-more").filter(has_text="换楼")
            check(more.count() == 1 and "另有 1 处换楼没列出" in more.inner_text(),
                  f"少画的那一行如实说出来:{more.inner_text().strip()}")
            rows.locator(".sc-walkmap").nth(2).click()
            page.wait_for_selector("#mapPanel.open")
            page.wait_for_function("() => document.querySelectorAll('.cm-box').length === 2")
            labels = page.evaluate("() => [...document.querySelectorAll('.cm-box-label')]"
                                   ".map(t => t.textContent)")
            check(labels == ["J2明德楼", "S4笃行楼"],
                  f"第三行点下去框的是它自己那两栋:{labels}")
            panel = page.locator("#scBody").inner_text()
            check(not re.search(r"\d+\s*分钟", panel),
                  "下钻视图同样一处「N 分钟」都没有")

            # ---- 5. 换楼最少的那天(OTHER_COL):周视图有、点进去也有 ----
            open_week()
            page.click(f".sc-col[data-day='{OTHER_COL}']")
            page.wait_for_selector(".sc-walk")
            check(page.locator(".sc-walk").count() == 1, "只有一处换楼的那天如实列一行")

            # ---- 6. 没换楼的一周:汇总句与徽章都不出现(不摆 0 处占位) ----
            page.evaluate("() => { window.__schedule__ = window.__SC_QUIET__; }")
            open_week()
            check(page.locator(".sc-walksum").count() == 0,
                  "整周没换楼 → 顶部那句汇总不出现(不是写成「0 天」)")
            check(page.locator(".sc-walkchip").count() == 0,
                  "没换楼的格子里不长徽章(不是摆了个「0 处」占位)")

            # ---- 7. 页面不许有 JS 错(白屏类回归) ----
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
    print("v0.9.36 前端回归:全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
