# -*- coding: utf-8 -*-
r"""v0.9.111 前端回归:模型清单分组、顶部/底部说同一个模型、提示只提醒一次、
监控栏那一节「环境信息」、思考菜单对齐,以及一条**渲染预算**。

用户拿 12 张截图点名的一批,这一支覆盖其中前端的六件:
    新 模型清单按供应商分组(行里不再每行重复一遍供应商名)
    新 顶部药丸与底部选择器写同一个模型;会话单独切过就标「本任务已切换」(p8)
    新 思考档位菜单开在自己那颗按钮底下,不是开在最左边(p11)
    新 上下文/压缩面板收窄(p10)
    新 首屏「小提示」与监控栏那段说明:点「知道了」之后本机记住,重开窗口不再出现(p12)
    新 监控栏「环境信息」一节:分支 / +N −M / 未提交文件 / 版本 / 本机数据 / 后台命令
    新 渲染预算:50 条消息的会话恢复,一次画完的时间要有个上限 —— 用户报的卡顿
      主因在后端(每切一次会话重算一次 1.6 秒的全库 COUNT),但这条守住前端这一半
跑法(项目根目录):
    PYTHONIOENCODING=utf-8 .venv\Scripts\python.exe scripts\e2e_ui_v09111.py
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

SELECTABLE = (
    [{"id": "jiyuan", "key": f"jiyuan::m{i}", "name": "基元", "model": f"neo-{i}",
      "has_key": True, "supports_vision": i % 3 == 0, "context_window": 1048576}
     for i in range(12)]
    + [{"id": "zhipu", "key": f"zhipu::m{i}", "name": "智谱", "model": f"glm-{i}",
        "has_key": True, "supports_vision": False, "context_window": 131072}
       for i in range(6)])

METRICS = {
    "provider_id": "jiyuan", "selected_key": "jiyuan::m3", "model": "neo-3",
    "session_override": True,
    "capabilities": ["文本", "工具调用"], "supports_vision": False, "known": True,
    "context_window": 1048576, "context_tokens": 30000, "consumed": 0,
    "compact_ratio": 0.7, "selectable": SELECTABLE,
    "context_breakdown": {
        "window": 1048576, "used": 30000, "compact_at": 0.7, "source": "estimated",
        "estimated": 30000,
        "buckets": [
            {"key": "system", "label": "系统提示词", "tokens": 3200},
            {"key": "tools", "label": "工具定义", "tokens": 20200},
            {"key": "skills", "label": "Skill", "tokens": 1500, "count": 25},
            {"key": "messages", "label": "对话消息", "tokens": 5100},
        ],
    },
}

ENV = {
    "app": "引路学长", "version": "0.9.111", "workspace": "D:/demo/daily",
    "is_repo": True, "branch": "main", "repo_root": "D:/demo/daily", "dirty": True,
    "files": [{"path": "a.py", "state": "改", "added": 3, "removed": 1, "binary": False},
              {"path": "新笔记.md", "state": "未跟踪", "added": None,
               "removed": None, "binary": False}],
    "total": 42, "more": 12, "added": 3, "removed": 1, "partial": True,
    "app_dir": "D:/demo/data", "backend": "local", "writable": True, "frozen": True,
    "jobs": [{"id": "j1", "command": "pytest -q tests", "running": True,
              "exit_code": None, "chars": 4200, "elapsed": 63},
             {"id": "j2", "command": "python scripts/build_exe.py", "running": False,
              "exit_code": 1, "chars": 800, "elapsed": 12}],
    "msg": "",
}

ACTIVITY = {"ok": True, "groups": [], "timeline": {"calls": 0}, "now": None,
            "summary": {"calls": 0}, "changes": {"total": 0, "records": []},
            "empty": True, "updated_at": "15:20:09"}

STUB_JS = ("""
window.__E2E_CALLS__ = [];
window.__stubReady = true;
const CALLS = window.__E2E_CALLS__;
const METRICS = __INJ_METRICS__;
const ENV = __INJ_ENV__;
const ACTIVITY = __INJ_ACT__;
// 真 pywebview 是**页面加载完之后**才注入 window.pywebview.api,再派发 pywebviewready。
// 替身必须照这个时序来:同步就把 api 挂上,会替产品代码掩盖一个真 bug ——
// 开机那次 set_chrome 撞在未注入的桥被静默跳过(深色界面顶一条系统白标题栏,p3/p4)。
const API = {
    set_chrome: (dark) => { CALLS.push(["set_chrome", dark]);
      return Promise.resolve({ok: true, applied: ["immersive-dark-mode", "border",
        "caption", "text", "title"], failed: [], msg: ""}); },
    get_info: () => { CALLS.push(["get_info"]); return Promise.resolve({
      version: "0.9.111", app_name: "引路学长",
      permission_mode: "confirm",
      permission_modes: [{value: "confirm", label: "变更前确认"}],
      session_key: "s1", model: "neo-0", active_provider: "jiyuan",
      providers: [{id: "jiyuan", name: "基元", model: "neo-0", has_key: true},
                  {id: "zhipu", name: "智谱", model: "glm-0", has_key: true}],
      api_key_configured: true, workspace: "D:/demo/daily",
      workspace_name: "daily", workspace_is_default: false,
      memory_count: 0, memory_user_count: 0, mcp_servers: [],
      sandbox: {}, vision: false, campus_map: null, library_health: {},
      skills: [], kb_count: 0, session_metrics: METRICS
    }); },
    get_session_metrics: () => Promise.resolve(Object.assign({ok: true}, METRICS)),
    get_activity: () => Promise.resolve(ACTIVITY),
    get_env_info: () => { CALLS.push(["get_env_info"]); return Promise.resolve(ENV); },
    get_monitor_pref: () => Promise.resolve({open: false, folded: {}}),
    set_monitor_pref: () => Promise.resolve({ok: true}),
    set_session_model: (id, model) => {
      CALLS.push(["set_session_model", id, model]);
      return Promise.resolve({ok: true});
    },
    compact_now: () => Promise.resolve({ok: false, msg: "这一支不测压缩"}),
    get_suggestions: () => Promise.resolve({suggestions: []}),
    get_countdowns: () => Promise.resolve({ok: true, countdowns: []}),
    rate_message: () => Promise.resolve({ok: true}),
    send_task: () => Promise.resolve({ok: true, session: "s1"}),
    refresh_sessions: () => Promise.resolve({ok: true}),
    get_recent_projects: () => Promise.resolve({ok: true, current: {}, default: {}, recent: []}),
    get_reasoning: () => Promise.resolve({
      visible: true, disabled: false, kind: "effort", choice: "default",
      hint: "", options: [{value: "default", label: "默认"}, {value: "off", label: "关闭"},
                          {value: "low", label: "低"}, {value: "high", label: "高"}]}),
};
// 注入时点选在 `load` 之后:DOMContentLoaded 一定已经跑过,产品代码里那一处
// 「DOMContentLoaded 时报主题」在这支里必然落空 —— 剩下的那一次只能来自 boot()。
const inject = () => setTimeout(() => {
  window.pywebview = { api: API };
  window.dispatchEvent(new Event("pywebviewready"));
}, 30);
if (document.readyState === "complete") inject();
else window.addEventListener("load", inject);
""").replace("__INJ_METRICS__", json.dumps(METRICS, ensure_ascii=False)) \
   .replace("__INJ_ENV__", json.dumps(ENV, ensure_ascii=False)) \
   .replace("__INJ_ACT__", json.dumps(ACTIVITY, ensure_ascii=False))


def _serve(directory: Path):
    class _QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = partial(_QuietHandler, directory=str(directory))
    port = None
    for candidate in range(18600, 18680):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", candidate), handler)
            port = candidate
            break
        except OSError:
            continue
    assert port is not None, "18600-18679 里找不到可绑定的端口"
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

    url, shutdown = _serve(FRONTEND)
    failures: list[str] = []

    def check(cond, message):
        if not cond:
            failures.append(message)
        print(("  ✓ " if cond else "  ✗ ") + message)

    try:
        with sync_playwright() as p:
            browser = _launch(p)
            # 一个 context 里开**新文档**才是用户那句「重开窗口」:同一份本机偏好(
            # localStorage 按 origin 存),但脚本、DOM 全部从头跑一遍。用 page.reload
            # 会撞上一个 Chromium 的导航快照竞态 —— 刚写下的偏好可能还留在旧文档那一侧,
            # 新文档第一帧读到的是空值,于是"记住了却没生效"的假红。
            ctx = browser.new_context(viewport={"width": 1180, "height": 760})
            ctx.add_init_script(STUB_JS)
            page = ctx.new_page()

            def reopen():
                nonlocal page
                page.close()
                page = ctx.new_page()
                page.goto(url, wait_until="load")

            page.goto(url, wait_until="load")
            page.wait_for_function(
                "() => document.querySelectorAll('#taskModelMenu .trow').length > 10")

            # ---- 0. 开机就把系统标题栏交给桥染(p3/p4) ----
            seq = page.evaluate("() => window.__E2E_CALLS__.map(c => c[0])")
            check(seq.count("set_chrome") >= 1,
                  "开机这一次就把主题报给标题栏(替身按真 pywebview 的时序在 load 之后"
                  "才注入 api → DOMContentLoaded 那一处必然落空,这一刀只能由 boot() 补)")
            check(seq.count("set_chrome") == 1,
                  f"一次开机只染一次标题栏(实测 {seq.count('set_chrome')} 次),"
                  "不是每条事件都去敲桥")
            check("set_chrome" in seq and "get_info" in seq
                  and seq.index("set_chrome") < seq.index("get_info"),
                  f"染标题栏排在 get_info 之前({seq[:3]}):补的是 boot 开头那一刀")

            # ---- 1. 分组:供应商名只出现在组头,行里不再每行重复 ----
            heads = page.eval_on_selector_all(
                "#tModelRows .tgroup .tg-name", "els => els.map(e => e.textContent)")
            check(heads == ["基元", "智谱"], f"两个组头按出现顺序渲染({heads})")
            row_texts = page.eval_on_selector_all(
                "#tModelRows .trow .tmodel", "els => els.map(e => e.textContent)")
            check(all("基元" not in t and "智谱" not in t for t in row_texts),
                  "行里不再重复供应商名(用户:p6 太长、没有区分)")
            cnt = page.locator("#tModelCount").inner_text()
            check(cnt == "共 18 个模型 · 2 家供应商", f"计数把分组说了({cnt})")

            # ---- 2. 顶部药丸与底部选择器说同一个模型(p8) ----
            pill = page.locator("#modelPill").inner_text()
            check("neo-3" in pill, f"顶部写的是这个会话真正在用的模型({pill})")
            check("本任务已切换" in pill, "会话单独切过 → 顶部标出来,不让人猜")
            check("neo-0" not in pill, "全局默认那个名字不该出现在顶部(它不是现在用的)")
            tm = page.locator("#tmName").inner_text()
            check(tm == "neo-3", f"底部那颗与顶部同值({tm})")

            # ---- 3. 思考菜单开在自己按钮底下(p11) ----
            page.wait_for_function("() => getComputedStyle(document.getElementById('thinkBtn')).display !== 'none'")
            page.click("#thinkBtn")
            page.wait_for_selector("#thinkMenu.show")
            align = page.evaluate("""() => {
              const b = document.getElementById('thinkBtn').getBoundingClientRect();
              const m = document.getElementById('thinkMenu').getBoundingClientRect();
              return {bl: b.left, ml: m.left, bb: b.bottom, mt: m.top, br: b.right, mr: m.right};
            }""")
            check(abs(align["ml"] - align["bl"]) <= 4,
                  f"菜单左边对齐自己那颗按钮(按钮 {align['bl']:.0f} vs 菜单 {align['ml']:.0f})")
            check(align["mt"] <= align["bb"] + 12, "菜单开在按钮上方,不是飘在别处")
            page.evaluate("() => document.getElementById('thinkMenu').classList.remove('show')")

            # ---- 4. 上下文/压缩面板收窄(p10) ----
            page.click("#tmContext")
            page.wait_for_selector("#ctxPanel.show")
            box = page.evaluate("""() => {
              const r = document.getElementById('ctxPanel').getBoundingClientRect();
              const rows = [...document.querySelectorAll('#ctxPanel .ctx-row')]
                .map(e => e.getBoundingClientRect().height);
              return {w: r.width, h: r.height, vh: window.innerHeight, rows};
            }""")
            check(box["w"] <= 345, f"面板宽度收到 {box['w']:.0f}px(以前 min-width 380)")
            # 元凶是同名 class:计划卡片 `.tk{ padding:10px 13px 9px; margin:10px 0 14px }`
            # 命中了明细行的 token 栏,一行从 20px 被撑到 69px。改名 tkn 之后必须回到行高。
            check(max(box["rows"]) < 30,
                  f"明细每行没被计划卡片的样式撑开(实测 {max(box['rows']):.0f}px,"
                  f"{len(box['rows'])} 行)")
            check(box["h"] < box["vh"] * 0.45,
                  f"面板没占掉大半屏({box['h']:.0f}px / 视口 {box['vh']}px)")
            page.evaluate("() => document.getElementById('ctxPanel').classList.remove('show')")

            # ---- 5. 环境信息那一节(p9) ----
            page.click("#btnActivity")
            page.wait_for_selector(".act-sect.env-sect, #actBody")
            page.wait_for_function(
                "() => document.querySelectorAll('#actBody .env-kv').length >= 4")
            kv = page.eval_on_selector_all(
                "#actBody .env-kv",
                "els => els.map(e => e.querySelector('.env-k').textContent + '=' "
                "+ e.querySelector('.env-v').textContent)")
            joined = " | ".join(kv)
            check("分支=main" in joined, f"分支来自真 git(不是写死的):{joined[:70]}")
            check("+3 −1" in joined and "(只算了列出的那些)" in joined,
                  "行数合计带上了「不完整」的标注 —— 有未跟踪文件时不许报一个假完整的数")
            check("42 个" in joined and "另有 12 个" in joined, "未提交文件数与「另有几个」都在")
            check("v0.9.111" in joined and "安装版" in joined, "版本与形态一句话说清")
            jobs = page.eval_on_selector_all("#actBody .env-job", "els => els.length")
            check(jobs == 2, f"后台命令两条都在(在跑 / 已退)({jobs})")
            cap = page.locator("#actBody .env-cap").inner_text()
            check("重启就没了" in cap, f"后台账本的边界写在屏上({cap[:28]}…)")
            sect_head = page.locator("#actBody .act-sect").last.inner_text()
            check("环境信息" in sect_head and "+3 −1(部分)" in sect_head,
                  "段标题摘要跟着 partial 走,不报一个看着完整的数")

            # ---- 6. 提示只提醒一次(p12) ----
            page.evaluate("() => document.getElementById('actFoot').scrollIntoView()")
            check(page.locator("#actFoot").is_visible(), "监控栏那段说明第一次是看得见的")
            page.click("#actFoot .tip-x")
            check(not page.locator("#actFoot").is_visible(), "点了「知道了」这一栏里就没了")
            reopen()
            page.wait_for_function("() => document.getElementById('actFoot') !== null")
            page.click("#btnActivity")     # 监控栏默认跟着上次的收起状态:先开栏再验偏好
            page.wait_for_timeout(300)
            check(page.evaluate("() => getComputedStyle("
                                "document.getElementById('actFoot')).display") == "none",
                  "重开窗口之后它不再出现(记在本机偏好里)")
            # 首屏那条是**另一条**提示:各记各的账,关一条不许把另一条也带走
            page.wait_for_function("() => document.querySelector('#chat .tip .tip-x') !== null")
            check(page.locator("#chat .tip").count() == 1,
                  "监控栏那条点过之后,首屏「小提示」第一次仍然出现(两条独立记账)")
            page.click("#chat .tip .tip-x")
            check(page.locator("#chat .tip").count() == 0,
                  "点「知道了」首屏那条当场消失")
            check(page.evaluate("() => localStorage.getItem('gcode-hints')")
                  == "act-foot,home-tip",
                  "两条各记各的,合在同一个偏好键里(顺序按点击先后)")
            reopen()
            page.wait_for_function("() => document.getElementById('actFoot') !== null")
            left = page.evaluate("() => [...document.querySelectorAll('#chat .tip')]"
                                 ".map(e => e.querySelector('.tip-x')"
                                 " ? e.querySelector('.tip-x').dataset.hint"
                                 " : e.textContent.slice(0, 18))")
            check(not left,
                  f"两条都记住之后,重开窗口一条也不再出现(p12:别常驻挡地方)"
                  f" —— 还剩 {left}")
            page.evaluate("() => localStorage.removeItem('gcode-hints')")
            reopen()
            page.wait_for_function("() => document.querySelector('#chat .tip .tip-x') !== null")
            check(page.locator("#chat .tip").count() == 1,
                  "清掉本机偏好,提示又回来了 —— 记住的是偏好,不是把话术删了")
            # 上一步 reload 过:替身按真 pywebview 的时序在 load 之后才注入 api,
            # 下一节直接调桥,得先等它回来(不是产品代码的竞态,是这支自己的)
            page.wait_for_function("() => window.pywebview && window.pywebview.api")

            # ---- 7. 渲染预算:50 条消息一次画完不许变成秒级 ----
            msgs = []
            for i in range(25):
                msgs.append({"role": "user", "text": f"第{i}个问题,帮我看看这段说明"
                             + "很长" * 40})
                msgs.append({"role": "assistant", "text": "## 小标题\n\n"
                             + "|列|值|\n|-|-|\n|a|1|\n" * 6
                             + "\n\n正文段落。" * 30})
            t_ms = page.evaluate("""(msgs) => {
              const t0 = performance.now();
              window.__gcodeEvent({type: 'resumed', session: 's1', messages: msgs});
              return performance.now() - t0;
            }""", msgs)
            check(t_ms < 900, f"50 条消息的恢复渲染 {t_ms:.0f}ms(预算 900ms)")
            shown = page.eval_on_selector_all("#chat .msg", "els => els.length")
            check(shown >= 45, f"这一屏真的把 {shown} 条画出来了,不是空转计时")

            browser.close()
    finally:
        shutdown()

    print("\n" + ("PASS: 分组 / 口径一致 / 对齐 / 收窄 / 环境信息 / 只提醒一次 / 渲染预算"
                  if not failures else
                  "FAIL: %d 处不对\n  - %s" % (len(failures), "\n  - ".join(failures))))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
