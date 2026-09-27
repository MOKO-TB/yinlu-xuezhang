"""MCP 收口测试(v0.9.61):断线重连 / 探测闸 / 对话里自助增删与热重载。

三条钉住的硬行为:
①**通道死了才重连** —— McpTransportError(进程死/管道断)触发一次自动重连并
如实标注;服务器明确拒绝(McpError)不重连,重连解决不了"它活着但拒绝了"。
②**探测是落盘的准入闸** —— probe_server 真连一次,连不上的配置绝不写进
config.json(写进去的表现是"每次启动失败一条",几周后才被发现)。
③**热重载不重启应用** —— 桥的 _mcp_admin 改完 config 立刻整组重连,
_mcp_tools 原列表换内容,现有会话的注册表当场去旧换新。
"""
import json
import sys

import pytest

from minicode import mcp as mcp_mod
from minicode.config import Config
from minicode.mcp import (
    McpClient, McpError, McpTransportError, admin, admin_tool, probe_server,
)
from minicode.tools.base import ToolContext

FAKE_SERVER = r"tests\fake_mcp_server.py"


def _fake_config(sid="fake", **kw) -> dict:
    return {"name": sid, "command": sys.executable, "args": [FAKE_SERVER], **kw}


@pytest.fixture
def client():
    c = McpClient(mcp_mod.parse_servers([_fake_config()])[0])
    assert c.start(), c.error
    yield c
    c.close()


# ---------------------------------------------------------------- 断线重连

def test_call_tool_reconnects_after_transport_death(client):
    """进程被杀 → 下一次调用自动重连一次并成功,结果里如实标注发生过重连。"""
    proc = client.transport._proc
    proc.kill()
    proc.wait(timeout=5)
    assert not client.alive
    out = client.call_tool("echo", {"message": "断线后"})
    assert "echo: 断线后" in out
    assert "已自动重连后重试成功" in out
    assert client.alive                      # 重连后是活的


def test_call_tool_server_error_does_not_reconnect(client, monkeypatch):
    """服务器明确拒绝(McpError)不重连 —— 重连解决不了"它活着但拒绝了"。"""

    def no_restart():
        raise AssertionError("协议错误不该触发重连")

    monkeypatch.setattr(client, "restart", no_restart)
    out = client.call_tool("不存在的工具", {})
    assert "失败" in out


def test_restart_refreshes_handshake_explicitly(client):
    """显式 restart:换新通道重新握手,server_info 刷新,对象身份不变。"""
    old_transport = client.transport
    assert client.restart() is True
    assert client.transport is not old_transport
    assert (client.server_info or {}).get("name") == "fake-mcp"
    assert client.call_tool("echo", {"message": "重启后"}) == "echo: 重启后"


def test_transport_error_is_a_distinct_subclass():
    """McpTransportError 是 McpError 的子类:既有 except McpError 的路全兼容。"""
    assert issubclass(McpTransportError, McpError)


# ---------------------------------------------------------------- 探测闸

def test_probe_server_ok_and_bad():
    ok = probe_server(_fake_config("probe-ok"))
    assert ok["ok"] is True and ok["n_tools"] == 2 and ok["server"] == "fake-mcp"
    bad = probe_server({"name": "probe-bad",
                        "command": "no-such-cmd-xz", "args": []})
    assert bad["ok"] is False and bad["error"]


def test_probe_server_rejects_invalid_raw():
    out = probe_server({"name": "缺 command"})
    assert out["ok"] is False and "不合法" in out["error"]


# ---------------------------------------------------------------- 管理面

def test_admin_without_hook_is_honest(monkeypatch):
    monkeypatch.setattr(mcp_mod, "_ADMIN_HOOK", None)
    out = admin("add", name="x", raw=_fake_config("x"))
    assert "不支持" in out and "config.json" in out


def test_admin_delegates_to_hook(monkeypatch):
    seen = {}

    def hook(action, name, raw):
        seen["args"] = (action, name, raw)
        return f"ok:{action}"

    monkeypatch.setattr(mcp_mod, "_ADMIN_HOOK", hook)
    assert admin("add", name="x", raw={"name": "x"}) == "ok:add"
    assert seen["args"][0] == "add" and seen["args"][1] == "x"


def test_admin_tool_list_reads_local_config(tmp_path):
    """list 在任何环境都可用(本地读 ctx.config),不依赖钩子。"""
    cfg = Config(mcp_servers=[{"name": "files", "command": "npx",
                               "args": ["-y", "@x/files"]}])
    ctx = ToolContext(workspace=tmp_path, config=cfg)
    out = admin_tool(ctx, action="list")
    assert "files" in out and "启用" in out and "stdio" in out
    empty = admin_tool(ToolContext(workspace=tmp_path, config=Config()),
                       action="list")
    assert "还没有配置" in empty and "add" in empty


def test_admin_tool_add_without_hook_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_mod, "_ADMIN_HOOK", None)
    ctx = ToolContext(workspace=tmp_path, config=Config())
    out = admin_tool(ctx, action="add", raw=_fake_config("x"))
    assert "不支持" in out


def test_admin_tool_bad_raw_json(tmp_path):
    ctx = ToolContext(workspace=tmp_path, config=Config())
    assert "JSON 对象" in admin_tool(ctx, action="add", raw="{不是 json")


# ---------------------------------------------------------------- 桥端到端(真子进程)

def test_bridge_admin_add_remove_roundtrip(config, tmp_path):
    """GUI 全链:add 真探测→落盘→热重载→注册表换新;remove 反向。"""
    from test_gui_bridge import make

    class _L:
        def stream_chat(self, messages, tools, on_text=None, on_reasoning=None,
                        on_retry=None):
            from minicode.llm import AssistantTurn
            return AssistantTurn(text="ok")

    bridge, _window, _session = make(config, tmp_path, _L())
    raw = _fake_config("fake2")
    out = bridge._mcp_admin("add", "", raw)
    assert "已添加「fake2」" in out and "探到 2 个工具" in out
    assert "已热重载" in out
    saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert any((s.get("id") or s.get("name")) == "fake2"
               for s in saved.get("mcp_servers") or [])
    assert any(t.name.startswith("mcp_fake2_") for t in bridge._mcp_tools)
    assert any(t.name.startswith("mcp_fake2_") for t in bridge.agent.registry)

    # 工具真能调通(热重载后的绑定是活的)
    echo_tool = next(t for t in bridge._mcp_tools
                     if t.name.startswith("mcp_fake2_echo"))
    ctx = ToolContext(workspace=tmp_path, config=config)
    assert "断线后" in echo_tool.handler(ctx, message="断线后") or \
        "echo" in echo_tool.handler(ctx, message="断线后")

    out2 = bridge._mcp_admin("remove", "fake2")
    assert "已移除" in out2 and "已热重载" in out2
    assert not any(t.name.startswith("mcp_fake2_") for t in bridge._mcp_tools)
    assert not any(t.name.startswith("mcp_fake2_") for t in bridge.agent.registry)
    saved2 = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert not any((s.get("id") or s.get("name")) == "fake2"
                   for s in saved2.get("mcp_servers") or [])
