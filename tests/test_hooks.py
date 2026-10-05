"""工具钩子(F3,v0.9.100)测试:模板替换、pre 拦截、post 如实附行、安全口径。

安全三条是底线:①钩子只从 config.json 来(没有工作区入口);②参数/结果
经临时文件交付不进命令行;③pre 拦截发生在 tool_start 之前(不出卡片)。
"""
import json

from minicode import hooks
from minicode.agent import Agent, HARD_REJECT_NOTE
from minicode.config import Config, Provider
from minicode.llm import AssistantTurn, ToolCall
from minicode.permission import ALLOW, ASK, REJECT
from minicode.tools.base import Tool, ToolContext


def test_run_hook_no_config_means_none():
    assert hooks.run_hook("pre_tool", None, "bash") is None
    assert hooks.run_hook("pre_tool", {}, "bash") is None
    assert hooks.run_hook("pre_tool", {"post_tool": "x"}, "bash") is None


def test_render_substitutes_tool_inline_and_args_via_file(tmp_path):
    args = {"path": "包含 空格 与\"引号\".txt"}
    cmd, tmp = hooks._render("检查 {tool} 的参数文件 {args_file}", "write_file", args, "args_file")
    assert "{tool}" not in cmd and "write_file" in cmd
    assert tmp is not None and json.loads(tmp.read_text(encoding="utf-8")) == args
    # 没有 {args_file} 占位就不落临时文件
    cmd2, tmp2 = hooks._render("echo {tool}", "bash", args, "args_file")
    assert tmp2 is None


def test_pre_hook_blocks_on_nonzero_exit():
    cfg = {"pre_tool": "exit 1"}
    r = hooks.run_hook("pre_tool", cfg, "bash", args={"path": "x"})
    assert r["blocked"] is True and r["ok"] is False
    # 成功不拦截
    r2 = hooks.run_hook("pre_tool", {"pre_tool": "echo ok"}, "bash", args={"path": "x"})
    assert r2["blocked"] is False and r2["ok"] is True
    # 超时也算拦截
    r3 = hooks.run_hook("pre_tool", {"pre_tool": "ping -n 30 127.0.0.1"}, "bash",
                        args={"path": "x"})
    assert r3["blocked"] is True


def test_post_hook_failure_does_not_block():
    r = hooks.run_hook("post_tool", {"post_tool": "exit 3"}, "bash", result="已完成")
    assert r["blocked"] is False and r["ok"] is False
    r2 = hooks.run_hook("post_tool", {"post_tool": "echo done"}, "bash", result="已完成")
    assert r2["ok"] is True


def test_pre_blocks_before_tool_start_and_reports(tmp_path):
    """端到端:pre 拦截发生在 tool_start 之前 —— 界面没有"正在执行"的卡片,
    回话是带钩子输出的错误。"""
    import json as _json
    from minicode.agent import Agent
    from minicode.config import Config, Provider
    from minicode.gui_ui import GuiUI
    from minicode.session import Session
    from minicode.tools.base import Tool, ToolContext
    from minicode.llm import ToolCall

    class FakeWindow:
        def __init__(self): self.events = []
        def evaluate_js(self, js): self.events.append(js)

    cfg = Config(providers=[Provider(id="t", name="T", base_url="https://x/v1",
                                     api_key="k", model="m")],
                 active_provider_id="t",
                 permission_mode="accept_all",
                 hooks={"pre_tool": "exit 7"})
    ui = GuiUI()
    ui.window = FakeWindow()
    started = []
    tool = Tool(name="write_file", description="", parameters={},
                handler=lambda ctx, **k: started.append(k) or "已写入")
    from minicode.permission import PermissionGate
    agent = Agent(llm=None, registry=[tool],
                  session=Session(system_prompt="s", log_dir=None),
                  gate=PermissionGate(cfg),
                  ui=ui,
                  ctx=ToolContext(workspace=tmp_path, config=cfg),
                  max_steps=1)
    out = agent._execute(ToolCall(id="c1", name="write_file",
                                  arguments=_json.dumps({"path": "a.txt", "content": "x"})))
    assert "工具钩子拦下" in out and started == [], "被拦的那步绝不能真执行"


# ------------------------------------------- matcher(C2,v0.9.102)

def test_string_form_still_runs_for_every_tool():
    cfg = {"pre_tool": "echo ok"}
    assert hooks.run_hook("pre_tool", cfg, "bash", args={"x": 1})["ok"] is True
    assert hooks.run_hook("pre_tool", cfg, "write_file", args={"path": "a"})["ok"] is True


def test_match_tools_limits_scope():
    cfg = {"pre_tool": {"command": "echo ok", "tools": ["write_file", "edit_file"]}}
    assert hooks.run_hook("pre_tool", cfg, "write_file", args={"path": "a"}) is not None
    assert hooks.run_hook("pre_tool", cfg, "bash", args={"cmd": "x"}) is None, \
        "名单外的工具一个字节都不跑"


