# -*- coding: utf-8 -*-
"""v0.9.159 前端回归:侧边栏底座(右列 tab 化 + 开放注册)。

这一轮换的是**承载面的形状**:右列从「任务监控」单页变成一张注册表,内置
五页与三方页走同一个 `register()`。最容易骗人的地方有三处,这场全钉:

1. **tab 是注册表画的,不是手写的**:五个内置页各有一格,点了真的换页
   (换页 = 容器显隐 + onShow,数据由桥来);
2. **数据是桥给的**:Git 行、后台命令、文件清单、子代理,页面上每一个数
   都能指回桥方法(stub 里埋的假账,页面上照原样出现;前端自己编一个数
   就会跟假账对不上);
3. **开放是真的**:一个"三方扩展"在页面加载前 `MiniCodePages.register(...)`,
   第六个 tab 就得出现并可用 —— 内置页没有任何特权通道。

跑法(项目根目录):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/e2e_ui_v09159.py
"""
from __future__ import annotations

import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from e2e_launch import launch          # noqa: E402  起浏览器这件事的一份真相

STUB_JS = """
window.__CALLS__ = [];
window.__ACT__ = {
  ok: true, empty: false, updated_at: "2026-10-11 10:00",
  now: null, timeline: {calls: 2}, findings: [],
  groups: [{key: "subagents", count: 2, more: 0, items: [
    {key: "sa:1", status: "running", title: "explore 子代理",
     sub: "在查代码", action: {type: "report"}, report: "第一路的报告"},
    {key: "sa:2", status: "ok", title: "review 子代理",
     sub: "审完了", action: {type: "report"}, report: "第二路的报告"}
  ]}]
};
const base = {
  get_info: () => Promise.resolve({
    version: "0.9.159", app_name: "引路学长", permission_mode: "accept_all",
    permission_modes: [{value: "accept_all", label: "自动放行"}],
    model: "test-model", active_provider: "test", workspace: "D:/demo/campus",
    workspace_name: "campus", workspace_is_default: true,
    providers: [{id: "test", name: "Test", model: "test-model", has_key: true}],
    api_key_configured: true, memory_count: 1, memory_user_count: 0,
    mcp_servers: [], skills: [], kb_count: 0, sandbox: null, campus_map: null,
    library_health: {}, paths: {app_dir: "D:/demo", writable: true},
    session_metrics: {ok: false, selectable: []}
  }),
  get_monitor_pref: () => Promise.resolve({ok: true, open: true, folded: {}}),
  set_monitor_pref: () => Promise.resolve({ok: true}),
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
  get_activity: () => Promise.resolve(window.__ACT__),
  get_env_info: () => Promise.resolve({ok: true}),
  // ---- Git 页 ----
  git_overview: () => Promise.resolve({
    ok: true, available: true, workspace: "D:/demo/campus",
    status: {ok: true, branch: "main", upstream: "origin/main", ahead: 1, behind: 0,
             rows: [
               {path: "src/a.py", x: " ", y: "M", staged: false, word: "修改"},
               {path: "docs/new.md", x: "?", y: "?", staged: false, word: "未跟踪"},
               {path: "readme.md", x: "M", y: " ", staged: true, word: "修改"}]},
    snapshots: [{sha: "abc123", label: "第 3 轮改动前", time: "2026-10-11 09:30"}]}),
  git_stage: (pj) => { window.__CALLS__.push(["git_stage", pj]);
                       return Promise.resolve({ok: true}); },
  git_diff: (p) => Promise.resolve({ok: true, text: "+新加的一行\\n-删掉的一行", note: ""}),
  git_log: () => Promise.resolve({ok: true, rows: [
      {sha: "a1b2c3d", date: "2026-10-10", subject: "init", head: true}]}),
  // ---- 终端页 ----
  get_terminal: () => Promise.resolve({ok: true, jobs: [
      {id: "j1", command: "echo hi", running: false, exit_code: 0, killed: false,
       chars: 3, dropped: 0, elapsed: 1, tail: "hi"},
      {id: "j2", command: "npm run build", running: true, exit_code: null,
       killed: false, chars: 120, dropped: 0, elapsed: 42, tail: "building..."}],
    buffer_chars: 4000, note: "只留尾部"}),
  terminal_run: (cmd) => { window.__CALLS__.push(["terminal_run", cmd]);
                           return Promise.resolve({ok: true, id: "j9", msg: "已放进后台"}); },
  terminal_kill: (id) => { window.__CALLS__.push(["terminal_kill", id]);
                           return Promise.resolve({ok: true}); },
  // ---- 文件页 ----
  list_project_files: (p, sub) => Promise.resolve({ok: true, path: p, rel: sub || "",
    items: [{name: "src", is_dir: true, size: 0, mtime: 1},
            {name: "readme.md", is_dir: false, size: 12, mtime: 2},
            {name: "app.py", is_dir: false, size: 99, mtime: 3}]}),
  file_read: (p) => Promise.resolve({ok: true, text: "# 你好\\n第一行",
                                     sha1: "a".repeat(40), size: 12, mtime: 1, path: p}),
  file_write: (p, c, s) => { window.__CALLS__.push(["file_write", p, s === ""]);
                             return Promise.resolve({ok: true, sha1: "b".repeat(40),
                                                     size: c.length, path: p,
                                                     created: s === ""}); },
  // ---- 三方扩展 ----
  get_extensions: () => Promise.resolve({ok: true, items: [], skipped: []}),
};
window.pywebview = {api: new Proxy(base, {
  get: (t, k) => (k in t) ? t[k]
    : ((...a) => { window.__CALLS__.push([k, ...a]); return Promise.resolve({ok: true}); })
})};
"""

