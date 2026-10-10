import json
import threading

import pytest

from minicode.agent import WRAP_UP_NOTE, Agent
from minicode.llm import AssistantTurn, ToolCall
from minicode.permission import PermissionGate
from minicode.session import Session
from minicode.tools.base import ToolContext
from minicode.tools.registry import build_default_registry


class FakeLLM:
    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = []

    def stream_chat(self, messages, tools, on_text=None, on_reasoning=None, on_retry=None, **kwargs):
        self.calls.append(list(messages))
        turn = self.turns.pop(0)
        if isinstance(turn, BaseException):
            raise turn
        return turn


class FakeUI:
    def __init__(self, confirm_choice="yes"):
        self.confirm_choice = confirm_choice
        self.confirmed = []
        self.reasons = []          # 每次确认时传给界面的"为什么要做这一步"
        self.tool_results = []
        self.hard_rejected = []
        self.interrupted = False
        self.time_limited = []       # 一轮总时限到点时的 (秒, 已走步数)
        self.errors = []

    def on_text_delta(self, t): pass

    def on_reasoning_delta(self, t): pass

    def on_retry(self, e, n): pass

    def finish_stream(self): pass

    def confirm_tool(self, tool, args, reason="", note="", can_remember=False):
        self.confirmed.append((tool.name, dict(args)))
        self.reasons.append(reason)
        return self.confirm_choice

    def tool_start(self, tool, args): pass

    def tool_done(self, tool, result):
        self.tool_results.append(result)

    def tool_rejected_hard(self, tool, args):
        self.hard_rejected.append((tool.name, dict(args)))

    def compacted(self, before, after):
        self.compacted_calls = getattr(self, "compacted_calls", [])
        self.compacted_calls.append((before, after))

    def info(self, text):
        self.infos = getattr(self, "infos", [])
        self.infos.append(text)

    def turn_interrupted(self):
        self.interrupted = True

    def llm_error(self, e):
        self.errors.append(e)

    def max_steps_reached(self, n): pass

    def turn_time_limited(self, seconds, steps):
        self.time_limited.append((seconds, steps))

    def fail_streak_limit(self, tool, limit):
        self.fail_streaks = getattr(self, "fail_streaks", [])
        self.fail_streaks.append((tool, limit))

    def token_limit_reached(self, limit, used):
        self.token_limits = getattr(self, "token_limits", [])
        self.token_limits.append((limit, used))

    def task_budget_reached(self, limit, used):
        self.task_budgets = getattr(self, "task_budgets", [])
        self.task_budgets.append((limit, used))

    def daily_budget_reached(self, limit, used):
        self.daily_budgets = getattr(self, "daily_budgets", [])
        self.daily_budgets.append((limit, used))


class Clock:
    """假时钟:每读一次走 step 秒,让"一轮总时限到点"可测而不用真等。"""

    def __init__(self, step: float):
        self.t = 0.0
        self.step = step

    def monotonic(self) -> float:
        self.t += self.step
        return self.t


def make_agent(config, ctx, llm, ui):
    return Agent(
        llm=llm,
        registry=build_default_registry(),
        session=Session(system_prompt="sys"),
        gate=PermissionGate(config),
        ui=ui,
        ctx=ctx,
    )


def test_agent_executes_tool_then_finishes(config, ctx, tmp_path):
    llm = FakeLLM([
        AssistantTurn(tool_calls=[ToolCall(
            id="c1", name="write_file",
            arguments=json.dumps({"path": "hello.txt", "content": "hi"}))]),
        AssistantTurn(text="完成"),
    ])
    ui = FakeUI()
    agent = make_agent(config, ctx, llm, ui)
    agent.run_turn("创建 hello.txt")

    assert (tmp_path / "hello.txt").read_text(encoding="utf-8") == "hi"
    roles = [m["role"] for m in agent.session.messages]
    assert roles == ["system", "user", "assistant", "tool", "assistant"]
    assert agent.session.messages[-1]["content"] == "完成"
    assert ui.confirmed == [("write_file", {"path": "hello.txt", "content": "hi"})]


def test_agent_denied_tool_skips_execution(config, ctx, tmp_path):
    llm = FakeLLM([
        AssistantTurn(tool_calls=[ToolCall(
            id="c1", name="write_file",
            arguments=json.dumps({"path": "x.txt", "content": "data"}))]),
        AssistantTurn(text="好的,不写了"),
    ])
    ui = FakeUI(confirm_choice="no")
    agent = make_agent(config, ctx, llm, ui)
    agent.run_turn("写文件")

    assert not (tmp_path / "x.txt").exists()
    tool_msg = next(m for m in agent.session.messages if m["role"] == "tool")
    assert "拒绝" in tool_msg["content"]


def test_agent_hard_reject_does_not_prompt(config, ctx):
    llm = FakeLLM([
        AssistantTurn(tool_calls=[ToolCall(
            id="c1", name="bash",
            arguments=json.dumps({"command": "rm -rf /"}))]),
        AssistantTurn(text="已停止"),
    ])
    ui = FakeUI()
    agent = make_agent(config, ctx, llm, ui)
    agent.run_turn("删库")

    assert ui.hard_rejected  # 走硬拒绝,不给确认弹窗
    assert not ui.confirmed
    tool_msg = next(m for m in agent.session.messages if m["role"] == "tool")
    assert "硬拒绝" in tool_msg["content"]


def test_agent_reports_invalid_json_args(config, ctx, tmp_path):
    llm = FakeLLM([
        AssistantTurn(tool_calls=[ToolCall(id="c1", name="write_file",
                                           arguments="{bad json")]),
        AssistantTurn(text="收到"),
    ])
    ui = FakeUI()
    agent = make_agent(config, ctx, llm, ui)
    agent.run_turn("写文件")

    assert not ui.confirmed  # 参数都没解析出来,不应弹确认
    tool_msg = next(m for m in agent.session.messages if m["role"] == "tool")
    assert "JSON" in tool_msg["content"]


def test_agent_unknown_tool(config, ctx):
    llm = FakeLLM([
        AssistantTurn(tool_calls=[ToolCall(id="c1", name="fly_to_moon",
                                           arguments="{}")]),
        AssistantTurn(text="ok"),
    ])
    ui = FakeUI()
    agent = make_agent(config, ctx, llm, ui)
    agent.run_turn("起飞")
    tool_msg = next(m for m in agent.session.messages if m["role"] == "tool")
    assert "未找到工具" in tool_msg["content"]


def test_agent_keyboard_interrupt(config, ctx):
    llm = FakeLLM([KeyboardInterrupt()])
    ui = FakeUI()
    agent = make_agent(config, ctx, llm, ui)
    agent.run_turn("你好")  # 不应抛异常
    assert ui.interrupted


def _write_turn(path, content, call_id="c1"):
    return AssistantTurn(tool_calls=[ToolCall(
        id=call_id, name="write_file",
        arguments=json.dumps({"path": path, "content": content}))])


def test_undo_restores_overwrite_lifo(config, ctx, tmp_path):
    llm = FakeLLM([
        _write_turn("doc.txt", "v1", "c1"),
        AssistantTurn(text="done1"),
        _write_turn("doc.txt", "v2", "c2"),
        AssistantTurn(text="done2"),
    ])
    agent = make_agent(config, ctx, llm, FakeUI())

    agent.run_turn("第一版")
    agent.run_turn("第二版")
    assert (tmp_path / "doc.txt").read_text(encoding="utf-8") == "v2"

    assert "已撤销" in agent.undo()
    assert (tmp_path / "doc.txt").read_text(encoding="utf-8") == "v1"

    assert "已撤销" in agent.undo()
    assert not (tmp_path / "doc.txt").exists()

    assert "没有可撤销" in agent.undo()


def test_undo_restores_edit(config, ctx, tmp_path):
    (tmp_path / "f.py").write_text("a = 1\n", encoding="utf-8")
    llm = FakeLLM([
        AssistantTurn(tool_calls=[ToolCall(
            id="c1", name="edit_file",
            arguments=json.dumps({"path": "f.py", "old_string": "a = 1",
                                  "new_string": "a = 2"}))]),
        AssistantTurn(text="done"),
    ])
    agent = make_agent(config, ctx, llm, FakeUI())
    agent.run_turn("改掉")

    assert (tmp_path / "f.py").read_text(encoding="utf-8") == "a = 2\n"
    agent.undo()
    assert (tmp_path / "f.py").read_text(encoding="utf-8") == "a = 1\n"


def test_undo_skips_readonly_and_bash(config, ctx, tmp_path):
    llm = FakeLLM([
        AssistantTurn(tool_calls=[ToolCall(
            id="c1", name="bash",
            arguments=json.dumps({"command": "echo hi > from_bash.txt"}))]),
        AssistantTurn(text="done"),
    ])
    agent = make_agent(config, ctx, llm, FakeUI())
    agent.run_turn("执行命令")
    assert "没有可撤销" in agent.undo()  # bash 改动不进快照栈


def test_confirm_carries_model_reason(config, ctx, tmp_path):
    """工具自己填的 reason 会传给界面当"为什么要做这一步",且不落到工具参数里。"""
    llm = FakeLLM([
        AssistantTurn(text="我先看一眼目录。", tool_calls=[ToolCall(
            id="c1", name="bash",
            arguments=json.dumps({"command": "ls -la",
                                  "reason": "确认工作区里有哪些文件"}))]),
        AssistantTurn(text="done"),
    ])
    ui = FakeUI()
    agent = make_agent(config, ctx, llm, ui)
    agent.run_turn("看看目录")

    assert ui.reasons == ["确认工作区里有哪些文件"]
    # reason 不是 shell 参数:handler 只应收到 command
    assert "reason" not in ui.confirmed[0][1]


def test_confirm_falls_back_to_narration(config, ctx):
    """模型没填 reason 时,用同一轮里它写的说明文字兜底。"""
    llm = FakeLLM([
        AssistantTurn(text="我先确认一下当前状态。\n\n这条命令只列出目录,不会改动文件。",
                      tool_calls=[ToolCall(id="c1", name="bash",
                                           arguments=json.dumps({"command": "ls"}))]),
        AssistantTurn(text="done"),
    ])
    ui = FakeUI()
    agent = make_agent(config, ctx, llm, ui)
    agent.run_turn("看看")

    # 界面拿到的是整段说明;取"最后一段作为原因"由 explain/界面层负责
    assert ui.reasons == ["我先确认一下当前状态。\n\n这条命令只列出目录,不会改动文件。"]


# ============================================ v0.9.40 D2:步数上限与窗口这把尺子

from minicode.compaction import schema_tokens  # noqa: E402
from minicode.config import _config_from_raw, save_config  # noqa: E402
from minicode.tools.base import Tool  # noqa: E402


def _big_tool(tag: str) -> Tool:
    """一份"很占地方的工具定义":只用来把 schema 的 token 量撑起来。"""
    return Tool(name=f"tool_{tag}", description="定" * 3000,
                parameters={"type": "object",
                            "properties": {"q": {"type": "string"}}},
                handler=lambda ctx: "")


class ScriptLLM:
    """按脚本回答,并记下每次请求带了什么 —— 收尾调用必须**不带工具**。"""

    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = []            # [(messages, tools)]

    def stream_chat(self, messages, tools, on_text=None, on_reasoning=None,
                    on_retry=None, **kwargs):
        self.calls.append((list(messages), list(tools)))
        turn = self.turns.pop(0)
        if isinstance(turn, BaseException):
            raise turn
        if on_text and turn.text:
            on_text(turn.text)
        return turn


class LoudUI(FakeUI):
    def __init__(self):
        super().__init__()
        self.texts = []
        self.stops = []

    def on_text_delta(self, t):
        self.texts.append(t)

    def max_steps_reached(self, n):
        self.stops.append(n)


def _tool_turn(call_id):
    return AssistantTurn(tool_calls=[ToolCall(
        id=call_id, name="glob", arguments=json.dumps({"pattern": "*.md"}))])


# ------------------------------------------------------------------ max_steps

def test_max_steps_comes_from_config(config, ctx):
    agent = make_agent(config, ctx, FakeLLM([]), FakeUI())
    assert agent.max_steps == 40 == config.max_steps


def test_bumping_config_moves_the_knob(config, ctx):
    """调大旋钮是真的生效,不是 config 里写了没人读。"""
    config.max_steps = 3
    agent = make_agent(config, ctx, FakeLLM([]), FakeUI())
    assert agent.max_steps == 3


def test_explicit_argument_beats_config(config, ctx):
    """子代理那一档仍由调用方给数:`task` 工具不该跟着主循环的旋钮跑满 40 步。"""
    config.max_steps = 40
    agent = Agent(llm=FakeLLM([]), registry=build_default_registry(),
                  session=Session(system_prompt="sys"), gate=PermissionGate(config),
                  ui=FakeUI(), ctx=ctx, max_steps=8)
    assert agent.max_steps == 8


def test_old_config_without_the_key_behaves_as_before():
    assert _config_from_raw({}).max_steps == 40
    assert _config_from_raw({"max_steps": 5}).max_steps == 5
    assert _config_from_raw({"max_steps": "7"}).max_steps == 7


def test_nonsense_max_steps_cannot_turn_the_loop_off():
    """0/负数会让人以为"关掉了对抗上限",实际是会转空圈:夹到至少 1。"""
    assert _config_from_raw({"max_steps": 0}).max_steps == 40
    assert _config_from_raw({"max_steps": -5}).max_steps == 1


def test_max_steps_survives_a_save_reload_cycle(config, tmp_path):
    """落盘再读回来还是这个数 —— 没进序列化白名单的字段会静默丢掉。"""
    import json
    config.max_steps = 12
    path = tmp_path / "config.json"
    save_config(path, config)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["max_steps"] == 12
    assert _config_from_raw(raw).max_steps == 12


# ------------------------------------------------------- 窗口这把尺子量了什么

def _chatty_session(turns: int = 4) -> Session:
    """几句真对话:够压缩函数下手(它按整轮折,少于三轮就不动)。"""
    s = Session(system_prompt="sys")
    for i in range(turns):
        s.add_user(f"问 {i} 今天有什么课")
        s.add_assistant(AssistantTurn(text=f"答 {i} 有课"))
    return s