def test_match_paths_requires_path_and_glob(tmp_path):
    cfg = {"pre_tool": {"command": "echo ok", "paths": ["*.py", "src/*"]}}
    assert hooks.run_hook("pre_tool", cfg, "write_file", args={"path": "a.py"}) is not None
    assert hooks.run_hook("pre_tool", cfg, "write_file", args={"path": "src/x.txt"}) is not None
    assert hooks.run_hook("pre_tool", cfg, "write_file", args={"path": "b.txt"}) is None
    # 工具没有 path 参数:指定了 paths 就不适用
    assert hooks.run_hook("pre_tool", cfg, "bash", args={"cmd": "x"}) is None
    # 反斜杠路径按正斜杠口径匹配(Windows 传参形状)
    assert hooks.run_hook("pre_tool", cfg, "write_file",
                          args={"path": "src\y.py"}) is not None


def test_bad_entry_shapes_are_skipped():
    assert hooks.run_hook("pre_tool", {"pre_tool": {}}, "bash") is None
    assert hooks.run_hook("pre_tool", {"pre_tool": {"command": "   "}}, "bash") is None
    assert hooks.run_hook("pre_tool", {"pre_tool": 42}, "bash") is None


# ------------------------------------------- H1(v0.9.115):能改、能表态

def _json_hook(tmp_path, payload, name="hook.py"):
    """写一只会把这些内容打成 JSON 的钩子脚本,返回可直接进 config 的命令。

    走脚本而不是 `python -c`:cmd.exe 的引号规则会让测试本身变成一门玄学。
    """
    p = tmp_path / name
    p.write_text("import json\nprint(json.dumps(%r))\n" % (payload,), encoding="utf-8")
    return f'python "{p}"'


def test_exit_two_is_a_declared_deny():
    """退出码 2 = 钩子**明确拒绝**,与"钩子没跑成"是分开的两件事。"""
    cfg = {"pre_tool": {"command": "exit 2"}}
    r = hooks.run_hook("pre_tool", cfg, "bash", args={"command": "ls"})
    assert r["blocked"] is True and r["declared"] is True
    assert r["decision"] == "deny" and r["exit_code"] == 2


def test_other_nonzero_is_broken_not_declared():
    cfg = {"pre_tool": {"command": "exit 5"}}
    r = hooks.run_hook("pre_tool", cfg, "bash", args={"command": "ls"})
    # 照旧拦(on_error 默认 block),但要说清是"没跑成"而不是"有人判了这一步"
    assert r["blocked"] is True and r["ok"] is False and r["declared"] is False


def test_on_error_continue_lets_the_step_pass():
    cfg = {"pre_tool": {"command": "exit 5", "on_error": "continue"}}
    r = hooks.run_hook("pre_tool", cfg, "bash", args={"command": "ls"})
    assert r["blocked"] is False and r["ok"] is False and r["decision"] == "continue"
    # post 永远不拦(老口径),on_error 写什么都一样
    cfg2 = {"post_tool": {"command": "exit 9", "on_error": "block"}}
    assert hooks.run_hook("post_tool", cfg2, "bash", result="x")["blocked"] is False


def test_json_decision_and_updated_input(tmp_path):
    cmd = _json_hook(tmp_path, {"decision": "deny", "reason": "这一步该换成 dry-run"})
    r = hooks.run_hook("pre_tool", {"pre_tool": {"command": cmd}}, "bash",
                      args={"command": "git push"})
    assert r["blocked"] is True and r["declared"] is True
    assert "dry-run" in r["output"], "钩子给的理由要能到用户与模型面前"
    assert r["decision"] == "deny"


def test_updated_input_only_on_pre(tmp_path):
    payload = {"updated_input": {"command": "git push --dry-run"}}
    cmd = _json_hook(tmp_path, payload)
    pre = hooks.run_hook("pre_tool", {"pre_tool": {"command": cmd}}, "bash",
                         args={"command": "git push"})
    assert pre["updated_input"] == {"command": "git push --dry-run"}
    # post 拿不到"改参数"这只口子:结果都跑完了,改参数没有意义
    post = hooks.run_hook("post_tool", {"post_tool": {"command": cmd}}, "bash",
                         result="ok")
    assert post["updated_input"] is None


def test_updated_output_and_context_only_from_structured_json(tmp_path):
    """普通 stdout 今天是被丢掉的 —— 把它当"要回灌的正文"等于改了没动过配置的人的行为。"""
    plain = hooks.run_hook("post_tool", {"post_tool": "echo 一堆日志"}, "bash",
                           result="原结果")
    assert plain["updated_output"] == "" and plain["context"] == ""
    cmd = _json_hook(tmp_path, {"updated_output": "3 failed, 12 passed",
                                "context": "测试跑过了"})
    got = hooks.run_hook("post_tool", {"post_tool": {"command": cmd}}, "bash",
                         result="原结果")
    assert got["updated_output"] == "3 failed, 12 passed"
    assert got["context"] == "测试跑过了"


def test_partial_stdout_is_not_guessed_as_json(tmp_path):
    """半屏混排文本不当结构化决策:猜一段 JSON 出来,等于让用户一次格式失误
    变成一次静默的权限变化。"""
    p = tmp_path / "noisy.py"
    p.write_text("print('格式化于 ' + __file__)\n"
                 "print('{\"decision\": \"deny\"}')\n", encoding="utf-8")
    r = hooks.run_hook("pre_tool", {"pre_tool": {"command": f'python "{p}"'}},
                       "bash", args={"command": "ls"})
    assert r["decision"] == "continue" and r["ok"] is True


