# -*- coding: utf-8 -*-
"""v0.9.112 前端回归:用户这轮 10 张截图里属于界面那半边。

跑法(项目根目录):
    PYTHONIOENCODING=utf-8 .venv\\Scripts\\python.exe scripts\\e2e_ui_v09112.py

覆盖:
    新 地图筛选之后舞台不许被压成一条(p8)—— 含一条**反证**:把 min-height 改回
      原样(0)再量一次,量不到就证明这条断言不是摆设
    新 收起/展开左侧栏 + Ctrl+B + 重开窗口记住(p5/p6)
    新 「终端」面板:后台命令的状态、耗时、输出末尾(p5 点名要的功能)
    新 「自动化」面板:定时任务的节奏/下一班/上次结论 + 开关真敲桥
    新 「子智能体」单独成节(可折叠、带耗时),不再埋在产出与来源第四屏(p9/p10)
    新 类别段可折叠 + 「再显示 N 条」是真能点的(点完 get_activity 带 full)
    新 轮次那一叠小卡(p2:一长条改成多个小分区)
    新 环境信息里的「标题栏」与「git」两行(p3/p4 与 p7 的降级要看得见)
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

# 真地图数据(36 个条目、含「教学楼」8 处 —— 用户那张截图就是按了这颗 chip)
from minicode.campus import frontend_payload    # noqa: E402
MAP = frontend_payload()

ACTIVITY = {
    "ok": True, "now": None, "timeline": {"calls": 3},
    "summary": {"calls": 3}, "changes": {"ok": True, "total": 0, "items": [],
                                         "note": ""},
    "updated_at": "20:21:04", "empty": False,
    "groups": [
        {"key": "subagents", "label": "子代理", "icon": "🤖", "count": 3, "more": 0,
         "items": [
             {"key": "sa:c1:0", "title": "查一下这个仓库的登录链路", "type": "Explore",
              "sub": "Explore · 合并报告 1820 字 · 用时 41s", "status": "ok",
              "badge": "已返回报告",
              "action": {"type": "detail", "label": "看报告", "text": "报告正文"}},
             {"key": "sa:c1:1", "title": "盘点权限门", "type": "general-purpose",
              "sub": "general-purpose · 合并报告 1820 字 · 用时 41s", "status": "ok",
              "badge": "已返回报告", "action": None},
             {"key": "sa:c2:0", "title": "还没回来的那路", "type": "audit",
              "sub": "audit", "status": "running", "badge": "运行中", "action": None},
         ]},
        {"key": "files", "label": "文件", "icon": "📄", "count": 14, "more": 2,
         "items": [{"key": f"f:{i}", "title": f"notes/{i}.md", "sub": "写入",
                    "status": "ok", "badge": "完成", "action": None}
                   for i in range(12)]},
    ],
}

ENV = {"app": "引路学长", "version": "0.9.112", "workspace": "D:/demo/daily",
       "is_repo": True, "branch": "main", "repo_root": "D:/demo/daily", "dirty": True,
       "files": [], "total": 3, "more": 0, "added": 60, "removed": 4,
       "partial": False, "app_dir": "D:/demo/data", "backend": "local",
       "writable": True, "frozen": True, "jobs": [], "msg": "",
       "git_note": "git.exe 启动失败(0xC0000142)",
       "chrome": {"ok": False, "applied": ["immersive-dark-mode"],
                  "failed": ["caption", "text"], "msg": "标题栏这几项系统不接受"}}

TERMINAL = {"ok": True, "buffer_chars": 20000,
            "note": "这是**这一程**的临时账:关掉程序就没了。",
            "jobs": [
                {"id": "j1", "command": "pytest -q tests", "running": True,
                 "exit_code": None, "killed": False, "chars": 4200, "dropped": 0,
                 "elapsed": 63, "tail": ".... [ 42%]\n一条在跑的输出"},
                {"id": "j2", "command": "python scripts/build_exe.py", "running": False,
                 "exit_code": 1, "killed": False, "chars": 800, "dropped": 120,
                 "elapsed": 12, "tail": "报错在最后一行"},
            ]}

AUTOMATION = {"ok": True, "handlers": ["kb_gap_digest", "mood_weekly_review"],
              "jobs": [
                  {"id": "mood_weekly_review", "label": "周复盘", "cadence": "weekly",
                   "rhythm": "每周周日 20 点后", "hour": 20, "minute": 0, "weekday": 6,
                   "every": 1, "enabled": True, "due_now": False,
                   "next": "10-04 20:00", "next_hint": "",
                   "last": "2026-09-27 20:05", "runs": 1, "result": "已推"},
                  {"id": "kb_gap_digest", "label": "知识库缺口周报", "cadence": "weekly",
                   "rhythm": "每周周五 18 点后", "hour": 18, "minute": 0, "weekday": 4,
                   "every": 1, "enabled": False, "due_now": False,
                   "next": "10-09 18:00", "next_hint": "",
                   "last": "", "runs": 0, "result": ""},
              ]}

STUB = ("""
window.__CALLS__ = [];
const CALLS = window.__CALLS__;
const MAP = __INJ_MAP__;
const ACTIVITY = __INJ_ACT__;
const ENV = __INJ_ENV__;
const TERMINAL = __INJ_TERM__;
const AUTOMATION = __INJ_AUTO__;
const API = {
  get_info: () => { CALLS.push(["get_info"]); return Promise.resolve({
    version: "0.9.112", app_name: "引路学长", permission_mode: "confirm",
    permission_modes: [{value: "confirm", label: "变更前确认"}],
    session_key: "s1", model: "deepseek-v4-flash", active_provider: "builtin",
    providers: [], api_key_configured: true, workspace: "D:/demo/daily",
    workspace_name: "daily", workspace_is_default: false,
    memory_count: 0, memory_user_count: 0, mcp_servers: [],
    sandbox: {}, vision: false, campus_map: MAP, library_health: {},
    skills: [], kb_count: 0, session_metrics: {ok: true, model: "deepseek-v4-flash",
      capabilities: [], selectable: [], known: true, context_window: 0,
      context_tokens: 0, consumed: 0}
  }); },
  get_session_metrics: () => Promise.resolve({ok: true, model: "deepseek-v4-flash",
    capabilities: [], selectable: [], known: true, context_window: 0,
    context_tokens: 0, consumed: 0}),
  get_activity: (full) => { CALLS.push(["get_activity", !!full]);
    return Promise.resolve(ACTIVITY); },
  get_env_info: () => Promise.resolve(ENV),
  get_terminal: () => { CALLS.push(["get_terminal"]); return Promise.resolve(TERMINAL); },
  get_automation: () => { CALLS.push(["get_automation"]);
    return Promise.resolve(AUTOMATION); },
  set_job_enabled: (id, on) => { CALLS.push(["set_job_enabled", id, on]);
    return Promise.resolve({ok: true, disabled: on ? [] : [id], msg: "好了"}); },
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
  compact_now: () => Promise.resolve({ok: false, msg: "这一支不测压缩"}),
  reveal_file: () => Promise.resolve({ok: false, msg: "不测"}),
};
const inject = () => setTimeout(() => {
  window.pywebview = { api: API };
  window.dispatchEvent(new Event("pywebviewready"));
}, 30);
if (document.readyState === "complete") inject();
else window.addEventListener("load", inject);
""").replace("__INJ_MAP__", json.dumps(MAP, ensure_ascii=False)) \
   .replace("__INJ_ACT__", json.dumps(ACTIVITY, ensure_ascii=False)) \
   .replace("__INJ_ENV__", json.dumps(ENV, ensure_ascii=False)) \
   .replace("__INJ_TERM__", json.dumps(TERMINAL, ensure_ascii=False)) \
   .replace("__INJ_AUTO__", json.dumps(AUTOMATION, ensure_ascii=False))


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
            page.wait_for_function("() => document.getElementById('btnTerminal') !== null"
                                   " && window.pywebview && pywebview.api.get_info")
            page.wait_for_timeout(400)

            # ---- 1. 地图:按类别筛选之后,舞台不许被压成一条(p8) ----
            page.click("#btnMap")
            page.wait_for_selector("#mapPanel.open")
            page.wait_for_timeout(500)
            base = page.evaluate("() => document.getElementById('mapStage')"
                                 ".getBoundingClientRect().height")
            chip = page.locator(".map-legend .lg-item", has_text="教学楼")
            if not chip.count():
                chip = page.locator(".map-legend button", has_text="教学楼")
            check(chip.count() > 0, "图例里有「教学楼」那颗 chip(真地图数据,36 条目)")
            chip.first.click()
            page.wait_for_timeout(600)          # 卡片与列表出现,挤压就发生在这一步
            got = page.evaluate("""() => {
              const st = document.getElementById('mapStage');
              const r = st.getBoundingClientRect();
              const card = document.getElementById('mapCard');
              const list = document.getElementById('mapList');
              const panel = document.getElementById('mapPanel');
              return {h: r.height, w: r.width,
                      card: card.getBoundingClientRect().height,
                      list: list.getBoundingClientRect().height,
                      over: panel.scrollHeight - panel.clientHeight};
            }""")
            check(base >= got["h"],
                  f"筛选前 {base:.0f}px → 筛选后 {got['h']:.0f}px:让出去的那一点"
                  "是卡片与列表自己的高度,不是从地图上抢的")
            check(got["h"] >= 240,
                  f"筛选后地图舞台还有 {got['h']:.0f}px(卡片 {got['card']:.0f} + "
                  f"列表 {got['list']:.0f} 都在自己肚子里滚)")
            check(got["over"] <= 4,
                  f"面板没有溢出滚出屏幕(超出 {got['over']:.0f}px)")
            # 反证:把这条 min-height 改回原样(0),同一份数据下舞台必须塌下去 ——
            # 量不到塌,上面那条断言就是摆设。
            revert = page.evaluate("""() => {
              const st = document.getElementById('mapStage');
              const old = st.style.minHeight;
              st.style.minHeight = '0px';
              st.style.flex = '1';
              const h = st.getBoundingClientRect().height;
              st.style.minHeight = old; st.style.flex = '';
              return h;
            }""")
            check(revert < got["h"] - 40,
                  f"反证:去掉 min-height 后同一屏塌到 {revert:.0f}px"
                  f"(修好是 {got['h']:.0f}px)—— 这条断言不是摆设")
            page.click("#mapClose") if page.locator("#mapClose").count() else None
            page.evaluate("() => document.getElementById('mapPanel').classList.remove('open')")

            # ---- 2. 收起侧栏 + Ctrl+B + 记住(p5/p6) ----
            w_before = page.evaluate("() => getComputedStyle(document.querySelector('.app'))"
                                     ".gridTemplateColumns")
            page.click("#btnSideToggle")
            page.wait_for_timeout(200)
            w_after = page.evaluate("() => getComputedStyle(document.querySelector('.app'))"
                                    ".gridTemplateColumns")
            check(w_before != w_after and w_after.split(" ")[1].startswith("0"),
                  f"点一下中间那一列真的收掉了({w_before} → {w_after})")
            # v0.9.113 起收起栏是纯图标(title/aria 承担解释),sideCap 那行字退役 ——
            # 状态翻没翻,看 title 与 aria-expanded。
            check(page.evaluate("() => document.getElementById('btnSideToggle').title")
                  == "展开左侧栏(Ctrl+B)", "按钮的提示跟着翻(收起来之后不能再写着「收起」)")
            check(page.evaluate("() => document.getElementById('btnSideToggle')"
                                ".getAttribute('aria-expanded')") == "false",
                  "aria-expanded 说的是真状态(收起 = false)")
            page.keyboard.press("Control+b")
            page.wait_for_timeout(150)
            check(page.evaluate("() => !document.querySelector('.app')"
                                ".classList.contains('side-closed')"),
                  "Ctrl+B 再按一次展开回去")
            page.keyboard.press("Control+b")
            page.wait_for_timeout(150)
            check(page.evaluate("() => localStorage.getItem('gcode-side')") == "closed",
                  "收起这件事记在本机偏好里")
            page2 = ctx.new_page()               # 真·重开窗口:同一份偏好,新文档
            page2.goto(url, wait_until="load")
            page2.wait_for_function("() => document.querySelector('.app')"
                                    ".classList.contains('side-closed')")
            check(page2.evaluate("() => document.getElementById('btnSideToggle').title")
                  == "展开左侧栏(Ctrl+B)", "重开窗口还是收着的,提示也对")
            page2.close()
            page.evaluate("() => localStorage.removeItem('gcode-side')")
            page.click("#btnSideToggle")         # 展开回来,后面的用例要看得到聊天区
            page.wait_for_timeout(200)

            # ---- 3. 终端(v0.9.162 起住进底部终端坞;旧只读弹窗已拆除) ----
            page.click("#btnTerminal")
            page.wait_for_function("() => document.querySelectorAll('.term-dock .tp-row')"
                                   ".length === 2")
            rows = page.eval_on_selector_all(
                ".term-dock .tp-row .tp-cmd",
                "els => els.map(e => e.textContent)")
            check(rows == ["pytest -q tests", "python scripts/build_exe.py"],
                  f"两条后台命令都在,在跑的排前面({rows})")
            page.locator(".term-dock [data-tail]").first.click()
            page.wait_for_selector(".term-dock .tp-out")
            check("42%" in page.locator(".term-dock .tp-out")
                  .first.inner_text(),
                  "点「看输出」摊出的是那条命令的输出末尾")
            # v0.9.159 起这一页**有**输入行:每条命令过沙箱硬闸,confirm 档每条先问 ——
            # 旧断言"面板里没有输入框"随只读弹窗一起退役
            check(page.locator(".term-dock #tpCmd").count() == 1,
                  "终端坞有输入行(terminal_run,沙箱照拦)")
            check(page.evaluate("() => document.getElementById('termOverlay')") is None,
                  "旧「后台命令」只读弹窗已拆除(v0.9.161)")

            # ---- 4. 自动化面板 ----
            page.click("#btnAutomate")
            page.wait_for_function("() => document.querySelectorAll('#autoBody .tp-row')"
                                   ".length === 2")
            heads = page.eval_on_selector_all("#autoBody .tp-cmd",
                                              "els => els.map(e => e.textContent)")
            check(heads == ["周复盘", "知识库缺口周报"],
                  f"列的是登记着的任务的人话名字({heads})")
            body = page.locator("#autoBody").inner_text()
            check("每周周日 20 点后" in body and "下一班 ~10-04 20:00" in body,
                  "节奏与下一班都在(数值由 jobs.next_due 算,不是前端编的)")
            check("上次 2026-09-27 20:05 · 共 1 次" in body and "已推" in body,
                  "上次什么时候跑、结论是什么,读的是 jobs-state.json 那本账")
            check("从未跑过" in body, "从没跑过的任务也列得出来(以前看板整条不列)")
            page.locator("#autoBody [data-sw]").last.click()
            page.wait_for_function("() => window.__CALLS__.some(c => c[0] === 'set_job_enabled')")
            call = [c for c in page.evaluate("() => window.__CALLS__")
                    if c[0] == "set_job_enabled"][-1]
            check(call[1] == "kb_gap_digest" and call[2] is True,
                  f"开关敲的是这一条任务、开回来(参数 {call[1:]})")
            page.click("#autoClose")
            page.wait_for_function("() => !document.getElementById('autoOverlay')"
                                   ".classList.contains('show')")

            # ---- 5. 子智能体单独成节(p9/p10) ----
            # v0.9.159 起终端按钮开的是侧边栏面板 —— 走到这里面板可能已经开着,
            # 监控键是开关不是"确保开":先判再开,别把上一段留下的开态点关了。
            if not page.evaluate("() => document.querySelector('.app')"
                                 ".classList.contains('act-open')"):
                page.click("#btnActivity")
            # v0.9.159 起面板里有多页:上一段可能停在终端页 —— 活动页不切过来,
            # #actBody 是隐藏的,下面的点击会因为"不可见"等到天荒地老。
            # (SidePages 是 const 不挂 window,走 openPanel 这条公开的路。)
            page.evaluate('() => openPanel("side:activity")')
            page.wait_for_function("() => document.querySelectorAll('#actBody .act-sect')"
                                   ".length >= 4")
            heads = page.eval_on_selector_all("#actBody .act-cap b",
                                              "els => els.map(e => e.textContent)")
            check(any("子智能体" in h for h in heads),
                  f"监控栏里有「子智能体」这一节({[h for h in heads if '🤖' in h or '子' in h]})")
            sa = page.locator("#actBody .act-sect", has_text="子智能体")
            check(sa.locator(".act-row").count() == 3,
                  f"三个子代理三行(并行一次调用拆出来的也算各行){sa.locator('.act-row').count()}")
            sat = sa.inner_text()
            check("用时 41s" in sat, "耗时看得见(读的是消息 ts 的间隔,不是估的)")
            check("合并报告" in sat, "并行派发那份报告是合在一起的,界面上不假装各算各的字数")
            check("运行中" not in sat or "还没回来的那路" in sat,
                  "没回来的那一路标在跑,不写成完成")
            groups = page.locator("#actBody .act-sect", has_text="产出与来源").first
            check("查一下这个仓库的登录链路" not in groups.inner_text(),
                  "子代理不再重复出现在「产出与来源」里(一节一件事)")
            # 类别段折叠 + 「再显示 N 条」
            cap = page.locator("#actBody .act-cap.fold", has_text="文件").first
            cap.click()
            page.wait_for_timeout(150)
            sec = page.locator("#actBody .act-sec", has_text="文件").first
            check(sec.evaluate("el => getComputedStyle("
                               "el.querySelector('.act-secbody')).display") == "none",
                  "类别那一层现在点得动(以前只有段级能折,类别头是死的)")
            cap.click()
            page.wait_for_timeout(150)
            more = page.locator("#actBody .act-more button", has_text="再显示")
            check(more.count() > 0, "「另有 N 条」现在是一颗按得动的按钮")
            before = page.evaluate("() => window.__CALLS__.filter(c => c[0] === 'get_activity')"
                                   ".length")
            more.first.click()
            page.wait_for_function(
                "() => window.__CALLS__.some(c => c[0] === 'get_activity' && c[1] === true)")
            after = page.evaluate("() => window.__CALLS__.filter(c => c[0] === 'get_activity')"
                                  ".length")
            check(after > before, "点它真的去问了一遍(带 full),不是把同一份 12 行再画一次")

            # ---- 6. 环境信息里那两行新的降级说明 ----
            env = page.locator("#actBody .act-sect", has_text="环境信息").first
            envt = env.inner_text()
            check("git.exe 启动失败" in envt,
                  "git 起不来时这一屏写的是「问不动」,不是「没有改动」")
            check("系统不收" in envt and "caption" in envt,
                  "标题栏那三条 DWM 属性本机收没收,写在屏上 —— 报障时不必再猜")

            # ---- 7. 轮次那一叠小卡(p2) ----
            page.evaluate("""() => {
              const chat = document.getElementById('chat');
              for (let i = 1; i <= 12; i++) {
                const d = document.createElement('div');
                d.className = 'msg user';
                d.dataset.uord = String(i);
                d.innerHTML = `<div class="bubble">第 ${i} 个问题:帮我看这一段`
                  + "很长".repeat(12) + `</div>`;
                chat.appendChild(d);
              }
              window.__gcodeEvent && window.__gcodeEvent({type: 'cleared', session: 's1'});
            }""")
            page.evaluate("() => document.getElementById('chat').scrollTop = 0")
            # 上面那次 cleared 会把首屏画回来;直接再塞一次消息节点并重算刻度
            page.evaluate("""() => {
              const chat = document.getElementById('chat');
              for (let i = 1; i <= 12; i++) {
                const d = document.createElement('div');
                d.className = 'msg user';
                d.dataset.uord = String(i);
                d.innerHTML = `<div class="bubble">第 ${i} 个问题:帮我看这一段`
                  + "很长".repeat(12) + `</div>`;
                chat.appendChild(d);
              }
              rebuildChatRail();
            }""")
            page.wait_for_selector("#chatRail:not([hidden])")
            page.hover("#chatRail")
            page.click("#chatRail .tick-head")
            page.wait_for_selector("#railPop.turns:not([hidden])")
            cards = page.eval_on_selector_all("#railPop .turn-card .tc-n",
                                              "els => els.map(e => e.textContent)")
            check(cards[:3] == ["1", "2", "3"] and len(cards) >= 12,
                  f"一叠小卡:每问一张,共 {len(cards)} 张")
            heads = page.eval_on_selector_all("#railPop .tg-head",
                                              "els => els.map(e => e.textContent)")
            check(len(heads) >= 2 and "第 1–10 问" in heads[0],
                  f"十问一组带小头,不再是看不完的一长条({heads})")
            page.evaluate("""() => {
              window.__before = document.querySelectorAll('.msg.user .bubble')[11]
                .getBoundingClientRect().top;
              document.querySelectorAll('#railPop .turn-card')[11].click();
            }""")
            page.wait_for_timeout(700)           # 平滑滚动是异步的,立刻量会量到旧位置
            jumped = page.evaluate("""() => ({
              before: window.__before,
              after: document.querySelectorAll('.msg.user .bubble')[11]
                     .getBoundingClientRect().top,
              flash: document.querySelectorAll('.msg.user.flash').length,
              vh: window.innerHeight})""")
            check(jumped["flash"] >= 1 and abs(jumped["after"]) <= jumped["vh"],
                  f"点卡片真的把那一问带到眼前(rect.top {jumped['before']:.0f} → "
                  f"{jumped['after']:.0f})并闪一下(flash {jumped['flash']})")
            check(page.evaluate("() => document.getElementById('railPop').hidden"),
                  "跳完这一叠自己收起来(不挡着看正文)")

            browser.close()
    finally:
        shutdown()

    print("\n" + ("PASS: 地图高度 / 收起侧栏 / 终端 / 自动化 / 子智能体 / 分区折叠 / 轮次小卡"
                  if not failures else
                  "FAIL: %d 处不对\n  - %s" % (len(failures), "\n  - ".join(failures))))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
