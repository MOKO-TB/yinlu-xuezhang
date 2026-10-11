"""子代理的并发边界与嵌套(L,v0.9.155)。

这一族钉两件事:①**默认关时行为逐字不变**(开关忘了传、或者用户没开,派下去的子代理
与 v0.9.154 完全一样);②打开之后三条边界真的成立 —— 跨层**共用同一张**文件占用表、
全机在飞有名额、深度到顶就**不再给** `task`。

为什么值得单开一份:这三条各自都是"不说清就会做错"的地方(见 HANDOFF §6 63),而它们
在默认关的那条路上**一次都不会被跑到** —— 没有这一族,开关打开那天才是第一次运行。
"""
from __future__ import annotations

from dataclasses import replace as dc_replace

import pytest

from minicode import config as config_mod, subagents
from minicode.llm import AssistantTurn
from minicode.tools.base import Tool
from minicode.tools.registry import build_default_registry

from test_agent import FakeLLM, FakeUI


def _registry():
    """带上 `task` 的那份注册表。

    ⚠ **`task` 不在 `build_default_registry()` 里** —— 它由 `app_setup` 装配时再接
    一个(见 `registry.build_default_registry` 的 docstring)。第一版这几支直接拿
    `build_default_registry()` 去测,结果是**测着"注册表里本来就没有 task"**,
    而不是"子代理该不该拿到它" —— 两支因此红在一个与本题无关的原因上。
    """
    task = Tool(name="task", description="派活",
                parameters={"type": "object", "properties": {}},
                handler=lambda ctx, **kw: "ok", read_only=False)
    return build_default_registry() + [task]


class _CapturingAgent:
    """`run_subagent` 建子代理时把 ctx 与工具集记下来,**不真跑**。

    不真跑的理由:这一族要证的是"派下去的那一份是什么样",不是"子代理跑得对不对"
    (那是 `test_subagent.py` 的活);真跑一遍还得拼系统提示词(冷启动 ~10 秒,见 §6 60)。
    """
    last: "_CapturingAgent | None" = None

    def __init__(self, **kw):
        self.ctx = kw.get("ctx")
        self.registry = kw.get("registry")
        self._undo_stack = []
        type(self).last = self

    def run_turn(self, text):
        self.text = text


@pytest.fixture
def cap(monkeypatch):
    monkeypatch.setattr(subagents, "Agent", _CapturingAgent)
    return _CapturingAgent


def _dispatch(ctx, config, tmp_path, nesting=None, depth=0, tier="explore"):
    """派一路子代理,把子代理拿到的那份 ctx / 工具集取回来。

    `tier` 默认 `explore`(只读侦察);要验"能不能派活"得用 `implement` 那种写档
    —— 只读档永远拿不到 `task`(见 `test_a_read_only_tier_never_gets_task`)。
    """
    spec = subagents.type_spec(tier, tmp_path, gaps={})
    ctx = dc_replace(ctx, workspace=tmp_path, subagent_depth=depth)
    if nesting is not None:
        config = dc_replace(config, subagent_nesting=nesting)
    subagents.run_subagent(ctx, spec, "看一眼", FakeLLM([AssistantTurn(text="报告")]),
                           FakeUI(), config, _registry())
    return _CapturingAgent.last


def _names(got):
    return {t.name for t in got.registry}


# ---------------------------------------------------------------- 默认:一个字不变
def test_nesting_is_off_by_default(ctx, config, tmp_path, cap):
    """**这一支是这一族的地基**:开关默认关时,子代理拿到的工具集与 v0.9.154 一样。"""
    got = _dispatch(ctx, config, tmp_path)
    assert "explore·task" not in _names(got), "默认关:子代理不许再派活"
    assert "explore·ask_user" not in _names(got), "ask_user 本来就摘掉,别一起放回来"


def test_the_allow_task_default_is_off():
    """`sub_tool_registry` 的默认值必须是 `False` —— **"忘了传"要维持今天的形状**,
    而不是悄悄打开递归。默认值站在安全那一边,由这一支钉住。"""
    spec = subagents.type_spec("implement", ".", gaps={})
    reg = _registry()
    # ⚠ 名字**带档位前缀**(`sub_tool_registry` 最后一步是
    # `dc_replace(t, name=f"{spec.type}·{t.name}")`)。第一版这里断言的是裸名
    # `"task"`,于是**第一句是空过的**(裸名永远不在里面)——那种断言看着在钉,
    # 其实什么都没钉。
    assert "implement·task" not in {t.name for t in subagents.sub_tool_registry(reg, spec)}
    opened = subagents.sub_tool_registry(reg, spec, allow_task=True)
    assert "implement·task" in {t.name for t in opened}


