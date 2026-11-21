"""test_runner 智能测试运行与自愈诊断模块测试(v0.9.164)。"""
from pathlib import Path
from unittest.mock import MagicMock

from minicode import test_runner
from minicode.tools.base import ToolContext


def test_detect_framework_empty(tmp_path: Path):
    assert test_runner.detect_framework(tmp_path) is None


def test_detect_framework_python(tmp_path: Path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    meta = test_runner.detect_framework(tmp_path)
    assert meta is not None
    assert meta["type"] == "python"
    assert meta["framework"] == "pytest"
    assert meta["cmd_template"][:3] == [meta["runner"], "-m", "pytest"]


def test_detect_framework_node(tmp_path: Path):
    (tmp_path / "package.json").write_text('{"name": "test-pkg", "scripts": {"test": "jest"}}', encoding="utf-8")
    meta = test_runner.detect_framework(tmp_path)
    assert meta is not None
    assert meta["type"] == "node"
    assert meta["framework"] == "npm"


def test_detect_framework_rust(tmp_path: Path):
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "test_pkg"\n', encoding="utf-8")
    meta = test_runner.detect_framework(tmp_path)
    assert meta is not None
    assert meta["type"] == "rust"
    assert meta["framework"] == "cargo"


def test_detect_framework_go(tmp_path: Path):
    (tmp_path / "go.mod").write_text("module example.com/pkg\n\ngo 1.22\n", encoding="utf-8")
    meta = test_runner.detect_framework(tmp_path)
    assert meta is not None
    assert meta["type"] == "go"
    assert meta["framework"] == "go"


def test_parse_pytest_output():
    sample = """
============================= test session starts =============================
collected 4 items

test_sample.py .F.s                                                      [100%]

================================== FAILURES ===================================
__________________________________ test_fail __________________________________

    def test_fail():
>       assert 1 == 2
E       assert 1 == 2

test_sample.py:6: AssertionError
=========================== short test summary info ===========================
FAILED test_sample.py::test_fail - assert 1 == 2
==================== 1 failed, 2 passed, 1 skipped in 0.12s ====================
"""
    parsed = test_runner.parse_pytest_output(sample)
    assert parsed["passed"] == 2
    assert parsed["failed"] == 1
    assert parsed["skipped"] == 1
    assert parsed["total"] == 4
    assert len(parsed["failures"]) == 1
    f0 = parsed["failures"][0]
    assert "test_fail" in f0["test"]
    assert "assert 1 == 2" in f0["summary"]


def test_run_tests_success_in_temp_dir(tmp_path: Path):
    # 创建一个纯通过的 Python 测试
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_ok.py").write_text(
        "def test_pass():\n    assert True\n",
        encoding="utf-8",
    )
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")

    res = test_runner.run_tests(tmp_path)
    assert res["framework"] == "pytest"
    assert res["ok"] is True
    assert res["status"] == "passed"
    assert res["passed"] >= 1
    assert res["failed"] == 0

    # 验证快照记录
    last = test_runner.get_last_run(tmp_path)
    assert last is not None
    assert last["ok"] is True


def test_run_tests_failure_in_temp_dir(tmp_path: Path):
    # 创建一个必定失败的 Python 测试
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_bad.py").write_text(
        "def test_failing_case():\n    assert 42 == 99, 'number mismatch'\n",
        encoding="utf-8",
    )
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")

    res = test_runner.run_tests(tmp_path)
    assert res["ok"] is False
    assert res["status"] == "failed"
    assert res["failed"] >= 1
    assert len(res["failures"]) >= 1
    assert any("test_failing_case" in f["test"] for f in res["failures"])

    # 验证 format_test_report
    text = test_runner.format_test_report(res)
    assert "【测试结论】" in text
    assert "【失败用例详情】" in text
    assert "test_failing_case" in text


def test_test_runner_tool_actions(tmp_path: Path):
    ctx = ToolContext(workspace=tmp_path, config=MagicMock())

    # 1. detect (无框架)
    det = test_runner.test_runner_tool(ctx, action="detect")
    assert "未检测到已知测试框架" in det

    # 2. detect (有框架)
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "mycrate"\n', encoding="utf-8")
    det2 = test_runner.test_runner_tool(ctx, action="detect")
    assert "已检测到测试框架" in det2
    assert "cargo" in det2

    # 3. last_result (无记录)
    lr1 = test_runner.test_runner_tool(ctx, action="last_result")
    assert "尚未运行过测试" in lr1

    # 4. run
    run_out = test_runner.test_runner_tool(ctx, action="run")
    assert "【测试框架】" in run_out

    # 5. last_result (有记录)
    lr2 = test_runner.test_runner_tool(ctx, action="last_result")
    assert "【最近一次测试运行快照】" in lr2

    # 6. invalid action
    err = test_runner.test_runner_tool(ctx, action="unknown")
    assert "未知的 action" in err


