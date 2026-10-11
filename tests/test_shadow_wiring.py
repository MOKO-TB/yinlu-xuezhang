"""H 的接线(v0.9.157):一轮里**第一次要动东西之前**,整个工作区被快照一次。

库本体那一族(`test_shadow_git.py`)验的是"快照与还原对不对";这一族只验**什么时候
触发** —— 而它有三个"多一次少一次都不对"的地方:

- **只读的回合不该付钱**(`read_only` 的工具直接跳过);
- **一轮只做一次**(第二次写工具进来不该再快照);
- **子代理不进去**(它们跑在另一个线程上,两路 `git add -A` 打同一本库就是数据竞争)。

第一版把触发点想放在 `agent.py` 里 `tool.handler(...)` 前面 —— 而那段 docstring 明写
"**唯一可以放进线程池的一段**",所以那是错的;正确落点是 `_prepare`(留在主线程按序跑)。
"""
from __future__ import annotations

import json

import pytest

from minicode import paths, shadow_git
from minicode.llm import AssistantTurn, ToolCall

from test_agent import FakeLLM, FakeUI, make_agent

pytestmark = pytest.mark.skipif(not shadow_git.available(),
                                reason="这台机器上没有 git")


@pytest.fixture
def store(tmp_path, monkeypatch):
    """影子库钉到 tmp 里 —— 源码态 APP_DIR 就是仓库,不钉就会写进仓库。"""
    root = tmp_path / "appdata"
    monkeypatch.setattr(paths, "app_path", lambda *p: root.joinpath(*p))
    return root


def _write(path="a.txt", content="x"):
    return AssistantTurn(tool_calls=[ToolCall(
        id="c1", name="write_file",
        arguments=json.dumps({"path": path, "content": content}))])


def test_a_writing_turn_takes_exactly_one_snapshot(config, ctx, tmp_path, store):
    """一轮里两次写文件,只该留**一次**快照 —— 先置位再动手,中途进来的工具不再触发。"""
    agent = make_agent(config, ctx, FakeLLM([
        _write("a.txt"), AssistantTurn(text="一"),
        _write("b.txt"), AssistantTurn(text="二")]), FakeUI())
    agent.run_turn("写两份")
    assert len(shadow_git.snapshots(ctx.workspace)) == 1, "一轮只该快照一次"


def test_a_read_only_turn_takes_no_snapshot(config, ctx, tmp_path, store):
    """只读的回合**不为它付钱** —— 读一个文件不该触发整个工作区的 git add -A。"""
    (ctx.workspace / "a.txt").write_text("内容\n", encoding="utf-8")
    agent = make_agent(config, ctx, FakeLLM([
        AssistantTurn(tool_calls=[ToolCall(
            id="c1", name="read_file", arguments=json.dumps({"path": "a.txt"}))]),
        AssistantTurn(text="看完了")]), FakeUI())
    agent.run_turn("看一眼")
    assert shadow_git.snapshots(ctx.workspace) == []


def test_the_switch_off_means_no_snapshot(config, ctx, tmp_path, store, monkeypatch):
    monkeypatch.setattr(config, "shadow_git", False, raising=False)
    agent = make_agent(config, ctx, FakeLLM([_write(), AssistantTurn(text="好")]), FakeUI())
    agent.run_turn("写一份")
    assert shadow_git.snapshots(ctx.workspace) == []


def test_the_flag_is_reset_every_turn(config, ctx, tmp_path, store):
    """上一轮快照过,不该让这一轮的第一次写**跳过**快照。"""
    agent = make_agent(config, ctx, FakeLLM([
        _write("a.txt"), AssistantTurn(text="一"),
        _write("b.txt"), AssistantTurn(text="二")]), FakeUI())
    agent.run_turn("第一轮")
    agent.run_turn("第二轮")
    assert len(shadow_git.snapshots(ctx.workspace)) == 2, "每一轮各该有自己的改动前像"


def test_a_subagent_never_snapshots(config, ctx, tmp_path, store):
    """**子代理跳过** —— 它们跑在另一个线程上、而且几路并行,让它们也快照就是那个
    竞争面本身。代价(这一条如实记着):子代理用 bash 间接改的文件不在这张网里。"""
    agent = make_agent(config, ctx, FakeLLM([]), FakeUI())
    agent._is_sub = True
    from minicode.tools.registry import build_default_registry
    tool = next(t for t in build_default_registry() if t.name == "write_file")
    agent._shadow_snapshot_once(tool)
    assert shadow_git.snapshots(ctx.workspace) == []


def test_shadow_line_says_which_state_it_is_in(config, ctx, tmp_path, store):
    """那一行必须**说得出口** —— 关着、没 git、开着各是一句,不能都是空。"""
    agent = make_agent(config, ctx, FakeLLM([]), FakeUI())
    assert "开" in agent.shadow_line()
    config.shadow_git = False
    assert "关" in agent.shadow_line()
