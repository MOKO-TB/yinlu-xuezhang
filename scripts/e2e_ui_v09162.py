# -*- coding: utf-8 -*-
"""v0.9.162 前端回归:ZCode 同款双面板开关 + 底部终端坞 + 入口栏回原样。

用户看图 p1/p3/p4 点的三件事,这场各钉一组:

1. **入口栏回原样**:底座四入口(Git/文件/帮手/侧聊)从左栏撤下 ——
   数格子、点一遍,多一颗少一颗都红;
2. **顶栏双开关**:「切换面板」开合右侧底座(按钮跟着亮/灭),「切换终端」
   开合底部终端坞;快捷键 Ctrl+Alt+B / Ctrl+` 各自等效;
3. **终端坞**:占内容区整行、顶边拖高(钳制两头)、开合状态与高度都记本地。

跑法(项目根目录):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/e2e_ui_v09162.py
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

from e2e_launch import launch          # noqa: E402

STUB_JS = r"""
window.__CALLS__ = [];
const base = {
  get_info: () => Promise.resolve({
    version: "0.9.162", app_name: "引路学长", permission_mode: "accept_all",
    permission_modes: [{value: "accept_all", label: "自动放行"}],
    model: "test-model", active_provider: "test", workspace: "D:/demo/campus",
    workspace_name: "campus", workspace_is_default: true,
    providers: [{id: "test", name: "Test", model: "test-model", has_key: true}],
    api_key_configured: true, memory_count: 1, memory_user_count: 0,
    mcp_servers: [], skills: [], kb_count: 0, sandbox: null, campus_map: null,
    library_health: {}, paths: {app_dir: "D:/demo", writable: true},
    session_metrics: {ok: false, selectable: []}
  }),
  get_monitor_pref: () => Promise.resolve({ok: true, open: false, folded: {}}),
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
  get_activity: () => Promise.resolve({ok: true, empty: true, groups: []}),
  get_env_info: () => Promise.resolve({ok: true}),
  ...(() => { let dockCwd = "D:/demo/campus"; return {
  get_terminal: () => Promise.resolve({ok: true, cwd: dockCwd, jobs: [
      {id: "j1", command: "echo hi", running: false, exit_code: 0, killed: false,
       chars: 3, dropped: 0, elapsed: 1, tail: "hi", cwd: "D:/demo/campus"}],
    buffer_chars: 4000, note: "只留尾部"}),
  open_url: (u) => { window.__CALLS__.push(["open_url", u]);
    return Promise.resolve({ok: true}); },
  mydata_open_dir: () => { window.__CALLS__.push(["mydata_open_dir"]);
    return Promise.resolve({ok: true}); },
  extensions_overview: () => { window.__CALLS__.push(["extensions_overview"]);
    return Promise.resolve({ok: true,
      skills: [{name: "code-review", description: "审代码", channel: "内置技能"},
               {name: "my-skill", description: "自写", channel: "应用目录"}],
      skill_channels: [{key: "builtin", label: "内置技能", note: "", path: "D:/x/skills",
                        exists: true, count: 26}],
      mcp: [{name: "files", connected: true, tools: 3}],
      sidepages: {items: [{id: "db", title: "数据库", version: "0.1"}],
                  skipped: [{dir: "broken", msg: "manifest.json 读不出来:JSONDecodeError"}],
                  root: "D:/demo/app/extensions"},
      hooks: {count: 1, events: ["stop"]}}); },
  check_update_now: () => { window.__CALLS__.push(["check_update_now"]);
    return Promise.resolve({ok: true, current: "0.9.162", update: {
      version: "0.9.173", date: "2026-10-14", truncated: false,
      name: "引路学长 v0.9.173",
      url: "https://github.com/MOKO-TB/yinlu-xuezhang/releases/tag/v0.9.173",
      notes: "# v0.9.173\n\n## 本次更新\n- 更新检查搬到 GitHub Release\n- 更新日志卡片"}}); },
  terminal_run: (c, cwd) => { window.__CALLS__.push(["terminal_run", c, cwd]);
    if (/^cd\s/i.test(c)) { dockCwd = "D:/demo/campus/sub";
      return Promise.resolve({ok: true, moved: true, cwd: dockCwd}); }
    return Promise.resolve({ok: true, id: "j9", msg: "已放进后台",
      cwd: cwd || dockCwd}); },
  };})(),
};
window.pywebview = {api: new Proxy(base, {
  get: (t, k) => (k in t) ? t[k]
    : ((...a) => { window.__CALLS__.push([k, ...a]); return Promise.resolve({ok: true}); })
})};
"""

DOCK_H = """() => parseInt(getComputedStyle(document.documentElement)
  .getPropertyValue('--dock-h'), 10) || 0"""


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
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.add_init_script(STUB_JS)
        page.goto(url)
        page.wait_for_timeout(700)

        # ① 入口栏回原样:16 颗,底座四入口一颗不剩
        rail_ids = page.evaluate(
            "() => [...document.querySelectorAll('.rail button')].map(b => b.id)")
        check(rail_ids.length if False else len(rail_ids) == 16,
              f"入口栏回到 16 颗(实际 {len(rail_ids)})")
        for gone in ("btnSpGit", "btnSpFiles", "btnSpSubs", "btnSpChat"):
            check(gone not in rail_ids, f"{gone} 已从入口栏撤下")

        # ② 顶栏「切换面板」:开合右侧底座,按钮跟着亮/灭
        check(page.evaluate("() => document.querySelector('.app')"
                            ".classList.contains('act-open')") is False,
              "开机时底座收着(不替用户拉开)")
        page.click("#btnPanelToggle")
        page.wait_for_selector(".app.act-open")
        check(page.evaluate("() => document.getElementById('btnPanelToggle')"
                            ".classList.contains('on')"),
              "切换面板按钮亮了(与开态同步)")
        page.click("#btnPanelToggle")
        page.wait_for_timeout(150)
        check(page.evaluate("() => !document.querySelector('.app')"
                            ".classList.contains('act-open')"),
              "再点收得回去")
        page.keyboard.press("Control+Alt+b")
        page.wait_for_selector(".app.act-open")
        check(page.evaluate("() => document.getElementById('btnPanelToggle')"
                            ".classList.contains('on')"),
              "Ctrl+Alt+B 等效于那颗按钮")

        # ③ 顶栏「切换终端」:开合底部终端坞
        check(page.evaluate("() => document.getElementById('termDock').hidden"),
              "开机时终端坞收着")
        page.click("#btnTermToggle")
        page.wait_for_function(
            "() => document.querySelectorAll('.term-dock .t-cmd').length === 1")
        check(page.evaluate("() => !document.getElementById('termDock').hidden"),
              "切换终端打开底部坞")
        check("Windows PowerShell" in page.locator("#tpBody").inner_text()
              and "版权所有" in page.locator("#tpBody").inner_text(),
              "打开就是 PowerShell 的横幅(不是卡片列表)")
        check(page.locator("#tpPrompt").text_content().startswith("PS "),
              "提示符内联在流尾(PS <目录>>),不是底部对话框")
        # 主题化(用户图 p1):浅色模式下坞不再是钉死的黑块 —— 背景走主题变量
        bg = page.evaluate(
            "() => getComputedStyle(document.querySelector('.td-body')).backgroundColor")
        check(bg not in ("rgb(20, 20, 24)", "rgb(20,20,24)"),
              f"浅色模式下坞底不再是硬编码黑(实际 {bg})")
        check(page.locator('.td-tab .td-shellname').count() == 1,
              "头部有 ZCode 式的工作区标签")
        check(page.evaluate("() => document.getElementById('btnTermToggle')"
                            ".classList.contains('on')"),
              "切换终端按钮亮了")
        page.keyboard.press("Control+`")
        page.wait_for_timeout(150)
        check(page.evaluate("() => document.getElementById('termDock').hidden"),
              "Ctrl+` 收回终端坞")
        page.keyboard.press("Control+`")
        page.wait_for_function(
            "() => document.querySelectorAll('.term-dock .t-cmd').length === 1")

        # ④ 坞占内容区整行(从入口栏右侧铺到窗右缘,不给 260px 的侧栏留缝)
        geo = page.evaluate("""() => {
          const d = document.getElementById('termDock').getBoundingClientRect();
          const rail = document.querySelector('.rail').getBoundingClientRect();
          return {left: d.left, right: d.right, railRight: rail.right, vw: innerWidth};
        }""")
        check(abs(geo["left"] - geo["railRight"]) < 2,
              f"坞从入口栏右缘起(左缘 {geo['left']:.0f} vs 栏右缘 {geo['railRight']:.0f})")
        check(abs(geo["right"] - geo["vw"]) < 2, "坞铺到窗右缘")

        # ⑤ 拖高 + 钳制 + 记账
        grip = page.locator("#tdGrip")
        box = grip.bounding_box()
        page.mouse.move(box["x"] + 40, box["y"])
        page.mouse.down()
        page.mouse.move(box["x"] + 40, 900 - 420, steps=4)
        page.mouse.up()
        page.wait_for_timeout(120)
        h1 = page.evaluate(DOCK_H)
        check(200 <= h1 <= 560 and h1 > 300, f"顶边拖高生效并记账({h1}px)")
        saved = page.evaluate("() => parseInt(localStorage.getItem('dock.h'), 10)")
        check(saved == h1, f"localStorage 记的是松手那一刻({saved} == {h1})")

        # ⑥ 坞里发话:accept_all 档直接过桥(带坞的当前目录);cd 就地生效
        page.fill("#tpCmd", "git status")
        page.press("#tpCmd", "Enter")
        page.wait_for_timeout(150)
        calls = page.evaluate("window.__CALLS__")
        check(["terminal_run", "git status", "D:/demo/campus"] in calls,
              "坞的输入行逐字过 terminal_run(带当前目录)")
        page.fill("#tpCmd", "cd sub")
        page.press("#tpCmd", "Enter")
        page.wait_for_timeout(150)
        check(page.evaluate("() => document.getElementById('tpPrompt')"
                            ".textContent.includes('sub')"),
              "cd 就地换提示符(桥记的当前目录落地)")

        # ⑦ 新版本「更新日志」卡(用户图 p1 同款):版本号可点 → 卡片;
        #    提示条那颗按钮也开它;「打开发布页」过 open_url
        page.click("#ver")
        page.wait_for_selector("#updateOverlay.show")
        check(page.locator("#updVer").text_content() == "0.9.173",
              "卡片抬头是版本号")
        check("2026-10-14" in page.locator("#updDate").text_content(),
              "日期来自 release 的 published_at")
        body_txt = page.locator("#updBody").inner_text()
        check("更新检查搬到 GitHub Release" in body_txt
              and "更新日志卡片" in body_txt,
              "整段更新日志都渲染了(不再截 60 字)")
        page.click("#updOpen")
        page.wait_for_timeout(150)
        calls2 = page.evaluate("window.__CALLS__")
        check(any(c[0] == "open_url" and "releases/tag/v0.9.173" in c[1]
                  for c in calls2), "「打开发布页」把 release 地址交给系统浏览器")
        page.click("#updLater")
        page.wait_for_timeout(120)
        check(not page.locator("#updateOverlay").is_visible(), "「以后再说」收起卡片")
        # 提示条那条路:proactive 事件 → 看更新内容 → 同一张卡
        page.evaluate(r"""() => window.__gcodeEvent({type: "proactive",
          kind: "update", version: "0.9.173", date: "2026-10-14",
          url: "https://github.com/MOKO-TB/yinlu-xuezhang/releases/tag/v0.9.173",
          notes: "# v0.9.173\n- 又一件"})""")
        page.wait_for_timeout(150)
        check("0.9.173" in page.locator("#proactiveText").text_content(),
              "提示条写着新版本号")
        check(page.locator("#proactiveGo").text_content() == "看更新内容",
              "那颗按钮改成「看更新内容」(不再直接跳走)")
        page.click("#proactiveGo")
        page.wait_for_timeout(150)
        check(page.locator("#updateOverlay").is_visible(), "按钮开的是更新日志卡")

        # ⑦ 扩展页(底座第六页,v0.9.172+):五区渲染 + **没装上的一条可见**
        #    (此前只有 console.warn)+ 打开数据目录过桥
        page.click("#updLater")            # 先把上一节开着的更新卡收掉
        page.wait_for_timeout(120)
        page.evaluate('() => openPanel("side:extensions")')   # 确保面板开 + 切到扩展页
        page.wait_for_timeout(250)
        check(page.locator('.sp-page[data-sp="extensions"]').is_visible(), "扩展页打开")
        caps = page.eval_on_selector_all("#extBody .ext-cap",
                                         "els => els.map(e => e.textContent)")
        check(len(caps) == 5, f"五区标题都在(实际 {caps})")
        check(any("侧边栏扩展" in c for c in caps) and any("技能渠道" in c for c in caps),
              "侧边栏扩展与技能渠道两区在场")
        body_txt = page.locator("#extBody").inner_text()
        check("broken" in body_txt and "读不出来" in body_txt,
              "**没装上的那条带着原因摊开了**(此前只活在 console 里)")
        check("数据库" in body_txt and "D:/demo/app/extensions" in body_txt,
              "已装扩展与数据目录路径都在")
        page.click("#extOpenDir")
        page.wait_for_timeout(150)
        calls3 = page.evaluate("window.__CALLS__")
        check(any(c[0] == "mydata_open_dir" for c in calls3), "「打开数据目录」过桥")

        check(not errors, f"无页面错误(实际:{errors[:2] or '无'})")
        browser.close()
    shutdown()

    print(f"\n共 {ran} 项,失败 {len(failures)} 项")
    for f in failures:
        print("  ✗", f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
