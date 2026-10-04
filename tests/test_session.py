import os

from minicode.llm import AssistantTurn, ToolCall
import json

from minicode import session as session_mod
from minicode.session import (Session, fork_log_file, list_sessions,
                              outbound, prune_sessions, to_markdown)
from minicode.tools.base import Tool


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


# ------------------------------------------- 运行轨迹导出(v0.9.98,§6 #22 剩项)

def _trace_messages():
    return [
        {"role": "system", "content": "系统提示词", "ts": "2026-10-01 10:00:00"},
        {"role": "user", "content": "帮我建一个文件", "ts": "2026-10-01 10:00:05"},
        {"role": "assistant", "content": "好,我用 write_file。",
         "tool_calls": [{"id": "c1", "function": {
             "name": "write_file",
             "arguments": '{"path": "a.txt", "content": "内容"}'}}],
         "ts": "2026-10-01 10:00:06"},
        {"role": "tool", "tool_call_id": "c1", "name": "write_file",
         "content": "已写入 a.txt", "ts": "2026-10-01 10:00:07"},
        {"role": "assistant", "content": "写好了。", "ts": "2026-10-01 10:00:08"},
    ]


def test_export_trace_writes_replayable_json(tmp_path):
    dest = tmp_path / "运行轨迹-1.json"
    r = session_mod.export_trace(_trace_messages(), dest, app_version="0.9.98")
    assert r["ok"] and r["count"] == 5 and r["tool_calls"] == 1
    data = json.loads(dest.read_text(encoding="utf-8"))
    assert data["format"] == session_mod.TRACE_FORMAT
    assert data["app_version"] == "0.9.98"
    roles = [m["role"] for m in data["messages"]]
    assert roles == ["system", "user", "assistant", "tool", "assistant"]
    call = data["messages"][2]["tool_calls"][0]
    assert call["name"] == "write_file" and "a.txt" in call["arguments"]
    assert data["messages"][3]["tool_call_id"] == "c1"
    assert all("ts" in m for m in data["messages"]), "v0.9.66 的消息级时刻要带进轨迹"


def test_export_trace_marks_truncation_instead_of_silently_cutting(tmp_path):
    long_content = "x" * (session_mod.TRACE_CONTENT_LIMIT + 500)
    long_args = "y" * (session_mod.TRACE_ARGS_LIMIT + 500)
    msgs = [{"role": "user", "content": long_content},
            {"role": "assistant", "tool_calls": [
                {"id": "c1", "function": {"name": "bash", "arguments": long_args}}]}]
    dest = tmp_path / "t.json"
    assert session_mod.export_trace(msgs, dest)["ok"]
    data = json.loads(dest.read_text(encoding="utf-8"))
    assert "截断,原" in data["messages"][0]["content"]
    assert "截断,原" in data["messages"][1]["tool_calls"][0]["arguments"]
    assert len(data["messages"][0]["content"]) < len(long_content)


def test_export_trace_tool_reports_and_warns_about_privacy(tmp_path):
    """处理器:文件落工作区根,回话里必须带着「原文含个人数据,交人前过一眼」。"""
    from types import SimpleNamespace
    from minicode.session import export_trace_tool
    sess = SimpleNamespace(messages=_trace_messages())
    ctx = SimpleNamespace(workspace=tmp_path, session=sess)
    out = export_trace_tool(ctx, reason="报障用")
    assert "已导出 5 条消息" in out and "过一眼" in out
    files = list(tmp_path.glob("运行轨迹-*.json"))
    assert len(files) == 1 and json.loads(files[0].read_text(encoding="utf-8"))["count"] == 5

    # 没有会话/还是空的:如实说,不写半个空文件
    before = list(tmp_path.glob("运行轨迹-*.json"))
    empty = SimpleNamespace(workspace=tmp_path, session=SimpleNamespace(messages=[]))
    assert "没有可导出" in export_trace_tool(empty, reason="")
    assert "没有可导出" in export_trace_tool(SimpleNamespace(workspace=tmp_path), reason="")
    assert list(tmp_path.glob("运行轨迹-*.json")) == before, "失败的调用不许落文件"


def test_export_trace_excluded_from_subagent_tools():
    """轨迹是主对话的会话记录:子代理的 ctx 带着父会话,让它导就是张冠李戴。"""
    from minicode.subagents import sub_tool_registry, SUBAGENT_TYPES
    from minicode.tools.registry import build_default_registry
    reg = build_default_registry() + [
        Tool(name="export_trace", description="", parameters={},
             handler=lambda ctx, **k: "")]
    for key in ("explore", "implement"):
        names = [t.name.rsplit("·", 1)[-1]
                 for t in sub_tool_registry(reg, SUBAGENT_TYPES[key])]
        assert "export_trace" not in names


