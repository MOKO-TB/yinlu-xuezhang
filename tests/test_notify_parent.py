"""子代理 → 主会话留话(I,v0.9.154)。

这一族最要紧的一支**不是"能不能送到",是"送到的时候它长什么样"**。

现成的插话信箱落进留档时是 `role: user` —— `_take_inbox` 里对每一条做
`session.add_user(text)`。那是给**用户自己**的插话写的(所以他本来就是 user);
子代理的提醒走那条路,在留档里就成了"用户说过的话",而模型会把它当"用户刚提的要求"。
**所以第一支把两条路摆在一起对比着钉**,而不是只钉新的那条 —— 只钉新的那条,
将来谁把两条"顺手统一"成一条,这一族还是全绿。

其余几支各对着一个"不说清就会做错"的地方:只用一次 / 带出处 / 满了要说出口 /
主会话调它是自言自语 / 子代理真拿得到这只工具。
"""
from __future__ import annotations

import json

from minicode import app_setup, subagents
from minicode.agent import NOTE_MAX, INBOX_MAX
from minicode.llm import AssistantTurn, ToolCall
from minicode.tools import catalog_sub
from minicode.tools.registry import build_default_registry

from test_agent import FakeLLM, FakeUI, make_agent


def _sys_text(agent) -> str:
    """这次请求里那条系统侧注记(没有就回空串)。"""
    msgs = agent._messages_for_request()
    for m in msgs:
        if m.get("role") == "system" and "留话" in (m.get("content") or ""):
            return m["content"]
    return ""


def _user_texts(agent) -> list:
    return [m.get("content") or "" for m in agent.session.messages
            if m.get("role") == "user"]


# ---------------------------------------------------------------- 对比:两条信箱
def test_a_note_never_becomes_a_user_message_while_an_interjection_does(
        config, ctx, tmp_path):
    """**这一支是整族的地基。**

    留话走的是"系统侧、只活一次请求"的注记;插话走的是"并进会话、`role: user`"。
    两条路都在,所以这里**同时验两条**,并明确断"留话那句一个字都没进 user 消息"。
    """
    agent = make_agent(config, ctx, FakeLLM([]), FakeUI())

    assert agent.queue_note("子代理这边有个岔路没定") is True
    note_view = _sys_text(agent)
    assert "〔子代理留话〕子代理这边有个岔路没定" in note_view, note_view
    assert _user_texts(agent) == [], \
        "留话**绝不许**变成 user 消息 —— 那正是它不走插话那条路的理由"

    # 对照:插话那条**就是**要变成 user 消息(它不是坏的,是两个用途)
    assert agent.queue_interjection("我自己补一句") is True
    agent._take_inbox()
    assert "我自己补一句" in _user_texts(agent)


def test_a_note_is_consumed_once(config, ctx, tmp_path):
    """只用一次:同一条不该在后面的每一步请求里重复出现。"""
    agent = make_agent(config, ctx, FakeLLM([]), FakeUI())
    agent.queue_note("只说一次")
    assert "只说一次" in _sys_text(agent)
    assert _sys_text(agent) == "", "第二条请求里它还在 = 每一步都在重复"


def test_two_notes_are_joined_and_each_keeps_its_own_source_line(config, ctx, tmp_path):
    """两条一起送时各自带一行出处 —— 一句没有出处的注记,模型只能猜是谁说的。"""
    agent = make_agent(config, ctx, FakeLLM([]), FakeUI())
    agent.queue_note("甲")
    agent.queue_note("乙")
    view = _sys_text(agent)
    assert view.count("〔子代理留话〕") == 2, view


def test_an_empty_note_is_not_taken_and_whitespace_is_squeezed(config, ctx, tmp_path):
    agent = make_agent(config, ctx, FakeLLM([]), FakeUI())
    assert agent.queue_note("   ") is False
    assert agent.queue_note("") is False
    assert agent.queue_note("  甲乙\n  丙  ") is True
    assert "〔子代理留话〕甲乙 丙" in _sys_text(agent), "换行与多余空白压成一个空格"


def test_the_note_channel_has_its_own_cap(config, ctx, tmp_path):
    """上限满了回 `False`(调用方要据此如实回话),而且它**不与插话共用一个数**。

    两个数是两种东西:插话收不下由界面回一句"排队满了"(用户看得见自己发出去了),
    留话收不下必须写进**子代理自己的工具结果**。共用一个数会让"改一个影响另一个"
    变成一件看不出来的事。
    """
    agent = make_agent(config, ctx, FakeLLM([]), FakeUI())
    assert [agent.queue_note(f"第 {i} 条") for i in range(NOTE_MAX + 1)] == \
        [True] * NOTE_MAX + [False]
    # 插话那一边**一点没受影响**(它有自己的数、自己的列表)
    assert agent._inbox == [] or all("第" not in x for x in agent._inbox)
    assert agent.queue_interjection("插话照样能进") is True
    assert INBOX_MAX > 0


