# -*- coding: utf-8 -*-
r"""v0.9.110 前端回归:模型选择列表要能滚、第一行要选得到。

同学实测的截图:模型多的时候,弹层从任务栏往上长到屏幕外面去了 ——
最上面那几行**被窗口上沿裁掉,又没有任何滚动条**,于是那几个模型永远选不到。
根因是 `.task-menu` 只写了 `bottom:calc(100% + 6px)`(向上长),
既没有 `max-height` 也没有 `overflow-y`:内容多高就长多高,超出视口的部分点不到。

这一支钉九件事:
    新 弹层高度不超过视口(长到屏幕外 = 失败)
    新 内容超出时**必须可滚**(scrollHeight > clientHeight 且 overflow-y 是 auto)
    新 第一行完整落在视口内,点它真的发出 set_session_model(以前点不到)
    新 滚到底之后最后一行也完整可见、也点得到
    新 顶上那个搜索框:筛出 1 行、计数写「匹配 1 / 共 40 个」、筛完点的就是筛出来的
    新 筛到没有结果时照实说筛了什么(不留一个空框让人以为坏了)
    新 滚到底搜索框仍钉在弹层顶部可见(改词不用先滚回上面找框)
    新 session_metrics 推送不许把打到一半的筛词冲掉
    新 反证:只有一行时不硬撑出滚动区
跑法(项目根目录):
    .venv\Scripts\python.exe scripts\e2e_ui_v09110.py     # 需要 PYTHONIOENCODING=utf-8
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

# 40 行:够把弹层顶出 900px 的视口(每行约 31px + 提示行),重现截图里那个模型量
SELECTABLE = [{
    "id": "jiyuan", "key": "jiyuan::m%02d" % i, "name": "JIYUAN",
    "model": "model-%02d" % i, "has_key": True,
    "supports_vision": i % 3 == 0, "context_window": 262144 if i % 2 else 1048576,
} for i in range(40)]

METRICS = {
    "provider_id": "jiyuan", "selected_key": "jiyuan::m20",
    "model": "model-20", "capabilities": ["文本", "工具调用"],
    "supports_vision": False, "known": True, "context_window": 1048576,
    "context_tokens": 26300, "consumed": 0, "compact_ratio": 0.7,
    "selectable": SELECTABLE,
    "context_breakdown": {
        "window": 1048576, "used": 26300, "compact_at": 0.7,
        "source": "estimated", "estimated": 26300,
        "buckets": [
            {"key": "system", "label": "系统提示词", "tokens": 3200},
            {"key": "tools", "label": "工具定义", "tokens": 5800},
            {"key": "skills", "label": "Skill", "tokens": 900, "count": 14},
            {"key": "messages", "label": "对话消息", "tokens": 16400},
        ],
    },
}

STUB_JS = ("""
window.__E2E_CALLS__ = [];
window.__stubReady = true;
const CALLS = window.__E2E_CALLS__;
const METRICS = __INJ_METRICS__;
window.pywebview = {
  api: {
    get_info: () => Promise.resolve({
      version: "0.9.110", app_name: "引路学长",
      permission_mode: "confirm",
      permission_modes: [{value: "confirm", label: "变更前确认"}],
      session_key: "s1", model: "model-20", active_provider: "jiyuan",
      providers: [{id: "jiyuan", name: "JIYUAN", model: "model-20", has_key: true}],
      api_key_configured: true, workspace: "D:/demo/yhbz",
      workspace_name: "yhbz", workspace_is_default: false,
      memory_count: 0, memory_user_count: 0, mcp_servers: [],
      sandbox: {}, vision: false, campus_map: null, library_health: {},
      skills: [], kb_count: 0, session_metrics: METRICS
    }),
    get_session_metrics: () => Promise.resolve(Object.assign({ok: true}, METRICS)),
    // 切换模型:记下参数,前端据此点亮那一行;返回 ok 让弹层关掉
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
    get_reasoning: () => Promise.resolve({visible: false, options: [], choice: "default"}),
  }
};
""").replace("__INJ_METRICS__", json.dumps(METRICS, ensure_ascii=False))


def _serve(directory: Path):
    class _QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = partial(_QuietHandler, directory=str(directory))
    port = None
    for candidate in range(18520, 18600):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", candidate), handler)
            port = candidate
            break
        except OSError:
            continue
    assert port is not None, "18520-18599 里找不到可绑定的端口"
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
            page = browser.new_page(viewport={"width": 1180, "height": 760})
            page.add_init_script(STUB_JS)
            page.goto(url, wait_until="load")
            page.wait_for_function(
                "() => document.getElementById('taskModelMenu') !== null"
                " && document.querySelectorAll('#taskModelMenu .trow').length > 30")

            page.click("#taskModelBtn")
            page.wait_for_selector("#taskModelMenu.show")

            # ---- 1. 弹层不许长到视口外面(截图里就是这一条坏了) ----
            box = page.evaluate("""() => {
              const m = document.getElementById('taskModelMenu');
              const r = m.getBoundingClientRect();
              return {top: r.top, bottom: r.bottom, h: r.height,
                      sh: m.scrollHeight, ch: m.clientHeight,
                      oy: getComputedStyle(m).overflowY,
                      vh: window.innerHeight};
            }""")
            check(box["top"] >= 0,
                  f"弹层顶边在窗口内(top={box['top']:.0f},视口高 {box['vh']}),"
                  "不再被上沿裁掉")
            check(box["bottom"] <= box["vh"] + 1,
                  f"弹层底边不出视口(bottom={box['bottom']:.0f})")
            check(box["sh"] > box["ch"],
                  f"40 行内容确实超出可视高度(scrollHeight {box['sh']} > clientHeight {box['ch']})")
            check(box["oy"] in ("auto", "scroll"),
                  f"超出部分给了滚动条(overflow-y: {box['oy']})")

            # ---- 2. 第一行完整可见,而且点得到 —— 这就是「最上面的模型选不到」 ----
            first = page.evaluate("""() => {
              const row = document.querySelector('#taskModelMenu .trow');
              const r = row.getBoundingClientRect();
              return {top: r.top, bottom: r.bottom, model: row.querySelector('.tmodel').textContent};
            }""")
            check(first["top"] >= 0, f"第一行完整在弹层里(top={first['top']:.0f}:{first['model']})")
            page.locator("#taskModelMenu .trow").first.click()
            page.wait_for_function(
                "() => window.__E2E_CALLS__.some(c => c[0] === 'set_session_model')")
            called = page.evaluate("() => window.__E2E_CALLS__[0]")
            check(called[1] == "jiyuan" and called[2] == first["model"],
                  f"点第一行真的切到了那个模型({called[2]})")

            # ---- 3. 滚到底,最后一行同样可见、同样点得到 ----
            page.click("#taskModelBtn")
            page.wait_for_selector("#taskModelMenu.show")
            page.evaluate("() => { const m = document.getElementById('taskModelMenu');"
                          " m.scrollTop = m.scrollHeight; }")
            last = page.evaluate("""() => {
              const rows = document.querySelectorAll('#taskModelMenu .trow');
              const r = rows[rows.length - 1].getBoundingClientRect();
              const mr = document.getElementById('taskModelMenu').getBoundingClientRect();
              return {top: r.top, bottom: r.bottom, model: rows[rows.length - 1]
                        .querySelector('.tmodel').textContent, menuBottom: mr.bottom};
            }""")
            check(last["top"] >= 0 and last["bottom"] <= last["menuBottom"] + 1,
                  f"滚到底后最后一行在弹层内可见({last['model']},"
                  f"top={last['top']:.0f})")
            page.locator("#taskModelMenu .trow").last.click()
            calls = page.evaluate("() => window.__E2E_CALLS__.map(c => c[2])")
            check(calls[-1] == last["model"], f"滚到底也点得到最后一行({calls[-1]})")
            check(len(calls) == 2, f"两次点击各发一次请求,没有重复({calls})")

            # ---- 4. 搜索框:四十行里找一个,不用来回拖 ----
            page.click("#taskModelBtn")
            page.wait_for_selector("#taskModelMenu.show")
            page.fill("#tModelFilter", "model-07")
            page.wait_for_function(
                "() => document.querySelectorAll('#tModelRows .trow').length === 1")
            cnt = page.locator("#tModelCount").inner_text()
            check(cnt == "匹配 1 / 共 40 个", f"计数写清「匹配几 / 共几个」({cnt})")
            page.locator("#tModelRows .trow").first.click()
            page.wait_for_function("() => window.__E2E_CALLS__.length === 3")
            called = page.evaluate("() => window.__E2E_CALLS__[2]")
            check(called[2] == "model-07", f"筛完点的就是筛出来的那个({called[2]})")

            # 筛到没结果:要说清筛了什么,别留一个空框
            page.click("#taskModelBtn")
            page.wait_for_selector("#taskModelMenu.show")
            page.fill("#tModelFilter", "zzz不存在的模型")
            page.wait_for_function(
                "() => document.querySelectorAll('#tModelRows .trow').length === 0")
            empty = page.locator("#tModelRows .tempty").inner_text()
            check("zzz不存在的模型" in empty, f"筛空了照实说筛了什么({empty[:26]}…)")

            # 滚到底时搜索框还在(钉在顶上):否则改个词要先滚回上面找框
            page.fill("#tModelFilter", "")
            page.wait_for_function(
                "() => document.querySelectorAll('#tModelRows .trow').length === 40")
            page.evaluate("() => { const m = document.getElementById('taskModelMenu');"
                          " m.scrollTop = m.scrollHeight; }")
            pinned = page.evaluate("""() => {
              const m = document.getElementById('taskModelMenu').getBoundingClientRect();
              const f = document.getElementById('tModelFilter').getBoundingClientRect();
              return {scrollTop: document.getElementById('taskModelMenu').scrollTop,
                      top: f.top, menuTop: m.top, vh: window.innerHeight};
            }""")
            check(pinned["scrollTop"] > 0 and pinned["top"] >= pinned["menuTop"] - 1
                  and pinned["top"] >= 0,
                  f"滚到底({pinned['scrollTop']}px)后搜索框仍钉在弹层顶部可见"
                  f"(top={pinned['top']:.0f},弹层顶 {pinned['menuTop']:.0f})")

            # 下一次 session_metrics 推送不许把打到一半的字冲掉
            page.fill("#tModelFilter", "flash")
            page.evaluate("() => window.__gcodeEvent({type: 'session_metrics', session: 's1'})")
            page.wait_for_function(
                "() => (document.getElementById('tModelFilter') || {}).value === 'flash'")
            check(True, "metrics 推送之后筛过的字还在(不是一推送就清空)")

            # ---- 5. 反证:短列表不该凭空长出滚动条 ----
            page.evaluate("""() => {
              window.__gcodeEvent({type: 'session_metrics', session: 's1',
                model: 'model-00', selected_key: 'jiyuan::m00', context_window: 1048576,
                context_tokens: 1000, compact_ratio: 0.7,
                selectable: [{id:'jiyuan', key:'jiyuan::m00', name:'JIYUAN',
                              model:'model-00', has_key:true, supports_vision:false,
                              context_window:1048576}],
                context_breakdown: {window:1048576, used:1000, compact_at:0.7,
                  source:'estimated', estimated:1000, buckets:[]}});
            }""")
            page.wait_for_function(
                "() => document.querySelectorAll('#taskModelMenu .trow').length <= 1")
            page.fill("#tModelFilter", "")   # 上一节筛的词还在(flash 匹配不到 model-00)
            page.wait_for_function(
                "() => document.querySelectorAll('#tModelRows .trow').length === 1")
            # 弹层此时是开着的(上面 fill 没关它);盲目再点一次按钮会把它 toggle 回去
            if not page.evaluate("() => document.getElementById('taskModelMenu')"
                                 ".classList.contains('show')"):
                page.click("#taskModelBtn")
            page.wait_for_selector("#taskModelMenu.show")
            short = page.evaluate("""() => {
              const m = document.getElementById('taskModelMenu');
              const row = m.querySelector('.trow').getBoundingClientRect();
              return {sh: m.scrollHeight, ch: m.clientHeight, rowH: row.height};
            }""")
            check(short["rowH"] > 10 and short["sh"] <= short["ch"] + 1,
                  f"只有一行时不硬撑出滚动区(内容 {short['sh']} <= 可视 {short['ch']},"
                  f"行高 {short['rowH']:.0f})")

            browser.close()
    finally:
        shutdown()

    print("\n" + ("PASS: 模型列表能滚、首尾都点得到" if not failures
                  else "FAIL: %d 处不对\n  - %s" % (len(failures), "\n  - ".join(failures))))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
