# -*- coding: utf-8 -*-
"""v0.9.114 前端回归:用户第三轮截图里属于界面那半边 + 点名的两个新功能。

跑法(项目根目录):
    PYTHONIOENCODING=utf-8 .venv\\Scripts\\python.exe scripts\\e2e_ui_v09114.py

覆盖(每一条都是真点、真量,不是源码里有这几个字):
    新 ✕/−/□ 三颗点下去真的敲到桥;桥回 ok:false 时界面必须说一句(图 p7 那颗死钮)
    新 收起栏图标**看得见**(量描边色与尺寸),三颗窗口按钮的字形垂直居中(图 p1)
    新 入口栏正常高度下不出滚动条,矮窗口里仍然滚得到所有入口(图 p3)
    新 「选学期」是自绘浮层:深色下背景跟着主题走,点选项真的重算(图 p2)
    新 自动化「新建」:填名字/内容/节奏真的发 add_automation,空内容不发(图 p5/p6)
    新功能① 计划核对单:批准 / 改一条 / 重排 三颗各发什么,重排没写理由就不发
    新功能③ 技能分享包:详情里那颗「打包分享」+ 底栏「导入分享包」都在
    新 托盘推的 open_panel(new_task/providers)在界面侧真的有落点
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

STATS = {"ok": True, "empty": False, "entries": 12, "courses": 12, "credits": 21.0,
         "avg": 86.2, "weighted": 85.9, "gpa5": 3.59, "gpa4": 3.51,
         "semesters": ["2023-2024-2", "2024-2025-1"], "semester": "",
         "counted": 12, "skipped": 0,
         "rows": [{"name": "高等数学", "score": "89", "credit": "4",
                   "semester": "2024-2025-1"}],
         "rows_truncated": False, "note": "以学校教务处绩点算法为准。",
         "source": "教务导入", "updated_at": "2026-10-04 12:31:08"}
JOBS = {"ok": True, "max": 20, "handlers": ["kb_gap_digest"], "msg": "", "jobs": [
    {"id": "mood_weekly_review", "label": "周复盘", "cadence": "weekly",
     "rhythm": "每周周日 20 点后", "hour": 20, "minute": 0, "weekday": 6,
     "every": 1, "enabled": True, "due_now": False, "next": "10-11 20:00",
     "next_hint": "", "last": "2026-09-27 20:04", "runs": 1, "result": "已汇总"},
    {"id": "u1", "label": "我自己加的", "cadence": "daily", "rhythm": "每天 21 点后",
     "hour": 21, "minute": 0, "weekday": None, "every": 1, "enabled": True,
     "due_now": False, "next": "10-05 21:00", "next_hint": "", "last": "",
     "runs": 0, "result": "", "user": True},
]}
SKILLS = {"ok": True, "skills": [
    {"name": "搜书指南", "description": "在馆藏里找一本书", "channel": "app",
     "uses": 3, "last": "2026-10-04 10:00"}]}

STUB = r"""
const CALLS = [];
window.__CALLS = CALLS;
window.__errs = [];
window.addEventListener("error", (e) => window.__errs.push(String(e.message || e)));
window.__borderless = true;
window.__closeOk = true;
const STATS = __INJ_STATS__;
const JOBS = __INJ_JOBS__;
const SKILLS = __INJ_SKILLS__;
function rec(name, args) { CALLS.push([name, args]); }
const API = {
  get_info: () => Promise.resolve({app_name: "引路学长", version: "0.9.114",
    permission_mode: "confirm", permission_modes: [{value:"confirm",label:"变更前确认"}],
    session_key: "s1", model: "m", active_provider: "builtin", providers: [],
    api_key_configured: true, workspace: "D:/demo/daily", workspace_name: "daily",
    workspace_is_default: false, memory_count: 0, memory_user_count: 0,
    mcp_servers: [], sandbox: {}, vision: false, campus_map: null,
    library_health: {}, skills: [], kb_count: 0,
    session_metrics: {ok: true, model: "m", capabilities: [], selectable: [],
      known: true, context_window: 0, context_tokens: 0, consumed: 0}}),
  get_session_metrics: () => Promise.resolve({ok: true, model: "m", capabilities: [],
    selectable: [], known: true, context_window: 0, context_tokens: 0, consumed: 0}),
  get_activity: () => Promise.resolve({ok: true, now: null, timeline: {calls: 0},
    summary: {calls: 0}, changes: {ok: true, total: 0, items: [], note: ""},
    updated_at: "", empty: true, groups: []}),
  get_env_info: () => Promise.resolve({}),
  // v0.9.129 规划锁:GUI 没有斜杠命令,这颗按钮是图形版除核对单之外唯一的出口。
  plan_approve: () => { rec("plan_approve", null);
    return Promise.resolve({ok: true, locked: false}); },
  get_monitor_pref: () => Promise.resolve({open: false, folded: {}}),
  set_monitor_pref: () => Promise.resolve({ok: true}),
  get_reasoning: () => Promise.resolve({visible: false, disabled: true, kind: "hidden",
    choice: "default", hint: "", options: []}),
  get_suggestions: () => Promise.resolve({suggestions: []}),
  get_countdowns: () => Promise.resolve({ok: true, countdowns: []}),
  get_recent_projects: () => Promise.resolve({ok: true, current: {}, default: {},
    recent: []}),
  refresh_sessions: () => Promise.resolve({ok: true}),
  rate_message: () => Promise.resolve({ok: true}),
  send_task: () => Promise.resolve({ok: true, session: "s1"}),
  new_task: () => { rec("new_task", {}); return Promise.resolve({ok: true}); },
  get_providers: () => Promise.resolve({ok: true, providers: [], presets: [],
                                        active_provider: "", api_key_configured: true}),
  resume_session: (f) => { rec("resume_session", f); return Promise.resolve({ok: true}); },
  reveal_file: (p) => { rec("reveal_file", p); return Promise.resolve({ok: true}); },
  get_schedule_view: () => Promise.resolve({ok: true,
    schedule: {registered: false}}),
  get_score_stats: (sem) => { rec("get_score_stats", sem || "");
    return Promise.resolve({ok: true,
      stats: Object.assign({}, STATS, {semester: sem || ""})}); },
  win_state: () => { rec("win_state", {});
    return Promise.resolve({ok: true, borderless: window.__borderless,
      maximized: false, rect: [0, 0, 1280, 800], work_area: [0, 0, 1920, 1032],
      bounds_ok: true}); },
  win_minimize: () => { rec("win_minimize", {}); return Promise.resolve({ok: true}); },
  win_zoom: () => { rec("win_zoom", {});
    return Promise.resolve({ok: true, maximized: true}); },
  win_close: () => { rec("win_close", {});
    return Promise.resolve({ok: window.__closeOk,
                            msg: window.__closeOk ? "" : "当前窗口后端没有 destroy()"}); },
  win_resize: (edge, dx, dy) => { rec("win_resize", [edge, dx, dy]);
    return Promise.resolve({ok: true, rect: [0, 0, 1280, 800]}); },
  get_automation: () => { rec("get_automation", {}); return Promise.resolve(JOBS); },
  set_job_enabled: (id, on) => { rec("set_job_enabled", [id, on]);
    return Promise.resolve({ok: true, disabled: [], msg: "好了"}); },
  add_automation: (label, prompt, cadence, weekday, hour, minute) => {
    rec("add_automation", [label, prompt, cadence, weekday, hour, minute]);
    return Promise.resolve({ok: true, id: "u9", msg: "已加上"}); },
  remove_automation: (id) => { rec("remove_automation", id);
    return Promise.resolve({ok: true, msg: "已删掉"}); },
  get_skills: () => Promise.resolve(SKILLS),
  get_skill_channels: () => Promise.resolve({ok: true, channels: []}),
  get_skill_body: (n) => Promise.resolve({ok: true, body: "# 怎么用\n先搜再借。"}),
  export_skill_pack: (n) => { rec("export_skill_pack", n);
    return Promise.resolve({ok: true, path: "D:/demo/技能分享包-搜书指南.zip",
                            files: 2, bytes: 4096, msg: "已打成一份分享包"}); },
  import_skill_pack: () => { rec("import_skill_pack", {});
    return Promise.resolve({ok: true, nonce: "n1", name: "别人的技能", count: 2,
                            files: ["SKILL.md", "assets/map.md"], created: "10-01",
                            tools: [], libs: [], conflict: true,
                            msg: "本机已有同名技能,导入前会先备份。"}); },
  apply_skill_pack: (nonce) => { rec("apply_skill_pack", nonce);
    return Promise.resolve({ok: true, written: 2, replaced: 1, msg: "已导入"}); },
  plan_review_reply: (p) => { rec("plan_review_reply", p);
    return Promise.resolve({ok: true}); },
};
const inject = () => setTimeout(() => {
  window.pywebview = { api: API };
  window.dispatchEvent(new Event("pywebviewready"));
}, 30);
if (document.readyState === "complete") inject();
else window.addEventListener("load", inject);
""".replace("__INJ_STATS__", json.dumps(STATS, ensure_ascii=False)) \
   .replace("__INJ_JOBS__", json.dumps(JOBS, ensure_ascii=False)) \
   .replace("__INJ_SKILLS__", json.dumps(SKILLS, ensure_ascii=False))

PLAN_EVENT = {"type": "plan_review", "goal": "把成绩导入修好",
              "steps": [{"n": 1, "title": "读现有代码", "verify": ""},
                        {"n": 2, "title": "改解析", "verify": "pytest -q"}]}


def _calls(page, name):
    return [c for c in page.evaluate("() => window.__CALLS") if c[0] == name]


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
            page.wait_for_function("() => window.pywebview && pywebview.api.get_info")
            page.wait_for_timeout(600)

            # ---- 1. 三颗窗口按钮:点得到,失败要说话(图 p7 那颗死钮)----
            page.click("#winMin")
            page.click("#winMax")
            page.wait_for_timeout(150)
            check(_calls(page, "win_minimize") and _calls(page, "win_zoom"),
                  "− 与 □ 真的敲到桥")
            page.evaluate("() => { window.__closeOk = false; }")
            page.click("#winClose")
            page.wait_for_timeout(250)
            warn = page.evaluate("() => document.querySelector('.toast') ? "
                                 "document.querySelector('.toast').innerText : ''")
            check(_calls(page, "win_close") and "destroy" in warn,
                  f"✕ 点不动时界面上会说一句(不再\"没反应\"):「{warn[:40]}」")
            page.evaluate("() => { window.__closeOk = true; }")

            # ---- 2. 图标看得见 + 三颗垂直居中(图 p1)----
            geo = page.evaluate("""() => {
              const b = document.getElementById('btnSideToggle');
              const svg = b.querySelector('svg');
              const cs = getComputedStyle(svg);
              const r = svg.getBoundingClientRect();
              const wb = [...document.querySelectorAll('.wb')].map(x => {
                const br = x.getBoundingClientRect(), sr = x.querySelector('svg').getBoundingClientRect();
                return Math.abs((br.top + br.height / 2) - (sr.top + sr.height / 2));
              });
              const box = b.getBoundingClientRect();
              return {w: r.width, h: r.height, color: cs.color, btn: box.width,
                      off: Math.max(...wb), bg: getComputedStyle(b).backgroundColor};
            }""")
            check(geo["w"] >= 14 and geo["h"] >= 14,
                  f"收起栏图标有 {geo['w']:.0f}×{geo['h']:.0f}px(不是化掉的一根线)")
            check(geo["color"].startswith("rgb") and "rgba(0, 0, 0, 0)" not in geo["color"],
                  f"描边吃的是真文字色:{geo['color']}")
            check(geo["bg"] != "rgba(0, 0, 0, 0)", "那颗有底,顶栏上一眼看得出是个按钮")
            check(geo["off"] < 1.5, f"三颗窗口按钮的字形垂直居中(最大偏移 {geo['off']:.2f}px)")

            # ---- 3. 入口栏不出滚动条,矮窗口仍够得着(图 p3)----
            rail = page.evaluate("""() => {
              const r = document.querySelector('.rail');
              return {over: r.scrollWidth - r.clientWidth,
                      vover: r.scrollHeight - r.clientHeight,
                      css: getComputedStyle(r).overflowY,
                      n: r.querySelectorAll('button').length};
            }""")
            check(rail["vover"] <= 0 and rail["over"] <= 0,
                  f"正常高度下没有溢出({rail['n']} 颗,溢出 {rail['vover']}px)")
            page.set_viewport_size({"width": 1280, "height": 480})
            page.wait_for_timeout(250)
            short = page.evaluate("""() => {
              const r = document.querySelector('.rail');
              r.scrollTop = 9999;
              const last = r.querySelector('#btnTheme').getBoundingClientRect();
              return {scrolled: r.scrollTop > 0, css: getComputedStyle(r).overflowY,
                      visible: last.bottom <= window.innerHeight + 1};
            }""")
            check(short["css"] == "auto" and short["scrolled"] and short["visible"],
                  "矮窗口里仍能滚到最后一颗(入口看不见 = 功能没了,这条不能松)")
            page.set_viewport_size({"width": 1280, "height": 900})
            page.wait_for_timeout(250)

            # ---- 4. 选学期:自绘浮层,深色下跟着换色(图 p2)----
            page.click("#btnSchedule")
            page.wait_for_selector("#scheduleOverlay.show")
            page.click('#scTabs button[data-tab="scores"]')
            page.wait_for_timeout(500)
            page.click("#scSemBtn")
            page.wait_for_selector("#scSemMenu:not([hidden])")
            opts = page.eval_on_selector_all("#scSemMenu .at-item b",
                                             "els => els.map(e => e.textContent)")
            check(opts == ["全部学期(2 个)", "2023-2024-2", "2024-2025-1"],
                  f"学期浮层:全部 + 存档里真出现过的两个学期 {opts}")
            light = page.evaluate("() => getComputedStyle(document.getElementById('scSemMenu')).backgroundColor")
            page.evaluate("() => document.documentElement.classList.add('dark')")
            page.wait_for_timeout(150)
            dark = page.evaluate("() => getComputedStyle(document.getElementById('scSemMenu')).backgroundColor")
            check(light != dark and "rgba(0, 0, 0, 0)" not in dark,
                  f"浮层跟着主题换色(浅 {light} → 深 {dark}),不再是系统白底")
            page.evaluate("() => document.documentElement.classList.remove('dark')")
            page.click('#scSemMenu .at-item:nth-child(2)')
            page.wait_for_timeout(400)
            last = _calls(page, "get_score_stats")[-1]
            check(last[1] == "2023-2024-2", f"点选项真的按那个学期重算:{last}")
            check(page.evaluate("() => document.getElementById('scSemMenu').hidden"),
                  "选完浮层自己收起来")
            page.click("#scSemBtn")
            page.wait_for_timeout(120)
            page.keyboard.press("Escape")
            check(page.evaluate("() => document.getElementById('scSemMenu').hidden"),
                  "Esc 关得掉浮层")
            page.evaluate("() => document.getElementById('scheduleOverlay')"
                          ".classList.remove('show')")

            # ---- 5. 自动化「新建」(图 p5/p6)----
            page.click("#btnAutomate")
            page.wait_for_selector("#autoOverlay.show")
            page.wait_for_timeout(400)
            rows = page.evaluate("""() => ({
              mine: document.querySelectorAll('#autoBody [data-del]').length,
              tags: [...document.querySelectorAll('#autoBody .jb-tag')]
                .map(e => e.textContent),
              sw: document.querySelectorAll('#autoBody [data-sw]').length})""")
            check(rows["mine"] == 1 and rows["tags"] == ["我加的"] and rows["sw"] == 2,
                  "自己加的那条带「我加的」标与「删」那颗;内置的不给删(给了就是假按钮)")
            page.click("#autoNew")
            page.wait_for_selector("#jbForm:not([hidden])")
            page.fill("#jbName", "每晚看待办")
            page.fill("#jbPrompt", "把今天要交的东西列三行")
            page.click('#jbCad [data-c="weekly"]')
            days = page.evaluate("""() => ({
              shown: !document.getElementById('jbDays').hidden,
              labels: [...document.querySelectorAll('#jbDayChips .jb-c')]
                .map(e => e.textContent),
              everyHidden: !document.getElementById('jbEveryRow').hidden})""")
            check(days["shown"] and days["labels"] == ["一", "二", "三", "四", "五", "六", "日"]
                  and not days["everyHidden"],
                  f"选「每周」才出星期 chips:{days['labels']}")
            page.click('#jbDayChips .jb-c:nth-child(3)')
            page.click("#jbSave")
            page.wait_for_timeout(300)
            sent = _calls(page, "add_automation")[-1]
            check(sent[1][0] == "每晚看待办" and sent[1][2] == "weekly"
                  and sent[1][3] == 2 and sent[1][4] == 20,
                  f"发出去的是 [{sent[1][0]}, …, weekly, 周三, 20 点]:{sent[1]}")
            page.evaluate("() => { window.__CALLS.length = 0; }")
            page.click("#autoNew")                     # 存成功后表单自己收起来了,再摊开
            page.wait_for_selector("#jbForm:not([hidden])")
            page.fill("#jbName", "")
            page.fill("#jbPrompt", "还是那句")
            page.click("#jbSave")
            page.wait_for_timeout(250)
            check(not _calls(page, "add_automation"),
                  "名字空着不发 —— 列表里不留一条点不动的任务")
            page.evaluate("() => document.getElementById('autoOverlay')"
                          ".classList.remove('show')")

            # ---- 6. 新功能①:计划核对单 ----
            page.evaluate("(e) => window.__gcodeEvent(e)", PLAN_EVENT)
            page.wait_for_selector("#planReviewOverlay.show")
            card = page.evaluate("""() => ({
              goal: document.getElementById('prGoal').innerText,
              steps: [...document.querySelectorAll('#prList .pr-t')]
                .map(e => e.innerText.replace(/\\s+/g, ' ')),
              nums: [...document.querySelectorAll('#prList .pr-n')]
                .map(e => e.textContent),
              edits: document.querySelectorAll('#prList .pr-edit').length})""")
            check(card["goal"] == "把成绩导入修好" and card["nums"] == ["1", "2"],
                  f"整张单子摊开:{card['goal']} / {len(card['steps'])} 步")
            check("pytest -q" in card["steps"][1],
                  "带验收判据的那一步把判据摆在单子上(批之前看得见要验什么)")
            check(card["edits"] == 2, "每一步都有自己的「改这条」")
            page.click("#prApprove")
            page.wait_for_timeout(250)
            appr = json.loads(_calls(page, "plan_review_reply")[-1][1])
            check(appr["mode"] == "approve"
                  and page.evaluate("() => !document.getElementById('planReviewOverlay')"
                                    ".classList.contains('show')"),
                  "批准 → 发 approve 并收卡")
            page.evaluate("(e) => window.__gcodeEvent(e)", PLAN_EVENT)
            page.wait_for_selector("#planReviewOverlay.show")
            page.click('#prList .pr-edit')
            page.wait_for_selector("#prList .pr-input")
            page.fill("#prList .pr-input", "先写一条会红的测试")
            page.click("#prList .pr-save")
            page.wait_for_timeout(250)
            edit = json.loads(_calls(page, "plan_review_reply")[-1][1])
            check(edit["mode"] == "edit" and edit["step"] == 1
                  and edit["text"] == "先写一条会红的测试",
                  f"改一条 → 发第 {edit.get('step')} 步的新内容:{edit}")
            page.evaluate("(e) => window.__gcodeEvent(e)", PLAN_EVENT)
            page.wait_for_selector("#planReviewOverlay.show")
            page.evaluate("() => { window.__CALLS.length = 0; }")
            page.click("#prRevise")
            page.wait_for_timeout(200)
            check(not _calls(page, "plan_review_reply"),
                  "点「重排」却没写为什么 → 不发(否则学长只能猜着重排)")
            page.fill("#prNote", "顺序不对,先补测试再改码")
            page.click("#prRevise")
            page.wait_for_timeout(250)
            rev = json.loads(_calls(page, "plan_review_reply")[-1][1])
            check(rev["mode"] == "revise" and "先补测试" in rev["note"], f"重排带上原话:{rev}")
            page.evaluate("(e) => window.__gcodeEvent(e)", PLAN_EVENT)
            page.wait_for_selector("#planReviewOverlay.show")
            page.click("#prClose")
            page.wait_for_timeout(250)
            later = json.loads(_calls(page, "plan_review_reply")[-1][1])
            check(later["mode"] == "unanswered",
                  "「稍后再说」回的是 unanswered —— 关卡必须同时松掉后端那条等待")

            # ---- 6b. 规划锁:聊天里那张计划卡片上的「批准这版计划」(v0.9.129) ----
            # 核对单会被关掉、也可能压根没弹(重排之后),所以卡片上这一颗不是装饰:
            # 少了它,图形版就有一条"锁上了却没人在界面上解得开"的死路。
            page.evaluate("""() => renderPlanCard({ok: true, has_plan: true,
                goal: '把成绩导入修好', total: 2, done: 0, locked: true,
                steps: [{n: 1, title: '复现', status: 'pending', ready: true,
                         blocked: false, depends_on: [], note: '', verify: ''},
                        {n: 2, title: '改解析', status: 'pending', ready: false,
                         blocked: true, depends_on: [1], note: '', verify: ''}],
                ready: [1], blocked: [2], failed: [], running: []})""")
            page.wait_for_timeout(200)
            btn = page.locator(".tk.locked .tk-approve")
            check(btn.count() == 1, "锁着的计划卡片上有一颗「批准这版计划」")
            check(btn.first.inner_text() == "批准这版计划", f"按钮文案:{btn.first.inner_text()}")
            btn.first.click()
            page.wait_for_timeout(300)
            check(_calls(page, "plan_approve"),
                  "点它真的敲了桥(不是只把按钮自己藏起来)")
            # 解锁之后重画:按钮必须消失 —— 留着就是一只点了没用的死按钮
            page.evaluate("""() => renderPlanCard({ok: true, has_plan: true,
                goal: '把成绩导入修好', total: 2, done: 0, locked: false,
                steps: [{n: 1, title: '复现', status: 'pending', ready: true,
                         blocked: false, depends_on: [], note: '', verify: ''},
                        {n: 2, title: '改解析', status: 'pending', ready: false,
                         blocked: true, depends_on: [1], note: '', verify: ''}],
                ready: [1], blocked: [2], failed: [], running: []})""")
            page.wait_for_timeout(200)
            check(page.locator(".tk-approve").count() == 0,
                  "没锁的卡片上不该还留着这颗按钮")

            # ---- 7. 新功能③:技能分享包两颗 ----
            page.click("#btnSkills")
            page.wait_for_selector("#skillsOverlay.show")
            page.wait_for_timeout(400)
            check(page.evaluate("() => !!document.getElementById('btnSkillPackImport')"),
                  "技能面板底栏有「导入分享包」")
            page.click(".skillcard")
            page.wait_for_selector("#btnSkillPack")
            page.click("#btnSkillPack")
            page.wait_for_timeout(300)
            check(_calls(page, "export_skill_pack"),
                  "详情里那颗「打包分享」真的发出去了(带着技能名)")
            check("分享包" in page.evaluate("() => document.getElementById('skillsBody').innerText"),
                  "打好的包写在屏上,并给一颗「在文件夹里显示」")
            page.evaluate("() => document.getElementById('skillsOverlay')"
                          ".classList.remove('show')")

            # ---- 8. 托盘推的 open_panel 有落点(图 p8/p9)----
            page.evaluate("() => window.__gcodeEvent({type:'open_panel', panel:'new_task'})")
            page.wait_for_timeout(300)
            check(_calls(page, "new_task"),
                  "托盘「新建任务」→ open_panel(new_task)→ 真的发了 new_task")
            page.evaluate("() => window.__gcodeEvent({type:'open_panel', panel:'providers'})")
            page.wait_for_selector("#provOverlay.show")
            check(True, "托盘「设置」→ open_panel(providers)→ 模型与密钥面板开出来了")
            page.keyboard.press("Escape")
            page.evaluate("() => document.getElementById('provOverlay')"
                          ".classList.remove('show')")
            page.evaluate("() => window.__gcodeEvent("
                          "{type:'open_panel', panel:'session:2026-10-04-甲.md'})")
            page.wait_for_timeout(300)
            got = _calls(page, "resume_session")
            check(got and got[-1][1] == "2026-10-04-甲.md",
                  f"托盘「最近会话」那一条 → 带着留档名回到底层那条既有路:{got}")

            errs = page.evaluate("() => window.__errs")
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
