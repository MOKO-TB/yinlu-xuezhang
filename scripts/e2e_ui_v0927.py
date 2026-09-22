# -*- coding: utf-8 -*-
"""v0.9.27 前端回归:课表面板的空档/待办叠加、当天下钻、填作息表。

**payload 由真后端算**:`minicode.schedule.view()` 在临时 APP_DIR 里读夹具课表生成
JSON,再喂给 stub 的 `get_schedule_view`。这样面板渲染的是真字段,而不是我手抄一份
"我以为后端会返回的形状" —— 那种 e2e 只会一直绿着骗人。

跑法(项目根目录,需要 PYTHONIOENCODING=utf-8 否则中文断言在 GBK 控制台炸):
    PYTHONIOENCODING=utf-8 .venv\\Scripts\\python.exe scripts\\e2e_ui_v0927.py
"""
from __future__ import annotations

import json
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


# --------------------------------------------------------------- 后端真视图

def build_payloads():
    """返回 (没填作息表的视图, 填了作息表的视图) —— 都出自 schedule.view() 本体。"""
    from minicode import calendar, paths, reminders, schedule

    tmp = Path(tempfile.mkdtemp(prefix="yx-v0927-"))
    paths.APP_DIR = tmp / "app"
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    thursday = monday + timedelta(days=3)
    # 开学周一故意设在**本周四**:周一~周三落在学期外,正好验"不在学期内 ≠ 有空"
    classes = [
        {"name": "高等数学", "day": 5, "periods": "1-2", "start_time": "08:00",
         "weeks": "1-16", "location": "思齐楼B203"},
        {"name": "体育课", "day": 5, "periods": "5-6", "weeks": "1-16",
         "location": "田径场"},
        {"name": "选修课", "day": 7, "weeks": "1-16"},        # 没写节次 → 算不准
    ]
    assert schedule.save(thursday.isoformat(), classes)[0]
    assert reminders.add("交实验报告", due=(monday + timedelta(days=4)).isoformat())["ok"]
    monkey_events = ([
        {"name": "期中考试周", "start": (monday + timedelta(days=4)).isoformat(),
         "end": (monday + timedelta(days=6)).isoformat(), "kind": "exam"},
    ], "夹具")
    calendar.load_events = lambda: monkey_events
    plain = schedule.view(today)
    assert schedule.save_period_times(
        [{"start": "08:00", "end": "08:45"}, {"start": "08:55", "end": "09:40"},
         {"start": "10:00", "end": "10:45"}, {"start": "10:55", "end": "11:40"},
         {"start": "14:00", "end": "14:45"}, {"start": "14:55", "end": "15:40"}],
        "夹具作息")[0]
    clocked = schedule.view(today)
    return plain, clocked


PLAIN, CLOCKED = build_payloads()

