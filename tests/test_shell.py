import pytest
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


@pytest.mark.slow      # 真等 5 s 超时:这条用例的耗时就是被测行为
def test_timeout(tmp_path):
    ctx = ToolContext(workspace=tmp_path, config=_cfg())
    if shell.resolve_shell_argv("auto") is None:
        # 没有 bash 时用 cmd 的 ping 模拟等待
        cmd = "ping -n 6 127.0.0.1 >nul"
    else:
        cmd = "echo start && sleep 5"
    out = shell.bash(ctx, cmd, timeout=1)
    assert "超时" in out


# --------------------------------------------------- Git Bash 探测(非默认安装)


def _fake_git_install(tmp_path, *, layout="cmd"):
    """造一个假的 Git 安装目录,返回 (git.exe, bash.exe)。"""
    root = tmp_path / "Git"
    git_exe = root / layout / "git.exe"
    git_exe.parent.mkdir(parents=True)
    git_exe.write_text("", encoding="utf-8")
    bash_exe = root / "bin" / "bash.exe"
    bash_exe.parent.mkdir(parents=True, exist_ok=True)
    bash_exe.write_text("", encoding="utf-8")
    return git_exe, bash_exe


def test_bash_beside_nonstandard_git(tmp_path, monkeypatch):
    """Git 装在非默认位置(如 D:\\git\\Git)时,靠 git.exe 反推出 bash。

    固定候选表只盖得住 C:\\Program Files\\Git;漏掉就会静默退回 cmd,
    于是 sleep/grep 之类命令直接报"不是内部或外部命令"。
    """
    git_exe, bash_exe = _fake_git_install(tmp_path)
    monkeypatch.setattr(shell, "_GIT_BASH_CANDIDATES", ())
    monkeypatch.setattr(shell.shutil, "which",
                        lambda n: str(git_exe) if n == "git" else None)
    assert shell._pick_bash() == str(bash_exe)


def test_bash_beside_git_supports_usr_bin_layout(tmp_path, monkeypatch):
    """有些安装方式只提供 usr/bin/bash。"""
    git_exe, _ = _fake_git_install(tmp_path)
    (tmp_path / "Git" / "bin" / "bash.exe").unlink()
    usr_bash = tmp_path / "Git" / "usr" / "bin" / "bash.exe"
    usr_bash.parent.mkdir(parents=True)
    usr_bash.write_text("", encoding="utf-8")
    monkeypatch.setattr(shell, "_GIT_BASH_CANDIDATES", ())
    monkeypatch.setattr(shell.shutil, "which",
                        lambda n: str(git_exe) if n == "git" else None)
    assert shell._pick_bash() == str(usr_bash)


def test_bash_detection_rejects_wsl_and_missing_git(tmp_path, monkeypatch):
    """System32 下的是 WSL,不能当 Git Bash;连 git 都没有时返回 None。"""
    monkeypatch.setattr(shell, "_GIT_BASH_CANDIDATES", ())
    wsl = r"C:\Windows\System32\bash.exe"
    monkeypatch.setattr(shell.shutil, "which",
                        lambda n: wsl if n == "bash" else None)
    assert shell._pick_bash() is None

    monkeypatch.setattr(shell.shutil, "which", lambda n: None)
    assert shell._pick_bash() is None
