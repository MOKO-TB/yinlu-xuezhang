# -*- coding: utf-8 -*-
"""v0.9.101 前端回归:本轮改动逐处回退 / 跨会话看板 / 指令层那一行。

补的是 §2 那行末尾自己记下的账 —— 三处新界面当时只有源码级针与桥级测试,
"按钮点下去到底发生了什么"没在真浏览器里验过。这一场验的就是那一句。

要钉住的八件事(每条都是界面**可能自己骗人**的那种):
1. 「本轮改动」的抬头那句边界必须真出现在屏上 —— 没有它,那一屏就变成
   "点全部回退就回到本轮开始前"的错觉(这是这一屏最危险的假话);
2. 行的顺序、`改/新建` 与 `恢复/删除` 全部照桥给的,前端不自己判 kind;
3. `newer` / `drift` 两个事实进得去**确认框的文案**,不只是列表里的小字 ——
   回退是动手面,点头之前必须看得见"退它会把别的也带走";
4. 点某一行敲的是 `undo_step(那一行的 seq)`,不是第一条、也不是全部;
5. 退完会重新拉一次 `get_activity`(清单来自撤销栈,前端不留缓存);
6. `changes` 空 → 整段不出现(不摆"暂无"占位);
7. 看板三态、"另有 N 条没列出"、"没读到"那行、以及 scan/boundary 两句都在屏上;
   三种点击各做各的真事:切会话 / 开计划弹窗 / 把话**填进**输入框而不发送;
8. 指令层那一行逐字是桥给的句子,`line` 空时整行隐藏。

跑法(项目根目录,需要 PYTHONIOENCODING=utf-8 否则中文断言在 GBK 控制台炸):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/e2e_ui_v09101.py
"""
from __future__ import annotations

import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # 判据里有 ✓

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from e2e_launch import launch          # noqa: E402  起浏览器这件事的一份真相

NOTE = ("只列我用 write_file / edit_file 改的文件;命令(bash / py_script)与"
        "外部程序改的不在这里,也退不掉。")
BOUNDARY = ("「在跑」只知道这个程序开着期间的事 —— 那份标记在桥的内存里,"
            "退出重开之后就看不见谁在跑了(边界写在方案 §七.4,已拍板接受)。")

