"""MCP 的 HTTP/SSE 传输(minicode/mcp.py 里的 HttpTransport / SseTransport)。

服务器是 stdlib 起的假 MCP(同文件内),两种形态都覆盖:
  /mcp     —— Streamable HTTP:响应可以是 JSON,也可以是一段 SSE
  /sse     —— 旧式 HTTP+SSE:GET 一条长流(先给 endpoint),POST 发请求
只测传输层,协议语义(握手/工具清单/回填文本)沿用 test_mcp.py 那套断言。
"""
import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from minicode.mcp import (
    HttpTransport, McpClient, McpError, McpServerConfig, SseTransport, StdioTransport,
    _iter_sse, _safe_url, make_transport, parse_servers, servers_to_raw,
)

ECHO_TOOL = {"name": "echo", "description": "回显",
             "inputSchema": {"type": "object",
                             "properties": {"message": {"type": "string"}},
                             "required": ["message"]}}


def _reply(msg):
    """假服务器的那点 JSON-RPC 语义:够握手 + 调一次 echo。"""
    rid, method = msg.get("id"), msg.get("method")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid,
                "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                           "serverInfo": {"name": "fake-http", "version": "1"}}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": [ECHO_TOOL]}}
    if method == "tools/call":
        args = (msg.get("params") or {}).get("arguments") or {}
        return {"jsonrpc": "2.0", "id": rid,
                "result": {"content": [{"type": "text",
                                        "text": f"echo: {args.get('message', '')}"}]}}
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": f"未知方法 {method}"}}


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"                 # 没有它就没法稳定地发 Content-Length

    def log_message(self, *args):                 # 别把测试输出搅乱
        pass

    # ---- 小工具
    def _body(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0))
                          or b"{}")

    def _send(self, status, body=b"", ctype="application/json; charset=utf-8", extra=None,
              close=False):
        self.send_response(status)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        if ctype:
            self.send_header("Content-Type", ctype)
        if not close:
            self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close" if close else "keep-alive")
        self.end_headers()
        if body:
            self.wfile.write(body)
        if close:
            self.close_connection = True

    def _authed(self) -> bool:
        want = self.server.require_auth
        return not want or self.headers.get("Authorization") == want

    # ---- 路由
    def do_GET(self):
        if not self.path.startswith("/sse"):
            self._send(404, b'{"error":"no such path"}')
            return
        sid = f"s{len(self.server.queues) + 1}"
        box: queue.Queue = queue.Queue()
        self.server.queues[sid] = box
        self._send(200, b"", ctype="text/event-stream", close=True)
        self.wfile.write(f"event: endpoint\ndata: /messages?sessionId={sid}\n\n".encode())
        self.wfile.flush()
        while True:
            try:
                item = box.get(timeout=8)
            except queue.Empty:
                break
            if item is None:
                break
            self.wfile.write(f"data: {json.dumps(item, ensure_ascii=False)}\n\n".encode())
            self.wfile.flush()

    def do_POST(self):
        if not self._authed():
            self._send(401, b'{"error":"unauthorized"}')
            return
        msg = self._body()
        self.server.requests.append((msg.get("method"), dict(self.headers)))
        if self.path.startswith("/messages"):
            sid = parse_qs(urlsplit(self.path).query).get("sessionId", [""])[0]
            box = self.server.queues.get(sid)
            if box is None:
                self._send(404, b"")
                return
            self._send(202, b"", ctype="")
            if msg.get("id") is not None:
                box.put(_reply(msg))
            return
        if not self.path.startswith("/mcp"):
            self._send(404, b"")
            return
        if self.server.empty_response and msg.get("method") != "initialize":
            self._send(200, b"", ctype="")
            return
        if self.server.bad_body:
            self._send(200, "<html>不是 JSON</html>".encode())
            return
        if msg.get("method") == "initialize":
            self.server.sessions += 1
            self._send(200, json.dumps(_reply(msg), ensure_ascii=False).encode(),
                       extra={"Mcp-Session-Id": f"sess-{self.server.sessions}"})
            return
        if self.server.require_session and not self.headers.get("Mcp-Session-Id"):
            self._send(400, b'{"error":"missing Mcp-Session-Id"}')
            return
        payload = json.dumps(_reply(msg), ensure_ascii=False).encode("utf-8")
        if self.server.sse_body:                 # 同一条报文,装成 SSE 返回
            self._send(200, b"", ctype="text/event-stream", close=True)
            self.wfile.write(f"data: {payload.decode()}\n\n".encode())
            self.wfile.flush()
            return
        self._send(200, payload)

    def do_DELETE(self):
        self.server.deleted += 1
        self._send(200, b"", ctype="")


class _Server(ThreadingHTTPServer):
    """客户端读完就挂断(SSE 响应尤其如此)—— 那不是测试失败,别打 traceback。"""

    daemon_threads = True

    def handle_error(self, request, client_address):
        pass


