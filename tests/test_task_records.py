import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from minicode import subagents, task_records as tr
from minicode.llm import AssistantTurn, ToolCall
from minicode.session import Session, list_sessions
from minicode.tools.base import ToolContext


def context(tmp_path, config, sessions_dir=None):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    parent = Session("test", log_dir=sessions_dir or tmp_path / "sessions", workspace=workspace)
    parent.add_user("原任务")
    return ToolContext(workspace=workspace, config=config, session=parent)


@pytest.fixture
def worker(monkeypatch):
    from minicode import app_setup
    monkeypatch.setattr(app_setup, "build_system_prompt", lambda *a, **kw: "test")
    seen = []

    class FakeAgent:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            seen.append(self)

        def run_turn(self, text):
            self.session.add_user(text)
            self.session.add_assistant(AssistantTurn(text="已有产物，请核查"))

        def close(self):
            pass

    monkeypatch.setattr(subagents, "Agent", FakeAgent)
    return seen, FakeAgent


def dispatch(ctx, **kwargs):
    return subagents.run_subagent(ctx, subagents.SUBAGENT_TYPES["explore"], "核查目标",
                                 None, None, ctx.config, [], **kwargs)


def test_atomic_utf8_and_threads(tmp_path, config, monkeypatch):
    ctx = context(tmp_path, config)
    run = tr.TaskRun(ctx, "explore", "中文目标")
    store, data = run.store, run.data
    target = store.run_dir(data["id"]) / "record.json"
    assert "中文目标" in target.read_text(encoding="utf-8")
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda n: store.write({**data, "report": str(n)}), range(30)))
    assert json.loads(target.read_text(encoding="utf-8"))["id"] == data["id"]
    old = target.read_bytes()

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(tr.os, "replace", fail)
    with pytest.raises(OSError):
        store.write({**data, "report": "not committed"})
    assert target.read_bytes() == old
    assert not list(target.parent.glob("*.tmp"))
    result = run.finish("报告", True)
    assert "不能保证可恢复" in result
    assert store.read(data["id"])["status"] == "unknown"


@pytest.mark.parametrize("run_id", ["../outside", "..\\outside", "/absolute", "a" * 36,
                                      "D:/sessions", "", None])
def test_reject_out_of_bounds_ids(tmp_path, run_id):
    store = tr.TaskRecords(tmp_path)
    with pytest.raises(ValueError):
        store.read(run_id)
    with pytest.raises(ValueError):
        store.write({"id": run_id})
    assert not (tmp_path / "subagents").exists()


def test_worker_records_child_context_and_archive(tmp_path, config, worker):
    ctx = context(tmp_path, config)
    text = dispatch(ctx)
    record = tr.TaskRecords(tmp_path / "sessions").list_for(ctx.workspace)[0]
    child = worker[0][0]
    assert child.ctx.session is child.session and child.session is not ctx.session
    assert child.session._task_parent_log == str(ctx.session.log_path)
    assert record["parent_log"] == str(ctx.session.log_path)
    assert record["root_parent_log"] == ctx.session.log_path.name
    assert record["status"] == "completed" and record["has_report"]
    assert record["pid"] == os.getpid() and record["process_token"]
    assert record["ended_at"] >= record["started_at"]
    assert record["goal"] == "核查目标"
    assert record["execution_workspace"] == str(ctx.workspace)
    assert record["report"] in text and "不代表验收通过" in text
    log = Path(record["session_log"])
    assert log.parent.name == record["id"]
    assert "已有产物" in log.read_text(encoding="utf-8")
    assert len(list_sessions(tmp_path / "sessions")) == 1


def test_nested_parent_task_and_session_are_explicit(tmp_path, config, worker):
    ctx = context(tmp_path, config)
    dispatch(ctx)
    child = worker[0][0]
    dispatch(child.ctx)
    store = tr.TaskRecords(tmp_path / "sessions")
    records = store.list_for(ctx.workspace)
    nested = next(r for r in records if r["parent_task_id"])
    assert nested["parent_task_id"] == child.session._task_run_id
    assert nested["parent_log"] == str(child.session.log_path)
    assert nested["root_parent_log"] == ctx.session.log_path.name
    assert len(records) == 2
    assert len(list_sessions(tmp_path / "sessions")) == 1


def test_timeout_keeps_running_and_late_report_is_persisted(tmp_path, config, worker):
    ctx = context(tmp_path, config)
    entered, release, done = threading.Event(), threading.Event(), threading.Event()
    normal = worker[1].run_turn

    def blocked(self, text):
        entered.set()
        assert release.wait(5)
        normal(self, text)

    worker[1].run_turn = blocked

    def run():
        try:
            return dispatch(ctx)
        finally:
            done.set()

    try:
        assert subagents._await([run], 0.02) == [None]
        assert entered.wait(3)
        store = tr.TaskRecords(tmp_path / "sessions")
        record = store.list_for(ctx.workspace)[0]
        assert record["status"] == "running" and not record["can_continue"]
        assert record["ended_at"] is None
    finally:
        release.set()
        assert done.wait(5)
    record = store.read(record["id"])
    assert record["status"] == "completed" and "已有产物" in record["report"]


