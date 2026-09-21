"""浏览器能力:CDP 客户端逻辑、线程级会话隔离、截图与工具文案。

不依赖真实浏览器:用 FakeConn 顶掉 WebSocket 层,断言发给 CDP 的
方法与参数、以及工具回填给模型的文案。真浏览器的端到端另见
scripts/e2e_browser.py。
"""
import base64
import json
import threading
import weakref

import pytest

from minicode import browser as br
from minicode.tools import browser as bt

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


class FakeConn:
    """顶掉 _Conn:按方法名返回预置结果,并记录所有调用。"""

    calls: list = []
    results: dict = {}

    def __init__(self, ws_url=None, *, ws=None, timeout=30.0):
        FakeConn.calls.append(("__init__", ws_url))

    def call(self, method, params=None, session=None, timeout=30.0):
        FakeConn.calls.append((method, params or {}, session))
        if method in self.results:
            v = self.results[method]
            if isinstance(v, Exception):
                raise v
            return v
        return _DEFAULTS.get(method, {})

    def close(self):
        FakeConn.calls.append(("close", {}, None))


_DEFAULTS = {
    "Target.createTarget": {"targetId": "T1"},
    "Target.attachToTarget": {"sessionId": "S1"},
    "Page.enable": {},
    "Runtime.enable": {},
    "Page.navigate": {},
    "Page.captureScreenshot": {"data": base64.b64encode(PNG).decode()},
    "Page.getLayoutMetrics": {"cssLayoutViewport": {"clientWidth": 1280, "clientHeight": 900}},
    "Browser.getVersion": {"product": "Edg/153.0"},
}


def _eval_results(values: dict):
    """把 {JS 表达式子串: 返回值} 变成 Runtime.evaluate 的响应序列。"""
    def handler(method, params, session):
        expr = (params or {}).get("expression", "")
        for key, val in values.items():
            if key in expr:
                return {"result": {"value": val}}
        return {"result": {"value": None}}
    return handler


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch):
    """每个用例一份干净的全局状态,且**绝不碰真实 WebSocket / 真实浏览器进程**。

    launch_browser 也必须桩掉:auto 现在默认走 launch,不桩的话几乎每个用例都会
    真的起一个 Edge(全量测试因此慢几十秒)——本文件先前就有这类隐性泄漏。
    """
    saved = (br._STATE.conn, br._STATE.browser, br._STATE.mode, br._STATE.headless,
             br._STATE.proc, br._STATE.port, dict(br._STATE.sessions))
    br._STATE.conn = None
    br._STATE.browser = ""
    br._STATE.mode = ""
    br._STATE.headless = True
    br._STATE.proc = None
    br._STATE.port = None
    br._STATE.sessions.clear()
    FakeConn.calls = []
    FakeConn.results = {}
    monkeypatch.setattr(br, "_Conn", FakeConn)
    monkeypatch.setattr(br, "_attach_endpoint",
                        lambda: ("ws://127.0.0.1:9222/devtools/browser/x",
                                 {"Browser": "Edg/153.0"}))

    class FakeProc:
        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

    def fake_launch(*, headless=True, port=None, profile_dir=None):
        FakeConn.calls.append(("__launch__", {"headless": headless}, None))
        return (FakeProc(), 9999,
                {"webSocketDebuggerUrl": "ws://127.0.0.1:9999/devtools/browser/y",
                 "Browser": "Edg/153.0"})

    monkeypatch.setattr(br, "launch_browser", fake_launch)
    monkeypatch.delenv(br.ENV_CDP_URL, raising=False)
    yield
    br._STATE.sessions.clear()
    (br._STATE.conn, br._STATE.browser, br._STATE.mode, br._STATE.headless,
     br._STATE.proc, br._STATE.port, sessions) = saved
    br._STATE.sessions.clear()
    br._STATE.sessions.update(sessions)


def _method_names():
    return [c[0] for c in FakeConn.calls]


# ---------------------------------------------------------------- 网址校验

def test_check_url_rejects_local_and_pseudo_schemes():
    for bad in ["file:///C:/Windows/win.ini", "javascript:alert(1)",
                "chrome://settings", "data:text/html,<script>x</script>",
                "view-source:https://a.test", "ftp://host/x"]:
        with pytest.raises(br.BrowserError):
            br.check_url(bad)


def test_check_url_normalizes():
    assert br.check_url("https://a.test/x") == "https://a.test/x"
    assert br.check_url("a.test/x") == "https://a.test/x"      # 缺协议补 https
    assert br.check_url("  http://127.0.0.1:5173/ ") == "http://127.0.0.1:5173/"
    for bad in ["", "   ", "https://", "not a url at all !!"]:
        with pytest.raises(br.BrowserError):
            br.check_url(bad)


# ---------------------------------------------------------------- 打开页面

def _page_eval_handler(seq: dict):
    """生成一个按"表达式子串 → 返回值"答 Runtime.evaluate 的 FakeConn.call。"""
    def fake_call(self, method, params=None, session=None, timeout=30.0):
        FakeConn.calls.append((method, params or {}, session))
        if method == "Runtime.evaluate":
            expr = (params or {}).get("expression", "")
            for key, val in seq.items():
                if key in expr:
                    return {"result": {"value": val}}
            return {"result": {"value": None}}
        if method in _DEFAULTS:
            return _DEFAULTS[method]
        return {}
    return fake_call


def test_open_page_drives_cdp_and_returns_rendered_text(isolated_state, monkeypatch):
    seq = {"document.readyState": "complete", "document.title": "通知详情",
           "location.href": "https://news.test/a",
           "innerText": "新生入学须知 正文第一段"}
    monkeypatch.setattr(FakeConn, "call", _page_eval_handler(seq))
    out = br.open_page("https://news.test/a", wait_ms=300)
    assert out["title"] == "通知详情"
    assert out["url"] == "https://news.test/a"
    assert out["text"].startswith("新生入学须知")
    assert out["loaded"] is True
    # auto 默认走独立实例(fixture 里是假 launch),绝不默认连用户自己的浏览器
    assert out["mode"] == "launch"
    assert "Page.navigate" in _method_names()
    # 新标签页要 enable 这两个域,否则收不到页面事件/求值上下文
    assert "Page.enable" in _method_names() and "Runtime.enable" in _method_names()
    nav = [c for c in FakeConn.calls if c[0] == "Page.navigate"][0]
    assert nav[1]["url"] == "https://news.test/a"
    assert nav[2] == "S1"                        # 命令带上了页面的 sessionId