def test_session_field_wired_into_runner_ctx(tmp_path):
    """装配层必须把 Session 挂进 ctx:漏了 = 模型调 export_trace 永远拿"没有可导出"。"""
    from minicode.config import Config, Provider
    from minicode.app_setup import build_runner_agent
    cfg = Config(providers=[Provider(id="t", name="T", base_url="https://x/v1",
                                     api_key="k", model="m")],
                 active_provider_id="t")
    agent, sess = build_runner_agent(cfg, tmp_path, ui=None, llm=None,
                                     sessions_dir=tmp_path / "s")
    assert agent.ctx.session is sess


# ------------------------------------------- 会话自动起名(v0.9.98)

def test_derive_title_takes_first_meaningful_line():
    assert session_mod.derive_title("帮我写一份蓝桥杯报名材料,越快越好\n第二行不算") \
        == "帮我写一份蓝桥杯报名材料,越快越好"[:24] or "蓝桥杯" in \
        session_mod.derive_title("帮我写一份蓝桥杯报名材料\n第二行不算")
    # 附件标记行不算题目,跳过取下一行
    assert session_mod.derive_title("[附件] 上传了 2 个文件\n帮我分析这份成绩单") \
        == "帮我分析这份成绩单"
    # Markdown 修饰符剥掉、空白折叠
    assert session_mod.derive_title("### **《三体》读后感**怎么写") \
        == "《三体》读后感怎么写"
    # 超长截断带省略号
    long = "这是一个超过二十四个字的提问所以结尾应该带上省略号对吧"
    out = session_mod.derive_title(long)
    assert out.endswith("…") and len(out) == 25
    # 没有可读内容回空串(调用方退回旧文案,不编标题)
    assert session_mod.derive_title("[附件] 上传了 1 个文件") == ""
    assert session_mod.derive_title("") == ""
    assert session_mod.derive_title(None) == ""


def test_list_sessions_captures_first_user_text(tmp_path):
    s = Session(system_prompt="sys", log_dir=tmp_path, workspace=tmp_path)
    s.add_user("第一句:写实验报告")
    s.add_user("第二句:改成周五交")
    s.add_tool_result("c1", "write_file", "已写入")
    rows = session_mod.list_sessions(tmp_path, limit=5)
    assert len(rows) == 1
    assert rows[0].first_user_text == "第一句:写实验报告"
    assert rows[0].last_user_text.startswith("第二句")
    # 老留档没有用户消息:两份都是空串,标题派生回空串
    (tmp_path / "session_old.jsonl").write_text(
        json.dumps({"type": "session_start", "time": "t"}) + "\n",
        encoding="utf-8")
    rows2 = session_mod.list_sessions(tmp_path, limit=5)
    old = next(r for r in rows2 if r.path.name == "session_old.jsonl")
    assert old.first_user_text == "" and session_mod.derive_title(old.first_user_text) == ""


# ------------------------------------------- 留档 schema 版本(F4,v0.9.100)

def test_session_start_carries_schema_version(tmp_path):
    """session_start 行带 schema=2:留档格式从此有版本记账,格式再变时有
    一条明确的迁移路径,而不是各消费方自己猜形状。"""
    import glob as _glob
    import json as _json
    from minicode import session as _smod
    sess = Session(system_prompt="s", log_dir=tmp_path, workspace=tmp_path)
    sess.add_user("第一句")
    f = _glob.glob(str(tmp_path / "session_*.jsonl"))[0]
    head = _json.loads(open(f, encoding="utf-8").readline())
    assert head["schema"] == _smod.LOG_SCHEMA_VERSION


def test_old_session_without_schema_still_loads(tmp_path):
    """老留档(无 schema 行)照常恢复:消费方把"没有这个字段"当正常形状。"""
    (tmp_path / "session_old.jsonl").write_text(
        json.dumps({"type": "session_start", "time": "t"}) + "\n"
        + json.dumps({"role": "user", "content": "旧会话的一句话"}) + "\n",
        encoding="utf-8")
    s = Session(system_prompt="s", log_dir=tmp_path, workspace=tmp_path)
    n = s.load_from(tmp_path / "session_old.jsonl")
    assert n == 1 and s.messages[-1]["content"] == "旧会话的一句话"


# ------------------------------------------- 会话分支(C4,v0.9.100→102)

def test_fork_log_file_copies_prefix_and_marks_origin(tmp_path):
    """行级复制保留原 ts;新 session_start 带 forked_from;upto 越界如实 None。"""
    from minicode.llm import AssistantTurn
    s = Session(system_prompt="s", log_dir=tmp_path / "src", workspace=tmp_path)
    s.add_user("第一问")            # 首条消息才懒创建留档(log_path 在这之后才有)
    real_src = s.log_path
    assert real_src is not None
    s.add_assistant(AssistantTurn(text="第一答"))
    s.add_user("第二问")
    s.add_user("第三问")
    # upto 语义 = 保留前 N **条消息**(user/assistant/tool 都算)
    got = fork_log_file(real_src, 2, tmp_path / "forks", tmp_path)
    assert got is not None
    new_path, kept = got
    assert kept == 2
    data = json.loads(new_path.read_text(encoding="utf-8").splitlines()[0])
    assert data["forked_from"] == real_src.name and data["schema"] == 2
    lines = new_path.read_text(encoding="utf-8").splitlines()
    roles = [json.loads(l).get("role") for l in lines[1:]]
    assert roles == ["user", "assistant"], "分叉只保留前两**条消息**"
    # 越界/非法
    assert fork_log_file(real_src, 0, tmp_path / "forks", tmp_path) is None
    assert fork_log_file(real_src, 99, tmp_path / "forks", tmp_path) is None
    assert fork_log_file(tmp_path / "nope.jsonl", 1, tmp_path / "forks", tmp_path) is None


