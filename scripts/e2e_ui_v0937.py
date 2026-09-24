# -*- coding: utf-8 -*-
"""v0.9.37 前端回归:右侧「任务监控」栏 —— 四段都得是真数据、真点得动、真记得住。

用户看图 p1 点名要"可以隐藏也可以展开的任务监管",规矩还是他那一条:
「是要真正能使用的不能只用于装饰」。所以这一场**不买手写好的假字典**:
喂给页面的 `get_activity()` 是**真跑出来的** —— 真 `Session` 写消息、真 `GuiUI`
记实时表(含一次真按 Stop 走的 `live_abandon()`)、真 `plans/plan.md` 经
`render_plan` 落盘再由 `plan_state` 读回来。页面里那一栏只是把这些数字摆出来。

要钉住的七件事:
1. **默认收起 + 记住选择**:开机不占地方;点开后写盘,重载页面(偏好用 localStorage
   当真磁盘)仍然是开着的 —— 证明往返的是文件,不是内存里的一个变量。
2. **「此刻」的秒数在走**:后端给的起始秒数之后,页面自己每隔一秒 +1,
   而不是拿刷新那一刻凑一个假时长。
3. **时间线按轮次折叠、轮内按先后**:标题那句「执行工具 N 次,其中 M 次失败」的
   N/M 与后端逐字相同;上一轮默认折起但**点得开**。
4. **配不到时刻的行不显示时间**:并行子代理被 `_QuietUI` 吞掉的那一步,
   界面上一个 HH:MM:SS 都没有(宁可少一列)。
5. **按了停止之后不转圈**:同一份消息 busy=False 重算 → 「已中断」出现在
   汇总、段落标题与那一行上,`.act-now` 整段消失。
6. **点一行真做事**:文件行真的调 `reveal_file`(路径逐字对上)、计划行真的打开
   计划弹窗、有回话的行真的展开原文。
7. **没做过的段不出现**:空会话只有「此刻」那句实话 + 一句"还没有动作",
   不摆一排"暂无"。

跑法(项目根目录,需要 PYTHONIOENCODING=utf-8 否则中文断言在 GBK 控制台炸):
    PYTHONIOENCODING=utf-8 .venv\\Scripts\\python.exe scripts\\e2e_ui_v0937.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PREF_KEY = "__e2e_monitor_pref__"


# --------------------------------------------------------------- 真后端产物

def _tool(name):
    from minicode.tools.base import Tool
    return Tool(name=name, description="", parameters={}, handler=lambda ctx, **k: "ok")


def _turn(user_text, calls):
    return {"text": user_text, "calls": calls}


# 一项 = (调用 id, 工具名, 参数, 回话或 None, 有没有被实时表记到)
TURNS = [
    _turn("帮我把这次期中成绩统计成报告", [
        ("c1", "read_file", {"path": "grades.csv"}, "姓名,分数\n张三,87\n李四,62\n", True),
        ("c2", "bash", {"command": "python analyze.py"},
         "错误: 找不到文件 analyze.py —— 先把脚本放进来", True),
        ("c3", "write_file", {"path": "report.md"}, "已写入 report.md(312 字节)", True),
    ]),
    _turn("再看看图书馆有没有相关资料", [
        # 并行子代理内部的那一步:`_QuietUI` 吞了 tool_start,所以实时表里**没有**它
        ("c4", "explore·read_file", {"path": "deep.md"}, "里面有三节写检索口径", False),
        ("c5", "grep", {"pattern": "检索"}, None, True),      # 还没回话 = 正在跑
    ]),
]


def _build_session(ws):
    """真写一份会话消息,同时用真 GuiUI 记一遍实时表(顺序与消息一致)。"""
    from minicode.gui_ui import GuiUI
    from minicode.llm import AssistantTurn, ToolCall
    from minicode.session import Session

    s = Session("sys", log_dir=None, workspace=ws)
    ui = GuiUI()
    for turn in TURNS:
        s.add_user(turn["text"])
        s.add_assistant(AssistantTurn(text="", tool_calls=[
            ToolCall(id=c[0], name=c[1], arguments=json.dumps(c[2], ensure_ascii=False))
            for c in turn["calls"]]))
        for cid, name, args, result, recorded in turn["calls"]:
            if recorded:
                ui.tool_start(_tool(name), args)
                if name == "write_file":
                    time.sleep(0.6)          # 让"用时"至少凑满 1 秒,别测出一个假 0
                if result is not None:
                    ui.tool_done(_tool(name), result)
            if result is not None:
                s.add_tool_result(cid, name, result)
    return s, ui


def _plan_state(ws):
    """真把计划写进 plans/plan.md,再用真 plan_state 读回来(和弹窗同源)。"""
    from minicode.planning import dispatch_text, plan_state, render_plan
    steps = [
        {"title": "读成绩表", "status": "done"},
        {"title": "跑分析脚本", "detail": "脚本缺失", "status": "failed"},
        {"title": "写报告", "status": "in_progress"},
        {"title": "查馆藏补充", "depends_on": [1], "status": "pending"},
        {"title": "汇总给老师", "depends_on": [2], "status": "pending"},
    ]
    (ws / "plans").mkdir(parents=True, exist_ok=True)
    (ws / "plans" / "plan.md").write_text(
        render_plan("期中成绩报告", steps), encoding="utf-8")
    st = plan_state(ws)
    return {"ok": True, "dispatch_text": dispatch_text(st), **st}


WS = Path(tempfile.mkdtemp(prefix="yx-v0937-"))
(WS / "report.md").write_text("# 期中成绩报告\n\n均分 74.5\n" * 6, encoding="utf-8")
(WS / "grades.csv").write_text("姓名,分数\n", encoding="utf-8")
SESSION, UI = _build_session(WS)

from minicode.activity import session_activity          # noqa: E402

BUSY = session_activity(SESSION.messages, workspace=WS, live=UI.live_rows(),
                        now=UI.live_now(), busy=True)
# 用户按「停止」:KeyboardInterrupt 逃出 _execute,tool_done 永不再来 → 真走这一步
UI.live_abandon()
IDLE = session_activity(SESSION.messages, workspace=WS, live=UI.live_rows(),
                        now=None, busy=False)
EMPTY = session_activity([], workspace=WS, live=[], now=None, busy=False)
PLAN = _plan_state(WS)
# 没有工作区计划的情形:同一份载荷把计划内容摘掉(证明"没计划"时那一段真的不出现)
NO_PLAN = {**PLAN, "has_plan": False, "steps": [], "total": 0, "done": 0,
           "ready": [], "blocked": [], "failed": [], "running": [],
           "dispatch_text": ""}

# ---- 自检:后端真算出了这些数(否则下面的断言全是在验一张空面板)
_T = BUSY["timeline"]["turns"]
assert BUSY["summary"]["calls"] == 5 and BUSY["summary"]["errors"] == 1, BUSY["summary"]
assert BUSY["summary"]["running"] == 1 and BUSY["summary"]["interrupted"] == 0
assert BUSY["now"] and BUSY["now"]["tool"] == "grep", BUSY["now"]
assert [t["n"] for t in _T] == [2, 1] and _T[1]["calls"] == 3, _T
assert _T[1]["errors"] == 1 and _T[0]["running"] == 1
assert IDLE["summary"]["interrupted"] == 1 and IDLE["summary"]["running"] == 0
assert IDLE["now"] is None and BUSY["empty"] is False and EMPTY["empty"] is True
assert PLAN["has_plan"] and PLAN["blocked"] == [5] and PLAN["ready"] == [4], PLAN


STUB_JS = """
window.__E2E_CALLS__ = [];
window.__ACT_BUSY__ = @@BUSY@@;
window.__ACT_IDLE__ = @@IDLE@@;
window.__ACT_EMPTY__ = @@EMPTY@@;
window.__PLAN__ = @@PLAN@@;
window.__NO_PLAN__ = @@NO_PLAN@@;
window.__ACT__ = window.__ACT_BUSY__;
window.__PREF_DATA__ = @@PREF@@;
window.__plan = window.__PLAN__;