def test_open_page_uses_attach_when_explicitly_asked(isolated_state, monkeypatch):
    """mode=attach 是"要用我的登录态"的显式请求,得真落到那条路上。"""
    seq = {"document.readyState": "complete", "document.title": "教务系统",
           "location.href": "https://jw.test/home", "innerText": "我的课表"}
    monkeypatch.setattr(FakeConn, "call", _page_eval_handler(seq))
    out = br.open_page("https://jw.test/home", wait_ms=200, mode="attach")
    assert out["mode"] == "attach" and out["title"] == "教务系统"
    assert "__launch__" not in [c[0] for c in FakeConn.calls], "不该另外起实例"


def test_open_page_truncates_and_reports_full_length(isolated_state, monkeypatch):
    long = "字" * 3000

    def fake_call(self, method, params=None, session=None, timeout=30.0):
        FakeConn.calls.append((method, params or {}, session))
        if method == "Runtime.evaluate":
            expr = (params or {}).get("expression", "")
            val = long if "innerText" in expr or "textContent" in expr else "t"
            return {"result": {"value": val}}
        return _DEFAULTS.get(method, {})

    monkeypatch.setattr(FakeConn, "call", fake_call)
    out = br.open_page("https://a.test", wait_ms=100, max_chars=200)
    assert out["chars"] == 3000                 # 真实长度仍然报出来
    assert out["truncated"] is True
    assert len(out["text"]) < 300


def test_navigate_error_text_surfaces(isolated_state):
    FakeConn.results["Page.navigate"] = {"errorText": "net::ERR_BLOCKED_BY_CLIENT"}
    with pytest.raises(br.BrowserError) as e:
        br.open_page("https://a.test")
    assert "ERR_BLOCKED_BY_CLIENT" in str(e.value)


# ---------------------------------------------------------------- 线程隔离

