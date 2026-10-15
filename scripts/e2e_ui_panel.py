# -*- coding: utf-8 -*-
"""v0.9.177「可交互产物」的真浏览器验证(界面那一半的闸)。

跑法(项目根目录,**要带 utf-8**,否则那句 ✓ 在 GBK 控制台炸):

    PYTHONIOENCODING=utf-8 .venv\\Scripts\\python.exe scripts\\e2e_ui_panel.py

**为什么 pytest 不够**:`tests/test_panel_gen.py`(28 支)管的是后端那一道 ——
`minicode/panel.py` 真删、`panel_preview` 真按路径拒。但这一格对用户的主张是界面级的:
"学长写的那份网页在我这里**显示得出来,却执行不了、也摸不到宿主、也出不了网**"。
这句话的真伪只存在于渲染器里 —— 沙箱档位与 CSP 都是浏览器在执行,Python 层看不见。

所以这里的桩 `panel_preview` **故意回一份带敌意的正文**(留着 `<script>` 没删):
验的是**第二道闸单独**能不能立住。两道闸各测各的,才叫两道。

十三个判定点,分三组:

**这一格该显形**
1. 带 `artifact` 的那一步在对话里显出一张「可交互产物」卡;
2. **没点开之前不发 `panel_preview`** —— 几百 KB 不该在流式打字机那条路上被读一遍;
3. 点开取一次,**收起再展开不重复取**。

**这一格关得住**
4. iframe 的 `sandbox` 属性是**空字符串**(不是没写,也不是 `allow-scripts`);
5. 正文显示得出来(不然"没执行"是白测 —— 一张空卡当然什么都执行不了);
6. 那张卡里的 `<script>` **没执行**(它想插进自己文档的那个节点不存在);
7. 它**攀不到宿主**(`window.parent.__ESCAPED` 与 `window.top.__ESCAPED` 都没写成);
8. 它想用一张**普通 `<img>`** 外联 ⇒ 那台服务器一个字节都没收到。

**这一格是我们自己那份,不是浏览器白给的**
9. 注入的 CSP 排在 `<body` 之前(排在后面管不到前面那一段的解析);
10. CSP 内容确实是 `default-src 'none'` 那一条;
11. 那份产物里 `<script>` **还在**(我们**故意没删**)—— 6/8 两条才算是浏览器这一道单独立功。

⚠ **第 8 条有反证,而且是当场撞出来的**:第一版这里直接把桩正文塞进 `srcdoc`,
第 6 条过、第 8 条**红** —— 那台服务器收到了 `/exfil`。原因是漏了
`panel.wrap_document`:CSP 是我们注入的,不注入就等于让浏览器去验一件我们没做的事。
补上之后同一个探针从 1 次命中变成 0 次。**沙箱管脚本与攀宿主,CSP 管外联,
这两道各拦各的,少一道就是留着另一道的洞。**

启动那一段一律走 `scripts/e2e_launch.py`(自己抄一条退路的那件事有闸管着)。
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
    sys.path.insert(0, str(ROOT))          # 要 import minicode.panel(那份 CSP 是真的)
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

# 一份留着三种越法的产物:改标题(证明脚本执行了)、攀 parent(证明能摸宿主)、
# 普通 img 外联(证明能出网)。端口由 `_serve_exfil` 那边拿到后**拼进正文**。
HOSTILE_HEAD = "<title>宿主标题</title><p>表格正文</p>"


def _hostile(port: int) -> str:
    """三种越法各试一次:改自己文档(证明脚本执行了)、攀 parent(证明摸得到宿主)、
    普通 img 外联(证明出得了网)。

    探针写成"往自己文档里插一段字"而不是 `document.title=…`:空档位的 iframe 是
    **不透明源**,整页 `content()` / `evaluate()` 不一定拿得到,而 Playwright 的定位器
    走 CDP target,查得到元素就是查得到 —— 少一层"读不到所以我只能猜"。
    """
    return (HOSTILE_HEAD +
            "<script>document.body.insertAdjacentHTML('beforeend',"
            "\"<p id='ranprobe'>RAN</p>\");"
            "try{window.parent.__ESCAPED=1}catch(e){}"
            "try{window.top.__ESCAPED=1}catch(e){}</script>"
            "<img alt=\"\" src=\"http://127.0.0.1:%d/exfil\">" % port)


STUB_JS = r"""
window.__E2E_CALLS__ = [];
window.__PAYLOAD__ = "";
window.__API__ = {
  get_info: () => Promise.resolve({
    version: "0.9.177", app_name: "引路学长", permission_mode: "confirm",
    permission_modes: [{value: "confirm", label: "变更前确认"}],
    session_key: "s1", model: "m", active_provider: "p",
    providers: [{id: "p", name: "P", model: "m", has_key: true}],
    api_key_configured: true, workspace: "D:/demo", workspace_name: "demo",
    workspace_is_default: false, paths: {app_dir: "D:/demo", writable: true},
    memory_count: 0, memory_user_count: 0, mcp_servers: [], skills: [],
    kb_count: 0, campus_map: null, library_health: {},
    session_metrics: {ok: false, selectable: []},
  }),
  panel_preview: (rel) => {
    window.__E2E_CALLS__.push(["panel_preview", rel]);
    return Promise.resolve({ok: true, name: rel, kind: "html",
                            bytes: (window.__PAYLOAD__ || "").length,
                            html: window.__PAYLOAD__,
                            note: "就地显示(第二道闸单独验:这份没删脚本)",
                            stripped: {}});
  },
};
window.pywebview = {api: new Proxy({}, {
  get: function (_t, k) {
    if (window.__API__[k]) return window.__API__[k];
    return function () {
      window.__E2E_CALLS__.push(String(k));
      return Promise.resolve({});
    };
  }})};
