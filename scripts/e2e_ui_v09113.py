# -*- coding: utf-8 -*-
"""v0.9.113 前端回归:无边框那一套 + 成绩那一屏 + 右缘导航(用户 9 张截图的界面半边)。

跑法(项目根目录):
    PYTHONIOENCODING=utf-8 .venv\\Scripts\\python.exe scripts\\e2e_ui_v09113.py

覆盖(每一条都是"点得到、看得见",不是源码里有这几个字):
    新 「收起左侧栏」搬到对话区左上角:点它真的收起、Ctrl+B 真的翻、重开还记着(p1/p2)
    新 无边框三颗 −□✕:只在窗口真的没有标题栏时出现,点下去真的敲到桥(p5/p6)
    新 有标题栏时那三颗与边缘把手**不出现**(两处都摆 = 两排关闭按钮)
    新 边缘把手 8 条与后端 EDGES 一一对上,拖一条真的发 win_resize(p7 的代价补票)
    新 课表面板「成绩」那一档:六个数 + 学期下拉;没登记课表也看得到成绩(p3/p4)
    新 读不到的那一项写「—」而不是 0(没有来源的数字不许装成数字)
    新 右缘导航:离边框留距、浅色下刻度真的看得见、悬停那一条凸显、跳过去的那一枚一直亮(p8/p9)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "scripts"))

from e2e_ui_v09111 import _launch, _serve    # noqa: E402  复用同一套起服务/起浏览器

FRONTEND = ROOT / "frontend"

STATS_ALL = {
    "ok": True, "empty": False, "entries": 42, "courses": 42, "credits": 74.5,
    "avg": 84.31, "weighted": 84.02, "gpa5": 3.4, "gpa4": 3.36,
    "semesters": ["2023-2024-1", "2023-2024-2", "2024-2025-1"], "semester": "",
    "counted": 40, "skipped": 2,
    "rows": [{"name": f"课程{i}", "score": str(70 + i), "credit": "3",
              "semester": "2024-2025-1"} for i in range(12)],
    "rows_truncated": True,
    "note": "等级成绩按常见折算;以学校教务处绩点算法为准。",
    "source": "教务导入", "updated_at": "2026-10-04 12:31:08",
}
STATS_ONE = dict(STATS_ALL, entries=11, courses=11, credits=19.0, avg=88.4,
                 weighted=88.1, gpa5=3.81, gpa4=3.62, semester="2024-2025-1",
                 skipped=0, rows=STATS_ALL["rows"][:6], rows_truncated=False)

SCHED = {"ok": True, "schedule": {"registered": False}}

STUB = r"""
const CALLS = [];
window.__CALLS = CALLS;
window.__errs = [];
window.addEventListener("error", (e) => window.__errs.push(String(e.message || e)));
window.__borderless = true;
const STATS = __INJ_STATS__;
const STATS1 = __INJ_STATS1__;
const SCHED = __INJ_SCHED__;
const API = {
  get_info: () => Promise.resolve({app_name: "引路学长", version: "0.9.113",
    permission_mode: "confirm", permission_modes: [{value:"confirm",label:"变更前确认"}],
    session_key: "s1", model: "deepseek-v4-flash", active_provider: "builtin",
    providers: [], api_key_configured: true, workspace: "D:/demo/daily",
    workspace_name: "daily", workspace_is_default: false,
    memory_count: 0, memory_user_count: 0, mcp_servers: [], sandbox: {}, vision: false,
    campus_map: null, library_health: {}, skills: [], kb_count: 0,
    session_metrics: {ok: true, model: "m", capabilities: [], selectable: [],
      known: true, context_window: 0, context_tokens: 0, consumed: 0}}),
  get_session_metrics: () => Promise.resolve({ok: true, model: "m", capabilities: [],
    selectable: [], known: true, context_window: 0, context_tokens: 0, consumed: 0}),
  get_activity: () => Promise.resolve({ok: true, now: null, timeline: {calls: 0},
    summary: {calls: 0}, changes: {ok: true, total: 0, items: [], note: ""},
    updated_at: "", empty: true, groups: []}),
  get_env_info: () => Promise.resolve({}),
  get_monitor_pref: () => Promise.resolve({open: false, folded: {}}),
  set_monitor_pref: () => Promise.resolve({ok: true}),
  get_reasoning: () => Promise.resolve({visible: false, disabled: true,
    kind: "hidden", choice: "default", hint: "", options: []}),
  get_suggestions: () => Promise.resolve({suggestions: []}),
  get_countdowns: () => Promise.resolve({ok: true, countdowns: []}),
  get_recent_projects: () => Promise.resolve({ok: true, current: {}, default: {},
    recent: []}),
  refresh_sessions: () => Promise.resolve({ok: true}),
  rate_message: () => Promise.resolve({ok: true}),
  send_task: () => Promise.resolve({ok: true, session: "s1"}),
  get_schedule_view: () => Promise.resolve(SCHED),
  get_score_stats: (sem) => { CALLS.push(["get_score_stats", sem || ""]);
    return Promise.resolve({ok: true,
      stats: sem ? Object.assign({}, STATS1, {semester: sem}) : STATS}); },
  win_state: () => { CALLS.push(["win_state"]);
    return Promise.resolve({ok: true, borderless: window.__borderless,
      maximized: false, rect: [100, 100, 1380, 920],
      work_area: [0, 0, 1920, 1032], bounds_ok: true}); },
  win_minimize: () => { CALLS.push(["win_minimize"]);
    return Promise.resolve({ok: true, msg: ""}); },
  win_zoom: () => { CALLS.push(["win_zoom"]);
    return Promise.resolve({ok: true, action: "maximize", maximized: true,
                            bounds: {ok: true, msg: ""}, msg: ""}); },
  win_close: () => { CALLS.push(["win_close"]);
    return Promise.resolve({ok: true, msg: ""}); },
  win_resize: (edge, dx, dy) => { CALLS.push(["win_resize", edge, dx, dy]);
    return Promise.resolve({ok: true, rect: [100, 100, 1380 + dx, 920 + dy], msg: ""}); },
  // 认不出表头那一种失败(p3/p4 的形状):页在、表在,只是列名对不上。
  // (`jwApi()` 认的是 schedule_import_open 在不在 —— 桥没这两颗时向导会退回文字导入,
  //  所以两个都得给,不然测的是"退路"而不是向导本身。)
  schedule_import_open: () => Promise.resolve({ok: true, stage: "need_login",
    url: "https://jw.example/gzasc/", title: "教务", msg: "登录页开了"}),
  scores_import_open: () => Promise.resolve({ok: true, stage: "need_login",
    url: "https://jw.example/gzasc/", title: "教务", msg: "登录页开了"}),
  scores_import_read: () => { CALLS.push(["scores_import_read"]);
    return Promise.resolve({ok: false, stage: "no_grid", rows: [],
      msg: "翻过 2 张页、3 个帧,都没认出成绩表(要有「课程/成绩」这样的表头)。"
         + " 学长翻到的表头长这样:序号 / 课程名称 / 课程性质 / 学分 / 成绩 / 成绩标识"}); },
};
const inject = () => setTimeout(() => {
  window.pywebview = { api: API };
  window.dispatchEvent(new Event("pywebviewready"));
}, 30);
if (document.readyState === "complete") inject();
else window.addEventListener("load", inject);
""".replace("__INJ_STATS__", json.dumps(STATS_ALL, ensure_ascii=False)) \
   .replace("__INJ_STATS1__", json.dumps(STATS_ONE, ensure_ascii=False)) \
   .replace("__INJ_SCHED__", json.dumps(SCHED, ensure_ascii=False))


def _fill_chat(page, n=12):
    """塞 n 问真消息节点并重算刻度(与 v0.9.112 那一场同一个手法)。"""
    page.evaluate("""(n) => {
      const chat = document.getElementById('chat');
      chat.innerHTML = '';
      for (let i = 1; i <= n; i++) {
        const d = document.createElement('div');
        d.className = 'msg user';
        d.dataset.uord = String(i);
        d.innerHTML = `<div class="bubble">第 ${i} 个问题:帮我看这一段`
          + "很长".repeat(14) + `</div>`;
        chat.appendChild(d);
      }
      rebuildChatRail();
    }""", n)


def main() -> int:
    from playwright.sync_api import sync_playwright
    url, shutdown = _serve(FRONTEND)
    failures = []

    def check(cond, message):
        if not cond:
            failures.append(message)
        print(("  ✓ " if cond else "  ✗ ") + message)

    try:
        with sync_playwright() as p:
            browser = _launch(p)
            ctx = browser.new_context(viewport={"width": 1280, "height": 900})
            ctx.add_init_script(STUB)
            page = ctx.new_page()
            page.goto(url, wait_until="load")
            page.wait_for_function("() => document.getElementById('btnSideToggle')"
                                   " && window.pywebview && pywebview.api.get_info")
            page.wait_for_timeout(500)

            # ---- 1. 收起栏搬到了对话区左上角(p1/p2) ----
            geo = page.evaluate("""() => {
              const b = document.getElementById('btnSideToggle');
              const tb = document.querySelector('.topbar').getBoundingClientRect();
              const rl = document.querySelector('.rail').getBoundingClientRect();
              const rb = b.getBoundingClientRect();
              return {inTopbar: rb.top >= tb.top - 1 && rb.bottom <= tb.bottom + 1,
                      leftOfPill: rb.left < document.getElementById('wsPill')
                        .getBoundingClientRect().left,
                      inRail: rb.left < rl.right,
                      x: rb.left, y: rb.top, w: rb.width};
            }""")
            check(geo["inTopbar"] and geo["leftOfPill"] and not geo["inRail"],
                  f"收起栏在对话区顶行的最左(x={geo['x']:.0f}, y={geo['y']:.0f}),"
                  " 不在入口栏里")
            check(geo["w"] < 40, f"它是图标一颗({geo['w']:.0f}px),不占文字位")

            page.click("#btnSideToggle")
            page.wait_for_timeout(250)
            st = page.evaluate("""() => ({
              closed: document.querySelector('.app').classList.contains('side-closed'),
              sw: document.querySelector('.sidebar').getBoundingClientRect().width,
              pane: getComputedStyle(document.querySelector('#btnSideToggle .pane')).opacity,
              title: document.getElementById('btnSideToggle').title,
              stored: localStorage.getItem('gcode-side')})""")
            check(st["closed"] and st["sw"] < 2,
                  f"点一下真的收起(侧栏宽 {st['sw']:.0f}px)")
            check(float(st["pane"]) < 0.3 and "展开" in st["title"],
                  f"按钮自己也在说事实:图标那一半淡到 {st['pane']}、title「{st['title']}」")
            page.keyboard.press("Control+b")
            page.wait_for_timeout(250)
            check(page.evaluate("() => !document.querySelector('.app')"
                                ".classList.contains('side-closed')"),
                  "Ctrl+B 与点按钮是同一条路(不是两套状态)")

            # 重开窗口还记着(同一个 context 的新页面 = 同一份 localStorage)
            page2 = ctx.new_page()
            page2.goto(url, wait_until="load")
            page2.wait_for_function("() => document.getElementById('btnSideToggle')")
            page2.wait_for_timeout(400)
            check(page2.evaluate("() => document.querySelector('.app')"
                                 ".classList.contains('side-closed')") is False,
                  "上一次开着的,新窗口开机也开着(习惯不该每次重摆)")
            page2.close()

            # ---- 2. 无边框三颗 + 边缘把手(只在真没标题栏时出现) ----
            vis = page.evaluate("""() => {
              const v = (id) => { const e = document.getElementById(id);
                const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
              const handles = document.querySelectorAll('#rz [data-e]').length;
              return {bar: !document.getElementById('winBtns').hidden,
                      rz: !document.getElementById('rz').hidden,
                      min: v('winMin'), max: v('winMax'), close: v('winClose'),
                      handles,
                      calls: (window.__CALLS || []).filter(c => c[0] === 'win_state').length};
            }""")
            check(vis["bar"] and vis["min"] and vis["max"] and vis["close"],
                  "无边框时 −□✕ 三颗都在,而且点得到")
            check(vis["handles"] == 8 and vis["rz"],
                  f"边缘把手 {vis['handles']} 条(上/下/左/右 + 四角)")
            check(vis["calls"] >= 1, "界面是**问过**窗口才摆的(win_state),不是照配置猜")
            page.click("#winMin")
            page.click("#winMax")
            page.wait_for_timeout(150)
            drag_box = page.evaluate("""() => {
              const r = document.getElementById('tbDrag').getBoundingClientRect();
              return {w: r.width, h: r.height};
            }""")
            check(drag_box["h"] > 20 and drag_box["w"] > 40,
                  f"拖拽区是顶栏中间那段空的({drag_box['w']:.0f}×{drag_box['h']:.0f}),"
                  " 不是整条(整条会把选字变成挪窗)")
            page.dblclick("#tbDrag")
            page.click("#winClose")
            page.wait_for_timeout(200)
            calls = page.evaluate("() => (window.__CALLS || []).map(c => c[0])")
            for want in ("win_minimize", "win_zoom", "win_close"):
                check(want in calls, f"{want} 真的敲到了桥")
            maxed = page.evaluate("""() => ({t: document.getElementById('winMax').title,
                                             s: document.getElementById('winMax').textContent})""")
            check("还原" in maxed["t"] and maxed["s"] == "❐",
                  f"最大化之后那颗自己变成「还原」(title「{maxed['t']}」{maxed['s']})")

            # 拖一条边:发的必须是增量,而且只发一次(rAF 合流)
            page.evaluate("() => { window.__CALLS.length = 0; }")
            box = page.evaluate("""() => {
              const e = document.querySelector('#rz [data-e=e]');
              const r = e.getBoundingClientRect();
              return {x: r.left + r.width / 2, y: r.top + r.height / 2};
            }""")
            page.mouse.move(box["x"], box["y"])
            page.mouse.down()
            for step in (6, 6, 6):
                page.mouse.move(box["x"] + step, box["y"])
                page.wait_for_timeout(60)
            page.mouse.up()
            rz_calls = [c for c in page.evaluate("() => window.__CALLS")
                        if c[0] == "win_resize"]
            check(rz_calls and all(c[1] == "e" for c in rz_calls),
                  f"拖右边发的是 win_resize('e', …),共 {len(rz_calls)} 次(合流后不逐个排队)")
            check(sum(c[2] for c in rz_calls) > 0,
                  f"位移是增量累加的:{[c[2] for c in rz_calls]}")

            # 有系统标题栏的那台机器:两套都不许出现
            page.evaluate("() => { window.__borderless = false; winInit(); }")
            page.wait_for_timeout(300)
            hidden = page.evaluate("""() => ({
              bar: document.getElementById('winBtns').hidden,
              rz: document.getElementById('rz').hidden})""")
            check(hidden["bar"] and hidden["rz"],
                  "窗口其实有标题栏时,三颗与把手都收着(不摆两排关闭按钮)")
            page.evaluate("() => { window.__borderless = true; winInit(); }")
            page.wait_for_timeout(200)

            # ---- 3. 课表面板里的「成绩」(p3/p4) ----
            page.click("#btnSchedule")
            page.wait_for_selector("#scheduleOverlay.show")
            page.wait_for_timeout(300)
            empty = page.evaluate("() => document.getElementById('scBody').innerText")
            check("还没有登记课表" in empty,
                  "没课表时默认那一档说的是课表(本机第一次,正是同学的状态)")
            page.click('#scTabs button[data-tab="scores"]')
            page.wait_for_timeout(500)
            panel = page.evaluate("""() => {
              const body = document.getElementById('scBody');
              const cards = [...body.querySelectorAll('.sc-stat')]
                .map(e => ({k: e.querySelector('.k').textContent.trim(),
                            v: e.querySelector('.v').textContent.trim()}));
              return {text: body.innerText, cards,
                      sems: [...body.querySelectorAll('#scSemSel option')]
                        .map(o => o.value),
                      on: document.querySelector('#scTabs button[data-tab=scores]')
                        .classList.contains('on'),
                      rows: body.querySelectorAll('.sc-crow').length};
            }""")
            want = {"五分制绩点": "3.4", "4.0 制绩点": "3.36", "平均成绩": "84.31",
                    "加权平均分": "84.02", "科目数量": "42 门", "总学分": "74.5 分"}
            got = {c["k"]: c["v"] for c in panel["cards"]}
            check(got == want, f"六个数摆出来了,与后端那一份一字不差:{got}")
            check(panel["on"], "「成绩」那一档自己亮着")
            check(len(panel["sems"]) == 4 and panel["sems"][0] == "",
                  f"学期下拉:全部 + 存档里真出现过的 {len(panel['sems']) - 1} 个学期")
            check("以学校教务处" in panel["text"] and "不进对话" in panel["text"],
                  "口径与隐私那两句写在同一屏(不是只给个数字)")
            check("2 条没参与加权" in panel["text"],
                  "跳过的行如实报数(缺学分不参与加权,但条数照实)")
            check("只列前 12 条" in panel["text"],
                  "清单被截断时说清「数字按全量算」")
            page.select_option("#scSemSel", "2024-2025-1")
            page.wait_for_timeout(500)
            one = page.evaluate("""() => ({
              cards: [...document.querySelectorAll('#scBody .sc-stat .v')]
                .map(e => e.textContent.trim()),
              rows: document.querySelectorAll('#scBody .sc-crow').length,
              sm: [...document.querySelectorAll('#scBody .sc-crow .sm')].length})""")
            check(one["cards"][:6] == ["3.81", "3.62", "88.4", "88.1", "11 门", "19 分"],
                  f"换学期真的重算了:{one['cards']}")
            last = page.evaluate("() => window.__CALLS.filter(c => c[0]=='get_score_stats')"
                                 ".slice(-1)[0]")
            check(last[1] == "2024-2025-1", f"发出去的是选中的那个学期:{last}")
            check(one["sm"] == 0, "筛到单个学期后不再重复标学期(那一列没意义)")

            # 读不到的一律「—」,不摆 0
            page.evaluate("""() => { scoreStats = {ok: false, empty: false, entries: 0,
              courses: 0, credits: 0, avg: null, weighted: null, gpa5: null, gpa4: null,
              semesters: [], semester: '', skipped: 0, rows: [],
              note: '这一支不测存档'}; scoreBusy = false; renderSchedule(); }""")
            dashes = page.evaluate("""() => [...document.querySelectorAll('#scBody .sc-stat .v')]
              .map(e => e.textContent.trim())""")
            check(dashes.count("—") >= 4, f"没有来源的数写「—」而不是 0:{dashes}")
            page.evaluate("() => { scoreStats = null; scoreBusy = false; scStatsGet(''); }")
            page.wait_for_timeout(400)

            # ---- 3.5 认不出表头时给的是"摊开给学长看",不是再点一次同样的失败 ----
            page.evaluate("() => { scoreStats = null; scoreBusy = false; }")
            page.click("#btnScoresImport")
            page.wait_for_selector("#jwOverlay.show")
            page.wait_for_timeout(300)
            page.click("#btnJwRead")
            page.wait_for_timeout(500)
            peek = page.evaluate("""() => {
              const b = document.getElementById('lnJwPeek');
              const warn = document.querySelector('.jw-warn');
              return {has: !!b, warn: warn ? warn.innerText : "",
                      retry: (document.getElementById('lnJwRetry') || {}).textContent};
            }""")
            check(peek["has"], "认不出表头时多了一颗「让学长看这一页的表格结构」")
            check("成绩标识" in peek["warn"] and "3 个帧" in peek["warn"],
                  "失败那一句把翻到的表头原样写出来了(下一轮不必再猜)")
            check(peek["retry"] == "再读一次", "页在、表在 → 「再读一次」仍是合理的(不是 no_page)")
            page.click("#lnJwPeek")
            page.wait_for_timeout(300)
            filled = page.evaluate("""() => ({
              v: document.getElementById('input').value,
              closed: !document.getElementById('jwOverlay').classList.contains('show')})""")
            check("page_parse" in filled["v"] and "先别存任何东西" in filled["v"],
                  "点下去真的把那句要结构的话填进输入框(只读,不替同学存)")
            check(filled["closed"], "向导收起来,人接着就在输入框边上下一步")

            # ---- 4. 右缘导航(p8/p9) ----
            page.evaluate("() => document.getElementById('scheduleOverlay')"
                          ".classList.remove('show')")
            _fill_chat(page, 12)
            page.wait_for_selector("#chatRail:not([hidden])")
            rail = page.evaluate("""() => {
              const r = document.getElementById('chatRail');
              const w = document.querySelector('.chat-wrap').getBoundingClientRect();
              const rr = r.getBoundingClientRect();
              const t = document.querySelector('#chatRail .tick');
              const cs = getComputedStyle(t);
              const root = getComputedStyle(document.documentElement);
              return {gapRight: w.right - rr.right, width: rr.width,
                      opacity: getComputedStyle(r).opacity,
                      tickBg: cs.backgroundColor, tickW: t.getBoundingClientRect().width,
                      line: 'rgba(40, 80, 72, 0.14)',
                      token: root.getPropertyValue('--rail-tick').trim()};
            }""")
            check(8 <= rail["gapRight"] <= 20,
                  f"刻度离右边框 {rail['gapRight']:.0f}px(Qoder 那样留了一段,不贴死)")
            check(float(rail["opacity"]) >= 0.85,
                  f"不 hover 也看得见(opacity {rail['opacity']})")
            check(rail["tickBg"] != rail["line"] and "rgba(0, 0, 0, 0)" != rail["tickBg"],
                  f"浅色下刻度色是加深过的那一档:{rail['tickBg']}")
            ticks = page.locator("#chatRail .tick")
            before = ticks.nth(3).bounding_box()["width"]
            ticks.nth(3).hover()
            page.wait_for_timeout(200)
            after = page.evaluate("""() => {
              const t = document.querySelectorAll('#chatRail .tick')[3];
              const cs = getComputedStyle(t);
              return {w: t.getBoundingClientRect().width, sh: cs.boxShadow};
            }""")
            check(after["w"] > before and "rgb" in after["sh"],
                  f"鼠标停在哪一条,那一条变宽({before:.0f}→{after['w']:.0f}px)并描一圈光")
            page.click("#chatRail .tick-head")
            page.wait_for_selector("#railPop.turns:not([hidden])")
            page.evaluate("() => document.querySelectorAll('#railPop .turn-card')[5].click()")
            page.wait_for_timeout(700)
            lit = page.evaluate("""() => {
              const on = [...document.querySelectorAll('#chatRail .tick')]
                .map((t, i) => [i, t.classList.contains('on')]).filter(x => x[1]);
              return {on, n: document.querySelectorAll('#chatRail .tick').length};
            }""")
            check(len(lit["on"]) == 1 and lit["on"][0][0] == 6,
                  f"跳过去的那一枚刻度一直亮着(第 {lit['on'] and lit['on'][0][0] + 1} 问,"
                  f"共 {lit['n']} 枚)—— 不用滚回去找")
            _fill_chat(page, 12)
            relit = page.evaluate("() => document.querySelectorAll('#chatRail .tick.on').length")
            check(relit == 1, "重建刻度之后那一枚还是亮的(不是只亮一次就丢)")

            errs = page.evaluate("() => window.__errs || []")
            check(not errs, f"页面没有未捕获错误:{errs}")
            browser.close()
    finally:
        shutdown()

    print("\n" + ("全部通过" if not failures else f"红 {len(failures)} 条:"))
    for f in failures:
        print("  - " + f)
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
