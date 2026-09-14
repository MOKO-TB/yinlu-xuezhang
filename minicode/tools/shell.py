"""命令执行工具:优先 Git Bash,回退 cmd。"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from minicode.tools.base import ToolContext

DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 300
TIMEOUT_OUTPUT_LIMIT = 4000

# C:\Windows\System32\bash.exe 是 WSL,不能当作 Git Bash 使用
_WSL_MARKERS = ("system32",)
_GIT_BASH_CANDIDATES = (
    r"C:\Program Files\Git\bin\bash.exe",
    r"C:\Program Files (x86)\Git\bin\bash.exe",
)


def _bash_beside_git() -> str | None:
    r"""Git 装在非标准位置时,从 git.exe 的位置反推 bash。

    固定候选表只盖得住默认的 C:\Program Files\Git;换盘符/换安装方式的机器
    (如 D:\git\Git)会漏掉,于是静默退回 cmd——`sleep`/`grep` 这类命令直接报
    "不是内部或外部命令",而且沙箱超时也测不出来。
    """
    git = shutil.which("git")
    if not git:
        return None
    p = Path(git).resolve()
    # 安装包版在 <root>\cmd\git.exe;其它安装方式可能只多一层,两种都试
    for root in (p.parent.parent, p.parent.parent.parent):
        for rel in (Path("bin") / "bash.exe", Path("usr") / "bin" / "bash.exe"):
            cand = root / rel
            if cand.exists() and not any(m in str(cand).lower()
                                         for m in _WSL_MARKERS):
                return str(cand)
    return None


def _pick_bash() -> str | None:
    found = shutil.which("bash")
    if found and not any(m in found.lower() for m in _WSL_MARKERS):
        return found
    for cand in _GIT_BASH_CANDIDATES:
        if os.path.exists(cand):
            return cand
    return _bash_beside_git()


def resolve_shell_argv(shell_cfg: str) -> list[str] | None:
    """把 shell 配置解析为命令前缀 argv;返回 None 表示用 shell=True(cmd)。"""
    shell = (shell_cfg or "auto").lower()
    if shell == "cmd":
        return None
    if shell == "powershell":
        return ["powershell", "-NoProfile", "-Command"]
    bash = _pick_bash()
    if bash and shell in ("auto", "bash"):
        return [bash, "-c"]
    return None


def _decode(data: bytes) -> str:
    for enc in ("utf-8", "gbk"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def bash(ctx: ToolContext, command: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    """在工作区目录执行一条 shell 命令,返回退出码与输出。"""
    timeout = min(max(1, int(timeout)), MAX_TIMEOUT)
    state = getattr(ctx, "sandbox", None)
    if state is not None:                      # 沙箱收紧执行超时上限
        timeout = state.policy.command_timeout(timeout)
    argv = resolve_shell_argv(ctx.config.shell)
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}

    try:
        if argv is None:
            proc = subprocess.run(
                command, shell=True, cwd=ctx.workspace, capture_output=True,
                timeout=timeout, creationflags=creationflags, env=env,
            )
        else:
            proc = subprocess.run(
                [*argv, command], cwd=ctx.workspace, capture_output=True,
                timeout=timeout, creationflags=creationflags, env=env,
            )
    except subprocess.TimeoutExpired as e:
        partial = _decode(e.stdout or b"")
        return (f"错误: 命令超时(>{timeout}s),已被终止。"
                f"已产生的输出:\n{partial[:TIMEOUT_OUTPUT_LIMIT]}")

    out = _decode(proc.stdout or b"").strip()
    err = _decode(proc.stderr or b"").strip()
    sections = [f"退出码: {proc.returncode}"]
    if out:
        sections.append(f"--- stdout ---\n{out}")
    if err:
        sections.append(f"--- stderr ---\n{err}")
    if proc.returncode != 0 and not out and not err:
        sections.append("(无输出。命令可能不存在,或需要不同的参数。)")
    return "\n".join(sections)
