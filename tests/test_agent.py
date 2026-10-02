import json

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

    def stream_chat(self, messages, tools, on_text=None, on_reasoning=None, on_retry=None):
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

    def confirm_tool(self, tool, args, reason=""):
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
                    on_retry=None):
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


# ------------------------------------------- 后台命令状态搭在请求上(v0.9.85)

class _StubJob:
    """喂 `_messages_for_request` 用的假任务:已结束、还没播报过。"""

    def __init__(self, jid: str = "j9"):
        self.id = jid
        self.running = False
        self.announced = False

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

def test_agents_md_injected_when_present(tmp_path):
    from minicode.app_setup import agents_prompt_section, build_system_prompt
    (tmp_path / "AGENTS.md").write_text("# 我的规矩\n- 回答先给结论\n", encoding="utf-8")
    out = agents_prompt_section(tmp_path)
    assert "项目指令" in out and "先给结论" in out
    # 子代理也带(项目指令对派出去干活的那一路同样有效)
    assert "项目指令" in build_system_prompt(tmp_path, for_subagent=True)


def test_agents_md_absent_means_empty_and_dot_name_fallback(tmp_path):
    from minicode.app_setup import agents_prompt_section
    assert agents_prompt_section(tmp_path) == ""      # 没有文件一个字不加
    (tmp_path / ".agents.md").write_text("备选名字的指令", encoding="utf-8")
    assert "备选名字" in agents_prompt_section(tmp_path)


def test_agents_md_truncated_with_marker(tmp_path):
    from minicode.app_setup import AGENTS_CHAR_LIMIT, agents_prompt_section
    (tmp_path / "AGENTS.md").write_text("长" * (AGENTS_CHAR_LIMIT + 500), encoding="utf-8")
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
    assert "MARKER-A" in app_setup.instructions_prompt_section(a)
    assert "MARKER-B" in app_setup.instructions_prompt_section(b)
    assert "MARKER-A" not in app_setup.instructions_prompt_section(b)


def test_guard_line_present_in_both_layers(tmp_path, monkeypatch):
    """红线不吃指令文件:两段都必须带那句"改不动它们",且不写"以本文件为准"。"""
    from minicode import app_setup
    _user_layer(monkeypatch, tmp_path)
    (tmp_path / "AGENTS.md").write_text("项目 MARKER", encoding="utf-8")
    out = app_setup.instructions_prompt_section(tmp_path)
    assert out.count(app_setup.INSTRUCTION_GUARD) == 2
    assert "改不动它们" in out


def test_layers_report_counts_not_content(tmp_path, monkeypatch):
    from minicode import app_setup
    _user_layer(monkeypatch, tmp_path, text="一\n二\n三\n")
    (tmp_path / "AGENTS.md").write_text("甲\n乙\n", encoding="utf-8")
    layers = app_setup.instruction_layers(tmp_path)
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


def test_subagent_still_gets_project_layer(tmp_path):
    """继承口径(v0.9.100 已定):派出去的那一路也在同一个工作区干活,项目规矩同样有效。"""
    from minicode.app_setup import build_system_prompt
    (tmp_path / "AGENTS.md").write_text("项目 MARKER", encoding="utf-8")
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
    ag.run_turn("第二句")                          # 内容真变了才重建、才说一声
    said = [t for t in _infos(ui) if "重新载入" in t]
    assert len(said) == 1, _infos(ui)
    assert "MARKER-D1" in ag.session.messages[0]["content"]
    ag.run_turn("第三句")                          # 没再变就不该再吵第二次
    assert len([t for t in _infos(ui) if "重新载入" in t]) == 1
