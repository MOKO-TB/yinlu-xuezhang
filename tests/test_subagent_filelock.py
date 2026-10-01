"""并行写子代理的文件路径闸(v0.9.98):"各路负责不同文件"从口头规矩变成程序闸。

三组测试各管一层:
① 占用表与闸的**语义**(纯单元,不碰 LLM):先到先得、别人被拦、本路重入放行、
   不同路径互不干扰、相对/绝对/大小写归一化成同一个键;
② **接线**:并行批次真的把同一张表与互不相同的路标签传给每一路
   (monkeypatch run_subagent 捕获参数;explore+implement 与单发不传表);
③ **端到端**:两路 implement 真派发写同一个文件,恰好一路被拦下且另一路内容
   完整落盘 —— 闸没接线时两路都会写成功,这条必红。
"""
import os
from types import SimpleNamespace

import pytest

from minicode.llm import AssistantTurn, ToolCall
from minicode import subagents
from minicode.subagents import (_BatchFileLocks, _guard_write_tools, _lock_key)
from minicode.tools.base import Tool
from minicode.toolsum import is_failure
from test_gui_bridge import make, wait_until
from test_subagent import RoutingLLM, _call, _final, _accept_all, _sys_of

# ③ 要真线程真派发,与 test_subagent 同一条慢车道
pytestmark = pytest.mark.slow


def _write_tool(recorder: list):
    """一只假的 write_file:真往磁盘写,记录每次真实落盘。"""
    def handler(ctx, path="", content="", **_):
        target = os.path.join(str(ctx.workspace), path)
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
        recorder.append(path)
        return f"已写入 {path}"
    return Tool(name="implement·write_file", description="", parameters={},
                handler=handler)


def _read_tool():
    return Tool(name="explore·read_file", description="", parameters={},
                handler=lambda ctx, **k: "内容")


# ---------------- ① 语义 ----------------

def test_lock_key_matches_relative_absolute_and_case(tmp_path):
    assert _lock_key("a/b.txt", tmp_path) == _lock_key(str(tmp_path / "a" / "b.txt"), tmp_path)
    if os.name == "nt":     # Windows 不分大小写;POSIX 上 normcase 是恒等,不能假钉
        assert _lock_key("A.TXT", tmp_path) == _lock_key("a.txt", tmp_path)


def test_try_acquire_first_wins_other_blocked_same_owner_reenters(tmp_path):
    locks = _BatchFileLocks()
    assert locks.try_acquire("race.txt", tmp_path, "第1路[implement]") == ""
    # 别的路来写同一个文件:报出持有者
    assert "第1路" in locks.try_acquire("race.txt", tmp_path, "第2路[implement]")
    # 本路重入(先写再改)放行
    assert locks.try_acquire("race.txt", tmp_path, "第1路[implement]") == ""
    # 不同文件互不影响
    assert locks.try_acquire("other.txt", tmp_path, "第2路[implement]") == ""


def test_guard_blocks_other_route_and_keeps_first_content_intact(tmp_path):
    locks = _BatchFileLocks()
    writes: list = []
    tool = _write_tool(writes)
    read_tool = _read_tool()
    ctx = SimpleNamespace(workspace=str(tmp_path))

    a = _guard_write_tools([tool, read_tool], locks, "第1路[implement]")
    b = _guard_write_tools([tool], locks, "第2路[implement]")

    # 写工具被包成替身,非写工具原样透传(同一个对象,不是包了一层的替身)
    assert a[0] is not tool and a[1] is read_tool

    ok_a = a[0].handler(ctx, path="race.txt", content="甲写的")
    assert "已写入" in ok_a and (tmp_path / "race.txt").read_text(encoding="utf-8") == "甲写的"

    blocked = b[0].handler(ctx, path="race.txt", content="乙写的")
    # 后到的拿到指名道姓的报错,文件还是先写那路的内容
    assert "第1路" in blocked and "race.txt" in blocked
    assert is_failure(blocked), "拦下的回话必须被口径判为失败,监控栏才标得出失败"
    assert (tmp_path / "race.txt").read_text(encoding="utf-8") == "甲写的"
    assert writes == ["race.txt"], "被拦的那次调用绝不许真落到磁盘"

    # 本路重入与另一路径不受影响
    assert "已写入" in a[0].handler(ctx, path="race.txt", content="甲改的")
    assert "已写入" in b[0].handler(ctx, path="mine.txt", content="乙的")