STUB_JS = """
window.__E2E_CALLS__ = [];
window.__SC_PLAIN__ = @@PLAIN@@;
window.__SC_CLOCKED__ = @@CLOCKED@@;
window.__schedule__ = window.__SC_PLAIN__;
window.pywebview = {
  api: {
    get_info: () => Promise.resolve({
      version: "0.9.27", app_name: "引路学长",
      permission_mode: "confirm",
      permission_modes: [{value: "confirm", label: "变更前确认"}],
      session_key: "s1", model: "deepseek-v4-flash", active_provider: "gzasc",
      providers: [{id: "gzasc", name: "广州应用科技学院",
                   model: "deepseek-v4-flash", has_key: true}],
      api_key_configured: true, workspace: "D:/demo/campus",
      workspace_name: "campus", workspace_is_default: true,
      memory_count: 0, memory_user_count: 0, mcp_servers: [],
      sandbox: {}, vision: false, campus_map: null, library_health: {},
      skills: [], kb_count: 0,
      session_metrics: {ok: false, selectable: []}
    }),
    get_schedule_view: () => Promise.resolve({ok: true, schedule: window.__schedule__}),
    set_notice_watch: () => Promise.resolve({ok: true}),
    open_url: () => Promise.resolve({ok: true}),
    send_task: () => Promise.resolve({ok: true, session: "s1"}),
    get_suggestions: () => Promise.resolve({suggestions: []}),
    get_countdowns: () => Promise.resolve({ok: true, countdowns: []}),
    rate_message: () => Promise.resolve({ok: true}),
    compact_now: () => Promise.resolve({ok: true}),
    refresh_sessions: () => Promise.resolve({ok: true}),
    get_recent_projects: () => Promise.resolve({ok: true, current: {}, default: {}, recent: []}),
    get_reasoning: () => Promise.resolve({visible: false, options: [], choice: "default"}),
    get_session_metrics: () => Promise.resolve({ok: false}),
    schedule_save_times: (rowsJson) => {
      window.__E2E_CALLS__.push(["schedule_save_times", rowsJson]);
      window.__schedule__ = window.__SC_CLOCKED__;      // 存成功后后端会连带回新视图
      return Promise.resolve({ok: true, msg: "作息表已存",
                              schedule: window.__SC_CLOCKED__});
    },
  }
};
"""


