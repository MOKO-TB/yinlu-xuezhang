"""命令执行工具:优先 Git Bash,回退 cmd。

两条会动手的入口(bash / py_script)都是**同步等结果**,上限 `MAX_TIMEOUT = 300` 秒。
对"跑一遍全量测试/装依赖/构建"这类活儿,300 秒不是够不够的问题,是**根本跑不完**:
超时会把进程杀掉并把已产生的输出倒给用户,任务等于白跑一趟。所以 `bash` 多一个
`background=true`:放后台跑、立刻返回一个 job 编号,再用 `bash_job` 查进度/看输出/终止。
"""
from __future__ import annotations

import atexit
import contextlib
import os
import shutil
import subprocess
import sys
import threading
import time
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


def bash(ctx: ToolContext, command: str, timeout: int = DEFAULT_TIMEOUT,
         background: bool = False) -> str:
    """在工作区目录执行一条 shell 命令,返回退出码与输出。

    `background=true` 时**不等结果**:命令放后台跑,立刻返回一个 job 编号,
    之后用 `bash_status` 看进度与输出、`bash_kill` 终止(实现见 `_BgJob`)。
    """
    argv = resolve_shell_argv(ctx.config.shell)
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    env, dropped = _child_env(ctx)
    env_note = _env_note(dropped)
    if background:
        return _bg_start(ctx, command, argv, env, creationflags, env_note)

    timeout = min(max(1, int(timeout)), MAX_TIMEOUT)
    state = getattr(ctx, "sandbox", None)
    if state is not None:                      # 沙箱收紧执行超时上限
        timeout = state.policy.command_timeout(timeout)

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


# ------------------------------------------------------------------ 后台命令

MAX_BG_JOBS = 12               # 同时在跑的后台任务上限(超了就拒绝,不静默挤掉旧的)
BG_KEEP_FINISHED = 5           # 已结束且已播报过的任务最多留几个(清历史,不清在跑的)
BG_BUFFER_CHARS = 200_000      # 每个任务只在内存里留尾部这么多字符
BG_TAIL_DEFAULT = 4000         # bash_status 一次回看的默认长度
BG_TAIL_MAX = 30_000
BG_KILL_WAIT = 3.0             # 杀完之后最多再等这么久确认它真退了

_bg_lock = threading.Lock()
_bg_jobs: dict[str, "_BgJob"] = {}
_bg_seq = 0


class _BgJob:
    """一条放后台跑的命令:输出读进**内存**缓冲,不落盘。

    为什么不落盘:多一个 `bg-jobs/*.log` 就要同时进 `.gitignore`、打包清理清单、
    迁移包清单和 SECURITY 四处税(本仓库对个人数据边界的规矩),而这种输出天生是
    "这一程的临时物" —— 应用退出后不该再有人拿到它。代价照实说:**重启就没了**。

    stdout 与 stderr 合到**同一条管道**按时间顺序存。分两条读要么起两个线程,
    要么先 `wait()` 再读 —— 后者在另一条管道写满时会把子进程卡死,是这类功能最
    经典的坑(全量 pytest 的进度条正好大量走 stderr)。
    """

    def __init__(self, job_id: str, proc, command: str, env_note: str = ""):
        self.id = job_id
        self.proc = proc
        self.command = " ".join(str(command).split())[:120]
        self.env_note = env_note
        self.started = time.monotonic()
        self.chunks: list[str] = []
        self.kept = 0
        self.dropped = 0
        self.killed = False
        self.announced = False      # 结束状态有没有已经告知过模型(只播一次)
        self.lock = threading.Lock()

    # -------------------------------------------------------------- 读输出
    def _pump(self) -> None:
        """后台线程:按行读,EOF 之后再取退出码。异常只进缓冲,不外抛。"""
        try:
            stream = self.proc.stdout
            while True:
                line = stream.readline() if stream is not None else b""
                if not line:
                    break
                self._append(_decode(line))
        except Exception as e:            # 强杀时管道会抛,不能带崩应用
            self._append(f"\n(读取输出中断:{type(e).__name__}: {e})\n")

    def _append(self, text: str) -> None:
        with self.lock:
            self.chunks.append(text)
            self.kept += len(text)
            while self.kept > BG_BUFFER_CHARS and len(self.chunks) > 1:
                drop = self.chunks.pop(0)
                self.kept -= len(drop)
                self.dropped += len(drop)

    # -------------------------------------------------------------- 给人看的
    @property
    def running(self) -> bool:
        return self.proc.poll() is None

    @property
    def exit_code(self) -> int | None:
        return self.proc.poll()

    def line(self) -> str:
        if self.running:
            state = f"运行中 {int(time.monotonic() - self.started)} 秒"
        else:
            state = (f"已结束,退出码 {self.exit_code}"
                     + ("(被终止)" if self.killed else ""))
        lost = f",前 {self.dropped} 字已被丢出缓冲" if self.dropped else ""
        return (f"{self.id} {state} · 已输出 {self.kept} 字{lost} · "
                f"{self.command}")

    def tail(self, n: int) -> str:
        with self.lock:
            body = "".join(self.chunks)
            dropped = self.dropped
        head = (f"(缓冲只留尾部 {BG_BUFFER_CHARS} 字;前面 {dropped} 字已丢掉,"
                "要完整结果请把命令输出重定向到工作区文件再读。)\n"
                if dropped else "")
        if len(body) > n:
            return head + "…(截掉前面 " + f"{len(body) - n} 字)\n" + body[-n:]
        return head + (body or "(还没有输出)")