# `changes` 刻意做成不对称的一份:seq 不是 1/2、两条同文件(一条 newer、一条 drift)、
# 还有一条"新建"—— 前端若自己按顺序编号、自己判 kind、或把 drift 弄丢,都会当场红。
STUB_JS = r"""
window.__CALLS__ = [];
window.__ACT__ = {
  ok: true, summary: {calls: 4, errors: 0, running: 0, interrupted: 0},
  timeline: {calls: 4, turns: []}, updated_at: "10:20:30",
  groups: [{key: "files", label: "文件", count: 2, more: 0, items: [
      {key: "f:doc.txt", title: "doc.txt", sub: "写入", badge: "ok", status: "ok",
       action: {type: "file", path: "doc.txt"}}]}],
  changes: {
    ok: true, total: 5, more: 2, note: __NOTE__, hint: "没有可退的改动",
    items: [
      {seq: 12, path: "src/app.py", kind: "改", verb: "恢复", at: "10:20:31",
       newer: 0, drift: true},
      {seq: 9, path: "src/app.py", kind: "改", verb: "恢复", at: "10:18:02",
       newer: 1, drift: false},
      {seq: 4, path: "notes/new.md", kind: "新建", verb: "删除", at: "10:11:40",
       newer: 0, drift: false}]}
};
window.__BOARD__ = {
  ok: true, at: "10:22:05", scan: 20, scan_note: "只列最近 20 个会话(不是全部)",
  boundary: __BOUNDARY__, failed_sources: ["计划"], empty: false,
  counts: {running: 3, waiting: 3, done: 1},
  more: {running: 1, waiting: 1, done: 0},
  groups: {
    running: [
      {state: "running", title: "修登录跳转", sub: "12 条 · 10-02 09:58 · 侧栏第 3 个",
       action: {type: "session", index: 3}, source: "会话"},
      {state: "running", title: "后台命令 j2:pytest -q", sub: "已跑 41 秒 · 输出 812 字",
       action: {type: "ask", text: "看一下后台命令 j2 跑到哪了"}, source: "后台命令"},
      {state: "running", title: "计划第 2 步:补测试", sub: "已标进行中(做完要标 done)",
       action: {type: "panel", panel: "plan"}, source: "计划"}],
    waiting: [
      {state: "waiting", title: "学长在等你答", sub: "提问卡开着",
       action: {}, source: "会话"},
      {state: "waiting", title: "第 4 步被上游卡住:打包", sub: "要人工决定",
       action: {type: "panel", panel: "plan"}, source: "计划"},
      {state: "waiting", title: "计划:没读到", sub: "RuntimeError: 计划文件坏了",
       action: {}, source: "计划"}],
    done: [
      {state: "done", title: "定时任务「周复盘」", sub: "上次 10-01 20:03 · 结论 已推",
       action: {type: "ask", text: "说一下「周复盘」上次跑出了什么"},
       source: "定时任务"}]}};
window.__CHANGES__ = { ok: true, total: 3, more: 0, note: __NOTE__, hint: "",
  items: [
    {seq: 12, path: "src/app.py", kind: "改", verb: "恢复", at: "10:20:31",
     newer: 0, drift: false, turn: 2, stale: false},
    {seq: 9, path: "notes/new.md", kind: "新建", verb: "删除", at: "10:18:02",
     newer: 0, drift: false, turn: 2, stale: false},
    {seq: 4, path: "old/keep.py", kind: "改", verb: "恢复", at: "10:11:40",
     newer: 0, drift: false, turn: 1, stale: false}] };
window.__INSTR__ = {ok: true, line: "内置 + 用户级(2 行) + 项目级(3 行)", layers: [
  {scope: "用户级", name: "AGENTS.md", lines: 2, chars: 41, truncated: false},
  {scope: "项目级", name: "AGENTS.md", lines: 3, chars: 60, truncated: true}]};

const base = {
  get_info: () => Promise.resolve({
    version: "0.9.101", app_name: "引路学长", permission_mode: "confirm",
    permission_modes: [{value: "confirm", label: "变更前确认"}],
    model: "test-model", active_provider: "test", workspace: "D:/demo/campus",
    workspace_name: "campus", workspace_is_default: true,
    providers: [{id: "test", name: "Test", model: "test-model", has_key: true}],
    api_key_configured: true, memory_count: 0, memory_user_count: 0,
    mcp_servers: [], skills: [], kb_count: 0, sandbox: null, campus_map: null,
    library_health: {}, paths: {app_dir: "D:/demo", writable: true},
    instructions: window.__INSTR__,
    session_metrics: {ok: false, selectable: []}}),
  get_activity: () => {
    window.__CALLS__.push(["get_activity"]);
    return Promise.resolve(window.__ACT__);
  },
  get_board: () => {
    window.__CALLS__.push(["get_board"]);
    return Promise.resolve(window.__BOARD__);
  },
  get_monitor_pref: () => Promise.resolve({ok: true, open: false, folded: {}}),
  set_monitor_pref: () => Promise.resolve({ok: true}),
  refresh_sessions: () => Promise.resolve({ok: true}),
  get_recent_projects: () => Promise.resolve({ok: true, current: {}, default: {},
                                              recent: []}),
  get_reasoning: () => Promise.resolve({visible: false, options: [], choice: "default"}),
  get_suggestions: () => Promise.resolve({suggestions: []}),
  get_slash_commands: () => Promise.resolve({ok: true, commands: []}),
  get_countdowns: () => Promise.resolve({ok: true, countdowns: []}),
  get_plan: () => Promise.resolve({ok: true, has_plan: true, goal: "测试看板",
                                   path: "plans/plan.md", total: 4, done: 1,
                                   verify: [], ready: [], blocked: [4], failed: [3],
                                   running: [2], dispatch_text: "按计划继续",
                                   steps: [{n: 1, title: "一件", detail: "",
                                            status: "done", depends_on: [],
                                            note: "", verify: "", ready: false,
                                            blocked: false}]}),
  get_session_metrics: () => Promise.resolve({ok: false}),
  read_clipboard: () => Promise.resolve({text: "", image: false, source: "empty"}),
  reveal_file: (p) => { window.__CALLS__.push(["reveal_file", p]);
                       return Promise.resolve({ok: true}); },
  undo_diff: (seq) => {
    window.__CALLS__.push(["undo_diff", seq]);
    return Promise.resolve({ok: true, path: "src/app.py", kind: "改",
      added: 2, removed: 1, drift: true, same: false, more: 7,
      note: "左边这份是我最后一次改它之前的内容,右边是磁盘上的现在这份。"
            + "这一处在我改过之后还被别的工具动过,所以这份 diff 里含别人的改动。",
      lines: ["@@ -10,2 +10,3 @@", "-old line", "+new line", "+another", " ctx line"]});
  },
  undo_records: () => {
    window.__CALLS__.push(["undo_records"]);
    return Promise.resolve(window.__CHANGES__);
  },
  edit_resend: (ord, text, expect, revert) => {
    window.__CALLS__.push(["edit_resend", ord, text, !!revert]);
    return Promise.resolve({ok: true, reverted: revert ? ["src/app.py"] : []});
  },
  undo_step: (seq) => {
    window.__CALLS__.push(["undo_step", seq]);
    return Promise.resolve({ok: true, msg: "已撤销:src/app.py 已恢复为改动前的内容"});
  },
  undo_all: () => {
    window.__CALLS__.push(["undo_all"]);
    return Promise.resolve({ok: true, msg: "已全部回退 5 处。"});
  },
  undo: () => { window.__CALLS__.push(["undo"]);
                return Promise.resolve({ok: true, msg: "已撤销"}); },
  resume_session: (i) => { window.__CALLS__.push(["resume_session", i]);
                           return Promise.resolve({ok: true}); },
  send_task: (t) => { window.__CALLS__.push(["send_task", t]);
                      return Promise.resolve({ok: true}); },
};
window.pywebview = {api: new Proxy(base, {
  get: (t, k) => (k in t) ? t[k]
    : ((...a) => { window.__CALLS__.push([k, ...a]); return Promise.resolve({ok: true}); })
})};
""".replace("__NOTE__", '"%s"' % NOTE).replace("__BOUNDARY__", '"%s"' % BOUNDARY)