def test_each_thread_gets_its_own_tab(isolated_state, monkeypatch):
    """两个会话(= 两个并发线程)各自开标签页,不会互相抢「当前页面」。

    必须真并发:线程退出后 ident 会被复用,那样测试就变成假阳性。
    """
    seq = iter(["T1", "T2"])

    def fake_call(self, method, params=None, session=None, timeout=30.0):
        FakeConn.calls.append((method, params or {}, session))
        if method == "Target.createTarget":
            return {"targetId": next(seq)}
        if method == "Target.attachToTarget":
            return {"sessionId": "S-" + (params or {}).get("targetId", "")}
        return _DEFAULTS.get(method, {})

    monkeypatch.setattr(FakeConn, "call", fake_call)
    gate = threading.Barrier(2)
    seen = {}

    def worker(name):
        sess = br._ensure_session()
        gate.wait(timeout=10)          # 两会话都建好之前不放行
        seen[name] = (sess.target_id, sess.cdp_session)

    threads = [threading.Thread(target=worker, args=(n,)) for n in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    assert seen["a"][0] != seen["b"][0], "两个线程共用了同一个标签页"
    assert seen["a"][1] != seen["b"][1]
    assert len(br._STATE.sessions) == 2


def test_same_thread_reuses_tab(isolated_state):
    first = br._ensure_session()
    second = br._ensure_session()
    assert first is second
    assert _method_names().count("Target.createTarget") == 1


def test_dead_thread_tab_is_swept(isolated_state, monkeypatch):
    """线程跑完(后台会话结束/子代理交差)后,它的标签页要能被回收。"""
    hold = threading.Event()
    ids = iter(["Tmain", "Tworker"])

    def fake_call(self, method, params=None, session=None, timeout=30.0):
        FakeConn.calls.append((method, params or {}, session))
        if method == "Target.createTarget":
            return {"targetId": next(ids)}
        if method == "Target.attachToTarget":
            return {"sessionId": "S" + str((params or {}).get("targetId"))}
        return _DEFAULTS.get(method, {})

    monkeypatch.setattr(FakeConn, "call", fake_call)
    main_sess = br._ensure_session()
    assert main_sess.target_id == "Tmain"

    def worker():
        br._ensure_session()
        hold.wait(timeout=10)

    t = threading.Thread(target=worker)
    t.start()
    hold.set()
    t.join(timeout=10)
    assert len(br._STATE.sessions) == 2, "线程退出后标签页应当还留着(等下一次回收)"
    # 任何一次拿会话的入口都会顺手回收已结束线程的页,但不碰还活着的线程
    assert br.health()["ok"]
    assert list(br._STATE.sessions) == [f"{br._KEY}:{threading.get_ident()}"], \
        "回收时误删了主线程的页,或没回收死线程的页"


def test_session_cap_closes_oldest(isolated_state):
    """标签页有上限:超出就关掉最旧的(当前这个一定保留)。"""
    conn = br._ensure_conn()[0]
    for i in range(br.MAX_SESSIONS):
        s = br.PageSession(conn=conn, target_id=f"T{i}", cdp_session=f"S{i}")
        s.opened_at = float(i)                          # 明确的新旧顺序
        br._STATE.sessions[f"k{i}"] = s
    fresh = br.PageSession(conn=conn, target_id="NEW", cdp_session="SN")
    fresh.opened_at = 99.0
    br._STATE.sessions["new"] = fresh
    br._prune_locked(keep="new")
    assert len(br._STATE.sessions) == br.MAX_SESSIONS
    assert "new" in br._STATE.sessions, "把正在用的页面关掉了"
    assert "k0" not in br._STATE.sessions, "没关最旧的那个"
    closed = [c[1]["targetId"] for c in FakeConn.calls if c[0] == "Target.closeTarget"]
    assert closed == ["T0"]


# ---------------------------------------------------------------- 截图

def test_screenshot_writes_png_and_pushes_event(ctx):
    opened = br.open_page("https://a.test", wait_ms=50)
    assert opened["url"]
    res = br.screenshot(ctx.workspace / "shots" / "a.png")
    out = ctx.workspace / "shots" / "a.png"
    assert out.exists() and out.read_bytes() == PNG
    assert res["bytes"] == len(PNG)
    assert res["viewport"] == (1280, 900)


def test_screenshot_without_page_raises(isolated_state):
    with pytest.raises(br.BrowserError) as e:
        br.screenshot("x.png")
    assert "browser_open" in str(e.value)          # 报错要告诉下一步该干什么


def test_eval_requires_open_page(isolated_state):
    with pytest.raises(br.BrowserError):
        br.evaluate("1+1")


def test_close_current_releases_tab(isolated_state):
    br._ensure_session()
    assert br.close_current() is True
    assert br.close_current() is False
    assert "Target.closeTarget" in _method_names()


# ---------------------------------------------------------------- 自检

def test_health_reports_launch_as_primary_route(isolated_state, monkeypatch):
    """health 的主口径要与 _ensure_conn 一致:能 launch 就报 launch,
    可选的登录态路线附在说明里(不再把 attach 当默认可用路线报)。"""
    monkeypatch.setattr(br, "find_browser_exe", lambda: ("Edge", r"C:\x\msedge.exe"))
    h = br.health()
    assert h["ok"] and h["mode"] == "launch"
    assert "独立实例" in h["detail"]
    assert "开着远程调试" in h["detail"]      # fixture 提供了可连端点,要一并报出来


def test_health_reports_launch_route_when_no_endpoint(isolated_state, monkeypatch):
    monkeypatch.setattr(br, "_attach_endpoint", lambda: None)
    monkeypatch.setattr(br, "find_browser_exe", lambda: ("Edge", r"C:\x\msedge.exe"))
    h = br.health()
    assert h["ok"] and h["mode"] == "launch" and "msedge" in h["detail"]


def test_health_reports_unavailable(isolated_state, monkeypatch):
    monkeypatch.setattr(br, "_attach_endpoint", lambda: None)
    monkeypatch.setattr(br, "find_browser_exe", lambda: None)
    h = br.health()
    assert not h["ok"] and h["detail"]


def test_attach_mode_required_reports_fix(isolated_state, monkeypatch):
    monkeypatch.setattr(br, "_attach_endpoint", lambda: None)
    with pytest.raises(br.BrowserUnavailable) as e:
        br._ensure_conn(mode="attach")
    assert "remote-debugging-port" in str(e.value)


# -------------------------------------------------- 真环境探测兼容(踩过的坑)

def test_probe_falls_back_to_devtools_active_port(monkeypatch, tmp_path):
    """实测环保类坑:用户自己的 Edge 在 9222 上对 /json/version 返 404,
    只认 localhost;而 DevToolsActivePort 文件里直接记着可用的 ws 路径。
    只依赖 HTTP 探测会把可用的登录态浏览器误判成「没找到」。"""
    f = tmp_path / "DevToolsActivePort"
    f.write_text("9222\n/devtools/browser/abc-123\n", encoding="utf-8")
    monkeypatch.setattr(br, "_devtools_files", lambda: [f])

    def fake_http(url, timeout=2.0):
        if "localhost" in url:                     # 只认 localhost 的那种 Edge
            return {"webSocketDebuggerUrl": "ws://localhost:9222/devtools/browser/abc-123"}
        return None

    monkeypatch.setattr(br, "_http_json", fake_http)
    info = br.probe_endpoint(9222)
    assert info and info["webSocketDebuggerUrl"].endswith("abc-123")


def test_probe_uses_port_file_when_http_all_404(monkeypatch, tmp_path):
    f = tmp_path / "DevToolsActivePort"
    f.write_text("9222\n/devtools/browser/dead-beef\n", encoding="utf-8")
    monkeypatch.setattr(br, "_devtools_files", lambda: [f])
    monkeypatch.setattr(br, "_http_json", lambda url, timeout=2.0: None)   # 两种 Host 都 404
    info = br.probe_endpoint(9222)
    assert info == {"webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/dead-beef",
                    "Browser": "浏览器(由 DevToolsActivePort 探测)"}
    assert br.probe_endpoint(59999) is None       # 文件里的端口对不上就不算


def test_endpoint_port_reads_the_url_port_not_the_ip():
    """文档推荐的写法是 http://127.0.0.1:9222 —— 老正则命中的是第一串数字,
    也就是 IP 里的 "127",于是永远去探 127 端口,attach 这条路"怎么都连不上"
    (抓教务样本时真机踩到)。端口必须从 URL 的 host:port 里取。"""
    assert br._endpoint_port("http://127.0.0.1:9222") == 9222
    assert br._endpoint_port("http://localhost:9223/") == 9223
    assert br._endpoint_port("127.0.0.1:9222") == 9222
    assert br._endpoint_port("http://127.0.0.1:9222/json/version") == 9222
    assert br._endpoint_port("9222") == 9222                  # 只写端口也认
    assert br._endpoint_port("开着呢") == 0                    # 没端口:交回上层报文案
    assert br._endpoint_port("http://127.0.0.1:99999") == 0    # 越界不崩


_REAL_ATTACH_ENDPOINT = br._attach_endpoint     # 导入期取真身(见下面用例的说明)


def test_attach_endpoint_probes_the_env_url_port(isolated_state, monkeypatch):
    """显式设了端点时,探测的端口必须是 URL 里那个,不能是 IP 的第一段。

    autouse 的 isolated_state 把 _attach_endpoint 整个桩成了"永远探得到",
    所以这里要在导入期取到的真身换回来 —— 否则用例测的是桩。
    """
    seen: list[int] = []

    def fake_probe(port, timeout=1.5):
        seen.append(int(port))
        return None

    monkeypatch.setattr(br, "_attach_endpoint", _REAL_ATTACH_ENDPOINT)
    monkeypatch.setattr(br, "probe_endpoint", fake_probe)
    monkeypatch.setenv(br.ENV_CDP_URL, "http://127.0.0.1:9222")
    with pytest.raises(br.BrowserUnavailable) as e:
        br._attach_endpoint()
    assert seen == [9222]
    assert "9222" in str(e.value)                 # 文案里带上用户写的那个地址


def test_attach_validated_by_real_handshake(isolated_state, monkeypatch):
    """探到端口不等于能用:浏览器已关/端口被别的程序占着时不能报「可 attach」。"""
    class DeadConn(FakeConn):
        def call(self, method, params=None, session=None, timeout=30.0):
            raise br.BrowserError("不是 CDP 服务")

    monkeypatch.setattr(br, "_Conn", DeadConn)
    with pytest.raises(br.BrowserUnavailable) as e:
        br._ensure_conn(mode="attach")
    assert "连不上" in str(e.value)
    assert br._STATE.conn is None


def test_explicit_attach_failure_does_not_silently_launch(isolated_state, monkeypatch):
    """mode=attach 连不上时必须报错,不能改用 launch —— 那是把用户要的"登录态"
    偷偷换成一个干净实例,结果完全不同(例如只会看到登录页)。"""
    class DeadConn(FakeConn):
        def call(self, method, params=None, session=None, timeout=30.0):
            raise br.BrowserError("不是 CDP 服务")

    monkeypatch.setattr(br, "_Conn", DeadConn)
    with pytest.raises(br.BrowserUnavailable):
        br._ensure_conn(mode="attach")
    assert "__launch__" not in [c[0] for c in FakeConn.calls]


def test_auto_prefers_launch_even_when_attach_available(isolated_state):
    """即使探到用户开着的调试端点,auto 也起独立实例:不默认动他那个
    带登录态、带几十个标签页的浏览器(对分发型程序太侵入,实测也容易卡住)。"""
    # fixture 已把 _attach_endpoint 桩成"探得到"、launch_browser 桩成假 launch
    _conn, _browser, mode = br._ensure_conn(mode="auto")
    assert mode == "launch"
    assert ("__launch__", {"headless": True}, None) in FakeConn.calls


def test_auto_uses_attach_only_when_user_asked(isolated_state, monkeypatch):
    """用户显式设了 YINLUXS_CDP_URL = 他的意图,auto 要按它走。"""
    monkeypatch.setenv(br.ENV_CDP_URL, "http://127.0.0.1:9222")
    _conn, _browser, mode = br._ensure_conn(mode="auto")
    assert mode == "attach"
    assert "__launch__" not in [c[0] for c in FakeConn.calls]


def test_auto_falls_back_to_attach_when_launch_impossible(isolated_state, monkeypatch):
    """本机起不了浏览器(比如没装 Edge/Chrome)时,attach 是最后的兜底。"""
    def boom(**kw):
        raise br.BrowserUnavailable("本机没找到 Edge/Chrome")

    monkeypatch.setattr(br, "launch_browser", boom)
    _conn, _browser, mode = br._ensure_conn(mode="auto")
    assert mode == "attach"


def test_auto_reports_both_routes_failed(isolated_state, monkeypatch):
    """两条路都不通:报错要把两个原因都带上,别只说一半。"""
    def boom(**kw):
        raise br.BrowserUnavailable("本机没找到 Edge/Chrome")

    monkeypatch.setattr(br, "launch_browser", boom)
    monkeypatch.setattr(br, "_attach_endpoint", lambda: None)
    with pytest.raises(br.BrowserUnavailable) as e:
        br._ensure_conn(mode="auto")
    msg = str(e.value)
    assert "没找到 Edge/Chrome" in msg and "没找到开着远程调试" in msg


def test_health_does_not_overclaim_when_probe_dies(isolated_state, monkeypatch):
    """探到残留端口却握手不通、且本机没浏览器时,不能对用户报「可用」。"""
    class DeadConn(FakeConn):
        def call(self, method, params=None, session=None, timeout=30.0):
            raise br.BrowserError("不是 CDP 服务")

    monkeypatch.setattr(br, "_Conn", DeadConn)
    monkeypatch.setattr(br, "find_browser_exe", lambda: None)
    h = br.health()
    assert not h["ok"], "探到死端点却报可用"
    assert "握手不通" in h["detail"]


def test_shutdown_is_idempotent(isolated_state):
    br._ensure_session()
    br.shutdown()
    br.shutdown()
    assert br._STATE.conn is None and not br._STATE.sessions


def test_launch_connect_failure_does_not_leak_process(isolated_state, monkeypatch):
    """起了浏览器但 WebSocket 连不上:要把进程收掉,不能 NameError/漏进程。"""
    class FakeProc:
        def __init__(self):
            self.terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

    proc = FakeProc()
    monkeypatch.setattr(br, "_attach_endpoint", lambda: None)
    monkeypatch.setattr(br, "launch_browser",
                        lambda **kw: (proc, 9999,
                                      {"webSocketDebuggerUrl": "ws://x", "Browser": "Edge"}))

    def boom(*a, **k):
        raise RuntimeError("socket hang up")

    monkeypatch.setattr(br, "_Conn", boom)
    with pytest.raises(br.BrowserUnavailable) as e:
        br._ensure_conn(mode="launch")
    assert "连不上" in str(e.value)
    assert proc.terminated, "失败的浏览器进程没被收掉"
    assert br._STATE.conn is None


# ---------------------------------------------------------------- 工具层文案

def test_tool_browser_open_returns_readable_text(ctx, monkeypatch):
    def fake_call(self, method, params=None, session=None, timeout=30.0):
        FakeConn.calls.append((method, params or {}, session))
        if method == "Runtime.evaluate":
            expr = (params or {}).get("expression", "")
            if "readyState" in expr:
                return {"result": {"value": "complete"}}
            if "document.title" in expr:
                return {"result": {"value": "奖学金公告"}}
            if "href" in expr:
                return {"result": {"value": "https://a.test/g"}}
            return {"result": {"value": "国家奖学金评定办法 第一条 …"}}
        return _DEFAULTS.get(method, {})

    monkeypatch.setattr(FakeConn, "call", fake_call)
    out = bt.browser_open(ctx, "https://a.test/g", wait_ms=100, reason="看看奖学金公告")
    assert "奖学金公告" in out and "国家奖学金" in out
    assert "browser_screenshot" in out          # 引导下一步


def test_tool_browser_open_unavailable_gives_fallback(ctx, monkeypatch):
    """两条路都不通时,给模型的要是「错误:」开头 + 可执行的兜底方案。"""
    def boom(**kw):
        raise br.BrowserUnavailable("本机没找到 Edge/Chrome")

    monkeypatch.setattr(br, "launch_browser", boom)
    monkeypatch.setattr(br, "_attach_endpoint", lambda: None)
    out = bt.browser_open(ctx, "https://a.test")
    assert out.startswith("错误:"), "不成刜 '错误:' 开头,工具卡片会误判成功"
    assert "不可用" in out and "web_crawl" in out      # 必须给兜底方案


def test_tool_browser_eval_formats_json(ctx, monkeypatch):
    br.open_page("https://a.test", wait_ms=20)

    def fake_call(self, method, params=None, session=None, timeout=30.0):
        if method == "Runtime.evaluate":
            return {"result": {"value": json.dumps([{"书": "三体"}], ensure_ascii=False)}}
        return _DEFAULTS.get(method, {})

    monkeypatch.setattr(FakeConn, "call", fake_call)
    out = bt.browser_eval(ctx, "JSON.stringify(rows)")
    assert "三体" in out
    assert bt.browser_eval(ctx, "   ").startswith("错误")


def test_tool_screenshot_pushes_event_and_markdown(ctx):
    events = []

    class UI:
        def push(self, type_, **payload):
            events.append({"type": type_, **payload})

    ctx.ui = UI()
    br.open_page("https://a.test", wait_ms=20)
    out = bt.browser_screenshot(ctx, path=" shots/页面.png ")
    assert "shots/页面.png" in out
    assert "![](shots/页面.png)" in out         # 教模型把图写进回答
    shots = [e for e in events if e["type"] == "browser"]
    assert shots and shots[0]["op"] == "shot"
    assert shots[0]["path"] == "shots/页面.png"
    assert (ctx.workspace / "shots" / "页面.png").exists()


def test_tool_screenshot_default_name_has_timestamp(ctx):
    br.open_page("https://a.test", wait_ms=20)
    out = bt.browser_screenshot(ctx)
    assert out.startswith("截图已保存:shots/网页-")


def test_tool_screenshot_outside_workspace_rejected(ctx):
    br.open_page("https://a.test", wait_ms=20)
    out = bt.browser_screenshot(ctx, path="../外置.png")
    assert "不合法" in out or "超出" in out


def test_tool_close_and_status(ctx):
    assert "没有打开的页面" in bt.browser_close(ctx)
    br.open_page("https://a.test", wait_ms=20)
    assert "已关闭当前页面" in bt.browser_close(ctx)
    assert "可用" in bt.browser_status(ctx)


# ---------------------------------------------------------------- 注册与提示词

def test_registry_has_browser_tools_with_reason():
    from minicode.tools.registry import build_default_registry
    tools = {t.name: t for t in build_default_registry()}
    for name in ("browser_open", "browser_eval", "browser_snapshot",
                 "browser_click", "browser_fill", "browser_screenshot",
                 "browser_close", "browser_status"):
        assert name in tools, f"缺工具 {name}"
    # 会开浏览器/操作页面的一律要走确认;纯查询的 browser_status/snapshot 才是只读
    for name in ("browser_status", "browser_snapshot"):
        assert tools[name].read_only is True
    for name in ("browser_open", "browser_eval", "browser_click", "browser_fill",
                 "browser_screenshot", "browser_close"):
        props = tools[name].parameters["properties"]
        assert "reason" in props, f"{name} 缺 reason 参数(确认框要给用户看目的)"


def test_system_prompt_documents_browser_usage(tmp_path, monkeypatch):
    from minicode import app_setup
    text = app_setup.build_system_prompt(tmp_path)
    assert "browser_open" in text and "browser_screenshot" in text
    assert "web_crawl" in text          # 要说清两者分工


def test_permission_explains_browser_tools():
    """确认框得把"要用本机浏览器联网/要用你的登录态/要写截图文件"说清楚。"""
    from minicode.explain import risk_notes
    assert any("浏览器" in r for r in risk_notes("browser_open", {"url": "https://a.test"}))
    attach = risk_notes("browser_open", {"url": "https://a.test", "mode": "attach"})
    assert any("登录态" in r for r in attach), attach
    assert any("JavaScript" in r for r in risk_notes("browser_eval", {"js": "1+1"}))
    assert any("截图文件" in r for r in risk_notes("browser_screenshot", {}))


def test_permission_detail_shows_url_and_js(config, tmp_path):
    from minicode.gui_bridge import Bridge
    from minicode.tools.base import Tool

    def make(name, **props):
        return Tool(name=name, description="d", parameters={"type": "object",
                      "properties": props}, handler=lambda **k: "")

    bridge = Bridge.__new__(Bridge)         # 只要 _permission_detail_text,不跑完整装配
    bridge.workspace = tmp_path
    assert "https://a.test/x" in bridge._permission_detail_text(
        make("browser_open", url={"type": "string"}), {"url": "https://a.test/x"})
    detail = bridge._permission_detail_text(make("browser_eval", js={"type": "string"}),
                                            {"js": "document.title"})
    assert "document.title" in detail
    assert "shots/" in bridge._permission_detail_text(
        make("browser_screenshot", path={"type": "string"}), {})
    # 子代理带前缀的名字也要能读到(否则回退成裸 JSON)
    assert "https://b.test" in bridge._permission_detail_text(
        make("explore·browser_open", url={"type": "string"}), {"url": "https://b.test"})


def test_web_public_profile_excludes_browser():
    """对外只读的网页版绝对不能拿到浏览器工具(SSRF + 在服务器上起浏览器)。"""
    from minicode.web_profile import EXCLUDED_TOOLS, PUBLIC_TOOLS
    for name in ("browser_open", "browser_eval", "browser_snapshot",
                 "browser_click", "browser_fill", "browser_screenshot",
                 "browser_close", "browser_status"):
        assert name not in PUBLIC_TOOLS
        assert name in EXCLUDED_TOOLS, f"{name} 应当被显式排除并写明原因"


def test_browser_section_is_stable_text():
    """这段进的是 system prompt 前缀,不能含时间戳/随机内容(否则每回合打断缓存)。"""
    from minicode.browser import browser_prompt_section
    a, b = browser_prompt_section(), browser_prompt_section()
    assert a == b and a.startswith("\n\n#")


# ---------------------------------------------------------------- 页面交互(snapshot/click/fill)

def _page_with_form():
    """一个带表单、按钮与延迟跳转的假页面(挂在 Runtime.evaluate 假连接后面)。"""
    br.open_page("https://a.test/form", wait_ms=20)


def _form_eval_handler(extra: dict | None = None):
    """按表达式特征回响应的 Runtime.evaluate 假连接;extra 可覆盖个别片段。"""
    def fake_call(self, method, params=None, session=None, timeout=30.0):
        FakeConn.calls.append((method, params or {}, session))
        if method != "Runtime.evaluate":
            return _DEFAULTS.get(method, {})
        expr = (params or {}).get("expression", "")
        for key, val in (extra or {}).items():
            if key in expr:
                return {"result": {"value": val}}
        if "data-ylx" in expr and "querySelectorAll" in expr:
            return {"result": {"value": json.dumps([
                {"n": 1, "kind": "input", "name": "username", "ph": "用户名"},
                {"n": 2, "kind": "input", "name": "pwd", "type": "password"},
                {"n": 3, "kind": "select", "name": "grade", "value": "大一",
                 "options": "大一 | 大二 | 大三"},
                {"n": 4, "kind": "check", "name": "agree", "label": "同意条款",
                 "checked": False},
                {"n": 5, "kind": "button", "text": "登录", "type": "submit"},
            ], ensure_ascii=False)}}
        if "readyState" in expr:
            return {"result": {"value": "complete"}}
        if "location.href" in expr:
            return {"result": {"value": "https://a.test/form"}}
        if "document.title" in expr:
            return {"result": {"value": "登录页"}}
        return {"result": {"value": None}}
    return fake_call


def test_snapshot_lists_interactable_elements(ctx, monkeypatch):
    _page_with_form()
    monkeypatch.setattr(FakeConn, "call", _form_eval_handler())
    out = bt.browser_snapshot(ctx)
    assert "1. " in out and "登录" in out
    assert "下拉框" in out and "大一 | 大二" in out
    assert "勾选" in out and "未勾选" in out
    assert "browser_click" in out            # 教模型下一步干什么
    # 密码框只有 name 没有 value/文字:不该把值带出来
    assert "pwd" in out


def test_snapshot_no_page_gives_guidance(ctx):
    out = bt.browser_snapshot(ctx)
    assert out.startswith("错误:") and "browser_open" in out


def test_click_by_index_uses_snapshot_marker(ctx, monkeypatch):
    _page_with_form()
    clicked: list[str] = []

    def fake_call(self, method, params=None, session=None, timeout=30.0):
        if method == "Runtime.evaluate":
            expr = (params or {}).get("expression", "")
            if "data-ylx" in expr and "click()" in expr:
                clicked.append(expr)
                return {"result": {"value": json.dumps(
                    {"ok": True, "tag": "button", "desc": "登录"}, ensure_ascii=False)}}
            if "readyState" in expr:
                return {"result": {"value": "complete"}}
            if "location.href" in expr:
                return {"result": {"value": "https://a.test/after"}}
            if "document.title" in expr:
                return {"result": {"value": "登录成功"}}
            return {"result": {"value": None}}
        return _DEFAULTS.get(method, {})

    monkeypatch.setattr(FakeConn, "call", fake_call)
    out = bt.browser_click(ctx, "5")
    assert clicked and "[data-ylx=" in clicked[0]      # 按标记点,不是按序数数
    assert "已点击:登录" in out
    assert "登录成功" in out and "https://a.test/after" in out


def test_click_by_text_refuses_ambiguity(ctx, monkeypatch):
    _page_with_form()
    monkeypatch.setattr(FakeConn, "call", _form_eval_handler({
        # 模拟页面上有两个「登录」
        "innerText": json.dumps({"ok": False,
                                 "error": "有 2 个元素都叫「登录」,先 browser_snapshot 拿序号再点"}),
    }))
    out = bt.browser_click(ctx, "登录")
    assert out.startswith("错误:") and "snapshot" in out


def test_click_stale_index_errors(ctx, monkeypatch):
    _page_with_form()
    monkeypatch.setattr(FakeConn, "call", _form_eval_handler({
        "data-ylx": json.dumps({"ok": False, "error": "序号 9 已失效,请先 browser_snapshot"}),
    }))
    out = bt.browser_click(ctx, "9")
    assert out.startswith("错误:") and "snapshot" in out


def test_fill_fields_and_submit(ctx, monkeypatch):
    _page_with_form()
    submitted: list[str] = []

    def fake_call(self, method, params=None, session=None, timeout=30.0):
        FakeConn.calls.append((method, params or {}, session))
        if method != "Runtime.evaluate":
            return _DEFAULTS.get(method, {})
        expr = (params or {}).get("expression", "")
        if "rec" in expr and "field" in expr:
            field = "年级" if "年级" in expr else "用户名"
            tag = "select" if field == "年级" else "input"
            val = "大二" if field == "年级" else "lihua"
            return {"result": {"value": json.dumps(
                {"ok": True, "field": field, "tag": tag, "set": val},
                ensure_ascii=False)}}
        if "submit" in expr or "提交" in expr:
            submitted.append(expr)
            return {"result": {"value": json.dumps(
                {"ok": True, "desc": "登录"}, ensure_ascii=False)}}
        if "readyState" in expr:
            return {"result": {"value": "complete"}}
        if "location.href" in expr:
            return {"result": {"value": "https://a.test/welcome"}}
        if "document.title" in expr:
            return {"result": {"value": "欢迎"}}
        return {"result": {"value": None}}

    monkeypatch.setattr(FakeConn, "call", fake_call)
    out = bt.browser_fill(ctx, {"用户名": "lihua", "年级": "大二"}, submit=True)
    assert "已填" in out and "lihua" in out and "大二" in out
    assert "已点提交" in out and "欢迎" in out
    assert submitted, "submit=true 却没点提交按钮"


def test_fill_partial_failure_does_not_submit(ctx, monkeypatch):
    _page_with_form()
    submitted = []

    def fake_call(self, method, params=None, session=None, timeout=30.0):
        FakeConn.calls.append((method, params or {}, session))
        if method != "Runtime.evaluate":
            return _DEFAULTS.get(method, {})
        expr = (params or {}).get("expression", "")
        if "提交按钮" in expr or "button" in expr.lower():
            submitted.append(expr)
        if "找不到字段" in expr:
            return {"result": {"value": json.dumps(
                {"ok": False, "error": "页面上找不到字段「学号」"}, ensure_ascii=False)}}
        if "rec" in expr:
            return {"result": {"value": json.dumps(
                {"ok": True, "field": "用户名", "set": "x"}, ensure_ascii=False)}}
        if "readyState" in expr:
            return {"result": {"value": "complete"}}
        if "location.href" in expr:
            return {"result": {"value": "https://a.test/form"}}
        if "document.title" in expr:
            return {"result": {"value": "登录页"}}
        return {"result": {"value": None}}

    monkeypatch.setattr(FakeConn, "call", fake_call)
    out = bt.browser_fill(ctx, {"用户名": "x", "学号": "20xx"}, submit=True)
    assert "学号" in out and "找不到字段" in out
    assert "没有点提交" in out, "字段失败还去点提交会把半张表单发出去"
    assert not submitted, "失败路径不该触发提交按钮的 JS"


def test_fill_reports_missing_options_of_select(ctx, monkeypatch):
    _page_with_form()
    monkeypatch.setattr(FakeConn, "call", _form_eval_handler({
        "SELECT": json.dumps({"ok": False,
                              "error": "下拉框「年级」里没有「博五」,现有选项: 大一 | 大二 | 大三"}),
    }))
    out = bt.browser_fill(ctx, {"年级": "博五"})
    assert "大一 | 大二 | 大三" in out          # 选项列表要带回给模型


def test_click_and_fill_require_open_page(ctx):
    assert bt.browser_click(ctx, "1").startswith("错误:")
    assert bt.browser_fill(ctx, {"a": "b"}).startswith("错误:")


# ------------------------------------- 接管现有页 / 按帧读取(教务课表导入这条路)

JW_TARGETS = {"targetInfos": [
    {"type": "page", "targetId": "TJW", "url": "https://jw.educationgroup.cn/gzasc/xsMain.jsp",
     "title": "教务系统"},
    {"type": "iframe", "targetId": "TIF", "url": "https://jw.educationgroup.cn/xskb/xskb_list.do",
     "title": ""},
    {"type": "page", "targetId": "OTHER", "url": "https://example.com/", "title": "别的"},
]}


def test_adopt_page_takes_over_the_users_tab_without_opening_a_new_one(isolated_state, monkeypatch):
    """登录完那张页是**用户的**:按 URL 认领它,不能再 createTarget,也不能关它。"""
    monkeypatch.setattr(br, "_ensure_conn",
                        lambda **kw: (FakeConn(), "Edg/153.0", "attach"))
    br._STATE.conn = FakeConn()
    br._STATE.mode = "attach"
    FakeConn.results = {"Target.getTargets": JW_TARGETS}
    ours = br._ensure_session()                       # 我们自己那张空白页
    FakeConn.calls.clear()
    got = br.adopt_page("educationgroup.cn", mode="attach")
    assert got["url"].endswith("xsMain.jsp") and got["mode"] == "attach"
    names = _method_names()
    assert "Target.createTarget" not in names, "接管时不该再开一张新页"
    assert "Target.closeTarget" in names, "我们自己那张旧页该让位"
    sess = br._STATE.sessions[br._session_key()]
    assert sess.target_id == "TJW" and sess.adopted is True
    assert ours.target_id not in [s.target_id for s in br._STATE.sessions.values()]


def test_adopt_page_refuses_to_boot_a_browser_just_to_look(isolated_state):
    """mode=launch 且还没有连接 = 没有"已经开着的页":回 None,别为找页拉起浏览器。"""
    assert br._STATE.conn is None
    assert br.adopt_page("educationgroup.cn", mode="launch") is None
    assert "__launch__" not in [c[0] for c in FakeConn.calls]
    assert br._STATE.sessions == {}


def test_adopt_page_says_nothing_found_when_no_page_matches(isolated_state, monkeypatch):
    monkeypatch.setattr(br, "_ensure_conn",
                        lambda **kw: (FakeConn(), "Edg/153.0", "attach"))
    br._STATE.conn = FakeConn()
    FakeConn.results = {"Target.getTargets": {"targetInfos": [
        {"type": "page", "targetId": "X", "url": "https://example.com/", "title": "别的"}]}}
    assert br.adopt_page("educationgroup.cn") is None
    assert br._STATE.sessions == {}, "没找到就不该登记会话"


def test_adopted_tab_is_never_closed_on_our_behalf(isolated_state):
    """脱手 ≠ 关页:回收与收工都不能替用户关掉他的教务窗口。"""
    conn = br._ensure_conn()[0]
    sess = br.PageSession(conn=conn, target_id="TJW", cdp_session="S1", adopted=True)
    br._STATE.sessions[br._session_key()] = sess
    br._close_session_locked(br._session_key(), sess)
    assert "Target.closeTarget" not in _method_names()
    assert br._STATE.sessions == {}

    sess2 = br.PageSession(conn=conn, target_id="TJW2", cdp_session="S2", adopted=True)
    br._STATE.sessions["a"] = sess2
    for i in range(br.MAX_SESSIONS + 3):              # 挤爆上限也不该殃及它
        s = br.PageSession(conn=conn, target_id=f"T{i}", cdp_session=f"S{i}")
        s.opened_at = float(i)
        br._STATE.sessions[f"k{i}"] = s
    br._prune_locked(keep="k0")
    assert "a" in br._STATE.sessions
    br._STATE.sessions.clear()
    br._STATE.sessions["a"] = sess2
    FakeConn.calls.clear()
    br.shutdown()
    assert "Target.closeTarget" not in _method_names()


FRAME_TREE = {"frameTree": {
    "frame": {"id": "F0", "url": "https://jw.educationgroup.cn/gzasc/xsMain.jsp", "name": ""},
    "childFrames": [
        {"frame": {"id": "F1", "url": "https://jw.educationgroup.cn/gzasc_jsxsd/xskb/xskb_list.do",
                   "name": "Frame1"},
         "childFrames": [{"frame": {"id": "F2",
                                    "url": "https://jw.educationgroup.cn/gzasc_jsxsd/xskb/xskb_list.do#print",
                                    "name": ""}}]},
        {"frame": {"id": "F3", "url": "https://jw.educationgroup.cn/other"}},
    ]}}


def _ready_page(monkeypatch, results: dict) -> None:
    conn = FakeConn()
    br._STATE.conn = conn
    br._STATE.mode = "launch"
    sess = br.PageSession(conn=conn, target_id="TJW", cdp_session="S1")
    br._STATE.sessions[br._session_key()] = sess
    FakeConn.results = results


def test_page_frames_sees_in_process_iframes(isolated_state, monkeypatch):
    """进程内 iframe 不作为 target 出现:只按 target 找会误判"这页是空的"。"""
    _ready_page(monkeypatch, {"Page.getFrameTree": FRAME_TREE})
    frames = br.page_frames()
    assert [f["frameId"] for f in frames] == ["F0", "F1", "F2", "F3"]
    assert [f["depth"] for f in frames] == [0, 1, 2, 1]
    assert "xskb_list.do" in frames[1]["url"]


def test_frame_helpers_need_a_page_first(isolated_state):
    with pytest.raises(br.BrowserError) as e:
        br.page_frames()
    assert "adopt_page" in str(e.value)


def test_eval_in_frame_uses_the_isolated_world_context(isolated_state, monkeypatch):
    """Runtime.evaluate 默认打在顶层帧:必须带 contextId,且字段名是 executionContextId。"""
    seen = {}

    def fake_call(self, method, params=None, session=None, timeout=30.0):
        FakeConn.calls.append((method, params or {}, session))
        if method == "Page.createIsolatedWorld":
            return {"executionContextId": 77}
        if method == "Runtime.evaluate":
            seen.update(params or {})
            return {"result": {"value": "ok"}}
        return _DEFAULTS.get(method, {})

    monkeypatch.setattr(FakeConn, "call", fake_call)
    _ready_page(monkeypatch, {})
    assert br.eval_in_frame("F1", "1+1") == "ok"
    assert seen["contextId"] == 77 and seen["returnByValue"] is True

    # JS 抛错要如实报错,不能当成"这帧没内容"
    monkeypatch.setattr(FakeConn, "call", lambda self, m, p=None, session=None, timeout=30.0:
                        ({"exceptionDetails": {"exception": {"description":
                            "SyntaxError: bad\n  at line 1"}}}
                         if m == "Runtime.evaluate" else {"executionContextId": 5}))
    with pytest.raises(br.BrowserError) as e:
        br.eval_in_frame("F1", "坏表达式")
    assert "出错" in str(e.value) and "SyntaxError" in str(e.value)


def test_read_frames_filters_by_url_and_keeps_going_when_one_frame_dies(isolated_state, monkeypatch):
    grid = json.dumps({"rows": [["星期一"]]})
    calls = {"n": 0}

    def fake_call(self, method, params=None, session=None, timeout=30.0):
        FakeConn.calls.append((method, params or {}, session))
        if method == "Page.getFrameTree":
            return FRAME_TREE
        if method == "Page.createIsolatedWorld":
            calls["n"] += 1
            if (params or {}).get("frameId") == "F3":
                raise br.BrowserError("帧刚被换掉")
            return {"executionContextId": 9}
        if method == "Runtime.evaluate":
            return {"result": {"value": grid}}
        return _DEFAULTS.get(method, {})

    monkeypatch.setattr(FakeConn, "call", fake_call)
    _ready_page(monkeypatch, {})
    hits = br.read_frames("GRID", url_like="xskb")
    assert len(hits) == 2 and {h["frameId"] for h in hits} == {"F1", "F2"}
    assert json.loads(hits[0]["value"])["rows"] == [["星期一"]]
    assert hits[0]["truncated"] is False
    # 不筛 URL:F3 报错也只是这一帧带 error,别的帧照样返回
    all_hits = br.read_frames("GRID")
    assert [h["frameId"] for h in all_hits] == ["F0", "F1", "F2", "F3"]
    bad = [h for h in all_hits if h.get("error")]
    assert len(bad) == 1 and bad[0]["frameId"] == "F3" and bad[0]["value"] is None
    assert sum(1 for h in all_hits if not h.get("error")) == 3


def test_read_frames_marks_truncation_instead_of_silently_cutting(isolated_state, monkeypatch):
    big = "课" * 5000

    def fake_call(self, method, params=None, session=None, timeout=30.0):
        if method == "Page.getFrameTree":
            return {"frameTree": {"frame": {"id": "F0", "url": "u"}}}
        if method == "Page.createIsolatedWorld":
            return {"executionContextId": 3}
        if method == "Runtime.evaluate":
            return {"result": {"value": big}}
        return {}

    monkeypatch.setattr(FakeConn, "call", fake_call)
    _ready_page(monkeypatch, {})
    hit = br.read_frames("T", max_chars=100)[0]
    assert hit["truncated"] is True and hit["chars"] == 5000 and len(hit["value"]) == 100
    whole = br.read_frames("T")[0]
    assert whole["truncated"] is False and whole["value"] == big


# ---------------------------------------------------------------- 看得见窗口

def test_ensure_visible_reuses_attach_and_visible_launch(isolated_state):
    br._STATE.conn = FakeConn()
    br._STATE.mode = "attach"
    br._STATE.headless = True                # attach 时这个字段无意义
    assert br.ensure_visible()["ok"]
    assert "__launch__" not in [c[0] for c in FakeConn.calls]
    br._STATE.mode = "launch"
    br._STATE.headless = False
    assert br.ensure_visible()["ok"]
    assert "__launch__" not in [c[0] for c in FakeConn.calls], "有窗口的实例被白起了第二个"


def test_ensure_visible_restarts_headless_instance_with_a_window(isolated_state):
    br._STATE.conn = FakeConn()
    br._STATE.mode = "launch"
    br._STATE.headless = True
    got = br.ensure_visible()
    launches = [c for c in FakeConn.calls if c[0] == "__launch__"]
    assert len(launches) == 1 and launches[0][1]["headless"] is False
    assert got["ok"] and br._STATE.headless is False and br._STATE.mode == "launch"


def test_ensure_visible_refuses_to_yank_a_running_task_window(isolated_state):
    """无窗口的自动浏览正跑到一半:掀它的桌等于让同伴现场蒸发,只能如实说等一等。"""
    br._STATE.conn = FakeConn()
    br._STATE.mode = "launch"
    br._STATE.headless = True
    br._STATE.sessions[br._session_key()] = br.PageSession(
        conn=br._STATE.conn, target_id="T1", cdp_session="S1",
        owner=weakref.ref(threading.current_thread()))
    with pytest.raises(br.BrowserUnavailable) as e:
        br.ensure_visible()
    assert "无窗口" in str(e.value)
    assert br._STATE.conn is not None, "报错时不该顺手把同伴的浏览器关掉"