# ---------------- ② 接线 ----------------

def _dispatch(config, tmp_path, types, tasks):
    """真派发一批 task,但 run_subagent 换成捕获参数的假身(不跑真子代理)。"""
    _accept_all(config)     # confirm 档下连 task 本身都要弹确认框,没人回答就等到超时
    llm = RoutingLLM(lambda messages: (
        _final("主代理收尾")
        if any(m.get("role") == "tool" for m in messages)
        else AssistantTurn(tool_calls=[_call(
            "task", {"type": types, "task": tasks}, "b1")])))
    bridge, _w, session = make(config, tmp_path, llm)
    bridge.send_task("接线")
    assert wait_until(lambda: not bridge._busy, timeout=15)
    return session


def test_batch_with_two_writers_shares_one_lock_table(config, tmp_path, monkeypatch):
    seen = []

    def spy(ctx, spec, task_text, llm, ui, cfg, registry, **kw):
        seen.append((spec.type, kw.get("route_label", ""), kw.get("file_locks")))
        return f"{spec.type} 报告"

    monkeypatch.setattr(subagents, "run_subagent", spy)
    _dispatch(config, tmp_path, ["implement", "implement"], ["甲", "乙"])

    assert len(seen) == 2
    assert {t for t, _r, _f in seen} == {"implement"}
    labels = {r for _t, r, _f in seen}
    assert labels == {"第1路[implement]", "第2路[implement]"}, labels
    tables = {id(f) for _t, _r, f in seen}
    assert len(tables) == 1 and None not in tables, "两路必须共用同一张占用表"


def test_mixed_and_single_dispatch_get_no_lock_table(config, tmp_path, monkeypatch):
    seen = []

    def spy(ctx, spec, task_text, llm, ui, cfg, registry, **kw):
        seen.append((spec.type, kw.get("file_locks")))
        return f"{spec.type} 报告"

    monkeypatch.setattr(subagents, "run_subagent", spy)
    # 探索+实现混编:只有一路能写,没有竞争对象
    _dispatch(config, tmp_path, ["explore", "implement"], ["查", "写"])
    assert len(seen) == 2 and all(f is None for _t, f in seen)

    # 单发:连批次都不是
    seen.clear()
    _dispatch(config, tmp_path, "implement", "单写")
    assert len(seen) == 1 and seen[0][1] is None


# ---------------- ③ 端到端 ----------------

def test_batch_two_writers_same_file_exactly_one_blocked(config, tmp_path):
    """两路真派发写同一个文件:恰好一路被闸拦下、报告里说得出被拦,
    另一路的内容完整落盘。闸没接线时两路都会写成功 —— 这条必红。"""
    _accept_all(config)

    def route(messages):
        head = _sys_of(messages)
        if "实现子代理" not in head:            # 主代理
            if any(m.get("role") == "tool" for m in messages):
                return _final("主代理收尾")
            return AssistantTurn(tool_calls=[_call("task", {
                "type": ["implement", "implement"],
                "task": ["把 race.txt 写成 甲写的", "把 race.txt 写成 乙写的"]}, "b1")])
        # 子代理:先看自己上一步的工具结果 —— 被闸拦下就如实收尾
        for m in reversed(messages):
            if m.get("role") == "tool":
                if "程序闸" in str(m.get("content") or ""):
                    return _final("被闸拦下,未写入")
                break
        task_text = str(messages[1].get("content") or "")
        content = "甲写的" if "甲" in task_text else "乙写的"
        return AssistantTurn(tool_calls=[_call(
            "implement·write_file", {"path": "race.txt", "content": content}, "w1")])

    bridge, _w, session = make(config, tmp_path, RoutingLLM(route))
    bridge.send_task("同文件竞争")
    assert wait_until(lambda: not bridge._busy, timeout=15)

    result = next(m["content"] for m in reversed(session.messages)
                  if m.get("role") == "tool")
    assert "[并行子代理完成 · 2 个]" in result
    assert result.count("被闸拦下") == 1, "必须恰好一路被拦(全拦 = 先到的也被挡;全放 = 闸没接线)"
    content = (tmp_path / "race.txt").read_text(encoding="utf-8")
    assert content in ("甲写的", "乙写的")
