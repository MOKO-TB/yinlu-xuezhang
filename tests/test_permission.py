import pytest

from minicode.permission import ALLOW, ASK, REJECT, PermissionGate, check_disaster_command
from minicode.tools.registry import build_default_registry


@pytest.fixture
def registry():
    return {t.name: t for t in build_default_registry()}


# ------------------------------------------------------------ 灾难命令

@pytest.mark.parametrize("cmd", [
    "rm -rf /",
    "rm -fr ~",
    "rm -rf .",
    "rm -r ..",
    "rm -rf *",
    "rm --recursive --force $HOME",
    "rm -rf node_modules && rm -rf /",
    "format c:",
    "mkfs.ext4 /dev/sdb1",
    "dd if=/dev/zero of=/dev/sda",
    ":(){ :|:& };:",
    "Remove-Item C:\\Windows -Recurse -Force",
    "del /s /q C:\\",
])
def test_disaster_blocked(cmd):
    assert check_disaster_command(cmd) is not None


@pytest.mark.parametrize("cmd", [
    "rm file.txt",
    "rm -rf node_modules",
    "rm -r src",
    "ls -la",
    "git status",
    "echo rm -rf /",
    "pip install requests",
    "",
])
def test_disaster_not_triggered(cmd):
    assert check_disaster_command(cmd) is None


# ------------------------------------------------------------ 权限门

def test_read_only_always_allowed(config, registry):
    gate = PermissionGate(config)
    assert gate.check(registry["read_file"], {"path": "a"}) == ALLOW
    assert gate.check(registry["grep"], {"pattern": "x"}) == ALLOW


def test_write_asks_in_confirm_mode(config, registry):
    gate = PermissionGate(config)
    assert gate.check(registry["write_file"], {"path": "a", "content": "b"}) == ASK


def test_turn_approval(config, registry):
    gate = PermissionGate(config)
    assert gate.check(registry["bash"], {"command": "ls"}) == ASK
    gate.approve_for_turn("bash")
    assert gate.check(registry["bash"], {"command": "ls"}) == ALLOW
    gate.new_turn()
    assert gate.check(registry["bash"], {"command": "ls"}) == ASK


def test_accept_all_mode(config, registry):
    config.permission_mode = "accept_all"
    gate = PermissionGate(config)
    assert gate.check(registry["write_file"], {"path": "a", "content": "b"}) == ALLOW


def test_disaster_reject_overrides_accept_all(config, registry):
    config.permission_mode = "accept_all"
    gate = PermissionGate(config)
    assert gate.check(registry["bash"], {"command": "rm -rf /"}) == REJECT


# ------------------------------------------------------------ 会话内记住(v0.9.43)

def test_approve_outside_session_flag_reaches_sandbox(tmp_path, config):
    """点「总是允许」时 session 标志要传到沙箱:路径进 session_approved;
    点「允许」(默认)只进回合级集合。"""
    from minicode.sandbox import SandboxPolicy, SandboxState
    from minicode.tools.base import Tool

    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "elsewhere" / "doc.txt"
    tool = Tool(name="read_file", description="d",
                parameters={"type": "object"}, handler=lambda ctx, **kw: "ok",
                read_only=True)
    target = str(outside.resolve())

    gate = PermissionGate(config, sandbox=SandboxState(SandboxPolicy(), ws))
    gate.approve_outside(tool, {"path": target}, session=True)
    assert target in gate.sandbox.session_approved
    assert gate.sandbox.path_allowed(outside.resolve())

    gate2 = PermissionGate(config, sandbox=SandboxState(SandboxPolicy(), ws))
    gate2.approve_outside(tool, {"path": target})          # 默认仍是回合级
    assert target not in gate2.sandbox.session_approved
    assert gate2.sandbox.path_allowed(outside.resolve())   # 但本回合放行