def _serve(**flags):
    srv = _Server(("127.0.0.1", 0), _Handler)
    srv.queues, srv.requests = {}, []
    srv.sessions, srv.deleted = 0, 0
    srv.require_auth = ""
    srv.sse_body = srv.empty_response = srv.bad_body = False
    srv.require_session = True
    for k, v in flags.items():
        setattr(srv, k, v)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


@pytest.fixture
def server():
    srv = _serve()
    yield srv
    for box in srv.queues.values():
        box.put(None)
    srv.shutdown()
    srv.server_close()


def _url(srv, path="/mcp"):
    return f"http://127.0.0.1:{srv.server_address[1]}{path}"


def _cfg(srv, transport="http", path="/mcp", **kw):
    """服务器配置:transport/url 之外的关键字是给 McpServerConfig 的。"""
    return McpServerConfig(sid="demo", transport=transport, url=_url(srv, path), **kw)


def _headers_for(srv, method):
    """假服务器记下的请求头:按 JSON-RPC 方法名取最后一条。"""
    hits = [h for m, h in srv.requests if m == method]
    return hits[-1] if hits else {}


# ------------------------------------------------------------------ Streamable HTTP

def test_http_handshake_and_call(server):
    c = McpClient(_cfg(server))
    try:
        assert c.start(), c.error
        assert [t["name"] for t in c.list_tools()] == ["echo"]
        assert c.server_info.get("name") == "fake-http"
        assert c.alive and c._proc is None          # HTTP 传输名下没有子进程
        assert c.call_tool("echo", {"message": "你好"}) == "echo: 你好"
    finally:
        c.close()
    assert not c.alive


def test_http_carries_the_session_id_from_initialize(server):
    """服务器只在 initialize 给 Mcp-Session-Id,之后漏了它就回 400 —— 会话必须续上。"""
    c = McpClient(_cfg(server))
    try:
        assert c.start(), c.error
        assert _headers_for(server, "tools/list").get("Mcp-Session-Id")
    finally:
        c.close()


def test_http_sends_headers_and_expands_env_server_vars(monkeypatch):
    """密钥走 ${ENV} 展开:配置里不写明文,请求头里带上真值。"""
    monkeypatch.setenv("FAKE_MCP_TOKEN", "s3cr3t")
    srv = _serve(require_auth="Bearer s3cr3t")
    try:
        cfg = McpServerConfig(sid="auth", transport="http", url=_url(srv),
                              headers={"Authorization": "Bearer ${FAKE_MCP_TOKEN}"})
        c = McpClient(cfg)
        try:
            assert c.start(), c.error
            assert _headers_for(srv, "initialize")["Authorization"] == "Bearer s3cr3t"
        finally:
            c.close()
        # 没配密钥的同一台服务器:连接失败要说清是 401,而不是含糊"连不上"
        bad = McpClient(McpServerConfig(sid="auth", transport="http", url=_url(srv)))
        assert bad.start() is False
        assert "HTTP 401" in bad.error and "unauthorized" in bad.error
    finally:
        srv.shutdown()
        srv.server_close()


def test_http_error_never_contains_the_secret(monkeypatch):
    """报错文本会进日志:密钥既不在 headers 里回显,也不在 URL 查询串里露出来。"""
    monkeypatch.setenv("FAKE_MCP_TOKEN", "s3cr3t")
    srv = _serve(require_auth="Bearer wrong")
    c = McpClient(McpServerConfig(sid="leak", transport="http",
                                  url=_url(srv) + "?key=s3cr3t",
                                  headers={"Authorization": "Bearer ${FAKE_MCP_TOKEN}"}))
    try:
        assert c.start() is False
        assert "s3cr3t" not in c.error, c.error
        assert "s3cr3t" not in c.stderr_tail()
    finally:
        srv.shutdown()
        srv.server_close()


def test_http_accepts_an_sse_shaped_response():
    srv = _serve(sse_body=True)
    try:
        c = McpClient(_cfg(srv))
        try:
            assert c.start(), c.error
            assert c.call_tool("echo", {"message": "流式"}) == "echo: 流式"
        finally:
            c.close()
    finally:
        srv.shutdown()
        srv.server_close()


def test_http_explains_an_empty_or_non_json_reply():
    srv = _serve(empty_response=True)
    try:
        c = McpClient(_cfg(srv))
        assert c.start() is False and "空响应" in c.error and "/mcp" in c.error
    finally:
        srv.shutdown()
        srv.server_close()
    srv2 = _serve(bad_body=True)
    try:
        assert McpClient(_cfg(srv2)).start() is False
    finally:
        srv2.shutdown()
        srv2.server_close()


def test_http_connection_refused_is_start_false_not_a_crash():
    """没人监听的端口:start() 老实返回 False,并把地址(不带查询串)说清楚。"""
    srv = _serve()
    port = srv.server_address[1]
    srv.shutdown()
    srv.server_close()
    c = McpClient(McpServerConfig(sid="gone", transport="http",
                                  url=f"http://127.0.0.1:{port}/mcp?token=abc"))
    assert c.start() is False
    assert "连不上" in c.error and "token=abc" not in c.error
    assert not c.alive


