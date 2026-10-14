"""指令采纳与撤销闭环测试(v0.9.172,清偿 HANDOFF §6 51(g))。

涵盖:
1. permspec: revoke_agents 根层与目录层独立撤销,不伤 permissions.json 白名单与 granted。
2. app_setup: adopt_root_agents / revoke_root_agents / revoke_layer。
3. main CLI: pop_adopt_agents_flag 旗标提取与 /instructions adopt/revoke 子命令。
4. GUI bridge: get_instruction_layers / adopt_instruction_layer / revoke_instruction_layer 与命令面板注册。
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from minicode import app_setup, permspec
from minicode.gui_bridge import Bridge, PublicApi
from main import (
    _instructions_command,
    pop_adopt_agents_flag,
)


@pytest.fixture(autouse=True)
def isolated_trust_file(tmp_path, monkeypatch):
    """把信任库指到临时目录 —— 绝不污染用户真实的 perms-trust.json。"""
    path = tmp_path / "perms-trust.json"
    monkeypatch.setattr(permspec, "trust_path", lambda: path)
    return path


def test_permspec_revoke_agents_root(tmp_path: Path):
    """根层指令采纳与独立撤销:不伤项目白名单与 granted 规则。"""
    ws = tmp_path
    agents_sha = "sha-agents-root-12345"
    perms_sha = "sha-perms-whitelist-67890"

    # 1. 预设白名单信任与 granted 记录
    permspec.trust(ws, perms_sha, rules_n=2)
    row = permspec._row(ws)
    row["granted"] = [{"tool": "run_command", "command_prefix": "pytest"}]
    permspec._put_row(ws, row)

    # 2. 采纳根层指令
    assert permspec.agents_adopted_sha(ws, rel="") == ""
    permspec.adopt_agents(ws, agents_sha, lines=10, rel="")
    assert permspec.agents_adopted_sha(ws, rel="") == agents_sha

    # 3. 撤销根指令
    revoked = permspec.revoke_agents(ws, rel="")
    assert revoked
    assert permspec.agents_adopted_sha(ws, rel="") == ""

    # 4. 验证白名单/granted 规则完好无损
    after_row = permspec._row(ws)
    assert after_row.get("sha256") == perms_sha
    assert len(after_row.get("granted", [])) == 1
    assert after_row["granted"][0]["command_prefix"] == "pytest"

    # 5. 再次撤销返回 False (幂等拒绝)
    assert not permspec.revoke_agents(ws, rel="")


def test_permspec_revoke_agents_subdir(tmp_path: Path):
    """子目录指令采纳与撤销:根层与子目录层互不干扰。"""
    ws = tmp_path
    root_sha = "sha-root-agents-111"
    sub_sha = "sha-sub-agents-222"

    permspec.adopt_agents(ws, root_sha, lines=5, rel="")
    permspec.adopt_agents(ws, sub_sha, lines=8, rel="backend")

    assert permspec.agents_adopted_sha(ws, rel="") == root_sha
    assert permspec.agents_adopted_sha(ws, rel="backend") == sub_sha

    # 仅撤销子目录层
    assert permspec.revoke_agents(ws, rel="backend")
    assert permspec.agents_adopted_sha(ws, rel="backend") == ""
    # 根层仍然保持采纳
    assert permspec.agents_adopted_sha(ws, rel="") == root_sha

    # 撤销根层
    assert permspec.revoke_agents(ws, rel="")
    assert permspec.agents_adopted_sha(ws, rel="") == ""


def test_app_setup_adopt_and_revoke_root(tmp_path: Path):
    """app_setup 层的 adopt_root_agents 与 revoke_root_agents 封装。"""
    ws = tmp_path
    (ws / "AGENTS.md").write_text("# Project Guidelines\nRun pytest before commit.", encoding="utf-8")

    ok, sha = app_setup.adopt_root_agents(ws)
    assert ok
    assert len(sha) == 64

    # 重复采纳是幂等的
    ok2, sha2 = app_setup.adopt_root_agents(ws)
    assert ok2
    assert sha2 == sha

    # 撤销根指令
    assert app_setup.revoke_root_agents(ws)
    # 再次撤销返回 False
    assert not app_setup.revoke_root_agents(ws)

    # 没有 AGENTS.md 时采纳返回 False
    empty_ws = tmp_path / "empty"
    empty_ws.mkdir()
    ok_empty, err = app_setup.adopt_root_agents(empty_ws)
    assert not ok_empty
    assert "未找到" in err


def test_app_setup_revoke_layer_subdir(tmp_path: Path):
    """app_setup 层的 revoke_layer 针对子目录指令层。"""
    ws = tmp_path
    mod = ws / "services" / "auth"
    mod.mkdir(parents=True)
    (mod / "AGENTS.md").write_text("Auth module guidelines", encoding="utf-8")

    layer = app_setup.layer_at(ws, "services/auth")
    assert layer is not None

    sha = app_setup.adopt_layer(ws, layer)
    assert len(sha) == 64

    # 撤销该层
    assert app_setup.revoke_layer(ws, layer)
    assert not app_setup.revoke_layer(ws, layer)


def test_main_pop_adopt_agents_flag():
    """CLI 参数解析提取 --adopt-project-agents / --adopt-agents。"""
    argv = ["--adopt-project-agents", "跑测试", "-w", "/some/path"]
    found, rest = pop_adopt_agents_flag(argv)
    assert found
    assert rest == ["跑测试", "-w", "/some/path"]

    argv2 = ["--adopt-agents", "整理文档"]
    found2, rest2 = pop_adopt_agents_flag(argv2)
    assert found2
    assert rest2 == ["整理文档"]

    argv3 = ["日常对话", "--json"]
    found3, rest3 = pop_adopt_agents_flag(argv3)
    assert not found3
    assert rest3 == ["日常对话", "--json"]


def test_main_instructions_command(tmp_path: Path):
    """CLI /instructions adopt/revoke 子命令及 agent._refresh_clock() 调用。"""
    ws = tmp_path
    (ws / "AGENTS.md").write_text("CLI test agents", encoding="utf-8")
    sub = ws / "client"
    sub.mkdir()
    (sub / "AGENTS.md").write_text("Client agents", encoding="utf-8")

    agent = MagicMock()
    ui = MagicMock()

    # 1. 采纳根层
    _instructions_command(agent, ui, ["adopt"], workspace=ws)
    agent._refresh_clock.assert_called_once()
    assert any("已采纳工作区根项目指令" in str(c) for c in ui.info.call_args_list)

    # 2. 采纳子目录层
    agent._refresh_clock.reset_mock()
    _instructions_command(agent, ui, ["adopt", "client"], workspace=ws)
    agent._refresh_clock.assert_called_once()
    assert any("已采纳目录 client" in str(c) for c in ui.info.call_args_list)

    # 3. 撤销子目录层
    agent._refresh_clock.reset_mock()
    _instructions_command(agent, ui, ["revoke", "client"], workspace=ws)
    agent._refresh_clock.assert_called_once()
    assert any("已撤销目录 client" in str(c) for c in ui.info.call_args_list)

    # 4. 撤销根层
    agent._refresh_clock.reset_mock()
    _instructions_command(agent, ui, ["revoke"], workspace=ws)
    agent._refresh_clock.assert_called_once()
    assert any("已撤销工作区根项目指令" in str(c) for c in ui.info.call_args_list)

    # 5. 不带参数展示状态
    ui.reset_mock()
    _instructions_command(agent, ui, [], workspace=ws)
    assert ui.info.called


def test_gui_bridge_instruction_layer_methods(tmp_path: Path):
    """GUI 桥接层与 PublicApi: 获取指令层、采纳与撤销。"""
    ws = tmp_path
    (ws / "AGENTS.md").write_text("Bridge agents test", encoding="utf-8")

    bridge = MagicMock()
    bridge.workspace = ws
    bridge.agent = MagicMock()
    bridge.agent._agents_layers = set()

    # 使用真实 Bridge 方法绑定
    bridge.get_instruction_layers = Bridge.get_instruction_layers.__get__(bridge, Bridge)
    bridge.adopt_instruction_layer = Bridge.adopt_instruction_layer.__get__(bridge, Bridge)
    bridge.revoke_instruction_layer = Bridge.revoke_instruction_layer.__get__(bridge, Bridge)

    # 1. 查询
    info = bridge.get_instruction_layers()
    assert info["ok"]
    assert any(l["name"] == "AGENTS.md" for l in info["layers"])

    # 2. 采纳
    res_adopt = bridge.adopt_instruction_layer("")
    assert res_adopt["ok"]
    assert "sha" in res_adopt
    bridge.agent._refresh_clock.assert_called_once()

    # 3. 撤销
    bridge.agent._refresh_clock.reset_mock()
    res_revoke = bridge.revoke_instruction_layer("")
    assert res_revoke["ok"]
    bridge.agent._refresh_clock.assert_called_once()

    # 4. 再次撤销
    res_revoke2 = bridge.revoke_instruction_layer("")
    assert not res_revoke2["ok"]

    # 5. PublicApi 代理转发
    api = PublicApi(bridge)
    assert api.get_instruction_layers()["ok"]
    assert api.adopt_instruction_layer("")["ok"]
    assert api.revoke_instruction_layer("")["ok"]


def test_gui_bridge_commands_catalog_has_instructions():
    """命令面板 (Ctrl+Shift+P) 目录包含 instructions 采纳与撤销快捷指令。"""
    bridge = MagicMock()
    bridge.get_commands_catalog = Bridge.get_commands_catalog.__get__(bridge, Bridge)
    catalog = bridge.get_commands_catalog()
    assert catalog["ok"]
    ids = {cmd["id"] for cmd in catalog["commands"]}
    assert "slash_instructions" in ids
    assert "slash_adopt_instructions" in ids
    assert "slash_revoke_instructions" in ids