def test_schema_tokens_are_counted_into_the_compact_trigger(config, ctx):
    """同一个会话、同样的消息量:只多了一份大 schema,就该触发压缩。

    旧算法只数消息,这种"消息没几句、工具定义几千 token"的回合会一路撞到
    真实窗口才想起来压 —— 这条钉住那几千 token 不再被漏掉。
    """
    config.context_window = 2000
    config.compact_at_ratio = 0.5          # 阈值 1000:消息够不到,两份大 schema 够得到
    llm = ScriptLLM([AssistantTurn(text="(摘要)"), AssistantTurn(text="今天有课")])
    agent = Agent(llm=llm, registry=[_big_tool("a"), _big_tool("b")],
                  session=_chatty_session(), gate=PermissionGate(config),
                  ui=FakeUI(), ctx=ctx)
    assert agent.tools_tokens > 1000
    agent.run_turn("你好")

    assert len(llm.calls) == 2
    first_msgs, first_tools = llm.calls[0]
    assert first_tools == []                                  # 摘要那一次不给工具
    assert "会话压缩器" in first_msgs[0]["content"]
    assert "(此前对话的摘要" in agent.session.messages[1]["content"]


def test_small_registry_does_not_trigger_on_schema_alone(config, ctx):
    """同样的消息、没有大工具定义:不该压 —— 压早了也是代价。"""
    config.context_window = 2000
    config.compact_at_ratio = 0.5
    llm = ScriptLLM([AssistantTurn(text="你好呀")])
    agent = Agent(llm=llm, registry=[], session=_chatty_session(),
                  gate=PermissionGate(config), ui=FakeUI(), ctx=ctx)
    assert agent.tools_tokens == schema_tokens([])
    agent.run_turn("你好")
    assert len(llm.calls) == 1          # 只有一次真请求,没顺手去压缩


def test_late_added_mcp_tools_are_in_the_one_measurement(config, ctx):
    """注册表在 Agent 构造前就装配完(MCP 工具已并进来),所以算一次就够。"""
    small = Agent(llm=FakeLLM([]), registry=build_default_registry(),
                  session=Session(system_prompt="sys"), gate=PermissionGate(config),
                  ui=FakeUI(), ctx=ctx)
    big = Agent(llm=FakeLLM([]), registry=build_default_registry() + [_big_tool("m")],
                session=Session(system_prompt="sys"), gate=PermissionGate(config),
                ui=FakeUI(), ctx=ctx)
    assert big.tools_tokens > small.tools_tokens
    # 整表一次序列化:加一个成员,增量就是那一份定义本身(误差只有分隔符那几个字符)
    one = schema_tokens([_big_tool("m")]) - schema_tokens([])
    assert abs((big.tools_tokens - small.tools_tokens) - one) <= 4


# --------------------------------------------------------- 撞上限之后的那段收尾

def test_steps_run_out_then_it_wraps_up(config, ctx):
    llm = ScriptLLM([_tool_turn("c1"), _tool_turn("c2"), AssistantTurn(text="我做完了 A、B,还差 C。")])
    ui = LoudUI()
    agent = Agent(llm=llm, registry=build_default_registry(),
                  session=Session(system_prompt="sys"), gate=PermissionGate(config),
                  ui=ui, ctx=ctx, max_steps=2)
    agent.run_turn("接着做")

    assert ui.stops == [2]
    assert llm.calls[-1][1] == []       # 收尾那一次**不带工具**
    assert "我做完了 A、B,还差 C。" in "".join(ui.texts)
    assert agent.session.messages[-1]["role"] == "assistant"


def test_wrap_up_leaves_no_dangling_tool_result(config, ctx):
    """撞上限时尾部正挂着没被读过的工具结果;收尾之后不许再留孤儿。"""
    llm = ScriptLLM([_tool_turn("c1"), _tool_turn("c2"), AssistantTurn(text="收")])
    agent = Agent(llm=llm, registry=build_default_registry(),
                  session=Session(system_prompt="sys"), gate=PermissionGate(config),
                  ui=LoudUI(), ctx=ctx, max_steps=2)
    agent.run_turn("跑两步")
    msgs = agent.session.messages
    assert msgs[-1]["role"] == "assistant"
    for i, m in enumerate(msgs):
        if m["role"] == "assistant" and m.get("tool_calls"):
            ids = {tc["id"] for tc in m["tool_calls"]}
            followed = {x.get("tool_call_id") for x in msgs[i + 1:i + 1 + len(ids)]}
            assert ids <= followed, "有工具调用没等到结果,下一轮请求会被 API 挡下"


def test_normal_finish_never_calls_the_wrap_up(config, ctx):
    llm = ScriptLLM([AssistantTurn(text="就这")])
    ui = LoudUI()
    agent = Agent(llm=llm, registry=build_default_registry(),
                  session=Session(system_prompt="sys"), gate=PermissionGate(config),
                  ui=ui, ctx=ctx, max_steps=2)
    agent.run_turn("在吗")
    assert len(llm.calls) == 1
    assert ui.stops == []


def test_wrap_up_failure_falls_back_to_the_old_behavior(config, ctx):
    """收尾自己失败就静默:最坏情况等于本轮改动之前,不能因为收尾把整回合判成错误。"""
    boom = ScriptLLM([_tool_turn("c1"), _tool_turn("c2"), RuntimeError("连不上")])
    ui = LoudUI()
    agent = Agent(llm=boom, registry=build_default_registry(),
                  session=Session(system_prompt="sys"), gate=PermissionGate(config),
                  ui=ui, ctx=ctx, max_steps=2)
    agent.run_turn("跑两步")
    assert ui.stops == [2]
    assert agent.session.messages[-1]["role"] == "tool"    # 回到"悬着结束"的旧行为
    assert ui.errors == []                                  # 没把它报成一次对话失败


def test_wrap_up_tool_calls_are_dropped_so_the_next_turn_still_works(config, ctx):
    """没给工具却回吐 tool_calls 的端点:摘掉再入会话,否则下一轮每个请求都 400。"""
    llm = ScriptLLM([_tool_turn("c1"), _tool_turn("c2"), _tool_turn("c9")])
    agent = Agent(llm=llm, registry=build_default_registry(),
                  session=Session(system_prompt="sys"), gate=PermissionGate(config),
                  ui=LoudUI(), ctx=ctx, max_steps=2)
    agent.run_turn("跑两步")
    assert not agent.session.messages[-1].get("tool_calls")


def test_wrap_up_tells_the_model_it_has_no_tools_left(config, ctx):
    """收尾那次要捎一句"工具用完了",否则模型会照着"下一步我去查一下"的口吻继续说。

    这句只活在这次请求里 —— 会话留档里不该出现一句用户没说过的话。
    """
    llm = ScriptLLM([_tool_turn("c1"), _tool_turn("c2"), AssistantTurn(text="收")])
    agent = Agent(llm=llm, registry=build_default_registry(),
                  session=Session(system_prompt="sys"), gate=PermissionGate(config),
                  ui=LoudUI(), ctx=ctx, max_steps=2)
    agent.run_turn("跑两步")
    sent = llm.calls[-1][0]
    assert sent[-1] == {"role": "user", "content": WRAP_UP_NOTE}
    assert all(m.get("content") != WRAP_UP_NOTE for m in agent.session.messages)
    # 步数还够得着的那几次请求,绝不带这句
    assert all(msgs[-1].get("content") != WRAP_UP_NOTE for msgs, _t in llm.calls[:-1])


def test_context_tokens_is_messages_plus_tool_definitions(config, ctx):
    from minicode.compaction import estimate_tokens
    agent = make_agent(config, ctx, FakeLLM([]), FakeUI())
    assert agent.context_tokens() == (estimate_tokens(agent.session.messages)
                                      + agent.tools_tokens)


# ------------------------------------------------------- 一轮总时限

def test_turn_time_limit_stops_between_steps(config, ctx, monkeypatch):
    """到点只在两步之间收手:不再问模型,但已发出去的那一步照常走完。
    v0.9.50 起到点后补一次**不带工具**的收尾(wrap_up) —— 与撞步数上限同一
    口径:此刻工具结果已躺在上下文里,直接 return 就是最戛然而止的出口。"""
    from minicode import agent as agent_mod
    monkeypatch.setattr(agent_mod, "time", Clock(6.0))
    config.turn_time_limit_seconds = 10
    llm = FakeLLM([
        AssistantTurn(tool_calls=[ToolCall(
            id="c1", name="glob", arguments=json.dumps({"pattern": "**/*"}))]),
        AssistantTurn(text="已经跑完这一步,剩下的下次接着来。"),
    ])
    ui = FakeUI()
    agent = make_agent(config, ctx, llm, ui)
    agent.run_turn("找文件")

    assert ui.time_limited == [(10, 1)]             # 报告:时限 + 已经走了几步
    assert ui.tool_results                          # 已派出去的那一步有结果
    assert len(llm.calls) == 2                      # 第二次 = wrap_up 收尾
    assert llm.calls[1][-1]["content"] == WRAP_UP_NOTE
    assert agent.session.messages[-1]["content"] == "已经跑完这一步,剩下的下次接着来。"


def test_turn_time_limit_zero_means_unlimited(config, ctx, monkeypatch):
    """0 = 不限:哪怕假时钟一小时走八回,也不会自己停下。"""
    from minicode import agent as agent_mod
    monkeypatch.setattr(agent_mod, "time", Clock(3600.0))
    config.turn_time_limit_seconds = 0
    llm = FakeLLM([
        AssistantTurn(tool_calls=[ToolCall(
            id="c1", name="glob", arguments=json.dumps({"pattern": "**/*"}))]),
        AssistantTurn(text="完成"),
    ])
    ui = FakeUI()
    make_agent(config, ctx, llm, ui).run_turn("找文件")

    assert ui.time_limited == [] and len(llm.calls) == 2


def test_compact_failure_does_not_kill_the_turn(config, ctx, monkeypatch):
    """压缩是一次真实的模型调用(把早期对话发出去做摘要):它失败绝不能把整个
    回合带走 —— 那样用户的提问连模型都没见到,报的还不是"模型调用失败"那句
    人话。失败退回不压缩,回答照常产出(v0.9.50)。"""
    config.context_window = 100          # 阈值 = 1 token,任何消息都超 → 必触发
    config.compact_at_ratio = 0.01
    llm = FakeLLM([
        RuntimeError("模型连不上"),      # 第一次调用 = 压缩那次:抛
        AssistantTurn(text="回答照常"),
    ])
    ui = FakeUI()
    agent = make_agent(config, ctx, llm, ui)
    for i in range(3):                       # 预置 3 轮历史:否则 compact 认定
        agent.session.add_user(f"旧问题{i}")  # "无可压缩"(<=keep_recent)压根不调模型
        agent.session.add_assistant(AssistantTurn(text=f"旧回答{i}"))
    agent.run_turn("给我讲讲选课")

    assert agent.session.messages[-1]["content"] == "回答照常"
    assert "先直接回答" in "".join(ui.infos)        # 失败要说人话,不是炸
    assert any("整理成摘要" in t for t in ui.infos)  # 开始整理时有提示,不再是静默黑洞
    # 本轮的提问在 recent 轮里,没有被压掉(压掉的是更早的"旧问题0")
    assert any(m.get("role") == "user" and m.get("content") == "给我讲讲选课"
               for m in agent.session.messages)


def test_compact_success_still_replaces_messages(config, ctx):
    """失败兜底不能把成功路径改坏:压得动时照常替换并推 compacted。"""
    config.context_window = 100
    config.compact_at_ratio = 0.01
    llm = FakeLLM([
        AssistantTurn(text="(此前对话的摘要)同学问了选课。"),
        AssistantTurn(text="回答"),
    ])
    ui = FakeUI()
    agent = make_agent(config, ctx, llm, ui)
    for i in range(3):
        agent.session.add_user(f"旧问题{i}")
        agent.session.add_assistant(AssistantTurn(text=f"旧回答{i}"))
    agent.run_turn("给我讲讲选课")

    assert getattr(ui, "compacted_calls", None), "压缩成功要推 compacted 事件"
    assert agent.session.messages[-1]["content"] == "回答"


def test_write_bad_python_gets_syntax_feedback(config, ctx, tmp_path):
    """Python 写后自检(借鉴 aider 的 lint-after-edit):write_file 落盘解析不过
    的 .py,工具结果当场带回行号与报错 —— 模型立刻修,而不是留一个假象。"""
    llm = FakeLLM([
        AssistantTurn(tool_calls=[ToolCall(
            id="c1", name="write_file",
            arguments=json.dumps({"path": "oops.py", "content": "def f(:\n"}))]),
        AssistantTurn(text="已修正"),
    ])
    ui = FakeUI()
    agent = make_agent(config, ctx, llm, ui)
    agent.run_turn("写个脚本")

    feedback = agent.session.messages[-2]["content"]      # 工具结果那条
    assert "语法自检" in feedback and "第 1 行" in feedback
    assert (tmp_path / "oops.py").exists()


def test_write_good_python_has_no_syntax_noise(config, ctx, tmp_path):
    llm = FakeLLM([
        AssistantTurn(tool_calls=[ToolCall(
            id="c1", name="write_file",
            arguments=json.dumps({"path": "ok.py", "content": "x = 1\n"}))]),
        AssistantTurn(text="完成"),
    ])
    agent = make_agent(config, ctx, llm, FakeUI())
    agent.run_turn("写个脚本")

    feedback = agent.session.messages[-2]["content"]
    assert "语法自检" not in feedback
    # 非 .py 文件不检
    llm2 = FakeLLM([
        AssistantTurn(tool_calls=[ToolCall(
            id="c2", name="write_file",
            arguments=json.dumps({"path": "note.txt", "content": "def f(:\n"}))]),
        AssistantTurn(text="完成"),
    ])
    agent2 = make_agent(config, ctx, llm2, FakeUI())
    agent2.run_turn("写个笔记")
    assert "语法自检" not in agent2.session.messages[-2]["content"]


# ---------------------------------------------------------------- 失败连击止损(v0.9.77)

def _fail_calls(name, args, n, start=0):
    """n 个回合,每回合调一次同一个(可能失败的)工具;结果由 registry 真跑决定。"""
    from minicode.llm import AssistantTurn, ToolCall
    return [AssistantTurn(tool_calls=[
        ToolCall(id=f"c{start + i}", name=name, arguments=json.dumps(args))])
        for i in range(n)]


