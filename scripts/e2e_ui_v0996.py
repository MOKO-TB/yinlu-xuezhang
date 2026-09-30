# -*- coding: utf-8 -*-
"""v0.9.96 前端回归:动手前问一句的那张卡(`#askOverlay`)。

用户给的截图里有六个部件:抬头「需要输入」、一句规矩、问题、带理由的推荐、
「标题 + 一行说明」的选项、分页与「发送回复」。这一场断的是**它们都接真数据**,
而不是画了个样子:

1. 卡片上的每一个字都出自后端 `asking.normalize` 归一化后的原文(前端不许润色);
2. 点哪个就回传哪个的 label,原样、不翻译、不改写;再点一次可以取消(不逼用户选);
3. 多选题能同时留两项,顺序按用户点的次序;
4. 「自己说一句」的文字进 `free`,不会被归到某个选项上;
5. 「按推荐继续」回的是 `recommend`,与逐题作答是两种不同的事实;
6. 答完(或压根没等到答)卡片会收,聊天区留下一行痕,输入框占位还原;
7. 整场点完控制台一条报错都没有。

载荷都出自**真**代码:`questions` 走真 `asking.normalize`,聊天区那句痕走真
`Bridge._ask_trace_text`(只借那一个方法,不另抄一份文案)。

跑法(项目根目录):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/e2e_ui_v0996.py
"""
from __future__ import annotations

