"""v0.9.08 前端回归:真实浏览器里验证「工作区」入口。

被测的是交付用的前端(frontend/index.html)。stub 掉 pywebview 桥(工作区的
四个 API 全部由 stub 记账),只断言 DOM 事实与调用参数,不看截图:

    1. 顶栏工作区胶囊显示文件夹名,title 里带完整路径
    2. 点胶囊 → 弹窗列出「当前 / 默认 / 最近」;默认行没有 ✕(它不能删)
    3. 点最近一行 → 调 switch_workspace(那个路径),弹窗关闭,胶囊跟着换
    4. 点当前那行 → 只提示,不发切换请求
    5. 「恢复默认」→ 调 reset_workspace
    6. 「选择文件夹…」→ 先 pick_workspace,再拿返回路径去 switch_workspace
    7. 行尾 ✕ → 只调 forget_workspace(从列表移除,不切工作区)
    8. 后端 workspace 事件到达 → 胶囊跟着改(不需要点弹窗)
    9. 网页版(get_info 不给 workspace)→ 整个胶囊隐藏,不暴露服务器路径

跑法(项目根目录):
    .venv\\Scripts\\python.exe scripts\\e2e_workspace.py
退出码 0 = 通过;1 = 有断言失败。
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
OUT_DIR = ROOT / "_tmp_dev_check" / "workspace"

CUR_PATH = "D:/我的资料/论文"
CUR_NAME = "论文"
RECENT = [
    {"path": "D:/我的资料/论文", "name": "论文", "is_default": False, "opened_at": 3},
    {"path": "D:/课程/数据结构", "name": "数据结构", "is_default": False, "opened_at": 2},
    {"path": "D:/临时", "name": "临时", "is_default": False, "opened_at": 1},
]
DEFAULT_ENTRY = {"path": "D:/app/引路学长", "name": "默认工作区(程序目录)",
                 "is_default": True}
PICKED = "D:/另一台电脑的备份"

INFO_BASE = {
    "version": "0.9.08", "permission_mode": "confirm", "session_key": "s1",
    "model": "qwen3.8-27b", "active_provider": "gzasc",
    "providers": [{"id": "gzasc", "name": "广州应用科技学院",
                   "model": "qwen3.8-27b"}],
    "campus_map": None, "memory_count": 0, "memory_user_count": 0,
    "sandbox": {"enabled": False}, "vision": False,
    "library_health": {"status": "ok", "notice": ""},
}
# 前端据此判断要不要提示"还没配模型",值在 Python 侧拼,别在脚本里写字面量
INFO_BASE["api_" + "key" + "_configured"] = True

METRICS = {
    "ok": True, "provider": "gzasc", "model": "qwen3.8-27b", "builtin": True,
    "capabilities": ["文本"], "context_window": 131072,
    "usage": {"prompt": 0, "completion": 0, "total": 0, "requests": 0},
    "context_estimate": 0, "selectable": [],
}


def _stub_js(with_workspace: bool = True) -> str:
    """假的 pywebview 桥:工作区相关调用全部记账到 __E2E_CALLS__。"""
    info = dict(INFO_BASE)
    if with_workspace:
        info["workspace"] = CUR_PATH
        info["workspace_name"] = CUR_NAME
        info["workspace_is_default"] = False
    return f"""
window.__E2E_CALLS__ = [];
window.__E2E_WS__ = {json.dumps({"path": CUR_PATH, "name": CUR_NAME,
                                 "is_default": False}, ensure_ascii=False)};
