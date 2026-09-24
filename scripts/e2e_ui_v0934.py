# -*- coding: utf-8 -*-
"""v0.9.34 前端回归:右侧「本次会话」活动面板必须**真能用**(用户看图 p2 立的规矩)。

这支测试的口径来自用户原话:「是要真正能使用的不能只用于装饰」。落到断言上就是三件事,
每一件都在浏览器里验,而不是在单元测试里验(单元测试只证明后端算得对,证明不了"这一行
点下去真会发生事情"):

1. **面板里出现的每一行都由真后端算出来**。payload 走真 `bridge.get_activity()`:
   子代理是真 `task` 工具真跑出来的(带 FakeLLM 的两路并行)、产出文件是真 `write_file`
   写到磁盘上的、失败是真 `edit_file` 报出来的(不是手写一句"错误: …"的夹具)、
   来源是真 `provenance.wrap()` 围栏里带出来的网址。前端只做"照着画"。
2. **每一行的点击都有后果**:产出行叫 `reveal_file(后端给的那个 path)`、来源行叫
   `open_url(那个 url)`、查询行把话填回 `#input`、子代理行展开真报告。反过来也验:
   文件在磁盘上被删掉之后 ↻,那一行必须自己改口成「已不在磁盘」并**失去**可点的样子。
3. **关着就不敲桥**:面板收起时来工具事件,`get_activity` 的调用数不许再涨。

跑法(项目根目录,需要 PYTHONIOENCODING=utf-8 否则中文断言在 GBK 控制台炸):
    PYTHONIOENCODING=utf-8 .venv\\Scripts\\python.exe scripts\\e2e_ui_v0934.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _assistant(calls):
    from minicode.llm import AssistantTurn, ToolCall
    return AssistantTurn(text="", tool_calls=[
        ToolCall(id=c["id"], name=c["name"], arguments=json.dumps(c["args"], ensure_ascii=False))
        for c in calls])


def build_payloads():
    """跑真工具 → 真磁盘 → 真 bridge.get_activity():返回(初版, 删文件+记一条记忆后)。"""
    from minicode import paths, provenance
    from minicode.config import Config, Provider
    from minicode.gui_bridge import create_app
    from minicode.llm import AssistantTurn
    from minicode.memory import memory as memory_tool
    from minicode.subagents import build_task_tool
    from minicode.tools import fs
    from minicode.tools.base import ToolContext
    from minicode.tools.registry import build_default_registry
    from tests.test_gui_bridge import FakeLLM, FakeWindow

    tmp = Path(tempfile.mkdtemp(prefix="yx-v0934-"))
    ws = tmp / "ws"
    ws.mkdir()
    paths.APP_DIR = tmp / "app"
    cfg = Config(providers=[Provider(id="test", name="Test",
                                      base_url="https://example.invalid/v1",
                                      api_key="test-key", model="test-model")],
                 active_provider_id="test")
    bridge, ui, _agent, _session = create_app(
        cfg, ws, llm=FakeLLM([]), sessions_dir=tmp / "sessions",
        config_path=tmp / "config.json")
    ui.attach(FakeWindow())
    ctx = ToolContext(workspace=ws, config=cfg)
    registry = build_default_registry()
    session = bridge.session

    def record(calls):
        """按真实消息形状写进会话:一条 assistant(带 N 个 tool_calls)+ N 条 tool 回话。"""
        session.add_user("把答辩的东西整理一下")
        session.add_assistant(_assistant(calls))
        for c in calls:
            if "result" in c:
                session.add_tool_result(c["id"], c["name"], c["result"])

    # ---- 1. 子代理:真并行(explore + review 各跑一次,报告是真 subagent 写的)----
    sub_llm = FakeLLM([
        AssistantTurn(text="答辩逻辑分三段:问题—做法—结论;PPT 目录已对齐。"),
        AssistantTurn(text="权限门有 3 处未覆盖:重名路径、外部路径、凭据文件。"),
    ])
    task_tool = build_task_tool(ctx, sub_llm, ui, cfg, registry)
    report = task_tool.handler(ctx, type=["explore", "review"],
                               task=["梳理答辩稿的逻辑线", "检查权限确认的门"])
    assert "## 1." in report and "## 2." in report, f"并行子代理报告不对:{report[:120]}"
    record([{"id": "t1", "name": "task",
             "args": {"type": ["explore", "review"],
                      "task": ["梳理答辩稿的逻辑线", "检查权限确认的门"]},
             "result": report}])
    # 还有一个刚派出去、没回来的(面板上必须转着,角标要数到它)
    record([{"id": "t2", "name": "task",
             "args": {"type": "explore", "task": "查一下绩点算错的报告在哪"}}])

    # ---- 2. 产出:真写盘 + 真失败(错误串由 edit_file 本体吐出来)----
    ok_result = fs.write_file(ctx, path="答辩提纲.md",
                              content="# 答辩提纲\n\n1. 问题\n2. 做法\n3. 结论\n")
    record([{"id": "w1", "name": "write_file",
             "args": {"path": "答辩提纲.md", "content": "# 答辩提纲…"},
             "result": ok_result}])
    assert (ws / "答辩提纲.md").exists(), "写盘没成,这支测试的产出部分就没意义了"
    (ws / "旧笔记.md").write_text("原始内容\n", encoding="utf-8")   # 不经工具,免得并成一行
    bad_result = fs.edit_file(ctx, path="旧笔记.md", old_string="根本不存在的串",
                              new_string="换掉")
    assert bad_result.startswith("错误"), f"edit_file 的错误口径变了:{bad_result}"
    record([{"id": "e1", "name": "edit_file",
             "args": {"path": "旧笔记.md", "old_string": "根本不存在的串",
                      "new_string": "换掉"},
             "result": bad_result}])

    # ---- 3. 网页动作 + 进了上下文的来源(围栏由真 provenance 包)----
    crawled = provenance.wrap(
        "web_crawl", {"url": "https://jwc.gzasc.edu.cn/xjdc"},
        "学籍登记表内页。正文里也有链接 https://ads.example/spam,但那不算来源。")
    record([{"id": "u1", "name": "web_crawl",
             "args": {"url": "https://jwc.gzasc.edu.cn/xjdc"}, "result": crawled}])
    # 查询行:关键词里塞一段 HTML —— 面板必须当字符串画出来,不许变成标签
    record([{"id": "u2", "name": "web_search",
             "args": {"query": "国家励志奖学金 <img src=x onerror=alert(1)> 条件"},
             "result": "1. 结果标题 https://a.example/1"}])
    record([{"id": "u3", "name": "search_knowledge",
             "args": {"query": "绩点算错"}, "result": "知识库无匹配"}])

    # v0.9.37 起「没有回话」分两档:会话还在跑 = 在跑,会话已经停了 = 已中断。
    # 这里那条 t2 的语义就是"刚派出去还没回来",所以取数前把 busy 立起来,
    # 否则角标那条断言与"最新那行是在跑"会一起红(那是口径变了,不是面板坏了)。
    bridge._current.busy = True
    first = bridge.get_activity()
    assert first["ok"] and not first["empty"], f"初版 payload 是空的:{first}"

    # ---- 4. 第二版:用户把刚生成的文件删了 + 学长真记了一条记忆 ----
    (ws / "答辩提纲.md").unlink()
    mem_result = memory_tool(ctx, action="save", category="project",
                             content="答辩 PPT 用 16:9 版式", scope="project")
    record([{"id": "m1", "name": "memory",
             "args": {"action": "save", "content": "答辩 PPT 用 16:9 版式",
                      "category": "project", "scope": "project"},
             "result": mem_result}])
    second = bridge.get_activity()
    assert second["ok"], second
    return first, second


ACT_A, ACT_B = build_payloads()


def _rows(act, key):
    return {r["key"]: r for r in next(g for g in act["groups"] if g["key"] == key)["items"]}


STUB_JS = """
window.__E2E_CALLS__ = [];
window.__ACT_A__ = @@A@@;
window.__ACT_B__ = null;          // 第二版由 Python 在页面开着的时候推进来
window.__ACT_MODE__ = "a";
window.__ACT_CALLS__ = 0;
const push = (name, args) => window.__E2E_CALLS__.push([name, args]);
window.pywebview = {
  api: {
    get_info: () => Promise.resolve({
      version: "0.9.34", app_name: "引路学长",
      permission_mode: "confirm",
      permission_modes: [{value: "confirm", label: "变更前确认"}],
      session_key: "s1", model: "test-model", active_provider: "test",
      providers: [{id: "test", name: "Test", model: "test-model", has_key: true}],
      api_key_configured: true, workspace: "D:/demo/campus",
      workspace_name: "campus", workspace_is_default: true,
      memory_count: 0, memory_user_count: 0, mcp_servers: [],
      sandbox: {}, vision: false, campus_map: null, library_health: {},
      skills: [], kb_count: 0,
      session_metrics: {ok: false, selectable: []}
    }),
    get_activity: () => {
      window.__ACT_CALLS__++;
      push("get_activity", null);
      const payload = (window.__ACT_MODE__ === "b") ? window.__ACT_B__ : window.__ACT_A__;
      return Promise.resolve(payload);
    },
    reveal_file: (p) => { push("reveal_file", p); return Promise.resolve({ok: true, msg: "已定位"}); },
    open_url: (u) => { push("open_url", u); return Promise.resolve({ok: true}); },
    get_plan: () => Promise.resolve({ok: true, has_plan: false, steps: [], total: 0,
                                     done: 0, ready: [], blocked: [], failed: [],
                                     running: [], dispatch_text: ""}),
    get_recent_projects: () => Promise.resolve({ok: true, current: {}, default: {}, recent: []}),
    get_reasoning: () => Promise.resolve({visible: false, options: [], choice: "default"}),
    get_suggestions: () => Promise.resolve({suggestions: []}),
    get_countdowns: () => Promise.resolve({ok: true, countdowns: []}),
    get_schedule_view: () => Promise.resolve({ok: true, schedule: null}),
    get_session_metrics: () => Promise.resolve({ok: false}),
    refresh_sessions: () => Promise.resolve({ok: true}),
    send_task: () => Promise.resolve({ok: true, session: "s1"}),
    set_notice_watch: () => Promise.resolve({ok: true}),
    rate_message: () => Promise.resolve({ok: true}),
    compact_now: () => Promise.resolve({ok: true}),
  }
};
"""


def _serve(directory: Path):
    class _QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = partial(_QuietHandler, directory=str(directory))
    port = None
    for candidate in range(18700, 18780):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", candidate), handler)
            port = candidate
            break
        except OSError:
            continue
    assert port is not None, "18700-18779 里找不到可绑定的端口"
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

    stub = STUB_JS.replace("@@A@@", json.dumps(ACT_A, ensure_ascii=False))
    url, shutdown = _serve(FRONTEND)
    failures: list[str] = []

    def check(cond, message):
        if not cond:
            failures.append(message)
        print(("  ✓ " if cond else "  ✗ ") + message)

    files_a = _rows(ACT_A, "files")
    sub_a = _rows(ACT_A, "subagents")
    web_a = _rows(ACT_A, "web")
    src_a = _rows(ACT_A, "sources")

    try:
        with sync_playwright() as p:
            browser = _launch(p)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.add_init_script(stub)
            page.goto(url, wait_until="load")
            page.wait_for_function("() => document.getElementById('btnActivity') !== null")

            def calls(name):
                # 注意那个 [name]:JS 侧是解构形参 ([name]) => ...,传裸字符串会被
                # 按字符拆开(name 变成 'r'),于是过滤永远为空。
                return page.evaluate(
                    "([name]) => window.__E2E_CALLS__.filter(c => c[0] === name)", [name])

            def grid_cols():
                return page.evaluate(
                    "() => getComputedStyle(document.querySelector('.app'))"
                    ".gridTemplateColumns.split(' ').length")

            # ---- 1. 开机不占位:没点开就没有这一栏的列,也不该去敲桥 ----
            check(page.locator("#activity").is_hidden(), "开机时活动面板是收起来的")
            check(not page.evaluate("() => document.querySelector('.app')"
                                   ".classList.contains('act-open')"),
                  "没开时 .app 不带 act-open(不抢这一栏的宽度)")
            cols_closed = grid_cols()
            check(page.evaluate("() => window.__ACT_CALLS__") == 0,
                  "关着的时候一次都不问后端(没人看就别统计)")

            # ---- 2. 点「🛰 监控」:真多出一列,数据来自后端 ----
            page.click("#btnActivity")
            page.wait_for_selector("#activity .act-row")
            cols_open = grid_cols()
            # 原来这里写死的是 `== 3`("三列网格")。p3 在最左边加了一条入口栏,基础列
            # 就变成 3,打开后是 4 —— 写死的数会因搬家而红。**这一条真正断的是「点开
            # 那一栏,网格确实多占了一列」**,所以量的是差值,同时把两边的实际列数打出来
            # 让人看得见(不是把断言放松:少一列、多两列照样红)。
            check(cols_open == cols_closed + 1,
                  f"打开后 .app 比关着时多一列(关 {cols_closed} 列 → 开 {cols_open} 列)")
            check(page.evaluate("() => window.__ACT_CALLS__") >= 1,
                  "打开时问了一次 get_activity")
            check(page.get_attribute("#btnActivity", "aria-expanded") == "true",
                  "按钮 aria-expanded 跟着开合走")
            sum_bits = page.locator("#actSum").inner_text()
            check(f"{ACT_A['summary']['calls']} 次工具调用" in sum_bits
                  and f"{ACT_A['summary']['errors']} 处失败" in sum_bits,
                  f"顶栏数字就是后端算的那几个:{sum_bits}")
            check(page.locator("#actPill").inner_text().strip()
                  == str(ACT_A["summary"]["running"]),
                  "在跑的数量落在顶栏角标上")

            # ---- 3. 类目:有内容的才出现,没做过的不摆"暂无" ----
            caps = page.locator("#actBody .act-sec > .act-cap b").all_inner_texts()
            check(caps == ["子代理", "产出", "网页查阅", "来源"],
                  f"类目按后端给的顺序排,且只列有内容的:{caps}")
            body_text = page.locator("#actBody").inner_text()
            check("暂无" not in body_text and "记忆" not in body_text,
                  "空类目不渲染成'暂无';这一版还没记过记忆,就不该出现'记忆'")

            # ---- 4. 子代理:两行(并行派发各一行)+ 一行在跑,点开是真报告 ----
            rows = page.locator("#actBody .act-sec").nth(0).locator(".act-row")
            check(rows.count() == 3, f"两个已返回 + 一个在跑 = 三行(实测 {rows.count()})")
            check(rows.nth(0).evaluate("e => e.className").find("running") >= 0
                  and rows.nth(0).evaluate("e => e.className").find("plain") >= 0,
                  "最新那条是'在跑'且没有可点样子(报告还没回来)")
            titles = [r.inner_text() for r in rows.all()]
            check(any("梳理答辩稿的逻辑线" in t for t in titles)
                  and any("检查权限确认的门" in t for t in titles),
                  "一次并行派发拆成逐行(不是一行'2 个子代理'):各查什么各占一行")
            check("查一下绩点" in titles[0], "面板按最新在上排(和后端顺序一致)")
            done_row = page.locator("#actBody .act-sec").nth(0).locator(
                ".act-row:not(.running)").first
            done_row.click()
            detail = page.locator(".act-detail").first.inner_text()
            check("答辩逻辑分三段" in detail, "点开的报告就是子代理真写回来的那段字")
            done_row.click()
            check(page.locator(".act-detail").count() == 0, "再点一下收起")
            check(all(v["status"] == "ok" for k, v in sub_a.items()
                      if "梳理" in v["title"] or "检查" in v["title"]),
                  "后端那两行确实标成已完成(前端只是照着画)")

            # ---- 5. 产出:点一下叫到桥,参数是后端给的那个路径 ----
            ok_key = next(k for k, v in files_a.items() if v["status"] == "ok")
            page.locator(f"#actBody .act-row[data-k='{ok_key}']").click()
            got = calls("reveal_file")
            check(len(got) == 1 and got[0][1] == files_a[ok_key]["action"]["path"],
                  f"产出行把后端给的相对路径原样交给 reveal_file({files_a[ok_key]['action']['path']})")
            check("改于" in page.locator(f"#actBody .act-row[data-k='{ok_key}'] .act-s").inner_text(),
                  "副标题的大小/时间是磁盘上真读出来的")
            failed_key = next(k for k, v in files_a.items() if v["status"] == "failed")
            frow = page.locator(f"#actBody .act-row[data-k='{failed_key}']")
            check("编辑失败" in frow.inner_text() and "failed" in frow.get_attribute("class"),
                  "edit_file 真报的错在面板上是红色那一档(和聊天卡片同一口径)")

            # ---- 6. 网页与来源:有网址的开网址,只有关键词的填回输入框 ----
            src_key = next(iter(src_a))
            page.locator(f"#actBody .act-row[data-k='{src_key}']").click()
            opened = calls("open_url")
            check(opened and opened[-1][1] == src_a[src_key]["action"]["url"]
                  == "https://jwc.gzasc.edu.cn/xjdc",
                  "来源行点开是围栏里那个真来源网址(不是页内噪声链接)")
            check("https://ads.example/spam" not in body_text,
                  "抓取正文里的页内链接没被当成'来源'列出来")
            q_key = next(k for k, v in web_a.items() if v["action"]["type"] == "fill")
            page.locator(f"#actBody .act-row[data-k='{q_key}']").click()
            check(page.input_value("#input") == web_a[q_key]["action"]["text"],
                  "查询行点击 = 把这句追问填回输入框,等用户改再发")
            check(page.evaluate("() => document.getElementById('actBody')"
                                ".querySelectorAll('img,script,iframe').length") == 0,
                  "关键词里的 <img onerror> 被当字符串画出来(全程 textContent)")

            # ---- 7. ↻ 之后磁盘改了:文件行自己改口,并失去可点的样子 ----
            page.evaluate("(b) => { window.__ACT_B__ = b; window.__ACT_MODE__ = 'b'; }", ACT_B)
            page.click("#btnActRefresh")
            page.wait_for_function("() => [...document.querySelectorAll('#actBody .act-row')]"
                                   ".some(r => r.className.indexOf('missing') >= 0)")
            caps2 = page.locator("#actBody .act-sec > .act-cap b").all_inner_texts()
            check("记忆" in caps2, f"真记了一条记忆后,类目里才长出'记忆':{caps2}")
            check("16:9" in page.locator("#actBody").inner_text(),
                  "记忆那一行写的是真存进去的那句话")
            page.locator(f"#actBody .act-row[data-k='{ok_key}']").click()
            check(len(calls("reveal_file")) == 1,
                  "文件已删:那一行不再敲桥(不给'看着能点、点了没用')")
            mrow = page.locator(f"#actBody .act-row[data-k='{ok_key}']")
            cls = mrow.get_attribute("class")
            check("missing" in cls and "plain" in cls
                  and "已不在磁盘" in mrow.inner_text(),
                  "删掉的文件那行改口成'已不在磁盘'(status/类名/徽章三处一致)")

            # ---- 8. 事件驱动刷新:开着才刷,关了就不问 ----
            before = page.evaluate("() => window.__ACT_CALLS__")
            page.evaluate("(e) => window.__gcodeEvent(e)",
                          {"type": "tool_done", "session": "s1", "name": "write_file", "ok": True})
            page.wait_for_function(
                f"() => window.__ACT_CALLS__ > {before}", timeout=4000)
            check(True, "开着面板时来一次 tool_done,面板自己重新问一遍后端")
            page.click("#btnActClose")
            check(not page.evaluate("() => document.querySelector('.app')"
                                    ".classList.contains('act-open')"),
                  "点 ✕ 收回两栏")
            check(page.locator("#actBody").inner_text().strip() == ""
                  and page.locator("#actPill").inner_text().strip() == "",
                  "收起后清空内容和角标(下次打开重新按后端数据画)")
            after = page.evaluate("() => window.__ACT_CALLS__")
            page.evaluate("(e) => window.__gcodeEvent(e)",
                          {"type": "tool_done", "session": "s1", "name": "bash", "ok": True})
            page.wait_for_timeout(700)
            check(page.evaluate("() => window.__ACT_CALLS__") == after,
                  "关着的时候来工具事件不再敲桥")
            browser.close()
    finally:
        shutdown()

    print()
    if failures:
        print(f"共 {len(failures)} 项失败:")
        for f in failures:
            print("  ✗ " + f)
        return 1
    print("v0.9.34 前端回归:全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