def _bg_new_id() -> str:
    global _bg_seq
    _bg_seq += 1
    return f"j{_bg_seq}"


def _bg_start(ctx: ToolContext, command: str, argv: list[str] | None,
              env: dict, creationflags: int, env_note: str) -> str:
    """把命令放进后台;返回给模型看的那句话(含 job 编号与下一步怎么查)。"""
    with _bg_lock:
        alive = [j for j in _bg_jobs.values() if j.running]
        if len(alive) >= MAX_BG_JOBS:
            return (f"错误: 已经有 {len(alive)} 个后台任务在跑(上限 {MAX_BG_JOBS}),"
                    "先 `bash_kill` 掉不要的,或者等它们结束。")
        job_id = _bg_new_id()
    try:
        proc = subprocess.Popen(
            ([*argv, command] if argv is not None else command),
            shell=(argv is None), cwd=str(ctx.workspace),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, env=env, creationflags=creationflags)
    except Exception as e:
        return f"错误: 后台启动失败({type(e).__name__}: {e})"
    job = _BgJob(job_id, proc, command, env_note)
    with _bg_lock:
        _bg_jobs[job_id] = job
        _bg_prune_locked()        # 在跑的不受这条影响:只清已结束且播过的历史
    threading.Thread(target=job._pump, daemon=True,
                     name=f"bg-{job_id}").start()
    return (f"已放进后台:{job_id}(pid={proc.pid}) · {job.command}\n"
            "它不会因为这一轮结束而停止 —— 跑完了去看:`bash_status`(id 传 "
            f"{job_id})拿输出尾部;要停:`bash_kill`(id 传 {job_id})。\n"
            "注意两条:后台跑**不受 timeout 限制**,也就没人替它超时收手;输出只在"
            "内存缓冲里(留尾部),**学长退出或电脑重启就没了**,要留档请重定向到工作区文件。")


def _bg_kill_tree(job: _BgJob) -> tuple[bool, str]:
    """终止整棵进程树。

    Git Bash 那条命令的直接子进程是 `bash.exe`,真正干活的是它下面的
    python / pytest —— 只杀 bash 会留下一堆孤儿继续吃 CPU。所以 Windows 上走
    `taskkill /T /F`(连子进程一起)。机器上没有 taskkill(极少数精简系统)时
    只能杀直接子进程,**并如实说子进程可能还活着**,不假装杀干净。
    """
    killed_tree = False
    if sys.platform == "win32":
        taskkill = shutil.which("taskkill")
        if taskkill:
            with contextlib.suppress(Exception):
                subprocess.run([taskkill, "/PID", str(job.proc.pid), "/T", "/F"],
                               capture_output=True, timeout=15,
                               creationflags=(subprocess.CREATE_NO_WINDOW
                                              if sys.platform == "win32" else 0))
            killed_tree = True
    if not killed_tree or job.running:
        with contextlib.suppress(Exception):
            job.proc.kill()
    job.killed = True
    deadline = time.monotonic() + BG_KILL_WAIT
    while job.running and time.monotonic() < deadline:
        time.sleep(0.05)
    if job.running:
        return (False, ("仍在运行:已发出终止,但它没在 "
                        f"{int(BG_KILL_WAIT)} 秒内退出。"
                        + ("" if killed_tree else
                           "(这台机器上没有 taskkill,只能杀直接子进程,"
                           "它下面的子进程可能还活着)")))
    return (True, "" if killed_tree or sys.platform != "win32" else
            "(没有 taskkill,只杀了直接子进程 —— 下面的子进程若还活着,学长无从确认)")