def _serve(directory: Path):
    class _QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = partial(_QuietHandler, directory=str(directory))
    port = None
    for candidate in range(18530, 18600):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", candidate), handler)
            port = candidate
            break
        except OSError:
            continue
    assert port is not None, "18530-18599 里找不到可绑定的端口"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}/index.html", httpd.shutdown


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

    stub = (STUB_JS.replace("@@PLAIN@@", json.dumps(PLAIN, ensure_ascii=False))
            .replace("@@CLOCKED@@", json.dumps(CLOCKED, ensure_ascii=False)))
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
            page.add_init_script(stub)
            page.goto(url, wait_until="load")
            page.wait_for_function("() => document.getElementById('btnSchedule') !== null")

            # ---- 1. 打开面板切到「本周」:空档/待办/学期外各有其形状 ----
            page.click("#btnSchedule")
            page.wait_for_selector("#scTabs button[data-tab='week']")
            page.click("#scTabs button[data-tab='week']")
            page.wait_for_selector(".sc-col[data-day]")
            check(page.locator(".sc-col[data-day]").count() == 7, "周网格七天齐全")
            fri = page.locator(".sc-col[data-day='5']")
            check(fri.locator(".sc-gap").first.inner_text().strip() == "第 3-4 节",
                  "周五格子里的空档写着「第 3-4 节」(没作息表就不带钟点)")
            check(fri.locator(".sc-todo").count() == 1
                  and "交实验报告" in fri.locator(".sc-todo").inner_text(),
                  "周五格子叠出当天要交的待办")
            check(fri.locator(".sc-evt").count() == 1
                  and "期中考试周" in fri.locator(".sc-evt").inner_text(),
                  "周五格子叠出校历事件")
            check(page.locator(".sc-col[data-day='6'] .sc-evt").count() == 0
                  and page.locator(".sc-col[data-day='7'] .sc-evt").count() == 0,
                  "跨四天的考试周只在首日出现一次(不重复铺满)")
            check(page.locator(".sc-col[data-day='1'] .sc-off").inner_text().strip() == "不在学期内",
                  "开学前的周一只写「不在学期内」,不写成有空")
            check(page.locator(".sc-col[data-day='6'] .sc-free").count() == 1,
                  "没课的周六是一个明确的「没课」块(全天)")
            check("算不准" in page.locator(".sc-col[data-day='7'] .sc-off").inner_text(),
                  "没写节次的那天空档标成「算不准」而不是瞎排")
            check("空档只按节次说" in page.locator("#scBody .sc-note").last.inner_text(),
                  "底部说明交代了当前口径:没填作息表不给钟点")

            # ---- 2. 点某天下钻:空档段 + 待办 + 校历分区,且能退回本周 ----
            fri.click()
            page.wait_for_selector(".sc-freerow")
            body = page.locator("#scBody").inner_text()
            check("空档" in body and "第 3-4 节" in body and "只按节次" in body,
                  "当天下钻列出空档段并标「只按节次」")
            check("这天要交" in body and "交实验报告" in body, "当天下钻列出要交的待办")
            check("校历安排" in body and "期中考试周" in body, "当天下钻列出校历安排")
            check(page.locator("#scTabs button[data-tab='week']").get_attribute("class")
                  .count("on") == 1, "下钻时「本周」标签仍是亮的(不丢位置感)")
            page.click(".sc-back")
            page.wait_for_selector(".sc-col[data-day]")
            check(page.locator(".sc-col[data-day]").count() == 7, "「← 回到本周」退回网格")

            # ---- 3. 填作息表:表单 → 桥 → 回来变成钟点口径 ----
            page.click("#btnScheduleTimes")
            page.wait_for_selector(".sc-trow")
            rows = page.locator(".sc-trow").count()
            check(rows == 8, f"默认列 8 节课可改(现在 {rows} 行)")
            page.locator('[data-act="times-plus"]').click()
            check(page.locator(".sc-trow").count() == rows + 1, "「+1 节」加一行")
            page.fill('.sc-in[data-n="1"][data-k="start"]', "08:00")
            page.fill('.sc-in[data-n="1"][data-k="end"]', "08:45")
            page.locator('[data-act="times-save"]').click()
            page.wait_for_selector("#scTabs button[data-tab='week']")
            check("作息表没保存" not in page.locator("#toasts").inner_text()
                  if page.locator("#toasts").count() else True, "缺第 2 节时前端先拦下空行")
            # 填齐再存:桥只该收到完整的一列 {period,start,end}
            for n in range(1, rows + 2):
                page.fill(f'.sc-in[data-n="{n}"][data-k="start"]', f"{8 + (n - 1) // 2:02d}:00")
                page.fill(f'.sc-in[data-n="{n}"][data-k="end"]', f"{8 + (n - 1) // 2:02d}:45")
            page.locator('[data-act="times-save"]').click()
            page.wait_for_selector(".sc-col[data-day]")
            calls = page.evaluate("() => window.__E2E_CALLS__.filter(c => c[0]==='schedule_save_times')")
            sent = json.loads(calls[-1][1])
            check(len(sent) == rows + 1 and sent[0]["period"] == 1
                  and sent[0]["start"] == "08:00", "保存发给桥的是逐节起止的结构(带 period)")
            check(page.locator(".sc-col[data-day='5'] .sc-gap").first.inner_text().strip()
                  == "第 3-4 节 10:00~11:40", "存完作息表后空档立刻带上钟点区间")
            check("空档已按你填的作息表给钟点" in page.locator("#scBody .sc-note").last.inner_text(),
                  "底部口径跟着改成「已按作息表」")

            # ---- 4. 已填过再进表单:行数跟着已存节数走 ----
            page.click("#btnScheduleTimes")
            page.wait_for_selector(".sc-trow")
            check(page.locator(".sc-trow").count() == 6
                  and "现在存着第 1-6 节" in page.locator("#scBody").inner_text(),
                  "表单默认列已存的 6 节并回显出处")
            page.locator('[data-act="times-clear"]').click()
            page.wait_for_selector(".sc-col[data-day]")
            cleared = json.loads(page.evaluate(
                "() => window.__E2E_CALLS__.filter(c => c[0]==='schedule_save_times').slice(-1)[0][1]"))
            check(cleared == [], "「不填了」发的是空数组(后端据此清掉本机文件)")
            browser.close()
    finally:
        shutdown()

    print()
    if failures:
        print(f"共 {len(failures)} 项失败:")
        for f in failures:
            print("  ✗ " + f)
        return 1
    print("v0.9.27 前端回归:全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