# ------------------------------------------------------------------ 工具那一层
def test_the_tool_hands_the_text_to_the_parent(config, ctx, tmp_path):
    got: list = []
    # 入口的约定是**回一个真假**(收下没),所以这里不能用 `list.append`
    # —— 它回 `None`,会被判成"没送到"。这不是测试的吹毛求疵:真入口
    # `Agent.queue_note` 就是回 bool,而"回 None 等于失败"正是那条约定。
    ctx.notify_parent = lambda t: (got.append(t), True)[1]
    out = catalog_sub.notify_parent(ctx, text="这边有个岔路")
    assert got == ["这边有个岔路"] and "已经留给主会话" in out


def test_the_tool_says_you_are_the_main_session(config, ctx, tmp_path):
    """主会话自己调它 —— 必须**如实说**,不许静默成功。

    静默成功的话,主会话会以为自己已经把话转给了谁,而那句话掉在地上。
    """
    assert getattr(ctx, "notify_parent", None) is None
    out = catalog_sub.notify_parent(ctx, text="喂")
    assert "你就是主会话" in out


def test_the_tool_says_so_when_the_box_is_full(config, ctx):
    """收不下必须说出口 —— 子代理看不见用户那一侧,不说它会以为自己提醒过了。"""
    ctx.notify_parent = lambda _t: False
    out = catalog_sub.notify_parent(ctx, text="要紧的事")
    assert "没送到" in out and "最终报告" in out


def test_the_tool_announces_a_truncation(config, ctx):
    got: list = []
    ctx.notify_parent = lambda t: (got.append(t), True)[1]
    out = catalog_sub.notify_parent(ctx, text="好" * (catalog_sub.NOTE_MAX_CHARS + 50))
    assert len(got[0]) == catalog_sub.NOTE_MAX_CHARS, "截断要发生在**送之前**"
    assert "只送了前面那一截" in out


def test_the_tool_refuses_an_empty_text(config, ctx):
    got: list = []
    ctx.notify_parent = got.append
    out = catalog_sub.notify_parent(ctx, text="   ")
    assert got == [] and "没写内容" in out


def test_the_tool_itself_is_read_only_and_reaches_subagents():
    """它必须**只读**(否则每次提醒都要点一次头,那只工具就没人会用了),
    而且必须真能被子代理拿到 —— 子代理的工具集是父注册表裁出来的。"""
    tool = next(t for t in build_default_registry() if t.name == "notify_parent")
    assert tool.read_only is True
    spec = subagents.type_spec("explore", ".", gaps={})
    names = {t.name for t in subagents.sub_tool_registry(build_default_registry(), spec)}
    assert "explore·notify_parent" in names


# ------------------------------------------------------------------ 管道那一支
def test_a_subagent_really_reaches_the_parent(monkeypatch, config, ctx, tmp_path):
    """**端到端那一支**:`run_subagent(notify=…)` 真把入口写进子代理的 ctx。

    系统提示词换成一个字:这一支验的是**管道**,不是提示词装配 ——
    真去拼一次要读馆藏库统计(~10 秒,见 HANDOFF §6 60),那与本支要证的事无关。
    """
    monkeypatch.setattr(app_setup, "build_system_prompt", lambda *a, **k: "sys")
    got: list = []
    spec = subagents.type_spec("explore", tmp_path, gaps={})
    llm = FakeLLM([
        AssistantTurn(tool_calls=[ToolCall(
            id="c1", name="explore·notify_parent",
            arguments=json.dumps({"text": "这边有个岔路"}))]),
        AssistantTurn(text="报告"),
    ])
    report = subagents.run_subagent(ctx, spec, "看一眼", llm, FakeUI(), config,
                                    build_default_registry(),
                                    notify={"enqueue": got.append})
    assert got == ["这边有个岔路"], f"子代理没递到父会话:{got}(报告 {report!r})"


def test_an_empty_holder_leaves_the_subagent_with_nobody_to_talk_to(
        monkeypatch, config, ctx, tmp_path):
    """容器还空着(或没传)时,子代理那边得到的是**如实的"没人可转达"**。

    装配顺序上真会出现这个窗口(`task` 工具先建、主 Agent 后建),而窗口期里
    静默"送到了"就是最坏的一种:话掉在地上,子代理以为说过了。
    """
    monkeypatch.setattr(app_setup, "build_system_prompt", lambda *a, **k: "sys")
    spec = subagents.type_spec("explore", tmp_path, gaps={})
    llm = FakeLLM([
        AssistantTurn(tool_calls=[ToolCall(
            id="c1", name="explore·notify_parent",
            arguments=json.dumps({"text": "喂"}))]),
        AssistantTurn(text="报告"),
    ])
    seen: list = []

    class _Spy(FakeUI):
        def tool_done(self, tool, result):
            seen.append(result)
            return super().tool_done(tool, result)

    subagents.run_subagent(ctx, spec, "看一眼", llm, _Spy(), config,
                           build_default_registry(), notify={})
    assert any("没有可以转达的对象" in str(s) for s in seen), seen
