"""子代理(task 工具)测试:派发、只读隔离、递归拒绝、步数上限、只读并行。"""
import json
import threading

from minicode.llm import AssistantTurn, ToolCall
from test_gui_bridge import make, wait_until


class QueueLLM:
    """父代理与子代理共用同一个脚本队列(单线程按序消费)。"""

    def __init__(self, turns):
        self.turns = list(turns)
        self.consumed = 0

    def stream_chat(self, messages, tools, on_text=None, on_reasoning=None,
                    on_retry=None):
        self.consumed += 1
        turn = self.turns.pop(0)
        if isinstance(turn, BaseException):
            raise turn
        if on_text and turn.text:
            on_text(turn.text)
        return turn


def _call(name, args, call_id="c1"):
    return ToolCall(id=call_id, name=name, arguments=json.dumps(args))


def _task_turn(type_, task_text, call_id="c1"):
    return AssistantTurn(tool_calls=[
        _call("task", {"type": type_, "task": task_text}, call_id)])


def _final(text):
    return AssistantTurn(text=text)


def _tool_result(session):
    return next(m["content"] for m in reversed(session.messages)
                if m.get("role") == "tool")


def _accept_all(config):
    config.permission_mode = "accept_all"   # 子代理只读/受限行为测试不弹确认


def test_task_explore_returns_report(config, tmp_path):
    _accept_all(config)
    llm = QueueLLM([
        _task_turn("explore", "看看工作区有什么"),
        _final("发现 1 个文件:README.md"),
        _final("主代理总结"),
    ])
    bridge, window, session = make(config, tmp_path, llm)
    bridge.send_task("看看工作区")
    assert wait_until(lambda: not bridge._busy)

    result = _tool_result(session)
    assert "侦察子代理完成" in result
    assert "发现 1 个文件" in result
    assert session.messages[-1]["content"] == "主代理总结"


def test_task_implement_writes_file(config, tmp_path):
    _accept_all(config)
    llm = QueueLLM([
        _task_turn("implement", "创建 s.txt 内容 hello"),
        AssistantTurn(tool_calls=[_call("implement·write_file",
                                         {"path": "s.txt", "content": "hello"}, "s1")]),
        _final("已创建 s.txt"),
        _final("子任务完成"),
    ])
    bridge, window, _ = make(config, tmp_path, llm)
    bridge.send_task("创建 s.txt")
    assert wait_until(lambda: not bridge._busy)

    assert (tmp_path / "s.txt").read_text(encoding="utf-8") == "hello"


def test_task_implement_confirm_flow(config, tmp_path):
    """confirm 模式:task 派发与子代理内部的写文件各确认一次。"""
    llm = QueueLLM([
        _task_turn("implement", "创建 s.txt 内容 hello"),
        AssistantTurn(tool_calls=[_call("implement·write_file",
                                         {"path": "s.txt", "content": "hello"}, "s1")]),
        _final("已创建 s.txt"),
        _final("子任务完成"),
    ])
    bridge, window, _ = make(config, tmp_path, llm)
    bridge.send_task("创建 s.txt")

    assert wait_until(lambda: len(window.of_type("confirm_request")) >= 1)
    bridge.confirm_reply("yes")                       # 确认 task 派发
    assert wait_until(lambda: len(window.of_type("confirm_request")) >= 2)
    bridge.confirm_reply("yes")                       # 确认子代理的 write_file
    assert wait_until(lambda: not bridge._busy)

    assert (tmp_path / "s.txt").read_text(encoding="utf-8") == "hello"
    assert window.of_type("confirm_request")[0]["tool"] == "task"
    assert window.of_type("confirm_request")[1]["tool"].startswith("implement·")


def test_task_explore_cannot_write(config, tmp_path):
    _accept_all(config)
    llm = QueueLLM([
        _task_turn("explore", "试着写个文件"),
        AssistantTurn(tool_calls=[_call("s1", {"path": "evil.txt", "content": "x"},
                                         "s1")]),
        _final("写不了,已放弃"),
        _final("好"),
    ])
    bridge, window, session = make(config, tmp_path, llm)
    bridge.send_task("试试写文件")
    assert wait_until(lambda: not bridge._busy)

    assert not (tmp_path / "evil.txt").exists()
    assert "写不了" in _tool_result(session)


