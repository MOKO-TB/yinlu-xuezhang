# -*- coding: utf-8 -*-
"""撞上下文超限时就地压一次再试(v0.9.143)。

对标量出来的一个真缺口:ZCode 的压缩有五种触发(含 `provider_overflow`
"端点说太长了"与 `model_downshift` "换模型了"),本项目只有"到阈值"与"人手压"两种。
今天撞到超限的结局是一句模型报错(无头那边还是退出码 1),用户以为程序坏了 ——
而窗口是量出来的,超了就是超了,**压一压真能过去**。

三条口径各有针:① 只试一次(压不动还接着撞就是拿有损压缩换一次次白等);
② 认不出就不压(认错一次的代价是把不该压的对话压掉了,压缩是有损的);
③ 压了要说出口(静默压完再试,用户看到的是"这句答得比平时空")。
"""
import pytest

from minicode.agent import Agent
from minicode.config import Config, Provider
from minicode.llm import AssistantTurn, is_context_overflow
from minicode.permission import PermissionGate
from minicode.session import Session
from minicode.tools.base import ToolContext
from minicode.tools.registry import build_default_registry

from test_agent import FakeLLM, FakeUI

OVERFLOW = RuntimeError("Error code: 400 - maximum context length is 32768 tokens, "
                        "however you requested 40000 tokens")


class RecUI(FakeUI):
    """带 `push` 的那一种界面(GUI 与 CLI 都有;FakeUI 没有,那正是要分开测的)。"""

    def __init__(self):
        super().__init__()
        self.pushed = []

    def push(self, type_, **payload):
        self.pushed.append((type_, payload.get("text", "")))


def _agent(monkeypatch, tmp_path, turns, compact_result=True, ui=None):
    cfg = Config(providers=[Provider(id="t", name="T", base_url="https://x.invalid/v1",
                                      api_key="k", model="m")], active_provider_id="t")
    llm = FakeLLM(turns)
    agent = Agent(llm=llm, registry=build_default_registry(),
                  session=Session(system_prompt="sys"), gate=PermissionGate(cfg),
                  ui=ui or RecUI(),
                  ctx=ToolContext(workspace=tmp_path, config=cfg))
    calls = {"force": 0}

    def fake_compact(force=False):
        if force:
            calls["force"] += 1
            return compact_result
        return False                      # 阈值那一档:这一族测试不关心

    monkeypatch.setattr(agent, "_maybe_compact", fake_compact)
    return agent, llm, calls


# ---------------------------------------------------------------- 认不认得出

@pytest.mark.parametrize("text", [
    "Error code: 400 - This model's maximum context length is 32768 tokens",
    "BadRequestError: 400 context_length_exceeded",
    "Error code: 400 - 请输入更短的内容:超出上下文长度",
    "BadRequestError: prompt is too long",
])
def test_the_overflow_shapes_we_actually_meet(text):
    assert is_context_overflow(RuntimeError(text)) is True


@pytest.mark.parametrize("exc", [
    RuntimeError("Error code: 400 - Invalid model name 'deepseekv4'"),
    RuntimeError("Error code: 401 - Invalid token"),
    ValueError("failed to parse request body"),
    RuntimeError(""),
    None,
])
def test_anything_else_is_not_an_overflow(exc):
    """**认错一次的代价是把不该压的对话压掉了。**

    参数写错的 400 与超长的 400 是同一个状态码 —— 所以判的是端点原话里的标记串,
    认不准一律"不是":宁可让这次失败照原样交给用户。
    """
    assert is_context_overflow(exc) is False


# ---------------------------------------------------------------- 只试一次

def test_it_compacts_once_and_retries_the_same_step(monkeypatch, tmp_path):
    agent, llm, calls = _agent(monkeypatch, tmp_path,
                               [OVERFLOW, AssistantTurn(text="补上的答案")])
    agent.run_turn("接着说")
    assert len(llm.calls) == 2, "该再试一次:今天就卡在第一发报错上"
    assert calls["force"] == 1
    assert agent.session.messages[-1]["content"] == "补上的答案"
    assert not agent.ui.errors, "救回来了就不该再报一次模型故障"


def test_the_rescue_is_said_out_loud(monkeypatch, tmp_path):
    """压了不说 = 用户只看到"这句答得比平时空" —— 有损的东西必须留一句人话。"""
    agent, _llm, _c = _agent(monkeypatch, tmp_path,
                             [OVERFLOW, AssistantTurn(text="好")])
    agent.run_turn("问")
    assert any("压缩" in t for _kind, t in agent.ui.pushed), agent.ui.pushed


def test_a_second_overflow_gives_up_instead_of_looping(monkeypatch, tmp_path):
    """两次都超限就收手:压不动还接着撞,是拿有损压缩换一次次白等。"""
    agent, llm, calls = _agent(monkeypatch, tmp_path,
                               [OVERFLOW, OVERFLOW, AssistantTurn(text="不该到这")])
    agent.run_turn("问")
    assert len(llm.calls) == 2
    assert calls["force"] == 1, "第二次超限不该再压一遍"
    assert len(agent.ui.errors) == 1


def test_nothing_to_compact_means_no_retry(monkeypatch, tmp_path):
    """压不动(已经没什么可压的了)就照原样失败 —— 别演一次「我试过了」。"""
    agent, llm, calls = _agent(monkeypatch, tmp_path, [OVERFLOW, AssistantTurn(text="x")],
                               compact_result=False)
    agent.run_turn("问")
    assert len(llm.calls) == 1 and calls["force"] == 1
    assert len(agent.ui.errors) == 1


def test_an_ordinary_failure_does_not_touch_compaction(monkeypatch, tmp_path):
    """认不出是超限时,一次都不许压:这条针钉的就是"认错一次的代价"。"""
    boom = RuntimeError("Error code: 400 - Invalid model name")
    agent, llm, calls = _agent(monkeypatch, tmp_path, [boom])
    agent.run_turn("问")
    assert calls["force"] == 0 and len(llm.calls) == 1
    assert len(agent.ui.errors) == 1


def test_a_ui_without_push_still_gets_rescued(monkeypatch, tmp_path):
    """没有 `push` 的界面(测试桩、子代理那份静默 UI)不许因为"要说一句话"而崩。"""
    agent, llm, _calls = _agent(monkeypatch, tmp_path,
                                [OVERFLOW, AssistantTurn(text="好")], ui=FakeUI())
    agent.run_turn("问")
    assert len(llm.calls) == 2 and not agent.ui.errors
