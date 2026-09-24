"""网页版服务测试:工具白名单、越权拒绝、跨站拦截、访客隔离、SSE 回合、并发上限。

这些断言是本次交付的安全边界 —— 白名单写错、门面漏开放一个方法,
都应该在这里红掉,而不是等到对外开放之后。
"""
import http.client
import json
import threading
import time

import pytest

from minicode.config import Config, Provider
from minicode.llm import AssistantTurn
from minicode.web_profile import EXCLUDED_TOOLS, PUBLIC_TOOLS
from minicode.web_server import DailyCounter, RateLimiter, create_server


# ---------------------------------------------------------------- 夹具


# 整文件打 slow:实测 51 s(2026-09-25 --durations=0)—— 慢在真线程/
# 真 socket/真超时,不是算法。日常只跑 `pytest -m "not slow"` 时它会被跳过。
pytestmark = pytest.mark.slow


class FakeLLM:
    """一次回合直接给出正文;可指定多轮脚本。"""

    def __init__(self, turns=None, delay=0.0):
        self.turns = list(turns) if turns else [AssistantTurn(text="图书馆在旧图那栋,学弟。")]
        self.delay = delay

    def stream_chat(self, messages, tools, on_text=None, on_reasoning=None, on_retry=None):
        if self.delay:
            time.sleep(self.delay)
        turn = self.turns.pop(0) if self.turns else AssistantTurn(text="(无更多回合)")
        if on_text and turn.text:
            on_text(turn.text)
        return turn


@pytest.fixture
def config():
    return Config(
        providers=[Provider(id="t", name="Test", base_url="http://10.0.0.1:8080/v1",
                            api_key="secret-key", model="test-model")],
        active_provider_id="t",
    )


@pytest.fixture
def server(tmp_path, config):
    """建好前端目录与服务,起在随机端口上。"""
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text(
        "<html><head><title>引路学长</title></head><body>hi</body></html>", encoding="utf-8")
    (frontend / "bridge-web.js").write_text("// shim\n", encoding="utf-8")

    httpd = create_server(
        config, tmp_path, host="127.0.0.1", port=0,
        frontend_dir=frontend,
        sessions_root=tmp_path / "web-sessions",
        config_path=tmp_path / "config.json",
        llm_factory=lambda cfg: FakeLLM(),
    )
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        t.join(timeout=5)


