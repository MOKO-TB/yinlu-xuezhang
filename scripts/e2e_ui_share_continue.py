# -*- coding: utf-8 -*-
"""前端回归:首页「接着做」那一行 + 每条回答上的 🖼 分享图。

两件事都遵守同一条老规矩:**后端没给东西就整行/整颗不出**,前端不摆空壳、不改写
后端给的文字。所以本场除了"看得见点得动",还专门验空数据的样子:
`continue: []` 时首页不该出现「接着做：」这半句话。

钉这些:
1. 「接着做」行的文案是后端原样(前端不重排、不加"继续加油"之类的安慰);
2. 点它是把 `ask` 填进输入框并聚焦,**不自动发送** —— 发不发是用户的事;
3. 逾期/今天到期那枚带 urgent 描边,普通计划步骤不带;
4. 后端 `continue` 为空 → 整行不出现;
5. 答完一条 → 消息上有 🖼;点一次叫一次 `share_image`,toast 显示后端那句 msg;
6. 连点只发一次(排卡片要几百毫秒,连点会在 images/ 落好几张);
7. 桥回 ok:false 时,toast 里是**那句原因原文**(如"没找到中文字体"),不是"生成失败"。

跑法(项目根目录):
    PYTHONIOENCODING=utf-8 .venv\\Scripts\\python.exe scripts\\e2e_ui_share_continue.py
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

CONTINUE = [
    {"kind": "due", "label": "已逾期 2 天：实验报告",
     "ask": "逾期的「实验报告」现在怎么处理，还有什么能补救",
     "note": "截止 2026-09-24", "urgent": True},
    {"kind": "plan", "label": "接着做：第 2 步「生成幻灯片」",
     "ask": "接着做计划的第 2 步「生成幻灯片」",
     "note": "计划里标着「进行中」的那一步", "urgent": False},
]

STUB_JS = """
window.__SHARE_CALLS__ = [];
window.__SEND_CALLS__ = [];
window.__SHARE_RESULT__ = {ok:true, path:"images/share-绩点.png", bytes: 48210,
                           truncated:false, sources:["https://gzasc.edu.cn/jwgl"],
                           msg:"已生成分享图：images/share-绩点.png（48,210 字节；来源 1 条）"};
