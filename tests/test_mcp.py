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


# ---------------------------------------------------------------- 二进制载体
# 钉的是"整坨 base64 不进 transcript"这一**类**,而不是某一句文案:
# 工具结果只能是字符串,把 data 字段序列化出去 = 每个后续回合都重付一遍,
# 而模型读不出内容。v0.9.121 前 image 就落在那条 else 里。

def _b64(n: int) -> str:
    import base64
    return base64.b64encode(b"\x89PNG\r\n" + b"x" * n).decode()


def test_image_result_never_inlines_the_payload():
    payload = _b64(3000)
    out = format_call_result({"content": [{"type": "image", "data": payload,
                                           "mimeType": "image/png"}]})
    assert payload not in out                 # 一个字符都不许漏进回话
    assert "未内联" in out and "image/png" in out
    assert "字节" in out


def test_audio_and_binary_resource_also_not_inlined():
    payload = _b64(2000)
    out = format_call_result({"content": [
        {"type": "audio", "data": payload, "mimeType": "audio/wav"},
        {"type": "resource", "resource": {"uri": "file:///p.pdf",
                                          "blob": payload,
                                          "mimeType": "application/pdf"}}]})
    assert payload not in out
    assert "语音未内联" in out and "二进制资源未内联" in out
    assert "file:///p.pdf" in out             # URI 要说得出,否则没路可接


def test_unknown_type_with_long_payload_not_inlined_short_one_still_dumped():
    """没认出的类型照常序列化(不假装认识),但带着长载荷的不能整坨塞。"""
    long_payload = _b64(1500)
    out = format_call_result({"content": [{"type": "drawing", "data": long_payload,
                                          "mimeType": "image/svg"}]})
    assert long_payload not in out and "未内联" in out
    kept = format_call_result({"content": [{"type": "drawing", "data": "abc"}]})
    assert '"data": "abc"' in kept           # 短载荷仍是原样序列化,别顺手扔掉


def test_binary_size_estimate_is_marked_as_estimate():
    out = format_call_result({"content": [{"type": "image", "data": "!!!not-b64!",
                                           "mimeType": "image/png"}]})
    assert "约" in out and "字节" in out      # 估出来的数不许写成精确数


# ---------------------------------------------------------------- 结构化结果

def test_structured_content_is_not_reported_as_empty():
    """声明了 outputSchema 的服务器把结果放在 structuredContent 里 ——
    漏读它就是"把有数据说成没数据",比没数据更难查。"""
    out = format_call_result({"content": [],
                              "structuredContent": {"score": 91, "gpa": "3.7"}})
    assert "空结果" not in out
    assert "91" in out and "gpa" in out
    both = format_call_result({"content": [{"type": "text", "text": "读完了"}],
                               "structuredContent": {"n": 1}})
    assert both.startswith("读完了") and "结构化结果" in both


def test_structured_content_says_how_much_was_cut():
    out = format_call_result({"structuredContent": {"rows": ["长" * 80] * 200}})
    assert "已截到" in out and "原长" in out   # 截了要报原长,不许装作是全部


def test_error_flag_survives_structured_only_result():
    out = format_call_result({"isError": True,
                              "structuredContent": {"why": "额度用尽"}})
    assert out.startswith("错误: ") and "额度用尽" in out


# ------------------------------------------------------------------ 协议往返

def test_start_lists_tools(client):
    names = [t["name"] for t in client.list_tools()]
    assert names == ["echo", "fail"]
    assert client.server_info.get("name") == "fake-mcp"
    assert client.alive


def test_call_tool_returns_text(client):
    assert client.call_tool("echo", {"message": "你好"}) == "echo: 你好"


def test_progress_and_log_notifications_are_observable(client):
    """stdio 通知不能再被读线程丢掉：进度带 token，日志进有界事件环。"""
    seen = []
    client.on_event = seen.append
    assert client.call_tool("echo", {"message": "观测"}) == "echo: 观测"
    events = client.event_snapshot()
    assert [e["kind"] for e in events[-2:]] == ["progress", "log"]
    progress, log = events[-2:]
    assert progress["token"] and progress["progress"] == 1 and progress["total"] == 2
    assert progress["message"] == "准备回显"
    assert log["level"] == "info" and log["logger"] == "fake"
    assert "echo 观测" in log["data"]
    assert [e["kind"] for e in seen[-2:]] == ["progress", "log"]
    assert client.last_event_line().startswith("日志[info] fake:")


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


# ------------------------------------------------------------------ 工具级名单
# 钉的不是"有没有这两个键",而是三件会**静默**坏掉的事:
# ① 名单只能收窄,不能扩权;② 报给界面与模型的只数必须是真挂上来的;
# ③ 写错的规则要说出口 —— 配了却没少一只,是查不出来的假配置。

def _mgr(sid, **kw):
    from minicode.mcp import connect_servers
    return connect_servers([_fake(sid, **kw)])