class Client:
    """带 cookie 的极简 HTTP 客户端。"""

    def __init__(self, httpd):
        self.port = httpd.server_address[1]
        self.cookie = ""

    def post(self, name, args=None, origin=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        headers = {"Content-Type": "application/json"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        if origin:
            headers["Origin"] = origin
        body = raw if raw is not None else json.dumps(args if args is not None else {},
                                                      ensure_ascii=False)
        conn.request("POST", "/api/" + name, body=body.encode("utf-8"), headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        set_cookie = resp.getheader("Set-Cookie")
        status = resp.status
        conn.close()
        if set_cookie and not self.cookie:
            self.cookie = set_cookie.split(";")[0]
        try:
            payload = json.loads(data.decode("utf-8")) if data else {}
        except json.JSONDecodeError:
            payload = {"_raw": data.decode("utf-8", "replace")}
        return status, payload

    def get(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        headers = {"Cookie": self.cookie} if self.cookie else {}
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        status = resp.status
        ctype = resp.getheader("Content-Type")
        conn.close()
        return status, ctype, data.decode("utf-8", "replace")


class SSECollector(threading.Thread):
    """后台读事件流,直到 stop_when 判定收够了为止。"""

    def __init__(self, port, cookie, stop_when=None, timeout=10):
        super().__init__(daemon=True)
        self.port = port
        self.cookie = cookie
        self.stop_when = stop_when or (
            lambda evs: any(e.get("type") == "finish_stream" for e in evs))
        self.timeout = timeout
        self.events = []
        self.error = None
        self.done = threading.Event()

    def run(self):
        try:
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=self.timeout)
            conn.request("GET", "/api/events", headers={"Cookie": self.cookie})
            resp = conn.getresponse()
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                line = resp.fp.readline()
                if not line:
                    break
                text = line.decode("utf-8", "replace").strip()
                if not text.startswith("data: "):
                    continue
                event = json.loads(text[len("data: "):])
                self.events.append(event)
                if self.stop_when(self.events):
                    break
            conn.close()
        except Exception as e:                      # 读流失败要让断言看得见
            self.error = e
        finally:
            self.done.set()

    def of_type(self, type_name):
        return [e for e in self.events if e.get("type") == type_name]


def make_frontend_ready(client):
    """拿到访客 cookie 并等访客建立完成。"""
    status, payload = client.post("hello")
    assert status == 200 and payload.get("ok"), payload


def is_within(root, child):
    try:
        child.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


# ---------------------------------------------------------------- 静态与注入


def test_index_injects_shim(server):
    client = Client(server)
    status, ctype, body = client.get("/")
    assert status == 200
    assert "text/html" in ctype
    assert "/bridge-web.js" in body          # shim 被注入
    assert body.count("/bridge-web.js") == 1


def test_static_traversal_blocked(server):
    client = Client(server)
    status, _ctype, _body = client.get("/../config.json")
    assert status in (403, 404)              # 绝不能读到工作区文件


# ---------------------------------------------------------------- 门面白名单


def test_owner_only_methods_refused(server):
    client = Client(server)
    make_frontend_ready(client)
    for name in ("get_providers", "save_providers", "set_provider",
                 "test_model", "fetch_models", "get_provider_presets",
                 "set_session_model", "export_session",
                 "get_token_usage",
                 "get_skills", "get_skill_body", "get_skill_channels", "add_skill",
                 "get_plan",
                 "set_sandbox", "set_permission_mode", "consolidate_memory",
                 "pick_attachments"):
        status, payload = client.post(name, {})
        assert status == 200, name
        assert payload.get("ok") is False, f"{name} 竟然放行了:{payload}"
        assert payload.get("msg"), f"{name} 应该给出可读的拒绝原因"


def test_session_search_only_sees_own_logs(server):
    """访客可以搜自己的会话(WebBridge 每人一份私有 sessions 目录),搜不到别人的。"""
    client = Client(server)
    make_frontend_ready(client)
    status, payload = client.post("search_sessions", {"query": "三体"})
    assert status == 200
    assert payload.get("ok") is True
    assert payload["total"] == 0        # 新访客没有历史会话
    assert payload["results"] == []


def test_unknown_method_refused(server):
    client = Client(server)
    make_frontend_ready(client)
    for name in ("bash", "read_file", "task", "eval", "__class__"):
        _status, payload = client.post(name, {})
        assert payload.get("ok") is False, f"{name} 不该存在"


def test_info_is_redacted(server):
    client = Client(server)
    make_frontend_ready(client)
    _status, info = client.post("get_info", {})
    assert info["workspace"] == ""            # 不暴露服务器路径
    assert info["base_url"] == ""             # 不暴露内网模型端点
    assert info["memory_count"] == 0
    assert info["memory_user_count"] == 0
    assert info["mcp_servers"] == []
    assert info["session_key"]                # 前端靠它筛事件
    for provider in info["providers"]:
        assert set(provider) == {"id", "name", "model"}   # 不含 base_url / has_key
    assert "secret-key" not in json.dumps(info, ensure_ascii=False)


def test_get_asset_only_images(server, tmp_path):
    client = Client(server)
    make_frontend_ready(client)
    _status, payload = client.post("get_asset", {"rel_path": "kb/school-gzasc.json"})
    assert payload.get("ok") is False
    assert "图片" in payload.get("msg", "")
    # 通过扩展名闸门后才会去找文件(返回"未找到"而不是"仅支持图片")
    _status, payload = client.post("get_asset", {"rel_path": "img/none.png"})
    assert "未找到" in payload.get("msg", "")


# ---------------------------------------------------------------- 工具白名单


def test_agent_registry_is_readonly_whitelist(server):
    client = Client(server)
    make_frontend_ready(client)
    vid = client.cookie.split("=", 1)[1]
    visitor = server.web_app.visitor(vid)
    names = {t.name for t in visitor.bridge.agent.registry}

    assert names, "白名单不该是空的"
    assert names <= PUBLIC_TOOLS, f"出现了白名单外的工具:{names - PUBLIC_TOOLS}"
    for name in EXCLUDED_TOOLS:
        assert name not in names, f"{name} 必须被排除({EXCLUDED_TOOLS[name]})"
    assert "task" not in names, "子代理会绕过白名单,必须排除"
    assert "read_file" not in names and "bash" not in names
    # 每个保留的工具都必须是只读的(不给同学任何写能力)
    assert all(t.read_only for t in visitor.bridge.agent.registry)


def test_system_prompt_has_no_memory_and_no_coding_rules(server, tmp_path):
    (tmp_path / "memory").mkdir(exist_ok=True)
    (tmp_path / "memory" / "MEMORY.md").write_text(
        "- [2026-09-13][project] 所有者私有笔记:校园 Key 在 config.local.json\n",
        encoding="utf-8")
    client = Client(server)
    make_frontend_ready(client)
    vid = client.cookie.split("=", 1)[1]
    prompt = server.web_app.visitor(vid).bridge.agent.session.messages[0]["content"]

    assert "所有者私有笔记" not in prompt, "长期记忆绝不能进对外提示词"
    assert "# 工作准则" not in prompt, "编码 agent 的工作准则不该出现在对外提示词里"
    assert "read_file" not in prompt and "bash" not in prompt
    assert "校园助手" in prompt


# ---------------------------------------------------------------- 跨站与限流


def test_cross_origin_rejected(server):
    client = Client(server)
    make_frontend_ready(client)
    status, payload = client.post("get_info", {}, origin="http://evil.example")
    assert status == 403
    assert payload.get("ok") is False
    # 同源 Origin 放行
    same = f"127.0.0.1:{server.server_address[1]}"
    status, payload = client.post("get_info", {}, origin=f"http://{same}")
    assert status == 200 and payload.get("ok") is not False


def test_rate_limiter_and_daily_counter():
    limiter = RateLimiter(3)
    assert [limiter.allow() for _ in range(4)] == [True, True, True, False]
    counter = DailyCounter(2)
    assert [counter.allow() for _ in range(3)] == [True, True, False]


# ---------------------------------------------------------------- 访客隔离


def test_two_visitors_are_isolated(server, tmp_path):
    a, b = Client(server), Client(server)
    make_frontend_ready(a)
    make_frontend_ready(b)
    assert a.cookie != b.cookie

    va = server.web_app.visitor(a.cookie.split("=", 1)[1])
    vb = server.web_app.visitor(b.cookie.split("=", 1)[1])
    assert va is not vb
    assert va.ui.queue is not vb.ui.queue
    assert va.bridge is not vb.bridge
    assert va.bridge._runners is not vb.bridge._runners
    # 各自的会话留档目录互不相通(所以会话列表/删除只影响自己)
    assert va.bridge.sessions_dir != vb.bridge.sessions_dir
    assert is_within(tmp_path / "web-sessions", va.bridge.sessions_dir)

    # 会话键各自从 s1 起是正常的(事件不出访客的队列),真正要证明的是上下文不串
    _s, info_b_before = b.post("get_info", {})
    _s, sent = a.post("send_task", {"text": "只有 A 看得到的问题"})
    assert sent.get("ok"), sent
    assert wait_until(lambda: len(server.web_app.visitor(
        a.cookie.split("=", 1)[1]).bridge.agent.session.messages) > 1)

    _s, info_a = a.post("get_info", {})
    _s, info_b = b.post("get_info", {})
    assert info_a["messages"] > info_b["messages"], "A 的对话串到 B 的上下文里了"
    assert info_b["messages"] == info_b_before["messages"], "B 的上下文被 A 影响了"


def test_one_visitor_cannot_see_anothers_sessions(server):
    a, b = Client(server), Client(server)
    make_frontend_ready(a)
    make_frontend_ready(b)
    a.post("send_task", {"text": "第一个访客的问题"})
    time.sleep(0.4)
    _s, listing = b.post("refresh_sessions", {})
    assert listing.get("ok")
    # b 看不到 a 的留档:refresh_sessions 只推自己的目录,事件不带别人内容
    vb = server.web_app.visitor(b.cookie.split("=", 1)[1])
    from minicode.session import list_sessions
    assert list_sessions(vb.bridge.sessions_dir) == []
    va = server.web_app.visitor(a.cookie.split("=", 1)[1])
    assert len(list_sessions(va.bridge.sessions_dir)) == 1


# ---------------------------------------------------------------- 回合与并发


def test_send_task_streams_over_sse(server):
    client = Client(server)
    make_frontend_ready(client)
    # 要等到 busy=False 才算这个回合真正收尾(finish_stream 比它先到)
    reader = SSECollector(client.port, client.cookie, stop_when=lambda evs: (
        any(e.get("type") == "finish_stream" for e in evs)
        and any(e.get("type") == "busy" and e.get("value") is False for e in evs)))
    reader.start()
    time.sleep(0.2)                            # 让 SSE 先接上
    status, payload = client.post("send_task", {"text": "图书馆在哪?"})
    assert status == 200 and payload.get("ok"), payload
    assert reader.done.wait(timeout=10), "没等到回合结束"
    assert reader.error is None, reader.error

    assert reader.of_type("text_delta"), "应当有流式正文"
    assert "旧图" in "".join(e["text"] for e in reader.of_type("text_delta"))
    assert reader.of_type("busy")[0]["value"] is True
    assert any(e["value"] is False for e in reader.of_type("busy"))
    assert reader.of_type("finish_stream")
    assert server.web_app.active_turns == 0    # 收到 busy=False 时名额必须已释放


def test_turn_cap_rejects_second_visitor(tmp_path, config):
    """并发上限:全局只允许 1 个回合时,第二位同学应被友好拒绝而不是排队挂死。"""
    gate = threading.Event()

    class GatedLLM(FakeLLM):
        def stream_chat(self, *args, **kwargs):
            gate.wait(timeout=5)
            return super().stream_chat(*args, **kwargs)

    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("<html><head></head><body></body></html>",
                                         encoding="utf-8")
    httpd = create_server(config, tmp_path, host="127.0.0.1", port=0,
                          frontend_dir=frontend, sessions_root=tmp_path / "ws",
                          config_path=tmp_path / "config.json",
                          turn_cap=1, llm_factory=lambda cfg: GatedLLM())
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        a, b = Client(httpd), Client(httpd)
        make_frontend_ready(a)
        make_frontend_ready(b)
        _s, first = a.post("send_task", {"text": "第一个"})
        assert first.get("ok"), first
        _s, second = b.post("send_task", {"text": "第二个"})
        assert second.get("ok") is False
        assert "同学" in second.get("msg", "")
        assert "忙" in second.get("msg", "") or "稍等" in second.get("msg", "")
        gate.set()
        assert wait_until(lambda: httpd.web_app.active_turns == 0), "名额没释放"
    finally:
        gate.set()
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_bad_request_bodies(server):
    client = Client(server)
    make_frontend_ready(client)
    status, payload = client.post("send_task", raw="{不是 json")
    assert status == 400 and payload.get("ok") is False
    status, payload = client.post("send_task", raw="[1,2,3]")
    assert status == 400 and payload.get("ok") is False
