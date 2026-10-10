"""`--output-format stream-json`:无头事件流的形状、顺序与结局。

读者是"把学长当子进程驱动的编排层"(CI、外层 agent),不是盯着日志看的人。
所以这一族钉的是**过程可判**:每行都能 `json.loads`、`seq` 严格递增、第一行
`stream_start`、最后一行 `result`;人话仍旧只走 stderr。
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from minicode import headless
from minicode.agent import Agent
from minicode.config import Config, Provider
from minicode.headless import HeadlessUI
from minicode.llm import AssistantTurn
from minicode.permission import ALLOW, ASK
from minicode.tools.base import Tool, ToolContext

sys.path.insert(0, str(Path(__file__).parent))
import main as cli          # noqa: E402
from test_headless import _AskGate, _Inner, _LiveSession, _call   # noqa: E402


class _Seg:
    """一次请求:先按顺序吐几段正文,再回那一份 turn。"""

    def __init__(self, segments, turn):
        self.segments = segments
        self.turn = turn


class _SegLLM:
    def __init__(self, steps):
        self.steps = list(steps)

    def stream_chat(self, *a, **k):
        step = self.steps.pop(0)
        on_text = k.get("on_text")
        for seg in step.segments:
            if on_text:
                on_text(seg)
        return step.turn


def _mk_agent(tmp_path, ui, gate, llm, handler=None, read_only=False):
    def write_tool(ctx, path="a.py", content=""):
        if handler:
            handler(ctx, path=path, content=content)
        return "已创建文件 a.py(1 行)。"

    cfg = Config(providers=[Provider(id="t", name="T", base_url="https://x/v1",
                                     api_key="k", model="m")],
                 active_provider_id="t", permission_mode="confirm")
    agent = Agent(llm=llm,
                  registry=[Tool(name="write_file", description="",
                                 read_only=read_only, handler=write_tool,
                                 parameters={"type": "object", "properties": {
                                     "path": {"type": "string"},
                                     "content": {"type": "string"}}})],
                  session=_LiveSession(), gate=gate, ui=ui,
                  ctx=ToolContext(workspace=tmp_path, config=cfg), max_steps=3)
    return agent, cfg


def _events(cap_out):
    return [json.loads(line) for line in cap_out.splitlines() if line.strip()]


# ------------------------------------------------------------------ 形状与顺序

def test_every_line_is_ndjson_with_stream_start_and_result_last(tmp_path, capsys):
    ui = HeadlessUI(_Inner(), events=headless.EventStream())
    llm = _SegLLM([_Seg(["好", "。"], AssistantTurn(text="好。"))])
    agent, cfg = _mk_agent(tmp_path, ui, _AskGate(ALLOW), llm)
    with pytest.raises(SystemExit) as out:
        cli.run_json(cfg, agent, agent.session, ui, "说一句", fmt="stream-json")
    assert out.value.code == headless.EXIT_OK
    lines = _events(capsys.readouterr().out)
    assert lines[0]["type"] == "stream_start"
    assert lines[0]["task"] == "说一句" and lines[0]["format"] == "stream-json"
    assert lines[-1]["type"] == "result"
    assert lines[-1]["response"] == "好。"
    assert [e["seq"] for e in lines] == list(range(1, len(lines) + 1))


def test_text_deltas_reassemble_into_the_response(tmp_path, capsys):
    ui = HeadlessUI(_Inner(), events=headless.EventStream())
    llm = _SegLLM([_Seg(["第一段", "第二段", "第三段"],
                        AssistantTurn(text="第一段第二段第三段"))])
    agent, cfg = _mk_agent(tmp_path, ui, _AskGate(ALLOW), llm)
    with pytest.raises(SystemExit):
        cli.run_json(cfg, agent, agent.session, ui, "说", fmt="stream-json")
    lines = _events(capsys.readouterr().out)
    deltas = [e["text"] for e in lines if e["type"] == "text_delta"]
    assert "".join(deltas) == lines[-1]["response"]


def test_tool_events_line_up_with_the_result_rows(tmp_path, capsys):
    ui = HeadlessUI(_Inner(), events=headless.EventStream())
    llm = _SegLLM([_Seg([], AssistantTurn(tool_calls=[_call()])),
                   _Seg([], AssistantTurn(text="建好了。"))])
    agent, cfg = _mk_agent(tmp_path, ui, _AskGate(ALLOW), llm,
                           handler=lambda ctx, **kw: None)
    with pytest.raises(SystemExit):
        cli.run_json(cfg, agent, agent.session, ui, "写个文件", fmt="stream-json")
    lines = _events(capsys.readouterr().out)
    starts = [e for e in lines if e["type"] == "tool_start"]
    dones = [e for e in lines if e["type"] == "tool_done"]
    assert [e["name"] for e in starts] == ["write_file"]
    assert [e["name"] for e in dones] == ["write_file"]
    assert starts[0]["summary"] and "trust" in starts[0]
    assert dones[0]["ok"] is True and dones[0]["error_preview"] == ""
    assert lines[-1]["tool_calls"] == [
        {"name": "write_file", "ok": True, "chars": 16}]


def test_a_refused_confirmation_leaves_no_event_and_exits_3(tmp_path, capsys):
    """没人点头不该在 stdout 上留下一张"确认卡"事件 —— 过程里根本没有过点头。"""
    ui = HeadlessUI(_Inner(), events=headless.EventStream())
    llm = _SegLLM([_Seg([], AssistantTurn(tool_calls=[_call()])),
                   _Seg([], AssistantTurn(text="没点头,没写。"))])
    agent, cfg = _mk_agent(tmp_path, ui, _AskGate(ASK), llm,
                           handler=lambda ctx, **kw: None)
    with pytest.raises(SystemExit) as out:
        cli.run_json(cfg, agent, agent.session, ui, "写个文件", fmt="stream-json")
    assert out.value.code == headless.EXIT_ASK_REFUSED
    lines = _events(capsys.readouterr().out)
    assert not [e for e in lines if e["type"] in ("confirm", "ask", "ask_request")]
    assert lines[-1]["asks_refused"][0]["name"] == "write_file"
    assert lines[-1]["exit_code"] == headless.EXIT_ASK_REFUSED


# ------------------------------------------------------------- 人话与旗标

def test_human_notes_stay_on_stderr(capsys):
    ui = HeadlessUI(_Inner(), events=headless.EventStream())
    ui.push("info", text="一句只该给人看的话")
    ui.info("另一句诊断")
    cap = capsys.readouterr()
    assert cap.out == "", "stdout 只能留给 NDJSON"
    assert "一句只该给人看的话" in cap.err and "另一句诊断" in cap.err


@pytest.mark.parametrize("argv", [
    ["--json", "--output-format", "stream-json", "任务"],
    ["--output-format", "stream-json", "--json", "任务"],
    ["--json", "--output-format=stream-json", "任务"],
])
def test_json_and_stream_json_are_mutually_exclusive(argv):
    with pytest.raises(headless.UsageError) as e:
        headless.pop_output_flags(argv)
    assert "stream-json" in str(e.value)


def test_stream_json_is_accepted_and_a_typo_is_still_refused():
    assert headless.pop_output_flags(
        ["--output-format", "stream-json", "任务"]) == ("stream-json", ["任务"])
    with pytest.raises(headless.UsageError):
        headless.pop_output_flags(["--output-format", "jsonl", "任务"])


# ------------------------------------------------------------ schema 与写失败

def test_schema_with_stream_json_keeps_the_stream_shape(tmp_path, capsys):
    from test_output_schema import SCHEMA
    ui = HeadlessUI(_Inner(), events=headless.EventStream())
    llm = _SegLLM([_Seg([], AssistantTurn(text='{"verdict":"pass","score":90}'))])
    agent, cfg = _mk_agent(tmp_path, ui, _AskGate(ALLOW), llm)
    with pytest.raises(SystemExit) as out:
        cli.run_json(cfg, agent, agent.session, ui, "判一下",
                     schema=SCHEMA, fmt="stream-json")
    assert out.value.code == headless.EXIT_OK
    lines = _events(capsys.readouterr().out)
    assert lines[0]["format"] == "stream-json"      # schema 没把它强制改成 json
    assert lines[-1]["structured_output"] == {"verdict": "pass", "score": 90}


def test_a_violating_answer_still_exits_6_in_the_stream(tmp_path, capsys):
    from test_output_schema import SCHEMA
    ui = HeadlessUI(_Inner(), events=headless.EventStream())
    llm = _SegLLM([_Seg([], AssistantTurn(text='{"verdict":"maybe"}'))])
    agent, cfg = _mk_agent(tmp_path, ui, _AskGate(ALLOW), llm)
    with pytest.raises(SystemExit) as out:
        cli.run_json(cfg, agent, agent.session, ui, "判一下",
                     schema=SCHEMA, fmt="stream-json")
    assert out.value.code == headless.EXIT_SCHEMA_VIOLATION
    lines = _events(capsys.readouterr().out)
    assert lines[-1]["schema_violations"]


class _BrokenOut:
    def write(self, _s):
        raise BrokenPipeError()

    def flush(self):
        pass


def test_a_broken_pipe_stops_emitting_and_exits_5(tmp_path, capsys):
    events = headless.EventStream(stream=_BrokenOut())
    ui = HeadlessUI(_Inner(), events=events)
    llm = _SegLLM([_Seg(["正文"], AssistantTurn(text="正文"))])
    agent, cfg = _mk_agent(tmp_path, ui, _AskGate(ALLOW), llm)
    with pytest.raises(SystemExit) as out:
        cli.run_json(cfg, agent, agent.session, ui, "说", fmt="stream-json")
    assert out.value.code == headless.EXIT_INTERRUPTED
    assert events.broken is True
    assert capsys.readouterr().out == "", "管道关了之后一个字节都不许再往 stdout 落"


def test_event_stream_numbers_lines_and_stops_after_a_write_failure():
    buf = io.StringIO()
    es = headless.EventStream(stream=buf)
    es.emit("a", x=1)
    es.emit("b")
    rows = [json.loads(line) for line in buf.getvalue().splitlines()]
    assert [r["seq"] for r in rows] == [1, 2]
    assert [r["type"] for r in rows] == ["a", "b"]
    es.broken = True
    es.emit("c")
    assert len(buf.getvalue().splitlines()) == 2