def test_forked_log_loads_as_a_session(tmp_path):
    """分叉出的留档能被 load_from 恢复成可继续对话的会话(ts 保留)。"""
    s = Session(system_prompt="s", log_dir=tmp_path, workspace=tmp_path)
    s.add_user("第一问")
    s.add_user("第二问")
    got = fork_log_file(s.log_path, 1, tmp_path / "forks", tmp_path)
    new_path, _kept = got
    forked = Session(system_prompt="s", log_dir=tmp_path, workspace=tmp_path)
    n = forked.load_from(new_path)
    assert n == 1 and forked.messages[-1]["content"] == "第一问"


# ------------------------------------------------------------------ W7 思考摘要

def test_reasoning_is_archived_clipped_with_true_count(tmp_path):
    """W7:思考摘要随留档走 —— 截断存、真字数另记;没有思考就不带键。"""
    s = Session(system_prompt="sys", log_dir=tmp_path)
    big = "想" * 5000
    s.add_assistant(AssistantTurn(text="答", reasoning=big))
    msg = s.messages[-1]
    assert msg["reasoning_chars"] == 5000           # 「思考 N 字」说的是真数
    assert len(msg["reasoning"]) < 5000 and "截断" in msg["reasoning"]
    s.add_assistant(AssistantTurn(text="没有思考"))
    assert "reasoning" not in s.messages[-1]        # 没有就不带键(与 ts 同规)


def test_outbound_strips_reasoning_bookkeeping(tmp_path):
    """出口必须净:ts/reasoning/reasoning_chars 都是本地记账,严格端点报 400。"""
    s = Session(system_prompt="sys", log_dir=tmp_path)
    s.add_assistant(AssistantTurn(text="答", reasoning="想" * 3000))
    out = outbound(s.messages)
    for m in out:
        assert not ({"ts", "reasoning", "reasoning_chars"} & set(m))
    assert "reasoning" in s.messages[-1]            # 拷贝剥离,本体不动


def test_old_archive_without_reasoning_still_loads(tmp_path):
    """老留档没有思考键:照常载入,消费方按「没有」处理(与 ts 同一条规矩)。"""
    import json as _json
    log = tmp_path / "session_old.jsonl"
    log.write_text("\n".join([
        _json.dumps({"type": "session_start", "time": "2026-01-01 00:00:00",
                     "workspace": "", "schema": 2}),
        _json.dumps({"role": "user", "content": "hi",
                     "ts": "2026-01-01 00:00:01"}),
        _json.dumps({"role": "assistant", "content": "答",
                     "ts": "2026-01-01 00:00:02"}),
    ]) + "\n", encoding="utf-8")
    s = Session(system_prompt="sys", log_dir=tmp_path)
    n = s.load_from(log)
    assert n == 2
    assert "reasoning" not in s.messages[-1]


# ------------------------------------------------------------------ W10 实测耗时

def test_tool_result_carries_elapsed_ms_when_measured(tmp_path):
    """W10:实测毫秒随留档走 —— 重启后时间线不再靠 ts 秒级差猜耗时。"""
    s = Session(system_prompt="sys", log_dir=tmp_path)
    s.add_tool_result("c1", "bash", "ok", elapsed_ms=2900)
    assert s.messages[-1]["elapsed_ms"] == 2900
    # 没实测(早退路径/老调用方)/负数/bool:一律不带键,宁缺毋错
    s.add_tool_result("c2", "bash", "ok")
    s.add_tool_result("c3", "bash", "ok", elapsed_ms=-1)
    s.add_tool_result("c4", "bash", "ok", elapsed_ms=True)
    for cid in ("c2", "c3", "c4"):
        assert "elapsed_ms" not in next(m for m in s.messages
                                        if m.get("tool_call_id") == cid)


def test_outbound_strips_elapsed_ms(tmp_path):
    """elapsed_ms 是本地记账:出端点前剥掉,严格端点对未知字段报 400。"""
    s = Session(system_prompt="sys", log_dir=tmp_path)
    s.add_tool_result("c1", "bash", "ok", elapsed_ms=2900)
    out = outbound(s.messages)
    assert all("elapsed_ms" not in m for m in out)
    assert s.messages[-1]["elapsed_ms"] == 2900      # 拷贝剥离,本体不动