@pytest.mark.parametrize("mode", ["raise", "empty", "preparation"])
def test_failures_have_terminal_record(tmp_path, config, worker, monkeypatch, mode):
    ctx = context(tmp_path, config)

    def fail(*args, **kwargs):
        raise RuntimeError("fake failure")

    if mode == "preparation":
        from minicode import subagent_worktree
        monkeypatch.setattr(subagent_worktree, "create", fail)
    else:
        monkeypatch.setattr(worker[1], "run_turn", fail if mode == "raise" else lambda *a: None)
    dispatch(ctx, isolation="worktree" if mode == "preparation" else "")
    record = tr.TaskRecords(tmp_path / "sessions").list_for(ctx.workspace)[0]
    assert record["status"] == "failed" and record["ended_at"]
    assert not record["has_report"]
    assert "未返回文本报告" in record["report"] if mode == "empty" else "失败" in record["report"] or "出错" in record["report"]


def test_worktree_metadata_preserves_original_workspace(tmp_path, config, worker, monkeypatch):
    from minicode import subagent_worktree
    ctx = context(tmp_path, config)
    copy = tmp_path / "copy"
    copy.mkdir()
    info = SimpleNamespace(root=copy, workspace=copy, base="abc123")
    monkeypatch.setattr(subagent_worktree, "create", lambda p: info)
    monkeypatch.setattr(subagent_worktree, "header", lambda *a: "isolated\n")
    monkeypatch.setattr(subagent_worktree, "summarize", lambda *a: "\nchanges")
    dispatch(ctx, isolation="worktree")
    record = tr.TaskRecords(tmp_path / "sessions").list_for(ctx.workspace)[0]
    assert record["workspace"] == str(ctx.workspace)
    assert record["execution_workspace"] == str(copy)
    assert record["worktree_path"] == str(copy) and record["worktree_base"] == "abc123"
    assert worker[0][0].ctx.workspace == copy


@pytest.mark.parametrize("probe, expected", [(("exited", ""), "interrupted"),
    (("alive", "original"), "running"), (("alive", "reused"), "unknown"),
    (("unknown", ""), "unknown"), (("alive", ""), "unknown"),
    (("exited", "reused"), "unknown")])
def test_old_process_is_checked_conservatively(tmp_path, config, monkeypatch, probe, expected):
    ctx = context(tmp_path, config)
    run = tr.TaskRun(ctx, "explore", "never replay")
    with tr._LOCK:
        tr._ACTIVE.discard((str(run.store.root), run.data["id"]))
    run.save(pid=987654, process_token="old", process_created="original")
    monkeypatch.setattr(tr, "_probe_process", lambda pid: probe)
    target = run.store.run_dir(run.data["id"]) / "record.json"
    before = target.read_bytes()
    record = run.store.read(run.data["id"])
    assert record["status"] == expected
    assert record["can_continue"] is (expected == "interrupted")
    assert target.read_bytes() == before
    assert json.loads(before)["status"] == "running"


def test_self_probe_never_uses_kill(tmp_path, monkeypatch):
    def forbidden(*args):
        raise AssertionError("os.kill must not probe a Windows PID")

    monkeypatch.setattr(os, "kill", forbidden)
    state, birth = tr._probe_process(os.getpid())
    assert state == "alive" and birth


def test_active_current_worker_stays_running_without_probe(tmp_path, config, monkeypatch):
    run = tr.TaskRun(context(tmp_path, config), "explore", "目标")
    monkeypatch.setattr(tr, "_probe_process", lambda pid: pytest.fail("unexpected probe"))
    assert run.store.read(run.data["id"])["status"] == "running"
    run.finish("完成", True)


def test_archive_failure_does_not_change_execution(tmp_path, config, worker, monkeypatch):
    ctx = context(tmp_path, config)
    original = Session._log_only

    def fail_child(self, obj):
        if self.origin == "subagent":
            raise OSError("fake disk failure")
        original(self, obj)

    monkeypatch.setattr(Session, "_log_only", fail_child)
    text = dispatch(ctx)
    assert "已有产物" in text and "不能保证可恢复" in text
    record = tr.TaskRecords(tmp_path / "sessions").list_for(ctx.workspace)[0]
    assert record["archive_error"] and not record["can_continue"]
    assert worker[0][0].ctx.config is config