def test_deny_list_hides_a_tool_and_the_counts_tell_the_truth():
    mgr = _mgr("dl", description="名单服务", deny_tools=["fail"])
    try:
        assert [t.name for t in mgr.build_tools()] == ["mcp_dl_echo"]
        row = mgr.tools_summary()[0]
        assert row["n_tools"] == 1            # 报给前端的必须是能调的那几只
        assert row["n_server_tools"] == 2     # 服务器自己声明的数另说,不混为一谈
        assert "fail" in row["hidden_line"] and "deny" in row["hidden_line"]
    finally:
        mgr.close_all()


def test_allow_list_mounts_only_the_listed_names():
    mgr = _mgr("al", allow_tools=["echo"])
    try:
        assert [t.name for t in mgr.build_tools()] == ["mcp_al_echo"]
        assert "不在 allow 名单里" in mgr.tools_summary()[0]["hidden_line"]
    finally:
        mgr.close_all()


def test_deny_wins_over_allow():
    """两份名单同时命中同一只时,严的那份说话(与 permissions.json 的 deny 优先同口径)。"""
    mgr = _mgr("dw", allow_tools=["echo", "fail"], deny_tools=["echo"])
    try:
        assert [t.name for t in mgr.build_tools()] == ["mcp_dw_fail"]
    finally:
        mgr.close_all()


def test_only_a_trailing_star_is_a_wildcard():
    """`e*` 认前缀;`*x` 不当通配 —— 会意外匹配的规则比没有规则更糟,
    而它必须被点名成"没匹配到任何工具",不能装作生效了。"""
    mgr = _mgr("pw", deny_tools=["e*"])
    try:
        assert [t.name for t in mgr.build_tools()] == ["mcp_pw_fail"]
    finally:
        mgr.close_all()
    mgr2 = _mgr("pw2", deny_tools=["*x"])
    try:
        assert len(mgr2.build_tools()) == 2
        assert "没匹配到任何工具" in mgr2.tools_summary()[0]["hidden_line"]
    finally:
        mgr2.close_all()


def test_a_list_can_never_invent_a_tool():
    """只收窄不扩权:allow 里写一个服务器没声明的名字,结果是**一只都不挂**,
    而不是"照单变出几只来"。"""
    mgr = _mgr("inv", allow_tools=["no_such_tool"])
    try:
        assert mgr.build_tools() == []
        line = mgr.tools_summary()[0]["hidden_line"]
        assert "no_such_tool" in line and "没匹配到" in line
    finally:
        mgr.close_all()


def test_probe_server_reports_mounted_not_declared():
    """添加服务器那句「探到 N 个工具」以前报的是服务器声明的数;配了名单之后
    那个数会和"模型真能调的"分家,而同学是照这句话决定要不要再配一台的。"""
    from minicode import mcp as mcp_mod
    probe = mcp_mod.probe_server({"id": "pb", "command": sys.executable,
                                  "args": [str(FAKE_SERVER)],
                                  "deny_tools": ["fail"]})
    assert probe["ok"] is True
    assert probe["n_tools"] == 1 and probe["n_server_tools"] == 2
    assert "fail" in probe["hidden_line"]


def test_lists_round_trip_and_absent_keys_stay_absent():
    """老配置回写不能凭空多出一个空名单键(与 permissions.json 的 decision 缺省
    同一口径:没配 = 一个字都不改)。"""
    servers = parse_servers([{"id": "a", "command": "npx", "deny_tools": ["x"]},
                             {"id": "b", "command": "npx"}])
    raw = servers_to_raw(servers)
    assert raw[0]["deny_tools"] == ["x"]
    assert "allow_tools" not in raw[0]
    assert "deny_tools" not in raw[1] and "allow_tools" not in raw[1]


def test_a_malformed_tools_list_does_not_take_the_registry_down():
    """`tools/list` 是第三方服务器发的:里面混进不是字典的条目时,既不能炸掉
    整张注册表(表现是配了 MCP 之后学长起不来),也不能把那条算进"工具数"。"""
    from minicode.mcp import McpManager, McpServerConfig

    class _BadClient:
        cfg = McpServerConfig(sid="bad", description="坏清单服务")
        server_info = {"name": "bad-server"}

        def list_tools(self):
            return [{"name": "ok"}, 7, None, "x"]

        def caps_line(self):
            return ""

        def call_tool(self, name, arguments):
            return "不该被调用"

    mgr = McpManager([_BadClient.cfg])
    mgr.clients = [_BadClient()]
    assert [t.name for t in mgr.build_tools()] == ["mcp_bad_ok"]
    row = mgr.tools_summary()[0]
    assert row["n_tools"] == 1 and row["n_server_tools"] == 1
    assert row["hidden_line"] == ""      # 没配名单就不编一句"挡了什么"


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
                            "n_server_tools": 2, "n_hidden": 0, "hidden_line": "",
                            "server": "fake-mcp",
                            # 不抄第二份措辞:能力那句由 caps_line 自己说,这里只钉
                            # "summary 的每一行都带着它"(形状仍是精确比对,多一个键就红)
                            "caps": mgr.clients[0].caps_line(),
                            "last_event": ""}]

        ctx = ToolContext(workspace=Path.cwd(), config=Config())
        out = tools[0].handler(ctx, message="hi")
        assert out == "echo: hi"
        assert [e["kind"] for e in mgr.events(limit=2)] == ["progress", "log"]
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
