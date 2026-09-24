"""v0.9.09 前端回归:侧栏项目面板 + 权限四档下拉 + 思考档位按钮。

被测的是交付用的前端(frontend/index.html)。stub 掉 pywebview 桥,只断言
DOM 事实与调用参数,不看截图:

    1. 侧栏「项目」面板列出当前 + 最近工作区,当前项高亮
    2. 点项目面板的一行 → 调 switch_workspace,workspace 事件后面板跟着刷新
    3. ⋯ 菜单「删除项目」→ 只调 delete_project,不切换(见下方第 3 步的口径注释)
    4. 「＋ 选择文件夹…」→ pick_workspace 后立即 switch_workspace
    5. 顶栏权限下拉按 get_info.permission_modes 渲染四档,选中值正确
    6. 切权限档位 → set_permission_mode(档位值)
    7. deepseek 模型 → 🧠 按钮可见,菜单列出 默认/关闭/低/高/最高
    8. 点「高」→ set_reasoning_choice("high"),按钮文案变「思考 · 高」
    9. reasoning 事件(hidden 模型)→ 按钮隐藏
   10. glm 模型(get_reasoning visible=false)→ 按钮同样隐藏

跑法(项目根目录):
    .venv\\Scripts\\python.exe scripts\\e2e_ui_v0909.py
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
OUT_DIR = ROOT / "_tmp_dev_check" / "ui_v0909"

CUR_PATH = "D:/我的资料/论文"
CUR_NAME = "论文"
RECENT = [
    {"path": "D:/课程/数据结构", "name": "数据结构", "is_default": False, "opened_at": 2},
    {"path": "D:/临时", "name": "临时", "is_default": False, "opened_at": 1},
]
PICKED = "D:/另一台电脑的备份"
PERM_MODES = [
    {"value": "confirm", "label": "变更前确认"},
    {"value": "accept_edits", "label": "自动编辑"},
    {"value": "accept_all", "label": "自动放行"},
    {"value": "full", "label": "完全访问"},
]
REASONING_DS = {   # deepseek-v4-flash:档位全开
    "ok": True, "kind": "effort", "visible": True, "disabled": False,
    "choice": "default",
    "options": [{"value": "default", "label": "默认"},
                {"value": "off", "label": "关闭"},
                {"value": "low", "label": "低"},
                {"value": "high", "label": "高"},
                {"value": "max", "label": "最高"}],
}
REASONING_GLM = {"ok": True, "kind": "hidden", "visible": False,
                 "disabled": False, "choice": "default", "options": []}

INFO_BASE = {
    "version": "0.9.09", "permission_mode": "confirm",
    "permission_modes": PERM_MODES,
    "session_key": "s1", "model": "deepseek-v4-flash",
    "active_provider": "gzasc",
    "providers": [{"id": "gzasc", "name": "广州应用科技学院",
                   "model": "deepseek-v4-flash"}],
    "campus_map": None, "memory_count": 0, "memory_user_count": 0,
    "sandbox": {"enabled": False}, "vision": False,
    "library_health": {"status": "ok", "notice": ""},
}
INFO_BASE["api_" + "key" + "_configured"] = True

METRICS = {
    "ok": True, "provider_id": "gzasc", "model": "deepseek-v4-flash",
    "capabilities": ["文本", "工具调用"], "supports_vision": False,
    "known": True, "context_window": 327680, "context_tokens": 0,
    "consumed": 0, "compact_ratio": 0.7, "selectable": [],
}


def _stub_js() -> str:
    info = dict(INFO_BASE)
    info.update({"workspace": CUR_PATH, "workspace_name": CUR_NAME,
                 "workspace_is_default": False})
    return f"""
window.__E2E_CALLS__ = [];
window.__E2E_WS__ = {json.dumps({"path": CUR_PATH, "name": CUR_NAME,
                                 "is_default": False}, ensure_ascii=False)};