def test_a_read_only_tier_never_gets_task_even_with_nesting_on(ctx, config, tmp_path, cap):
    """**开关打开也不给只读档。**

    `task` 是写档工具(`read_only=False`),而"只读档只留只读工具"那条闸在它前面 ——
    所以一个 `explore` 子代理**没法派出一路会写文件的活**。这不是巧合而是必须:
    否则"派一路只读侦察"就成了"绕开只读限制去写文件"的入口(网页版排除 `task`
    时写的正是这条理由)。
    """
    got = _dispatch(ctx, config, tmp_path, nesting=True, tier="explore")
    assert "explore·task" not in _names(got)


def test_the_config_switch_defaults_to_off():
    assert config_mod.Config().subagent_nesting is False


# ------------------------------------------------------------------ 打开之后三条
def test_turning_it_on_gives_the_subagent_task(ctx, config, tmp_path, cap):
    got = _dispatch(ctx, config, tmp_path, nesting=True, tier="implement")
    assert "implement·task" in _names(got)


def test_past_the_depth_cap_task_disappears_again(ctx, config, tmp_path, cap):
    """到顶的那一层里**连工具都不出现** —— 而不是出现了再回一句"不许派"。

    一只永远拒绝的工具只会让模型白试一次(与 `ask_user` 从子代理摘掉同一个理由)。
    """
    got = _dispatch(ctx, config, tmp_path, nesting=True, tier="implement",
                    depth=subagents.MAX_SUB_DEPTH)
    assert "implement·task" not in _names(got)
    assert got.ctx.subagent_depth == subagents.MAX_SUB_DEPTH + 1


def test_the_depth_is_incremented_and_carried(ctx, config, tmp_path, cap):
    """深度随 ctx 继承:主会话 0 → 子代理 1。它同时是"到顶了没有"的唯一依据。"""
    got = _dispatch(ctx, config, tmp_path, nesting=True)
    assert got.ctx.subagent_depth == 1


def test_the_batch_lock_table_is_shared_downwards(ctx, config, tmp_path, cap):
    """**跨层必须是同一张表。** 各建一张的话,父批与子批可以同时写同一个文件而两边
    都不报错 —— 那道"同一批里两路写同一个文件当场拦下"的程序闸就漏了(§6 63 ①)。"""
    table = subagents._BatchFileLocks()
    spec = subagents.type_spec("explore", tmp_path, gaps={})
    subagents.run_subagent(ctx, spec, "看一眼", FakeLLM([AssistantTurn(text="好")]),
                           FakeUI(), config, _registry(),
                           batch_locks=table)
    assert cap.last.ctx.batch_locks is table


def test_without_a_parent_table_the_subagent_gets_none(ctx, config, tmp_path, cap):
    """顶层派活时没有父表 —— 那一份由**这一批**自己建(`task` 里那个
    `getattr(ctx, "batch_locks", None) or ...`),不该凭空塞一张进去。"""
    got = _dispatch(ctx, config, tmp_path)
    assert got.ctx.batch_locks is None


# ------------------------------------------------------------ 全机在飞那一道名额
def test_the_inflight_ceiling_refuses_instead_of_queueing():
    """满了**不排队**,而是让调用方如实回一句。

    排队会把"等多久"变成一个没人说得清的数,而用户那边只看见一个不动的界面。
    """
    assert subagents.inflight_count() == 0, "这一支要求从零开始(上一个测试没还名额就是 bug)"
    try:
        assert all(subagents._acquire_slot()
                   for _ in range(subagents.GLOBAL_SUBAGENT_CEILING))
        assert subagents._acquire_slot() is False
        ran: list = []
        out = subagents._gated(lambda: (ran.append(1), "跑了")[1])()
        assert ran == [], "没拿到名额就**不许执行**"
        assert "完全没有执行" in out
        assert subagents.inflight_count() == subagents.GLOBAL_SUBAGENT_CEILING
    finally:
        for _ in range(subagents.GLOBAL_SUBAGENT_CEILING):
            subagents._release_slot()
    assert subagents.inflight_count() == 0


def test_a_gated_run_gives_the_slot_back_even_when_it_raises():
    """异常也要还名额 —— 不然跑几次失败之后,**再也派不动任何子代理**,
    而现象是"名额满了"这句与失败毫无关系的话。"""
    assert subagents.inflight_count() == 0

    def boom():
        raise RuntimeError("故意")

    with pytest.raises(RuntimeError):
        subagents._gated(boom)()
    assert subagents.inflight_count() == 0


def test_a_gated_run_gives_the_slot_back_on_the_normal_path():
    assert subagents.inflight_count() == 0
    assert subagents._gated(lambda: "好")() == "好"
    assert subagents.inflight_count() == 0
