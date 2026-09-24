# -*- coding: utf-8 -*-
"""v0.9.39 p1 前端回归:输入框右键「粘贴」不再弹那条浏览器授权横条。

用户看图 p1 点名:「右键粘贴到对话框左上角显示这个提示希望不要出现太违和感了」——
WebView2 弹的是「http://127.0.0.1:25460 想要 查看复制到剪贴板的文本和图像 / 阻止 / 允许」,
触发者是 `navigator.clipboard.readText()` / `read()`。这一场钉的就是**这一对函数有没有
被叫到**:页面里把 `navigator.clipboard` 换成一只会记账的假对象,点一次菜单里的「粘贴」,
然后看账本 —— 走本机桥的那几种情况账本必须是空的,只有该退回浏览器的时候才许有。

规矩还是那一条:「是要真正能使用的不能只用于装饰」。所以五种结果各跑一遍真点击:
1. 文字 → 打进输入框,浏览器剪贴板一次没碰;
2. 图片 → 交回本机落 uploads/(add_dropped_attachments 收到的就是 {name,b64}),
   附件条真长出那张卡片;
3. 空 → 只说「剪贴板是空的」,**不**偷偷去碰浏览器(那条会弹横条);
4. 读不到(unavailable)→ 这才允许退回浏览器(账本里该出现 readText);
5. 本机桥压根不在(网页版/非 Windows)→ 走浏览器,功能仍然可用。

跑法(项目根目录):
    PYTHONIOENCODING=utf-8 .venv\\Scripts\\python.exe scripts\\e2e_ui_v0939_p1.py
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

# 2x2 透明 PNG,够真(后端真的能落盘、前端真的能出缩略图)
PNG_B64 = ("iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAC0lEQVR4nGNgAAIAAAUA"
           "Afo/hiQAAAAASUVORK5CYII=")

STUB_JS = """
window.__NAV__ = [];            // 浏览器剪贴板被叫到第几次、各是哪个方法
window.__CALLS__ = [];          // 交回本机的调用(含参数原文)
window.__CLIP__ = {ok: false, source: "unavailable", msg: "夹具还没设置"};
window.__LEGACY_TEXT__ = "浏览器读到的文字";
window.__PNG_B64__ = "@@PNG@@";

/* 把 navigator.clipboard 换成记账的假对象:它一被读就是一次违和的横条。
   属性是 getter,所以每次取到的都是"当下"的实现 —— 与真浏览器一样是异步的。 */
