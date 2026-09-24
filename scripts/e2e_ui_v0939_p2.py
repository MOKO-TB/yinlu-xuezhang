# -*- coding: utf-8 -*-
"""v0.9.39 p2 前端回归:主题一换,系统标题栏真的跟着换了。

用户看图 p2 点名:「第二个应用的最上面那一行与应用界面不符合」。修法是**保留系统
标题栏、只改色与文字**,颜色由本机 `minicode/window_chrome.py` 涂,而"当下是深色
还是浅色"这句话只有前端知道 —— 所以要它在前端每次换主题(含开机那一次)时报过来。

这一场钉的是**这条线通不通**,全部是真点击、真刷新:
1. 开机(浅色):`set_chrome` 被叫到一次,参数 false;
2. 开机(上次留下深色 localStorage):`html.dark` 在,而且报的是 true —— 漏了这一条
   就是"深色界面顶着一面白标题栏",正是用户看到的那一眼;
3. 点「🌙 深色」:再报一次,值翻转,按钮文案跟着翻;
4. 本机桥回答"rejected"(窗口没就绪/老系统/非 Windows):主题照样切,不弹未捕获错误
   —— 它只是装饰,不能把界面拖下水;
5. 本机桥压根没有这个方法(网页版/未装本机桥):页面照常换主题,一句错都不报。

跑法(项目根目录):
    PYTHONIOENCODING=utf-8 .venv\\Scripts\\python.exe scripts\\e2e_ui_v0939_p2.py
"""
from __future__ import annotations

import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

STUB_JS = """
window.__CHROME__ = [];         // 报给本机的每一次 set_chrome 的参数
window.__REJECT__ = false;      // 运行时可改:让本机桥回答"这次没改成"
@@BOOT@@
const base = {
  get_info: () => Promise.resolve({
    version: "0.9.39", app_name: "引路学长",
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
  /* 桥的回答形状与 window_chrome.apply() 逐字一致:真机 Win10 上会有 failed 三条,
     那只是"系统不接受这几项",前端什么都不用做 —— 所以这里也只需回得像个字典。 */
  set_chrome: (dark) => {
    window.__CHROME__.push(dark);
    if (window.__REJECT__) return Promise.reject(new Error("窗口还没就绪,标题栏暂不改"));
    return Promise.resolve({ok: true, applied: ["immersive-dark-mode", "border",
                                                "caption", "text", "title"],
                            failed: [], msg: ""});
  },
};
/* 其余方法一律回 {ok: true}:这一场只关心标题栏那一条线。 */
window.pywebview = {api: new Proxy(base, {
  get: (t, k) => {
    if (k === "set_chrome" && window.__NO_CHROME__) return undefined;
    return (k in t) ? t[k]
      : ((...a) => Promise.resolve({ok: true}));
  }
})};
"""

BOOT_DARK = 'localStorage.setItem("gcode-theme", "dark");'
BOOT_LIGHT = 'localStorage.setItem("gcode-theme", "light");'
NO_CHROME = "window.__NO_CHROME__ = true;"


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

    with sync_playwright() as p:
        browser = _launch(p)

        def open_page(boot, extra=""):
            page = browser.new_page(viewport={"width": 1280, "height": 860})
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}")
                    if m.type == "error" else None)
            page.on("response", lambda r: errors.append(f"http {r.status}: {r.url}")
                    if r.status >= 400 else None)
            page.add_init_script(STUB_JS.replace("@@BOOT@@", boot + "\n" + extra))
            page.goto(url)
            page.wait_for_timeout(900)
            return page

        def dark(page):
            return page.evaluate(
                "() => document.documentElement.classList.contains('dark')")

        def theme_text(page):
            # 原本量的是整颗按钮的 textContent("☀️ 浅色"),断的是"这一格给的是
            # 下一步能切去的那一档"。p3 之后它变成"图标一行 + 标签一行"两行结构,
            # 所以这里把两行拼回原来那句话再比 —— **期望串一个字没改**,改的是取法;
            # 若哪天只剩图标、标签丢了,拼出来就不等于"☀️ 浅色",这条照样红。
            return page.evaluate("""() => {
                const b = document.getElementById('btnTheme');
                const glyph = (b.firstChild.textContent || '').trim();
                const cap = b.querySelector('.cap');
                return cap ? glyph + ' ' + cap.textContent.trim() : glyph;
            }""")

        # ---- 1) 开机浅色:桥要被问到一次,且问的是 false
        page = open_page(BOOT_LIGHT)
        check(page.query_selector("#input") is not None, "① 页面起来了(输入框在)")
        check(page.evaluate("window.__CHROME__") == [False],
              f"① 开机就把主题报给本机了:{page.evaluate('window.__CHROME__')}")
        check(dark(page) is False, "① 浅色开机:html 上没有 dark")
        page.close()

        # ---- 2) 开机深色:那一面白标题栏就是这张图漏掉的样子
        page = open_page(BOOT_DARK)
        check(dark(page) is True, "② 上次留下深色 → 界面是深色")
        check(page.evaluate("window.__CHROME__") == [True],
              f"② 深色也报了,报的是 true:{page.evaluate('window.__CHROME__')}")
        check(theme_text(page) == "☀️ 浅色",
              "② 按钮给的是「下一步能切去的那一档」:" + repr(theme_text(page)))

        # ---- 3) 点一下切换:再报一次,值翻转
        page.click("#btnTheme")
        page.wait_for_timeout(260)
        check(page.evaluate("window.__CHROME__") == [True, False],
              f"③ 点「☀️ 浅色」又报了一次且翻了:{page.evaluate('window.__CHROME__')}")
        check(dark(page) is False, "③ 界面真的切回浅色了")
        check(theme_text(page) == "🌙 深色", "③ 按钮文案跟着翻")

        # ---- 4) 本机桥说"这次没改成":界面不受牵连
        page.evaluate("() => { window.__REJECT__ = true; window.__CHROME__ = []; }")
        page.click("#btnTheme")
        page.wait_for_timeout(260)
        check(page.evaluate("window.__CHROME__") == [True],
              "④ 桥被拒的那一次仍然报到了(不报=以后没人能重试)")
        check(dark(page) is True, "④ 标题栏没改成,主题照样切完了")
        check(theme_text(page) == "☀️ 浅色", "④ 界面没因为桥失败而停在半路")
        page.close()

        # ---- 5) 桥没有这个方法(网页版):一句错都不许有
        page = open_page(BOOT_LIGHT, extra=NO_CHROME)
        check(page.evaluate("window.__CHROME__") == [],
              "⑤ 没有本机桥时不去调用它")
        page.click("#btnTheme")
        page.wait_for_timeout(260)
        check(dark(page) is True, "⑤ 没有本机桥也切得动主题(功能没被绑死)")
        page.close()

        browser.close()

    shutdown()
    bad_http = [e for e in errors if e.startswith("http ")]
    check(all("favicon" in e for e in bad_http),
          f"除 favicon 外没有丢资源(拿到 {len(bad_http)} 条):{bad_http[:3]}")
    real_errors = [e for e in errors
                   if "favicon" not in e and "Failed to load resource" not in e
                   and "窗口还没就绪" not in e]      # ④ 那条是本场自己造的拒绝
    check(not real_errors, f"控制台无报错(拿到 {len(real_errors)} 条){real_errors[:3]}")

    print()
    if failures:
        for f in failures:
            print("FAIL:", f)
        print(f"\n{len(failures)} 项不通过")
        return 1
    print("PASS: 开/关主题四个路径都把当下状态报给了本机,报不动的时候界面也不受牵连")
    return 0


if __name__ == "__main__":
    sys.exit(main())
