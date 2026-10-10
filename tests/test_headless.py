"""无头模式(`--json`):参数解析、静默 UI、退出码与那份 JSON。

这一族测试的共同前提只有一句话:**stdout 必须能被 `json.loads`**。
所以凡是"往 stdout 打字"的路径都在这里当场验,不靠人眼看一遍。
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
from minicode.llm import AssistantTurn, ToolCall
from minicode.permission import ALLOW, ASK
from minicode.tools.base import Tool, ToolContext

sys.path.insert(0, str(Path(__file__).parent))
import main as cli          # noqa: E402  —— 一次性模式的入口在这里,不是 minicode 里


# ------------------------------------------------------------------ 参数解析

def test_json_flag_is_popped_and_the_task_survives():
    assert headless.pop_output_flags(["--json", "写一份请假条"]) == (
        "json", ["写一份请假条"])


def test_explicit_format_beats_the_shorthand():
    # 两个都给时谁赢必须写死:显式的比简写的说得清楚。
    assert headless.pop_output_flags(
        ["--json", "--output-format", "text", "任务"]) == ("text", ["任务"])


def test_equals_form_and_missing_value():
    assert headless.pop_output_flags(["--output-format=json"]) == ("json", [])
    with pytest.raises(headless.UsageError):
        headless.pop_output_flags(["--output-format"])


def test_unknown_format_is_an_error_not_a_silent_fallback():
    # 拼错了却以为自己解析成功 —— 这类旗标最贵的一种坏法,当场拒。
    with pytest.raises(headless.UsageError) as e:
        headless.pop_output_flags(["--output-format", "jsonl", "任务"])
    assert "jsonl" in str(e.value)


def test_flags_never_leak_into_the_task():
    """`unmanagle_slash_command` 是把 argv 整个 join 起来当提问的。

    旗标没摘干净就会变成任务正文里的 `--json` 三个字,模型收到的是
    「--json 帮我改代码」——它不会报错,只会照着一个怪词答题。
    """
    fmt, rest = headless.pop_output_flags(["--json", "帮我改代码"])
    assert fmt == "json"
    assert cli.unmanagle_slash_command(rest) == "帮我改代码"


# ---------------------------------------------------------------- 静默那半边

class _Inner:
    """内层真 UI 的替身:只用来验"没想到的方法转得出去"。"""

    def __init__(self):
        self.delegated = []

    def push(self, kind, **kw):
        self.delegated.append(kind)


def _tool(name="write_file", read_only=False):
    return Tool(name=name, description="", read_only=read_only,
                handler=lambda ctx, **kw: "ok",
                parameters={"type": "object", "properties": {}})


def test_diagnoses_go_to_stderr_never_stdout(capsys):
    ui = HeadlessUI(_Inner())
    ui.info("横幅那种话")
    ui.error("馆藏库有点问题")
    ui.on_text_delta("模型的正文")
    cap = capsys.readouterr()
    assert cap.out == "", "stdout 只能留给那一份 JSON"
    assert "馆藏库有点问题" in cap.err
    assert "模型的正文" not in cap.out


def test_confirmations_are_refused_and_recorded():
    ui = HeadlessUI(_Inner())
    assert ui.confirm_tool(_tool(), {"path": "a.py"}, "要写文件") == "no"
    assert ui.confirm_destructive("要退 3 个文件") is False
    assert [r["name"] for r in ui.asks_refused] == ["write_file", "(destructive)"]


def test_ask_user_reports_unanswered_instead_of_guessing():
    """「没人按就自动替他选推荐项」是这里最想要也最不能给的便利。"""
    from minicode import asking
    ui = HeadlessUI(_Inner())
    out = ui.ask_user([{"id": 0, "q": "要不要覆盖?", "options": [{"label": "要"}]}])
    assert out["mode"] == asking.MODE_UNANSWERED
    assert out["answers"] == []
    assert ui.asks_refused[0]["reason"] == "要不要覆盖?"


def test_tool_rows_judge_success_through_the_single_truth():
    ui = HeadlessUI(_Inner())
    t = _tool("grep", read_only=True)
    ui.tool_start(t, {"pattern": "x"})
    ui.tool_done(t, "3 处命中")
    ui.tool_start(t, {"pattern": "y"})
    ui.tool_done(t, "错误: 没找到")
    assert [r["ok"] for r in ui.tool_calls] == [True, False]
    assert ui.tool_calls[0]["name"] == "grep"


def test_args_are_summarized_without_the_body():
    """无头输出常被贴进 CI 日志:正文里可能是同学的课件原文或凭据。"""
    brief = headless._brief({"path": "a.py", "content": "SECRET" * 200})
    assert brief["path"] == "a.py"
    assert brief["content_chars"] == 1200
    assert "SECRET" not in json.dumps(brief, ensure_ascii=False)


def test_long_scalar_args_are_clipped():
    brief = headless._brief({"command": "ls " + "x" * 500})
    assert len(brief["command"]) == 160


def test_unknown_attributes_delegate_to_the_real_ui():
    ui = HeadlessUI(_Inner())
    ui.push("session_metrics", session="x")
    assert ui.inner.delegated == ["session_metrics"]


def test_hard_reject_is_not_an_ask():
    """红线挡的与"没人点头"是两件事,混在一起说就会把红线说成用户的决定。"""
    ui = HeadlessUI(_Inner())
    ui.tool_rejected_hard(_tool("bash"), {"command": "rm -rf C:\\"})
    assert ui.hard_rejected == ["bash"]
    assert ui.asks_refused == []


# ---------------------------------------------------------- 结局、退出、那份 JSON

def _endings():
    """五种「这一轮怎么结束的」一律经**回调**置起。

    原来这里写的是 `setattr(u, "llm_error", "…")`,而它正是那个崩溃漏网的原因:
    字段与同名方法打架时 setattr 照样绿,agent 调的那个却已经被实例属性盖掉了。
    经回调置状态 = 每根针都真的把 agent 那句 `self.ui.X(...)` 跑了一遍。
    """
    return [
        (lambda u: None, headless.EXIT_OK, "ok"),
        (lambda u: u.confirm_tool(_tool(), {"path": "a.py"}, "要写文件"),
         headless.EXIT_ASK_REFUSED, "ask_refused"),
        (lambda u: u.max_steps_reached(40), headless.EXIT_LIMITED, "limited"),
        (lambda u: u.daily_budget_reached(1000, 1000),
         headless.EXIT_LIMITED, "limited"),
        (lambda u: u.task_budget_reached(500, 500),
         headless.EXIT_LIMITED, "limited"),
        (lambda u: u.llm_error(RuntimeError("APITimeoutError: 连接超时")),
         headless.EXIT_RUNTIME, "llm_error"),
        (lambda u: u.turn_interrupted(), headless.EXIT_INTERRUPTED, "interrupted"),
    ]


@pytest.mark.parametrize("fire, want", [(f, w) for f, w, _ in _endings()])
def test_exit_code_for_each_ending(fire, want):
    ui = HeadlessUI(_Inner())
    fire(ui)
    assert headless.exit_code_for(ui) == want


def test_exit_code_priority_when_everything_fires_at_once():
    """全中一遍,验的是"谁赢",不是"谁先发生"。"""
    ui = HeadlessUI(_Inner())
    for fire, _want, _status in _endings()[1:]:
        fire(ui)
    assert headless.exit_code_for(ui) == headless.EXIT_INTERRUPTED


@pytest.mark.parametrize("fire, _code, want", _endings())
def test_status_names_the_same_ending(fire, _code, want):
    ui = HeadlessUI(_Inner())
    fire(ui)
    assert headless.status_for(ui) == want


def test_a_model_failure_ends_the_run_instead_of_killing_it(config, ctx):
    """回归针:模型报错时 `--json` 要的是「退出码 2 + 那一份 JSON」,不是 traceback。

    `HeadlessUI` 曾把记录用的字段起名 `llm_error`,与 agent 要调的那个回调同名 ——
    实例属性优先于类方法,于是 `self.ui.llm_error(e)` 成了 "'str' object is not
    callable",异常处理器自己抛出去:进程 rc=1、stdout 空、JSON 里那句 error 永远没有。
    这条从真 `Agent.run_turn` 走进真 `HeadlessUI`,任何"回调被字段盖掉"的改法都会在这里响。
    """
    from test_agent import FakeLLM, make_agent

    llm = FakeLLM([RuntimeError("网关 502")])
    ui = HeadlessUI(_Inner())
    agent = make_agent(config, ctx, llm, ui)
    agent.run_turn("说一句你好")                      # 不该抛
    assert ui.llm_failure.startswith("RuntimeError: 网关 502")

    cfg = Config(providers=[Provider(id="t", name="T", base_url="https://x/v1",
                                     api_key="k", model="m")], active_provider_id="t")
    result = json.loads(headless.render(headless.build_result(
        ui, agent, agent.session, cfg, task="说一句你好", elapsed_ms=5, fmt="json")))
    assert result["exit_code"] == headless.EXIT_RUNTIME
    assert result["status"] == "llm_error" and result["ok"] is False
    assert result["error"] == "RuntimeError: 网关 502"


def test_no_ui_callback_is_shadowed_by_a_record_field():
    """把这一整类而不是那一个名字钉住:agent 调的每个回调,在 HeadlessUI 上必须仍可调。

    这里从 agent 的源码里取名字,不手写清单 —— 手写的那份会和 agent 新加的回调脱钩,
    而脱钩之后这道闸就只会在旧名字上响。
    """
    import re
    src = (Path(__file__).resolve().parent.parent / "minicode" / "agent.py").read_text(
        encoding="utf-8")
    called = set(re.findall(r"self\.ui\.([a-z_]+)\(", src))
    assert called, "没从 agent 里认出任何 ui 回调,这条闸本身失灵了"
    ui = HeadlessUI(_Inner())
    bad = sorted(n for n in called if not callable(getattr(ui, n, None)))
    assert not bad, f"这些回调被同名字段盖住了:{bad}"


def test_result_is_parseable_and_keeps_chinese():
    ui = HeadlessUI(_Inner())
    cfg = Config(providers=[Provider(id="t", name="T", base_url="https://x/v1",
                                     api_key="k", model="m")], active_provider_id="t")
    result = json.loads(headless.render(headless.build_result(
        ui, Agent.__new__(Agent), _Session(), cfg, task="帮我改代码",
        elapsed_ms=12, fmt="json")))
    raw = headless.render(headless.build_result(
        ui, Agent.__new__(Agent), _Session(), cfg, task="帮我改代码",
        elapsed_ms=12, fmt="json"))
    assert result["ok"] is True and result["exit_code"] == 0
    assert "\\u5e2e" not in raw, "中文被转义后机器照样能解,人不能 —— 最难查的那种没问题"


class _Session:
    messages = [{"role": "assistant", "content": "改完了,一共三处。"}]
    log_path = Path("sessions/a.jsonl")


def test_response_is_the_last_assistant_message():
    ui = HeadlessUI(_Inner())
    cfg = Config(providers=[Provider(id="t", name="T", base_url="https://x/v1",
                                     api_key="k", model="m")], active_provider_id="t")
    result = headless.build_result(ui, Agent.__new__(Agent), _Session(), cfg,
                                   task="t", elapsed_ms=1, fmt="json")
    assert result["response"] == "改完了,一共三处。"
    assert result["session_file"].endswith("a.jsonl")


def test_a_session_that_never_answered_reports_empty_not_missing():
    ui = HeadlessUI(_Inner())
    cfg = Config(providers=[Provider(id="t", name="T", base_url="https://x/v1",
                                     api_key="k", model="m")], active_provider_id="t")

    class _Empty:
        messages = [{"role": "user", "content": "hi"}]
        log_path = None

    result = headless.build_result(ui, Agent.__new__(Agent), _Empty(), cfg,
                                   task="t", elapsed_ms=1, fmt="json")
    assert result["response"] == "" and result["session_file"] is None


# ---------------------------------------------------------------- 真跑那一路

class _AskGate:
    """每次都开 ASK 的门 —— 无头模式最要命的那条路正是"该问人却没人可问"。"""

    ask_note = ""
    ask_kind = ""
    project_allow_reason = ""

    def __init__(self, decision):
        self.decision = decision
        self.approvals = []

    def new_turn(self):
        pass

    def check(self, tool, args):
        return self.decision

    def approve_for_turn(self, name):
        self.approvals.append(name)

    def approve_outside(self, tool, args, session=False):
        pass


def _agent(tmp_path, ui, gate, steps, handler):
    def write_tool(ctx, path="a.py", content=""):
        handler(ctx, path=path, content=content)
        return "已创建文件 a.py(1 行)。"

    cfg = Config(providers=[Provider(id="t", name="T", base_url="https://x/v1",
                                     api_key="k", model="m")],
                 active_provider_id="t", permission_mode="confirm")
    agent = Agent(llm=_Steps(*steps),
                  registry=[Tool(name="write_file", description="", read_only=False,
                                 handler=write_tool,
                                 parameters={"type": "object", "properties": {
                                     "path": {"type": "string"},
                                     "content": {"type": "string"}}})],
                  session=_LiveSession(), gate=gate, ui=ui,
                  ctx=ToolContext(workspace=tmp_path, config=cfg), max_steps=3)
    return agent, cfg


class _Steps:
    def __init__(self, *steps):
        self.steps = list(steps)

    def stream_chat(self, *a, **k):
        return self.steps.pop(0)


class _LiveSession:
    def __init__(self):
        self.messages = []
        self.log_path = None

    def add_user(self, text, content=None):
        self.messages.append({"role": "user", "content": text})

    def add_assistant(self, turn):
        self.messages.append({"role": "assistant", "content": turn.text})

    def add_tool_result(self, call_id, name, content, **kw):
        self.messages.append({"role": "tool", "tool_call_id": call_id,
                              "name": name, "content": content})

    def refresh_system_prompt(self, text):
        return False


def _call():
    return ToolCall(id="c1", name="write_file",
                    arguments=json.dumps({"path": "a.py", "content": "x" * 400}))


def test_headless_refuses_the_write_and_says_so(tmp_path, capsys):
    """ASK 在无头模式里的归宿:**不点头**,并且把没点头这件事说出来。

    这条是本次改动的安全边界:换入口不许等于换权限。Codex 的 `--prompt`
    默认 yolo 是反例(见 minicode/headless.py 模块开头第 2 条)。
    """
    wrote = []
    ui = HeadlessUI(_Inner())
    agent, cfg = _agent(tmp_path, ui, _AskGate(ASK),
                        [AssistantTurn(tool_calls=[_call()]),
                         AssistantTurn(text="这一步需要你点头,我没写。")],
                        handler=lambda ctx, **kw: wrote.append(kw["path"]))
    agent.run_turn("写个文件")
    assert wrote == [], "没点头就不能落盘 —— 文件在不在磁盘上才是这条的断言"
    result = headless.build_result(ui, agent, agent.session, cfg, task="写个文件",
                                   elapsed_ms=5, fmt="json")
    assert result["exit_code"] == headless.EXIT_ASK_REFUSED
    assert result["ok"] is False
    assert result["asks_refused"][0]["name"] == "write_file"
    assert result["asks_refused"][0]["args"]["content_chars"] == 400
    assert "xxxx" not in headless.render(result)


def test_the_rendered_json_never_carries_a_key():
    """`render` 带 `default=str`,而 `config.provider` 是 dataclass、repr 里有 api_key。

    这条针钉的是那一整类泄漏:无头输出常被贴进 CI 日志与工单。
    """
    cfg = Config(providers=[Provider(id="t", name="T", base_url="https://x/v1",
                                     api_key="sk-SECRET-9f2", model="m")],
                 active_provider_id="t")
    raw = headless.render(headless.build_result(
        HeadlessUI(_Inner()), Agent.__new__(Agent), _Session(), cfg,
        task="t", elapsed_ms=1, fmt="json"))
    assert "sk-SECRET-9f2" not in raw
    assert json.loads(raw)["provider"] == "t"


def test_headless_still_delivers_an_answer_when_nothing_needs_asking(tmp_path):
    ui = HeadlessUI(_Inner())
    agent, cfg = _agent(tmp_path, ui, _AskGate(ALLOW),
                        [AssistantTurn(tool_calls=[_call()]),
                         AssistantTurn(text="建好了。")],
                        handler=lambda ctx, **kw: None)
    agent.run_turn("写个文件")
    result = headless.build_result(ui, agent, agent.session, cfg, task="写个文件",
                                   elapsed_ms=5, fmt="json")
    assert result["ok"] is True and result["exit_code"] == 0
    assert result["response"] == "建好了。"
    assert result["tool_calls"] == [{"name": "write_file", "ok": True, "chars": 16}]


def test_run_json_prints_only_the_json(tmp_path, capsys, monkeypatch):
    """`main.run_json` 是 stdout 契约的最后一跳:那一屏除了 JSON 不能有别的。"""
    ui = HeadlessUI(_Inner())
    agent, cfg = _agent(tmp_path, ui, _AskGate(ALLOW),
                        [AssistantTurn(text="好。")],
                        handler=lambda ctx, **kw: None)
    with pytest.raises(SystemExit) as out:
        cli.run_json(cfg, agent, agent.session, ui, "说一句")
    assert out.value.code == headless.EXIT_OK
    cap = capsys.readouterr()
    assert json.loads(cap.out)["response"] == "好。"


def test_run_json_exits_with_the_ask_code(tmp_path, capsys):
    ui = HeadlessUI(_Inner())
    agent, cfg = _agent(tmp_path, ui, _AskGate(ASK),
                        [AssistantTurn(tool_calls=[_call()]),
                         AssistantTurn(text="要点头。")],
                        handler=lambda ctx, **kw: None)
    with pytest.raises(SystemExit) as out:
        cli.run_json(cfg, agent, agent.session, ui, "写个文件")
    assert out.value.code == headless.EXIT_ASK_REFUSED
    assert json.loads(capsys.readouterr().out)["status"] == "ask_refused"


def test_json_without_a_task_is_an_argument_error(monkeypatch, tmp_path):
    """REPL 里静默不渲染不叫无头,叫看不见 —— 宁可当场拒。"""
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    ui = HeadlessUI(_Inner())
    agent, _cfg = _agent(tmp_path, ui, _AskGate(ALLOW), [],
                        handler=lambda ctx, **kw: None)
    with pytest.raises(SystemExit) as out:
        cli._repl(_cfg, tmp_path, ui, agent, agent.session, argv=[], hui=ui)
    assert out.value.code == headless.EXIT_ARGS


def test_json_refuses_a_slash_command(monkeypatch, tmp_path):
    """斜杠命令的输出是给人看的一段话,放进来 stdout 那份 JSON 就废了。"""
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    ui = HeadlessUI(_Inner())
    agent, cfg = _agent(tmp_path, ui, _AskGate(ALLOW), [],
                        handler=lambda ctx, **kw: None)
    with pytest.raises(SystemExit) as out:
        cli._repl(cfg, tmp_path, ui, agent, agent.session, argv=["/usage"], hui=ui)
    assert out.value.code == headless.EXIT_ARGS


def test_every_exit_code_is_documented():
    """六个码各有归宿;新加一个却没人解释它 = 调用方只能猜。"""
    codes = {headless.EXIT_OK, headless.EXIT_RUNTIME, headless.EXIT_ARGS,
             headless.EXIT_ASK_REFUSED, headless.EXIT_LIMITED,
             headless.EXIT_INTERRUPTED}
    assert len(codes) == 6, "退出码撞车了(两个结局同一个数)"
    src = (Path(__file__).resolve().parent.parent
           / "minicode" / "headless.py").read_text(encoding="utf-8")
    for name in ("EXIT_RUNTIME", "EXIT_ASK_REFUSED", "EXIT_LIMITED",
                 "EXIT_INTERRUPTED"):
        assert f"{name} = " in src, f"{name} 没有一句它是干什么的"


def test_stdout_is_pinned_to_utf8_because_a_pipe_is_not_a_console(monkeypatch):
    """打包态真跑抓到的:`_fix_console_encoding` 设的是控制台代码页,对管道无效。

    不钉这一条,那份 JSON 会按 cp936 上线 —— 中文回答与中文路径当场变乱码,
    而宽容解码的消费者会静默拿到一串替换符(表现是"留档文件明明报了路径却说没有")。
    """
    import io
    buf = io.BytesIO()
    stream = io.TextIOWrapper(buf, encoding="gbk", errors="replace")
    monkeypatch.setattr(sys, "stdout", stream)
    headless.ensure_utf8_stdout()
    print(r"D:\引路学长\sessions\session_1.jsonl")
    stream.flush()
    raw = buf.getvalue()
    assert raw.decode("utf-8").strip().endswith("session_1.jsonl"), raw[:60]
    with pytest.raises(UnicodeDecodeError):
        raw.decode("ascii")      # 证明出去的是多字节 UTF-8,不是被转义成 \uXXXX