Object.defineProperty(window.navigator, "clipboard", {
  configurable: true,
  get() {
    return {
      readText: () => { window.__NAV__.push("readText");
                        return Promise.resolve(window.__LEGACY_TEXT__); },
      read: () => { window.__NAV__.push("read");
                    return Promise.reject(new Error("e2e: 图片这条路不测")); },
      writeText: (t) => { window.__NAV__.push("writeText"); return Promise.resolve(); },
    };
  }
});

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
    sandbox: {}, vision: true, campus_map: {buildings: []}, library_health: {},
    skills: [], kb_count: 0,
    session_metrics: {ok: false, selectable: []}
  }),
  /* 本机桥:形状与 minicode/clipboard.read_clipboard() 逐字一致 —— 这一场
     就是拿后端真实的四种 source 喂它,不是随手编的字典。 */
  read_clipboard: () => Promise.resolve(JSON.parse(JSON.stringify(window.__CLIP__))),
  add_dropped_attachments: (jsonStr) => {
    window.__CALLS__.push(["add_dropped_attachments", jsonStr]);
    const items = JSON.parse(jsonStr).map(x => ({
      name: x.name, path: "uploads/" + x.name, kind: "image",
      size: Math.round(x.b64.length * 0.75), mime: "image/png",
      preview: "data:image/png;base64," + x.b64,
      local: "D:/demo/campus/uploads/" + x.name}));
    return Promise.resolve({ok: true, items: items, errors: []});
  },
};
/* 其余方法一律回 {ok: true}:这一场只关心粘贴那一条,别的入口不测。 */
window.pywebview = {api: new Proxy(base, {
  get: (t, k) => (k in t) ? t[k]
    : ((...a) => { window.__CALLS__.push([k, ...a]); return Promise.resolve({ok: true}); })
})};
""".replace("@@PNG@@", PNG_B64)


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

    def paste(page):
        """真点一次右键菜单里的「粘贴」:先清账本,再等异步落地。"""
        page.evaluate("() => { window.__NAV__ = []; window.__CALLS__ = []; }")
        page.click("#input", button="right")
        page.wait_for_selector('#ctxMenu button[data-act="paste"]', state="visible")
        page.click('#ctxMenu button[data-act="paste"]')
        page.wait_for_timeout(280)

    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1280, "height": 860})
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}")
                if m.type == "error" else None)
        # 404 要把 URL 记下来才知道滤得掉的是 favicon 而不是真丢了一个资源
        page.on("response", lambda r: errors.append(f"http {r.status}: {r.url}")
                if r.status >= 400 else None)
        page.add_init_script(STUB_JS)
        page.goto(url)
        page.wait_for_timeout(900)
        check(page.query_selector("#input") is not None, "页面起来了(输入框在)")

        # 1) 文字:桥给什么就打什么,浏览器剪贴板一次都不该被碰
        page.evaluate("() => { window.__CLIP__ = {ok: true, source: 'text',"
                      " text: '从本机剪贴板来的一句话',"
                      " files: [], msg: ''}; }")
        page.fill("#input", "")
        paste(page)
        check("从本机剪贴板来的一句话" in page.input_value("#input"),
              "① 文字真打进输入框")
        check(page.evaluate("window.__NAV__") == [],
              f"① 没叫浏览器读剪贴板(账本:{page.evaluate('window.__NAV__')})")

        # 2) 图片:字节交回本机,附件条长出那张卡片
        page.evaluate("() => { window.__CLIP__ = {ok: true, source: 'image', text: '',"
                      " files: [{name: '粘贴图片-20260924-163000.png',"
                      " b64: window.__PNG_B64__}], msg: ''}; }")
        paste(page)
        calls = page.evaluate("window.__CALLS__")
        up = [c for c in calls if isinstance(c, list) and c[0] == "add_dropped_attachments"]
        check(len(up) == 1, "② 图片真的交回本机上传那一条路")
        if up:
            items = json.loads(up[0][1])
            check([set(x) for x in items] == [{"name", "b64"}],
                  f"② 交回去的就是 {{name,b64}} 这个形状:{items}")
            check(items[0]["b64"] == PNG_B64, "② 字节一个不差(不是重新编码的替身)")
        check(page.evaluate("() => { const n = document.querySelector('#attachBar .att .nm');"
                            " return n ? n.textContent : ''; }") == "粘贴图片-20260924-163000.png",
              "② 附件条真画出那张卡片")
        check(page.evaluate("window.__NAV__") == [],
              "② 图片这条路也没叫浏览器读剪贴板")

        # 3) 空:一句实话,不许偷偷退回会弹横条的那条路
        page.evaluate("() => { window.__CLIP__ = {ok: false, source: 'empty', text: '',"
                      " files: [], msg: '剪贴板是空的'}; }")
        paste(page)
        check("剪贴板是空的" in page.evaluate(
            "() => Array.from(document.querySelectorAll('#toasts .toast')).map(e => e.dataset.msg).join('|')"),
              "③  toast 说的是那句实话")
        check(page.evaluate("window.__NAV__") == [], "③ 空剪贴板没退回浏览器")

        # 4) 读不到:这才该退回浏览器(功能不能因为换路就坏掉)
        page.evaluate("() => { window.__CLIP__ = {ok: false, source: 'unavailable',"
                      " text: '', files: [], msg: '剪贴板正被别的程序占用'}; }")
        page.fill("#input", "")
        paste(page)
        check(page.evaluate("window.__NAV__")[:1] == ["readText"],
              f"④ 桥读不到时退回浏览器那一条(账本:{page.evaluate('window.__NAV__')})")
        check("浏览器读到的文字" in page.input_value("#input"),
              "④ 退回后仍然粘得上(功能没因为改版坏掉)")

        # 5) 本机桥不在(网页版/非 Windows):老路照走
        page.evaluate("() => { window.pywebview.api.read_clipboard = undefined; }")
        page.fill("#input", "")
        paste(page)
        check("readText" in page.evaluate("window.__NAV__"),
              "⑤ 没有本机桥时仍然能粘贴(走浏览器)")

        browser.close()

    shutdown()
    # favicon 在 http:// 直开时本来就常常没有;除此之外一个 404 都不许有
    bad_http = [e for e in errors if e.startswith("http ")]
    check(all("favicon" in e for e in bad_http),
          f"除 favicon 外没有丢资源(拿到 {len(bad_http)} 条):{bad_http[:3]}")
    real_errors = [e for e in errors
                   if "favicon" not in e and "Failed to load resource" not in e]
    check(not real_errors, f"控制台无报错(拿到 {len(real_errors)} 条){real_errors[:3]}")

    print()
    if failures:
        for f in failures:
            print("FAIL:", f)
        print(f"\n{len(failures)} 项不通过")
        return 1
    print("PASS: 右键粘贴四种结果全走本机桥,浏览器那条只在桥不在/读不到时才碰")
    return 0


if __name__ == "__main__":
    sys.exit(main())
