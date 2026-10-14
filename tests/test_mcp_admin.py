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


# ------------------------------------------------------------ 能力如实上报

def test_caps_line_says_what_the_server_declared_and_what_we_use(client):
    """对端声明的能力要逐项摊开,并说清**哪几样学长不接** —— 不假装。

    不说清的表现是:用户看到「连上了、2 个工具」,就以为那台服务器的其余能力
    也能用。v0.9.172+ 起 prompts 已接(与 tools/resources 同级),logging 仍未接
    —— 所以"没实现"的名单里只剩 logging,而这一支针正是钉"名单要如实"。
    ⚠ 断言写成**整串**:只写 `"学长只接了 tools" in line` 会被
    `"学长只接了 tools、resources、prompts"` 子串蒙过去(实测踩过)。
    """
    line = client.caps_line()
    assert "对端声明" in line
    for prim in ("tools", "resources", "prompts", "logging"):
        assert prim in line, prim
    assert "学长只接了 tools、resources、prompts" in line, line
    assert "logging 没实现" in line and "不会去调" in line, line


def test_caps_line_when_the_server_declared_nothing():
    """没握过手/对端没声明:照实说,不替它编一份能力表。"""
    c = McpClient(mcp_mod.parse_servers([_fake_config("no-caps")])[0])
    assert c.server_caps == {}
    assert "没在握手里声明能力" in c.caps_line()


def test_restart_refreshes_caps(client):
    """重连先清空再握手:清完忘了填回去,面板上就成了「对端没声明能力」。"""
    assert "对端声明" in client.caps_line()
    assert client.restart() is True
    assert "对端声明" in client.caps_line()


def test_probe_server_reports_caps():
    ok = probe_server(_fake_config("probe-caps"))
    assert ok["ok"] is True and "学长只接了 tools" in ok["caps"]
    bad = probe_server({"name": "probe-bad2",
                        "command": "no-such-cmd-xz", "args": []})
    assert bad["ok"] is False and bad["caps"] == ""


def test_tools_summary_carries_caps():
    """侧栏健康行的悬停提示吃的就是这一份:summary 少了 caps,前端就无话可说。"""
    mgr = mcp_mod.McpManager(mcp_mod.parse_servers([_fake_config("sum")]))
    mgr.connect_all()
    try:
        rows = mgr.tools_summary()
        assert rows and "学长只接了 tools" in rows[0]["caps"]
    finally:
        mgr.close_all()


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


# ------------------------------------------- prompts 与 elicitation(v0.9.172+ 续四)

def test_prompts_are_listed_and_fetched(client):
    """声明了 prompts 才拉清单;get 把模板内容拼成给模型看的文本,
    非文本块**只报类型不内联**(与 resources 的 blob 同一条口径)。"""
    names = [p.get("name") for p in client.prompts]
    assert names == ["summarize"], client.prompts
    out = client.get_prompt("summarize", {"text": "今天下雨了"})
    assert "总结成三点" in out and "今天下雨了" in out
    assert "[user]" in out
    assert "(image 内容不内联)" in out, "图片块不许内联进上下文"


def test_get_prompt_without_content_says_so():
    """服务器给了空结果:如实说,不返回空串装成功(走同一只假客户端,改回包太绕,
    直接对 _request 打桩)。"""
    from minicode.mcp import McpClient
    cfg = mcp_mod.parse_servers([_fake_config("empty-prompt")])[0]
    c = McpClient(cfg)
    c._request = lambda *a, **k: None          # 服务器回了空:如实说"空结果"
    assert "空结果" in c.get_prompt("whatever")
    c._request = lambda *a, **k: {}            # 回了对象但里面没有消息:另一句实话
    assert "没有可显示的内容" in c.get_prompt("whatever")


def test_elicitation_is_declined_by_default_and_sampling_is_refused(client):
    """没装回调:elicitation 按规范回 decline(**不是**静默丢弃 —— 那会让对端干等);
    sampling 明确拒绝并说清理由(与 authorization_code 同一条立场)。"""
    got, err = client._handle_server_request(
        {"id": 7, "method": "elicitation/create",
         "params": {"message": "要删掉这 3 个文件吗?", "requestedSchema": {"type": "object"}}})
    assert err is None and got == {"action": "decline"}

    got2, err2 = client._handle_server_request({"id": 8, "method": "sampling/createMessage"})
    assert got2 is None and err2 and "sampling" in err2["message"] and "不支持" in err2["message"]

    got3, err3 = client._handle_server_request({"id": 9, "method": "别的/方法"})
    assert got3 is None and "不支持" in err3["message"]


def test_elicit_callback_answer_is_passed_through(client):
    """装了回调(有提问通道的一方):用户答什么就回什么,动作原样透传。"""
    seen = {}

    def on_elicit(message, schema):
        seen["message"] = message
        return {"action": "accept", "content": {"ok": True}}

    client.on_elicit = on_elicit
    got, err = client._handle_server_request(
        {"id": 10, "method": "elicitation/create",
         "params": {"message": "允许写这个文件吗?", "requestedSchema": {}}})
    assert err is None and got["action"] == "accept" and got["content"] == {"ok": True}
    assert seen["message"] == "允许写这个文件吗?"

    def boom(*_a):
        raise RuntimeError("界面炸了")
    client.on_elicit = boom
    got2, err2 = client._handle_server_request(
        {"id": 11, "method": "elicitation/create", "params": {"message": "x"}})
    assert err2 is None and got2 == {"action": "decline"}, "回调炸了也要回一条,不能让对端干等"


def test_inbound_request_always_writes_a_reply():
    """传输层兜底:没装钩子时也要**写出一条** JSON-RPC error(方法名带上),
    这是"不支持要说得出口"的最后一环。"""
    from minicode.mcp import _StreamedTransport
    sent = []

    class _T(_StreamedTransport):
        def notify(self, msg):
            sent.append(msg)
    cfg = mcp_mod.parse_servers([_fake_config("inbound")])[0]
    tr = _T(cfg)
    tr._inbound_request({"id": 42, "method": "elicitation/create", "params": {}})
    assert len(sent) == 1 and sent[0]["id"] == 42
    assert sent[0]["error"]["code"] == -32601 and "elicitation/create" in sent[0]["error"]["message"]
