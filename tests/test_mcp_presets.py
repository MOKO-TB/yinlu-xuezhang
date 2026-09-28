"""MCP 预设模板库(v0.9.70,路线图 #3 最后半条)+ 服务器健康汇总。

钉四件事:①模板表自身形状 —— id 唯一、每条写清 needs、**绝不内置密钥类
字段**;②preset → 配置的合并边界 —— raw 只许给占位符值与可选 name,
command/args/env/headers/trusted 一律以模板为准(否则预设就是任意命令执行
口子);③占位符诚实报错 —— 缺参数、路径不存在都直说,不猜不代建;
④健康汇总 —— 配置是唯一事实,连上的补活状态,没连上的如实标「未连上」。
"""

import json
import sys

from minicode import mcp as mcp_mod
from minicode.config import Config
from minicode.gui_bridge import create_app
from minicode.llm import AssistantTurn

FAKE_SERVER = r"tests\fake_mcp_server.py"


class _Ctx:
    class _C:
        mcp_servers = []
    config = _C()


# ------------------------------------------------------------ 模板表形状

def test_preset_table_shape():
    ids = [p["id"] for p in mcp_mod.MCP_PRESETS]
    assert ids and len(ids) == len(set(ids)), "预设 id 必须唯一"
    for p in mcp_mod.MCP_PRESETS:
        assert p["command"], p["id"]                  # 预设全是 stdio 本地进程
        has_token = any("__" in a for a in p["args"])
        assert has_token == bool(p.get("placeholder")), \
            f"{p['id']}:args 里有占位符就必须登记 placeholder,反之亦然"
        assert p["needs"].strip() and p["description"].strip(), \
            f"{p['id']}:needs 与 description 是给用户看的,不许空着"
        assert isinstance(p["read_only"], bool) and isinstance(p["trusted"], bool)
        blob = json.dumps(p, ensure_ascii=False)
        for secret in ("api_key", "sk-", "Authorization", "token"):
            assert secret not in blob.lower(), f"{p['id']} 不许内置密钥类字段"


def test_find_preset_case_insensitive_and_unknown():
    assert mcp_mod.find_preset("FILESYSTEM")["id"] == "filesystem"
    assert mcp_mod.find_preset("nope") is None
    assert mcp_mod.find_preset("") is None


# ------------------------------------------------------------ preset → 配置

def test_build_preset_zero_arg():
    cfg, err = mcp_mod.build_preset_config("everything", None)
    assert err == "" and cfg["command"] == "npx"
    assert cfg["args"] == ["-y", "@modelcontextprotocol/server-everything"]
    assert cfg["transport"] == "stdio" and cfg["enabled"] is True


def test_build_preset_filesystem_needs_existing_dir(tmp_path):
    cfg, err = mcp_mod.build_preset_config("filesystem", {})
    assert cfg is None and "path" in err and "先问用户" in err
    cfg, err = mcp_mod.build_preset_config("filesystem",
                                           {"path": str(tmp_path / "没有")})
    assert cfg is None and "已存在的文件夹" in err      # 不建、不猜
    cfg, err = mcp_mod.build_preset_config("filesystem", {"path": str(tmp_path)})
    assert err == "" and str(tmp_path) in cfg["args"]
    assert all("__" not in a for a in cfg["args"]), "占位符必须被替换干净"


def test_build_preset_raw_cannot_override_command():
    """raw 能改 command,预设就成了任意命令执行口子 —— 其余字段一律以模板为准。"""
    cfg, err = mcp_mod.build_preset_config(
        "everything", {"command": "evil.exe", "args": ["/c", "boom"],
                       "env": {"X": "1"}, "headers": {"Authorization": "x"},
                       "trusted": True, "read_only": False})
    assert err == ""
    assert cfg["command"] == "npx"
    assert cfg["args"] == ["-y", "@modelcontextprotocol/server-everything"]
    assert cfg["trusted"] is False and "env" not in cfg and "headers" not in cfg


def test_build_preset_name_override_and_unknown():
    cfg, _ = mcp_mod.build_preset_config("time", {"name": "我的时钟"})
    assert cfg["id"] == "我的时钟"
    cfg, err = mcp_mod.build_preset_config("nope", None)
    assert cfg is None and "list_presets" in err


# ------------------------------------------------------------ 工具处理器

def test_admin_tool_list_presets_mentions_needs():
    out = mcp_mod.admin_tool(_Ctx(), action="list_presets")
    for p in mcp_mod.MCP_PRESETS:
        assert p["id"] in out
    assert "Node.js" in out and "真连一次探测" in out


def test_admin_tool_add_with_preset_delegates_built_config(monkeypatch):
    seen = {}

    def hook(action, name="", raw=None):
        seen.update(action=action, name=name, raw=raw)
        return "ok"

    monkeypatch.setattr(mcp_mod, "_ADMIN_HOOK", hook)
    out = mcp_mod.admin_tool(_Ctx(), action="add", raw={"preset": "everything"})
    assert out == "ok"
    assert seen["action"] == "add" and seen["name"] == "everything"
    assert seen["raw"]["command"] == "npx"


def test_admin_tool_add_preset_missing_param_reports_honestly():
    out = mcp_mod.admin_tool(_Ctx(), action="add", raw={"preset": "filesystem"})
    assert out.startswith("错误") and "path" in out


def test_admin_tool_list_empty_points_to_presets():
    out = mcp_mod.admin_tool(_Ctx(), action="list")
    assert "list_presets" in out


# ------------------------------------------------------------ 健康汇总(桥层)

def _bridge(config, tmp_path, mcp_servers):
    cfg = Config(
        providers=config.providers, active_provider_id="test",
        mcp_servers=mcp_servers)
    llm = type("L", (), {"stream_chat": lambda self, *a, **k: AssistantTurn(text="")})()
    bridge, ui, _agent, _session = create_app(
        cfg, tmp_path, llm=llm, sessions_dir=tmp_path / "sessions",
        config_path=tmp_path / "config.json")
    return bridge


class _FakeClient:
    def __init__(self, sid, label, n_tools):
        self.cfg = type("C", (), {"sid": sid, "label": label})()
        self.server_info = {"name": f"{sid}-server"}
        self._tools = list(range(n_tools))

    def list_tools(self):
        return self._tools


class _FakeManager:
    def __init__(self, clients):
        self.clients = clients


def test_bridge_mcp_summary_merges_config_and_live(config, tmp_path):
    bridge = _bridge(config, tmp_path, mcp_servers=[
        {"id": "files", "command": sys.executable, "args": [FAKE_SERVER]},
        {"id": "dead", "command": "no_such_cmd_for_test"},
        {"id": "off", "command": "npx", "enabled": False},
    ])
    bridge._mcp = _FakeManager([_FakeClient("files", "本地文件夹", 3)])
    items = {x["id"]: x for x in bridge._mcp_summary()}
    assert items["files"]["connected"] is True and items["files"]["n_tools"] == 3
    assert items["files"]["label"] == "本地文件夹"
    assert items["files"]["server"] == "files-server"
    assert items["dead"]["connected"] is False and items["dead"]["n_tools"] == 0
    assert items["off"]["enabled"] is False and items["off"]["connected"] is False


def test_bridge_mcp_summary_empty_when_nothing_configured(config, tmp_path):
    bridge = _bridge(config, tmp_path, mcp_servers=[])
    assert bridge._mcp_summary() == []