def test_timeout_is_per_entry_and_capped_with_a_word_said():
    cfg = {"pre_tool": {"command": "exit 5", "timeout": 999}}
    r = hooks.run_hook("pre_tool", cfg, "bash", args={"command": "ls"})
    assert "超过上限" in r["output"] and "120" in r["output"]
    # 配了个不是数字的 timeout:按默认跑,而且要说话,不能静悄悄改掉用户的意思
    r2 = hooks.run_hook("pre_tool", {"pre_tool": {"command": "exit 5",
                                                  "timeout": "很快"}},
                        "bash", args={"command": "ls"})
    assert "不是数字" in r2["output"]


def test_matcher_command_prefix():
    cfg = {"pre_tool": {"command": "echo hit", "command_prefix": ["git", "npm"]}}
    assert hooks.run_hook("pre_tool", cfg, "bash",
                          args={"command": "git status"}) is not None
    assert hooks.run_hook("pre_tool", cfg, "bash",
                          args={"command": "GIT push"}) is not None, "大小写不敏感"
    assert hooks.run_hook("pre_tool", cfg, "bash", args={"command": "rm -rf x"}) is None
    # 没有 command 参数的工具:声明了 command_prefix 就不适用(与 paths 同一口径)
    assert hooks.run_hook("pre_tool", cfg, "read_file", args={"path": "a"}) is None


def test_matcher_args_match(tmp_path):
    cfg = {"pre_tool": {"command": "echo hit", "args_match": {"url": "*example.com*"}}}
    assert hooks.run_hook("pre_tool", cfg, "web_crawl",
                          args={"url": "https://example.com/x"}) is not None
    assert hooks.run_hook("pre_tool", cfg, "web_crawl",
                          args={"url": "https://other.com"}) is None
    assert hooks.run_hook("pre_tool", cfg, "web_crawl", args={}) is None


def test_matched_starts_no_process(tmp_path):
    """红线预检要在"到底有没有钩子认领这一步"上说得出口,而**不能顺手起进程**。"""
    marker = tmp_path / "did_run"
    script = tmp_path / "mk.py"
    script.write_text(f'open(r"{marker}", "w").close()\nprint("ok")\n', encoding="utf-8")
    cfg = {"pre_tool": {"command": f'python "{script}"', "tools": ["bash"]}}
    assert hooks.matched("pre_tool", cfg, "bash", args={"command": "ls"}) is True
    assert not marker.exists(), "matched 只判认领,不跑命令"
    assert hooks.matched("pre_tool", cfg, "read_file", args={"path": "a"}) is False
    assert hooks.matched("pre_tool", None, "bash", args={}) is False
    hooks.run_hook("pre_tool", cfg, "bash", args={"command": "ls"})
    assert marker.exists(), "真跑的那次才该留下文件"


def test_clip_note_says_how_much_was_cut():
    long_text = "问" * 5000
    got = hooks._clip_note(long_text, 1200)
    assert "共 5000 字" in got and "只留了 1200 字" in got


# ------------------------------------------- H1/H2(v0.9.115):接到 _execute 上
#
# 这一组断的不是"钩子返回了什么",而是**顺序**:门看的是哪份参数、卡片画的是哪份、
# 硬拒到底叫没叫钩子、两把尺有没有被折成一把。这四条是方案 §四 H1 的契约红线,
# 破了其中任何一条,功能看起来仍然全对 —— 而那正是最坏的那种坏。

class _RecUI:
    """只记不画的界面替身:要断的是"卡片拿到的是哪份参数",不是像素。"""

    def __init__(self):
        self.started = []
        self.dones = []
        self.hard_rejected = []
        self.infos = []
        self.confirms = []      # 弹过几次确认框 —— H3 的"到底问没问人"就看这个

    def tool_start(self, tool, args):
        self.started.append(dict(args))

    def tool_done(self, tool, result):
        self.dones.append(result)

    def tool_rejected_hard(self, tool, args):
        self.hard_rejected.append(tool.name)

    def confirm_tool(self, *a, **k):
        self.confirms.append(a)
        return "yes"

    def finish_stream(self):
        pass

    def on_text_delta(self, text):
        pass

    def on_reasoning_delta(self, text):
        pass

    def llm_error(self, e):
        raise AssertionError(f"这一轮不该走到 LLM 报错:{type(e).__name__}: {e}")

    def turn_interrupted(self):
        pass

    def push(self, *a, **k):
        pass

    def info(self, text, *a, **k):
        self.infos.append(str(text))


class _StubGate:
    """权限门替身,把每次 check **看到的参数**记下来。

    H1 第一条契约的断言依据就是这份记录:门必须至少有一次看到最终真要执行的那份
    参数 —— 否则"用户点过头的内容"与"被执行的内容"是两回事。
    """

    def __init__(self, *decisions, ask_kind: str = "", reject_reason: str = ""):
        self.decisions = list(decisions)
        self.seen = []
        self.ask_note = ""
        self.ask_kind = ask_kind        # H3:真门会在这里写"是哪条口开的";替身照写
        self.reject_reason = reject_reason   # L2:被项目规则拒时那条规则的人话
        self.project_allow_reason = ""
        self.turn_allowed = []          # 被升成"本轮总是允许"的工具名
        self.outside_approved = []      # [(session?)] —— 代答不许升长期授权,记在这

    def check(self, tool, args):
        self.seen.append(dict(args))
        return self.decisions.pop(0) if self.decisions else ALLOW

    def approve_for_turn(self, name):
        self.turn_allowed.append(name)

    def approve_outside(self, tool, args, session=False):
        self.outside_approved.append(bool(session))

    def new_turn(self):
        self.ask_note = ""
        self.project_allow_reason = ""


