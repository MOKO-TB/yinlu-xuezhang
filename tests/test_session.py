import os

from minicode.llm import AssistantTurn, ToolCall
from minicode.session import Session, list_sessions, outbound, prune_sessions, to_markdown


def test_roundtrip(tmp_path):
    s1 = Session(system_prompt="sys", log_dir=tmp_path)
    s1.add_user("任务 A")
    s1.add_assistant(AssistantTurn(tool_calls=[ToolCall(
        id="c1", name="bash", arguments='{"command": "ls"}')]))
    s1.add_tool_result("c1", "bash", "退出码: 0")
    s1.add_assistant(AssistantTurn(text="完成"))

    s2 = Session(system_prompt="sys2", log_dir=tmp_path)
    n = s2.load_from(s1.log_path)
    assert n == 4
    assert s2.messages[0]["role"] == "system"      # 恢复时保留当前 system
    assert s2.messages[1:] == s1.messages[1:]
    assert s2.log_path == s1.log_path               # 后续继续写入同一文件


def test_clear_keeps_system(tmp_path):
    s = Session(system_prompt="sys", log_dir=tmp_path)
    s.add_user("hi")
    s.clear()
    assert [m["role"] for m in s.messages] == ["system"]


def test_load_skips_marker_lines(tmp_path):
    s1 = Session(system_prompt="sys", log_dir=tmp_path)
    s1.add_user("hi")
    s1.clear()
    s2 = Session(system_prompt="sys", log_dir=tmp_path)
    n = s2.load_from(s1.log_path)
    assert n == 1  # cleared 标记行不计入消息


def test_list_sessions(tmp_path):
    s1 = Session(system_prompt="sys", log_dir=tmp_path)
    s1.add_user("第一个任务:修复测试")
    s1.add_assistant(AssistantTurn(text="好的"))
    s2 = Session(system_prompt="sys", log_dir=tmp_path)
    s2.add_user("第二个任务")

    # 强制 mtime 顺序,避免同秒写入导致顺序不稳定
    os.utime(s1.log_path, (1000, 1000))
    os.utime(s2.log_path, (2000, 2000))

    summaries = list_sessions(tmp_path)
    assert [s.path for s in summaries] == [s2.log_path, s1.log_path]
    assert summaries[0].last_user_text == "第二个任务"
    assert summaries[1].n_messages == 2
    assert "修复测试" in summaries[1].last_user_text


def test_list_sessions_empty(tmp_path):
    assert list_sessions(tmp_path) == []


def test_lazy_log_creation(tmp_path):
    s = Session(system_prompt="sys", log_dir=tmp_path)
    assert s.log_path is None                          # 未发消息不建文件
    assert list(tmp_path.glob("*.jsonl")) == []
    s.clear()                                          # 空会话上清空也不建文件
    assert list(tmp_path.glob("*.jsonl")) == []
    s.add_user("hi")
    assert s.log_path is not None and s.log_path.exists()


def test_fresh_start_resets_log_pointer(tmp_path):
    s = Session(system_prompt="sys", log_dir=tmp_path)
    s.add_user("hi")
    old = s.log_path
    s.fresh_start()
    assert [m["role"] for m in s.messages] == ["system"]
    assert s.log_path is None
    assert old.exists()                                # 旧留档保留,由调用方处置
    s.add_user("again")
    assert s.log_path != old                           # 新消息写入新文件


# -------------------------------------------------- 消息时间戳与保留策略(v0.9.66)

def test_every_appended_message_carries_ts(tmp_path):
    """v0.9.66:每条真实落档的消息带 ts;system 提示词不是"发生的事",不强求。"""
    import re
    s = Session(system_prompt="sys", log_dir=tmp_path)
    s.add_user("任务 A")
    s.add_tool_result("c1", "bash", "ok")
    for m in s.messages:
        if m["role"] == "system":
            continue
        assert re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", m.get("ts", "")), m


def test_outbound_strips_ts_and_never_mutates_session():
    """发往模型端点前剥 ts;剥离是拷贝,session.messages 本体不动。"""
    s = Session(system_prompt="sys", log_dir=None)
    s.add_user("问题")
    out = outbound(s.messages)
    assert all("ts" not in m for m in out)
    assert all("ts" in m for m in s.messages if m["role"] != "system")


def test_to_markdown_carries_per_message_time(tmp_path):
    s = Session(system_prompt="sys", log_dir=tmp_path)
    s.add_user("任务 A")
    s.add_assistant(AssistantTurn(text="完成"))
    md = to_markdown(s)
    assert "## 我(" in md and "## 引路学长(" in md     # 新留档:逐条带时刻
    # 旧留档没有 ts:导出照旧能出,不带时刻
    old = [{k: v for k, v in m.items() if k != "ts"} for m in s.messages]
    s2 = Session(system_prompt="sys", log_dir=None)
    s2.messages = list(old)
    md2 = to_markdown(s2)
    assert "## 我\n" in md2 and "(" not in md2.split("## 我\n")[1].split("\n")[0]


def test_prune_sessions_keeps_newest_and_protects_current(tmp_path):
    import time as _t
    paths = []
    for i in range(5):
        p = tmp_path / f"session_2026092{i}_090000.jsonl"
        p.write_text('{"role": "user", "content": "x"}\n', encoding="utf-8")
        os.utime(p, (_t.time() - (100 - i), _t.time() - (100 - i)))
        paths.append(p)
    protect = {paths[4]}                    # 当前会话恰好是最新,protect 只是兜底
    removed = prune_sessions(tmp_path, keep_last=2, protect=protect)
    assert len(removed) == 3
    left = sorted(p.name for p in tmp_path.glob("session_*.jsonl"))
    assert left == ["session_20260923_090000.jsonl", "session_20260924_090000.jsonl"]
    assert prune_sessions(tmp_path, keep_last=0) == []   # 0 = 不清理


def test_prune_sessions_never_touches_the_protected_file(tmp_path):
    import time as _t
    p1 = tmp_path / "session_20260920_090000.jsonl"
    p2 = tmp_path / "session_20260921_090000.jsonl"
    for p, age in ((p1, 200), (p2, 100)):      # 显式 mtime:同秒创建会让排序抖动
        p.write_text("{}\n", encoding="utf-8")
        os.utime(p, (_t.time() - age, _t.time() - age))
    # 只留 1 个,但唯一被保护的是旧的 p1:宁可多留,不删当前会话
    removed = prune_sessions(tmp_path, keep_last=1, protect={p1})
    assert removed == []
    assert p1.exists() and p2.exists()