SECT_SELECTOR = ".act-cap[data-sect='changes']"


def _serve(directory: Path):
    class _QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = partial(_QuietHandler, directory=str(directory))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}/index.html", httpd.shutdown


def main() -> int:
    from playwright.sync_api import sync_playwright

    url, shutdown = _serve(FRONTEND)
    failures: list[str] = []
    ran = 0

    def check(cond, message):
        nonlocal ran
        ran += 1
        print(("  ✓ " if cond else "  ✗ ") + message)
        if not cond:
            failures.append(message)

    with sync_playwright() as p:
        browser = launch(p)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.add_init_script(STUB_JS)
        page.goto(url)
        page.wait_for_selector("#ver", timeout=15000)

        def calls(name):
            return [c for c in page.evaluate("window.__CALLS__") if c and c[0] == name]

        # ---- 0. 编辑重发:文件退不退要问一句,而且两个出口都能走通(R3) ----
        page.evaluate("() => { addUserMsg('第一问问法'); addUserMsg('第二问问法'); }")
        page.evaluate("() => { startEdit(2, '第二问问法'); "
                      "document.getElementById('input').value = '换个问法再发'; }")
        page.evaluate("() => send()")
        page.wait_for_selector("#appConfirmOverlay.show", timeout=5000)
        ask = page.inner_text("#appConfirmMsg")
        check("2 处" in ask and "src/app.py" in ask and "notes/new.md" in ask,
              f"问句报的是「这一问之后改过几处」与文件名(实测「{ask[:56]}…」)")
        check("old/keep.py" not in ask,
              "更早那一问(第 1 问)自己改的文件不混进来回退范围")
        check(page.inner_text("#btnAppOk").strip() == "退回文件并重发"
              and page.inner_text("#btnAppCancel").strip() == "保留文件,只重发",
              "两个按钮各说各的结局(不是含糊的「确定/取消」)")
        page.click("#btnAppOk")
        page.wait_for_function(
            "() => window.__CALLS__.some(c => c[0] === 'edit_resend')", timeout=5000)
        check(calls("edit_resend")[-1][1] == 2 and calls("edit_resend")[-1][3] is True,
              "点「退回文件并重发」:编辑的是第 2 问、传的是 revert_files=true")
        check(page.evaluate("() => document.getElementById('toasts').textContent")
              .count("已退回 1 处") == 1,
              "退了几处由后端回话决定,前端不自己数")

        # 第二条出口:保留文件只重发
        page.evaluate("() => { startEdit(2, '第二问问法'); "
                      "document.getElementById('input').value = '第三种问法'; }")
        page.evaluate("() => send()")
        page.wait_for_selector("#appConfirmOverlay.show", timeout=5000)
        page.click("#btnAppCancel")
        page.wait_for_function(
            "() => window.__CALLS__.filter(c => c[0] === 'edit_resend').length >= 2",
            timeout=5000)
        check(calls("edit_resend")[-1][3] is False,
              "点「保留文件,只重发」传 false —— 取消这一支不该什么都不做")

        # 没改过文件:一次确认都不弹,直接重发
        page.evaluate("() => { window.__CHANGES__ = {ok: true, items: [], total: 0,"
                      " more: 0, note: '', hint: ''}; }")
        page.evaluate("() => { startEdit(2, '第二问问法'); "
                      "document.getElementById('input').value = '第四种问法'; }")
        page.evaluate("() => send()")
        page.wait_for_function(
            "() => window.__CALLS__.filter(c => c[0] === 'edit_resend').length >= 3",
            timeout=5000)
        check(page.evaluate("() => !document.getElementById('appConfirmOverlay')"
                            ".classList.contains('show')"),
              "那一问之后没改过文件就不弹确认(不问一个没有选项的问题)")
        check(calls("edit_resend")[-1][3] is False, "直接重发时 revert 是 false")
        page.evaluate("() => { window.__CHANGES__ = {ok: true, total: 3, more: 0,"
                      " note: 'x', hint: '', items: []}; }")

        # ---- 1. 指令层那一行:逐字是桥给的句子,空就整行不出现 ----
        page.wait_for_function(
            "() => !document.getElementById('instrLine').hidden", timeout=8000)
        instr = page.inner_text("#instrLine").strip()
        check(instr == "📐 指令:内置 + 用户级(2 行) + 项目级(3 行)",
              f"侧栏「📐 指令」逐字是桥给的那句(实测「{instr}」)")
        check("被截过" in (page.get_attribute("#instrLine", "title") or ""),
              "悬停说明里带出「项目级那一份注入时被截过」(不只在后端知道)")
        page.evaluate("() => renderInstrLine({ok: true, line: '', layers: []})")
        check(page.evaluate("() => document.getElementById('instrLine').hidden"),
              "一份指令文件都没有 → 那一行整行隐藏(不写「未配置」占位)")
        page.evaluate("() => renderInstrLine(window.__INSTR__)")

        # ---- 2. 打开监控:「本轮改动」那一段与它的抬头 ----
        page.click("#btnActivity")
        page.wait_for_selector("#actBody .act-sect", timeout=8000)
        sect = page.locator("#actBody .act-sect").filter(has=page.locator(SECT_SELECTOR))
        check(sect.count() == 1, "监控栏里有「本轮改动」这一段")
        check(NOTE[:14] in sect.first.inner_text(),
              "抬头那句边界(只列 write_file/edit_file)真在屏上")
        caps = page.evaluate("() => [...document.querySelectorAll('#actBody .act-sect "
                             "> .act-cap')].map(e => e.textContent)")
        check(any("本轮改动" in c for c in caps),
              f"段标题是「本轮改动」并带处数(实测 {caps})")
        check("5 处可退" in " ".join(caps),
              "段标题那个 5 是桥给的 total,不是前端数它自己截过的 3 行")

        rows = page.locator("#actBody [data-k^='change:']")
        check(rows.count() == 3, f"一屏列出 3 行(另有 2 处被截)(实测 {rows.count()})")
        first = rows.nth(0).inner_text()
        check("src/app.py" in first and "我改过之后还被别的工具动过" in first,
              f"第一行把 drift 那个事实说出来(实测「{first[:46]}…」)")
        check("恢复" in rows.nth(0).inner_text(),
              "「改」那类行右侧徽章是「恢复」")
        check("删除" in rows.nth(2).inner_text(),
              "「新建」那类行右侧徽章是「删除」—— 不把它说成恢复")
        check("还有 1 处没退" in rows.nth(1).inner_text(),
              "同一文件更早那一处标明「这之后还有 1 处没退」")
        more = "|".join(sect.locator(".act-more").all_inner_texts())
        check("另有 2 处没列出" in more, f"截断那件事写在明面上(实测「{more}」)")
        all_texts = sect.locator(".act-row").all_inner_texts()
        allrow = next((t for t in all_texts if "全部回退" in t), "")
        check("全部回退 5 处" in allrow, "最后那行「全部回退 5 处」的数也是桥给的")

        # ---- 2b. 「看改了什么」:展开的是后端算好的 diff,且不动手 ----
        chip = sect.locator(".act-row").first.locator(".act-chip")
        check(chip.inner_text() == "看改了什么", "每一行那颗芯片默认写「看改了什么」")
        chip.click()
        page.wait_for_function(
            "() => window.__CALLS__.some(c => c[0] === 'undo_diff')", timeout=5000)
        holder = sect.locator(".ch-diff").first
        check(holder.is_visible(), "点芯片之后 diff 块露出来了")
        head_line = holder.locator(".d-note").inner_text()
        check("+2" in head_line and "-1" in head_line.replace("−", "-")
              and "还被别的工具动过" in head_line,
              f"diff 抬头那行是后端算的数与那句提醒(实测「{head_line[:52]}…」)")
        adds = holder.locator(".d-add")
        dels = holder.locator(".d-del")
        check(adds.count() == 2 and dels.count() == 1,
              f"+ 的两行进 d-add、− 的一行进 d-del(实测 {adds.count()}/{dels.count()})")
        check("另有 7 行 diff 没列出" in holder.locator(".d-more").inner_text(),
              "被截掉的行数写在明面上,不假装是完整 diff")
        check(not calls("undo_step") and not calls("undo_all"),
              "看改动这一步一次回退都没敲 —— 展开与动手是两件事")
        chip.click()
        page.wait_for_timeout(200)
        check(not holder.is_visible(), "再点一次就收起(展开态记在行自己身上,不敲桥)")
        check(calls("undo_diff")[-1][1] == 12,
              "展开时敲的是这一行的 seq(不是第一条)")

        # ---- 3. 点一行:确认框里带代价,点头才敲对那一条 ----
        get_before = len(calls("get_activity"))
        rows.nth(0).click()
        page.wait_for_selector("#appConfirmOverlay.show", timeout=5000)
        msg = page.inner_text("#appConfirmMsg")
        check("src/app.py" in msg and "恢复成它改动前的内容" in msg,
              f"确认框说的是这一处(实测「{msg[:40]}…」)")
        check("我改过之后还被别的工具动过" in msg and "一起没掉" in msg,
              "回退前的警告把「那部分也一起没了」念在点头之前")
        page.click("#btnAppCancel")
        page.wait_for_timeout(250)
        check(not calls("undo_step"), "点「取消」之后一次 undo_step 都没敲")
        rows.nth(0).click()
        page.wait_for_selector("#appConfirmOverlay.show", timeout=5000)
        page.click("#btnAppOk")
        page.wait_for_function(
            "() => window.__CALLS__.some(c => c[0] === 'undo_step')", timeout=5000)
        check(calls("undo_step")[-1][1] == 12,
              f"敲的是这一行的 seq=12(实测 {calls('undo_step')[-1][1]})")
        page.wait_for_function(
            "() => document.getElementById('toasts').textContent.includes('已撤销')",
            timeout=5000)
        check(len(calls("get_activity")) > get_before,
              "退完之后重新拉一次活动数据 —— 清单不留在前端缓存里")

        # ---- 4. 更早那一处:newer 那句警告换一种 ----
        rows.nth(1).click()
        page.wait_for_selector("#appConfirmOverlay.show", timeout=5000)
        msg2 = page.inner_text("#appConfirmMsg")
        check("还有 1 处改动没退" in msg2,
              f"退更早那一处时警告变成「上面还有 1 处没退」(实测「{msg2[-46:]}」)")
        page.click("#btnAppCancel")

        # ---- 5. 全部回退也是一次确认 ----
        sect.locator(".act-row").nth(
            next(i for i, t in enumerate(all_texts) if "全部回退" in t)).click()
        page.wait_for_selector("#appConfirmOverlay.show", timeout=5000)
        check("退不掉" in page.inner_text("#appConfirmMsg"),
              "「全部回退」的确认框里写明命令改的退不掉(不让人以为一键回到本轮开始前)")
        page.click("#btnAppOk")
        page.wait_for_function(
            "() => window.__CALLS__.some(c => c[0] === 'undo_all')", timeout=5000)
        check(len(calls("undo_all")) == 1, "undo_all 恰好一次")

        # ---- 6. 没有可退的改动 → 整段不出现 ----
        page.evaluate("() => { window.__ACT__.changes = {ok: true, items: [],"
                      " total: 0, more: 0, note: '', hint: '没有可退的改动'};"
                      " activityRefresh(); }")
        page.wait_for_timeout(500)
        check(sect.count() == 0, "空清单时那一段整个不出现(不摆「暂无」撑版面)")
        page.evaluate("() => { window.__ACT__.changes = {"
                      " ok: false, items: [], total: 0, more: 0, note: '',"
                      " hint: '读取本轮改动失败:RuntimeError: 栈读不动'};"
                      " activityRefresh(); }")
        page.wait_for_timeout(500)
        check(sect.count() == 1 and "读不出来" in sect.first.inner_text(),
              "后端读不出来时明说读不出来,不当成「没有改动」")

        # ---- 7. 跨会话看板 ----
        page.click("#btnBoard")
        page.wait_for_selector("#boardOverlay.show", timeout=5000)
        page.wait_for_selector("#boardBody .act-row", timeout=8000)
        note = page.inner_text("#boardNote")
        check("只列最近 20 个会话" in note and "退出重开" in note,
              "看板抬头那两句是上限与边界,不是空白说明")
        board_caps = page.evaluate(
            "() => [...document.querySelectorAll('#boardBody .act-cap')]"
            ".map(e => e.textContent)")
        check(board_caps == ["在跑(3)", "等你(3)", "完成(1)"],
              f"三态标题带桥给的计数(实测 {board_caps})")
        waiting = page.locator("#boardBody .act-row")
        texts = [waiting.nth(i).inner_text() for i in range(waiting.count())]
        check(any("没读到" in t and "计划文件坏了" in t for t in texts),
              "坏掉的那个来源改口「没读到」并带原因,别的行照常")
        check(sum(1 for t in texts if "另有" in t) == 0
              and len(page.locator("#boardBody .act-more").all_inner_texts()) == 2,
              "「另有 N 条没列出」按后端 more 出现两处(在跑/等你各截 1 条),不混进行里")
        check(all("会话" in t or "计划" in t or "定时任务" in t or "后台命令" in t
                  for t in texts), "每一行右侧那个来源名都来自后端,不是前端猜的")

        # 三种点击各做各的真事
        page.click("#boardBody .act-row:nth-child(2)")   # 「在跑」第一条 = 会话行
        page.wait_for_function(
            "() => window.__CALLS__.some(c => c[0] === 'resume_session')", timeout=5000)
        check(calls("resume_session")[-1][1] == 3,
              "会话行真的切到侧栏第 3 个(resume_session(3))")
        check(page.evaluate("() => !document.getElementById('boardOverlay')"
                            ".classList.contains('show')"),
              "切会话之后看板自己收起(留着会挡住切过去的界面)")
        page.click("#btnBoard")
        page.wait_for_selector("#boardOverlay.show", timeout=5000)
        page.evaluate("() => [...document.querySelectorAll('#boardBody .act-row')]"
                      ".find(e => /计划第 2 步/.test(e.textContent)).click()")
        page.wait_for_selector("#planOverlay.show", timeout=5000)
        check(True, "计划那一行点开的是真计划弹窗(#planOverlay.show)")
        page.click("#btnPlanClose")
        page.click("#btnBoard")
        page.wait_for_selector("#boardOverlay.show", timeout=5000)
        input_before = page.input_value("#input")
        page.evaluate("() => [...document.querySelectorAll('#boardBody .act-row')]"
                      ".find(e => /后台命令 j2/.test(e.textContent)).click()")
        page.wait_for_timeout(200)
        check("后台命令 j2" in page.input_value("#input")
              and page.input_value("#input") != input_before,
              "后台命令那行把追问**填进**输入框,不替你发送")
        check(not calls("send_task"), "看板任何一行都不自动发消息")
        before_idle = len(page.evaluate("window.__CALLS__"))
        page.evaluate("() => [...document.querySelectorAll('#boardBody .act-row')]"
                      ".find(e => /学长在等你答/.test(e.textContent)).click()")
        page.wait_for_timeout(200)
        check(len(page.evaluate("window.__CALLS__")) == before_idle,
              "没有真动作的那一行点了什么都不敲桥(不给点不开的空行装事做)")

        # ---- 8. 全程不报错 ----
        check(not errors, f"整场零页面错误(实测 {errors[:2]})")

        browser.close()
    shutdown()
    print()
    if failures:
        print(f"FAIL: {len(failures)}/{ran} 项不对 →", "; ".join(failures))
        return 1
    print(f"PASS: v0.9.101 三处新界面 {ran} 项全对(真 Chromium 点过)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
