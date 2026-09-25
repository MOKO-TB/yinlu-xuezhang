"""v0.9.40 D0 时钟收口:托盘连着开三天,「今天有什么课」不能还答前天的。

方案在主线那份 `D:/game/mini-code/docs/roadmap-v0.9.40-模型层收口.md`(本树没带副本)。
这份测试钉的不是"重建能跑通",而是**什么时候重建、重建改了谁、没跨天时一次都不重建**:
按日重建的代价是破一次 KV Cache,所以"同一天零重建"和"跨天必重建"同样重要。
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from minicode.agent import Agent
from minicode.llm import AssistantTurn
from minicode.permission import PermissionGate
from minicode.session import Session
from tests.test_agent import FakeLLM


class UI:
    """够用的假界面:只关心"有没有跨天提示"和"回合没炸"。"""

    def __init__(self):
        self.infos: list[str] = []
        self.interrupted = False

    def on_text_delta(self, t): pass
    def on_reasoning_delta(self, t): pass
    def on_retry(self, e, n): pass
    def finish_stream(self): pass
    def tool_start(self, tool, args): pass
    def tool_done(self, tool, result): pass
    def compacted(self, before, after): pass
    def turn_interrupted(self): self.interrupted = True
    def llm_error(self, e): pass
    def max_steps_reached(self, n): pass
    def turn_time_limited(self, seconds, steps): pass

    def info(self, text):
        self.infos.append(text)


@pytest.fixture
def rebuilds(monkeypatch):
    """把重建入口换成一个会记账的假货。

    `_refresh_clock` 是**调用时**才 `from minicode.app_setup import build_system_prompt`,
    所以换模块属性就换得掉 —— 钉住"重建走的是这同一个函数",不出现第二份日期来源。
    """
    calls: list[str] = []

    def fake(workspace=None):
        calls.append(date.today().isoformat())
        return f"新提示词 {date.today().isoformat()}"

    monkeypatch.setattr("minicode.app_setup.build_system_prompt", fake)
    return calls


def _agent(config, ctx, llm=None, session=None, ui=None):
    session = session or Session(system_prompt="旧提示词", workspace=ctx.workspace)
    ui = ui or UI()
    agent = Agent(llm=llm or FakeLLM([AssistantTurn(text="好")]),
                  registry=[], session=session, gate=PermissionGate(config),
                  ui=ui, ctx=ctx)
    return agent, session, ui


def test_construction_remembers_todays_date(config, ctx):
    agent, _, _ = _agent(config, ctx)
    assert agent._prompt_date == date.today()


# ---------------------------------------------------------------- Session 那一半

def test_refresh_replaces_only_the_system_message(config, ctx):
    session = Session(system_prompt="旧提示词")
    session.add_user("问")
    assert session.refresh_system_prompt("新提示词") is True
    assert [m["role"] for m in session.messages] == ["system", "user"]
    assert session.messages[0]["content"] == "新提示词"
    assert session.messages[1]["content"] == "问"


def test_refresh_refuses_when_head_is_not_system():
    session = Session(system_prompt="sys")
    session.messages = [{"role": "user", "content": "没有系统提示的怪形状"}]
    assert session.refresh_system_prompt("新") is False
    assert session.messages[0]["content"] == "没有系统提示的怪形状"


def test_refresh_writes_nothing_to_the_log(tmp_path):
    """留档不记 system 行:重建必须**不**往 JSONL 里添东西,否则 resume 会多出半截对话。"""
    log_dir = tmp_path / "sessions"
    session = Session(system_prompt="旧提示词", log_dir=log_dir)
    session.add_user("问")
    path = session.log_path
    before = path.read_text(encoding="utf-8").splitlines()
    session.refresh_system_prompt("新提示词")
    after = path.read_text(encoding="utf-8").splitlines()
    assert after == before
    assert not any('"role": "system"' in line for line in after)


def test_resume_then_rebuild_keeps_the_body():
    """恢复旧会话后跨天重建:正文一条不少,只有开头那条提示词是新的。"""
    session = Session(system_prompt="旧提示词")
    session.refresh_system_prompt("新提示词")
    session.add_user("昨天的问题")
    roles = [m["role"] for m in session.messages]
    assert roles == ["system", "user"]
    assert session.messages[0]["content"] == "新提示词"


# ------------------------------------------------------------------ Agent 那一半

def test_same_day_turns_never_rebuild(config, ctx, rebuilds):
    agent, session, ui = _agent(config, ctx)
    agent.run_turn("第一问")
    agent.run_turn("第二问")
    assert rebuilds == []
    assert session.messages[0]["content"] == "旧提示词"
    assert ui.infos == []


def test_cross_day_rebuilds_once_and_says_so(config, ctx, rebuilds):
    agent, session, ui = _agent(config, ctx)
    agent._prompt_date = date.today() - timedelta(days=1)
    agent.run_turn("今天有什么课")
    assert len(rebuilds) == 1
    assert session.messages[0]["content"] == f"新提示词 {date.today().isoformat()}"
    assert ui.infos == ["已跨天,日期与课表已刷新"]


def test_second_turn_after_the_cross_does_not_rebuild_again(config, ctx, rebuilds):
    """一天只破一次 KV Cache:跨天后的第二、第三个回合不再重建。"""
    agent, _, ui = _agent(config, ctx)
    agent._prompt_date = date.today() - timedelta(days=1)
    agent.run_turn("第一问")
    agent.run_turn("第二问")
    agent.run_turn("第三问")
    assert len(rebuilds) == 1
    assert len(ui.infos) == 1


def test_rebuild_lands_before_the_first_request(config, ctx, rebuilds):
    """重建必须发生在**这条请求之前** —— 否则模型这一轮拿到的还是过期日子。"""
    llm = FakeLLM([AssistantTurn(text="好"), AssistantTurn(text="好")])
    agent, _, _ = _agent(config, ctx, llm=llm)
    agent._prompt_date = date.today() - timedelta(days=1)
    agent.run_turn("今天有什么课")
    assert llm.calls[0][0]["role"] == "system"
    assert llm.calls[0][0]["content"].startswith("新提示词")


def test_rebuild_survives_a_session_without_system_prompt(config, ctx, rebuilds):
    """头部不是 system 的会话:重建不能假设它一定在,不炸也不谎报。"""
    session = Session(system_prompt="sys")
    session.messages = [{"role": "user", "content": "只有一条用户消息"}]
    agent, _, ui = _agent(config, ctx, session=session)
    agent._prompt_date = date.today() - timedelta(days=1)
    agent.run_turn("问")
    assert rebuilds == [date.today().isoformat()]   # 试过了:重建函数确实被叫起来
    assert session.messages[0]["content"] == "只有一条用户消息"
    assert ui.infos == []          # 但没换成,就不说"已刷新"