def test_task_cannot_recurse(config, tmp_path):
    _accept_all(config)
    llm = QueueLLM([
        _task_turn("explore", "再派一个子代理"),
        AssistantTurn(tool_calls=[_call("s1", {"type": "explore", "task": "递归"},
                                         "s1")]),
        _final("递归被拒"),
        _final("ok"),
    ])
    bridge, window, session = make(config, tmp_path, llm)
    bridge.send_task("递归测试")
    assert wait_until(lambda: not bridge._busy)
    assert "递归被拒" in _tool_result(session)
    # 结构性验证:子代理工具集里没有 task
    from minicode.subagents import sub_tool_registry, SUBAGENT_TYPES
    sub_names = [t.name for t in sub_tool_registry(bridge.agent.registry,
                                                   SUBAGENT_TYPES["explore"])]
    assert "task" not in sub_names and "explore·glob" in sub_names


def test_task_unknown_type(config, tmp_path):
    _accept_all(config)
    llm = QueueLLM([
        _task_turn("fly", "起飞"),
        _final("ok"),
    ])
    bridge, window, session = make(config, tmp_path, llm)
    bridge.send_task("起飞")
    assert wait_until(lambda: not bridge._busy)
    assert "未知子代理类型" in _tool_result(session)


def test_task_sub_bash_disaster_hard_reject(config, tmp_path):
    # 保持 confirm 模式:若灾难命令未被硬拒绝,会弹第二个确认——以此断言
    llm = QueueLLM([
        _task_turn("implement", "删库"),
        AssistantTurn(tool_calls=[_call("s1", {"command": "rm -rf /"}, "s1")]),
        _final("被拒了"),
        _final("ok"),
    ])
    bridge, window, session = make(config, tmp_path, llm)
    bridge.send_task("危险命令")
    assert wait_until(lambda: len(window.of_type("confirm_request")) >= 1)
    bridge.confirm_reply("yes")                       # 只确认 task 派发
    assert wait_until(lambda: not bridge._busy, timeout=15)

    assert "被拒了" in _tool_result(session)
    assert len(window.of_type("confirm_request")) == 1   # rm -rf 未获得确认机会


def test_task_sub_steps_capped(config, tmp_path):
    _accept_all(config)
    class EndlessLLM:
        def __init__(self):
            self.consumed = 0

        def stream_chat(self, messages, tools, on_text=None, on_reasoning=None,
                        on_retry=None):
            self.consumed += 1
            if self.consumed == 1:
                return _task_turn("explore", "数一数工具调用次数")
            if 2 <= self.consumed <= 21:  # 子代理 20 步上限
                return AssistantTurn(tool_calls=[
                    _call(f"explore·glob", {"pattern": "**/*"}, f"g{self.consumed}")])
            return _final("全部结束")

    endless = EndlessLLM()
    bridge, window, session = make(config, tmp_path, endless)
    bridge.send_task("无限循环")
    assert wait_until(lambda: not bridge._busy, timeout=30)

    assert endless.consumed == 22   # 父 task 1 + 子 20(上限)+ 父收尾 1
    assert "(子代理已结束,但未返回文本报告)" in _tool_result(session)


# ------------------------------------------------------- 只读子代理并行派发


class RoutingLLM:
    """按会话内容分派回复,并记录并发峰值。

    并发子代理的消费顺序不确定,不能用全局队列(先到先得会把回复串台);
    每个子代理有独立的 system prompt 与各自的用户消息,据此路由才稳。

    gate:在"占着并发名额"的状态里调用(用完才释放)。假回复几乎是瞬时的,
    不加闸门就观察不到重叠——用 Barrier 卡住两路,才能真正证明是并发执行。
    """

    def __init__(self, route, gate=None, gate_when=None):
        self.route = route
        self.gate = gate
        self.gate_when = gate_when or (lambda messages: False)
        self.calls: list[list[dict]] = []
        self.live = 0
        self.max_live = 0
        self._lock = threading.Lock()

    def stream_chat(self, messages, tools, on_text=None, on_reasoning=None,
                    on_retry=None):
        with self._lock:
            self.calls.append([dict(m) for m in messages])
            self.live += 1
            self.max_live = max(self.max_live, self.live)
        try:
            if self.gate and self.gate_when(messages):
                self.gate()
            turn = self.route(messages)
            if on_text and turn.text:
                on_text(turn.text)
            return turn
        finally:
            with self._lock:
                self.live -= 1


def _is_subagent(messages) -> bool:
    head = _sys_of(messages)
    return "子代理" in head and "# 角色" in head


def _sys_of(messages) -> str:
    return str(messages[0].get("content") or "") if messages else ""


_BATCH_TURN = AssistantTurn(tool_calls=[
    _call("task", {"type": ["explore", "review"],
                   "task": ["找登录相关代码", "审查权限模块"]}, "b1")])


