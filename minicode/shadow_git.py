"""Shadow Git 检查点(H,v0.9.157):把**整个工作区**在改动前快照进一个影子 git 库。

**它补的是自建 undo 记不住的那一半。** `checkpoints.py` 记的是"agent 亲手写的每一份
文件"—— 那些快照挂在 `write_file` / `edit_file` / `apply_patch` 的 handler 上。于是
**`bash` / `py_script` 里间接改过的文件根本没人告诉它**(HANDOFF §4 里那条"并行写的
内容竞争没有程序闸"旁边就记着同一个形状的实话:`bash/py_script` 里间接写的文件闸
管不住)。这里记的是"这一刻整个工作区长什么样",所以那一半也进得来。

**四条写死的规矩**:

1. **绝不碰用户仓库的 `.git`。** 影子库在**应用数据目录**里(`<数据目录>/shadow-git/
   <工作区键>/`),用 `--git-dir` 指过去、`--work-tree` 指回工作区。用户的 `git log`
   一条都不会多出来,`git status` 也不会多出东西 —— 这是 ROADMAP §四 H 那句
   "后者不动仓库历史"的全部含义(原文"最低实现"里写的 `git commit --no-verify`
   **是错的**:那样提交进的是**用户自己的历史**)。
2. **回滚只还原、绝不删除。** `restore_only` 用 `git checkout <sha> -- .`:它只写
   "那次快照里有"的路径,工作区里**之后新建的文件原样留着** —— 于是它不可能删掉
   用户在这期间手动建的东西。**"完整恢复到那一刻"是另一条路,而那条路要删文件,
   所以不做。**
3. **它是一张保险,不是一次同步。** 单次快照有时间上限,超时就**放弃这一次**并如实
   说,而不是让"每次动手前先等 30 秒"变成常态;`git` 不在、工作区不可写、影子库建
   不起来,一律**静默不生效但在报表里说得出为什么**。
4. **一个工作区一本账。** 键是工作区路径的哈希:两个项目共用一本的话,回滚会把另一个
   项目的文件也写回去 —— 那是拿用户的两个项目互相赌。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import time
from pathlib import Path

SHADOW_DIR_NAME = "shadow-git"
# 影子库自己的排除表。**不抄用户的 `.gitignore`** —— 那是**它仓库**的规矩,而影子库
# 要盖住的是"工作区里所有会被改的东西",两者不是一回事。这里只排"大且必然不是源码"
# 的那几类,别的都进(宁多勿少:漏进去的代价是快照慢一点,漏掉的代价是回滚不回来)。
DEFAULT_EXCLUDES = (
    "node_modules/", ".venv/", "venv/", "__pycache__/", "*.pyc",
    "dist/", "build/", "target/", ".pytest_cache/", ".mypy_cache/", ".ruff_cache/",
    "sessions/", "tool-outputs/", ".git/",
)
# 单次快照的时间上限。超了放弃这一次 —— 保险不该变成"每次动手前先等"。
SNAPSHOT_TIMEOUT = 20.0
COMMIT_TIMEOUT = 20.0
# 快照条数上限(影子库自己会长);超出之后**不主动删历史**(git 的 gc 会管),
# 这个数只用来限制"列出来给你看"的条数。
LIST_LIMIT = 30


def git_bin() -> str:
    """用哪个 git。留一个环境变量口子,是为了测试与"本机 git 在奇怪的位置"。"""
    return os.environ.get("MINICODE_GIT") or "git"


def available() -> bool:
    """这台机器上有没有 git。没有就整个功能不生效(而报表要说得出来)。"""
    try:
        r = subprocess.run([git_bin(), "--version"], capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def shadow_dir(workspace) -> Path | None:
    """这个工作区的影子库落在哪儿;算不出来回 None。"""
    try:
        ws = str(Path(workspace).resolve())
    except (OSError, ValueError):
        return None
    if not ws:
        return None
    key = hashlib.sha256(ws.encode("utf-8")).hexdigest()[:16]
    try:
        from minicode.paths import app_path
        return Path(app_path(SHADOW_DIR_NAME, key))
    except Exception:
        return None


def _run(args: list[str], cwd=None, timeout: float = COMMIT_TIMEOUT):
    return subprocess.run([git_bin(), *args], cwd=cwd, capture_output=True,
                          timeout=timeout, check=False)


def _git(git_dir: Path, args: list[str], cwd=None, timeout: float = COMMIT_TIMEOUT):
    return _run([f"--git-dir={git_dir}", *args], cwd=cwd, timeout=timeout)


def ensure(workspace) -> Path | None:
    """把影子库准备好(库 + 排除表),回它的 `.git` 路径;建不起来回 None。

    排除表**每次写一遍**:它只是一个文本,幂等,而这样升级版本时新加的那几条自动生效
    (否则老用户的影子库里永远缺那几条,而没有人会去手工补)。
    """
    d = shadow_dir(workspace)
    if d is None:
        return None
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    gd = d / ".git"
    if not gd.exists():
        try:
            r = _run(["init", "--quiet", str(d)])
        except (OSError, subprocess.SubprocessError):
            return None
        if r.returncode != 0:
            return None
    try:
        info = gd / "info"
        info.mkdir(parents=True, exist_ok=True)
        (info / "exclude").write_text("\n".join(DEFAULT_EXCLUDES) + "\n",
                                      encoding="utf-8")
    except OSError:
        pass
    return gd


def snapshot(workspace, label: str = "") -> str:
    """把工作区**整个**快照进影子库,回那次提交的 sha;做不到就回空串。

    **什么时候调**:一轮里**第一次要动文件之前**(惰性),而不是每轮开头 ——
    只读的回合不该为此付钱。调用方见 `agent._shadow_guard`。
    """
    if not available():
        return ""
    gd = ensure(workspace)
    if gd is None:
        return ""
    ws = Path(workspace)
    try:
        add = _git(gd, ["add", "-A", "--", "."], cwd=ws, timeout=SNAPSHOT_TIMEOUT)
        if add.returncode != 0:
            return ""
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        msg = f"检查点 {stamp}" + (f" · {label}" if label else "")
        c = _git(gd, ["-c", "user.name=引路学长", "-c", "user.email=agent@localhost",
                      "-c", "commit.gpgsign=false", "-c", "core.hooksPath=",
                      "commit", "--no-verify", "--allow-empty", "--quiet",
                      "-m", msg], cwd=ws, timeout=COMMIT_TIMEOUT)
        if c.returncode != 0:
            return ""
        h = _git(gd, ["rev-parse", "HEAD"], cwd=ws)
    except (OSError, subprocess.SubprocessError):
        return ""
    return h.stdout.decode("utf-8", "replace").strip() if h.returncode == 0 else ""


def snapshots(workspace, limit: int = LIST_LIMIT) -> list[dict]:
    """列出最近的几次快照(新到旧);没有就回空列表。

    `files` 是那次快照记下的路径条数 —— 它同时是"这次回滚会写回几份"的预告。
    """
    gd = ensure(workspace)
    if gd is None or not available():
        return []
    try:
        log = _git(gd, ["log", f"-{max(1, int(limit))}",
                        "--pretty=format:%H%x1f%ct%x1f%s"], cwd=Path(workspace))
        if log.returncode != 0:
            return []
        out = []
        for line in log.stdout.decode("utf-8", "replace").splitlines():
            parts = line.split("\x1f")
            if len(parts) != 3:
                continue
            sha, ts, subject = parts
            out.append({"sha": sha, "ts": int(ts or 0), "subject": subject})
        return out
    except (OSError, subprocess.SubprocessError, ValueError):
        return []


def tracked_paths(workspace, sha: str) -> list[str]:
    """那次快照里记下的全部路径(工作区相对路径)。"""
    gd = ensure(workspace)
    if gd is None or not sha:
        return []
    try:
        r = _git(gd, ["ls-tree", "-r", "--name-only", sha], cwd=Path(workspace))
    except (OSError, subprocess.SubprocessError):
        return []
    if r.returncode != 0:
        return []
    return [p for p in r.stdout.decode("utf-8", "replace").splitlines() if p]


def restore_only(workspace, sha: str) -> tuple[int, str]:
    """把快照里记过的每一份**写回工作区**,**绝不删除任何东西**。回 `(份数, 说明)`。

    ⚠ **这一条就是 H 的安全边界。** `git checkout <sha> -- .` 只写"那次快照里有"的
    路径;工作区里**之后新建的文件它原样留着** —— 所以它不可能删掉用户在这期间手动
    建的东西。**"完整恢复到那一刻"要删文件,所以这一版没有那条路。**
    """
    if not sha:
        return 0, "没给快照 id。"
    gd = ensure(workspace)
    if gd is None or not available():
        return 0, "影子库用不了(git 不在,或数据目录不可写)。"
    paths = tracked_paths(workspace, sha)
    if not paths:
        return 0, f"那次快照里一份文件都没有({sha[:8]}),没什么可还原的。"
    ws = Path(workspace)
    try:
        r = _git(gd, ["checkout", sha, "--", "."], cwd=ws, timeout=SNAPSHOT_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        return 0, f"还原时出错:{type(e).__name__}: {e}"
    if r.returncode != 0:
        detail = r.stderr.decode("utf-8", "replace").strip()[:200]
        return 0, f"还原失败:{detail or 'git checkout 非零退出'}"
    return len(paths), ""


def _force_remove(func, path, _exc):
    """`rmtree` 的兜底:git 的对象文件是**只读**的,Windows 上直接删会 `WinError 5`。

    第一版就是这么失败的 —— `drop()` 回 False,而影子库还好好躺在那儿
    (现象是"我说删了,它还在",比报错更难查)。
    """
    try:
        os.chmod(path, 0o700)
        func(path)
    except OSError:
        raise


def drop(workspace) -> bool:
    """把这个工作区的影子库整个删掉(用户说"不要这份安全网"时用)。"""
    d = shadow_dir(workspace)
    if d is None or not d.exists():
        return False
    import shutil
    try:
        shutil.rmtree(d, onerror=_force_remove)
    except OSError:
        return False
    return True