def test_test_runner_tool_passes_context(tmp_path, monkeypatch):
    from minicode.config import Config

    ctx = ToolContext(tmp_path, Config())
    run = MagicMock(return_value={"ok": False, "summary": "未完成"})
    monkeypatch.setattr(test_runner, "run_tests", run)
    test_runner.test_runner_tool(ctx, target="tests/test_one.py")
    assert run.call_args.args == (tmp_path,)
    assert run.call_args.kwargs == {"target": "tests/test_one.py", "ctx": ctx}


def test_test_runner_timeout_decodes_bytes_and_preserves_snapshot(tmp_path, monkeypatch):
    import subprocess

    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    run = MagicMock(side_effect=subprocess.TimeoutExpired(
        ["pytest"], 2, output=b"partial\xff", stderr=b"error tail"))
    monkeypatch.setattr(test_runner, "run_argv", run)
    result = test_runner.run_tests(tmp_path, timeout=120)
    assert not result["ok"] and result["status"] == "timeout"
    assert "2s" in result["summary"]
    assert "partial" in result["failures"][0]["details"]
    assert "error tail" in result["failures"][0]["details"]
    assert test_runner.get_last_run(tmp_path) is result
    assert run.call_args.args[0].sandbox.policy.enabled


def test_test_runner_actual_command_policy_rejection(tmp_path, monkeypatch):
    from minicode.config import Config
    from minicode.sandbox import SandboxPolicy, SandboxState

    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    ctx = ToolContext(tmp_path, Config(permission_mode="full"),
                      SandboxState(SandboxPolicy(deny_patterns=["pytest"]), tmp_path))
    run = MagicMock()
    monkeypatch.setattr(test_runner, "run_argv", run)
    result = test_runner.run_tests(tmp_path, ctx=ctx)
    assert not result["ok"] and result["status"] == "error"
    run.assert_not_called()


def test_test_runner_target_cannot_escape_or_inject_options(tmp_path, monkeypatch):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    run = MagicMock()
    monkeypatch.setattr(test_runner, "run_argv", run)
    for target in ("../outside.py", "--override-ini=addopts=evil"):
        result = test_runner.run_tests(tmp_path, target=target)
        assert not result["ok"] and result["status"] == "error"
    run.assert_not_called()


def test_gui_tests_capture_runner_context_gate_and_confirmation(tmp_path, monkeypatch):
    import subprocess
    import threading
    from types import SimpleNamespace
    from minicode.config import Config
    from minicode.gui_bridge import Bridge
    from minicode.permission import PermissionGate
    from minicode.sandbox import SandboxPolicy, SandboxState

    original = tmp_path / "original"
    original.mkdir()
    (original / "pytest.ini").write_text("[pytest]\n")
    config = Config()
    ctx = ToolContext(original, config, SandboxState(SandboxPolicy(), original))
    gate = PermissionGate(config, sandbox=ctx.sandbox, workspace=original)
    new_gate = MagicMock()
    next_runner = SimpleNamespace(agent=SimpleNamespace(gate=new_gate))
    ui = MagicMock()
    runner = SimpleNamespace(agent=SimpleNamespace(ctx=ctx, gate=gate), ui=ui)
    bridge = SimpleNamespace(_navigation_lock=threading.Lock(), _current=runner, workspace=original)

    def confirm(tool, args, reason, note):
        assert tool.name == "bash" and not tool.read_only and "pytest" in args["command"]
        assert bridge._navigation_lock.acquire(blocking=False), "确认等待不应持有导航锁"
        try:
            bridge._current = next_runner
            bridge.workspace = tmp_path / "new-workspace"
        finally:
            bridge._navigation_lock.release()
        return "yes"

    ui.confirm_tool.side_effect = confirm
    run = MagicMock(return_value=subprocess.CompletedProcess([], 0, b"1 passed", b""))
    monkeypatch.setattr(test_runner, "run_argv", run)
    result = Bridge.run_tests(bridge)
    assert result["ok"]
    assert run.call_args.args[0] is ctx and run.call_args.kwargs["cwd"] == original
    ui.confirm_tool.assert_called_once()
    new_gate.check.assert_not_called()
    new_gate.approve_outside.assert_not_called()


def test_gui_test_denial_does_not_start_process(tmp_path, monkeypatch):
    import threading
    from types import SimpleNamespace
    from minicode.config import Config
    from minicode.gui_bridge import Bridge
    from minicode.permission import PermissionGate
    from minicode.sandbox import SandboxPolicy, SandboxState

    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    config = Config()
    ctx = ToolContext(tmp_path, config, SandboxState(SandboxPolicy(), tmp_path))
    gate = PermissionGate(config, sandbox=ctx.sandbox, workspace=tmp_path)
    ui = MagicMock()
    ui.confirm_tool.return_value = "no"
    runner = SimpleNamespace(agent=SimpleNamespace(ctx=ctx, gate=gate), ui=ui)
    bridge = SimpleNamespace(_navigation_lock=threading.Lock(), _current=runner)
    run = MagicMock()
    monkeypatch.setattr(test_runner, "run_argv", run)
    result = Bridge.run_tests(bridge)
    assert not result["ok"] and result["status"] == "error"
    run.assert_not_called()
