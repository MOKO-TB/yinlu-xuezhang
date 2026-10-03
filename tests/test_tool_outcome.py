"""v0.9.39 Z1 第一步:一次工具调用「成没成」只剩一处判断。

用户点名要抄 ZCode 的「工具钩子生命周期」。读现状后的实测结论(写进 HANDOFF §6):
派发点其实**只有一个**(`agent.py` 里 `tool.handler(...)` 全仓仅一处),所以这一轮
不去铺框架,只把那条直线上**已经在漏**的一件小口子收掉:

    同一句 `result.startswith(("错误", "工具执行出错", "用户拒绝"))`,原来写着三份 ——
    `gui_ui.tool_done`(决定聊天里那张卡片打 ✓ 还是 ✗)、`ui.tool_done`(终端那行)、
    `activity.FAILED_PREFIXES`(决定右侧「任务监控」时间线把这一步标成失败)。

三份各自抄的后果不是难看,是**会不一致**:新增一种失败措辞或改一句文案时,完全可能
出现"卡片 ✓、时间线写着失败"——那正是用户反复强调的「不能只用于装饰」的反面。
所以这里钉四件事:

1. 判定的**行为表**逐条钉死(含刻意保留的旧边界,收口不改判法);
2. 那串字面量在 `minicode/` 下**只许出现在 toolsum.py**,前端也不许抄第四份;
3. 三个消费者确实调的是同一个函数;
4. 拿**真的 Agent 跑真的四条失败出口 + 一条成功出口**,证明界面收到的 `ok` 与
   `toolsum.is_failure` 判的是同一件事(前一条只是文本对账,这条才是行为对账)。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from minicode import activity, gui_ui, toolsum
from minicode.agent import Agent
from minicode.llm import AssistantTurn, ToolCall
from minicode.permission import PermissionGate
from minicode.session import Session
from minicode.tools.base import Tool, ToolContext

ROOT = Path(__file__).resolve().parents[1]

FAILED_TUPLE = '"错误", "工具执行出错", "用户拒绝"'


def _py_files():
    pkg = ROOT / "minicode"
    assert pkg.is_dir(), f"找不到包目录 {pkg}"
    return sorted(pkg.rglob("*.py"))


# ---------------------------------------------------------------- 1. 行为表

@pytest.mark.parametrize("result, want", [
    ("错误: 工具参数不是有效 JSON(Expecting value)。原始参数: {坏", True),
    ("错误: 未找到工具 'no_such_tool'。可用工具: bash", True),
    ("错误: 该操作被安全策略硬拒绝(可能造成不可恢复的破坏),已阻止执行,请勿重试。", True),
    ("工具执行出错: RuntimeError: boom", True),
    ("用户拒绝了本次工具调用。请停止重试,询问用户希望如何处理。", True),
    ("已写入 hello.txt(2 字节)", False),
    ("这里没有失败", False),
    ("", False),
    # 只认**开头**,不做全文匹配:正文里出现"错误"两个字不算失败。
    ("检索完成,其中 1 条记录标注为错误示例", False),
    # 非字符串不当失败(宁可标对,不标错):
    (None, False),
    (123, False),
    (["错误: 像是失败"], False),
    # 已知边界(刻意保留):判定按字面开头,不去前导空白。收口这一轮的规矩是
    # **只减份数、不改判法** —— 三处消费者原本就都这样,若现在顺手 lstrip,
    # 就等于在同一刀里换语义,红的时候分不清是哪件事。要放宽请单独一轮 + 配测试。
    ("  错误: 前面有空格", False),
    ("\n错误: 前面有换行", False),
])
def test_is_failure_matches_the_documented_table(result, want):
    assert toolsum.is_failure(result) is want


def test_failed_prefixes_are_the_same_tuple_activity_uses():
    """activity 里那个名字现在只是**沿用**,不是第二份真相。"""
    assert activity.FAILED_PREFIXES is toolsum.FAILED_PREFIXES
    assert activity._failed("错误: x") is True
    assert activity._failed("写好了") is False


# ---------------------------------------------------------------- 2. 只剩一份

def test_the_failure_literal_exists_in_exactly_one_module():
    """"三份"变成"一份"这件事,直接扫源码。"""
    hits = [f.relative_to(ROOT).as_posix()
            for f in _py_files() if FAILED_TUPLE in f.read_text(encoding="utf-8")]
    assert hits == ["minicode/toolsum.py"], f"失败前缀又被抄了一份: {hits}"


def test_no_consumer_reimplements_the_check():
    """三个消费者一律不许再自己 `startswith(`。"""
    for rel in ("minicode/gui_ui.py", "minicode/ui.py", "minicode/activity.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert not re.search(r"startswith\(\(?[\"']错误", src), \
            f"{rel} 里又有一份自己的判定"
        assert "toolsum.is_failure" in src or "FAILED_PREFIXES = toolsum" in src, \
            f"{rel} 不再问统一口径了"


def test_frontend_does_not_start_a_fourth_copy():
    """前端只信 `tool_done` 推过去的 ok 字段;在那里再判一次 = 第四份真相。"""
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    assert FAILED_TUPLE not in html
    assert "工具执行出错" not in html, "前端开始自己认失败文案了"


# ---------------------------------------------------------------- 3. 展示口径

def test_error_preview_is_empty_for_success_and_capped_for_failure():
    assert toolsum.error_preview("一切正常") == ""
    assert toolsum.error_preview(None) == ""
    long_fail = "错误: " + "啊" * 500
    got = toolsum.error_preview(long_fail)
    assert len(got) == toolsum.ERROR_PREVIEW, "截断长度是界面排版依赖的数,别漂"
    assert got == long_fail[:toolsum.ERROR_PREVIEW]
    # 与 toolsum 同一个判据:成功就一定是空,不再各写各的
    assert toolsum.error_preview("工具执行出错: X") == "工具执行出错: X"


# ---------------------------------------------- 4. 真跑一遍:界面收到的 ok 同源

class FakeLLM:
    def __init__(self, turns):
        self.turns = list(turns)

    def stream_chat(self, messages, tools, on_text=None, on_reasoning=None,
                    on_retry=None, **kwargs):
        return self.turns.pop(0)


class RecordingGuiUI(gui_ui.GuiUI):
    """真 GuiUI,只把 push 换成记账 —— 判定的落点就在 push 出去的那两个字段上。"""

    def __init__(self):
        super().__init__()
        self.events: list[dict] = []

    def push(self, type_: str, **payload) -> None:
        payload["type"] = type_
        self.events.append(payload)

    def confirm_tool(self, tool, args, reason="", note=""):   # 代用户点按钮
        return getattr(self, "choice", "yes")

    def done_events(self) -> list[dict]:
        return [e for e in self.events if e["type"] == "tool_done"]


def _boom_ctx(tmp_path, config) -> ToolContext:
    return ToolContext(workspace=tmp_path, config=config)


def _agent(ui, registry, config, ctx):
    return Agent(llm=FakeLLM(ui._turns), registry=registry,
                 session=Session(system_prompt="sys"),
                 gate=PermissionGate(config), ui=ui, ctx=ctx)


def _tool(name, handler, read_only=True):
    return Tool(name=name, description="d",
                parameters={"type": "object", "properties": {}, "required": []},
                handler=handler, read_only=read_only)


def _one_call_turn(name, arguments="{}"):
    """两轮:第一轮模型要求调这个工具,第二轮它说一句就收工。"""
    return [AssistantTurn(tool_calls=[ToolCall(id="c1", name=name,
                                               arguments=arguments)]),
            AssistantTurn(text="结束")]


def _run(tmp_path, config, ui, registry):
    agent = _agent(ui, registry, config, _boom_ctx(tmp_path, config))
    agent.run_turn("跑一次")
    results = [m for m in agent.session.messages if m["role"] == "tool"]
    assert len(results) == 1, f"这一次调用没留下 tool 消息: {results}"
    return str(results[0]["content"]), ui.done_events()


def test_raising_tool_reports_failure_everywhere_the_same(tmp_path, config):
    def boom(ctx, **kw):
        raise RuntimeError("boom")

    ui = RecordingGuiUI()
    ui._turns = _one_call_turn("boom")
    result, dones = _run(tmp_path, config, ui, [_tool("boom", boom)])

    assert result.startswith("工具执行出错"), result
    assert toolsum.is_failure(result) is True
    assert len(dones) == 1 and dones[0]["ok"] is False, \
        f"判定说失败,推给界面的却是对的: {dones}"
    assert dones[0]["error_preview"] == result      # 同一个函数,不是各截一份


def test_pre_execution_refusals_are_failures_with_no_card(tmp_path, config):
    """没开始跑就被挡掉的三种(未注册 / 参数坏 JSON / 硬拒绝):结果仍是失败口径。

    跑这一条跑出来的**实情**(所以断言写成这样):这三条都在 `tool_start` **之前**
    return,所以界面根本不会收到 `tool_done`,聊天里也就没有那张 ✓/✗ 卡片 ——
    统一口径管的是"有判定之处必须同一个结论",不是"每一步都必须有卡片"。
    右侧时间线不受影响:它是从会话消息里判的(见下面那条测试)。
    """
    def never(ctx, **kw):
        raise AssertionError("这条不该被执行")

    cases = [("没有这个工具", "{}"), ("never", "{坏 JSON")]
    for name, arguments in cases:
        ui = RecordingGuiUI()
        ui._turns = _one_call_turn(name, arguments)
        result, dones = _run(tmp_path, config, ui, [_tool("never", never)])
        assert toolsum.is_failure(result) is True, f"{name}: {result}"
        assert dones == [], f"{name} 根本没开跑,不该有 tool_done: {dones}"
def test_user_rejection_is_a_failure_not_a_crash(tmp_path, config):
    """用户点拒绝 = 这一步没成(不是崩),但同样在 `tool_start` 之前 return。"""
    def never(ctx, **kw):
        raise AssertionError("用户已经拒绝了,不该再被执行")

    ui = RecordingGuiUI()
    ui.choice = "no"                                # 确认框里点拒绝
    ui._turns = _one_call_turn("write_it")
    write = _tool("write_it", never, read_only=False)   # 非只读 → 走确认
    result, dones = _run(tmp_path, config, ui, [write])

    assert result.startswith("用户拒绝"), result
    assert toolsum.is_failure(result) is True
    assert dones == [], f"没开跑就不该有卡片: {dones}"
    # 卡片缺席不等于这一行没地方说真话:右侧时间线是从**会话消息**里判的,
    # 它照样把这次拒绝标成失败(两处用的是同一个 is_failure)。
    assert _timeline_statuses(_session_with(result, "write_it")) == ["failed"]
def test_successful_call_is_ok_and_carries_no_error_text(tmp_path, config):
    def ok_tool(ctx, **kw):
        return "已写入 hello.txt(2 字节)"

    ui = RecordingGuiUI()
    ui._turns = _one_call_turn("ok_tool")
    result, dones = _run(tmp_path, config, ui, [_tool("ok_tool", ok_tool)])

    assert toolsum.is_failure(result) is False
    assert dones[0]["ok"] is True
    assert dones[0]["error_preview"] == "", "成功那一步不该带错误原文(界面会多一行)"


def test_monitor_timeline_and_card_agree_on_the_same_run(tmp_path, config):
    """同一次调用,时间线(activity)与工具卡片(gui_ui)必须同一个结论。

    这正是这次收口的**目的**:两份各写各的时,最能出现的症状就是两处自相矛盾 ——
    聊天里那张卡片打 ✓,右侧「任务监控」却写着失败(或反过来)。
    """
    def boom(ctx, **kw):
        raise RuntimeError("boom")

    ui = RecordingGuiUI()
    ui._turns = _one_call_turn("boom")
    result, dones = _run(tmp_path, config, ui, [_tool("boom", boom)])

    statuses = _timeline_statuses(_session_with(result))

    assert dones[0]["ok"] is False, "卡片那侧的判断变了?"
    assert statuses == ["failed"], f"时间线没有把这一步标成失败: {statuses}"
    assert activity._failed(result) is True


def _session_with(result: str, name: str = "boom") -> Session:
    """造一份"调过一次、结果就这一句"的会话,喂给时间线。"""
    session = Session(system_prompt="sys")
    session.add_assistant(AssistantTurn(tool_calls=[
        ToolCall(id="c1", name=name, arguments="{}")]))
    session.add_tool_result("c1", name, result)
    return session


def _timeline_statuses(session) -> list[str]:
    return _all_statuses(activity.timeline(session.messages))


def _all_statuses(node) -> list[str]:
    """从 timeline 那份嵌套字典里把每一行的 status 抠出来(不写死它套几层)。"""
    out: list[str] = []
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if isinstance(cur.get("tool"), str) and "status" in cur:
                out.append(str(cur["status"]))
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return out
