"""MCP 客户端测试:配置解析、协议握手/调用、超时容错、注册表与权限门集成。"""
import sys
from pathlib import Path

import pytest

from minicode.config import Config, _config_from_raw
from minicode.mcp import (
    McpClient, McpServerConfig, format_call_result, parse_servers,
    servers_to_raw, tool_name_for,
)
from minicode.permission import ALLOW, ASK, PermissionGate
from minicode.tools.base import ToolContext

FAKE_SERVER = Path(__file__).resolve().parent / "fake_mcp_server.py"


def _fake(server_id="fake", **kw) -> McpServerConfig:
    return McpServerConfig(sid=server_id, command=sys.executable,
                           args=[str(FAKE_SERVER)], **kw)


@pytest.fixture
def client():
    c = McpClient(_fake())
    assert c.start(), c.error
    yield c
    c.close()


# ------------------------------------------------------------------ 配置解析

def test_parse_servers_skips_invalid_and_normalizes():
    raw = [
        {"id": "fs server!", "command": "npx", "args": "-y", "env": {"A": 1},
         "read_only": True, "description": "文件系统"},
        {"name": "second", "command": "uvx"},
        {"id": "no-command", "args": ["x"]},      # 缺 command → 跳过
        "not-a-dict",                              # 非法项 → 跳过
        {"id": "fs server!", "command": "other"},  # 重名 → 跳过
    ]
    servers = parse_servers(raw)
    assert [s.sid for s in servers] == ["fs_server", "second"]
    assert servers[0].args == ["-y"]               # 字符串 args 归一成列表
    assert servers[0].env == {"A": "1"}
    assert servers[0].read_only is True
    assert servers[0].label == "文件系统"
    assert servers[1].enabled is True and servers[1].read_only is False


def test_parse_servers_non_list():
    assert parse_servers(None) == []
    assert parse_servers({"id": "x"}) == []


def test_servers_to_raw_roundtrip():
    raw = [{"id": "a", "command": "npx", "args": ["-y", "pkg"], "env": {"K": "v"},
            "read_only": True, "description": "d"}]
    again = servers_to_raw(parse_servers(raw))
    assert again[0]["id"] == "a"
    assert again[0]["args"] == ["-y", "pkg"]
    assert again[0]["env"] == {"K": "v"}
    assert again[0]["read_only"] is True
    assert parse_servers(again)[0].sid == "a"


def test_config_keeps_mcp_servers_raw():
    cfg = _config_from_raw({"mcp_servers": [{"id": "a", "command": "npx"}]})
    assert cfg.mcp_servers == [{"id": "a", "command": "npx"}]
    assert _config_from_raw({"mcp_servers": "oops"}).mcp_servers == []


def test_tool_name_sanitized_and_unique():
    taken: set[str] = set()
    assert tool_name_for("fs", "read file", taken) == "mcp_fs_read_file"
    assert tool_name_for("fs", "read file", taken) == "mcp_fs_read_file_2"
    long_name = tool_name_for("s", "x" * 200, taken)
    assert len(long_name) <= 64 and long_name.isascii()


# ------------------------------------------------------------------ 结果拍平

def test_format_call_result():
    assert format_call_result({"content": [{"type": "text", "text": "hi"},
                                           {"type": "text", "text": "there"}]}) == "hi\nthere"
    assert format_call_result({"isError": True,
                               "content": [{"type": "text", "text": "boom"}]}) == "错误: boom"
    assert format_call_result({"content": [{"type": "resource",
                                            "resource": {"uri": "file:///a"}}]}) == "file:///a"
    assert format_call_result(None) == "(MCP 工具无返回内容)"
    assert format_call_result({"content": []}) == "(工具返回空结果)"


# ------------------------------------------------------------------ 协议往返

def test_start_lists_tools(client):
    names = [t["name"] for t in client.list_tools()]
    assert names == ["echo", "fail"]
    assert client.server_info.get("name") == "fake-mcp"
    assert client.alive


def test_call_tool_returns_text(client):
    assert client.call_tool("echo", {"message": "你好"}) == "echo: 你好"


def test_call_tool_error_flag(client):
    assert client.call_tool("fail", {}) == "错误: 故意失败"


