"""子代理(task 工具)测试:派发、只读隔离、递归拒绝、步数上限。"""
import json

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
