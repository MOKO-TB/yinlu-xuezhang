from minicode.config import Config, Provider
from minicode.tools import shell
from minicode.tools.base import ToolContext


def _cfg(shell="auto"):
    return Config(providers=[Provider(id="t", name="T", base_url="https://x",
                                      api_key="k", model="m")],
                  active_provider_id="t", shell=shell)


def test_echo(ctx):
    out = shell.bash(ctx, "echo hello-minicode")
    assert "hello-minicode" in out
    assert "退出码: 0" in out


def test_exit_code_and_stderr(ctx):
    out = shell.bash(ctx, "false")
    assert "退出码: 1" in out


def test_cmd_fallback(tmp_path):
    ctx = ToolContext(workspace=tmp_path, config=_cfg(shell="cmd"))
    assert shell.resolve_shell_argv("cmd") is None
    out = shell.bash(ctx, "echo hello-from-cmd")
    assert "hello-from-cmd" in out


def test_timeout(tmp_path):
    ctx = ToolContext(workspace=tmp_path, config=_cfg())
    if shell.resolve_shell_argv("auto") is None:
        # 没有 bash 时用 cmd 的 ping 模拟等待
        cmd = "ping -n 6 127.0.0.1 >nul"
    else:
        cmd = "echo start && sleep 5"
    out = shell.bash(ctx, cmd, timeout=1)
    assert "超时" in out