def test_fail_streak_stops_after_three_identical_failures(config, ctx):
    """同一个调用连挂 3 次:第 3 次后停止工具循环,收一次尾并把文案换成
    「说明卡点、交给用户」—— 不再原样重试烧 token 到 max_steps。"""
    ui = FakeUI()
    turns = (_fail_calls("nope", {"x": 1}, 3)
             + [AssistantTurn(text="收尾:卡在了一个不存在的工具上")])
    llm = FakeLLM(turns)
    agent = make_agent(config, ctx, llm, ui)
    agent.max_steps = 20
    agent.run_turn("做个事")
    assert getattr(ui, "fail_streaks", []) == [("nope", 3)]
    assert llm.turns == []                          # 收尾那次真的发出了
    # 收尾 note 只进那一次请求、不入留档(_wrap_up 的既有口径),从发出的请求里断
    last_request = llm.calls[-1]
    assert "连续失败 3 次" in last_request[-1]["content"]   # 收尾文案是失败版的
    assert "轮数已经用完" not in last_request[-1]["content"]  # 不是步数那条


def test_fail_streak_resets_on_arg_change(config, ctx):
    """换参数重试是健康的思路调整:键变了计数重置,不触发止损。"""
    ui = FakeUI()
    turns = (_fail_calls("nope", {"x": 1}, 2)
             + _fail_calls("nope", {"x": 2}, 1, start=2)
             + _fail_calls("nope", {"x": 1}, 1, start=3)
             + [AssistantTurn(text="还在试")])
    agent = make_agent(config, ctx, FakeLLM(turns), ui)
    agent.max_steps = 20
    agent.run_turn("做个事")
    assert getattr(ui, "fail_streaks", []) == []    # 没触发
    assert agent._fail_streak_count == 1            # 新键下只累计到 1


def test_fail_streak_resets_on_success(config, ctx):
    """中间成功一次就重置:失败的连击必须**连续**才算数。"""
    ui = FakeUI()
    ok = [AssistantTurn(tool_calls=[ToolCall(id="ok1", name="nope2",
                                             arguments=json.dumps({"y": 0}))])]
    # nope2 不存在?换一个真成功的:用 read_file 读一个真文件
    (ctx.workspace / "ok.txt").write_text("hi", encoding="utf-8")
    ok = [AssistantTurn(tool_calls=[ToolCall(id="ok1", name="read_file",
                                             arguments=json.dumps({"path": "ok.txt"}))])]
    turns = (_fail_calls("nope", {"x": 1}, 2) + ok
             + _fail_calls("nope", {"x": 1}, 2, start=5)
             + [AssistantTurn(text="继续")])
    agent = make_agent(config, ctx, FakeLLM(turns), ui)
    agent.max_steps = 20
    agent.run_turn("做个事")
    assert getattr(ui, "fail_streaks", []) == []    # 成功打断了连击


def test_fail_streak_counts_across_tool_calls_in_one_turn(config, ctx):
    """同一回合里并行发三次同样的失败调用:也算连击(键相同)。"""
    ui = FakeUI()
    from minicode.llm import AssistantTurn, ToolCall
    turns = [AssistantTurn(tool_calls=[
        ToolCall(id=f"p{i}", name="nope", arguments=json.dumps({"x": 1}))
        for i in range(3)])]
    agent = make_agent(config, ctx, FakeLLM(turns), ui)
    agent.max_steps = 20
    agent.run_turn("做个事")
    assert getattr(ui, "fail_streaks", []) == [("nope", 3)]


# ---------------------------------------------------------------- 成本闸门(v0.9.78)

def _turn_with_usage(calls, pt=40, ct=10):
    from minicode.llm import AssistantTurn, ToolCall
    return AssistantTurn(
        tool_calls=[ToolCall(id=c["id"], name=c["name"],
                             arguments=json.dumps(c["args"]))
                    for c in calls],
        usage={"prompt_tokens": pt, "completion_tokens": ct})


def test_session_token_limit_stops_between_turns(config, ctx):
    """会话累计消耗(只认服务端 usage)超过上限:两步之间收手,收一次尾。

    口径与两条时限一致 —— 检查在循环顶部,用的是上一回合结束时的数,
    正在跑的那一步不受影响(不承诺"强杀")。
    """
    config.session_token_limit = 100        # 每回合 +50:第 4 轮顶部 150 > 100
    ui = FakeUI()
    turns = [
        _turn_with_usage([{"id": "c1", "name": "nope", "args": {"x": 1}}]),
        _turn_with_usage([{"id": "c2", "name": "nope", "args": {"x": 2}}]),
        _turn_with_usage([{"id": "c3", "name": "nope", "args": {"x": 3}}]),
        AssistantTurn(text="收尾:做到一半,剩下的等继续"),
    ]
    llm = FakeLLM(turns)
    agent = make_agent(config, ctx, llm, ui)
    agent.max_steps = 20
    agent.run_turn("做个事")
    assert getattr(ui, "token_limits", []) == [(100, 150)]
    assert agent.turn_tokens == 150
    # 收尾那次请求带的是成本版文案;第 4 轮的工具没被发出去
    last_request = llm.calls[-1]
    assert "session_token_limit" in last_request[-1]["content"]
    assert any(m.get("tool_call_id") == "c3" for m in agent.session.messages)
    assert not any(m.get("tool_call_id") == "c4" for m in agent.session.messages)


def test_session_token_limit_zero_means_unlimited(config, ctx):
    """默认 0 = 不限:消耗再大也不触发(config 未登记时老 config 行为不变)。"""
    ui = FakeUI()
    turns = [_turn_with_usage([{"id": f"c{i}", "name": "nope",
                                "args": {"x": i}}], pt=500, ct=100)
             for i in range(3)] + [AssistantTurn(text="完")]
    llm = FakeLLM(turns)
    agent = make_agent(config, ctx, llm, ui)
    agent.max_steps = 20
    agent.run_turn("做个事")
    assert getattr(ui, "token_limits", None) is None
    assert agent.turn_tokens == 1800


def test_session_token_limit_counts_across_turns_of_same_session(config, ctx):
    """闸门数的是**会话累计**(agent 实例生命周期),不是单回合:跨 run_turn 累计。"""
    config.session_token_limit = 120
    ui = FakeUI()
    llm = FakeLLM([
        _turn_with_usage([{"id": "c1", "name": "nope", "args": {"x": 1}}], pt=60, ct=10),
        AssistantTurn(text="第一轮完"),
        _turn_with_usage([{"id": "c2", "name": "nope", "args": {"x": 2}}], pt=60, ct=10),
        AssistantTurn(text="收尾"),
    ])
    agent = make_agent(config, ctx, llm, ui)
    agent.max_steps = 20
    agent.run_turn("第一问")            # 70 < 120,不触发
    assert getattr(ui, "token_limits", None) is None
    agent.run_turn("第二问")            # 累计 140 > 120,这轮顶部触发
    assert getattr(ui, "token_limits", []) == [(120, 140)]


def test_task_token_budget_resets_for_each_user_turn(config, ctx):
    """任务预算数的是这一句用户指令;下一句「继续」重新起算。"""
    config.task_token_budget = 60
    ui = FakeUI()
    llm = FakeLLM([
        _turn_with_usage([{"id": "c1", "name": "nope", "args": {"x": 1}}], pt=40, ct=10),
        AssistantTurn(text="第一轮完"),
        _turn_with_usage([{"id": "c2", "name": "nope", "args": {"x": 2}}], pt=40, ct=10),
        AssistantTurn(text="第二轮完"),
    ])
    agent = make_agent(config, ctx, llm, ui)
    agent.max_steps = 20
    agent.run_turn("第一问")
    agent.run_turn("继续")
    assert getattr(ui, "task_budgets", None) is None
    assert agent.turn_tokens == 100 and agent._run_tokens == 50


def test_task_token_budget_stops_between_turns(config, ctx):
    config.task_token_budget = 50
    ui = FakeUI()
    turns = [
        _turn_with_usage([{"id": "c1", "name": "nope", "args": {"x": 1}}], pt=41, ct=10),
        _turn_with_usage([{"id": "c2", "name": "nope", "args": {"x": 2}}], pt=41, ct=10),
        AssistantTurn(text="收尾"),
    ]
    llm = FakeLLM(turns)
    agent = make_agent(config, ctx, llm, ui)
    agent.max_steps = 20
    agent.run_turn("做个事")
    assert getattr(ui, "task_budgets", []) == [(50, 51)]
    assert "task_token_budget" in llm.calls[-1][-1]["content"]
    assert any(m.get("tool_call_id") == "c1" for m in agent.session.messages)
    assert not any(m.get("tool_call_id") == "c2" for m in agent.session.messages)


def test_daily_budget_hard_gate_and_one_run_override(config, ctx, tmp_path, monkeypatch):
    from minicode import usage as usage_mod
    monkeypatch.setattr(usage_mod, "db_path", lambda: tmp_path / "u.db")
    usage_mod.set_db_path(tmp_path / "u.db")
    usage_mod.reset_daily_budget_override()
    usage_mod.record_usage("p", "m", {"prompt_tokens": 90, "completion_tokens": 10})
    config.daily_token_budget = 100
    ui = FakeUI()
    llm = FakeLLM([
        _turn_with_usage([{"id": "c1", "name": "nope", "args": {"x": 1}}]),
        AssistantTurn(text="收尾"),
    ])
    agent = make_agent(config, ctx, llm, ui)
    agent.max_steps = 20
    agent.run_turn("做个事")
    assert getattr(ui, "daily_budgets", []) == [(100, 100)]
    assert "daily_token_budget" in llm.calls[-1][-1]["content"]
    usage_mod.continue_daily_budget()
    agent.run_turn("今天继续")
    assert len(getattr(ui, "daily_budgets", [])) == 1
    assert usage_mod.budget_state(100)["level"] == "over"
    assert usage_mod.budget_state(100)["overridden"] is True
    usage_mod.reset_daily_budget_override()
    assert usage_mod.budget_state(100)["overridden"] is False


def test_session_limit_outranks_daily_limit_but_both_are_recorded(config, ctx,
                                                                  tmp_path, monkeypatch):
    """会话闸与日预算**同时**触顶:正文报会话那条,但两条原因都在。

    构造:日预算 100,库里先垫 30;第一回合 LLM 回 70 → 日账正好 100(超),
    会话累计 70 > 69(超)。第二回合顶部两闸同时命中 —— 会话先判,但不能把
    日预算那条吞掉:limits 里两条都要有,正文补一句"今天也已经超了"。
    """
    from minicode import usage as usage_mod
    monkeypatch.setattr(usage_mod, "db_path", lambda: tmp_path / "u.db")
    usage_mod.set_db_path(tmp_path / "u.db")
    usage_mod.reset_daily_budget_override()
    usage_mod.record_usage("p", "m", {"prompt_tokens": 20, "completion_tokens": 10})
    config.session_token_limit = 69
    config.daily_token_budget = 100
    ui = FakeUI()
    llm = FakeLLM([_turn_with_usage([{"id": "c1", "name": "nope",
                                       "args": {"x": 1}}], pt=60, ct=10),
                   AssistantTurn(text="收尾")])
    agent = make_agent(config, ctx, llm, ui)
    agent.max_steps = 20
    agent.run_turn("做个事")
    assert getattr(ui, "token_limits", []) == [(69, 70)]
    assert getattr(ui, "daily_budgets", []) == [(100, 100)]
    assert "session_token_limit" in llm.calls[-1][-1]["content"]
    # 正文补了日预算那条,没把"今天也已经超了"藏起来
    assert "daily_token_budget" in llm.calls[-1][-1]["content"]


# ------------------------------------------- 后台命令状态搭在请求上(v0.9.85)

class _StubJob:
    """喂 `_messages_for_request` 用的假任务:已结束、还没播报过。

    字段跟着真 `_BgJob` 走(多出来的 `raw`/`recorded`/`exit_code` 是验收补账要读的)——
    故意不做容错:真类加字段而桩不加,这条测试就该红,而不是被 getattr 悄悄兜住。
    """

    def __init__(self, jid: str = "j9"):
        self.id = jid
        self.running = False
        self.announced = False
        self.raw = "pytest -q"
        self.recorded = False
        self.exit_code = 0
        # 真 `_BgJob` 有的字段这里就得有(加字段而桩不加,这条测试该红不是该被 getattr 兜住)。
        # `notified` 走的是读输出那条线程,状态注入这条路不碰它 —— 所以这里给 False
        # 也不会在跑测试时真弹通知;哪天真把回执也接到"被看见"的落点上,这条测试会先红。
        self.notified = False
        self.lock = threading.Lock()

    def tail(self, n: int = 400) -> str:
        return "1 passed"

    def line(self) -> str:
        return "j9 已结束,退出码 0 · 已输出 12 字 · pytest -q"


def test_bg_brief_rides_the_request_not_the_transcript(config, ctx, monkeypatch):
    """状态行只活在本次请求里:留档一个字都不该多,系统提示词(整段前缀)也不动。"""
    from minicode.tools import shell
    monkeypatch.setattr(shell, "_bg_jobs", {"j9": _StubJob()})
    agent = make_agent(config, ctx, FakeLLM([]), FakeUI())
    before = len(agent.session.messages)
    msgs = agent._messages_for_request()
    assert msgs[-1]["role"] == "user" and "本机后台命令现状" in msgs[-1]["content"]
    assert "j9 已结束,退出码 0" in msgs[-1]["content"]
    assert len(agent.session.messages) == before, "写进留档 = 之后每一步都重复带着"
    assert msgs[0]["content"] == "sys", "塞进系统提示词会把 KV Cache 前缀打断"
    # 播报的同一刻补进验收账(慢验收走后台就靠这一步;账在 ctx 上,不进留档)
    assert [r["command"] for r in getattr(ctx, "verify_runs", [])] == ["pytest -q"]
    assert len(agent.session.messages) == before, "补账也不许顺手往留档写东西"


def test_no_bg_jobs_adds_nothing_to_the_request(config, ctx, monkeypatch):
    from minicode.tools import shell
    monkeypatch.setattr(shell, "_bg_jobs", {})
    agent = make_agent(config, ctx, FakeLLM([]), FakeUI())
    msgs = agent._messages_for_request()
    assert len(msgs) == 1 and msgs[0]["role"] == "system"


# FakeUI 本来就记 info(自我基类第 59 行)与确认调用,不需要额外子类。


