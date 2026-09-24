# -*- coding: utf-8 -*-
"""v0.9.11 前端回归:真实浏览器里验证本轮四项需求。

    p1/p2 模型实时同步:保存模型(session_metrics 事件)→ 任务面板立刻更新
    p3 深色模式:深色下「+ 新建任务」按钮不再是白底(已并入项目卡片后,验证
       当前项目卡片内 newhere 按钮在深色下是深色玻璃)
    p4/p6 项目卡片:卡片带路径行与操作钮,与纯文本会话条目区分;当前项目卡片
       内有「＋ 新建任务」;⋯ 菜单有查看文件/重命名/删除
    p7 项目文件:查看文件弹窗列出真实条目,点目录可进入,面包屑可返回

跑法(项目根目录):
    .venv\\Scripts\\python.exe scripts\\e2e_ui_v0911.py
退出码 0 = 通过;1 = 有断言失败。
"""
from __future__ import annotations

import json
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
OUT_DIR = ROOT / "_tmp_dev_check" / "ui_v0911"

PROJ = {
    "current": {"path": "D:/demo/yhbz", "name": "yhbz", "is_default": False},
    "default": {"path": "D:/app", "name": "默认工作区(程序目录)", "is_default": True},
    "recent": [
        {"path": "D:/demo/yhbz", "name": "yhbz", "is_default": False},
        {"path": "D:/demo/paper", "name": "paper", "is_default": False},
    ],
}

STUB_JS = """
window.__E2E_CALLS__ = [];
window.__stubReady = true;
const CALLS = window.__E2E_CALLS__;
function log(){ CALLS.push([...arguments]); return Promise.resolve({ok:true}); }
window.pywebview = {
  api: {
    get_info: () => Promise.resolve({
      version: "0.9.11", app_name: "引路学长",
      permission_mode: "confirm",
      permission_modes: [
        {value: "confirm", label: "变更前确认"},
        {value: "accept_edits", label: "自动编辑"},
        {value: "accept_all", label: "自动放行"},
        {value: "full", label: "完全访问"}
      ],
      session_key: "s1", model: "deepseek-v4-flash", active_provider: "gzasc",
      providers: [{id: "gzasc", name: "广州应用科技学院", model: "deepseek-v4-flash"}],
      workspace: "D:/demo/yhbz", workspace_name: "yhbz", workspace_is_default: false,
      memory_count: 0, memory_user_count: 0,
      sandbox: {enabled: false}, vision: false, campus_map: null,
      api_key_configured: true,
      reasoning: {visible: false, options: [], choice: "default"},
      /* 这一份 payload 的形状照 `minicode/model_catalog.py` 的 `snapshot()`:
         每一行带 `key = f"{供应商id}::{模型名}"`,整份带 `selected_key = f"{在用供应商}::{在用模型}"`。
         v0.9.13 把"哪一行是当前项"从 `provider_id` 换成这两把钥匙(同一供应商多模型时
         provider_id 分不开),夹具没跟着改 → 这一度是**假绿**:菜单里根本没有 `.on` 行,
         断言读到的是旧 payload 残留的那一行。 */
      session_metrics: {ok: true, provider_id: "gzasc", model: "deepseek-v4-flash",
        selected_key: "gzasc::deepseek-v4-flash",
        capabilities: ["文本"], supports_vision: false, known: true,
        context_window: 327680, context_tokens: 100, consumed: 0,
        compact_ratio: 0.7,
        selectable: [
          {key: "gzasc::deepseek-v4-flash",
           id: "gzasc", name: "广州应用科技学院", model: "deepseek-v4-flash",
           has_key: true, builtin: true, supports_vision: false,
           context_window: 327680}
        ]}
    }),
    refresh_sessions: () => Promise.resolve({ok: true}),
    get_suggestions: () => Promise.resolve({items: []}),
    get_reasoning: () => Promise.resolve({visible: false, options: [], choice: "default"}),
    get_recent_projects: () => Promise.resolve(window.__PROJ__),
    get_session_metrics: () => Promise.resolve({ok: false}),
    resume_session: (i) => log("resume_session", i),
    delete_session: (i) => log("delete_session", i),
    send_task: (t) => log("send_task", t),
    new_task: () => log("new_task"),
    pick_workspace: () => Promise.resolve({ok: false, msg: "已取消"}),
    do_switch: null,
    switch_workspace: (p) => log("switch_workspace", p),
    forget_workspace: (p) => log("forget_workspace", p),
    delete_project: (p) => { CALLS.push(["delete_project", p]); return Promise.resolve({ok: true, recent: []}); },
    rename_workspace: (p, n) => { CALLS.push(["rename_workspace", p, n]); return Promise.resolve({ok: true, name: n}); },
    list_project_files: (p, sub) => {
      CALLS.push(["list_project_files", p, sub]);
      const items = sub ? [{name: "inner.txt", is_dir: false, size: 3, mtime: 0}]
        : [{name: "docs", is_dir: true, size: 0, mtime: 0},
           {name: "readme.md", is_dir: false, size: 123, mtime: 0}];
      return Promise.resolve({ok: true, rel: sub || "", items});
    }
  }
};
"""