def _bg_job_or_error(job_id: str) -> tuple["_BgJob | None", str]:
    key = str(job_id or "").strip()
    with _bg_lock:
        known = list(_bg_jobs)
    if not key:
        return None, (f"错误: 要传 id(本次运行起过的后台任务:{', '.join(known) or '还没有'})。")
    job = _bg_jobs.get(key if key.startswith("j") else f"j{key}")
    if job is None:
        return None, (f"错误: 没有后台任务 {key}。本次运行起过的:"
                      f"{', '.join(known) or '(无)'}")
    return job, ""


def _bg_shutdown() -> None:
    """应用退出时收掉还在跑的后台任务,不留孤儿进程。

    这是「后台」这条能力**唯一的生命周期边界**(它不受 timeout 管),所以写成
    atexit 而不是靠人记得杀 —— 与 `browser.shutdown()` 同一个手法。
    """
    with _bg_lock:
        jobs = list(_bg_jobs.values())
    for job in jobs:
        if job.running:
            with contextlib.suppress(Exception):
                _bg_kill_tree(job)


atexit.register(_bg_shutdown)


def bash_status(ctx: ToolContext, id: str = "", tail: int = BG_TAIL_DEFAULT) -> str:
    """`bash_status` 工具:看后台任务在不在跑、跑到哪了、输出了什么。"""
    if str(id or "").strip():
        job, err = _bg_job_or_error(id)
        if job is None:
            return err
        n = max(0, min(int(tail or BG_TAIL_DEFAULT), BG_TAIL_MAX))
        return (f"{job.line()}\n"
                f"--- 输出尾部(最后 {n} 字)---\n{job.tail(n)}{job.env_note}")
    with _bg_lock:
        jobs = list(_bg_jobs.values())
    if not jobs:
        return ("还没有后台任务。`bash` 传 background=true 就会放后台跑,"
                "适合全量测试/装依赖/构建这类超过 300 秒的活儿。")
    return ("本次运行的后台任务:\n"
            + "\n".join("- " + j.line() for j in jobs)
            + f"\n(每个任务最多在内存里留尾部 {BG_BUFFER_CHARS} 字;"
              "学长退出时会被收掉)")


def bash_kill(ctx: ToolContext, id: str) -> str:
    """`bash_kill` 工具:终止一个后台任务(连它的子进程一起)。"""
    job, err = _bg_job_or_error(id)
    if job is None:
        return err
    if not job.running:
        return f"{job.id} 已经结束(退出码 {job.exit_code}),不用杀。"
    ok, note = _bg_kill_tree(job)
    return (f"{job.id} 已终止。{note}" if ok else f"错误: {job.id} {note}")


def _bg_list_snapshot() -> list[str]:
    """给测试与界面用的编号清单(不暴露进程对象)。"""
    with _bg_lock:
        return list(_bg_jobs)


def bg_brief_for_model() -> str:
    """本机后台任务的现状一行,给**本次请求**用;没有任务时返回空串。

    为什么要这一行:后台任务的处境是"模型容易忘记去查"——它派完活就往下做,
    下一步直接跟用户说"跑完了",而进程其实还在跑。等它想起来调 `bash_status`
    往往要再一轮。

    为什么挂在请求尾部而不是系统提示词里:系统提示词是**整段前缀**,每步往里加
    变化的内容会把 KV Cache 从那里打断(与 D0 时钟"按天重建而不是每轮注入一行
    时间"是同一套取舍)。所以这段只作为一条末尾的临时消息发出去,**不进留档**,
    历史前缀一个字不动;没有后台任务时更是空串,一个字都不注入。

    播几次:还在跑的每一步都播(状态在变);跑完的**只播一次**(`announced` 记账),
    免得一条旧任务的死讯每步都跟模型说一遍。
    """
    with _bg_lock:
        picked = [j for j in _bg_jobs.values() if j.running or not j.announced]
        for j in picked:
            if not j.running:
                j.announced = True
    if not picked:
        return ""
    lines = "\n".join("- " + j.line() for j in picked)
    return (f"〔本机后台命令现状(这一行只活在本次请求里,没有进留档;"
            f"共 {len(_bg_list_snapshot())} 个任务曾起过)〕\n{lines}\n"
            "要看输出调 bash_status(id=…),要停调 bash_kill(id=…)。"
            "**别替用户假设它已经跑完了** —— 只有 poll 到的状态才算数。")


def _bg_prune_locked() -> None:
    """清掉"已结束且已播报过"的老任务,只留最近 BG_KEEP_FINISHED 个。

    为什么要清:上限 12 只管**在跑的**,而历史任务会一直堆在表里 —— 不清就是
    只涨不落,还会把 bash_status 的清单撑成一屏旧账。
    """
    done = [j for j in _bg_jobs.values() if not j.running and j.announced]
    for j in sorted(done, key=lambda x: x.started)[:-BG_KEEP_FINISHED]:
        _bg_jobs.pop(j.id, None)


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
