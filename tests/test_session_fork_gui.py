"""v0.9.171 会话分叉 GUI/命令全链路 + 影子快照管理单元测试。"""
from __future__ import annotations

import json

from minicode import slash_commands, shadow_git
from minicode.gui_bridge import PublicApi, create_app
from minicode.llm import AssistantTurn
from minicode.session import Session


class DummyLLM:
    def stream_chat(self, *args, **kwargs):
        return AssistantTurn(text="ok")


def make_test_bridge(config, tmp_path):
    bridge, ui, agent, session = create_app(
        config, tmp_path, llm=DummyLLM(), sessions_dir=tmp_path / "sessions",
        config_path=tmp_path / "config.json"
    )
    return bridge, session


def test_slash_commands_expansion():
    """测试 /snapshots, /restore, /fork 命令展开与参数判定。"""
    snap_prompt = slash_commands.expand("snapshots")
    assert snap_prompt is not None
    assert "影子 Git" in snap_prompt or "快照" in snap_prompt
    assert not slash_commands.has_arg("snapshots")

    restore_prompt = slash_commands.expand("restore")
    assert restore_prompt is not None
    assert "$text" in restore_prompt
    assert slash_commands.has_arg("restore")

    fork_prompt = slash_commands.expand("fork")
    assert fork_prompt is not None
    assert "$text" in fork_prompt
    assert slash_commands.has_arg("fork")


def test_commands_catalog_has_new_slash_commands(config, tmp_path):
    """测试命令面板 Catalog 包含新命令。"""
    bridge, _ = make_test_bridge(config, tmp_path)
    catalog = bridge.get_commands_catalog()
    cmd_ids = {c["id"] for c in catalog["commands"]}
    assert "slash_snapshots" in cmd_ids
    assert "slash_restore" in cmd_ids
    assert "slash_fork" in cmd_ids


def test_bridge_git_drop_snapshots(config, tmp_path, monkeypatch):
    """测试 bridge.git_drop_snapshots 门面与实现。"""
    bridge, _ = make_test_bridge(config, tmp_path)
    assert callable(getattr(PublicApi, "git_drop_snapshots", None))

    # 模拟 shadow_git.drop 返回 True
    monkeypatch.setattr(shadow_git, "drop", lambda ws: True)
    res = bridge.git_drop_snapshots()
    assert res["ok"] is True
    assert "已清空" in res["msg"]

    # 模拟 shadow_git.drop 返回 False
    monkeypatch.setattr(shadow_git, "drop", lambda ws: False)
    res = bridge.git_drop_snapshots()
    assert res["ok"] is False


def test_bridge_fork_session_by_filename(config, tmp_path):
    """测试 Bridge 按留档文件名分叉会话。"""
    bridge, _ = make_test_bridge(config, tmp_path)

    # 制造一个初始会话留档
    src_session = Session("系统提示", log_dir=bridge.sessions_dir, workspace=bridge.workspace)
    src_session.add_user("第一问")
    src_session.add_assistant(AssistantTurn(text="第一答"))
    src_path = src_session.log_path
    assert src_path is not None and src_path.exists()

    # 通过文件名分叉
    res = bridge.fork_session(src_path.name)
    assert res["ok"] is True
    new_file = res["file"]
    assert new_file != src_path.name
    assert (bridge.sessions_dir / new_file).exists()


def test_bridge_fork_session_with_upto(config, tmp_path):
    """测试 Bridge 带 upto 限制分叉会话。"""
    bridge, _ = make_test_bridge(config, tmp_path)

    src_session = Session("系统提示", log_dir=bridge.sessions_dir, workspace=bridge.workspace)
    src_session.add_user("问题一")
    src_session.add_assistant(AssistantTurn(text="回答一"))
    src_session.add_user("问题二")
    src_session.add_assistant(AssistantTurn(text="回答二"))
    src_path = src_session.log_path

    # upto=2 (包含 session_start 与第一轮问答)
    res = bridge.fork_session(src_path.name, upto=2)
    assert res["ok"] is True
    new_path = bridge.sessions_dir / res["file"]
    lines = [json.loads(line) for line in new_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    user_msgs = [m["content"] for m in lines if m.get("role") == "user"]
    assert user_msgs == ["问题一"]
    assert "问题二" not in str(lines)


def test_bridge_fork_session_by_index(config, tmp_path):
    """测试 Bridge 按侧栏会话序号分叉。"""
    bridge, _ = make_test_bridge(config, tmp_path)

    src_session = Session("系统提示", log_dir=bridge.sessions_dir, workspace=bridge.workspace)
    src_session.add_user("你好")
    src_session.add_assistant(AssistantTurn(text="学长在"))

    # 序号 1
    res = bridge.fork_session(1)
    assert res["ok"] is True
    assert res["file"].endswith(".jsonl")


def test_bridge_fork_session_not_found(config, tmp_path):
    """测试分叉不存在的会话优雅报错。"""
    bridge, _ = make_test_bridge(config, tmp_path)
    res = bridge.fork_session("not_exist_session.jsonl")
    assert res["ok"] is False
    assert "不存在" in res["msg"] or "未找到" in res["msg"]


def test_cli_snapshots_drop(tmp_path, monkeypatch):
    """测试 CLI _snapshots_command 支持 drop/clean。"""
    from main import _snapshots_command

    class FakeAgent:
        def __init__(self, ws):
            self.ctx = type("Ctx", (), {"workspace": ws})()
        def shadow_line(self):
            return "影子检查点: 开"

    class SimpleUI:
        def __init__(self):
            self.infos = []
        def info(self, text: str):
            self.infos.append(text)
        def error(self, text: str):
            self.infos.append(text)

    ui = SimpleUI()
    agent = FakeAgent(tmp_path)

    monkeypatch.setattr(shadow_git, "drop", lambda ws: True)
    _snapshots_command(agent, ui, ["drop"])
    assert any("已清空" in s for s in ui.infos)