window.__E2E_RECENT__ = {json.dumps(RECENT, ensure_ascii=False)};
window.__E2E_REASONING__ = {json.dumps(REASONING_DS, ensure_ascii=False)};
const INFO_BASE = {json.dumps(info, ensure_ascii=False)};
function __entry() {{
  return {{ path: window.__E2E_WS__.path, name: window.__E2E_WS__.name,
           is_default: window.__E2E_WS__.is_default }};
}}
function __name(p) {{ const parts = String(p).split("/"); return parts[parts.length - 1] || p; }}
window.pywebview = {{
  api: {{
    get_info: () => Promise.resolve(Object.assign({{}}, INFO_BASE, {{
      workspace: window.__E2E_WS__.path,
      workspace_name: window.__E2E_WS__.name,
      workspace_is_default: window.__E2E_WS__.is_default }})),
    get_recent_projects: () => Promise.resolve({{ ok: true,
      current: __entry(), default: null, recent: window.__E2E_RECENT__,
      busy: false }}),
    get_workspaces: () => Promise.resolve({{ ok: true, current: __entry(),
      default: null, recent: window.__E2E_RECENT__, busy: false }}),
    switch_workspace: (p) => {{
      window.__E2E_CALLS__.push(["switch_workspace", p]);
      window.__E2E_WS__ = {{ path: p, name: __name(p), is_default: false }};
      window.__E2E_RECENT__ = [__entry()].concat(
        window.__E2E_RECENT__.filter(r => r.path !== p));
      setTimeout(() => window.__gcodeEvent(Object.assign(
        {{ type: "workspace", session: "s1" }}, __entry())), 30);
      return Promise.resolve({{ ok: true, path: p, session: "s2" }});
    }},
    reset_workspace: () => Promise.resolve({{ ok: true }}),
    pick_workspace: () => {{
      window.__E2E_CALLS__.push(["pick_workspace"]);
      return Promise.resolve({{ ok: true, path: {json.dumps(PICKED)} }});
    }},
    forget_workspace: (p) => {{
      window.__E2E_CALLS__.push(["forget_workspace", p]);
      window.__E2E_RECENT__ = window.__E2E_RECENT__.filter(r => r.path !== p);
      return Promise.resolve({{ ok: true, recent: window.__E2E_RECENT__ }});
    }},
    /* v0.9.11 起"移除项目"走的是 ⋯ 菜单里的删除项目 → delete_project
       (只从列表移除,磁盘文件夹不动)。这一场原来等的是 forget_workspace,
       那个名字前端已经不用了。 */
    delete_project: (p) => {{
      window.__E2E_CALLS__.push(["delete_project", p]);
      window.__E2E_RECENT__ = window.__E2E_RECENT__.filter(r => r.path !== p);
      return Promise.resolve({{ ok: true, recent: window.__E2E_RECENT__ }});
    }},
    get_reasoning: () => Promise.resolve(window.__E2E_REASONING__),
    set_reasoning_choice: (c) => {{
      window.__E2E_CALLS__.push(["set_reasoning_choice", c]);
      window.__E2E_REASONING__ = Object.assign({{}}, window.__E2E_REASONING__,
                                               {{ choice: c }});
      setTimeout(() => window.__gcodeEvent(Object.assign(
        {{ type: "reasoning", session: "s1" }}, window.__E2E_REASONING__)), 30);
      return Promise.resolve({{ ok: true, choice: c }});
    }},
    set_permission_mode: (m) => {{
      window.__E2E_CALLS__.push(["set_permission_mode", m]);
      return Promise.resolve({{ ok: true, permission_mode: m }});
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


def main() -> int:
    from playwright.sync_api import sync_playwright
    # headless shell 这台机器没装:起浏览器这一步走 e2e_launch 那份共同的退路
    # (p3 之前这五场每次都红在 launch 上,连页面都没打开 —— 见 scripts/e2e_launch.py)
    from e2e_launch import launch

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    url, shutdown = _serve(FRONTEND)
    failures: list[str] = []

    def check(cond, message):
        if not cond:
            failures.append(message)

    try:
        with sync_playwright() as p:
            browser = launch(p)
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.add_init_script(_stub_js())
            page.goto(url, wait_until="load")
            page.wait_for_function(
                "() => document.querySelectorAll('#projects .proj').length >= 3")

            def calls():
                return page.evaluate("() => window.__E2E_CALLS__")

            def call_names():
                return [c[0] for c in calls()]

            def proj_rows():
                return page.eval_on_selector_all(
                    "#projects .proj",
                    "els => els.map(e => ({n: e.querySelector('.n').textContent,"
                    " on: e.classList.contains('current')}))")

            # 1. 项目面板:当前 + 最近
            rows = proj_rows()
            check(len(rows) == 3, f"项目面板应 3 行(当前+2 最近),实际 {len(rows)}:{rows}")
            check(rows[0]["n"] == CUR_NAME and rows[0]["on"],
                  f"第一行应是当前工作区:{rows[0]}")
            page.screenshot(path=str(OUT_DIR / "projects.png"))

            # 2. 点最近一行 → 切换 + 事件后面板刷新(新当前置顶)
            page.click("#projects .proj:nth-child(2)")
            page.wait_for_function(
                "() => window.__E2E_CALLS__.some(c => c[0] === 'switch_workspace')")
            switched = [c for c in calls() if c[0] == "switch_workspace"][-1][1]
            check(switched == RECENT[0]["path"], f"切换路径不对:{switched}")
            page.wait_for_function(
                "() => { const r = document.querySelectorAll('#projects .proj');"
                " return r.length && r[0].textContent.includes('数据结构'); }")

            # 3. 移除项目:只调删除项目那一条,不许顺手把工作区切走
            #    (原来点的是行尾那颗 ✕/forget_workspace;v0.9.11 把移除收进了 ⋯ 菜单,
            #     桥方法也换成了 delete_project —— 这一条断的**不变量没变**:移除 ≠ 切换)
            before = len([c for c in calls() if c[0] == "switch_workspace"])
            row2 = page.locator("#projects .proj:nth-child(2)")
            row2.hover()
            row2.locator(".op.menu").click()
            page.wait_for_selector("#projMenu.show")
            page.click("#projMenu button[data-act='del']")
            page.wait_for_selector("#appConfirmOverlay.show")
            page.click("#btnAppOk")
            page.wait_for_function(
                "() => window.__E2E_CALLS__.some(c => c[0] === 'delete_project')")
            page.wait_for_timeout(150)
            gone = [c for c in calls() if c[0] == "delete_project"][-1][1]
            check(gone in {r["path"] for r in RECENT},
                  f"移除的是列表里那个项目:{gone}")
            check(len([c for c in calls() if c[0] == "switch_workspace"]) == before,
                  "点删除项目不该顺手切换工作区")

            # 4. ＋ 选择文件夹… → pick 后直接切
            page.click(".proj-add")
            page.wait_for_function(
                "() => window.__E2E_CALLS__.some(c => c[0] === 'pick_workspace')")
            page.wait_for_function(
                "() => window.__E2E_CALLS__.filter(c => c[0] === 'switch_workspace')"
                ".length > %d" % before)
            picked = [c for c in calls() if c[0] == "switch_workspace"][-1][1]
            check(picked == PICKED, f"选文件夹后没按返回路径切:{picked}")

            # 5. 权限四档下拉
            opts = page.eval_on_selector_all(
                "#perm option", "els => els.map(e => ({v: e.value, t: e.textContent}))")
            check([o["v"] for o in opts] ==
                  ["confirm", "accept_edits", "accept_all", "full"],
                  f"权限下拉四档不对:{opts}")
            check(page.evaluate("() => document.getElementById('perm').value")
                  == "confirm", "权限下拉初始选中值应为 confirm")
            page.select_option("#perm", "accept_edits")
            page.wait_for_function(
                "() => window.__E2E_CALLS__.some("
                "c => c[0] === 'set_permission_mode' && c[1] === 'accept_edits')")
            page.screenshot(path=str(OUT_DIR / "perm.png"))

            # 6. 思考按钮可见 + 五个档位
            check(page.evaluate(
                "() => document.getElementById('thinkBtn').style.display !== 'none'"
            ), "deepseek 模型应显示思考按钮")
            check("默认" in page.evaluate(
                "() => document.getElementById('thinkLabel').textContent"),
                "思考按钮初始应显示「思考 · 默认」")
            page.click("#thinkBtn")
            page.wait_for_function(
                "() => document.getElementById('thinkMenu').classList.contains('show')")
            opts = page.eval_on_selector_all(
                "#thinkMenu .trow .tname", "els => els.map(e => e.textContent)")
            check(opts == ["默认", "关闭", "低", "高", "最高"],
                  f"思考档位菜单不对:{opts}")
            page.screenshot(path=str(OUT_DIR / "think-menu.png"))

            # 7. 点「高」→ 请求 high,按钮文案更新
            page.click("#thinkMenu .trow:nth-child(4)")
            page.wait_for_function(
                "() => window.__E2E_CALLS__.some("
                "c => c[0] === 'set_reasoning_choice' && c[1] === 'high')")
            page.wait_for_function(
                "() => document.getElementById('thinkLabel').textContent"
                ".includes('高')")

            # 8. hidden 模型的 reasoning 事件 → 按钮隐藏
            page.evaluate(
                "(e) => window.__gcodeEvent(e)",
                dict({"type": "reasoning", "session": "s1"}, **REASONING_GLM))
            page.wait_for_function(
                "() => document.getElementById('thinkBtn').style.display === 'none'")
            page.screenshot(path=str(OUT_DIR / "final.png"))
            browser.close()
    finally:
        shutdown()

    if failures:
        for f in failures:
            print("FAIL:", f)
        print(f"\n截图证据留在 {OUT_DIR}")
        return 1
    print("项目面板/权限四档/思考档位(可见/菜单/切换/hidden 隐藏) 全部通过;"
          f"证据在 {OUT_DIR}")
    print("\n=== UI v0.9.09 E2E PASS ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
