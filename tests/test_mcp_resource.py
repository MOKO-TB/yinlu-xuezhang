"""MCP resources 接入(B1,v0.9.102,P7 方案实施)的端到端与闸。

钉三件事:①握手声明与实现一致(CLIENT_PRIMITIVES 含 resources 就必须真能
list/read);②对端没声明时照实说,不编清单;③read 的内容按外部内容信任级
处理(fence 由 agent 层套,这里钉工具的 trust 标)。
"""
import sys

import pytest

from minicode import mcp as mcp_mod
from minicode.mcp import McpClient

FAKE_SERVER = r"tests\fake_mcp_server.py"


def _client(**kw):
    c = McpClient(mcp_mod.parse_servers(
        [{"name": "fake", "command": sys.executable, "args": [FAKE_SERVER], **kw}])[0])
    assert c.start(), c.error
    yield c
    c.close()


@pytest.fixture
def client():
    yield from _client()


def test_start_pulls_resource_list_when_declared(client):
    assert "resources" in client.server_caps
    assert [x["uri"] for x in client.resources] == ["fake://notes.txt", "fake://logo.png"]


def test_read_resource_text_and_blob(client):
    text, mime = client.read_resource("fake://notes.txt")
    assert "笔记正文" in text and mime == "text/plain"
    blob, mime2 = client.read_resource("fake://logo.png")
    assert "二进制内容" in blob and "未内联" in blob, "base64 绝不灌进上下文"


def test_read_unknown_uri_reports_error(client):
    with pytest.raises(mcp_mod.McpError):
        client.read_resource("fake://nope")


def test_handler_list_and_read(client):
    from minicode import hooks as _hooks  # noqa: F401  (确保模块都在)
    mcp_mod.set_manager_hook(lambda: _FakeMgr(client))
    try:
        listing = mcp_mod.mcp_resource_handler(None, action="list")
        assert "fake://notes.txt" in listing and "[fake]" in listing
        read = mcp_mod.mcp_resource_handler(None, action="read",
                                            sid="fake", uri="fake://notes.txt")
        assert read.startswith("[MCP 资源 fake://notes.txt")
        assert "笔记正文" in read
        # 没声明/未知 sid/未知 action 三种照实
        capped = _FakeMgr(_NoResClient())
        mcp_mod.set_manager_hook(lambda: capped)
        assert "没声明 resources" in mcp_mod.mcp_resource_handler(None, action="list")
        assert "没有编号为" in mcp_mod.mcp_resource_handler(
            None, action="read", sid="ghost", uri="x")
        assert "未知 action" in mcp_mod.mcp_resource_handler(None, action="删除")
    finally:
        mcp_mod.set_manager_hook(lambda: None)


class _FakeMgr:
    def __init__(self, client):
        self.clients = [client]


class _NoResClient:
    cfg = type("C", (), {"sid": "plain"})()
    server_caps = {}
    resources: list = []


def test_tool_shape_and_exclusions():
    from minicode.tools.registry import build_default_registry
    tool = next(t for t in build_default_registry() if t.name == "mcp_resource")
    assert tool.trust == "external"
    assert tool.read_only is False, "read 是外部内容,必须过确认框(P7 红线)"
    from minicode.web_profile import EXCLUDED_TOOLS
    assert "mcp_resource" in EXCLUDED_TOOLS
