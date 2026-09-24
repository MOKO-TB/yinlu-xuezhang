"""命令执行工具:优先 Git Bash,回退 cmd。"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from minicode.sandbox import scrub_env
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


def _child_env(ctx: ToolContext, extra: dict | None = None
               ) -> tuple[dict, list[str]]:
    """子进程环境:UTF-8 开关照旧,凭据类变量按沙箱开关摘掉。

    摘掉的是**给脚本的那份**,应用自己要用的 key 在启动时已读进内存;
    摘了多少个会在输出末尾如实写出来(不静默改变脚本能看到的 worlds)。
    """
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", **(extra or {})}
    state = getattr(ctx, "sandbox", None)
    if state is not None and getattr(state.policy, "strip_env_secrets", True):
        env, dropped = scrub_env(env)
        return env, dropped
    return env, []


def _env_note(dropped: list[str]) -> str:
    if not dropped:
        return ""
    shown = ", ".join(dropped[:6]) + ("…" if len(dropped) > 6 else "")
    return (f"\n(沙箱已从子进程环境里摘掉 {len(dropped)} 个名字像凭据的变量:{shown};"
            "需要它们请在 config.json 把 sandbox.strip_env_secrets 设为 false)")


def bash(ctx: ToolContext, command: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    """在工作区目录执行一条 shell 命令,返回退出码与输出。"""
    timeout = min(max(1, int(timeout)), MAX_TIMEOUT)
    state = getattr(ctx, "sandbox", None)
    if state is not None:                      # 沙箱收紧执行超时上限
        timeout = state.policy.command_timeout(timeout)
    argv = resolve_shell_argv(ctx.config.shell)
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    env, dropped = _child_env(ctx)
    env_note = _env_note(dropped)

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
                f"已产生的输出:\n{partial[:TIMEOUT_OUTPUT_LIMIT]}{env_note}")

    out = _clip(_decode(proc.stdout or b"").strip())
    err = _clip(_decode(proc.stderr or b"").strip())
    sections = [f"退出码: {proc.returncode}"]
    if out:
        sections.append(f"--- stdout ---\n{out}")
    if err:
        sections.append(f"--- stderr ---\n{err}")
    if proc.returncode != 0 and not out and not err:
        sections.append("(无输出。命令可能不存在,或需要不同的参数。)")
    return "\n".join(sections) + env_note


BASH_OUTPUT_LIMIT = 6000


def _clip(text: str) -> str:
    """单路输出截断:整段不限量会把上下文一次性撑爆。"""
    if len(text) <= BASH_OUTPUT_LIMIT:
        return text
    return (text[:BASH_OUTPUT_LIMIT]
            + f"\n…(输出过长,只保留了前 {BASH_OUTPUT_LIMIT} 字,"
            + f"共 {len(text)} 字;要完整结果请把命令输出重定向到工作区文件再读)")


# ------------------------------------------------------- py_script(内置运行时)

PY_SCRIPT_OUTPUT_LIMIT = 8000


def _py_runtime_command() -> list[str] | None:
    """拿到"能跑脚本的内置解释器"命令行。

    源码态就是当前 venv 的 python;打包版没有裸 python 可用,走 CLI exe 的
    `--py` 模式(main.py 里实现)—— 它带着打包进去的 pptx/docx/openpyxl/pypdf。
    单文件版没有 CLI 伙伴,返回 None 由调用方给出可读错误。
    """
    if getattr(sys, "frozen", False):
        cli = Path(sys.executable).parent / "引路学长-CLI.exe"
        return [str(cli), "--py"] if cli.exists() else None
    return [sys.executable, "-X", "utf8"]


def py_script(ctx: ToolContext, path: str, timeout: int = 120) -> str:
    """py_script 工具:用应用内置的 Python 运行时执行一个脚本文件。

    学生机器上不一定装了 Python,但打包版里带着完整解释器与文档库
    (python-pptx / python-docx / openpyxl / pypdf)—— 转换类技能生成的脚本
    都靠这个工具跑。脚本必须先写进工作区(write_file),路径受沙箱约束。
    """
    from minicode.tools.base import PathBlocked, PathOutsideWorkspace, resolve_path

    raw = str(path or "").strip()
    if not raw:
        return "错误: 缺少脚本路径。先用 write_file 把脚本写进工作区,再把相对路径传进来。"
    try:
        target = resolve_path(ctx, raw)
    except (PathBlocked, PathOutsideWorkspace) as e:
        return f"错误: {e}"
    if not target.is_file():
        return f"错误: 脚本不存在: {raw}。先用 write_file 把脚本写进工作区再运行。"
    timeout = max(10, min(int(timeout), MAX_TIMEOUT))
    sandbox = getattr(ctx, "sandbox", None)
    if sandbox is not None:
        sandbox_timeout = int(getattr(sandbox.policy, "timeout_seconds", 0) or 0)
        if sandbox_timeout > 0:
            timeout = min(timeout, sandbox_timeout)
    base = _py_runtime_command()
    if base is None:
        return ("错误: 当前是单文件版,没有内置脚本运行时(缺 引路学长-CLI.exe)。"
                "请改用安装包/便携版,或把脚本内容直接展示给用户手动运行。")
    cmd = [*base, str(target)]
    env, dropped = _child_env(ctx, {"PYTHONUTF8": "1"})
    env_note = _env_note(dropped)
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        proc = subprocess.run(cmd, cwd=ctx.workspace, capture_output=True,
                              timeout=timeout, env=env, creationflags=creationflags)
    except subprocess.TimeoutExpired as e:
        partial = _decode(e.stdout or b"")
        return (f"错误: 脚本运行超过 {timeout} 秒被中止。已产生的输出:\n"
                f"{partial[:TIMEOUT_OUTPUT_LIMIT]}{env_note}")
    out = _decode(proc.stdout or b"").strip()
    err = _decode(proc.stderr or b"").strip()
    sections = [f"退出码: {proc.returncode}"]
    if out:
        sections.append(f"--- stdout ---\n{out}")
    if err:
        sections.append(f"--- stderr ---\n{err}")
    text = "\n".join(sections)
    if len(text) > PY_SCRIPT_OUTPUT_LIMIT:
        text = text[:PY_SCRIPT_OUTPUT_LIMIT] + "\n…(输出过长,已截断)"
    if proc.returncode != 0 and not out and not err:
        text += "\n(无输出。脚本可能抛了异常却没打印,检查代码或让脚本 print 关键步骤。)"
    return text + env_note