const PREF_KEY = "__e2e_monitor_pref__";
/* 折叠状态走 localStorage:它是这一场里唯一能活过 page.reload() 的东西,
   正好当"磁盘"用 —— 重载后还是那个样子,才说明真记到了文件里。 */
window.pywebview = {
  api: {
    get_info: () => Promise.resolve({
      version: "0.9.37", app_name: "引路学长",
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
    get_activity: () => Promise.resolve(window.__ACT__),
    get_plan: () => Promise.resolve(window.__plan),
    get_monitor_pref: () => {
      let p = null;
      try { p = JSON.parse(localStorage.getItem(PREF_KEY) || "null"); } catch (e) { p = null; }
      return Promise.resolve(p || window.__PREF_DATA__);
    },
    set_monitor_pref: (isOpen, foldedJson) => {
      window.__E2E_CALLS__.push(["set_monitor_pref", isOpen, foldedJson]);
      const cur = JSON.parse(localStorage.getItem(PREF_KEY) || "null")
        || JSON.parse(JSON.stringify(window.__PREF_DATA__));
      if (isOpen !== null && isOpen !== undefined) cur.open = !!isOpen;
      if (foldedJson) {
        try {
          const patch = JSON.parse(foldedJson);
          cur.folded = Object.assign({}, cur.folded || {}, patch);
        } catch (e) { /* 坏 JSON 忽略:与后端同一条口径 */ }
      }
      localStorage.setItem(PREF_KEY, JSON.stringify(cur));
      return Promise.resolve({ok: true, ...cur});
    },
    open_url: (u) => { window.__E2E_CALLS__.push(["open_url", u]);
                        return Promise.resolve({ok: true}); },
    reveal_file: (p) => { window.__E2E_CALLS__.push(["reveal_file", p]);
                          return Promise.resolve({ok: true}); },
    get_schedule_view: () => Promise.resolve({ok: false, msg: "e2e 不测课表"}),
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
    from playwright.sync_api import sync_playwright

    stub = (STUB_JS
            .replace("@@BUSY@@", json.dumps(BUSY, ensure_ascii=False))
            .replace("@@IDLE@@", json.dumps(IDLE, ensure_ascii=False))
            .replace("@@EMPTY@@", json.dumps(EMPTY, ensure_ascii=False))
            .replace("@@PLAN@@", json.dumps(PLAN, ensure_ascii=False))
            .replace("@@NO_PLAN@@", json.dumps(NO_PLAN, ensure_ascii=False))
            .replace("@@PREF@@", json.dumps({"ok": True, "open": False, "folded": {}})))
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
            page.wait_for_function("() => document.getElementById('btnActivity') !== null")
            page.wait_for_timeout(250)      # 等 boot 里那一路 get_* 回来

            def calls():
                return page.evaluate("() => window.__E2E_CALLS__")

            def texts(sel):
                return [t.strip() for t in
                        page.eval_on_selector_all(sel, "es => es.map(e => e.textContent)")]

            # ---- 1. 默认收起:不敲桥、不占地方 ----
            check(page.locator(".app.act-open").count() == 0,
                  "开机默认收起(与 monitor-pref 的默认值一致)")
            check("set_monitor_pref" not in [c[0] for c in calls()],
                  "只是开机读一次偏好,没有拿默认值回写磁盘")
            page.click("#btnActivity")
            page.wait_for_selector(".app.act-open")
            page.wait_for_selector("#actBody .act-sect")
            check(page.is_visible("#activity"), "点顶栏「🛰 监控」真的把栏挤出来了")

            # ---- 2. 「此刻」:转圈 + 秒数在走 ----
            now = page.locator(".act-now")
            check(now.count() == 1, "只有一行「此刻」(不是每步都挂一个)")
            head = now.locator(".act-t").inner_text().strip()
            check(head.startswith("搜代码"), f"此刻那一步的名字来自后端动词表:{head}")
            check(page.locator(".act-now .act-g").count() == 1, "真的转起了那个圈(有 .act-g 元素)")
            first = now.locator(".act-time").inner_text().strip()
            page.wait_for_timeout(1300)
            second = now.locator(".act-time").inner_text().strip()
            n1 = int(first.replace("已跑 ", "").replace(" 秒", ""))
            n2 = int(second.replace("已跑 ", "").replace(" 秒", ""))
            check(n2 >= n1 + 1, f"「已跑 N 秒」这一格自己在走:{first} → {second}")
            check("explore" not in now.locator(".act-s").inner_text(),
                  "子代理前缀只在该显示时显示(这一行不是子代理)")

            # ---- 3. 段结构与真数字 ----
            caps = texts(".act-cap.fold b")
            check(caps == ["⏱ 此刻", "🗂 计划", "🧾 时间线", "📦 产出与来源"],
                  f"四段按此刻/计划/时间线/产出排,空的段不占位:{caps}")
            s = BUSY["summary"]
            sumtxt = page.locator("#actSum").inner_text().strip()
            check(f"{s['calls']} 次工具调用" in sumtxt and f"{s['errors']} 处失败" in sumtxt
                  and f"{s['running']} 个在跑" in sumtxt,
                  f"抬头那行数字逐字取自后端:{sumtxt}")
            check("统计于" in sumtxt, f"标了「统计于 HH:MM:SS」(是算的时刻,不是编的):{sumtxt}")

            # ---- 4. 计划段:数字与状态全取自 plan_state,点一行开弹窗 ----
            rows = page.locator("#actBody .plm-row")
            check(rows.count() == PLAN["total"],
                  f"计划行数 = 步骤数({PLAN['total']}),没有前端自己补的")
            badges = texts("#actBody .plm-row .act-badge")
            check(badges == ["完成", "失败", "进行中", "可做", "被卡住"],
                  f"五步的徽章逐一对上依赖算法:{badges}")
            cls = page.eval_on_selector_all("#actBody .plm-row", "es => es.map(e => e.className)")
            check(all(x.split()[-1] == y for x, y in
                      zip(cls, ["done", "failed", "run", "pending", "blocked"])),
                  f"每行的圆点 class 与徽章同一种状态:{[c.split()[-1] for c in cls]}")
            pct = page.eval_on_selector("#actBody .plm-bar i", "e => e.style.width")
            check(pct == f"{round(PLAN['done'] * 100 / PLAN['total'])}%",
                  f"进度条宽度 = 后端 done/total:{pct}")
            check(f"{PLAN['done']}/{PLAN['total']} 已完成"
                  in page.locator("#actBody .act-sect .act-more").first.inner_text(),
                  "段尾那句「N/M 已完成」也是后端那两个数")
            rows.nth(3).click()
            page.wait_for_selector("#planOverlay.show")
            check(page.locator("#plBody .pl-step").count() == PLAN["total"],
                  "点监控栏里的计划行 → 真打开了计划弹窗(不是只改了 class)")
            page.click("#btnPlanClose")
            page.wait_for_selector("#planOverlay:not(.show)", state="attached")

            # ---- 5. 时间线:轮次折叠、标题数字、轮内先后 ----
            def head_text(t):
                bits = [f"执行工具 {t['calls']} 次"]
                if t["errors"]:
                    bits.append(f"其中 {t['errors']} 次失败")
                if t["running"]:
                    bits.append(f"{t['running']} 次在跑")
                if t["stale"]:
                    bits.append(f"{t['stale']} 次已中断")
                return ",".join(bits)

            def loc_texts(loc):
                return [loc.nth(i).inner_text().strip() for i in range(loc.count())]

            tl_secs = page.locator("#actBody .act-sect")
            timeline_sec = tl_secs.nth(2)
            tl = timeline_sec.locator(".act-turn")
            check(tl.count() == BUSY["timeline"]["turns_total"],
                  f"轮数与后端一致({BUSY['timeline']['turns_total']} 轮)")
            heads = loc_texts(timeline_sec.locator(".act-turnhead b"))
            t0, t1 = BUSY["timeline"]["turns"]
            check(heads[0] == head_text(t0),
                  f"最新那轮的标题数字逐字取自后端:{heads[0]}")
            check(heads[1] == head_text(t1),
                  f"上一轮写明「其中 M 次失败」:{heads[1]}")
            check("「" in timeline_sec.locator(".act-turnhead .act-q").nth(1).inner_text(),
                  "轮次标题带着当初那句用户提问")
            check("folded" in (tl.nth(1).get_attribute("class") or ""),
                  "上一轮默认折起(最新一轮才展开)")
            tl.nth(1).locator(".act-turnhead").click()
            check("folded" not in (tl.nth(1).get_attribute("class") or ""),
                  "点标题行真的把上一轮展开了")
            check(tl.nth(1).locator(".act-caret").inner_text().strip() == "▾",
                  "展开后那个尖头跟着翻(不是只换了 class 看不见)")
            order = loc_texts(tl.nth(1).locator(".act-row .act-badge"))
            check(order == ["读文件", "跑命令 · 失败", "写文件"],
                  f"轮内按**先后**排,不是倒过来;失败那行把状态写在动词旁边:{order}")
            subs = loc_texts(tl.locator(".act-row .act-s"))
            with_time = [t for t in subs if ":" in t]
            check(len(with_time) == 4,
                  f"实时表记到的 4 步有时刻,其余不编:{with_time}")
            check(not any("00:00:00" in t for t in with_time),
                  "没有一行拿零点整凑数")
            dur = [t for t in subs if "用时" in t]
            check(len(dur) == 3, f"只有真记到结束的时刻才有「用时」:{dur}")
            check("1s" in " ".join(dur), "写文件那一步的用时是真的(睡过 0.6 秒)")

            # ---- 6. 点一行真做事:reveal_file 与「看原文」 ----
            file_row = page.locator("#actBody .act-row", has_text="report.md").first
            file_row.click()
            page.wait_for_timeout(150)
            rev = [c for c in calls() if c[0] == "reveal_file"]
            check(len(rev) == 1 and rev[0][1].endswith("report.md"),
                  f"点文件行真的调了 reveal_file,路径逐字对上:{rev}")
            bash_row = page.locator("#actBody .act-row", has_text="python analyze.py").first
            check("failed" in (bash_row.get_attribute("class") or ""),
                  "失败那一步的圆点是红的(class=failed)")
            bash_row.click()
            detail = page.locator("#actBody .act-detail")
            check(detail.count() == 1 and "找不到文件" in detail.inner_text(),
                  "点失败那行真的展开了它的原文(后端给的那段,不是前端编的)")
            bash_row.click()
            check(page.locator("#actBody .act-detail").count() == 0,
                  "再点一次收回去")
            plain = page.eval_on_selector_all(
                "#actBody .act-row.plain .act-t", "es => es.map(e => e.textContent)")
            check(any("检索" in t for t in plain),
                  f"正在跑的那一步没有去处 → 老实标成不可点:{plain}")

            # ---- 7. 子代理那一步:不显示时刻 ----
            sub = page.locator("#actBody .act-row", has_text="deep.md").first
            check(sub.locator(".act-s").count() == 0,
                  "并行子代理那一步没被实时表记到 → 宁可不显示时间")
            check("读文件" in sub.locator(".act-badge").inner_text(),
                  "但这一行照样在时间线上(少报比编数字更糟)")

            # ---- 8. 按了停止:不转圈,改说「已中断」 ----
            page.evaluate("() => { window.__ACT__ = window.__ACT_IDLE__; }")
            page.click("#btnActRefresh")
            page.wait_for_timeout(200)
            check(page.locator(".act-now").count() == 0,
                  "会话停下来后「此刻」那一行不再装作有东西在跑")
            check("没有正在执行的工具" in page.locator(".act-idle").first.inner_text(),
                  "「此刻」改说大实话")
            idle_sum = page.locator("#actSum").inner_text().strip()
            check("1 个已中断" in idle_sum and "个在跑" not in idle_sum,
                  f"抬头改报「已中断」、不再报「在跑」:{idle_sum}")
            interrupted = [t for t in texts("#actBody .act-badge") if "已中断" in t]
            check(interrupted == ["搜代码 · 已中断"],
                  f"那一行自己写着「已中断」,不是只换个圆点颜色:{interrupted}")
            check(page.locator("#actBody .act-row.running").count() == 0,
                  "全场没有一个 .running 的圆点在转")

            # ---- 9. 空会话:不摆一排"暂无" ----
            # 计划段与弹窗同源(都吃 renderPlan),所以换计划要走真事件 plan_changed,
            # 而不是偷偷换一个全局变量 —— 顺带证明"模型标完一步,栏里立刻跟着变"。
            def push_plan(payload):
                page.evaluate("(p) => window.__gcodeEvent("
                              "Object.assign({type: 'plan_changed', session: 's1'}, p))",
                              payload)
                page.wait_for_timeout(120)

            page.evaluate("() => { window.__ACT__ = window.__ACT_EMPTY__; }")
            push_plan(NO_PLAN)
            page.click("#btnActRefresh")
            page.wait_for_timeout(200)
            caps2 = texts(".act-cap.fold b")
            check(caps2 == ["⏱ 此刻"], f"空会话只剩「此刻」一段(其余段整段不出现):{caps2}")
            check(page.locator("#actBody .act-empty").count() == 1,
                  "补一句「这一轮还没有要汇报的动作」")
            check(page.locator("#actBody .act-row").count() == 0,
                  "没有一行是硬撑出来的空行")
            # 回到有料的那份,继续测折叠持久化
            page.evaluate("() => { window.__ACT__ = window.__ACT_BUSY__; }")
            push_plan(PLAN)
            page.click("#btnActRefresh")
            page.wait_for_selector("#actBody .plm-row")

            # ---- 10. 段折叠:写盘 → 重载页面 → 还是这个样子 ----
            tl_cap = page.locator(".act-cap.fold", has_text="时间线")
            tl_cap.click()
            page.wait_for_timeout(150)
            folded = [c for c in calls() if c[0] == "set_monitor_pref" and c[2]]
            check(folded and json.loads(folded[-1][2]) == {"timeline": True},
                  f"折一段只写那一段(增量,不冲掉别的段):{folded[-1][2] if folded else None}")
            check("folded" in (page.locator("#actBody .act-sect").nth(2).get_attribute("class") or ""),
                  "点标题真的把这一段收起来了")
            plan_cap = page.locator(".act-cap.fold", has_text="计划")
            plan_cap.click()
            page.wait_for_timeout(150)
            page.reload(wait_until="load")
            page.wait_for_selector("#actBody .act-sect")
            page.wait_for_timeout(250)
            states = page.eval_on_selector_all(
                "#actBody .act-sect", "es => es.map(e => e.querySelector('b').textContent + '=' + (e.classList.contains('folded') ? '折' : '开'))")
            check(sum(1 for x in states if x.endswith("时间线=折")) == 1
                  and sum(1 for x in states if x.endswith("计划=折")) == 1,
                  f"重载后还是折着的(真记到了「磁盘」):{states}")
            check(page.locator(".app.act-open").count() == 1,
                  "重载后这一栏仍然开着(上次是用户点开的)")
            page.locator(".act-cap.fold", has_text="时间线").click()
            page.wait_for_timeout(120)
            check("folded" not in (page.locator("#actBody .act-sect").nth(2).get_attribute("class") or ""),
                  "再点一次能展开 —— 折叠不是一扇单向门")

            # ---- 11. Ctrl+Shift+M:键盘也能收起/展开 ----
            page.keyboard.press("Control+Shift+M")
            page.wait_for_timeout(120)
            check(page.locator(".app.act-open").count() == 0,
                  "Ctrl+Shift+M 真的收起了这一栏")
            stored = page.evaluate(
                f"() => JSON.parse(localStorage.getItem('{PREF_KEY}') || 'null')")
            check(stored and stored.get("open") is False,
                  f"收起这件事也写进了偏好文件:{stored}")
            page.keyboard.press("Control+Shift+M")
            page.wait_for_selector(".app.act-open")
            check(page.locator("#actBody .act-sect").count() == 4,
                  "重新展开后四段都在(折叠状态没被这次开关冲掉)")

            # ---- 12. 关掉这一栏不再敲桥 ----
            page.click("#btnActClose")
            page.wait_for_timeout(120)
            before = len(calls())
            page.evaluate("() => window.__gcodeEvent && window.__gcodeEvent({type:'tool_start', name:'bash', session:'s1'})")
            page.wait_for_timeout(300)
            check(len(calls()) == before, "栏收着的时候,工具事件不再触发统计(白费)")

            # ---- 12.5 存一张图给人眼看(写到系统临时目录,绝不进仓库) ----
            page.click("#btnActivity")
            page.wait_for_selector(".app.act-open")
            page.wait_for_timeout(250)
            check(page.locator("#actBody .act-sect").count() == 4,
                  "重新点开后四段又都在了(收起→展开这一趟没弄丢内容)")
            # 这一条原本量 `#btnActivity` 的高度(<32 ⇒ 顶栏只有一行)。p3 之后那颗按钮
            # 搬进了最左入口栏,而且本来就是"图标 + 标签"两行的设计 —— 再量它就等于拿设计稿
            # 当尺子。**它真正守的是「300px 那一栏挤进来时,顶栏不许被挤到换行」**,所以改成
            # 直接量顶栏自己:单行时的高 = 上下 padding + 最高那个子元素(两个都是现量的,
            # 不是写死的魔数);真换了行就会多出一整行 + 一个 gap,这个式子立刻不成立。
            tb = page.evaluate("""() => {
                const t = document.querySelector('.topbar');
                const cs = getComputedStyle(t);
                const pad = parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom);
                let tall = 0;
                for (const c of t.children) tall = Math.max(tall, c.getBoundingClientRect().height);
                return {h: t.getBoundingClientRect().height, one: pad + tall, n: t.children.length};
            }""")
            check(tb["h"] <= tb["one"] + 1,
                  "栏开着(300px)顶栏仍是一行:%.1fpx(单行算出来 %.1fpx,%d 个控件)"
                  % (tb["h"], tb["one"], tb["n"]))
            shot = Path(tempfile.gettempdir()) / "yx-v0937-rail.png"
            page.screenshot(path=str(shot))
            print(f"  · 截图:{shot}")

            # ---- 13. 页面不许有 JS 错(白屏类回归) ----
            bad = [c for c in console
                   if "Failed to load resource" not in c and "favicon" not in c]
            check(not bad, f"整场跑下来控制台没有 JS 报错:{bad[:3]}")
            check(not missing, f"静态服务上没有 404 的资源:{missing[:4]}")
            browser.close()
    finally:
        shutdown()

    print()
    if failures:
        print(f"共 {len(failures)} 项失败:")
        for f in failures:
            print("  ✗ " + f)
        return 1
    print("v0.9.37 前端回归:全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