def bridge_record(tmp_path, config, llm=None, sessions_dir=None):
    from minicode.gui_bridge import create_app
    from test_gui_bridge import FakeLLM, FakeWindow
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    bridge, ui, _, session = create_app(
        config, workspace, llm=llm or FakeLLM([AssistantTurn(text="已核查，没有重放")]),
        sessions_dir=sessions_dir or tmp_path / "sessions", config_path=tmp_path / "config.json",
        app_dir=tmp_path, workspace_file=tmp_path / "workspaces.json")
    window = FakeWindow()
    ui.attach(window)
    session.add_user("原父任务")
    session.add_assistant(AssistantTurn(tool_calls=[ToolCall(
        id="old-call", name="write_file", arguments='{"path":"must-not-exist","content":"replay"}')]))
    run = tr.TaskRun(bridge.agent.ctx, "implement", "人工核查已有产物")
    run.finish("报告不等于验收通过", True)
    return bridge, window, run


def test_gui_query_is_read_only_and_isolated(tmp_path, config):
    from minicode.gui_bridge import PublicApi
    bridge, _, run = bridge_record(tmp_path, config)
    root = tmp_path / "sessions"
    before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    current, count = bridge._current, len(bridge._runners)
    api = PublicApi(bridge)
    assert api.get_task_runs()["task_runs"][0]["id"] == run.data["id"]
    assert api.get_activity()["task_runs"][0]["id"] == run.data["id"]
    assert not bridge._busy and bridge._current is current and len(bridge._runners) == count
    assert before == {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    other_ws = tmp_path / "other"
    other_ws.mkdir()
    bridge.workspace = other_ws
    assert api.get_task_runs()["task_runs"] == []
    assert not api.continue_task_run(run.data["id"])["ok"]
    bridge.workspace = tmp_path / "workspace"
    visitor, _, _ = bridge_record(tmp_path, config, sessions_dir=tmp_path / "visitor-b")
    assert run.data["id"] not in [r["id"] for r in visitor.get_task_runs()["task_runs"]]
    assert not visitor.continue_task_run(run.data["id"])["ok"]
    assert not api.continue_task_run("../outside")["ok"]
    bridge.close()
    visitor.close()


@pytest.mark.parametrize("state", ["completed", "failed", "interrupted"])
def test_manual_continue_restores_parent_and_submits_new_turn_without_replay(
        tmp_path, config, monkeypatch, state):
    from minicode.gui_bridge import PublicApi
    from test_gui_bridge import wait_until
    bridge, window, run = bridge_record(tmp_path, config)
    if state == "failed":
        run.save(status="failed", has_report=False, report="未返回文本报告")
    elif state == "interrupted":
        run.save(status="running", ended_at=None, pid=987654, process_token="old")
        monkeypatch.setattr(tr, "_probe_process", lambda pid: ("exited", ""))
    parent_path = bridge.session.log_path
    old_bytes = (run.store.run_dir(run.data["id"]) / "record.json").read_bytes()
    bridge._runners.clear()
    bridge.open_session()
    got = PublicApi(bridge).continue_task_run(run.data["id"])
    assert got["ok"] and got["source_run_id"] == run.data["id"]
    assert wait_until(lambda: bridge._current.thread and not bridge._current.thread.is_alive())
    assert bridge.session.log_path == parent_path
    users = [m["content"] for m in bridge.session.messages if m["role"] == "user"]
    assert len(users) == 2
    assert run.data["id"] in users[-1] and got["attempt_id"] in users[-1]
    assert "先核查状态" in users[-1] and "不重放已完成命令" in users[-1]
    assert str(run.store.run_dir(run.data["id"])) in users[-1]
    assert not (bridge.workspace / "must-not-exist").exists()
    assert (run.store.run_dir(run.data["id"]) / "record.json").read_bytes() == old_bytes
    events = [e["type"] for e in window.events]
    assert events.index("cleared") < events.index("resumed") < events.index("user")
    bridge.close()


@pytest.mark.parametrize("reason", ["running", "unknown", "busy", "missing_parent", "missing_workspace"])
def test_continue_refuses_unconfirmed_or_unavailable_parent(tmp_path, config, monkeypatch, reason):
    bridge, window, run = bridge_record(tmp_path, config)
    if reason in ("running", "unknown"):
        run.save(status="running", ended_at=None, pid=987654, process_token="old",
                 process_created="original")
        monkeypatch.setattr(tr, "_probe_process", lambda pid:
                            ("alive", "original") if reason == "running" else ("unknown", ""))
    elif reason == "busy":
        bridge._current.busy = True
    elif reason == "missing_parent":
        bridge.session.log_path.unlink()
    else:
        bridge.workspace.rmdir()
    current = bridge._current
    assert not bridge.continue_task_run(run.data["id"])["ok"]
    assert current is bridge._current
    assert not window.of_type("cleared") and not window.of_type("user")
    bridge._current.busy = False
    bridge.close()
