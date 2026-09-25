"""子代理(task 工具)测试:派发、只读隔离、递归拒绝、步数上限、只读并行。"""
import pytest
import json
import threading

from minicode.llm import AssistantTurn, ToolCall
from test_gui_bridge import make, wait_until


# 整文件打 slow:实测 51 s(2026-09-25 --durations=0)—— 慢在真线程/
# 真 socket/真超时,不是算法。日常只跑 `pytest -m "not slow"` 时它会被跳过。
pytestmark = pytest.mark.slow


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
    """子代理把 20 步用满时,现在**以一段收尾报告结束**,而不是留一句占位符。

    v0.9.40 D2 之前:步数耗尽 → 直接结束 → 父那边拿到的是
    "(子代理已结束,但未返回文本报告)",侦察了一整圈什么也没带回来。
    现在耗尽后补一次不带工具的调用,所以调用数 = 1(父派) + 20(子用满)
    + 1(子收尾) + 1(父最后一答)。多出那一次是**真花 token 的**,写清楚免得日后被当成回归。
    """
    _accept_all(config)
    class EndlessLLM:
        def __init__(self):
            self.consumed = 0
            self.tool_counts = []      # 每次请求带了几份工具定义

        def stream_chat(self, messages, tools, on_text=None, on_reasoning=None,
                        on_retry=None):
            self.consumed += 1
            self.tool_counts.append(len(tools))
            if self.consumed == 1:
                return _task_turn("explore", "数一数工具调用次数")
            if 2 <= self.consumed <= 21:  # 子代理 20 步上限
                return AssistantTurn(tool_calls=[
                    _call("explore·glob", {"pattern": "**/*"}, f"g{self.consumed}")])
            return _final("全部结束")

    endless = EndlessLLM()
    bridge, window, session = make(config, tmp_path, endless)
    bridge.send_task("无限循环")
    assert wait_until(lambda: not bridge._busy, timeout=30)

    assert endless.consumed == 23   # 父 1 + 子 20(上限)+ 子收尾 1 + 父收尾 1
    assert endless.tool_counts[21] == 0      # 第 22 次就是那次"合上工具"的收尾
    result = _tool_result(session)
    assert "全部结束" in result
    assert "未返回文本报告" not in result


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
        assert wait_until(lambda b=bridge: not b._busy, timeout=15), expect
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


# ------------------------------------------------------- 领域档与自动路由

def test_task_auto_routes_and_shows_the_reason(config, tmp_path):
    """type=auto:路由选档,命中什么词、要加载哪些技能一并回显给用户。"""
    _accept_all(config)
    llm = QueueLLM([
        _task_turn("auto", "帮我把这篇论文的提纲和参考文献理一下"),
        _final("按 paper-writing 的规矩给了提纲"),
        _final("主代理收尾"),
    ])
    bridge, window, session = make(config, tmp_path, llm)
    bridge.send_task("写论文")
    assert wait_until(lambda: not bridge._busy)

    result = _tool_result(session)
    assert "[写作子代理完成]" in result
    assert "派「写作」档" in result and "命中「" in result and "计分" in result
    assert "paper-writing" in result          # 指向的是已有技能,不是新写一套


def test_task_auto_without_features_says_so(config, tmp_path):
    """没命中领域特征时不硬派:退回 explore,并在理由里承认是没命中。"""
    _accept_all(config)
    llm = QueueLLM([
        _task_turn("auto", "你看下这个"),
        _final("先看了看目录"),
        _final("收尾"),
    ])
    bridge, window, session = make(config, tmp_path, llm)
    bridge.send_task("模糊请求")
    assert wait_until(lambda: not bridge._busy)

    result = _tool_result(session)
    assert "[侦察子代理完成]" in result
    assert "没命中任何领域关键词" in result


def test_task_explicit_type_gets_no_routing_note(config, tmp_path):
    """显式指定档位时不该多塞一段"我替你路由了"。"""
    _accept_all(config)
    llm = QueueLLM([
        _task_turn("review", "审一下这段代码"),
        _final("没有问题"),
        _final("收尾"),
    ])
    bridge, window, session = make(config, tmp_path, llm)
    bridge.send_task("审查")
    assert wait_until(lambda: not bridge._busy)
    assert "派「" not in _tool_result(session)


def test_domain_tool_sets_are_prefixed_and_read_only_isolated(config, tmp_path):
    """english 是只读档:拿不到写工具;code 档能拿到,前缀按档位来。"""
    from minicode.subagents import sub_tool_registry, type_spec
    from minicode.tools.registry import build_default_registry
    reg = build_default_registry()

    english = [t.name for t in sub_tool_registry(reg, type_spec("english", tmp_path))]
    assert english and all(n.startswith("english·") for n in english)
    assert "english·write_file" not in english
    assert "english·search_knowledge" in english
    assert "english·task" not in english          # 任何档都不能递归派活

    code = [t.name for t in sub_tool_registry(reg, type_spec("code", tmp_path))]
    assert "code·write_file" in code and "code·bash" in code


def test_batch_parallel_allows_read_only_domain_but_not_writers(config, tmp_path):
    _accept_all(config)
    llm = QueueLLM([
        AssistantTurn(tool_calls=[_call("task", {
            "type": ["english", "writing"],
            "task": ["查四级时间", "写论文提纲"]}, "b1")]),
        _final("收尾"),
    ])
    bridge, window, session = make(config, tmp_path, llm)
    bridge.send_task("混合并发")
    assert wait_until(lambda: not bridge._busy)
    result = _tool_result(session)
    assert "批量并行只支持只读类型" in result and "writing" in result
    assert "english" not in result.split("但收到了")[1]


def test_subagent_timeout_is_visible_and_not_called_a_failure(config, tmp_path):
    """子代理超时:报告说"不再等它",不写成失败,也不谎称已停止它。"""
    import time as _time
    _accept_all(config)
    config.subagent_time_limit_seconds = 1

    class SlowSubLLM:
        """子代理那一路慢到超时;父代理两问都秒回。"""

        def __init__(self):
            self.parent_calls = 0

        def stream_chat(self, messages, tools, on_text=None, reasoning=None,
                        on_retry=None, **kw):
            system = str(messages[0].get("content")) if messages else ""
            if "只读侦察子代理" in system:
                _time.sleep(3)
                return _final("迟到的报告")
            self.parent_calls += 1
            return (_task_turn("explore", "慢慢看") if self.parent_calls == 1
                    else _final("收尾"))

    bridge, window, session = make(config, tmp_path, SlowSubLLM())
    bridge.send_task("超时测试")
    assert wait_until(lambda: not bridge._busy, timeout=20)

    result = _tool_result(session)
    assert "超时未返回" in result and "上限 1 秒" in result
    assert "不算它失败" in result and "仍在后台跑完" in result
    assert "迟到的报告" not in result      # 迟到的结果不假装收到