window.__stubReady = true;
"""


def _serve(directory: Path):
    class _QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = partial(_QuietHandler, directory=str(directory))
    httpd = None
    for candidate in range(18620, 18700):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", candidate), handler)
            break
        except OSError:
            continue
    assert httpd is not None, "18620-18699 里找不到可绑定的端口"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return "http://127.0.0.1:%d/index.html" % httpd.server_address[1], httpd


def _serve_exfil(hits: dict):
    """一只"谁来自报家门"的服务器:被请求就在 hits 里记一笔(断言⑥用它)。"""
    class _H(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            hits["n"] = hits.get("n", 0) + 1
            hits["path"] = self.path
            body = bytes.fromhex("89504e470d0a1a0a")
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    for candidate in range(18720, 18800):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", candidate), _H)
            break
        except OSError:
            continue
    else:
        raise AssertionError("18720-18799 里没有可绑定的端口")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, candidate


def _push(page, event: dict) -> None:
    page.evaluate("window.__gcodeEvent(%s)" % json.dumps(event, ensure_ascii=False))


def main() -> int:
    from playwright.sync_api import sync_playwright
    from e2e_launch import launch

    hits: dict = {}
    exfil, exfil_port = _serve_exfil(hits)
    url, site = _serve(FRONTEND)
    failures: list[str] = []
    tally = {"n": 0}

    def check(cond, message):
        tally["n"] += 1
        if not cond:
            failures.append(message)
        print(("  \u2713 " if cond else "  \u2717 ") + message, flush=True)

    try:
        with sync_playwright() as p:
            browser = launch(p)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.add_init_script(STUB_JS)
            page.goto(url, wait_until="load")
            page.wait_for_function("() => !!document.getElementById('chat')")
            # 这一份**只跳过净化那一步**(`<script>` 留着),但走真的 `wrap_document`:
            # CSP 是我们注入的,不注入就等于让浏览器去验一件我们没做的事。
            from minicode import panel as _panel
            page.evaluate("(s) => { window.__PAYLOAD__ = s; }",
                          _panel.wrap_document(_hostile(exfil_port), "html"))

            _push(page, {"type": "tool_start", "name": "file_write",
                         "summary": "复盘.html", "trust": "local",
                         "artifact": "复盘.html"})
            page.wait_for_timeout(250)

            # ① 卡显形
            cards = page.locator(".gencard")
            check(cards.count() == 1, "带 artifact 的那一步显出了一张「可交互产物」卡")
            if cards.count() != 1:
                return _report(tally, failures)

            # ② 没点开之前不出网、不出桥
            check(not [c for c in page.evaluate("() => window.__E2E_CALLS__")
                       if isinstance(c, list) and c[0] == "panel_preview"],
                  "没点开之前**不发** panel_preview")

            # ③ 点开取一次;收起再展开不重复取
            head = page.locator(".gencard .gen-head").first
            head.click()
            page.wait_for_timeout(350)
            calls = lambda: [c for c in page.evaluate("() => window.__E2E_CALLS__")
                             if isinstance(c, list) and c[0] == "panel_preview"]
            check(len(calls()) == 1, "点开取了一次(实得 %d)" % len(calls()))
            head.click()
            head.click()
            page.wait_for_timeout(250)
            check(len(calls()) == 1, "收起再展开**不重复取**(实得 %d)" % len(calls()))

            frame_el = page.locator(".gencard iframe.gen-frame")
            check(frame_el.count() == 1, "预览用的 iframe 在场")
            if frame_el.count() != 1:
                return _report(tally, failures)

            # ④ 空档位
            sb = frame_el.first.get_attribute("sandbox")
            check(sb is not None and sb.strip() == "",
                  "iframe 的 sandbox 是**空档位**(拿到的是 %r)" % sb)

            # ⑤ 脚本没执行 / 攀不到宿主 / 正文照常在
            page.wait_for_timeout(400)
            inner = frame_el.first.content_frame      # 属性,不是方法(FrameLocator)
            try:
                body_text = inner.locator("body").inner_text(timeout=3000)
            except Exception as e:
                body_text = ""
                check(False, "iframe 的内容读得到(读不到则 ⑤⑦ 没法判:%s)"
                      % type(e).__name__)
            check("表格正文" in body_text, "正文显示得出来(不然下面的\"没执行\"是白测)")
            check(inner.locator("#ranprobe").count() == 0,
                  "产物里的 <script> **没执行**(它插进去的那个节点不存在)")
            check(page.evaluate("() => window.__ESCAPED || 0") == 0,
                  "产物**攀不到宿主**(window.parent / window.top 都被拒)")

            # ⑥ 普通 img 外联被 CSP 挡住:那台服务器一个字节都没收到
            page.wait_for_timeout(500)
            check(not hits.get("n"),
                  "产物想外联的那张图**没出网**(hits=%r)" % hits)

            # ⑦ CSP 排在正文之前:整份文档里那条 meta 必须在 `<body` 之前。
            # (`locator("head").innerHTML()` 在这个不透明源里读回来是空的,而
            #  `locator("html").inner_html()` 读得到 —— 所以判"位置"而不是判"在哪个标签里")
            doc_html = ""
            try:
                doc_html = inner.locator("html").inner_html(timeout=3000)
            except Exception as e:
                check(False, "整份文档读得到(读不到则 ⑦ 没法判:%s)" % type(e).__name__)
            at = doc_html.find("Content-Security-Policy")
            check(0 <= at and at < doc_html.find("<body"),
                  "注入的 CSP 排在正文之前(meta 在 %d,<body 在 %d)"
                  % (at, doc_html.find("<body")))
            check("default-src 'none'" in doc_html, "这条 CSP 确实是断外联那一条")
            check(doc_html.lower().find("<script") > doc_html.find("<body"),
                  "这份里那段 <script> 还在(我们**故意没删**,单验浏览器这一道)")
    finally:
        site.shutdown()
        site.server_close()
        exfil.shutdown()
        exfil.server_close()
    return _report(tally, failures)


def _report(tally, failures) -> int:
    print("\n%s  e2e_ui_panel:%d 项检查,%d 项通过" % (
        "PASS" if not failures else "FAIL", tally["n"],
        tally["n"] - len(failures)), flush=True)
    for f in failures:
        print("  ✗ " + f, flush=True)
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
