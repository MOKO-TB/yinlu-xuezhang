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
OUT_DIR = ROOT / "_tmp_dev_check" / "ui-panel"
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
window.__ANNOT_PAYLOAD__ = "";
window.__RUN_PAYLOAD__ = "";
window.__WORK_PAYLOAD__ = "";
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
  panel_preview: (rel, mode) => {
    window.__E2E_CALLS__.push(["panel_preview", rel, mode || "preview"]);
    if (String(rel).indexOf("annotations/") === 0) {
      return Promise.resolve({ok: true, name: rel, kind: "html", mode: "preview",
                              bytes: (window.__WORK_PAYLOAD__ || "").length,
                              html: window.__WORK_PAYLOAD__,
                              note: "工作版预览", stripped: {}});
    }
    if (mode === "annotate") {
      return Promise.resolve({ok: true, name: rel, kind: "html", mode: "annotate",
                              bytes: (window.__ANNOT_PAYLOAD__ || "").length,
                              html: window.__ANNOT_PAYLOAD__,
                              hint: "批注模式：选中文字或点一个元素",
                              note: "", stripped: {}});
    }
    if (mode === "interactive") {
      return Promise.resolve({ok: true, name: rel, kind: "html", mode: "interactive",
                              bytes: (window.__RUN_PAYLOAD__ || "").length,
                              html: window.__RUN_PAYLOAD__,
                              note: "交互版：在隔离沙箱里运行，不能联网/碰宿主",
                              stripped: {}});
    }
    return Promise.resolve({ok: true, name: rel, kind: "html", mode: "preview",
                            bytes: (window.__PAYLOAD__ || "").length,
                            html: window.__PAYLOAD__,
                            note: "就地显示(第二道闸单独验:这份没删脚本)",
                            stripped: {}});
  },
  panel_annotate: (rel, payload) => {
    window.__E2E_CALLS__.push(["panel_annotate", rel, String(payload)]);
    return Promise.resolve({ok: true, source: rel,
                            working: "annotations/批注样例.working.html",
                            text: "我在产物《" + rel + "》上做了一处批注，请**只改这一处**：\n"
                                + "- 选中的文字：「高数 92」\n- 要求：把 92 改成 95\n"
                                + "请改在工作副本 `annotations/批注样例.working.html` 上，"
                                + "已交付的 `" + rel + "` 不要动。"});
  },
  panel_adopt: (rel, working) => {
    window.__E2E_CALLS__.push(["panel_adopt", rel, String(working)]);
    return Promise.resolve({ok: true, source: rel, working: String(working),
                            backup: "annotations/批注样例.backup-20260101-000000.html"});
  },
  panel_compare: (rel, working) => {
    window.__E2E_CALLS__.push(["panel_compare", rel, String(working)]);
    return Promise.resolve({ok: true, mode: "preview",
      original_html: window.__PAYLOAD__,
      working_html: window.__WORK_PAYLOAD__,
      diff: {kind: "text",
             summary: "共 1 处文字变化（改 1 / 增 0 / 删 0）",
             changes: [{type: "changed", id: "",
                        old: "高数 92", new: "高数 95（补考）"}],
             counts: {changed: 1, added: 0, removed: 0}, truncated: 0}});
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

    OUT_DIR.mkdir(parents=True, exist_ok=True)
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
            # 批注档:模型脚本先被 read_and_sanitize 删掉,再注入受信脚本。
            annot_body, _ac, _aw = _panel.read_and_sanitize(
                ("<p id='p1'>高数 92</p><p>英语 88</p>"
                 "<script>parent.__ANN_ESC=1</script>").encode("utf-8"))
            page.evaluate("(s) => { window.__ANNOT_PAYLOAD__ = s; }",
                          _panel.wrap_annotatable(annot_body, "html"))
            # 交互版:模型脚本原样保留,由 sandbox=allow-scripts(无同源)+ CSP 断网。
            # 这段脚本同时做三件事:证明交互真的能改 DOM、试摸宿主、试 fetch 出网。
            run_html = (
                "<style>body{font-family:system-ui;padding:18px}"
                "#bar{height:18px;background:#3FBDAE;border-radius:9px;"
                "transition:width .1s}</style>"
                "<h3>调豆模拟器</h3>"
                "<label>研磨 <input id='r' type='range' min='0' max='100' value='20'>"
                " <b id='out'>20</b></label>"
                "<button id='preset'>预设 70</button>"
                "<div id='bar' style='width:20%'></div>"
                "<script>"
                "var r=document.getElementById('r'),out=document.getElementById('out'),"
                "bar=document.getElementById('bar');"
                "function draw(){out.textContent=r.value;bar.style.width=r.value+'%';}"
                "r.addEventListener('input',draw);draw();"
                "document.getElementById('preset').onclick=function(){r.value=70;draw();};"
                "try{parent.__RUN_ESC=1}catch(e){}"
                "try{fetch('http://127.0.0.1:__PORT__/run')}catch(e){}"
                "try{localStorage.setItem('x','1');window.__RUN_LS=1}"
                "catch(e){window.__RUN_LS=0}"
                "</script>").replace("__PORT__", str(exfil_port))
            page.evaluate("(s) => { window.__RUN_PAYLOAD__ = s; }",
                          _panel.wrap_interactive(run_html))
            page.evaluate("(s) => { window.__WORK_PAYLOAD__ = s; }",
                          _panel.wrap_document(
                              "<p>工作版正文</p><p>高数 95（补考）</p>", "html"))

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

            # ------------------------------------------------ 批注模式
            _push(page, {"type": "tool_start", "name": "file_write",
                         "summary": "批注样例.html", "trust": "local",
                         "artifact": "批注样例.html"})
            page.wait_for_timeout(250)
            cards = page.locator(".gencard")
            check(cards.count() == 2, "批注样例又出了一张产物卡(实得 %d)" % cards.count())
            if cards.count() != 2:
                return _report(tally, failures)
            ann_card = cards.nth(1)
            ann_card.locator(".gen-annot").click()
            page.wait_for_timeout(600)
            ann_frame = ann_card.locator("iframe.ann-frame")
            check(ann_frame.count() == 1, "点「批注」后出现可批注 iframe")
            if ann_frame.count() != 1:
                return _report(tally, failures)
            sb = ann_frame.first.get_attribute("sandbox")
            check(sb is not None and "allow-scripts" in sb,
                  "批注 iframe 给了 allow-scripts(拿到的是 %r)" % sb)
            check("allow-same-origin" not in (sb or ""),
                  "批注 iframe **没有**给同源")
            page.wait_for_timeout(400)
            check(page.evaluate("() => window.__ANN_ESC || 0") == 0,
                  "模型脚本在批注档里仍然没执行")
            try:
                inner = ann_frame.first.content_frame
                body_text = inner.locator("body").inner_text(timeout=3000)
            except Exception as e:
                body_text = ""
                check(False, "批注 iframe 内容读得到(%s)" % type(e).__name__)
            check("批注模式" in body_text, "受信批注脚本真的跑起来了(hint 在)")

            # 模拟子页选中一段文字:父页应把片段填进批注条
            page.evaluate("""() => {
              const f = document.querySelector('iframe.ann-frame');
              window.dispatchEvent(new MessageEvent('message', {
                source: f.contentWindow,
                data: {type: 'ann-select', mode: 'text', text: '高数 92',
                       selector: 'p:nth-child(1)', tag: 'p', block: 'p1'}
              }));
            }""")
            page.wait_for_timeout(200)
            snippet = ann_card.locator(".ann-snippet").inner_text()
            check("高数 92" in snippet, "选中片段回填到了批注条:%r" % snippet)
            check("p1" in snippet, "Office 块 id 没显示在批注条:%r" % snippet)
            ann_card.locator(".ann-input").fill("把 92 改成 95，并注明补考")
            ann_card.locator(".ann-foot .b-yes").click()
            page.wait_for_timeout(300)
            calls = page.evaluate("() => window.__E2E_CALLS__")
            ann_calls = [c for c in calls if isinstance(c, list)
                         and c[0] == "panel_annotate"]
            check(len(ann_calls) == 1, "panel_annotate 调了一次(实得 %d)" % len(ann_calls))
            if ann_calls:
                payload = json.loads(ann_calls[0][2])
                check(payload.get("text") == "高数 92", "选中文字原样带回:%r" % payload)
                check(payload.get("selector") == "p:nth-child(1)", "选择器原样带回")
                check(payload.get("block") == "p1", "Office 块 id 没带回")
                check("补考" in payload.get("instruction", ""), "修改要求原样带回")
            check("工作副本" in ann_card.inner_text(), "发完后卡片显示了工作副本路径")
            check("只改这一处" in page.locator("#chat").inner_text(),
                  "发回对话的那条用户消息在场")
            page.screenshot(path=str(OUT_DIR / "annotate.png"))

            # 原版 vs 工作版:两栏只读预览
            compare_btn = ann_card.locator(".ann-foot button", has_text="对比工作版")
            check(compare_btn.count() == 1, "批注后没有「对比工作版」按钮")
            compare_btn.click()
            page.wait_for_timeout(500)
            check(page.locator(".cmp-overlay").count() == 1, "没有打开对比视图")
            check(page.locator(".cmp-pane").count() == 2, "对比视图不是两栏")
            cmp_frames = page.locator(".cmp-pane iframe")
            check(cmp_frames.count() == 2, "对比视图没有两个 iframe")
            check(page.locator(".cmp-cap", has_text="已交付版").count() == 1,
                  "对比视图缺已交付版标题")
            check(page.locator(".cmp-cap", has_text="工作版").count() == 1,
                  "对比视图缺工作版标题")
            work_text = cmp_frames.nth(1).content_frame.locator("body").inner_text()
            check("工作版正文" in work_text, "对比视图的工作版内容不对")
            diff_text = page.locator(".cmp-diff").inner_text()
            check("共 1 处" in diff_text, "对比视图没有差异摘要")
            check("高数 95" in diff_text, "对比视图没有改后内容")
            calls = page.evaluate("() => window.__E2E_CALLS__")
            cmp_calls = [c for c in calls if isinstance(c, list)
                         and c[0] == "panel_compare"]
            check(len(cmp_calls) == 1, "panel_compare 调用次数不对")
            page.screenshot(path=str(OUT_DIR / "compare.png"))
            page.locator(".cmp-head button", has_text="关闭").click()
            page.wait_for_timeout(200)
            check(page.locator(".cmp-overlay").count() == 0, "对比视图没关掉")

            # 采用工作版:二次确认 → panel_adopt(rel, working)
            ann_card.locator(".ann-foot .b-always").click()
            page.wait_for_selector("#appConfirmOverlay.show")
            page.click("#btnAppOk")
            page.wait_for_timeout(300)
            calls = page.evaluate("() => window.__E2E_CALLS__")
            adopt_calls = [c for c in calls if isinstance(c, list)
                           and c[0] == "panel_adopt"]
            check(len(adopt_calls) == 1, "采用工作版调了一次(实得 %d)" % len(adopt_calls))
            if adopt_calls:
                check(adopt_calls[0][1] == "批注样例.html", "采用的是这张产物")
                check("annotations/" in adopt_calls[0][2], "采用的是工作副本路径")
            check("已采用工作版" in ann_card.inner_text(), "采用后卡片显示了已采用")
            page.screenshot(path=str(OUT_DIR / "adopt.png"))

            # ------------------------------------------------ 交互版(Gen UI)
            _push(page, {"type": "tool_start", "name": "file_write",
                         "summary": "咖啡模拟器.html", "trust": "local",
                         "artifact": "咖啡模拟器.html"})
            page.wait_for_timeout(250)
            cards = page.locator(".gencard")
            check(cards.count() == 3, "交互版又出了一张产物卡(实得 %d)" % cards.count())
            if cards.count() != 3:
                return _report(tally, failures)
            run_card = cards.nth(2)
            run_card.locator(".gen-run").click()
            page.wait_for_timeout(700)
            run_frame = run_card.locator("iframe.run-frame")
            check(run_frame.count() == 1, "点「运行」后出现交互 iframe")
            if run_frame.count() != 1:
                return _report(tally, failures)
            sb = run_frame.first.get_attribute("sandbox")
            check(sb is not None and "allow-scripts" in sb,
                  "交互 iframe 给了 allow-scripts(拿到的是 %r)" % sb)
            check("allow-same-origin" not in (sb or ""),
                  "交互 iframe **没有**给同源")
            inner = run_frame.first.content_frame
            try:
                out0 = inner.locator("#out").inner_text(timeout=3000)
            except Exception as e:
                out0 = ""
                check(False, "交互 iframe 内容读得到(%s)" % type(e).__name__)
            check(out0 == "20", "初始数值读得到(实得 %r)" % out0)
            inner.locator("#r").evaluate(
                "r => { r.value = '10';"
                " r.dispatchEvent(new Event('input', {bubbles: true})); }")
            page.wait_for_timeout(120)
            check(inner.locator("#out").inner_text() == "10",
                  "拖动滑条后数值实时变了")
            check(inner.locator("#bar").evaluate("el => el.style.width") == "10%",
                  "滑条也改了图(条形宽度 10%)")
            inner.locator("#preset").click()
            page.wait_for_timeout(120)
            check(inner.locator("#out").inner_text() == "70",
                  "预设按钮也能改数值")
            check(page.evaluate("() => window.__RUN_ESC || 0") == 0,
                  "交互版**攀不到宿主**(parent 属性没写成)")
            check(inner.locator("body").evaluate("() => window.__RUN_LS") == 0,
                  "交互版的 localStorage 被拒(不透明源)")
            check(not hits.get("n"),
                  "交互版里的 fetch **没出网**(hits=%r)" % hits)
            page.screenshot(path=str(OUT_DIR / "run.png"))
            # 放大/全屏:只把卡 fixed 铺满,不搬 iframe —— 模拟器状态不能丢
            fs_btn = run_card.locator(".gen-fs")
            check(fs_btn.count() == 1, "产物卡没有放大按钮")
            fs_btn.click()
            page.wait_for_timeout(300)
            check(page.locator(".gencard.fullscreen").count() == 1, "没有进入全屏")
            check(page.locator(".gen-fs-backdrop").count() == 1, "全屏没有遮罩")
            check(fs_btn.inner_text() == "还原", "放大按钮没变成还原")
            check(inner.locator("#out").inner_text() == "70", "全屏后模拟器状态丢了")
            page.screenshot(path=str(OUT_DIR / "run-fullscreen.png"))
            fs_btn.click()
            page.wait_for_timeout(300)
            check(page.locator(".gencard.fullscreen").count() == 0, "没有退出全屏")
            check(run_card.locator("iframe.run-frame").count() == 1,
                  "退出全屏把 iframe 弄没了")
            run_card.locator(".gen-head").click()
            page.wait_for_timeout(250)
            check(run_card.locator("iframe.run-frame").count() == 0,
                  "收起卡片会摘掉 iframe(停止运行)")
            page.screenshot(path=str(OUT_DIR / "run-stopped.png"))
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