def test_task_batch_runs_readonly_subagents_in_parallel(config, tmp_path):
    _accept_all(config)

    def route(messages):
        head = _sys_of(messages)
        if "只读侦察子代理" in head:
            return _final("侦察报告:登录逻辑在 auth.py")
        if "审查子代理" in head:
            return _final("审查报告:权限判断缺边界检查")
        if any(m.get("role") == "tool" for m in messages):
            return _final("两路侦察都回来了")
        return _BATCH_TURN

    llm = RoutingLLM(route, gate=threading.Barrier(2).wait,
                     gate_when=_is_subagent)
    bridge, window, session = make(config, tmp_path, llm)
    bridge.send_task("并行调查")
    assert wait_until(lambda: not bridge._busy, timeout=15)

    result = _tool_result(session)
    assert "[并行子代理完成 · 2 个]" in result
    assert "侦察报告:登录逻辑在 auth.py" in result
    assert "审查报告:权限判断缺边界检查" in result
    # 报告按请求顺序合并(explore 在前,review 在后),不受完成先后影响
    assert result.index("侦察报告") < result.index("审查报告")
    assert "## 1. explore" in result and "## 2. review" in result
    assert llm.max_live >= 2, "两路只读子代理应在同一时刻并发执行"


def test_task_batch_keeps_subagent_streaming_out_of_the_chat(config, tmp_path):
    """并发子代理的流式文本不能插进主聊天区(会互相穿插成乱码)。"""
    _accept_all(config)

    def route(messages):
        head = _sys_of(messages)
        if "只读侦察子代理" in head:
            return _final("侦察流式噪声")
        if "审查子代理" in head:
            return _final("审查流式噪声")
        if any(m.get("role") == "tool" for m in messages):
            return _final("主代理结论")
        return _BATCH_TURN

    llm = RoutingLLM(route)
    bridge, window, session = make(config, tmp_path, llm)
    bridge.send_task("并行调查")
    assert wait_until(lambda: not bridge._busy, timeout=15)

    streamed = "".join(e["text"] for e in window.of_type("text_delta"))
    assert "侦察流式噪声" not in streamed
    assert "审查流式噪声" not in streamed
    assert "主代理结论" in streamed          # 主代理自己的输出照常流式
    # 工具卡片也只在主代理侧出现,子代理的不往聊天区画
    assert [e["name"] for e in window.of_type("tool_start")] == ["task"]


def test_task_batch_rejects_write_types(config, tmp_path):
    _accept_all(config)
    llm = RoutingLLM(lambda messages: (
        AssistantTurn(tool_calls=[_call("task", {
            "type": ["explore", "implement"],
            "task": ["看看代码", "顺手改一下"]}, "b1")])
        if not any(m.get("role") == "tool" for m in messages)
        else _final("被拒了")))
    bridge, window, session = make(config, tmp_path, llm)
    bridge.send_task("混合批次")
    assert wait_until(lambda: not bridge._busy, timeout=15)

    result = _tool_result(session)
    assert "只支持只读类型" in result and "implement" in result


def test_task_batch_validation(config, tmp_path):
    _accept_all(config)
    cases = [
        ({"type": ["explore", "review"], "task": ["只有一个"]}, "数量必须一致"),
        ({"type": ["explore"] * 5, "task": ["a"] * 5}, "一次最多并行"),
        ({"type": ["explore", "fly"], "task": ["a", "b"]}, "未知子代理类型"),
        ({"type": ["explore", ""], "task": ["a", "b"]}, "不能为空"),
        ({"type": 42, "task": "a"}, "必须是字符串或字符串数组"),
    ]
    for args, expect in cases:
        case_dir = tmp_path / expect[:6]
        case_dir.mkdir(parents=True, exist_ok=True)
        llm = RoutingLLM(lambda messages, _a=args: (
            AssistantTurn(tool_calls=[_call("task", _a, "b1")])
            if not any(m.get("role") == "tool" for m in messages)
            else _final("ok")))
        bridge, window, session = make(config, case_dir, llm)
        bridge.send_task("校验")
        assert wait_until(lambda: not bridge._busy, timeout=15), expect
        assert expect in _tool_result(session), (expect, _tool_result(session))


def test_task_single_string_unchanged(config, tmp_path):
    """向后兼容:type/task 传字符串仍是单发,流式输出照常进聊天区。"""
    _accept_all(config)
    llm = QueueLLM([
        _task_turn("explore", "看看工作区有什么"),
        _final("侦察字幕"),
        _final("主代理总结"),
    ])
    bridge, window, session = make(config, tmp_path, llm)
    bridge.send_task("看看工作区")
    assert wait_until(lambda: not bridge._busy)

    result = _tool_result(session)
    assert "[侦察子代理完成]" in result and "侦察字幕" in result
    assert not result.startswith("[并行子代理完成")
    assert "侦察字幕" in "".join(e["text"] for e in window.of_type("text_delta"))
