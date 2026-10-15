"""MCP prompts 的**工具面**(v0.9.175)。

协议那半 v0.9.172+ 就接了(握手后拉 `prompts/list`、`get_prompt()` 也在),欠的一直是
"模型有没有一条路去用它" —— 这一支文件钉的就是那件被漏掉的事:

  · 注册表里必须有 `mcp_prompt` 这只工具(否则 `prompts/list` 是白拉,而**界面上一切正常**);
  · 清单只报服务器声明过的,没声明照实说,不编;
  · `get` 要能把参数递过去(参数是模板的一部分,吞掉参数=拿到一段半成品指示);
  · 信任级是 `external`、非只读 —— 取回来的是**那台服务器写的一段指示**,过确认框、
    进上下文要套溯源围栏,与 `mcp_resource` 同一条红线;
  · 网页版与服务端模式都没有它(访客与外部客户端不该能替这台机器决定读什么)。
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


# ---------------------------------------------------------------- 协议那半(回归用)

def test_start_pulls_prompt_list_when_declared(client):
    assert "prompts" in client.server_caps
    assert [x["name"] for x in client.prompts] == ["summarize"]
    # 参数面要留住 —— 没有它,get 时模型不知道该填什么
    assert client.prompts[0]["arguments"][0]["name"] == "text"


# ---------------------------------------------------------------- 清单

def test_handler_lists_prompts_with_their_arguments(client):
    mcp_mod.set_manager_hook(lambda: _FakeMgr(client))
    try:
        out = mcp_mod.mcp_prompt_handler(None, action="list")
        assert "[fake] summarize" in out, out
        assert "text" in out and "(选填)" not in out, "必填参数不许标成选填"
        assert "把一段文字总结成三点" in out
    finally:
        mcp_mod.set_manager_hook(lambda: None)


def test_handler_says_so_when_nothing_is_declared_or_online():
    mcp_mod.set_manager_hook(lambda: _FakeMgr(_NoPromptClient()))
    try:
        assert "没声明 prompts" in mcp_mod.mcp_prompt_handler(None, action="list")
    finally:
        mcp_mod.set_manager_hook(lambda: None)
    mcp_mod.set_manager_hook(lambda: None)
    assert "拿不到在线的 MCP 管理器" in mcp_mod.mcp_prompt_handler(None, action="list")


# ---------------------------------------------------------------- 取一条

def test_handler_get_passes_arguments_through(client):
    mcp_mod.set_manager_hook(lambda: _FakeMgr(client))
    try:
        out = mcp_mod.mcp_prompt_handler(None, action="get", sid="fake",
                                         name="summarize",
                                         arguments={"text": "甲乙丙丁"})
        assert out.startswith("[MCP prompt") and "服务器 fake" in out
        assert "请把下面这段总结成三点" in out
        assert "甲乙丙丁" in out, "参数没递过去 = 拿到一段半成品指示"
        assert "[user]" in out, "角色要留住:那是服务器写的指示,不是学长自己的话"
    finally:
        mcp_mod.set_manager_hook(lambda: None)


def test_handler_get_reports_the_three_ways_to_get_it_wrong(client):
    mcp_mod.set_manager_hook(lambda: _FakeMgr(client))
    try:
        assert "没有编号为" in mcp_mod.mcp_prompt_handler(
            None, action="get", sid="ghost", name="summarize")
        assert "要带 name" in mcp_mod.mcp_prompt_handler(None, action="get", sid="fake")
        assert "未知 action" in mcp_mod.mcp_prompt_handler(None, action="删除")
        # 对端回错误(问一个它没有的模板)不许变成静默空串
        err = _RaiseClient()
        mcp_mod.set_manager_hook(lambda: _FakeMgr(err))
        assert "取 prompt「nope」失败" in mcp_mod.mcp_prompt_handler(
            None, action="get", sid="err", name="nope")
    finally:
        mcp_mod.set_manager_hook(lambda: None)


class _FakeMgr:
    def __init__(self, client):
        self.clients = [client]


class _NoPromptClient:
    cfg = type("C", (), {"sid": "plain"})()
    server_caps = {}
    prompts: list = []


class _RaiseClient:
    cfg = type("C", (), {"sid": "err"})()
    server_caps = {"prompts": {}}
    prompts: list = []

    def get_prompt(self, name, arguments=None):
        raise mcp_mod.McpError("unknown prompt")


# ---------------------------------------------------------------- 工具形状与四处排除

def test_tool_shape_and_exclusions():
    from minicode.tools.registry import build_default_registry
    tool = next((t for t in build_default_registry() if t.name == "mcp_prompt"), None)
    assert tool is not None, "协议接了而模型没有路去用 —— 这一格就是白拉一次 prompts/list"
    assert tool.trust == "external", "服务器写的指示按外部内容处理,不是学长自己的规矩"
    assert tool.read_only is False, "get 取回外部内容,必须过确认框(与 mcp_resource 同档)"
    from minicode.web_profile import EXCLUDED_TOOLS
    assert "mcp_prompt" in EXCLUDED_TOOLS, "网页版访客不该能替这台机器决定读什么"
    from minicode import mcp_server
    assert "mcp_prompt" in mcp_server.EXCLUDED_TOOLS, \
        "--mcp-serve 那一侧也不放开:外部客户端触发本机去读服务器指示"