def test_project_allow_leaves_both_traces(config, tmp_path, monkeypatch):
    """按项目白名单放行必须留两处痕:安全日志一行 + 界面一条 info。

    这是"用户当场没点过头"的放行,静默等于没有审计 —— 而这份文件被设计成
    不可自证(见 permspec 模块开头),事后能查就是唯一的兜底。
    """
    import json as _json
    from minicode import permspec, provenance
    from minicode.permission import PermissionGate
    from minicode.tools.registry import build_default_registry

    log = []
    monkeypatch.setattr(provenance, "log_escalation",
                        lambda gate, name, args, note, choice: log.append(
                            (gate, name, note, choice)))
    monkeypatch.setattr(permspec, "trust_path", lambda: tmp_path / "trust.json")

    ws = tmp_path / "proj"
    (ws / "notes").mkdir(parents=True)
    (ws / permspec.PERMISSIONS_FILE).write_text(
        _json.dumps({"allow": [{"tool": "write_file", "path_prefix": "notes/"}]},
                    ensure_ascii=False), encoding="utf-8")
    names = {t.name for t in build_default_registry()}
    spec = permspec.load(ws, names)
    permspec.trust(ws, spec.sha, len(spec.rules))

    ctx = ToolContext(workspace=ws, config=config)
    llm = FakeLLM([
        AssistantTurn(tool_calls=[ToolCall(
            id="w1", name="write_file",
            arguments=_json.dumps({"path": "notes/a.md", "content": "x"}))]),
        AssistantTurn(text="写好"),
    ])
    ui = FakeUI()
    agent = make_agent(config, ctx, llm, ui)
    agent.gate = PermissionGate(config, workspace=ws, tool_names=names)
    agent.run_turn("写个笔记")

    assert (ws / "notes" / "a.md").exists(), "白名单没生效 = 这条测试没测到东西"
    assert log and log[0][0] == "project_allow", log
    assert "第 1 条" in log[0][2] and log[0][3] == "auto"
    infos = getattr(ui, "infos", [])
    assert any("没问你" in t for t in infos), infos
    assert any("改一个字" in t for t in infos), "界面上要看得出这是一份会失效的授权"


def test_untrusted_whitelist_still_asks_and_leaves_no_trace(config, tmp_path, monkeypatch):
    """没批准过的白名单不生效:该弹确认框弹确认框,不许出现 project_allow 留痕。"""
    import json as _json
    from minicode import permspec, provenance
    from minicode.permission import PermissionGate
    from minicode.tools.registry import build_default_registry

    log = []
    monkeypatch.setattr(provenance, "log_escalation",
                        lambda *a: log.append(a))
    monkeypatch.setattr(permspec, "trust_path", lambda: tmp_path / "trust.json")
    ws = tmp_path / "proj2"
    (ws / "notes").mkdir(parents=True)
    (ws / permspec.PERMISSIONS_FILE).write_text(
        _json.dumps({"allow": [{"tool": "write_file", "path_prefix": "notes/"}]}),
        encoding="utf-8")
    names = {t.name for t in build_default_registry()}
    ctx = ToolContext(workspace=ws, config=config)
    llm = FakeLLM([
        AssistantTurn(tool_calls=[ToolCall(
            id="w1", name="write_file",
            arguments=_json.dumps({"path": "notes/a.md", "content": "x"}))]),
        AssistantTurn(text="算了"),
    ])
    ui = FakeUI(confirm_choice="no")
    agent = make_agent(config, ctx, llm, ui)
    agent.gate = PermissionGate(config, workspace=ws, tool_names=names)
    agent.run_turn("写个笔记")
    assert not (ws / "notes" / "a.md").exists()      # 用户拒绝了 = 本来就该问
    assert ui.confirmed, "没批准过就该弹确认框"
    assert not [e for e in log if e[0] == "project_allow"]
    assert not any("没问你" in t for t in getattr(ui, "infos", []))


# ------------------------------------ 压缩那把尺子:服务端实测当底线(v0.9.97)


def _usage(pt: int) -> dict:
    return {"prompt_tokens": pt, "completion_tokens": 5, "total_tokens": pt + 5}


def _agent(config, ctx, llm, session=None, registry=None):
    return Agent(llm=llm, registry=registry or [], session=session or _chatty_session(),
                 gate=PermissionGate(config), ui=FakeUI(), ctx=ctx)


def test_real_prompt_tokens_set_the_floor(config, ctx):
    """端点给了 usage:量窗口就用那个真数,不再靠字符估。"""
    config.context_window = 200000
    agent = _agent(config, ctx, ScriptLLM([AssistantTurn(text="好", usage=_usage(9000))]))
    agent.run_turn("再来一句")
    assert agent.context_source() == "real"
    assert agent.context_tokens() == 9000


def test_a_smaller_real_number_never_shortens_the_ruler(config, ctx):
    """只往上顶、不往下压:实测比估算小就用估算,不许把压缩推晚。"""
    config.context_window = 200000
    agent = _agent(config, ctx, ScriptLLM([AssistantTurn(text="好", usage=_usage(10))]),
                   registry=[_big_tool("a")])
    agent.run_turn("再来一句")
    assert agent.tools_tokens > 1000
    assert agent.context_source() == "estimated"
    assert agent.context_tokens() >= agent.tools_tokens


def test_the_floor_dies_when_the_message_count_jumps(config, ctx):
    """/resume 换了一份长对话:那个实测数属于另一段上下文,不能拿来当底线。"""
    config.context_window = 200000
    agent = _agent(config, ctx, ScriptLLM([AssistantTurn(text="好", usage=_usage(9000))]))
    agent.run_turn("再来一句")
    assert agent.context_source() == "real"
    agent._real_prompt = (len(agent.session.messages) + 400, 9000)   # 跳变
    assert agent.context_source() == "estimated"


def test_no_usage_means_no_fake_measurement(config, ctx):
    """端点不给 usage:照旧估算,不假装量过(这条是"没有来源的数字一律不说"在这里的形状)。"""
    config.context_window = 200000
    agent = _agent(config, ctx, ScriptLLM([AssistantTurn(text="好")]))
    agent.run_turn("再来一句")
    assert agent._real_prompt is None
    assert agent.context_source() == "estimated"


def test_the_real_number_makes_compaction_come_earlier(config, ctx):
    """存在的理由:估算够不到阈值、实测早就过了 —— 没有这层就会撞墙才压。"""
    config.context_window = 2000
    config.compact_at_ratio = 0.5              # 阈值 1000
    llm = ScriptLLM([AssistantTurn(text="第一答", usage=_usage(1500)),
                     AssistantTurn(text="(摘要)"), AssistantTurn(text="第二答")])
    agent = _agent(config, ctx, llm)
    agent.run_turn("第一问")
    assert agent.context_source() == "real"          # 1500 > 估算,已经顶在上面了
    assert agent.context_tokens() == 1500            # 而阈值是 1000 —— 下一次请求前就该压
    agent.run_turn("第二问")
    assert len(llm.calls) == 3, [c[0][0]["content"][:20] for c in llm.calls]
    assert "会话压缩器" in llm.calls[1][0][0]["content"]   # 第二次请求前真的先压了


def test_compaction_invalidates_the_floor_it_just_used(config, ctx):
    """压完消息变少:那个实测数不能再当底线,否则会立刻又"过阈值"反复压。"""
    config.context_window = 2000
    config.compact_at_ratio = 0.5
    llm = ScriptLLM([AssistantTurn(text="第一答", usage=_usage(1500)),
                     AssistantTurn(text="(摘要)"), AssistantTurn(text="第二答")])
    agent = _agent(config, ctx, llm)
    agent.run_turn("第一问")
    agent.run_turn("第二问")
    assert agent.context_source() == "estimated", "压缩之后还拿旧的实测数当底线"


# ------------------------------------------- 工作区 AGENTS.md 注入(F1,v0.9.100)

def _adopted(workspace, monkeypatch, trust_home=None):
    """点一次头(v0.9.127 §四.2「首次采纳点头」)。

    项目级那段从"在场就注入"改成"本机采纳过这一份内容才注入",所以这些用例要先
    采纳。信任账(perms-trust.json)必须指到 tmp:不指的话测试会去读写仓库根那份
    真的 —— 换一台机器结果就不一样,那类针比没测更糟。

    `trust_home` 单独给,是因为一条用例里采纳**两个**工作区时那本账必须是同一本:
    各写一本的话,第二次采纳把第一次的记录换掉了,"A 的规矩没混进 B"就测了个假。
    """
    from minicode import app_setup, permspec
    root = workspace if trust_home is None else trust_home
    monkeypatch.setattr(permspec, "trust_path",
                        lambda: root / "app" / "perms-trust.json")
    assert app_setup.adopt_agents(workspace)


def test_agents_md_injected_when_present(tmp_path, monkeypatch):
    from minicode.app_setup import agents_prompt_section, build_system_prompt
    (tmp_path / "AGENTS.md").write_text("# 我的规矩\n- 回答先给结论\n", encoding="utf-8")
    _adopted(tmp_path, monkeypatch)
    out = agents_prompt_section(tmp_path)
    assert "项目指令" in out and "先给结论" in out
    # 子代理也带(项目指令对派出去干活的那一路同样有效)
    assert "项目指令" in build_system_prompt(tmp_path, for_subagent=True)


def test_agents_md_absent_means_empty_and_dot_name_fallback(tmp_path, monkeypatch):
    from minicode.app_setup import agents_prompt_section
    assert agents_prompt_section(tmp_path) == ""      # 没有文件一个字不加
    (tmp_path / ".agents.md").write_text("备选名字的指令", encoding="utf-8")
    assert agents_prompt_section(tmp_path) == ""      # 在场但没点过头:照样不注入
    _adopted(tmp_path, monkeypatch)
    assert "备选名字" in agents_prompt_section(tmp_path)


def test_agents_md_truncated_with_marker(tmp_path, monkeypatch):
    from minicode.app_setup import AGENTS_CHAR_LIMIT, agents_prompt_section
    (tmp_path / "AGENTS.md").write_text("长" * (AGENTS_CHAR_LIMIT + 500), encoding="utf-8")
    _adopted(tmp_path, monkeypatch)
    out = agents_prompt_section(tmp_path)
    assert "已截断" in out and len(out) < AGENTS_CHAR_LIMIT + 200


# ------------------------------------------------------------ 本轮改动审查(v0.9.101 ①R1)

def _two_writes(config, ctx, tmp_path, second="v2-longer"):
    llm = FakeLLM([
        _write_turn("doc.txt", "v1", "c1"),
        AssistantTurn(text="done1"),
        _write_turn("doc.txt", second, "c2"),
        AssistantTurn(text="done2"),
    ])
    agent = make_agent(config, ctx, llm, FakeUI())
    agent.run_turn("第一版")
    agent.run_turn("第二版")
    return agent


def test_undo_records_lists_newest_first_with_kinds(config, ctx, tmp_path):
    agent = _two_writes(config, ctx, tmp_path)
    rep = agent.undo_records()
    assert rep["ok"] and rep["total"] == 2
    assert [it["path"] for it in rep["items"]] == ["doc.txt", "doc.txt"]
    # 最新在前:第一条是"改"(有前像),第二条是"新建"(前像是 None)——
    # 撤销它俩的动作不一样:恢复 vs 删除,话术必须分开(§四.4)。
    assert [it["kind"] for it in rep["items"]] == ["改", "新建"]
    assert [it["verb"] for it in rep["items"]] == ["恢复", "删除"]
    assert rep["items"][0]["newer"] == 0           # 最新那条之上没有更动的了
    assert rep["items"][1]["newer"] == 1           # 退更早那次会连带跳过更新的改动
    assert rep["items"][0]["seq"] != rep["items"][1]["seq"]


def test_undo_records_says_which_turn_each_change_belongs_to(config, ctx, tmp_path):
    """这本栈是**按会话**攒的,而段标题叫「本轮改动」—— 界面要能自己算出"这一轮"
    是哪几条,否则长会话里那句「本轮」就是骗人的(v0.9.128 补的两个字段)。
    """
    agent = _two_writes(config, ctx, tmp_path)
    rep = agent.undo_records()
    assert rep["turn"] == 2                    # 现在停在第几问
    assert rep["turns"] == 2                   # 这份账跨了几问
    assert [it["turn"] for it in rep["items"]] == [2, 1]
    # 同一问里改两处:分组要落在同一格子里,而不是"每处算一问"
    agent.llm.turns = [
        _write_turn("a.txt", "1", "c3"), _write_turn("b.txt", "2", "c4"),
        AssistantTurn(text="done3")]
    agent.run_turn("第三版")
    rep = agent.undo_records()
    assert rep["turn"] == 3 and rep["turns"] == 3
    assert [it["turn"] for it in rep["items"]] == [3, 3, 2, 1]


def test_undo_by_seq_only_touches_that_one(config, ctx, tmp_path):
    """退第 2 次不吃第 1 次:这是「逐文件回退」与「换个名字的乱序撤销」唯一的区别。"""
    agent = _two_writes(config, ctx, tmp_path, second="v2-longer")
    latest = agent.undo_records()["items"][0]
    assert latest["newer"] == 0
    assert "已撤销" in agent.undo(latest["seq"])
    assert agent.undo_records()["total"] == 1      # 更早的那条原样留着
    assert (tmp_path / "doc.txt").read_text(encoding="utf-8") == "v1"


def test_undo_older_entry_warns_newer_ones_remain(config, ctx, tmp_path):
    """退更早的那一处必须说清"上面还有几处没退"——否则用户以为退到了本轮开始前。"""
    agent = _two_writes(config, ctx, tmp_path, second="v2-longer")
    older = agent.undo_records()["items"][1]
    assert older["newer"] == 1
    msg = agent.undo(older["seq"])
    assert "删除" in msg and "还有 1 处改动没退" in msg
    assert agent.undo_records()["total"] == 1


def test_undo_without_seq_still_pops_latest(config, ctx, tmp_path):
    """「/undo」与「↶ 撤销」的老行为零变化:不传编号就是后进先出退最近一步。"""
    agent = _two_writes(config, ctx, tmp_path, second="v2-longer")
    assert "已撤销" in agent.undo()
    assert (tmp_path / "doc.txt").read_text(encoding="utf-8") == "v1"
    assert agent.undo_records()["total"] == 1


def test_undo_unknown_seq_says_list_is_stale(config, ctx, tmp_path):
    agent = _two_writes(config, ctx, tmp_path)
    msg = agent.undo(9999)
    assert "清单可能已经过期" in msg
    assert agent.undo_records()["total"] == 2      # 没退成就一条都不能少


