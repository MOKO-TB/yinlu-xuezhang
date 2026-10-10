"""`--mcp-serve`:本 agent 作 MCP 服务端,被外部客户端按 stdio 调用。

这一族钉两件事:① 协议形状与自家客户端(`minicode.mcp.McpClient`)对得上;
② 安全边界不是装饰 —— 白名单外的一律拒、需要点头的一律拒、被拒的那一次留痕。
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from minicode import mcp_server
from minicode.config import Config
from minicode.mcp import McpClient, McpServerConfig

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(Path(__file__).parent))
import main as cli          # noqa: E402


def _init(rid=1, name="Cursor", version="1.2"):
    return {"jsonrpc": "2.0", "id": rid, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": name, "version": version}}}


def _call(rid, name, args):
    return {"jsonrpc": "2.0", "id": rid, "method": "tools/call",
            "params": {"name": name, "arguments": args}}


def _text(resp) -> str:
    return resp["result"]["content"][0]["text"]


# ------------------------------------------------------------------ 最小会话

def test_initialize_and_tool_list(tmp_path):
    server = mcp_server._Server(Config(), tmp_path)
    info = server.handle(_init())["result"]
    assert info["protocolVersion"] == mcp_server.PROTOCOL_VERSION
    assert "tools" in info["capabilities"]
    assert info["serverInfo"]["name"] == mcp_server.SERVER_NAME
    listed = server.handle({"jsonrpc": "2.0", "id": 2,
                            "method": "tools/list"})["result"]["tools"]
    assert len(listed) == len(mcp_server.SERVE_TOOLS)
    names = {t["name"] for t in listed}
    assert "library_search" in names and "gpa_calc" in names
    for t in listed:
        assert t["inputSchema"]["type"] == "object"


def test_notifications_produce_no_response(tmp_path):
    server = mcp_server._Server(Config(), tmp_path)
    assert server.handle({"jsonrpc": "2.0",
                          "method": "notifications/initialized"}) is None
    assert server.handle({"jsonrpc": "2.0", "id": None,
                          "method": "notifications/initialized"}) is None


def test_the_whitelist_and_the_exclusion_table_never_overlap():
    assert not (mcp_server.SERVE_TOOLS & set(mcp_server.EXCLUDED_TOOLS))
    assert "write_file" in mcp_server.EXCLUDED_TOOLS


# ---------------------------------------------------------------- 真能调一只

def test_a_readonly_tool_really_runs(tmp_path):
    """`gpa_calc` 是纯本地计算,不依赖馆藏库/网络 —— 真跑一次看协议回话。"""
    server = mcp_server._Server(Config(), tmp_path)
    resp = server.handle(_call(1, "gpa_calc", {
        "courses": [{"name": "高数", "score": 90, "credit": 4}]}))
    assert "isError" not in resp["result"]
    assert resp["result"]["content"][0]["type"] == "text"
    assert "90" in _text(resp)


# ------------------------------------------------------------------ 负例

def test_a_write_tool_is_refused_at_the_tool_level(tmp_path):
    """写类被拒要长成**工具级失败**,不是 `-32601 未知方法` —— 工具真在注册表里。"""
    server = mcp_server._Server(Config(), tmp_path)
    resp = server.handle(_call(1, "write_file", {"path": "a.py", "content": "x"}))
    assert "error" not in resp, "写类工具不该报成协议错误"
    assert resp["result"]["isError"] is True
    text = _text(resp)
    assert "需要人确认" in text and "无人在场" in text
    assert not (tmp_path / "a.py").exists()


def test_the_refusal_comes_from_the_gate_not_a_missing_tool(tmp_path, monkeypatch):
    """反证:把 `write_file` 放进白名单、权限开到 full,它就真能写。

    证明拒绝来自权限门(白名单/档位),不是"工具压根没挂上来"这种假绿。
    """
    cfg = Config(permission_mode="full")
    monkeypatch.setattr(mcp_server, "SERVE_TOOLS",
                        mcp_server.SERVE_TOOLS | {"write_file"})
    server = mcp_server._Server(cfg, tmp_path)
    resp = server.handle(_call(1, "write_file",
                               {"path": "a.py", "content": "hello"}))
    assert "isError" not in resp["result"], resp
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "hello"


def test_protocol_errors_are_distinguished(tmp_path):
    server = mcp_server._Server(Config(), tmp_path)
    assert server.handle({"jsonrpc": "2.0", "id": 1,
                          "method": "no/such"})["error"]["code"] == -32601
    assert server.handle(_call(2, "no_such_tool", {}))["error"]["code"] == -32602


def test_a_refusal_is_logged_with_the_client_label(tmp_path, monkeypatch):
    from minicode import paths
    monkeypatch.setattr(paths, "APP_DIR", tmp_path / "app")
    server = mcp_server._Server(Config(), tmp_path)
    server.handle(_init(name="Cursor", version="1.2"))
    server.handle(_call(2, "write_file", {"path": "a.py", "content": "x"}))
    log = paths.app_path("injection-events.log")
    assert log.is_file()
    rec = json.loads(log.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["tool"] == "write_file" and rec["user"] == "no"
    assert rec["client"] == "Cursor 1.2" and rec["why"]


# ---------------------------------------------------------- 主循环与 stdout

def _drive(lines: list[str], tmp_path) -> str:
    out = io.StringIO()
    old_in, old_out = sys.stdin, sys.stdout
    sys.stdin, sys.stdout = io.StringIO("\n".join(lines) + "\n"), out
    try:
        assert mcp_server.serve(Config(), tmp_path) == 0
    finally:
        sys.stdin, sys.stdout = old_in, old_out
    return out.getvalue()


def test_stdout_is_pure_protocol_and_bad_lines_are_skipped(tmp_path):
    raw = _drive([
        json.dumps(_init(), ensure_ascii=False),
        "{not json",
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}),
        json.dumps(_call(4, "gpa_calc",
                         {"courses": [{"name": "高数", "score": 90, "credit": 4}]}),
                   ensure_ascii=False),
    ], tmp_path)
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    assert [r.get("id") for r in rows] == [1, 3, 4]
    assert "高数" in json.dumps(rows[-1], ensure_ascii=False)
    assert rows[-1]["result"]["content"][0]["text"]


def test_stdin_close_is_a_clean_exit(tmp_path):
    out = io.StringIO()
    old_in, old_out = sys.stdin, sys.stdout
    sys.stdin, sys.stdout = io.StringIO(""), out
    try:
        assert mcp_server.serve(Config(), tmp_path) == 0
    finally:
        sys.stdin, sys.stdout = old_in, old_out
    assert out.getvalue() == ""


# ------------------------------------------------------------ 入口接线

def test_pop_mcp_serve_flag():
    assert mcp_server.pop_mcp_serve_flag(["--mcp-serve", "x"]) == (True, ["x"])
    assert mcp_server.pop_mcp_serve_flag(["x"]) == (False, ["x"])
    # 值型写法不认:一个手滑的 `--mcp-serve=1` 不该被当成"要起服务端"
    assert mcp_server.pop_mcp_serve_flag(["--mcp-serve=1"]) == (False, ["--mcp-serve=1"])


def test_main_routes_mcp_serve_before_any_ui(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(cli, "load_config", lambda: Config())
    monkeypatch.setattr(cli.mcp_server, "serve",
                        lambda config, workspace: seen.setdefault("ws", workspace) and 0)
    monkeypatch.setattr(sys, "argv",
                        ["main.py", "--mcp-serve", "--workspace", str(tmp_path)])
    with pytest.raises(SystemExit) as out:
        cli.main()
    assert out.value.code == 0
    assert seen["ws"] == tmp_path.resolve()


def test_main_refuses_mcp_serve_with_a_headless_format(monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_config", lambda: Config())
    monkeypatch.setattr(cli.mcp_server, "serve", lambda *a, **k: 0)
    monkeypatch.setattr(sys, "argv", ["main.py", "--mcp-serve", "--json", "x"])
    with pytest.raises(SystemExit) as out:
        cli.main()
    assert out.value.code == cli.headless.EXIT_ARGS
    assert "mcp-serve" in capsys.readouterr().err


# ------------------------------------------------- 与自家客户端的端到端自证

_SELF = (
    "import sys; from pathlib import Path;"
    "from minicode.config import Config;"
    "from minicode.mcp_server import serve;"
    "sys.exit(serve(Config(), Path(sys.argv[1])))"
)


def test_end_to_end_with_our_own_client(tmp_path):
    """两边形状不一致会当场暴露:用自家 `McpClient` 连本服务端走完三步。"""
    cfg = McpServerConfig(
        sid="self", command=sys.executable,
        args=["-c", _SELF, str(tmp_path)],
        env={"PYTHONPATH": str(ROOT)},
        start_timeout=30.0, call_timeout=30.0)
    client = McpClient(cfg)
    try:
        assert client.start(), client.error
        names = {t["name"] for t in client.tools}
        assert "gpa_calc" in names and "library_search" in names
        out = client.call_tool("gpa_calc", {
            "courses": [{"name": "高数", "score": 90, "credit": 4}]})
        assert "90" in out
    finally:
        client.close()