def test_http_close_sends_a_session_delete(server):
    c = McpClient(_cfg(server))
    assert c.start(), c.error
    c.close()
    assert server.deleted == 1                       # 礼貌地让服务器回收会话


# ------------------------------------------------------------------ 旧式 HTTP+SSE

def test_sse_transport_end_to_end(server):
    c = McpClient(_cfg(server, transport="sse", path="/sse"))
    try:
        assert c.start(), c.error
        assert c.alive and isinstance(c.transport, SseTransport)
        assert [t["name"] for t in c.list_tools()] == ["echo"]
        assert c.call_tool("echo", {"message": "老式"}) == "echo: 老式"
    finally:
        c.close()
    assert not c.alive


def test_sse_transport_survives_a_server_that_never_gives_an_endpoint():
    srv = _serve()
    bad = McpServerConfig(sid="dead", transport="sse",
                          url=_url(srv, "/nope"), start_timeout=2)
    c = McpClient(bad)
    try:
        assert c.start() is False
        assert "endpoint" in c.error or "404" in c.error
    finally:
        c.close()
        srv.shutdown()
        srv.server_close()


# ------------------------------------------------------------------ 配置与选择

def test_make_transport_follows_config():
    assert isinstance(make_transport(McpServerConfig(sid="a", command="npx")),
                      StdioTransport)
    assert isinstance(make_transport(McpServerConfig(sid="b", transport="http",
                                                     url="http://x/mcp")), HttpTransport)
    assert isinstance(make_transport(McpServerConfig(sid="c", transport="sse",
                                                     url="http://x/sse")), SseTransport)


def test_parse_servers_accepts_url_only_entries():
    raw = [
        {"id": "local", "command": "npx"},
        {"id": "school", "transport": "streamable-http", "url": "https://mcp/x/mcp",
         "headers": {"Authorization": "Bearer ${T}"}, "read_only": True},
        {"id": "legacy", "transport": "HTTP+SSE", "url": "https://mcp/sse"},
        {"id": "no-url", "transport": "http"},                      # HTTP 却没地址 → 跳过
        {"id": "file-scheme", "transport": "http", "url": "file:///c:/x"},   # 只认 http(s)
        {"id": "typo", "transport": "https-thing", "url": "https://mcp/x"},  # 认不出 → 跳过
    ]
    servers = parse_servers(raw)
    assert [(s.sid, s.transport) for s in servers] == [("local", "stdio"),
                                                       ("school", "http"),
                                                       ("legacy", "sse")]
    assert servers[1].headers == {"Authorization": "Bearer ${T}"}
    assert servers[0].command == "npx" and servers[0].url == ""
    again = servers_to_raw(servers)
    assert again[0] == {"id": "local", "command": "npx"}     # 默认传输不回写,配置不涨噪声
    assert again[1]["transport"] == "http" and again[1]["url"] == "https://mcp/x/mcp"
    assert parse_servers(again)[1].transport == "http"


def test_http_server_without_command_still_roundtrips():
    """老配置只有 command;新配置只有 url —— 两种都要能存回去。"""
    servers = parse_servers([{"id": "u", "transport": "sse", "url": "https://mcp/sse",
                              "description": "校内"}])
    raw = servers_to_raw(servers)
    assert raw[0]["id"] == "u"
    assert "command" not in raw[0]                   # 纯 URL 服务器不该被写成有进程
    assert raw[0]["transport"] == "sse" and raw[0]["url"] == "https://mcp/sse"
    assert raw[0]["description"] == "校内"


# ------------------------------------------------------------------ SSE 解析小函数

@pytest.mark.parametrize("text,want", [
    ("data: {}\n\n", [("", "{}")]),
    ("event: endpoint\ndata: /messages?sid=1\n\n", [("endpoint", "/messages?sid=1")]),
    (": 心跳\n\n", []),                                   # 注释不是事件
    ("data: a\ndata: b\n\n", [("", "a\nb")]),             # 多行 data 要拼起来
    ("data:{\"id\": 1}\n\n", [("", '{"id": 1}')]),       # 冒号后可以没有空格
])
def test_iter_sse_parses_events(text, want):
    assert list(_iter_sse(text.splitlines())) == want


def test_iter_sse_reads_bytes_lines():
    assert list(_iter_sse([b"event: endpoint\n", b"data: /m\n", b"\n"])) == [("endpoint", "/m")]


def test_safe_url_drops_the_query():
    assert _safe_url("https://mcp.school.edu/mcp?key=abc") == "https://mcp.school.edu/mcp"
    assert _safe_url("不是个地址") == "不是个地址"


def test_request_to_a_dead_transport_raises_mcperror(server):
    """传输层单独用也要老实报错(桥/面板可能直接拿它探活)。"""
    t = HttpTransport(McpServerConfig(sid="x", transport="http", url="http://127.0.0.1:1/mcp",
                                      start_timeout=2))
    with pytest.raises(McpError) as e:
        t.request({"jsonrpc": "2.0", "id": 1, "method": "initialize"}, 2)
    assert "连不上" in str(e.value)
