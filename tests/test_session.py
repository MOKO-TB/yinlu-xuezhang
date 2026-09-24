import os

from minicode.llm import AssistantTurn, ToolCall
from minicode.session import Session, list_sessions


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