def test_undo_records_never_show_absolute_path(config, ctx, tmp_path):
    """载荷里只给相对工作区的路径 —— 绝对路径不出桥(与导出面同一口径)。"""
    ag = _two_writes(config, ctx, tmp_path)
    payload = ag.undo_records()
    text = json.dumps(payload, ensure_ascii=False)
    assert str(tmp_path) not in text
    for it in payload["items"]:
        assert not it["path"].startswith(("\\", "/")) and ":" not in it["path"]


def test_undo_records_flag_external_touch(config, ctx, tmp_path):
    """快照之后被 bash / 外部程序改过 → 这一处必须被点名,不许静默按前像 diff。"""
    ag = _two_writes(config, ctx, tmp_path, second="v2")
    (tmp_path / "doc.txt").write_text(
        "v2 之后命令又往里写了一大段,长度也变了", encoding="utf-8")
    item = ag.undo_records()["items"][0]
    assert item["drift"] is True
    assert "还被别的工具动过" in ag.undo(item["seq"])


def test_undo_records_exclude_bash_changes(config, ctx, tmp_path):
    """命令改的东西不进清单 —— 否则「全部回退」就成了一句假承诺(§四.1)。"""
    llm = FakeLLM([
        AssistantTurn(tool_calls=[ToolCall(
            id="c1", name="bash",
            arguments=json.dumps({"command": "echo hi > from_bash.txt"}))]),
        AssistantTurn(text="done"),
    ])
    ag = make_agent(config, ctx, llm, FakeUI())
    ag.run_turn("执行命令")
    rep = ag.undo_records()
    assert rep["total"] == 0
    assert "命令" in rep["note"]


def test_failed_write_leaves_no_rollback_row(config, ctx, tmp_path):
    """没落盘的那一次不进清单:退不动的行比少列一行更容易骗人。"""
    (tmp_path / "f.py").write_text("a = 1\n", encoding="utf-8")
    llm = FakeLLM([
        AssistantTurn(tool_calls=[ToolCall(
            id="c1", name="edit_file",
            arguments=json.dumps({"path": "f.py", "old_string": "压根没有的这一行",
                                  "new_string": "b = 2"}))]),
        AssistantTurn(text="done"),
    ])
    ag = make_agent(config, ctx, llm, FakeUI())
    ag.run_turn("改坏")
    assert ag.undo_records()["total"] == 0
    assert "没有可撤销" in ag.undo()


def test_undo_new_file_says_delete_not_restore(config, ctx, tmp_path):
    llm = FakeLLM([_write_turn("new.txt", "内容", "c1"), AssistantTurn(text="done")])
    ag = make_agent(config, ctx, llm, FakeUI())
    ag.run_turn("新建")
    msg = ag.undo()
    assert "删除" in msg and "恢复" not in msg
    assert not (tmp_path / "new.txt").exists()


def test_undo_all_walks_back_to_start(config, ctx, tmp_path):
    ag = _two_writes(config, ctx, tmp_path, second="v2-longer")
    msg = ag.undo_all()
    assert "已全部回退 2 处" in msg
    assert not (tmp_path / "doc.txt").exists()      # 新建的那处退到最后 = 文件不在
    assert "没有可撤销" in ag.undo()
    assert "命令" in msg                            # 退不掉的那一类要一并说清


# ------------------------------------------------- 分层指令文件(v0.9.101 ④L1/L3)

def _user_layer(monkeypatch, tmp_path, text="用户级 MARKER-USER\n- 回答先给结论\n"):
    """把「本机数据目录」指到 tmp,免得测试碰到真仓库里那份 AGENTS.md。"""
    from minicode import app_setup
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setattr(app_setup, "app_path", lambda *p: home.joinpath(*p))
    if text is not None:
        (home / "AGENTS.md").write_text(text, encoding="utf-8")
    return home


def test_two_layers_ordered_user_then_project(tmp_path, monkeypatch):
    from minicode import app_setup
    _user_layer(monkeypatch, tmp_path)
    (tmp_path / "AGENTS.md").write_text("项目级 MARKER-PROJ\n", encoding="utf-8")
    _adopted(tmp_path, monkeypatch)
    out = app_setup.instructions_prompt_section(tmp_path)
    assert out.index("MARKER-USER") < out.index("MARKER-PROJ")
    assert "工程规矩(用户级" in out and "项目指令(工作区" in out


def test_user_layer_without_project_says_nothing_about_it(tmp_path, monkeypatch):
    """缺的那一层不写"本项目未提供"那种装饰 —— 没读到的东西就不出现在屏上。"""
    from minicode import app_setup
    _user_layer(monkeypatch, tmp_path)
    out = app_setup.instructions_prompt_section(tmp_path)
    assert "MARKER-USER" in out and "项目指令" not in out


def test_no_files_injects_nothing_and_creates_nothing(tmp_path, monkeypatch):
    from minicode import app_setup
    home = _user_layer(monkeypatch, tmp_path, text=None)
    assert app_setup.instructions_prompt_section(tmp_path) == ""
    assert list(home.iterdir()) == []          # 不静默创建模板文件


def test_switching_workspace_switches_project_layer(tmp_path, monkeypatch):
    from minicode import app_setup
    _user_layer(monkeypatch, tmp_path)
    a = tmp_path / "A"
    b = tmp_path / "B"
    a.mkdir()
    b.mkdir()
    (a / "AGENTS.md").write_text("A 的规矩 MARKER-A", encoding="utf-8")
    (b / ".agents.md").write_text("B 的规矩 MARKER-B", encoding="utf-8")
    _adopted(a, monkeypatch, tmp_path)     # 同一本信任账,两个工作区各记一行
    _adopted(b, monkeypatch, tmp_path)
    assert "MARKER-A" in app_setup.instructions_prompt_section(a)
    assert "MARKER-B" in app_setup.instructions_prompt_section(b)
    assert "MARKER-A" not in app_setup.instructions_prompt_section(b)


def test_guard_line_present_in_both_layers(tmp_path, monkeypatch):
    """红线不吃指令文件:两段都必须带那句"改不动它们",且不写"以本文件为准"。"""
    from minicode import app_setup
    _user_layer(monkeypatch, tmp_path)
    (tmp_path / "AGENTS.md").write_text("项目 MARKER", encoding="utf-8")
    _adopted(tmp_path, monkeypatch)
    out = app_setup.instructions_prompt_section(tmp_path)
    assert out.count(app_setup.INSTRUCTION_GUARD) == 2
    assert "改不动它们" in out


def test_layers_report_counts_not_content(tmp_path, monkeypatch):
    from minicode import app_setup
    _user_layer(monkeypatch, tmp_path, text="一\n二\n三\n")
    (tmp_path / "AGENTS.md").write_text("甲\n乙\n", encoding="utf-8")
    layers = app_setup.instruction_layers(tmp_path)
    assert layers[1]["adopted"] is False      # 还没点过头:这一屏要说得出这个差别
    assert app_setup.instructions_line(tmp_path) == \
        "内置 + 用户级(3 行) + 项目级(2 行、未采纳)"
    _adopted(tmp_path, monkeypatch)
    assert [it["scope"] for it in layers] == ["用户级", "项目级"]
    assert layers[0]["lines"] == 3 and layers[1]["lines"] == 2
    assert layers[1]["name"] == "AGENTS.md"
    text = app_setup.instructions_line(tmp_path)
    assert text == "内置 + 用户级(3 行) + 项目级(2 行)"
    assert "一" not in text and "甲" not in text      # 只报数,不重复贴内容


def test_layers_empty_line_is_empty(tmp_path, monkeypatch):
    from minicode import app_setup
    _user_layer(monkeypatch, tmp_path, text=None)
    assert app_setup.instruction_layers(tmp_path) == []
    assert app_setup.instructions_line(tmp_path) == ""


def test_truncation_reports_how_much(tmp_path, monkeypatch):
    from minicode import app_setup
    _user_layer(monkeypatch, tmp_path, text="长" * (app_setup.AGENTS_CHAR_LIMIT + 30))
    out = app_setup.user_agents_prompt_section()
    assert "已截断" in out and "只注入了前 4000 字" in out and "4030 字" in out
    assert app_setup.instruction_layers(tmp_path)[0]["truncated"] is True


def test_subagent_still_gets_project_layer(tmp_path, monkeypatch):
    """继承口径(v0.9.100 已定):派出去的那一路也在同一个工作区干活,项目规矩同样有效。"""
    from minicode.app_setup import build_system_prompt
    (tmp_path / "AGENTS.md").write_text("项目 MARKER", encoding="utf-8")
    _adopted(tmp_path, monkeypatch)
    assert "MARKER" in build_system_prompt(tmp_path, for_subagent=True)


# ------------------------------------------------ 某一处改动的 diff(v0.9.101 ①R2)

def _seq_of(agent, nth=0):
    return agent.undo_records()["items"][nth]["seq"]


def test_diff_compares_preimage_with_disk_now(config, ctx, tmp_path):
    llm = FakeLLM([
        _write_turn("d.txt", "line1\nline2\n", "c1"), AssistantTurn(text="ok"),
        _write_turn("d.txt", "line1\nCHANGED\n", "c2"), AssistantTurn(text="ok")])
    ag = make_agent(config, ctx, llm, FakeUI())
    ag.run_turn("一")
    ag.run_turn("二")
    d = ag.undo_diff(_seq_of(ag, 0))        # 最新那一处:前像 = 第一版
    assert d["ok"] and d["kind"] == "改"
    assert d["path"] == "d.txt"             # 相对路径,绝对路径不出桥
    joined = "\n".join(d["lines"])
    assert "-line2" in joined and "+CHANGED" in joined
    assert d["added"] == 1 and d["removed"] == 1
    assert "最后一次改它之前" in d["note"] or "我改之前" in joined + d["note"]
    assert d["same"] is False


def test_diff_of_new_file_is_all_additions(config, ctx, tmp_path):
    ag = _two_writes(config, ctx, tmp_path, second="v2-longer")
    created = [it for it in ag.undo_records()["items"] if it["kind"] == "新建"][0]
    d = ag.undo_diff(created["seq"])
    assert d["ok"] and d["kind"] == "新建" and d["removed"] == 0


def test_diff_flags_that_others_changed_it_too(config, ctx, tmp_path):
    """快照之后被命令改过 → note 必须说这份 diff 里含别人的改动,不让人拿它对答案。"""
    ag = _two_writes(config, ctx, tmp_path, second="v2")
    (tmp_path / "doc.txt").write_text("命令行又写进去的一段更长内容\n", encoding="utf-8")
    seq = _seq_of(ag, 0)
    d = ag.undo_diff(seq)
    assert d["ok"] and d["drift"] is True and "含别人的改动" in d["note"]


def test_diff_says_identical_when_reverted(config, ctx, tmp_path):
    ag = _two_writes(config, ctx, tmp_path, second="v1")   # 第二次写回同样内容
    d = ag.undo_diff(_seq_of(ag, 0))
    assert d["ok"] and d["same"] is True and d["added"] == 0


def test_diff_on_deleted_file_reports_removals(config, ctx, tmp_path):
    ag = _two_writes(config, ctx, tmp_path, second="v2-longer")
    (tmp_path / "doc.txt").unlink()
    d = ag.undo_diff(_seq_of(ag, 0))
    assert d["ok"] and d["kind"] == "已删除" and d["removed"] >= 1


def test_binary_and_oversize_say_cannot_compute(config, ctx, tmp_path):
    """算不出就说算不出 —— 给一份"看起来没有变化"的空 diff 比承认算不出更坏。"""
    (tmp_path / "b.bin").write_bytes(b"\xff\xfe\x00binary")
    llm = FakeLLM([
        AssistantTurn(tool_calls=[ToolCall(
            id="c1", name="write_file",
            arguments=json.dumps({"path": "b.bin", "content": "text now"}))]),
        AssistantTurn(text="ok")])
    ag = make_agent(config, ctx, llm, FakeUI())
    ag.run_turn("写")
    d = ag.undo_diff(_seq_of(ag, 0))
    assert d["ok"] is False and "UTF-8" in d["msg"]

    big = tmp_path / "big.txt"
    big.write_text("x" * (ag.DIFF_SCAN_BYTES + 10), encoding="utf-8")
    llm2 = FakeLLM([
        AssistantTurn(tool_calls=[ToolCall(
            id="c2", name="write_file",
            arguments=json.dumps({"path": "big.txt", "content": "small now"}))]),
        AssistantTurn(text="ok")])
    ag2 = make_agent(config, ctx, llm2, FakeUI())
    ag2.run_turn("改大的")
    d2 = ag2.undo_diff(_seq_of(ag2, 0))
    assert d2["ok"] is False and "KB" in d2["msg"]


def test_diff_unknown_seq_says_stale(config, ctx, tmp_path):
    ag = _two_writes(config, ctx, tmp_path)
    d = ag.undo_diff(4242)
    assert d["ok"] is False and "过期" in d["msg"]


def test_diff_line_cap_reports_more(config, ctx, tmp_path):
    body = "\n".join(f"line {i}" for i in range(400))
    llm = FakeLLM([
        _write_turn("many.txt", "旧的一份\n", "c1"), AssistantTurn(text="ok"),
        _write_turn("many.txt", body, "c2"), AssistantTurn(text="ok")])
    ag = make_agent(config, ctx, llm, FakeUI())
    ag.run_turn("一")
    ag.run_turn("二")
    d = ag.undo_diff(_seq_of(ag, 0))
    assert d["ok"] and len(d["lines"]) <= ag.DIFF_LINE_LIMIT
    assert d["more"] > 0


# --------------------------- D1 提示词指纹的两个底(v0.9.102 补的坑,有测试才敢算验过)

def _infos(ui):
    return getattr(ui, "infos", [])


def test_new_session_first_turn_never_claims_a_reload(config, ctx):
    """`_prompt_sig` 不立底 = 每条新会话的第一句话就 AttributeError,整个回合崩掉。

    这不是假想的严重度:040e72e 那笔把读它的代码提交了、没提交建它的那一行,
    全量当场红 168 项。第二件事同样重要 —— 第一遍只记账,**不能**冒出一句
    「指令文件有变化」,那是没发生过的事。
    """
    llm = FakeLLM([AssistantTurn(text="一"), AssistantTurn(text="二")])
    ui = FakeUI()
    ag = make_agent(config, ctx, llm, ui)
    assert ag._prompt_sig is None                 # 构造时立了底,不是漏了
    ag.run_turn("你好")
    assert ag._prompt_sig is not None
    assert not [t for t in _infos(ui) if "重新载入" in t or "已跨天" in t]