import json
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # 判据里有 ✓

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
for p in (str(ROOT), str(ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

from e2e_launch import launch          # noqa: E402  起浏览器这件事的一份真相

RAW = [
    {"q": "宣传片用哪种模式做?", "header": "制作模式",
     "why": "我的推荐是「模板模式」:项目自带面板很适合逐镜头展示,约 36s / 10 镜头。"
            "想要贴合产品本身的视觉,选自主创作(更久)。",
     "options": [{"label": "模板(推荐)", "note": "真实网页版截图 + 学长文案,最快出片"},
                 {"label": "自主自由创作", "note": "自定视觉方向与配乐,耗时更长"},
                 {"label": "共同创作", "note": "每个节点停下来等确认,来回最多"}]},
    {"q": "哪几件事要放进片子?", "multi": True,
     "why": "只放真做过的功能,别放打算做的。",
     "options": [{"label": "任务监控栏", "note": "时间线与实时表"},
                 {"label": "🩺 体检面板", "note": "每一行都由一次真调用撑起"},
                 {"label": "子代理并行", "note": "最多 6 路"}]},
]

STUB_JS = """
window.__CALLS__ = [];
const base = {
  get_info: () => Promise.resolve({
    version: "0.9.96", app_name: "引路学长", permission_mode: "confirm",
    permission_modes: [{value: "confirm", label: "变更前确认"}],
    model: "test-model", active_provider: "test", workspace: "D:/demo/campus",
    workspace_name: "campus", workspace_is_default: true,
    providers: [{id: "test", name: "Test", model: "test-model", has_key: true}],
    api_key_configured: true, memory_count: 0, memory_user_count: 0,
    mcp_servers: [], skills: [], kb_count: 0, sandbox: null, campus_map: null,
    library_health: {}, paths: {app_dir: "D:/demo", writable: true},
    session_metrics: {ok: false, selectable: []}
  }),
  get_monitor_pref: () => Promise.resolve({ok: true, open: false, folded: {}}),
  set_monitor_pref: () => Promise.resolve({ok: true, open: false, folded: {}}),
  refresh_sessions: () => Promise.resolve({ok: true}),
  get_recent_projects: () => Promise.resolve({ok: true, current: {}, default: {}, recent: []}),
  get_reasoning: () => Promise.resolve({visible: false, options: [], choice: "default"}),
  get_suggestions: () => Promise.resolve({suggestions: []}),
  get_slash_commands: () => Promise.resolve({ok: true, commands: []}),
  get_countdowns: () => Promise.resolve({ok: true, countdowns: []}),
  get_plan: () => Promise.resolve({ok: true, has_plan: false, steps: [], total: 0,
                                   done: 0, ready: [], blocked: [], failed: [],
                                   running: [], dispatch_text: ""}),
  get_session_metrics: () => Promise.resolve({ok: false}),
  read_clipboard: () => Promise.resolve({text: "", image: false, source: "empty"}),
};
window.pywebview = {api: new Proxy(base, {
  get: (t, k) => (k in t) ? t[k]
    : ((...a) => { window.__CALLS__.push([k, ...a]); return Promise.resolve({ok: true}); })
})};
"""


def _ask_calls(page):
    return [c for c in page.evaluate("window.__CALLS__") if c and c[0] == "ask_reply"]


def _serve(directory: Path):
    class _QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = partial(_QuietHandler, directory=str(directory))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}/index.html", httpd.shutdown


def _trace_text(questions, mode, answers):
    """聊天区那一行痕:借真 `Bridge._ask_trace_text`,不在这儿再抄一份文案。"""
    from minicode.gui_bridge import Bridge

    class _B:
        _ask_questions = questions
        _ask_trace_text = Bridge._ask_trace_text

    return _B()._ask_trace_text(mode, answers)


def main() -> int:
    from playwright.sync_api import sync_playwright

    from minicode import asking
    qs, err = asking.normalize(RAW)
    assert not err and len(qs) == 2, err          # 载荷必须是真归一化过的形状
    url, shutdown = _serve(FRONTEND)
    failures: list[str] = []

    def check(cond, message):
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

        placeholder_before = page.get_attribute("#input", "placeholder") or ""
        page.evaluate("(q) => window.__gcodeEvent({type:'ask_request',"
                      " questions: q, note: '这三件定了我才好排步骤',"
                      " label: '第 1 步之前'})", qs)
        page.wait_for_selector("#askOverlay.show", timeout=5000)

        # ---- 1. 抬头与规矩那句在(必须说清"不答会怎样",否则像被锁住) ----
        head = page.inner_text(".ask-head")
        check("需要输入" in head, "抬头写着「需要输入」")
        check("按推荐继续" in head or "自己说" in head,
              "规矩那句告诉用户有不逐题选的出口")
        check("这三件定了我才好排步骤" in page.inner_text("#askNote"),
              "模型写的补充原样出现在卡上")

        # ---- 2. 问题/推荐/选项都是后端原文 ----
        body = page.inner_text("#askBody")
        check(qs[0]["q"] in body and qs[0]["options"][0]["label"] in body,
              "问题与选项 label 是后端原文(前端不重写一遍)")
        check("约 36s / 10 镜头" in body, "推荐理由那句在(推荐要带理由与代价)")
        check("真实网页版截图" in body, "每个选项的那行说明在(只有标题等于没问)")
        check("【制作模式】" in body, "短标 header 摆在问题前面")
        check(page.locator("#askBody .ask-opt").count() == 3,
              f"第一题三个选项(实测 {page.locator('#askBody .ask-opt').count()})")

        # ---- 3. 分页:一题一屏,第一屏没有「上一个」 ----
        check(page.inner_text("#askPage").strip() == "1 / 2", "分页写的是 1 / 2")
        check(not page.is_visible("#askPrev"), "第一屏不摆「上一个」")
        check(page.is_visible("#askNext"), "还有下一题时「下一个」可点")

        # ---- 4. 点选:点哪个选哪个,再点一次取消 ----
        opts = page.locator("#askBody .ask-opt")
        opts.nth(1).click()
        check(page.locator("#askBody .ask-opt.on").count() == 1,
              "点一项就只选中那一项")
        check("自主自由创作" in page.locator("#askBody .ask-opt.on").inner_text(),
              "选中的正是被点的那一项")
        opts.nth(1).click()
        check(page.locator("#askBody .ask-opt.on").count() == 0,
              "再点一次取消 —— 不选是合法答案,界面不逼用户选一个")
        opts.nth(0).click()

        # ---- 5. 自己补一句 ----
        page.fill("#askBody .ask-free", "预算只有 800 块")

        # ---- 6. 翻到第二题:多选留两项 ----
        page.click("#askNext")
        check(page.inner_text("#askPage").strip() == "2 / 2", "翻页后分页是 2 / 2")
        check("(可多选)" in page.inner_text("#askBody"), "多选题在题面写明")
        q2 = page.locator("#askBody .ask-opt")
        q2.nth(2).click()
        q2.nth(0).click()
        on = page.locator("#askBody .ask-opt.on")
        check(on.count() == 2, "多选能同时留两项")
        check("任务监控栏" in on.nth(0).inner_text(),
              "卡片上选项仍按后端顺序摆(用户点选的先后只体现在回传里,不重排界面)")

        # ---- 7. 发送回复:回传的是 label 原话 ----
        page.click("#askSend")
        page.wait_for_function("() => window.__CALLS__.some(c => c[0] === 'ask_reply')",
                               timeout=5000)
        payload = json.loads(_ask_calls(page)[-1][1])
        check(payload["mode"] == "answer", "发送回复走的是 answer 这条出口")
        first = payload["answers"][0]
        check(first["id"] == 0 and first["choice"] == ["模板(推荐)"],
              f"第一题回传原样 label(实测 {first})")
        check(first["free"] == "预算只有 800 块", "自己补的那句进 free,不并进选项")
        second = payload["answers"][1]
        check(second["choice"] == ["子代理并行", "任务监控栏"],
              f"多选题回传两项(实测 {second})")
        check(page.locator("#askOverlay.show").count() == 0,
              "发完卡片就收(不留着让人以为还没提交)")

        # ---- 8. 后端推痕:聊天区留一行,输入框占位还原 ----
        trace = _trace_text(qs, "answer", payload["answers"])
        page.evaluate("(t) => window.__gcodeEvent({type:'ask_trace', text: t})", trace)
        check(page.locator(".ask-trace").count() == 1, "聊天区留下一行痕(可回看)")
        check("模板(推荐)" in page.inner_text(".ask-trace")
              and "预算只有 800 块" in page.inner_text(".ask-trace"),
              "那一行写的就是用户答的内容")
        check((page.get_attribute("#input", "placeholder") or "") == placeholder_before,
              "输入框占位还原(不留一句挂在屏幕上的假提示)")

        # ---- 9. 第二张卡:按推荐继续是另一种事实 ----
        page.evaluate("(q) => window.__gcodeEvent({type:'ask_request', questions: q,"
                      " note: '', label: ''})", qs[:1])
        page.wait_for_selector("#askOverlay.show", timeout=5000)
        page.click("#askRecommend")
        page.wait_for_function(
            "() => window.__CALLS__.filter(c => c[0] === 'ask_reply').length >= 2",
            timeout=5000)
        check(json.loads(_ask_calls(page)[-1][1])["mode"] == "recommend",
              "「按推荐继续」回的是 recommend,不与逐题作答混成一种")
        page.evaluate("(t) => window.__gcodeEvent({type:'ask_trace', text: t})",
                      _trace_text(qs[:1], "recommend", []))
        check("按推荐继续" in page.locator(".ask-trace").nth(1).inner_text(),
              "痕里写明是他点的「按推荐」(不是替他确认了每一题)")

        # ---- 10. 压根没等到回答:卡片要收,痕要说"没等到" ----
        page.evaluate("(q) => window.__gcodeEvent({type:'ask_request', questions: q,"
                      " note: '', label: ''})", qs[:1])
        page.wait_for_selector("#askOverlay.show", timeout=5000)
        page.evaluate("() => window.__gcodeEvent({type:'ask_trace',"
                      " text: '没等到回答,这一题按未回答处理'})")
        check(page.locator("#askOverlay.show").count() == 0,
              "没等到回答时卡片也会收(不能挂着等一个不会来的人)")
        check("没等到" in page.locator(".ask-trace").last.inner_text(),
              "痕里照实写没等到,不写成「用户选了推荐」")

        check(not errors, f"整场点完,控制台没有一条报错:{errors[:3]}")
        browser.close()
    shutdown()

    if failures:
        print(f"\nFAIL: {len(failures)} 项不对")
        for f in failures:
            print(" - " + f)
        return 1
    print("\nPASS: 提问卡的每个字都出自后端,答什么回什么,没答就说没答")
    return 0


if __name__ == "__main__":
    sys.exit(main())
