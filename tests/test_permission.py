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
