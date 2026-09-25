"""CLI 斜杠命令的回归测试:每个命令都必须能跑通(不能有 NameError 之类)。"""
import pytest

from main import handle_slash, unmanagle_slash_command
from minicode import config as config_mod
from minicode.config import Config
from minicode.session import Session
from minicode.tools.base import ToolContext


def test_unmangles_msys_paths():
    assert unmanagle_slash_command(["D:/git/Git/history"]) == "/history"
    assert unmanagle_slash_command(["C:/Program Files/Git/undo"]) == "/undo"
    assert unmanagle_slash_command(["C:\\Program Files\\Git\\resume"]) == "/resume"


def test_leaves_normal_tasks_alone():
    assert unmanagle_slash_command(["/model"]) == "/model"
    assert unmanagle_slash_command(["修复", "pytest", "报错"]) == "修复 pytest 报错"
    assert unmanagle_slash_command(["运行", "model"]) == "运行 model"
    assert unmanagle_slash_command(["优化", "agent.py", "的性能"]) == "优化 agent.py 的性能"


class FakeUI:
    def __init__(self):
        self.messages: list[tuple[str, str]] = []

    def info(self, text): self.messages.append(("info", text))
    def error(self, text): self.messages.append(("error", text))
    def help_text(self): self.messages.append(("help", ""))


class FakeAgent:
    """只用到 ctx.workspace / undo / 上下文估算的最小替身。"""

    def __init__(self, workspace):
        self.ctx = ToolContext(workspace=workspace, config=Config())
        self.tools_tokens = 1234       # /history 要能把工具定义那份占用单独报出来

    def undo(self): return "已撤销"

    def compact_now(self): return None

    def context_tokens(self): return 5000 + self.tools_tokens


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "CREDENTIALS_PATH",
                        tmp_path / "home" / ".yinluxs" / "credentials.json")
    return tmp_path, FakeUI(), FakeAgent(tmp_path)


SILENT_COMMANDS = ("/clear", "/compact")   # 只改状态,不往 UI 打消息


@pytest.mark.parametrize("cmd", [
    "/help", "/clear", "/model", "/history", "/undo", "/compact",
    "/sandbox", "/sandbox off", "/memory", "/memory user",
    "/remember 喜欢先给结论", "/dream", "/dream all",
    "/skills", "/kb", "/plan", "/nothing-like-this",
    "/find", "/find 三体",
])
def test_slash_commands_do_not_crash(env, cmd):
    """重构 handle_slash 时漏传 workspace,曾让 /memory /plan /skills /kb 直接 NameError。"""
    _workspace, ui, agent = env
    session = Session(system_prompt="测试用系统提示")
    assert handle_slash(cmd, session, Config(), ui, agent) is False
    if cmd not in SILENT_COMMANDS:
        assert ui.messages, f"{cmd} 没有任何输出"


def test_slash_exit_returns_true(env):
    _workspace, ui, agent = env
    assert handle_slash("/exit", Session(system_prompt="x"), Config(), ui, agent) is True


def test_history_reports_the_same_ruler_the_compact_trigger_uses(env):
    """/history 说的"约多少 token"必须含工具定义 —— 否则阈值看着很远,却会突然压。"""
    _workspace, ui, agent = env
    session = Session(system_prompt="测试用系统提示")
    session.add_user("在吗")
    handle_slash("/history", session, Config(), ui, agent)
    text = "\n".join(t for _kind, t in ui.messages)
    assert f"{agent.context_tokens()} tokens" in text
    assert "工具定义 1234 tokens" in text


def test_find_prints_hits_without_leaking_the_sessions_path(env, monkeypatch):
    """/find 与模型侧 search_history 共用 history_hits:终端这条要真能搜到自己说过的话,
    但留档绝对路径不该打到屏幕上(那是家目录)。"""
    import json

    from minicode import session as session_mod
    _workspace, ui, agent = env
    logs = _workspace / "sessions"
    logs.mkdir()
    (logs / "session_20260917_2130.jsonl").write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in [
        {"type": "session_start", "time": "2026-09-17 21:30", "workspace": str(_workspace)},
        {"role": "user", "content": "三体这本书在图书馆哪里"},
        {"role": "assistant", "content": "三楼社科阅览室"},
    ]) + "\n", encoding="utf-8")
    monkeypatch.setattr(session_mod, "SESSIONS_DIR", logs)

    handle_slash("/find 三体", Session(system_prompt="x"), Config(), ui, agent)
    text = "\n".join(t for _kind, t in ui.messages)
    assert "2026-09-17 21:30" in text and "图书馆" in text
    assert str(logs) not in text, "留档绝对路径不该出现在终端"


def test_dream_command_reports_result(env):
    _workspace, ui, agent = env
    workspace = agent.ctx.workspace
    from minicode.memory import MemoryEntry, memory_path, save_to
    save_to(memory_path(workspace), [MemoryEntry("2026-01-01", "project", "同一条"),
                                     MemoryEntry("2026-01-02", "project", "同一条")])
    handle_slash("/dream", Session(system_prompt="x"), Config(), ui, agent)
    text = "\n".join(t for _kind, t in ui.messages)
    assert "整理完成" in text and "2 条 → 1 条" in text