# 三方注册要等 SidePages 存在:上面 init_script 里 MiniCodePages 还没有。
# 改成页面 load 后再注册 —— 由测试第一段自己点火。
EXT_JS = """
() => { window.MiniCodePages.register({id: "ext:hello", title: "扩展页", icon: "🧩",
  setup(el) { el.innerHTML = '<div class="sp-head"><b>🧩 扩展页</b></div>'
    + '<div class="sp-body">来自三方扩展的页面</div>'; },
  onShow() { window.__CALLS__.push(["ext:onShow"]); }});
  return document.querySelectorAll("#spTabs .sp-tab").length; }
"""


def _serve(directory: Path):
    import threading

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
        page.wait_for_timeout(700)

        # ① tab 是注册表画的:五个内置页,一页一格
        tabs = page.locator("#spTabs .sp-tab")
        check(tabs.count() == 5, f"内置页 5 格(实际 {tabs.count()})")
        check(page.locator('.sp-page[data-sp="activity"]').count() == 1,
              "活动监控成为底座第一页(容器在 sp-pages 里)")

        # ② 三方扩展同一张表:register 即出现,没有特权通道
        n = page.evaluate(EXT_JS)
        check(n == 6, f"三方注册后 6 格(实际 {n})")
        page.locator('#spTabs .sp-tab[title="扩展页"]').click()
        page.wait_for_timeout(120)
        check(page.locator('.sp-page[data-sp="ext:hello"]').is_visible(),
              "扩展页能打开(与内置页同一条 open 路)")
        check(page.locator('.sp-page[data-sp="activity"]').is_hidden(),
              "切到扩展页时活动页隐藏(显隐是互斥的)")

        # ③ Git 页:行来自 git_overview,按钮把路径过桥
        page.locator('#spTabs .sp-tab[title="Git"]').click()
        page.wait_for_timeout(300)
        check(page.locator('#gpBody .gp-row[data-path="src/a.py"]').count() == 1,
              "未暂存的行在(来自 git_overview 的 rows)")
        check(page.locator('#gpBody .gp-row[data-path="readme.md"]').count() == 1,
              "已暂存的行在")
        check("main" in page.locator("#gpBranch").text_content() or
              page.locator("#gpBranch").text_content() == "main", "分支名来自桥")
        page.locator('[data-op="stage"][data-path="src/a.py"]').click()
        page.wait_for_timeout(150)
        calls = page.evaluate("window.__CALLS__")
        check(["git_stage", '["src/a.py"]'] in calls,
              f"暂存按钮把路径逐字过桥(实际 {calls[-1] if calls else '无'})")

        # ④ 终端页:accept_all 档直接跑,输入逐字过桥;后台账同一份
        page.locator('#spTabs .sp-tab[title="终端"]').click()
        page.wait_for_timeout(300)
        check(page.locator('#tpBody .tp-row[data-job="j2"]').count() == 1,
              "后台账里的命令在(与「后台命令」面板同一本)")
        page.fill("#tpCmd", "git status")
        page.click("#tpRun")
        page.wait_for_timeout(150)
        calls = page.evaluate("window.__CALLS__")
        check(["terminal_run", "git status"] in calls,
              "输入的命令逐字过 terminal_run(accept_all 档不弹确认)")

        # ⑤ 子代理页:数据与活动页同源,报告就在行上
        page.locator('#spTabs .sp-tab[title="子代理"]').click()
        page.wait_for_timeout(300)
        check(page.locator('#spSubBody .act-row').count() >= 2,
              "两个子代理整列显示(来自 get_activity 的 groups)")
        check("explore 子代理" in page.locator("#spSubBody").text_content(),
              "子代理标题来自桥,不是前端编的")

        # ⑥ 文件页:树来自 list_project_files,打开文件内容来自 file_read
        page.locator('#spTabs .sp-tab[title="文件"]').click()
        page.wait_for_timeout(300)
        check(page.locator('#fpList .fp-row[data-name="readme.md"]').count() == 1,
              "目录树来自 list_project_files")
        page.locator('#fpList .fp-row[data-name="readme.md"]').click()
        page.wait_for_timeout(200)
        check("第一行" in page.locator("#fpView").text_content(),
              "文件内容来自 file_read")
        page.click("#fpEdit")
        page.fill("#fpTa", "# 改过的内容")
        page.click("#fpSave")
        page.wait_for_timeout(200)
        calls = page.evaluate("window.__CALLS__")
        check(any(c[0] == "file_write" and c[2] is False for c in calls),
              "保存带着打开时的底账 sha 过桥(不是空 sha 的新建)")

        # ⑦ openPanel 的 side: 路由
        page.evaluate('openPanel("side:git")')
        page.wait_for_timeout(200)
        check(page.locator('.sp-page[data-sp="git"]').is_visible(),
              'openPanel("side:git") 直达 Git 页(agent 深链接得进来)')

        # 全程不许有页面错误
        check(not errors, f"无页面错误(实际:{errors[:2] or '无'})")

        browser.close()
    shutdown()

    print(f"\n共 {ran} 项,失败 {len(failures)} 项")
    for f in failures:
        print("  ✗", f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
