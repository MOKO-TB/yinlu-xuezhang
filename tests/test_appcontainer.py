"""AppContainer 真执行层：工作区写入、越界读拒绝、网络 capability、shell 接线。"""
from __future__ import annotations

import socket
import os
import sys

import pytest

from minicode import appcontainer
from minicode.config import Config
from minicode.sandbox import SandboxPolicy, SandboxState
from minicode.tools import shell
from minicode.tools.base import ToolContext

pytestmark = pytest.mark.slow


@pytest.fixture(autouse=True)
def _cleanup_appcontainer():
    yield
    appcontainer.cleanup_all()


def _needs_appcontainer():
    return pytest.mark.skipif(
        sys.platform != "win32" or not appcontainer.available(),
        reason="当前机器没有 AppContainer API(显式跳过,不是静默)",
    )


@_needs_appcontainer()
def test_appcontainer_file_network_and_tool_integration(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("OUTSIDE-SECRET", encoding="utf-8")

    child = (
        "import pathlib\n"
        f"pathlib.Path({str(workspace / 'inside.txt')!r}).write_text('INSIDE')\n"
        "print('WRITE-OK')\n"
        f"pathlib.Path({str(outside)!r}).read_text(encoding='utf-8')\n"
    )
    proc = appcontainer.Popen(
        [sys.executable, "-c", child], cwd=workspace, env=dict(os.environ),
        allow_network=False,
    )
    out, err = proc.communicate(timeout=30)
    assert proc.returncode != 0
    assert "WRITE-OK" in out.decode("utf-8", "replace")
    assert "PermissionError" in err.decode("utf-8", "replace")
    assert (workspace / "inside.txt").read_text(encoding="utf-8") == "INSIDE"

    network_code = (
        "import socket\n"
        "try:\n"
        "    socket.create_connection(('www.baidu.com', 443), timeout=5).close()\n"
        "    print('NET-OK')\n"
        "except Exception as e:\n"
        "    print('NET-BLOCKED', type(e).__name__)\n"
    )
    denied = appcontainer.Popen(
        [sys.executable, "-c", network_code], cwd=workspace,
        env=dict(os.environ), allow_network=False,
    )
    denied_out, _ = denied.communicate(timeout=30)
    assert "NET-BLOCKED" in denied_out.decode("utf-8", "replace")

    try:
        socket.create_connection(("www.baidu.com", 443), timeout=5).close()
        network_available = True
    except OSError:
        network_available = False
    if network_available:
        allowed = appcontainer.Popen(
            [sys.executable, "-c", network_code], cwd=workspace,
            env=dict(os.environ), allow_network=True,
        )
        allowed_out, _ = allowed.communicate(timeout=30)
        assert "NET-OK" in allowed_out.decode("utf-8", "replace")

    restricted_path = workspace / "restricted.txt"
    restricted = appcontainer.Popen(
        [os.environ.get("COMSPEC") or "cmd.exe", "/d", "/c",
         "echo IN>restricted.txt"],
        cwd=workspace,
        env=dict(os.environ), allow_network=False, mode="restricted",
    )
    restricted.communicate(timeout=30)
    assert restricted_path.read_text(encoding="utf-8").strip() == "IN"
    blocked = appcontainer.Popen(
        [os.environ.get("COMSPEC") or "cmd.exe", "/d", "/c",
         "echo MUTATED>..\\outside.txt"],
        cwd=workspace,
        env=dict(os.environ), allow_network=False, mode="restricted",
    )
    blocked.communicate(timeout=30)
    assert outside.read_text(encoding="utf-8") == "OUTSIDE-SECRET"

    script = workspace / "script.py"
    script.write_text(
        "from pathlib import Path\n"
        "Path('from-shell.txt').write_text('SHELL')\n"
        "print('PY-SCRIPT-OK')\n",
        encoding="utf-8",
    )
    restricted_state = SandboxState(
        SandboxPolicy(restricted_token=True, allow_network=False), workspace)
    restricted_ctx = ToolContext(
        workspace=workspace, config=Config(), sandbox=restricted_state)
    restricted_py = shell.py_script(restricted_ctx, "script.py", timeout=20)
    assert "PY-SCRIPT-OK" in restricted_py
    assert "AppContainer 已启用" in restricted_py
    restricted_bash = shell.bash(restricted_ctx, "echo RESTRICTED-BASH-OK", timeout=20)
    assert "RESTRICTED-BASH-OK" in restricted_bash
    assert "受限令牌" in restricted_bash

    state = SandboxState(
        SandboxPolicy(app_container=True, allow_network=False), workspace)
    ctx = ToolContext(workspace=workspace, config=Config(), sandbox=state)
    py_out = shell.py_script(ctx, "script.py", timeout=20)
    assert "PY-SCRIPT-OK" in py_out
    assert "AppContainer 已启用" in py_out
    assert (workspace / "from-shell.txt").read_text(encoding="utf-8") == "SHELL"

    bash_out = shell.bash(ctx, "echo BASH-OK", timeout=20)
    assert "BASH-OK" in bash_out
    assert "AppContainer 已启用" in bash_out
