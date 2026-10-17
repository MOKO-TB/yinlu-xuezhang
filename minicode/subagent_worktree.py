"""子代理显式 Git worktree 隔离。

这里只解决“文件改动互不踩”的问题：每路子代理拿一份独立工作副本，主工作区不被
直接写入。它不是 OS 级安全沙箱，也不能阻止命令主动访问副本之外；文件/网络权限仍
由既有的 PermissionGate 与 SandboxState 管。

边界：
- 只从 Git 仓库创建；不是仓库就明确失败，不退化成复制目录。
- 从当前 HEAD 建 detached worktree，并把**已跟踪的未提交改动**以二进制 patch 带入。
- 未跟踪与 ignored 文件不复制，报告中明确计数。
- 完成后不自动合并、不自动删除；路径回给用户/主代理人工审阅。
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
_GIT_TIMEOUT = 30
_STATUS_LIMIT = 20


class WorktreeUnavailable(RuntimeError):
    """当前工作区无法创建子代理隔离副本。"""


@dataclass(frozen=True)
class IsolatedWorktree:
    repo_root: Path
    root: Path
    workspace: Path
    base: str
    parent_dirty: int
    untracked_omitted: int
    tracked_patch: bool

    def as_dict(self) -> dict:
        return {
            "repo_root": str(self.repo_root),
            "root": str(self.root),
            "workspace": str(self.workspace),
            "base": self.base,
            "parent_dirty": self.parent_dirty,
            "untracked_omitted": self.untracked_omitted,
            "tracked_patch": self.tracked_patch,
        }


def _git_bin() -> str:
    return os.environ.get("MINICODE_GIT") or "git"


def _run(args: list[str], cwd: Path, *, input_bytes: bytes | None = None) -> bytes:
    try:
        proc = subprocess.run(
            [_git_bin(), *args],
            cwd=str(cwd),
            input=input_bytes,
            capture_output=True,
            stdin=subprocess.DEVNULL if input_bytes is None else None,
            timeout=_GIT_TIMEOUT,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise WorktreeUnavailable(f"git 起不来：{type(e).__name__}: {e}") from e
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or b"").decode("utf-8", "replace").strip()
        raise WorktreeUnavailable(detail[:500] or f"git 退出码 {proc.returncode}")
    return proc.stdout


def _text(args: list[str], cwd: Path) -> str:
    return _run(args, cwd).decode("utf-8", "replace").strip()


def _repo_root(workspace: Path) -> Path:
    try:
        raw = _text(["rev-parse", "--show-toplevel"], workspace)
    except WorktreeUnavailable as e:
        raise WorktreeUnavailable(f"当前工作区不是可用的 Git 仓库：{e}") from e
    if not raw:
        raise WorktreeUnavailable("当前工作区不是可用的 Git 仓库：git 没有返回仓库根目录")
    return Path(raw).resolve()


def _status_lines(repo_root: Path) -> list[str]:
    raw = _text(["status", "--porcelain", "--untracked-files=all"], repo_root)
    return [line for line in raw.splitlines() if line.strip()]


def _cleanup_failed(root: Path, target: Path) -> None:
    try:
        _run(["worktree", "remove", "--force", str(target)], root)
    except WorktreeUnavailable:
        pass


def create(workspace: Path) -> IsolatedWorktree:
    """从当前仓库状态创建一份隔离副本；失败时抛 WorktreeUnavailable。"""
    ws = Path(workspace).resolve()
    repo_root = _repo_root(ws)
    try:
        relative_workspace = ws.relative_to(repo_root)
    except ValueError as e:
        raise WorktreeUnavailable(f"工作区不在 Git 仓库根目录之下：{ws}") from e

    base = _text(["rev-parse", "HEAD"], repo_root)
    if not base:
        raise WorktreeUnavailable("仓库还没有 HEAD 提交，无法创建隔离副本")
    status = _status_lines(repo_root)
    untracked = sum(1 for line in status if line.startswith("?? "))
    tracked_dirty = len(status) - untracked
    patch = _run(["diff", "--binary", "--no-ext-diff", "--no-color", "HEAD", "--"],
                 repo_root) if tracked_dirty else b""

    key = hashlib.sha256(str(repo_root).lower().encode("utf-8")).hexdigest()[:12]
    parent = Path(tempfile.gettempdir()) / "minicode-subagent-worktrees" / key
    target = parent / f"wt-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise WorktreeUnavailable(f"隔离副本目录建不起来：{e}") from e

    _run(["worktree", "add", "--detach", "--quiet", str(target), base], repo_root)
    try:
        if patch:
            _run(["apply", "--binary", "--whitespace=nowarn", "-"], target,
                 input_bytes=patch)
    except Exception:
        _cleanup_failed(repo_root, target)
        raise

    return IsolatedWorktree(
        repo_root=repo_root,
        root=target.resolve(),
        workspace=(target / relative_workspace).resolve(),
        base=base,
        parent_dirty=tracked_dirty,
        untracked_omitted=untracked,
        tracked_patch=bool(patch),
    )


def header(info: IsolatedWorktree, type_key: str) -> str:
    """机器可解析的一行来源说明，路径加引号避免空格截断。"""
    return (
        f"[worktree|{type_key}] isolated={json.dumps(str(info.workspace), ensure_ascii=False)}"
        f" base={info.base[:12]} parent_dirty={info.parent_dirty}"
        f" untracked_omitted={info.untracked_omitted}"
        f" tracked_patch={'yes' if info.tracked_patch else 'no'} merge=manual\n"
    )


def pending_note(info: IsolatedWorktree, type_key: str) -> str:
    """超时路径专用：线程还活着，绝不能说它已结束或删掉它的副本。"""
    return (
        f"\n[worktree|{type_key}] 隔离副本仍在 {json.dumps(str(info.workspace), ensure_ascii=False)}；"
        "主代理已停止等待，但这一路仍可能继续写。副本不会自动合并或删除。"
    )


def summarize(info: IsolatedWorktree, type_key: str) -> str:
    """完成后列真实改动；只读事实，不 merge、不 cleanup。"""
    try:
        status = _status_lines(info.root)
        stat = _text(["diff", "--stat", "HEAD", "--"], info.root)
    except WorktreeUnavailable as e:
        return f"\n[worktree-diff|{type_key}] 改动清单读不出来：{e}"

    shown = status[:_STATUS_LIMIT]
    more = len(status) - len(shown)
    lines = [f"\n[worktree-diff|{type_key}] files={len(status)}"]
    lines.extend("  " + line.replace("\r", " ").replace("\n", " ") for line in shown)
    if more > 0:
        lines.append(f"  …另有 {more} 项")
    if stat:
        lines.append("  diffstat: " + " | ".join(
            line.strip() for line in stat.splitlines() if line.strip()))
    if not status:
        lines.append("  (副本相对基线与带入改动均无新增变化)")
    return "\n".join(lines)


def _isolated_base() -> Path:
    return Path(tempfile.gettempdir()) / "minicode-subagent-worktrees"


def is_isolated_path(path: Path) -> bool:
    """只允许操作本模块创建的临时 worktree，拒绝任意目录。"""
    try:
        p = Path(path).resolve()
        base = _isolated_base().resolve()
        return p != base and p.is_relative_to(base)
    except (OSError, ValueError):
        return False


def _git_text(args: list[str], cwd: Path) -> str:
    return _run(args, cwd).decode("utf-8", "replace").strip()


def _changed_paths(root: Path) -> list[str]:
    raw = _run(["diff", "--name-only", "-z", "HEAD", "--"], root)
    return [p for p in raw.decode("utf-8", "replace").split("\0") if p]


def _prepare_changes(root: Path) -> None:
    """把未跟踪文件标成 intent-to-add，让二进制 diff 也能带出来。"""
    _run(["add", "-N", "-A", "--", "."], root)


def review(path: Path, limit: int = 12000) -> str:
    """审阅一份隔离副本，不写主工作区。"""
    root = Path(path).resolve()
    if not is_isolated_path(root):
        return "错误: 只能审阅本程序创建的隔离 worktree。"
    try:
        _prepare_changes(root)
        status = _git_text(["status", "--porcelain", "--untracked-files=all"], root)
        stat = _git_text(["diff", "--stat", "HEAD", "--"], root)
        patch = _run(["diff", "--binary", "--no-ext-diff", "--no-color",
                      "HEAD", "--"], root)
    except WorktreeUnavailable as e:
        return f"错误: 审阅隔离副本失败:{e}"
    body = []
    if status:
        body.append("改动状态:\n" + status)
    if stat:
        body.append("改动统计:\n" + stat)
    diff_text = patch.decode("utf-8", "replace")
    if diff_text:
        body.append("完整 diff:\n" + diff_text)
    text = "\n\n".join(body) or "(隔离副本没有改动)"
    if len(text) > limit:
        text = text[:limit] + f"\n…(diff 超过 {limit} 字,已截断)"
    return f"[worktree|review] {root}\n{text}"


def apply_to_workspace(path: Path, target: Path, *, allow_dirty: bool = False,
                       dry_run: bool = False) -> str:
    """把隔离副本相对 HEAD 的改动应用回目标 Git 仓库，不提交。"""
    root = Path(path).resolve()
    target = Path(target).resolve()
    if not is_isolated_path(root):
        return "错误: 只能应用本程序创建的隔离 worktree。"
    try:
        repo = Path(_git_text(["rev-parse", "--show-toplevel"], target)).resolve()
    except (WorktreeUnavailable, OSError, ValueError) as e:
        return f"错误: 目标工作区不是可用的 Git 仓库:{e}"
    if repo != target:
        return ("错误: 当前实现只允许把副本应用回 Git 仓库根目录;"
                f"目标仓库根是 {repo}。")
    try:
        _prepare_changes(root)
        paths = _changed_paths(root)
        if not paths:
            return "隔离副本没有改动，什么都没应用。"
        if not allow_dirty:
            dirty = _run(["status", "--porcelain", "--untracked-files=all",
                          "--", *paths], target)
            if dirty.strip():
                return ("错误: 目标工作区这些路径已有未提交改动，拒绝覆盖。"
                        "先处理干净，或显式传 allow_dirty=true。\n"
                        + dirty.decode("utf-8", "replace").strip())
        patch = _run(["diff", "--binary", "--no-ext-diff", "--no-color",
                      "HEAD", "--"], root)
        _run(["apply", "--check", "--binary", "-"], target, input_bytes=patch)
        if dry_run:
            return f"[worktree|apply] 预检通过，未落盘。可应用 {len(paths)} 个路径。"
        _run(["apply", "--binary", "-"], target, input_bytes=patch)
    except WorktreeUnavailable as e:
        return f"错误: 应用隔离副本失败:{e}"
    return (f"[worktree|apply] 已应用 {len(paths)} 个路径到 {target};"
            "未提交、未删除隔离副本。")


def worktree_apply_tool(ctx, action: str = "diff", path: str = "",
                        allow_dirty: bool = False, dry_run: bool = False) -> str:
    """工具处理器：审阅/应用隔离副本。"""
    root = Path(str(path or "").strip())
    if not path:
        return "错误: 请传隔离副本路径(path)。"
    if action == "diff":
        return review(root)
    if action == "apply":
        return apply_to_workspace(root, Path(ctx.workspace),
                                  allow_dirty=bool(allow_dirty),
                                  dry_run=bool(dry_run))
    return "错误: action 只能是 diff 或 apply。"