def _serve(directory: Path):
    class _QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = partial(_QuietHandler, directory=str(directory))
    # 随机端口可能撞上 Chromium 的 unsafe port 黑名单(66xx 一带):不在禁区才用。
    # 逐次创建再关闭会把候选端口打进 TIME_WAIT,越试越少 —— 这里先选号再长期持有。
    import socket
    port = None
    sock = None
    for _ in range(200):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        got = probe.getsockname()[1]
        probe.close()
        if 6000 <= got <= 7999 or got in (25, 501, 6665, 6666, 6667, 6668, 6669):
            continue
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", got), handler)
            port = got
            break
        except OSError:
            continue
    assert port is not None, "拿不到 Chromium 允许的端口"
    import threading
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}/index.html", httpd.shutdown


def _push(page, event: dict):
    page.evaluate(f"window.__gcodeEvent({json.dumps(event, ensure_ascii=False)})")


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
        print(("  ✓ " if cond else "  ✗ ") + message)

    try:
        with sync_playwright() as p:
            browser = launch(p)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.add_init_script(STUB_JS)
            page.add_init_script(f"window.__PROJ__ = {json.dumps(PROJ)}")
            # 深色主题(localStorage 与 applyTheme 一致)
            page.add_init_script(
                "localStorage.setItem('gcode-theme','dark');"
                "document.documentElement.classList.add('dark');")
            page.goto(url, wait_until="load")
            page.wait_for_function(
                "() => document.querySelectorAll('#projects .proj').length >= 2")

            # ---- p4/p6:项目卡片结构(与纯文本会话区分) ----
            first = page.locator("#projects .proj").first
            check(first.locator(".p").count() == 1,
                  "项目卡片有路径行(第二行小字)")
            check(first.locator(".ops .op").count() == 2,
                  "项目卡片悬浮操作钮(＋/⋯)存在")
            check(page.locator(".proj.current .newhere").count() == 1,
                  "当前项目卡片内有「＋ 新建任务」按钮")
            check(page.locator(".proj.current .curtag").inner_text() == "当前",
                  "当前项目卡片带「当前」徽标")
            # 全局旧按钮已移除
            check(page.locator(".sidebar > .newtask").count() == 0,
                  "侧栏顶部不再有独立的全局新建按钮(并入卡片)")

            # ---- p3:深色下 newhere 按钮不是白底 ----
            dark = page.evaluate(
                "() => { const s = getComputedStyle("
                "document.querySelector('.proj.current .newhere'));"
                "const rgb = s.backgroundColor.match(/\\d+/g).map(Number);"
                "return {lum: (rgb[0]+rgb[1]+rgb[2])/3, html: document.documentElement.classList.contains('dark')}; }")
            check(dark["html"] is True, "页面处于深色主题")
            check(dark["lum"] < 140,
                  f"深色下「＋ 新建任务」按钮不是白底(亮度 {dark['lum']:.0f})")

            # ---- p4:点卡片内新建任务 → new_task ----
            page.click(".proj.current .newhere")
            page.wait_for_function(
                "() => window.__E2E_CALLS__.some(c => c[0] === 'new_task')")
            check(True, "点卡片内「＋ 新建任务」→ new_task()")

            # ---- p4:⋯ 菜单:查看文件/重命名/删除 ----
            first.hover()
            first.locator(".op.menu").click()
            page.wait_for_selector("#projMenu.show")
            acts = page.eval_on_selector_all(
                "#projMenu button", "els => els.map(e => e.dataset.act)")
            check(acts == ["files", "rename", "del"],
                  f"⋯ 菜单含查看文件/重命名/删除({acts})")

            # ---- p4:删除项目 → delete_project ----
            page.click("#projMenu button[data-act='del']")
            page.wait_for_selector("#appConfirmOverlay.show")
            page.click("#btnAppOk")
            page.wait_for_function(
                "() => window.__E2E_CALLS__.some(c => c[0] === 'delete_project')")
            check(True, "确认后调用 delete_project(文件夹不删,仅移出列表)")

            # ---- p7:查看文件弹窗 ----
            first.hover()
            first.locator(".op.menu").click()
            page.wait_for_selector("#projMenu.show")
            page.click("#projMenu button[data-act='files']")
            page.wait_for_selector("#filesOverlay.show")
            page.wait_for_function(
                "() => document.querySelectorAll('#filesList .frow').length >= 2")
            names = page.eval_on_selector_all(
                "#filesList .frow .fn", "els => els.map(e => e.textContent)")
            check(names[0] == "docs" and "readme.md" in names,
                  f"文件列表目录在前、文件在后({names})")
            sizes = page.eval_on_selector_all(
                "#filesList .frow .fs", "els => els.map(e => e.textContent)")
            check("123 B" in sizes, f"文件显示大小({sizes})")

            # 点目录进子目录
            page.click("#filesList .frow.dir")
            page.wait_for_function(
                "() => window.__E2E_CALLS__.filter(c => c[0]==='list_project_files')"
                ".some(c => c[2] === 'docs')")
            page.wait_for_function(
                "() => document.querySelectorAll('#filesList .frow').length >= 2")
            check(True, "点目录进入子目录(list_project_files(p,'docs'))")
            up = page.locator("#filesList .frow.dir").first
            check(up.inner_text().find("..") >= 0, "子目录里有「上一级」入口")

            # 面包屑返回根
            page.click("#filesCrumb a")
            page.wait_for_function(
                "() => window.__E2E_CALLS__.filter(c => c[0]==='list_project_files')"
                ".some(c => c[2] === '')")
            check(True, "点面包屑「项目根目录」返回根")
            page.click("#btnFilesClose")            # 关掉弹窗再测任务面板
            page.wait_for_function(
                "() => !document.getElementById('filesOverlay').classList.contains('show')")

            # ---- p1/p2:session_metrics 事件 → 任务面板实时更新 ----
            _push(page, {"type": "session_metrics", "session": "s1",
                         "provider_id": "ds", "model": "deepseek-chat",
                         "selected_key": "ds::deepseek-chat",
                         "capabilities": ["文本", "工具调用"],
                         "supports_vision": False, "known": True,
                         "context_window": 65536, "context_tokens": 42,
                         "consumed": 7, "compact_ratio": 0.7,
                         "selectable": [
                             {"key": "gzasc::deepseek-v4-flash",
                              "id": "gzasc", "name": "广州应用科技学院",
                              "model": "deepseek-v4-flash", "has_key": True,
                              "builtin": True, "supports_vision": False,
                              "context_window": 327680},
                             {"key": "ds::deepseek-chat", "id": "ds",
                              "name": "DeepSeek", "model": "deepseek-chat",
                              "has_key": True, "builtin": False,
                              "supports_vision": False, "context_window": 65536}
                         ]})
            page.wait_for_function(
                "() => document.getElementById('tmName').textContent === 'deepseek-chat'")
            caps = page.inner_text("#tmCaps")
            check("纯文本" in caps and "65.5K" in caps,
                  f"任务面板能力标签已跟随新模型({caps})")
            page.click("#taskModelBtn")
            page.wait_for_selector("#taskModelMenu.show")
            rows = page.eval_on_selector_all(
                "#taskModelMenu .trow .tname", "els => els.map(e => e.textContent)")
            check("DeepSeek" in rows,
                  f"可切列表立刻出现新加的模型({rows})")
            ons = page.eval_on_selector_all(
                "#taskModelMenu .trow.on .tname", "els => els.map(e => e.textContent)")
            check(ons == ["DeepSeek"],
                  f"当前选中项与新模型一致、且**只点亮那一行**(实测 {ons})")

            page.screenshot(path=str(OUT_DIR / "projects_and_files.png"),
                            full_page=False)
            browser.close()
    finally:
        shutdown()

    print()
    if failures:
        print(f"FAIL:{len(failures)} 项未过")
        for f in failures:
            print("  -", f)
        return 1
    print("PASS: v0.9.11 前端回归全过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