def test_changing_agents_md_reloads_once_and_says_so(config, ctx, tmp_path, monkeypatch):
    from minicode import app_setup
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(app_setup, "app_path", lambda *p: home.joinpath(*p))
    llm = FakeLLM([AssistantTurn(text="一"), AssistantTurn(text="二"),
                   AssistantTurn(text="三")])
    ui = FakeUI()
    ag = make_agent(config, ctx, llm, ui)
    ag.run_turn("第一句")                          # 这一遍只记指纹
    (tmp_path / "AGENTS.md").write_text("新加的规矩 MARKER-D1", encoding="utf-8")
    _adopted(tmp_path, monkeypatch)                # 不点这一句头就没有这一段
    ag.run_turn("第二句")                          # 内容真变了才重建、才说一声
    said = [t for t in _infos(ui) if "重新载入" in t]
    assert len(said) == 1, _infos(ui)
    assert "MARKER-D1" in ag.session.messages[0]["content"]
    ag.run_turn("第三句")                          # 没再变就不该再吵第二次
    assert len([t for t in _infos(ui) if "重新载入" in t]) == 1


# ---------------------------------------- 编辑重发连着回退工作区(v0.9.103 ①R3)

def test_rollback_reverts_only_what_came_after_that_question(config, ctx, tmp_path):
    llm = FakeLLM([
        _write_turn("a.txt", "A1", "c1"), AssistantTurn(text="一答"),
        _write_turn("b.txt", "B1", "c2"), AssistantTurn(text="二答"),
        _write_turn("a.txt", "A2", "c3"), AssistantTurn(text="三答"),
    ])
    ag = make_agent(config, ctx, llm, FakeUI())
    ag.run_turn("第一问")      # 写 a.txt(新建)
    ag.run_turn("第二问")      # 写 b.txt(新建)
    ag.run_turn("第三问")      # 改 a.txt
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "A2"

    r = ag.rollback_to_ordinal(3)        # 退到第三问之前
    assert r["ok"] and r["reverted"] == ["a.txt"]
    # 退的是"第三问之前"那一版:A1 还在(它是第一问的产物,不属于本次要退的范围)
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "A1"
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "B1"   # 第二问的成果不牵连
    assert ag.undo_records()["total"] == 2      # 退掉的从栈里摘走,没退的原样留着


def test_rollback_reports_that_command_changes_are_not_covered(config, ctx, tmp_path):
    """退完那句必须说"命令改的没被退":否则用户以为整轮回到了起点(§四.1)。"""
    llm = FakeLLM([
        AssistantTurn(tool_calls=[ToolCall(
            id="c1", name="bash",
            arguments=json.dumps({"command": "echo 命令产物 > out.txt"}))]),
        AssistantTurn(text="一答"),
        _write_turn("w.txt", "W1", "c2"), AssistantTurn(text="二答"),
    ])
    ag = make_agent(config, ctx, llm, FakeUI())
    ag.run_turn("第一问")
    ag.run_turn("第二问")
    r = ag.rollback_to_ordinal(2)
    assert r["ok"] and "命令" in r["msg"]
    assert (tmp_path / "out.txt").exists(), "命令写的文件不该被动过(也不在账里)"


def test_rollback_before_any_write_is_an_explicit_noop(config, ctx):
    ag = make_agent(config, ctx, FakeLLM([AssistantTurn(text="在的")]), FakeUI())
    ag.run_turn("你好")
    r = ag.rollback_to_ordinal(1)
    assert r["ok"] and r["reverted"] == [] and "没有我用写文件工具改过的东西" in r["msg"]


def test_rollback_refuses_when_compaction_moved_the_ordinals(config, ctx, tmp_path):
    """压缩会把前面的提问折进摘要,"第 N 问"从此指另一句话。

    号错了退的是**别人那几问的成果**,而且是动手面 —— 所以这里必须拒绝、一个字都不动,
    并把该走的路说清楚(逐处退不需要坐标)。
    """
    config.context_window = 100
    config.compact_at_ratio = 0.01
    llm = FakeLLM([
        AssistantTurn(text="(此前对话的摘要)同学问了选课。"),
        _write_turn("keep.txt", "K1", "c1"), AssistantTurn(text="写好了"),
    ])
    ag = make_agent(config, ctx, llm, FakeUI())
    for i in range(3):
        ag.session.add_user(f"旧问题{i}")
        ag.session.add_assistant(AssistantTurn(text=f"旧回答{i}"))
    ag.run_turn("写个文件")           # 这一问里先压缩、再写文件
    epoch_before = ag._epoch
    assert epoch_before >= 1, "压缩成功就该把纪元推前,否则这道闸形同虚设"
    turn_of_write = ag.undo_records()["items"][0]["turn"]
    ag._epoch += 1                    # 模拟"又压过一次"(纪元与记录不再相同)
    r = ag.rollback_to_ordinal(turn_of_write)
    assert r["ok"] is False and r.get("stale") == 1
    assert (tmp_path / "keep.txt").read_text(encoding="utf-8") == "K1", "拒绝时一个字都不该动"
    assert "本轮改动" in r["msg"]      # 指出那条不需要坐标的路


def test_undo_records_carry_the_turn_coordinate(config, ctx, tmp_path):
    llm = FakeLLM([_write_turn("t.txt", "T1", "c1"), AssistantTurn(text="一答"),
                   _write_turn("t.txt", "T2", "c2"), AssistantTurn(text="二答")])
    ag = make_agent(config, ctx, llm, FakeUI())
    ag.run_turn("第一问")
    ag.run_turn("第二问")
    items = ag.undo_records()["items"]
    assert [it["turn"] for it in items] == [2, 1]     # 最新在前,各属于自己那一问
    assert all(it["stale"] is False for it in items)


def test_rollback_bad_ordinal_is_an_error_not_a_wipe(config, ctx, tmp_path):
    ag = _two_writes(config, ctx, tmp_path)
    for bad in ("", "第一问", 0, -3, True):
        r = ag.rollback_to_ordinal(bad)
        assert r["ok"] is False, bad
    assert ag.undo_records()["total"] == 2        # 参数错了不许退任何东西


# ------------------------------------------------ 安全日志里「是哪道门问的」那一列
# 钉的是"类别由 ask_kind 派生、不从文案猜"这一整类 —— `_ask` 的 docstring 早就写着
# 靠匹配文案的代价:改一个字判定就悄悄失效,而没有任何一条测试会红。

class _NoteUI(FakeUI):
    """真门在有 note 时会用四参数调确认框(第三参数是"为什么做",第四是警示行);
    上面那份 FakeUI 只收三参数,那是 v0.9.115 之前的形状。"""

    def confirm_tool(self, tool, args, reason="", note="", can_remember=False):
        self.confirmed.append((tool.name, dict(args)))
        self.reasons.append(reason)
        self.notes = getattr(self, "notes", [])
        self.notes.append(note)
        return self.confirm_choice


def _captured_gates(monkeypatch):
    from minicode import provenance
    seen = []
    monkeypatch.setattr(provenance, "log_escalation",
                        lambda gate, name, args, note, choice: seen.append(gate))
    return seen


def test_log_gate_uses_the_kind_not_the_words(config, ctx, monkeypatch):
    seen = _captured_gates(monkeypatch)
    agent = make_agent(config, ctx, FakeLLM([]), _NoteUI())
    tool = Tool(name="read_file", description="", parameters={},
                handler=lambda c, **kw: "", read_only=True)
    for kind, expect in [("credential", "credential"), ("external", "escalation"),
                         ("git", "git"), ("project_rule", "project_rule"),
                         ("outside_path", "outside_path"), ("", "escalation")]:
        seen.clear()
        # 文案里**故意不含**「密钥」「凭据」四个字:旧判定会把凭据门记成升级门
        agent._log_security_ask(tool, {}, "这一步要问人", "yes", kind)
        assert seen == [expect], (kind, seen)


def test_git_gate_is_logged_by_its_own_kind_end_to_end(config, tmp_path, monkeypatch):
    """走真 `_execute`:往仓库 `.git/hooks/` 写那一问过去只被记成笼统的 escalation,
    现在按门自己的名字记 —— 事后翻这份日志的人能分清"是哪道门问的、用户答了什么"。"""
    from minicode.sandbox import SandboxPolicy, SandboxState

    seen = _captured_gates(monkeypatch)
    ws = tmp_path / "proj"
    (ws / ".git").mkdir(parents=True)
    llm = FakeLLM([
        AssistantTurn(tool_calls=[ToolCall(
            id="c1", name="write_file",
            arguments=json.dumps({"path": ".git/hooks/pre-commit",
                                  "content": "#!/bin/sh\nexit 0\n"}))]),
        AssistantTurn(text="不写了"),
    ])
    ui = _NoteUI()
    ui.confirm_choice = "no"
    agent = Agent(
        llm=llm, registry=build_default_registry(), session=Session(system_prompt="sys"),
        gate=PermissionGate(config, sandbox=SandboxState(SandboxPolicy(), ws),
                            workspace=ws),
        ui=ui, ctx=ToolContext(workspace=ws, config=config))
    agent.run_turn("给仓库加个提交前检查")
    assert seen == ["git"], seen                  # 旧形状:这一条是 "escalation"
    assert ui.notes and ".git" in ui.notes[0]     # 问人的那句话仍然说得出为什么


# -------------------------------------------------- 每步计时住在"自己那一路"里
# §6 50(b) 并发的前置:`_execute` 以前把毫秒写在 `self` 上,同一条消息里两路一起跑
# 就是后跑的那一路盖掉前一路的实测 —— 表现不是报错,是留档里那些耗时变成对不上人。

def _lane_agent(config, tmp_path, tag="lane_a", delay=0.05):
    import time

    from minicode.permission import PermissionGate
    from minicode.session import Session

    def slow(ctx, **kw):
        time.sleep(delay)
        return tag

    tool = Tool(name="slow_read", description="", read_only=True, handler=slow,
                parameters={"type": "object", "properties": {}})
    ws = tmp_path / tag
    ws.mkdir(exist_ok=True)
    return Agent(llm=FakeLLM([]), registry=[tool], session=Session(system_prompt="s"),
                 gate=PermissionGate(config), ui=FakeUI(),
                 ctx=ToolContext(workspace=ws, config=config))


def _call(i="c1"):
    return ToolCall(id=i, name="slow_read", arguments="{}")


def test_a_per_call_lane_leaves_the_default_one_untouched(config, tmp_path):
    agent = _lane_agent(config, tmp_path)
    lane = {"step_ms": None, "hook_ms": None}
    assert agent._execute(_call(), timing=lane) == "lane_a"
    assert isinstance(lane["step_ms"], int) and lane["step_ms"] >= 40, lane
    assert lane["hook_ms"] is None                 # 没配钩子就不该有这个数
    # 默认那一路一个字都不接 —— 否则并发时"最近一次"会被任意后到的线程改写
    assert agent._step_ms is None and agent._hook_ms is None


def test_two_lanes_do_not_share_one_slot(config, tmp_path):
    a = _lane_agent(config, tmp_path, tag="lane_a")
    lane1, lane2 = {"step_ms": None, "hook_ms": None}, {"step_ms": None, "hook_ms": None}
    assert a._execute(_call("c1"), timing=lane1) == "lane_a"
    assert a._execute(_call("c2"), timing=lane2) == "lane_a"
    assert lane1["step_ms"] >= 40 and lane2["step_ms"] >= 40
    lane2["step_ms"] = 1
    assert lane1["step_ms"] >= 40, "两路共用了同一个格子"


def test_an_early_exit_clears_its_own_lane_not_the_previous_one(config, tmp_path):
    """W10 那条"早退不残留上一步实测"现在要按**每一路**成立:同一 lane 里换一步
    早退(工具不存在),上一步的毫秒必须被清掉,而不是留在里面冒充这一步。"""
    a = _lane_agent(config, tmp_path)
    lane = {"step_ms": None, "hook_ms": None}
    a._execute(_call(), timing=lane)
    assert lane["step_ms"] is not None
    out = a._execute(ToolCall(id="c2", name="没有这只工具", arguments="{}"), timing=lane)
    assert out.startswith("错误: 未找到工具")
    assert lane == {"step_ms": None, "hook_ms": None}, lane


# ------------------------------------------------------------ 可以进线程池那一段
# §6 50(b):并发只能把 `_run_handler` 放进线程池。这两条针钉的是**那一段的边界**——
# 以后谁把 `ui.tool_done` / `gate.check` / `session.add_*` 挪进来,这里当场红,
# 而不是等两路确认框串味、或留档顺序倒了才发现。

def test_the_parallel_seam_touches_nothing_shared(config, tmp_path):
    import threading

    a = _lane_agent(config, tmp_path)
    ui = a.ui
    lane = {"step_ms": None, "hook_ms": None}
    p = a._prepare(_call(), lane)
    assert p.runnable and p.tool is not None
    before_done = len(ui.tool_results)
    before_msgs = len(a.session.messages)
    before_gate = (a.gate.ask_kind, a.gate.project_allow_reason)

    main_thread = threading.current_thread()
    seen = []

    def run():
        seen.append(threading.current_thread() is not main_thread)
        seen.append(a._run_handler(p.tool, p.args))

    t = threading.Thread(target=run)
    t.start()
    t.join()
    ran_elsewhere, (result, step_ms) = seen
    assert ran_elsewhere, "这段没跑在另一条线程上,这条针就没在管并发"
    assert result == "lane_a" and step_ms >= 40
    # 收尾(界面结束、会话留档、门的报告字段)仍归主线程的 `_finish`,这一段不许碰
    assert len(ui.tool_results) == before_done
    assert len(a.session.messages) == before_msgs
    assert (a.gate.ask_kind, a.gate.project_allow_reason) == before_gate
    assert lane["step_ms"] is None      # 毫秒由调用方写进这一路,不在这一段里落


def test_the_three_parts_still_add_up_to_one_step(config, tmp_path):
    """拆开之后必须仍然是「一次调用」:prepare → run → finish 拼起来与 _execute 同值。"""
    a = _lane_agent(config, tmp_path)
    lane1 = {"step_ms": None, "hook_ms": None}
    whole = a._execute(_call("c1"), timing=lane1)
    lane2 = {"step_ms": None, "hook_ms": None}
    p = a._prepare(_call("c2"), lane2)
    result, step_ms = a._run_handler(p.tool, p.args)
    lane2["step_ms"] = step_ms
    assert a._finish(p, result) == whole == "lane_a"
    assert lane1["hook_ms"] is None and lane2["hook_ms"] is None
    assert isinstance(lane1["step_ms"], int) and isinstance(lane2["step_ms"], int)


