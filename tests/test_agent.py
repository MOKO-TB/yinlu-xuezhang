import json

from minicode.agent import Agent
from minicode.llm import AssistantTurn, ToolCall
from minicode.permission import PermissionGate
from minicode.session import Session
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
        self.tool_results = []
        self.hard_rejected = []
        self.interrupted = False
        self.errors = []

    def on_text_delta(self, t): pass

    def on_reasoning_delta(self, t): pass

    def on_retry(self, e, n): pass

    def finish_stream(self): pass

    def confirm_tool(self, tool, args):
        self.confirmed.append((tool.name, dict(args)))
        return self.confirm_choice

    def tool_start(self, tool, args): pass

    def tool_done(self, tool, result):
        self.tool_results.append(result)

    def tool_rejected_hard(self, tool, args):
        self.hard_rejected.append((tool.name, dict(args)))

    def compacted(self, before, after): pass

    def info(self, text): pass

    def turn_interrupted(self):
        self.interrupted = True

    def llm_error(self, e):
        self.errors.append(e)

    def max_steps_reached(self, n): pass


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