class _Steps:
    """假 LLM:按步喂回合(run_turn 用得到)。"""

    def __init__(self, *steps):
        self.steps = list(steps)

    def stream_chat(self, *a, **k):
        return self.steps.pop(0)


def _grep_tool(handler):
    return Tool(name="grep", description="", handler=handler, read_only=True,
                parameters={"type": "object", "properties": {
                    "pattern": {"type": "string"}, "path": {"type": "string"}},
                    "required": ["pattern"]})


def _hook_agent(tmp_path, cfg_hooks, handler=None, gate=None, steps=None,
                tools=None):
    from minicode.session import Session
    cfg = Config(providers=[Provider(id="t", name="T", base_url="https://x/v1",
                                     api_key="k", model="m")],
                 active_provider_id="t", permission_mode="confirm",
                 hooks=cfg_hooks)
    ui = _RecUI()
    s = Session(system_prompt="s", log_dir=None)
    agent = Agent(llm=_Steps(*(steps or [])), registry=(
        tools or [_grep_tool(handler or (lambda ctx, **k: "跑完了"))]), session=s,
        gate=gate or _StubGate(), ui=ui,
        ctx=ToolContext(workspace=tmp_path, config=cfg), max_steps=3)
    return agent, s, ui


def _write_tool(tmp_path, result_text):
    """一只真往工作区落盘的 write_file 替身。
    内容故意是**合法 Python**:内置的 ast 自检(`_py_syntax_feedback`)一发现问题就会
    往结果里加它自己那行,那支钩子测的就不是它测的东西了。
    """
    def handler(ctx, path="a.py", content=""):
        (tmp_path / str(path)).write_text("ok = 1\n", encoding="utf-8")
        return result_text
    return Tool(name="write_file", description="", read_only=False, handler=handler,
                parameters={"type": "object", "properties": {
                    "path": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["path", "content"]})



def _call(**args):
    return ToolCall(id="c1", name="grep", arguments=json.dumps(args))


def test_rewrite_makes_the_gate_look_at_final_args(tmp_path):
    cmd = _json_hook(tmp_path, {"updated_input": {"pattern": "*.py", "path": "a.txt"}})
    agent, s, ui = _hook_agent(
        tmp_path, {"pre_tool": {"command": cmd}},
        handler=lambda ctx, **k: "跑完了:" + json.dumps(k, ensure_ascii=False, sort_keys=True))
    out = agent._execute(_call(pattern="SECRET_TOKEN"))

    assert agent.gate.seen[0] == {"pattern": "SECRET_TOKEN"}, "红线预检看的是原参数"
    assert agent.gate.seen[1] == {"pattern": "*.py", "path": "a.txt"}, \
        "正式那道门必须看改写后的 —— 这条破了就等于让用户对一份参数点头、执行另一份"
    assert ui.started == [{"pattern": "*.py", "path": "a.txt"}], "卡片画的必须是真要执行的那份"
    assert json.loads(out.split("跑完了:")[1].split("\n")[0]) == {
        "path": "a.txt", "pattern": "*.py"}, "handler 拿到的是改写后的"
    assert "钩子改写" in out and "安全日志" in out, "第三处留痕:回给模型也要写明"


def test_rewrite_rejected_when_it_breaks_the_schema(tmp_path):
    """改写不得凭空吃掉必填项,也不得塞进这只工具没有的参数 —— 那就是一次静默的改道。"""
    cmd = _json_hook(tmp_path, {"updated_input": {"path": "a.txt"}})  # 少了必填 pattern
    agent, s, ui = _hook_agent(tmp_path, {"pre_tool": {"command": cmd}})
    out = agent._execute(_call(pattern="keep"))
    assert agent.gate.seen[-1] == {"pattern": "keep"}, "不合 schema 的改写不采纳"
    assert "不合这只工具的参数形状" in out and "pattern" in out


def test_red_line_never_calls_the_hook(tmp_path):
    marker = tmp_path / "ran"
    script = tmp_path / "mk.py"
    script.write_text(f'open(r"{marker}", "w").close()\nprint("ok")\n', encoding="utf-8")
    agent, s, ui = _hook_agent(tmp_path, {"pre_tool": {"command": f'python "{script}"'}},
                               gate=_StubGate(REJECT))
    out = agent._execute(_call(pattern="x"))
    assert out == HARD_REJECT_NOTE
    assert not marker.exists(), "硬拒根本不叫钩子"
    assert ui.started == [] and ui.hard_rejected == ["grep"]


def test_declared_deny_and_broken_hook_say_different_things(tmp_path):
    cmd = _json_hook(tmp_path, {"decision": "deny", "reason": "这一步应改成 dry-run"})
    agent, s, ui = _hook_agent(tmp_path, {"pre_tool": {"command": cmd}})
    out = agent._execute(_call(pattern="push"))
    assert "工具钩子拦下" in out and "声明拒绝" in out and "dry-run" in out

    agent2, s2, ui2 = _hook_agent(tmp_path, {"pre_tool": {"command": "exit 5"}})
    out2 = agent2._execute(_call(pattern="push"))
    assert "工具钩子拦下" in out2 and "没跑成" in out2, "两种拦下该说的话完全不同"


def test_hook_ms_lands_beside_the_step_one_not_folded_into_it(tmp_path):
    """耗时单列一个键:elapsed_ms 的口径是"handler 全程"(确认框等待都不算),
    把钩子折进去等于当场改掉那把尺的定义。"""
    slow = 'python -c "import time;time.sleep(0.35)"'
    agent, s, ui = _hook_agent(tmp_path, {"pre_tool": {"command": slow}},
                               steps=[AssistantTurn(tool_calls=[_call(pattern="x")]),
                                      AssistantTurn(text="收到")])
    agent.run_turn("跑一下")
    msg = [m for m in s.messages if m.get("role") == "tool"][0]
    assert msg["hook_ms"] >= 300, "钩子的实测毫秒要落档"
    assert "elapsed_ms" in msg and msg["elapsed_ms"] < msg["hook_ms"], "两把尺不许折成一把"


def test_no_hook_means_the_archive_gains_no_key(tmp_path):
    """方案 §五.1:没配钩子的人不该在留档里看见任何新东西。"""
    agent, s, ui = _hook_agent(tmp_path, {},
                               steps=[AssistantTurn(tool_calls=[_call(pattern="x")]),
                                      AssistantTurn(text="收到")])
    agent.run_turn("跑一下")
    msg = [m for m in s.messages if m.get("role") == "tool"][0]
    assert "hook_ms" not in msg


def test_hook_ms_never_leaves_the_machine(tmp_path):
    """本地记账字段一律不许进请求体:严格端点对未知键直接 400(v0.9.66 那条教训)。"""
    from minicode.session import outbound
    agent, s, ui = _hook_agent(
        tmp_path, {"pre_tool": {"command": 'python -c "print(1)"'}},
        steps=[AssistantTurn(tool_calls=[_call(pattern="x")]),
               AssistantTurn(text="收到")])
    agent.run_turn("跑一下")
    stored = [m for m in s.messages if m.get("role") == "tool"][0]
    assert "hook_ms" in stored
    sent = outbound(s.messages)
    assert all("hook_ms" not in m for m in sent)


def test_broken_hook_three_in_a_row_speaks_once(tmp_path):
    cfg = {"pre_tool": {"command": "exit 5", "on_error": "continue"}}
    agent, s, ui = _hook_agent(tmp_path, cfg)
    for _ in range(3):
        agent._execute(_call(pattern="x"))
    assert len(ui.infos) == 1 and "连着 3 次没跑成" in ui.infos[0]
    # 一条真在拒绝的钩子工作得好好的 —— 把它算进"坏了"是骗用户去修没坏的东西
    agent2, s2, ui2 = _hook_agent(tmp_path, {"pre_tool": {"command": "exit 2"}})
    for _ in range(4):
        agent2._execute(_call(pattern="x"))
    assert ui2.infos == []


def test_dur_text_appends_the_hook_segment():
    from minicode import activity
    assert activity._dur_text({"dur": 5, "hook_ms": 400}) == "5s(钩子 0.4s)"
    assert activity._dur_text({"dur": 5}) == "5s"
    assert activity._dur_text({"hook_ms": 400}) == "", \
        "这一步的实测都没有就不显示,更不拿钩子那一截凑一个数"


# ------------------------------------------- H3(v0.9.115):permission_request
#
# 这一组钉的是"机器能不能替人点头"的边界。最贵的一种坏不是拦错放错,而是**一条
# 坏掉的钩子悄悄替用户答了话** —— 所以下面每条都在断两件事:弹框弹没弹,
# 以及 `agent._hook_ms` 是不是 None(没跑 = 没耗时,这个探针不用读日志就知道钩子
# 到底有没有被叫)。

def _ask_agent(tmp_path, hooks_cfg, ask_kind="mode"):
    gate = _StubGate(ASK, ask_kind=ask_kind)
    agent, s, ui = _hook_agent(tmp_path, hooks_cfg, gate=gate)
    return agent, s, ui, gate


def test_permission_hook_can_answer_an_ordinary_ask(tmp_path):
    cmd = _json_hook(tmp_path, {"decision": "allow", "reason": "测试期一律放行"})
    agent, s, ui, gate = _ask_agent(tmp_path, {"permission_request": {"command": cmd}})
    out = agent._execute(_call(pattern="x"))
    assert ui.confirms == [], "钩子明确答了允许,就不该再拿同一问去烦用户"
    assert ui.started == [{"pattern": "x"}] and "跑完了" in out
    assert "用户没有点过头" in out, "代答必须写明这不是用户批准的"
    assert gate.outside_approved == [False] and gate.turn_allowed == [], \
        "代答只这一条、只这一回合:不升「本轮总是允许」,更不落盘"


def test_the_three_red_line_asks_never_go_to_the_hook(tmp_path):
    """凭据、仓库 `.git`、外部内容升格 —— 这三类门不叫钩子,一次都不叫。"""
    cmd = _json_hook(tmp_path, {"decision": "allow"})
    for kind in ("credential", "git", "external"):
        agent, s, ui, gate = _ask_agent(tmp_path, {"permission_request": {"command": cmd}},
                                        ask_kind=kind)
        agent._execute(_call(pattern="x"))
        assert len(ui.confirms) == 1, f"{kind} 那条门必须问人"
        assert agent._hook_ms is None, f"{kind}:钩子连跑都不该跑"


def test_broken_permission_hook_does_not_answer_for_the_user(tmp_path):
    """`on_error` 在这一类上**不适用**:没跑成的归宿既不是拦也不是放,而是照旧弹框。"""
    for on_error in ("block", "continue"):
        cfg = {"permission_request": {"command": "exit 5", "on_error": on_error}}
        agent, s, ui, gate = _ask_agent(tmp_path, cfg)
        out = agent._execute(_call(pattern="x"))
        assert len(ui.confirms) == 1, f"on_error={on_error} 也不许替用户答话"
        assert "跑完了" in out, "用户点了允许,这一步就照办"


def test_silent_permission_hook_leaves_the_answer_to_the_human(tmp_path):
    """跑成但没表态(整段 stdout 不是带 decision 的 JSON)= 照常问人。"""
    agent, s, ui, gate = _ask_agent(tmp_path, {"permission_request": "echo 我只是打日志"})
    agent._execute(_call(pattern="x"))
    assert len(ui.confirms) == 1 and agent._hook_ms is not None


def test_hook_deny_says_it_was_not_the_user(tmp_path):
    cmd = _json_hook(tmp_path, {"decision": "deny", "reason": "仓库正忙,别在这时候推"})
    agent, s, ui, gate = _ask_agent(tmp_path, {"permission_request": {"command": cmd}})
    out = agent._execute(_call(pattern="x"))
    assert "不是用户拒的" in out and "仓库正忙" in out
    assert ui.started == [] and ui.confirms == [], "被代答拒掉的那一步:既不画卡也不弹框"
    assert gate.outside_approved == [], "拒了就不许再记任何放行"


def test_permission_hook_ms_counts_into_the_step(tmp_path):
    slow = 'python -c "import time;time.sleep(0.35)"'
    cfg = {"permission_request": {"command": slow}}
    agent, s, ui, gate = _ask_agent(tmp_path, cfg)
    agent._execute(_call(pattern="x"))
    assert agent._hook_ms >= 300, "代答这一步的实测毫秒同样要拿到"


def test_no_permission_hook_asks_exactly_like_before(tmp_path):
    """没配这一类钩子的人:弹框一次、参数与文案与基线一致(§五.1)。"""
    agent, s, ui, gate = _ask_agent(tmp_path, {})
    out = agent._execute(_call(pattern="x"))
    assert len(ui.confirms) == 1 and agent._hook_ms is None and "跑完了" in out
    assert gate.outside_approved == [False], "用户点「允许」仍走老路径,只是没人替他答"


def test_permission_request_honours_the_matcher(tmp_path):
    cmd = _json_hook(tmp_path, {"decision": "allow"})
    cfg = {"permission_request": {"command": cmd, "tools": ["read_file"]}}
    agent, s, ui, gate = _ask_agent(tmp_path, cfg)
    agent._execute(_call(pattern="x"))
    assert len(ui.confirms) == 1 and agent._hook_ms is None, "名单外的工具不叫钩子"


def test_hard_reject_offers_an_exit_instead_of_a_dead_end():
    """#49d:硬拒过去只丢一句"请勿重试",模型没出口 —— 要么原地打转,要么试探绕过。"""
    assert "请勿重试" not in HARD_REJECT_NOTE
    assert "向用户" in HARD_REJECT_NOTE, "给的是说话的对象,不是绕过的通道"
    assert "不要换一种写法绕过" in HARD_REJECT_NOTE


def test_real_gate_names_which_door_asked(tmp_path):
    """真门得说得出是哪条口开的 —— H3 的"不叫钩子"判定靠这个结构字段,不靠读中文。"""
    from minicode.permission import PermissionGate
    cfg = Config(providers=[Provider(id="t", name="T", base_url="https://x/v1",
                                     api_key="k", model="m")],
                 active_provider_id="t", permission_mode="confirm")
    gate = PermissionGate(cfg)
    write = Tool(name="write_file", description="", read_only=False,
                 handler=lambda ctx, **k: "",
                 parameters={"type": "object", "properties": {}})
    assert gate.check(write, {"path": "a.txt", "content": "x"}) == ASK
    assert gate.ask_kind == "mode"
    assert gate.check(_grep_tool(lambda ctx, **k: ""), {"pattern": "x"}) == ALLOW
    assert gate.ask_kind == "", "放行/硬拒都不该留下一个开口的门"


# ------------------------------------------- H4(v0.9.115):两个事件钩子
#
# 钉四件事:各叫几次、那句附话到得了模型且**只到一次**、用户原话一个字都不许动、
# 子代理不叫。第三件最容易被"顺手加个改写"破坏 —— 留档里那条必须是他说过的话。

def _counting_hook(tmp_path, name):
    """写一只每被叫一次就往计数文件里追加一行的钩子,回 (命令, 计数路径)。"""
    marker = tmp_path / f"{name}.count"
    script = tmp_path / f"{name}.py"
    script.write_text(f'open(r"{marker}", "a", encoding="utf-8").write("1\\n")\n'
                      'print("ok")\n', encoding="utf-8")
    return f'python "{script}"', marker


def _hits(marker):
    return 0 if not marker.exists() else len(
        marker.read_text(encoding="utf-8").splitlines())


class _RecordingLLM:
    """收下每次请求的消息体 —— "这一次请求带没带上那句附话"只能从出口看。"""

    def __init__(self, steps):
        self.steps, self.sent = list(steps), []

    def stream_chat(self, messages, *a, **k):
        self.sent.append([dict(m) for m in messages])
        return self.steps.pop(0)


def test_event_hooks_fire_the_right_number_of_times(tmp_path):
    start_cmd, start_n = _counting_hook(tmp_path, "start")
    prompt_cmd, prompt_n = _counting_hook(tmp_path, "prompt")
    cfg = {"session_start": {"command": start_cmd},
           "user_prompt_submit": {"command": prompt_cmd}}
    agent, s, ui = _hook_agent(tmp_path, cfg, steps=[
        AssistantTurn(text="一"), AssistantTurn(text="二")])
    agent.run_turn("第一问")
    agent.run_turn("第二问")
    assert _hits(start_n) == 1, "一个实例 = 一次会话,session_start 只该叫一回"
    assert _hits(prompt_n) == 2, "每问都该叫一次 user_prompt_submit"


def test_event_hook_context_reaches_the_model_once(tmp_path):
    cmd = _json_hook(tmp_path, {"context": "今天仓库在冻结,别改公共接口"},
                     name="ctxhook.py")
    agent, s, ui = _hook_agent(tmp_path, {"session_start": {"command": cmd}})
    agent.llm = _RecordingLLM([AssistantTurn(text="一"), AssistantTurn(text="二")])
    agent.run_turn("开始")
    agent.run_turn("接着")
    first = str(agent.llm.sent[0][0].get("content"))
    assert "冻结" in first, "附的那句话要真到模型眼前"
    second = str(agent.llm.sent[1][0].get("content"))
    assert "冻结" not in second, "它只活在一次请求里 —— 常驻上下文是另一笔账,不许偷偷做到这一步"



def test_user_line_stays_verbatim(tmp_path):
    cmd = _json_hook(tmp_path, {"context": "附一句"}, name="v.py")
    agent, s, ui = _hook_agent(tmp_path, {"user_prompt_submit": {"command": cmd}},
                               steps=[AssistantTurn(text="一")])
    agent.run_turn("我原话就是这样")
    user_msgs = [m for m in s.messages if m.get("role") == "user"]
    assert user_msgs[-1]["content"] == "我原话就是这样", "留档里那条必须是他说过的字"


def test_event_hook_appends_rather_than_overwrites(tmp_path):
    """`_pending_prior` 另有住户(差评避让那条线在 `_note_low_rated_prior` 里写它),
    钩子那句只能追加 —— 谁覆盖谁,另一句就静默消失了。"""
    cmd = _json_hook(tmp_path, {"context": "钩子说的话"}, name="ap.py")
    agent, s, ui = _hook_agent(tmp_path, {})
    agent._pending_prior = "这条查询上周被点过差评"
    agent._collect_event_hook("user_prompt_submit", "问一句",
                              {"user_prompt_submit": {"command": cmd}})
    assert "上周被点过差评" in agent._pending_prior, "先验不能被钩子顶掉"
    assert "钩子说的话" in agent._pending_prior, "钩子那句也不该被先验挤掉"
    assert agent._pending_prior.count("上周被点过差评") == 1



def test_subagent_flag_suppresses_the_events(tmp_path):
    start_cmd, start_n = _counting_hook(tmp_path, "sub_start")
    prompt_cmd, prompt_n = _counting_hook(tmp_path, "sub_prompt")
    cfg = {"session_start": {"command": start_cmd},
           "user_prompt_submit": {"command": prompt_cmd}}
    agent, s, ui = _hook_agent(tmp_path, cfg, steps=[AssistantTurn(text="干完了")])
    agent._is_sub = True
    agent.run_turn("这是任务文本,不是会话开场")
    assert _hits(start_n) == 0 and _hits(prompt_n) == 0, "子代理不叫这两个事件"


def test_regenerate_does_not_ask_the_hook_twice(tmp_path):
    prompt_cmd, prompt_n = _counting_hook(tmp_path, "regen")
    agent, s, ui = _hook_agent(tmp_path, {"user_prompt_submit": {"command": prompt_cmd}},
                               steps=[AssistantTurn(text="一"), AssistantTurn(text="二")])
    s.add_user("原来那句")
    agent.run_turn("原来那句", add_user=False)     # 用户点「重新生成」
    assert _hits(prompt_n) == 0, "同一句话没被重新提交,不该再叫一次"


def test_deny_on_an_event_kind_says_it_did_nothing():
    r = hooks.run_hook("session_start", {"session_start": "exit 2"}, "")
    assert r["blocked"] is False and r["ok"] is False
    assert "deny 没生效" in r["output"], "拦不住用户发言这件事要当场说,不许悄悄吞"


def test_event_kinds_ignore_the_tools_filter():
    """非工具类事件没有"哪只工具"这一维:配了 tools 也不许把它筛成永不触发。"""
    cfg = {"session_start": {"command": "echo hit", "tools": ["bash"]}}
    assert hooks.run_hook("session_start", cfg, "") is not None


def test_no_event_hooks_changes_nothing(tmp_path):
    agent, s, ui = _hook_agent(tmp_path, {}, steps=[AssistantTurn(text="一")])
    agent.run_turn("普通一问")
    assert agent._pending_prior == ""


def test_pre_tool_context_reaches_the_model(tmp_path):
    """把 pre 的 `context` 这根线钉住:它和 post 的一样是附注,曾经只在文档里成立。"""
    cmd = _json_hook(tmp_path, {"context": "已记入审计表"}, name="pctx.py")
    agent, s, ui = _hook_agent(tmp_path, {"pre_tool": {"command": cmd}})
    out = agent._execute(_call(pattern="x"))
    assert "已记入审计表" in out


# ------------------------------------------- H6(v0.9.115):编辑后自动跑检查并回灌
#
# 这条闭环是 H1 的 `updated_output` 拼出来的第一个真用例(矩阵里的 P2 行业标配)。
# 它能不能成立,只取决于一件事:**钩子拿不拿得到"刚写的是哪个文件"** ——
# 旧载荷只有 {tool, result},那句结果是给活人看的,不是接口。

def test_post_hook_sees_which_file_changed(tmp_path):
    seen = tmp_path / "seen.txt"
    script = tmp_path / "spy.py"
    script.write_text(
        "import json,sys\n"
        "d = json.load(open(sys.argv[1], encoding='utf-8'))\n"
        f"open(r\"{seen}\", \"w\", encoding=\"utf-8\").write(str(d['args'].get('path')))\n"
        "print(\"ok\")\n", encoding="utf-8")
    agent, s, ui = _hook_agent(
        tmp_path, {"post_tool": {"command": f'python "{script}" "{{result_file}}"'}},
        tools=[_write_tool(tmp_path, "已写入 a.py")])
    agent._execute(ToolCall(id="c1", name="write_file",
                             arguments=json.dumps({"path": "a.py", "content": "x"})))
    assert seen.read_text(encoding="utf-8") == "a.py", \
        "post 的载荷必须带着 args —— 不然 lint 钩子只能去解析那句给人看的结果"


def test_lint_after_edit_loop_closes(tmp_path):
    """写坏一次 → 当场把失败原文回灌给模型,而不是留着"文件已写好"的假象。"""
    script = tmp_path / "lint.py"
    script.write_text(
        "import json,sys\n"
        "d = json.load(open(sys.argv[1], encoding='utf-8'))\n"
        "if str(d['args'].get('path')).endswith('.py') and 'BAD' in str(d['result']):\n"
        "    print(json.dumps({'updated_output': 'lint: 第 1 行 E999 语法错', 'context': '已跑过 lint'}))\n"
        "else:\n"
        "    print(json.dumps({'context': 'lint 通过'}))\n", encoding="utf-8")
    cfg = {"post_tool": {"command": f'python "{script}" "{{result_file}}"',
                        "tools": ["write_file"], "paths": ["*.py"],
                        "timeout": 30, "on_error": "continue"}}
    agent, s, ui = _hook_agent(tmp_path, cfg, tools=[_write_tool(tmp_path, "已写好(BAD)")])
    out = agent._execute(ToolCall(id="c1", name="write_file",
                                  arguments=json.dumps({"path": "a.py", "content": "x"})))
    assert "第 1 行 E999 语法错" in out, "失败原文要真到模型面前"
    assert "已写好(BAD)" in out, "原结果不许被吞 —— 回灌是追加,不是替换"
    assert "lint 通过" not in out


# ------------------------------------------- L2(v0.9.116):项目规则的两副面孔

def test_project_rule_ask_is_never_answered_by_the_hook(tmp_path):
    """用户写 ask 就是为了亲自看一眼 —— 另一条预先配好的钩子替他答,等于自己拆自己装的门。"""
    cmd = _json_hook(tmp_path, {"decision": "allow", "reason": "一律放行"}, name="pr.py")
    gate = _StubGate(ASK, ask_kind="project_rule")
    agent, s, ui = _hook_agent(tmp_path, {"permission_request": {"command": cmd}}, gate=gate)
    agent._execute(_call(pattern="x"))
    assert len(ui.confirms) == 1, "project_rule 那条 ask 必须问人"
    assert agent._hook_ms is None, "连叫都不该叫钩子"


def test_project_deny_and_redline_reject_say_different_things(tmp_path, monkeypatch):
    """两种拒绝说两种话:一种"这事危险、只有用户能放开",一种"这是你自己写的规则拦的"。"""
    from minicode import provenance
    logged = []
    monkeypatch.setattr(provenance, "log_escalation",
                        lambda *a: logged.append(a))
    gate = _StubGate(REJECT, reject_reason="permissions.json 第 2 条:拒绝动 路径 `a.md`")
    agent, s2, ui = _hook_agent(tmp_path, {}, gate=gate)
    out = agent._execute(_call(pattern="x"))
    assert "permissions.json" in out and "不是安全红线" in out
    assert "安全策略硬拒绝" not in out, "别把用户写的规则说成系统红线"
    assert "不要换一种写法绕过它" in out
    assert logged and logged[0][0] == "project_deny", "规则拒也要留痕"

    gate2 = _StubGate(REJECT)          # 没有 reject_reason = 红线
    agent2, s3, ui2 = _hook_agent(tmp_path, {}, gate=gate2)
    assert agent2._execute(_call(pattern="x")) == HARD_REJECT_NOTE
