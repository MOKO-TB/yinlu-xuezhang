# -*- coding: utf-8 -*-
"""开机冒烟:窗口打开后 boot() 必须**跑完整**,不是"没白屏就算好"。

为什么单独要这一支:v0.9.22 起 renderTaskMetrics 里写错一个变量名(用了没声明过的
`canVisionToggle`),它在 boot() 中间抛 ReferenceError,被最外层 .catch 接住后只在
输入框下面留一行小字「界面初始化失败:...」。窗口照样开、聊天照样能用,但它后面
的六件事全没做 —— 其中一件就是 `CampusMap.setData(info.campus_map)`,于是免安装版
里地图面板是空的(用户看到的正是这个)。已有的 e2e_ui_v09xx 系列没有一个去碰
hint 和地图,所以这条 bug 一路绿着发到了 Release 里。

这一支的口径:
1. **payload 出自真后端** —— campus_map 走 `campus.frontend_payload()`,
   session_metrics 走 `model_catalog.session_metrics()`(拿真 Bridge 算),
   不是我手抄一份"我以为的形状";抄的夹具只会一直绿。
2. 断言初始化没报错 **且** 报错之后的每一步都真的执行到了(桥上有调用记录)。
3. 断言地图拿到数据后画得出来(#mapStage 里有 svg、图例徽章数对得上建筑类别),
   并且点顶栏「地图」能开面板 —— 这是用户判断"地图能用"的唯一依据。

跑法(项目根目录,需要 PYTHONIOENCODING=utf-8 否则中文断言在 GBK 控制台炸):
    PYTHONIOENCODING=utf-8 .venv\\Scripts\\python.exe scripts\\e2e_ui_init.py
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


# --------------------------------------------------------------- 真后端 payload

def build_info() -> dict:
    """get_info 的那一坨字段:地图与任务面板两块都由真后端算,别的手抄。"""
    from minicode import campus
    from minicode.config import Config, Provider
    from minicode.gui_bridge import create_app
    from minicode.model_catalog import session_metrics
    from tests.test_gui_bridge import FakeLLM, FakeWindow

    tmp = Path(tempfile.mkdtemp(prefix="yx-init-"))
    cfg = Config(providers=[Provider(id="test", name="Test",
                                      base_url="https://example.invalid/v1",
                                      api_key="test-key", model="test-model")],
                 active_provider_id="test")
    bridge, ui, _agent, _session = create_app(
        cfg, tmp, llm=FakeLLM([]), sessions_dir=tmp / "sessions",
        config_path=tmp / "config.json")
    ui.attach(FakeWindow())
    metrics = session_metrics(bridge.agent, bridge.session, bridge.config,
                              bridge._current.llm_override)
    payload = campus.frontend_payload()
    assert payload["items"], "campus-index.json 里一个建筑都没有,这支测试就没意义了"
    return {
        "version": "0.9.32", "app_name": "引路学长",
        "permission_mode": "confirm",
        "permission_modes": [{"value": "confirm", "label": "变更前确认"}],
        "session_key": "s1", "model": "test-model", "active_provider": "test",
        "providers": [{"id": "test", "name": "Test", "model": "test-model",
                       "has_key": True}],
        "api_key_configured": True, "workspace": str(tmp),
        "workspace_name": tmp.name, "workspace_is_default": True,
        "memory_count": 0, "memory_user_count": 0, "mcp_servers": [],
        "sandbox": {}, "vision": bool(metrics["vision"]),
        "campus_map": payload, "library_health": {}, "skills": [], "kb_count": 0,
        "session_metrics": metrics,
        "paths": {"dir": str(tmp / "app"), "writable": True, "files": []},
    }


INFO = build_info()
# boot() 里 renderTaskMetrics **之后**必须发生的调用;少一个就是初始化半路死了
AFTER_METRICS = ["refresh_sessions", "get_suggestions", "get_recent_projects",
                 "get_reasoning", "get_plan"]

STUB_JS = """
window.__E2E_CALLS__ = [];
window.__INFO__ = @@INFO@@;
window.__AFTER__ = [];
const __RET__ = {
  get_plan: () => ({ok: true, has_plan: false, steps: [], total: 0, done: 0,
                    ready: [], blocked: [], failed: [], dispatch_text: ""}),
  get_recent_projects: () => ({ok: true, current: {}, default: {}, recent: []}),
  get_reasoning: () => ({visible: false, options: [], choice: "default"}),
  get_suggestions: () => ({suggestions: []}),
  get_countdowns: () => ({ok: true, countdowns: []}),
  get_schedule_view: () => ({ok: true, schedule: null}),
  get_session_metrics: () => ({ok: false}),
  refresh_sessions: () => ({ok: true}),
};
const __API__ = {
  get_info: () => Promise.resolve(window.__INFO__),
  open_url: () => Promise.resolve({ok: true}),
  send_task: () => Promise.resolve({ok: true, session: "s1"}),
  set_notice_watch: () => Promise.resolve({ok: true}),
  rate_message: () => Promise.resolve({ok: true}),
  compact_now: () => Promise.resolve({ok: true}),
};
// 没点名的方法一律记一笔再给 {ok:true}:桥面长了新方法不该把冒烟测试弄红,
// 但"被调过"这件事必须留在记录里,否则断言不到初始化走到了哪一步。
window.pywebview = {api: new Proxy(__API__, {
  get(target, name) {
    if (typeof name !== "string" || name === "then") return target[name];
    if (!(name in target)) {
      target[name] = (...args) => {
        window.__AFTER__.push(name);
        const r = (name in __RET__) ? __RET__[name](...args) : {ok: true};
        return Promise.resolve(r);
      };
    }
    return target[name];
  }
})};
"""


def _serve(directory: Path):
    class _QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = partial(_QuietHandler, directory=str(directory))
    port = None
    for candidate in range(18620, 18700):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", candidate), handler)
            port = candidate
            break
        except OSError:
            continue
    assert port is not None, "18620-18699 里找不到可绑定的端口"
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

    stub = STUB_JS.replace("@@INFO@@", json.dumps(INFO, ensure_ascii=False))
    url, shutdown = _serve(FRONTEND)
    failures: list[str] = []

    def check(cond, message):
        if not cond:
            failures.append(message)
        print(("  ✓ " if cond else "  ✗ ") + message)

    try:
        with sync_playwright() as p:
            browser = _launch(p)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.add_init_script(stub)
            page.goto(url, wait_until="load")
            page.wait_for_function(
                "() => document.getElementById('ver').textContent.length > 0")

            hint = page.locator("#hint").inner_text()
            check("界面初始化失败" not in hint,
                  f"boot() 没被异常打断(hint 现在写着:{hint[:60] or '空'})")
            check("当前模型服务不可用" not in hint, "Key 已配置时不报模型不可用")

            # ---- 任务面板:渲染器本身要出得来(它就是当年抛错的那一处) ----
            check(page.locator("#tmName").inner_text().strip() == "test-model",
                  "任务面板模型名来自真 session_metrics")
            check(page.locator("#tmCaps .cap").count() >= 1,
                  "能力徽章(多模态/纯文本 + 上下文窗口)渲染出来了")

            # ---- 抛错点之后的每一步都得跑到,不然只是"看起来没崩" ----
            called = page.evaluate("() => window.__AFTER__")
            missing = [m for m in AFTER_METRICS if m not in called]
            check(not missing,
                  f"初始化后半段全部执行(缺:{missing}) —— 地图就是这一段的受害者")

            # ---- 地图:数据真进了组件、画得出来、点顶栏能开(p3 的现场) ----
            check(page.locator("#mapStage svg").count() == 1,
                  "CampusMap.setData 收到了后端的 campus_map 并画出底图 SVG")
            cats = {it["category"] for it in INFO["campus_map"]["items"]}
            chips = page.locator("#mapLegend .lg-chip").count()
            check(chips == len(cats),
                  f"图例类别徽章 {chips} 个 = 后端数据里的 {len(cats)} 类建筑")
            page.click("#btnMap")
            page.wait_for_timeout(400)
            check(page.locator("#mapPanel.open").count() == 1,
                  "点顶栏「🗺 地图」面板滑出来了")
            check(page.locator("#mapTitle").inner_text().strip()
                  == "🗺 " + INFO["campus_map"]["title"],
                  "面板标题用的是后端给的校区名(换数据集时面板不会继续挂旧校区)")
            img = page.locator("#mapStage image.cm-map-img")
            check(img.count() == 1
                  and img.first.get_attribute("href").endswith(
                      INFO["campus_map"]["base_map"]),
                  "底图 <image> 指向后端算好的 base_map 路径")
            loaded = page.evaluate(
                "() => { const i = document.querySelector('#mapStage image.cm-map-img');"
                " return i ? (i.getBBox().width > 0) : false; }")
            check(loaded, "底图真的画进了 SVG 里(不是一片空白面板)")
            page.fill("#mapSearch", "图书馆")
            page.wait_for_timeout(250)
            check(page.locator("#mapList .ml-row").count() > 0
                  and "命中" in page.locator("#mapSearchInfo").inner_text(),
                  "地图里搜「图书馆」出得来候选列表(数据真的可用,不是空壳)")

            browser.close()
    finally:
        shutdown()

    print()
    if failures:
        print(f"共 {len(failures)} 项失败:")
        for f in failures:
            print("  ✗ " + f)
        return 1
    print("开机冒烟:初始化跑完整、地图拿得到数据 —— 通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