window.__CONTINUE__ = @@CONTINUE@@;
const base = {
  get_info: () => Promise.resolve({
    version: "0.9.41", app_name: "引路学长",
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
  get_suggestions: () => Promise.resolve({suggestions: ["四六级怎么备考"],
                                          continue: window.__CONTINUE__}),
  get_countdowns: () => Promise.resolve({ok: true, countdowns: []}),
  get_weather_card: () => Promise.resolve({ok: false}),
  send_task: (t) => { window.__SEND_CALLS__.push(t); return Promise.resolve({ok: true}); },
  share_image: (t) => { window.__SHARE_CALLS__.push(t);
                        if ((window.__SHARE_DELAY__ || 0) > 0)
                          return new Promise(res => setTimeout(
                            () => res(window.__SHARE_RESULT__), window.__SHARE_DELAY__));
                        return Promise.resolve(window.__SHARE_RESULT__); },
};
window.pywebview = {api: new Proxy(base, {
  get: (t, k) => (k in t) ? t[k] : ((...a) => Promise.resolve({ok: true}))
})};
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

    url, shutdown = _serve(FRONTEND)
    failures: list[str] = []
    errors: list[str] = []

    def check(cond, message):
        if not cond:
            failures.append(message)
        print(("  ok   " if cond else "  FAIL ") + message)

    def stub_for(cont):
        return STUB_JS.replace("@@CONTINUE@@", json.dumps(cont, ensure_ascii=False))

    with sync_playwright() as p:
        browser = _launch(p)

        def open_page(cont):
            page = browser.new_page(viewport={"width": 1280, "height": 860})
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            page.add_init_script(stub_for(cont))
            page.goto(url)
            page.wait_for_timeout(1100)
            return page

        # ---- 1~3 有真状态时
        page = open_page(CONTINUE)
        chips = page.locator(".go-chips .chip-go")
        check(chips.count() == 2, f"① 「接着做」行有两枚(实得 {chips.count()})")
        check(page.text_content(".go-chips .chip-go") == "已逾期 2 天：实验报告",
              "① 文案是后端原样,前端没重排")
        check("接着做：" in page.text_content(".go-chips"),
              "① 行头写的是「接着做」,不是「试试这些」")
        first_cls = page.get_attribute(".go-chips .chip-go", "class")
        second_cls = page.evaluate(
            "() => document.querySelectorAll('.go-chips .chip-go')[1].className")
        check("urgent" in first_cls and "urgent" not in second_cls,
              f"③ 逾期的标紧急、计划步骤不标:{first_cls} / {second_cls}")

        # ---- 2) 点它填 ask 且不自动发
        page.click(".go-chips .chip-go")
        page.wait_for_timeout(220)
        filled = page.evaluate("() => document.getElementById('input').value")
        check("逾期的「实验报告」" in filled,
              f"② 填进去的是 ask 那句:{filled[:36]}")
        check(page.evaluate("window.__SEND_CALLS__") == [],
              "② 一次都没自动发送")

        # ---- 5~7 🖼 分享图
        page.evaluate("t => window.__gcodeEvent({type:'text_delta', text: t})",
                      "绩点按必修口径算，来源 https://gzasc.edu.cn/jwgl")
        page.evaluate("() => window.__gcodeEvent({type:'finish_stream'})")
        page.wait_for_timeout(300)
        last = page.locator(".msg.assistant").last
        last.hover()
        check(last.locator(".shr").count() == 1, "④ 答完的那条上有 🖼")
        last.locator(".shr").click()
        page.wait_for_timeout(320)
        calls = page.evaluate("window.__SHARE_CALLS__")
        check(len(calls) == 1 and "绩点按必修口径算" in calls[0],
              f"⑤ 点一次发一次,发的是这条回答原文:{str(calls)[:60]}")
        check(last.locator(".shr.pending").count() == 0,
              "⑤ 完成后 pending 已摘掉(卡住了就再也点不动)")

        # ---- 6) 请求还飞在半路时连点,只发一次
        # 桩默认即时回(所以第 5 步之后 pending 早解了);这里把回话拖到 600 ms,
        # 让第二次点击真的落在"上一张还在排"的时间里 —— 否则这条断言是空的。
        page.evaluate("() => { window.__SHARE_CALLS__ = []; window.__SHARE_DELAY__ = 600; }")
        box = last.locator(".shr")
        box.hover()
        box.click()
        page.wait_for_timeout(80)
        check(last.locator(".shr.pending").count() == 1,
              "⑥ 请求在半路时这颗按钮是 pending 态")
        box.click(force=True)
        page.wait_for_timeout(120)
        check(len(page.evaluate("window.__SHARE_CALLS__")) == 1,
              "⑥ pending 期间再点一次不会重复发(否则 images/ 里会落好几张)")
        page.wait_for_timeout(700)
        check(last.locator(".shr.pending").count() == 0,
              "⑥ 回来后 pending 摘掉,还能再分享")
        page.evaluate("() => { window.__SHARE_DELAY__ = 0; }")

        # ---- 7) 失败时把原因原文显示出来
        page.evaluate("""() => { window.__SHARE_RESULT__ = {ok:false,
                          msg:"找到字体但读不动:[WinError 5] 拒绝访问"}; }""")
        last.hover()
        last.locator(".shr").click()
        page.wait_for_timeout(280)
        toasts = page.evaluate("() => document.body.innerText")
        check("找到字体但读不动" in toasts,
              "⑦ 失败提示用的是后端那句原因原文,不是笼统的「生成失败」")
        page.close()

        # ---- 4) 后端说没在做的事 → 整行不出现
        page = open_page([])
        check(page.locator(".go-chips").count() == 0,
              "④ 后端 continue 为空时整行不渲染(不摆一个空壳行头)")
        check("接着做" not in page.text_content(".placeholder"),
              "④ 行头那半句话也不留下")
        page.close()

        browser.close()

    real_errors = [e for e in errors if "favicon" not in e]
    check(not real_errors, f"无 pageerror:{real_errors[:2]}")
    shutdown()
    print()
    if failures:
        for f in failures:
            print("FAIL:", f)
        print(f"\n{len(failures)} 项不通过")
        return 1
    print("PASS: 接着做那行只说真在推进的事、填空不发、空数据整行不出;🖼 一次一请求且失败报原文")
    return 0


if __name__ == "__main__":
    sys.exit(main())