# ------------------------------------------------------------- 超长输出转存回读
# 对标 Claude 的 MCP 落盘与 ZCode 的 artifactPath。三条不许坏:头部与今天逐字节一致、
# 尾巴**真读得回来**(落在工作区内,不触发越界确认)、存不成就照原样截断不装样子。

def _spill_agent(tmp_path, limit=100, body_len=6000):
    from minicode.config import Config, Provider
    from minicode.permission import PermissionGate
    from minicode.session import Session

    body = "".join(f"第{i}行:语义语料\n" for i in range(body_len // 12))

    def big(ctx, **kw):
        return body

    tool = Tool(name="big_out", description="", read_only=True, handler=big,
                parameters={"type": "object", "properties": {}})
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    cfg = Config(providers=[Provider(id="t", name="T", base_url="https://x/v1",
                                     api_key="k", model="m")],
                 active_provider_id="t", max_output_chars=limit)
    agent = Agent(llm=FakeLLM([]), registry=[tool], session=Session(system_prompt="s"),
                  gate=PermissionGate(cfg, workspace=ws), ui=FakeUI(),
                  ctx=ToolContext(workspace=ws, config=cfg))
    return agent, ws, body, limit


def _big_call():
    return ToolCall(id="c1", name="big_out", arguments="{}")


def test_long_output_spills_the_tail_and_keeps_the_head_verbatim(tmp_path):
    agent, ws, body, limit = _spill_agent(tmp_path)
    out = agent._execute(_big_call())
    assert out.startswith(body[:limit])          # 头部一个字节都不许变
    assert "已存进 tool-outputs/" in out and "不必重跑" in out
    files = list((ws / "tool-outputs").glob("*.txt"))
    assert len(files) == 1, files
    assert files[0].read_text(encoding="utf-8") == body   # 存的是全文,不是又一个截断


def test_the_spilled_file_is_readable_without_a_dialog(tmp_path):
    """落点必须在沙箱内:否则模型读回来要弹框,无头模式下直接判"没人可问"——
    那是"存了但读不到",比不存更坏。用上一轮那道只读预演问一句,不许它说要问。"""
    agent, ws, _body, _limit = _spill_agent(tmp_path)
    out = agent._execute(_big_call())
    rel = out.split("已存进 ", 1)[1].split(",", 1)[0]
    read_tool = next(t for t in build_default_registry() if t.name == "read_file")
    assert agent.gate.would_ask(read_tool, {"path": rel}) is False
    # 而且真读得到内容(走 handler,不是只看路径字符串)
    assert "语义语料" in read_tool.handler(agent.ctx, path=rel)


def test_short_output_writes_nothing_at_all(tmp_path):
    agent, ws, _body, _limit = _spill_agent(tmp_path, limit=100000)
    out = agent._execute(_big_call())
    assert "tool-outputs" not in out
    assert not (ws / "tool-outputs").exists()     # 没超就一个文件都不许多写


def test_unwritable_target_falls_back_to_the_old_wording(tmp_path):
    """`tool-outputs` 那个名字被一个同名**文件**占着 → mkdir 真的失败。
    这时必须退回今天那句原文,而不是在回话里指一条不存在的路。"""
    agent, ws, _body, limit = _spill_agent(tmp_path)
    (ws / "tool-outputs").write_text("占了这个名字", encoding="utf-8")
    out = agent._execute(_big_call())
    assert out.endswith(f"…(输出超过 {limit} 字符,已截断)")
    assert "已存进" not in out
    assert (ws / "tool-outputs").read_text(encoding="utf-8") == "占了这个名字"


def test_spill_file_is_capped_and_says_so(tmp_path):
    agent, ws, _body, _limit = _spill_agent(tmp_path, limit=50, body_len=4000)
    import minicode.agent as am
    cap, old = 3000, am.SPILL_FILE_MAX_CHARS
    am.SPILL_FILE_MAX_CHARS = cap
    try:
        out = agent._execute(_big_call())
    finally:
        am.SPILL_FILE_MAX_CHARS = old
    assert "文件本身也在第" in out and "处截过" in out
    f = list((ws / "tool-outputs").glob("*.txt"))[0]
    assert len(f.read_text(encoding="utf-8")) == cap


def test_public_web_profile_turns_spilling_off(tmp_path):
    """网页版每访客一个 Bridge、工作区却共用一份 —— 转存等于跨访客可见。

    这里只验"那道开关被对外收敛关掉"这件事实(真调 `apply_public_profile`);
    它同时会把注册表收成 PUBLIC_TOOLS,所以不能再拿 `big_out` 去跑第二步 ——
    机制本身由下一支针钉。
    """
    from minicode.session import Session
    from minicode.web_profile import apply_public_profile

    agent, ws, _body, _limit = _spill_agent(tmp_path)
    assert agent.output_spill is True
    apply_public_profile(agent, Session(system_prompt="s"), ws)
    assert agent.output_spill is False
    assert not (ws / "tool-outputs").exists()


def test_spill_off_keeps_the_old_truncation_and_writes_no_file(tmp_path):
    """开关关掉时:回话与今天逐字节一致,而且一个文件都不许多写。"""
    agent, ws, body, limit = _spill_agent(tmp_path)
    agent.output_spill = False
    out = agent._execute(_big_call())
    assert out == body[:limit] + f"\n…(输出超过 {limit} 字符,已截断)"
    assert not (ws / "tool-outputs").exists()


# ---------------------------------------------------------------- 同一回合内并发
# §6 50(b)。针全部用**屏障**:三条 handler 互相等,串行必然超时 ——
# 所以"真的并行了"这件事不靠计时猜,也不靠读源码断言。

def _barrier_agent(tmp_path, barrier, note="ok"):
    from minicode.config import Config, Provider
    from minicode.permission import PermissionGate
    from minicode.session import Session

    def handler(ctx, **kw):
        barrier.wait(timeout=6)              # 撞不到别的线程就抛 BrokenBarrier/Timeout
        return note

    tool = Tool(name="grep", description="", read_only=True, concurrent_safe=True,
                handler=handler,
                parameters={"type": "object", "properties": {}})
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    cfg = Config(providers=[Provider(id="t", name="T", base_url="https://x/v1",
                                    api_key="k", model="m")],
                 active_provider_id="t")
    agent = Agent(llm=FakeLLM([]), registry=[tool], session=Session(system_prompt="s"),
                  gate=PermissionGate(cfg, workspace=ws), ui=FakeUI(),
                  ctx=ToolContext(workspace=ws, config=cfg))
    return agent, tool


def _three_calls():
    return [ToolCall(id=f"c{k}", name="grep", arguments="{}") for k in (1, 2, 3)]


def test_three_safe_calls_really_run_together(tmp_path):
    barrier = threading.Barrier(3)
    agent, _tool = _barrier_agent(tmp_path, barrier)
    agent._dispatch_tool_calls(_three_calls())
    texts = [m for m in agent.session.messages if m["role"] == "tool"]
    assert len(texts) == 3, texts
    assert all(t["content"] == "ok" for t in texts)
    assert [t["tool_call_id"] for t in texts] == ["c1", "c2", "c3"]   # 顺序不变


def test_serial_path_would_break_the_barrier_so_this_proves_the_gate(tmp_path):
    """反面对照:同一批调用,资格被摘掉后就该串行 —— 屏障必须**等不到人**而报错。
    没有这条,上面那条针可能只是"三条一起抛错所以看起来跑完了"。"""
    barrier = threading.Barrier(3)
    agent, tool = _barrier_agent(tmp_path, barrier)
    tool.concurrent_safe = False
    agent._tool_map = {tool.name: tool}
    agent._dispatch_tool_calls(_three_calls())
    texts = [m["content"] for m in agent.session.messages if m["role"] == "tool"]
    assert len(texts) == 3
    assert all("错误" in t or "超时" in t or "BrokenBarrier" in t for t in texts), texts


def test_results_are_archived_in_the_models_order_even_if_they_finish_backwards(tmp_path):
    """完成顺序与给出顺序相反时,留档仍是模型给的顺序 —— 悬空/错配对儿是 400 的来源。"""
    from minicode.session import Session

    order_in, order_out = [], []
    done = threading.Barrier(3)

    def handler(ctx, **kw):
        order_in.append(kw.get("pattern", ""))
        done.wait(timeout=6)
        n = int(kw["pattern"][-1])
        import time as _t
        _t.sleep((3 - n) * 0.05)            # c3 先完,c1 后完
        order_out.append(kw["pattern"])
        return f"r{n}"

    from minicode.config import Config, Provider

    tool = Tool(name="grep", description="", read_only=True, concurrent_safe=True,
                handler=handler, parameters={"type": "object", "properties": {}})
    cfg = Config(providers=[Provider(id="t", name="T", base_url="https://x/v1",
                                     api_key="k", model="m")], active_provider_id="t")
    ws = tmp_path / "ws"
    ws.mkdir()
    agent = Agent(llm=FakeLLM([]), registry=[tool], session=Session(system_prompt="s"),
                  gate=PermissionGate(cfg, workspace=ws), ui=FakeUI(),
                  ctx=ToolContext(workspace=ws, config=cfg))
    calls = [ToolCall(id=f"c{k}", name="grep",
                      arguments=json.dumps({"pattern": f"p{k}"})) for k in (1, 2, 3)]
    agent._dispatch_tool_calls(calls)
    got = [(m["tool_call_id"], m["content"]) for m in agent.session.messages
           if m["role"] == "tool"]
    assert got == [("c1", "r1"), ("c2", "r2"), ("c3", "r3")], got
    assert len(order_in) == 3 and set(order_in) == {"p1", "p2", "p3"}
    assert order_out != ["p1", "p2", "p3"], "完成顺序恰好等于给出顺序,这支针就没在验什么"


def test_a_call_that_would_ask_never_joins_the_batch(tmp_path):
    """资格第四条:门会问的那一步绝不进池 —— 从工作线程弹确认框是不报错的坏法。"""
    from minicode.permission import PermissionGate
    from minicode.sandbox import SandboxPolicy, SandboxState
    from minicode.session import Session

    from minicode.config import Config, Provider

    asked = []

    def handler(ctx, **kw):
        asked.append(threading.current_thread() is threading.main_thread())
        return "读了"

    tool = Tool(name="read_file", description="", read_only=True,
                concurrent_safe=True, handler=handler,
                parameters={"type": "object", "properties": {}})
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "elsewhere.txt"
    outside.write_text("外面", encoding="utf-8")
    cfg = Config(providers=[Provider(id="t", name="T", base_url="https://x/v1",
                                     api_key="k", model="m")], active_provider_id="t")
    gate = PermissionGate(cfg, sandbox=SandboxState(SandboxPolicy(), ws), workspace=ws)
    ui = FakeUI()
    agent = Agent(llm=FakeLLM([]), registry=[tool], session=Session(system_prompt="s"),
                  gate=gate, ui=ui, ctx=ToolContext(workspace=ws, config=cfg))
    inside = ws / "a.txt"
    inside.write_text("里面", encoding="utf-8")
    calls = [ToolCall(id="c1", name="read_file",
                      arguments=json.dumps({"path": "a.txt"})),
             ToolCall(id="c2", name="read_file",
                      arguments=json.dumps({"path": str(outside.resolve())}))]
    assert agent._lane_ok(calls[0]) is True         # 第一条有资格
    assert agent._lane_ok(calls[1]) is False       # 越界那条会问人 → 绝不进池
    agent._dispatch_tool_calls(calls)
    assert len(ui.confirmed) == 1                   # 只问了一次,且在主线程
    assert asked == [True, True], asked   # 都在主线程:一条是单只批次,另一条串行
    got = [m["tool_call_id"] for m in agent.session.messages if m["role"] == "tool"]
    assert got == ["c1", "c2"]                      # 两条都有结果,顺序不变


def test_lane_ok_reasons_line_by_line(tmp_path):
    """四条例外逐条钉:非安全名单 / 参数不是 JSON / 参数不是对象 / 门会问。"""
    barrier = threading.Barrier(1)
    agent, tool = _barrier_agent(tmp_path, barrier)
    good = ToolCall(id="c1", name="grep", arguments="{}")
    assert agent._lane_ok(good) is True
    tool.concurrent_safe = False
    assert agent._lane_ok(good) is False, "名单外的工具不许进池"
    tool.concurrent_safe = True
    assert agent._lane_ok(ToolCall(id="c2", name="grep",
                                   arguments="{坏 json")) is False
    assert agent._lane_ok(ToolCall(id="c3", name="grep",
                                   arguments="[1,2]")) is False
    assert agent._lane_ok(ToolCall(id="c4", name="没有这只",
                                   arguments="{}")) is False


def test_concurrent_safe_is_opt_in_and_only_on_read_only_tools():
    """资格表是白名单:新加工具不会自动进池;而池子里一只只读以外的都不许有。"""
    from minicode.tools.concurrency import CONCURRENT_SAFE
    from minicode.tools.registry import build_default_registry

    tools = build_default_registry()
    named = {t.name: t for t in tools}
    safe = {t.name for t in tools if t.concurrent_safe}
    assert safe == set(CONCURRENT_SAFE) & set(named), "名单与实际标记不一致"
    assert all(named[n].read_only for n in safe), "非只读的工具进了并发名单"
    # 两类"只读但绝不并发"的危险分子必须在名单外:弹框在 handler 里 / 共用 CDP 会话
    assert "ask_user" not in safe and "browser_snapshot" not in safe
    assert "search_knowledge" not in safe     # 检索那一刻要记 kb-hits,两路会错配


# -------------------------------------------------- 项目指令文件的首次采纳点头
# docs/roadmap-分层指令文件.md §四.2⑧ + §七.2。这一件的全部价值在"没点过头就不许
# 说话",所以针要钉三处:没采纳时提示词里**没有**那一段、点了头当轮就**有**、
# 而"按推荐/没答/不采纳"三种都不算点头。

_YES = "采纳,以后这一份不再问"
_NO = "不采纳"


class _NodUI(FakeUI):
    """带提问通道的界面替身:把弹出来的卡记下来,并按测试给的答案回。"""

    def __init__(self, reply):
        super().__init__()
        self.reply = reply
        self.cards = []

    def ask_user(self, questions, note="", label=""):
        self.cards.append((questions, note))
        return self.reply


def _agents_ws(tmp_path, monkeypatch, text="项目 MARKER-NOD\n"):
    """工作区放一份项目指令 + 把本机信任账指到 tmp(不指就读写到仓库根那份真的)。"""
    from minicode import permspec
    (tmp_path / "AGENTS.md").write_text(text, encoding="utf-8")
    monkeypatch.setattr(permspec, "trust_path",
                        lambda: tmp_path / "app" / "perms-trust.json")
    return tmp_path


def test_unadopted_project_file_is_not_in_the_prompt(config, ctx, tmp_path, monkeypatch):
    """没点过头:那一段一个字都不进提示词,而且界面听得见一句"没采纳"。"""
    _agents_ws(tmp_path, monkeypatch)
    ui = FakeUI()                       # 这个形态没有提问通道(无头就是这个样子)
    agent = make_agent(config, ctx, FakeLLM([AssistantTurn(text="好")]), ui)
    agent.run_turn("第一问")
    assert "MARKER-NOD" not in agent.session.messages[0]["content"]
    assert any("没采纳" in t for t in _infos(ui)), _infos(ui)


@pytest.mark.parametrize("reply", [
    {"mode": "unanswered", "reason": "无头模式没有人能回答"},
    {"mode": "recommend", "answers": []},
    {"mode": "answer", "answers": [{"id": 0, "choice": _NO}]},
    {"mode": "answer", "answers": [{"id": 0, "free": "先别照它做"}]},
])
def test_only_an_explicit_yes_adopts(config, ctx, tmp_path, monkeypatch, reply):
    """没答、按推荐、点了「不采纳」、自己打一句都**不算点头**(§四.2 那条硬口径)。"""
    _agents_ws(tmp_path, monkeypatch)
    ui = _NodUI(reply)
    agent = make_agent(config, ctx, FakeLLM([AssistantTurn(text="好")]), ui)
    agent.run_turn("第一问")
    assert "MARKER-NOD" not in agent.session.messages[0]["content"]
    assert len(ui.cards) == 1


def test_the_nod_adopts_and_the_same_turn_already_sees_it(config, ctx, tmp_path,
                                                          monkeypatch):
    """点了头当轮就生效:提示词里有那一段,而且不是"下次开会话才认"。"""
    from minicode import app_setup
    _agents_ws(tmp_path, monkeypatch)
    ui = _NodUI({"mode": "answer", "answers": [{"id": 0, "choice": _YES}]})
    agent = make_agent(config, ctx,
                       FakeLLM([AssistantTurn(text="一"), AssistantTurn(text="二")]), ui)
    agent.run_turn("第一问")
    assert "MARKER-NOD" in agent.session.messages[0]["content"]
    assert app_setup.agents_adoption(tmp_path)["adopted"] is True
    # 第二问:指纹没变、已采纳 —— 不该再弹一张卡,也不该再说一次"重新载入"
    agent.run_turn("第二问")
    assert len(ui.cards) == 1
    assert len([t for t in _infos(ui) if "重新载入" in t]) == 0


def test_editing_the_file_after_a_nod_asks_again(config, ctx, tmp_path, monkeypatch):
    """点头记的是**内容指纹**:文件改一个字,那句"我采纳过"就不 covers 新内容了。"""
    from minicode import app_setup
    _agents_ws(tmp_path, monkeypatch)
    ui = _NodUI({"mode": "answer", "answers": [{"id": 0, "choice": _YES}]})
    agent = make_agent(config, ctx,
                       FakeLLM([AssistantTurn(text="一"), AssistantTurn(text="二")]), ui)
    agent.run_turn("第一问")
    assert app_setup.agents_adoption(tmp_path)["adopted"] is True
    (tmp_path / "AGENTS.md").write_text("项目 MARKER-NEW\n", encoding="utf-8")
    assert app_setup.agents_adoption(tmp_path)["adopted"] is False
    assert "MARKER-NEW" not in agent.session.messages[0]["content"]


def test_a_refused_file_is_not_asked_twice_in_one_session(config, ctx, tmp_path,
                                                          monkeypatch):
    """拒过一次就这一会话不再追问(否则每一问都弹一遍成了骚扰)。"""
    _agents_ws(tmp_path, monkeypatch)
    ui = _NodUI({"mode": "answer", "answers": [{"id": 0, "choice": _NO}]})
    agent = make_agent(config, ctx,
                       FakeLLM([AssistantTurn(text="一"), AssistantTurn(text="二")]), ui)
    agent.run_turn("第一问")
    agent.run_turn("第二问")
    assert len(ui.cards) == 1
    assert len([t for t in _infos(ui) if "没采纳" in t]) == 1


def test_subagents_never_ask(config, ctx, tmp_path, monkeypatch):
    """子代理不弹采纳卡:派出去那一路拿的是任务文本,而提示词那段本来就要求
    "本机采纳过" —— 在这儿问等于把主会话的一次决定重问 N 遍。"""
    from minicode.permission import PermissionGate
    from minicode.session import Session

    _agents_ws(tmp_path, monkeypatch)
    ui = _NodUI({"mode": "answer", "answers": [{"id": 0, "choice": _YES}]})
    sub = Agent(llm=FakeLLM([AssistantTurn(text="子")]), registry=[],
               session=Session(system_prompt="sys"), gate=PermissionGate(config),
               ui=ui, ctx=ctx, is_sub=True)
    sub.run_turn("派个活")
    assert ui.cards == []
    assert "MARKER-NOD" not in sub.session.messages[0]["content"]


def test_the_nod_survives_a_new_session(config, ctx, tmp_path, monkeypatch):
    """采纳是本机的账,不是这一会话的:同一份内容开新会话不再问第二次。"""
    from minicode.app_setup import build_system_prompt
    from minicode.permission import PermissionGate
    from minicode.session import Session

    _agents_ws(tmp_path, monkeypatch)
    ui = _NodUI({"mode": "answer", "answers": [{"id": 0, "choice": _YES}]})
    first = make_agent(config, ctx, FakeLLM([AssistantTurn(text="一")]), ui)
    first.run_turn("第一问")
    assert len(ui.cards) == 1

    ui2 = _NodUI({"mode": "answer", "answers": [{"id": 0, "choice": _NO}]})
    second = Agent(llm=FakeLLM([AssistantTurn(text="二")]),
                   registry=build_default_registry(),
                   session=Session(system_prompt="sys"),
                   gate=PermissionGate(config), ui=ui2, ctx=ctx)
    second.run_turn("换个会话再问")
    assert ui2.cards == []                       # 没再问
    # 新会话的提示词里带着那一段。这里直接问 `build_system_prompt`:真机上它是
    # create_app 拼好的,而测试那个 Session 的开头是一条占位 system,拿它断言等于
    # 断言了夹具。
    assert "MARKER-NOD" in build_system_prompt(tmp_path)


# ------------------------------------------------------------------ 回合中途插话
# §6 6(c):跑着的时候补一句,先进信箱,在**安全边界**读(请求之前、handler 之外)。
# 针全部走"第二次请求真看到了什么" —— 并进会话这件事只有从下一次请求的消息体里
# 才看得出来,断言 session 尾部是看得见的下游,不是这件事本身。

class _StepLLM(FakeLLM):
    """第 n 次请求**返回之后**替界面说一句(on_call 里调 queue_interjection)。

    这等价于"handler 正在跑的时候用户按了 Enter":单线程、可重复,而时序与真机一致。
    另有一条真线程的用例(test_interjection_from_another_thread...)钉跨线程那一面。
    """

    def __init__(self, turns, on_call=None):
        super().__init__(turns)
        self.on_call = on_call

    def stream_chat(self, messages, tools, **kwargs):
        n = len(self.calls) + 1
        turn = super().stream_chat(messages, tools, **kwargs)
        if self.on_call:
            self.on_call(n, turn)
        return turn


def _roles(agent):
    return [m["role"] for m in agent.session.messages]


def test_interjection_is_read_between_the_tool_result_and_the_next_request(config, ctx, tmp_path):
    box = {}

    def on_call(n, turn):
        if n == 1:
            assert box["a"].queue_interjection("只看第三章就好") is True

    llm = _StepLLM([
        AssistantTurn(tool_calls=[ToolCall(
            id="c1", name="write_file",
            arguments=json.dumps({"path": "a.txt", "content": "hi"}))]),
        AssistantTurn(text="收到"),
    ], on_call)
    agent = make_agent(config, ctx, llm, FakeUI())
    box["a"] = agent
    agent.run_turn("创建 a.txt")

    assert _roles(agent) == ["system", "user", "assistant", "tool", "user", "assistant"]
    tail = llm.calls[1][-2:]
    assert tail[0]["role"] == "tool" and tail[1]["role"] == "user"
    assert "第三章" in tail[1]["content"]


def test_interjection_at_the_end_of_a_turn_keeps_that_turn_going(config, ctx, tmp_path):
    """正要收口时补的话算**这一轮**的后续步骤(拍过的口径),不是"下一轮才读到"。

    没有插话时这一轮只发一次请求;发了两次就是那条 continue 真走成了 —— 针钉的是
    第二次请求带着这句用户消息,而不是"多调了一次"这个数。
    """
    box = {}

    def on_call(n, turn):
        if n == 1:
            box["a"].queue_interjection("再把日期也写上")

    llm = _StepLLM([AssistantTurn(text="第一段"), AssistantTurn(text="第二段")], on_call)
    agent = make_agent(config, ctx, llm, FakeUI())
    box["a"] = agent
    agent.run_turn("写个总结")

    assert len(llm.calls) == 2
    assert _roles(agent) == ["system", "user", "assistant", "user", "assistant"]
    assert "再把日期也写上" in llm.calls[1][-1]["content"]
    assert agent.session.messages[-1]["content"] == "第二段"


def test_interjections_keep_the_order_they_were_typed(config, ctx, tmp_path):
    """一个窗口里补两句:先说的先读到,两句各是一条 user 消息(不合成一句)。"""
    box = {}

    def on_call(n, turn):
        if n == 1:
            box["a"].queue_interjection("第一句")
            box["a"].queue_interjection("第二句")

    llm = _StepLLM([
        AssistantTurn(tool_calls=[ToolCall(
            id="c1", name="write_file",
            arguments=json.dumps({"path": "a.txt", "content": "hi"}))]),
        AssistantTurn(text="好了"),
    ], on_call)
    agent = make_agent(config, ctx, llm, FakeUI())
    box["a"] = agent
    agent.run_turn("创建 a.txt")

    users = [m["content"] for m in agent.session.messages
             if m["role"] == "user" and not m["content"].startswith("创建")]
    assert users == ["第一句", "第二句"]
    assert [c["content"] for c in llm.calls[1][-2:]] == ["第一句", "第二句"]


def test_box_is_bounded_and_a_blank_line_does_not_count(config, ctx, tmp_path):
    """上限与空句子:满了要说得出口(调用方据此回话),空格那句是"按错了"不是"要说话"。"""
    from minicode.agent import INBOX_MAX

    agent = make_agent(config, ctx, FakeLLM([]), FakeUI())
    assert agent.queue_interjection("") is False
    assert agent.queue_interjection("   \n ") is False
    assert agent._inbox == []
    for i in range(INBOX_MAX):
        assert agent.queue_interjection(f"第{i}句") is True
    assert agent.queue_interjection("溢出来的一句") is False
    assert len(agent._inbox) == INBOX_MAX
    assert "溢出" not in "".join(agent._inbox)


def test_a_stopped_turn_keeps_the_box_for_the_next_one(config, ctx, tmp_path):
    """按停止不该吃掉用户已经打出去的那句:话留在信箱,下一轮开头读到。

    时机挑在"边界之后"才测得到这件事:第二次请求已经发出去(上一个安全边界读过了
    信箱),这时用户补一句并立刻按停止 —— 于是这句只能等下一轮的边界。
    """
    box = {}
    seen = []

    class Stopped:
        def stream_chat(self, messages, tools, **kwargs):
            seen.append(len(messages))
            box["a"].queue_interjection("别忘了只看第三章")
            raise KeyboardInterrupt

    agent = make_agent(config, ctx, Stopped(), FakeUI())
    box["a"] = agent
    agent.run_turn("写个总结")
    assert len(seen) == 1                       # 被打断在这一轮的第一次请求
    assert agent.ui.interrupted is True
    assert agent._inbox == ["别忘了只看第三章"]      # 没并进会话,也没丢

    agent.llm = _StepLLM([AssistantTurn(text="第二段")])
    agent.run_turn("接着弄")
    assert agent._inbox == []
    assert _roles(agent) == ["system", "user", "user", "user", "assistant"]
    # 补的那句排在"接着弄"**之后**:它是 leftovers,而信箱只在请求前的那个边界被读,
    # 那一轮的提问早进了会话。顺序是这一个边界的副产品,不是另起一套优先级。
    assert [m["content"] for m in agent.session.messages[-3:]] == [
        "接着弄", "别忘了只看第三章", "第二段"]


def test_interjection_from_another_thread_lands_in_the_same_turn(tmp_path):
    """真跨线程:回合卡在 handler 里,界面线程这时收句子。

    上面几条都是单线程换时序 —— 那证不了锁,也证不了"handler 正在跑的时候能收"。
    """
    from minicode.config import Config, Provider
    from minicode.permission import PermissionGate
    from minicode.session import Session

    inside, release = threading.Event(), threading.Event()

    def handler(ctx, **kw):
        inside.set()
        release.wait(timeout=8)
        return "ok"

    tool = Tool(name="grep", description="", read_only=True, handler=handler,
                parameters={"type": "object", "properties": {}})
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    cfg = Config(providers=[Provider(id="t", name="T", base_url="https://x/v1",
                                    api_key="k", model="m")],
                 active_provider_id="t")
    agent = Agent(llm=_StepLLM([
        AssistantTurn(tool_calls=[ToolCall(id="c1", name="grep", arguments="{}")]),
        AssistantTurn(text="好"),
    ]), registry=[tool], session=Session(system_prompt="s"),
        gate=PermissionGate(cfg, workspace=ws), ui=FakeUI(),
        ctx=ToolContext(workspace=ws, config=cfg))

    t = threading.Thread(target=agent.run_turn, args=("先跑这一轮",), daemon=True)
    t.start()
    assert inside.wait(timeout=6), "handler 没跑起来,这条用例没测到任何东西"
    assert agent.queue_interjection("换成只看第三章") is True
    release.set()
    t.join(timeout=10)
    assert not t.is_alive()
    assert _roles(agent) == ["system", "user", "assistant", "tool", "user", "assistant"]