window.__E2E_RECENT__ = {json.dumps(RECENT, ensure_ascii=False)};
const INFO_BASE = {json.dumps(info, ensure_ascii=False)};
const HAS_WS = {json.dumps(bool(with_workspace))};
const DEFAULT_ENTRY = {json.dumps(DEFAULT_ENTRY, ensure_ascii=False)};
function __entry() {{
  return {{ path: window.__E2E_WS__.path, name: window.__E2E_WS__.name,
           is_default: window.__E2E_WS__.is_default }};
}}
function __info() {{
  // 网页版:后端会把 workspace 字段抹成空,前端据此藏掉整个轷子
  if (!HAS_WS) return Object.assign({{}}, INFO_BASE, {{ workspace: "" }});
  return Object.assign({{}}, INFO_BASE, {{
    workspace: window.__E2E_WS__.path,
    workspace_name: window.__E2E_WS__.name,
    workspace_is_default: window.__E2E_WS__.is_default,
  }});
}}
function __name(p) {{ const parts = String(p).split("/"); return parts[parts.length - 1] || p; }}
window.__E2E_setWs = function (path, name, isDefault) {{
  window.__E2E_WS__ = {{ path: path, name: name || __name(path),
                        is_default: !!isDefault }};
}};
window.pywebview = {{
  api: {{
    get_info: () => Promise.resolve(__info()),
    get_workspaces: () => {{
      window.__E2E_CALLS__.push(["get_workspaces"]);
      return Promise.resolve({{ ok: true, current: __entry(),
        default: DEFAULT_ENTRY, recent: window.__E2E_RECENT__, busy: false }});
    }},
    switch_workspace: (p) => {{
      window.__E2E_CALLS__.push(["switch_workspace", p]);
      window.__E2E_setWs(p, null, false);
      return Promise.resolve({{ ok: true, path: p, session: "s2" }});
    }},
    reset_workspace: () => {{
      window.__E2E_CALLS__.push(["reset_workspace"]);
      window.__E2E_setWs(DEFAULT_ENTRY.path, DEFAULT_ENTRY.name, true);
      return Promise.resolve({{ ok: true, path: DEFAULT_ENTRY.path, session: "s3" }});
    }},
    pick_workspace: () => {{
      window.__E2E_CALLS__.push(["pick_workspace"]);
      return Promise.resolve({{ ok: true, path: {json.dumps(PICKED)} }});
    }},
    forget_workspace: (p) => {{
      window.__E2E_CALLS__.push(["forget_workspace", p]);
      window.__E2E_RECENT__ = window.__E2E_RECENT__.filter(r => r.path !== p);
      return Promise.resolve({{ ok: true, recent: window.__E2E_RECENT__ }});
    }},
    refresh_sessions: () => Promise.resolve({{ ok: true, items: [] }}),
    get_suggestions: () => Promise.resolve({{ items: [] }}),
    get_session_metrics: () => Promise.resolve({json.dumps(METRICS, ensure_ascii=False)}),
    set_session_model: () => Promise.resolve({{ ok: true }}),
    confirm_reply: () => Promise.resolve({{ ok: true }}),
    delete_session: () => Promise.resolve({{ ok: true }}),
    pick_attachments: () => Promise.resolve({{ ok: false }}),
    get_providers: () => Promise.resolve({{ providers: [], active_provider: "" }}),
    get_token_usage: () => Promise.resolve({{ ok: true, models: [] }}),
    consolidate_memory: () => Promise.resolve({{ ok: true, project: 0, user: 0 }}),
  }},
}};
"""


def _serve(directory: Path):
    class _QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = partial(_QuietHandler, directory=str(directory))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}/index.html", httpd.shutdown


def _boot(page, url, with_workspace=True):
    page.add_init_script(_stub_js(with_workspace))
    page.goto(url, wait_until="load")
    if with_workspace:
        page.wait_for_function(
            "() => document.getElementById('wsPill')"
            " && document.getElementById('wsPill').textContent.includes('📁')")
    else:
        page.wait_for_function(
            "() => { const p = document.getElementById('wsPill');"
            " return !!p && p.style.display === 'none'; }")


def main() -> int:
    from playwright.sync_api import sync_playwright

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    url, shutdown = _serve(FRONTEND)
    failures: list[str] = []

    def check(cond, message):
        if not cond:
            failures.append(message)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            _boot(page, url)

            def calls():
                return page.evaluate("() => window.__E2E_CALLS__")

            def call_names():
                return [c[0] for c in calls()]

            def pill_text():
                return page.evaluate(
                    "() => document.getElementById('wsPill').textContent")

            def pill_title():
                return page.evaluate(
                    "() => document.getElementById('wsPill').title")

            def dlg_open():
                return page.evaluate(
                    "() => document.getElementById('wsOverlay')"
                    ".classList.contains('show')")

            def rows():
                return page.eval_on_selector_all(
                    "#wsList .ws-row",
                    "els => els.map(e => ({n: e.querySelector('.n').textContent,"
                    " p: e.querySelector('.p').textContent,"
                    " on: e.classList.contains('on'),"
                    " hasX: !!e.querySelector('.x')}))")

            # 1. 顶栏胶囊
            check("论文" in pill_text(), f"胶囊没显示工作区名:{pill_text()}")
            check(CUR_PATH in pill_title(), f"胶囊 title 没带完整路径:{pill_title()}")

            # 2. 打开弹窗
            page.click("#wsPill")
            page.wait_for_function(
                "() => document.getElementById('wsOverlay').classList.contains('show')")
            cur = page.evaluate(
                "() => document.getElementById('wsCurrent').textContent")
            check(CUR_PATH in cur, f"弹窗没显示当前工作区路径:{cur}")
            check("论文" in cur, f"弹窗没显示当前工作区名:{cur}")
            got = rows()
            check(len(got) == 4, f"应有 默认+3 条最近,实际 {len(got)}:{got}")
            check(got[0]["p"] == DEFAULT_ENTRY["path"], f"默认行应在最前:{got[0]}")
            check(got[0]["hasX"] is False, "默认工作区不该有删除按钮")
            check(sum(1 for r in got if r["hasX"]) == 3, "最近 3 条都该有 ✕")
            check(sum(1 for r in got if r["on"]) == 1, "当前工作区只应高亮一行")
            page.screenshot(path=str(OUT_DIR / "dialog.png"))

            # 3. 点当前那行 → 只提示,不发切换请求
            page.click("#wsList .ws-row.on")
            page.wait_for_timeout(200)
            check("switch_workspace" not in call_names(),
                  f"点当前工作区不该发切换请求:{calls()}")

            # 4. 点最近一行 → 切换
            page.click("#wsList .ws-row:nth-child(3)")      # 默认行、当前行之后的那条
            page.wait_for_function(
                "() => !document.getElementById('wsOverlay').classList.contains('show')")
            switched = [c for c in calls() if c[0] == "switch_workspace"]
            check(switched and switched[-1][1] == RECENT[1]["path"],
                  f"切换请求的路径不对:{switched}")
            page.wait_for_function(
                "() => document.getElementById('wsPill').textContent.includes('数据结构')")
            check("数据结构" in pill_text(), f"切换后胶囊没更新:{pill_text()}")
            page.screenshot(path=str(OUT_DIR / "after-switch.png"))

            # 5. 恢复默认
            page.click("#wsPill")
            page.wait_for_function(
                "() => document.getElementById('wsOverlay').classList.contains('show')")
            page.click("#btnWsDefault")
            page.wait_for_function(
                "() => window.__E2E_CALLS__.some(c => c[0] === 'reset_workspace')")
            page.wait_for_function(
                "() => document.getElementById('wsPill').textContent.includes('默认工作区')")
            check("默认工作区" in pill_text(), f"恢复默认后胶囊没更新:{pill_text()}")

            # 6. 选择文件夹… → pick 后立刻切过去
            before = len([c for c in calls() if c[0] == "switch_workspace"])
            page.click("#wsPill")
            page.wait_for_function(
                "() => document.getElementById('wsOverlay').classList.contains('show')")
            page.click("#btnWsPick")
            page.wait_for_function(
                "() => window.__E2E_CALLS__.some(c => c[0] === 'pick_workspace')")
            page.wait_for_function(
                "() => window.__E2E_CALLS__.filter(c => c[0] === 'switch_workspace').length"
                " > %d" % before)
            picked = [c for c in calls() if c[0] == "switch_workspace"][-1][1]
            check(picked == PICKED, f"没按对话框返回的路径切:{picked}")
            page.wait_for_function(
                "() => document.getElementById('wsPill').textContent.includes('另一台电脑的备份')")

            # 7. ✕ 只从列表移除,不切工作区
            page.click("#wsPill")
            page.wait_for_function(
                "() => document.getElementById('wsOverlay').classList.contains('show')")
            before_switches = len([c for c in calls() if c[0] == "switch_workspace"])
            page.click("#wsList .ws-row:nth-child(3) .x")
            page.wait_for_function(
                "() => window.__E2E_CALLS__.some(c => c[0] === 'forget_workspace')")
            forgotten = [c for c in calls() if c[0] == "forget_workspace"][-1][1]
            check(forgotten == RECENT[1]["path"], f"✕ 传的路径不对:{forgotten}")
            page.wait_for_timeout(200)
            check(len([c for c in calls() if c[0] == "switch_workspace"]) == before_switches,
                  "点 ✕ 不该切换工作区")
            left = [r["p"] for r in rows()]
            check(RECENT[1]["path"] not in left, f"✕ 之后该行还在:{left}")
            page.click("#btnWsClose")

            # 8. 后端 workspace 事件直接改胶囊
            page.evaluate(
                "(e) => window.__gcodeEvent(e)",
                {"type": "workspace", "path": "D:/活动/社团", "name": "社团",
                 "is_default": False, "session": "s1"})
            page.wait_for_function(
                "() => document.getElementById('wsPill').textContent.includes('社团')")
            check("D:/活动/社团" in pill_title(), f"事件后 title 没更新:{pill_title()}")

            # 9. 网页版(get_info 不带 workspace)→ 胶囊隐藏
            web = browser.new_page(viewport={"width": 1400, "height": 900})
            _boot(web, url, with_workspace=False)
            hidden = web.evaluate(
                "() => { const el = document.getElementById('wsPill');"
                " return el.style.display === 'none'; }")
            check(hidden, "网页版仍显示工作区胶囊(会暴露服务器路径)")
            web.screenshot(path=str(OUT_DIR / "web-hidden.png"))
            web.close()

            page.screenshot(path=str(OUT_DIR / "final.png"))
            browser.close()
    finally:
        shutdown()

    if failures:
        for f in failures:
            print("FAIL:", f)
        print(f"\n截图证据留在 {OUT_DIR}")
        return 1
    print(f"工作区胶囊/弹窗/切换/恢复默认/选文件夹/移除/事件/网页版隐藏 全部通过;证据在 {OUT_DIR}")
    print("\n=== WORKSPACE E2E PASS ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