def test_call_tool_unknown_name(client):
    out = client.call_tool("nope", {})
    assert out.startswith("错误:") and "未知工具" in out


def test_close_terminates_process(client):
    proc = client._proc
    client.close()
    assert proc.poll() is not None
    assert not client.alive


def test_start_failure_is_tolerated():
    """命令不存在:start() 返回 False 并记录原因,不抛异常。"""
    c = McpClient(McpServerConfig(sid="bad", command="definitely-not-a-real-cmd-xyz",
                                  start_timeout=2))
    assert c.start() is False
    assert c.error
    c.close()


def test_start_timeout_on_silent_server():
    """服务器不响应握手 → 超时返回 False(带 stderr 线索)。"""
    c = McpClient(McpServerConfig(sid="silent", command=sys.executable,
                                  args=["-c", "import time; time.sleep(30)"],
                                  start_timeout=0.6))
    assert c.start() is False
    assert "超时" in c.error
    c.close()


# ------------------------------------------------------------------ 管理器集成

def test_manager_builds_tools_and_summary():
    from minicode.mcp import connect_servers
    mgr = connect_servers([_fake("demo", description="演示服务"),
                           McpServerConfig(sid="off", command="x", enabled=False)])
    try:
        tools = mgr.build_tools()
        assert [t.name for t in tools] == ["mcp_demo_echo", "mcp_demo_fail"]
        assert all("[MCP:演示服务]" in t.description for t in tools)
        assert tools[0].parameters["required"] == ["message"]
        assert all(t.read_only is False for t in tools)
        summary = mgr.tools_summary()
        assert summary == [{"id": "demo", "label": "演示服务", "n_tools": 2,
                            "server": "fake-mcp"}]

        ctx = ToolContext(workspace=Path.cwd(), config=Config())
        out = tools[0].handler(ctx, message="hi")
        assert out == "echo: hi"
    finally:
        mgr.close_all()


def test_read_only_server_marks_tools_read_only():
    from minicode.mcp import connect_servers
    mgr = connect_servers([_fake("ro", read_only=True)])
    try:
        assert all(t.read_only for t in mgr.build_tools())
    finally:
        mgr.close_all()


def test_permission_gate_asks_for_mcp_tools():
    from minicode.mcp import connect_servers
    mgr = connect_servers([_fake("ask"), _fake("trust", read_only=True)])
    try:
        tools = {t.name: t for t in mgr.build_tools()}
        gate = PermissionGate(Config(permission_mode="confirm"))
        assert gate.check(tools["mcp_ask_echo"], {}) == ASK
        assert gate.check(tools["mcp_trust_echo"], {}) == ALLOW
        gate2 = PermissionGate(Config(permission_mode="accept_all"))
        assert gate2.check(tools["mcp_ask_echo"], {}) == ALLOW
    finally:
        mgr.close_all()


def test_create_agent_registers_mcp_tools_and_closes(config, tmp_path):
    """装配层:配置里的 MCP 服务器真的进了注册表,退出时进程被回收。"""
    from minicode.app_setup import create_agent
    from tests.test_gui_bridge import FakeLLM, FakeWindow
    from minicode.llm import AssistantTurn

    config.mcp_servers = [{"id": "demo", "command": sys.executable,
                           "args": [str(FAKE_SERVER)]}]
    ui = FakeWindow()
    agent, _session = create_agent(config, tmp_path, ui, llm=FakeLLM([AssistantTurn(text="x")]),
                                   sessions_dir=tmp_path / "sessions")
    try:
        names = [t.name for t in agent.registry]
        assert "mcp_demo_echo" in names
        assert "mcp_demo_echo" in agent._tool_map
        procs = [c._proc for c in agent.mcp.clients]
        assert procs and all(p.poll() is None for p in procs)
    finally:
        agent.close()
    assert all(p.poll() is not None for p in procs)      # 关停后无孤儿进程


def test_format_call_result_empty_content():
    # 有 content 但文本为空 → 明确说"空结果",不甩一坨 JSON
    assert format_call_result({"content": [{"type": "text", "text": ""}]}) == "(工具返回空结果)"
