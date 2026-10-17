"""子代理 worktree 隔离：真 Git 建副本、带入已跟踪改动、不复制未跟踪/ignored。"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from minicode import subagent_worktree

GIT = shutil.which("git")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True,
                   capture_output=True, text=True)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / ".gitignore").write_text(".env\n", encoding="utf-8")
    (repo / "app.txt").write_text("v1\n", encoding="utf-8")
    sub = repo / "pkg"
    sub.mkdir()
    (sub / "main.py").write_text("print('v1')\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    return repo


@pytest.mark.skipif(GIT is None, reason="本机没有 git(显式跳过,不是静默)")
def test_create_copies_tracked_dirty_and_omits_untracked_and_ignored(tmp_path):
    repo = _repo(tmp_path)
    (repo / "app.txt").write_text("dirty tracked\n", encoding="utf-8")
    (repo / "new.txt").write_text("untracked\n", encoding="utf-8")
    (repo / ".env").write_text("SECRET=local\n", encoding="utf-8")

    info = subagent_worktree.create(repo / "pkg")
    try:
        assert info.repo_root == repo.resolve()
        assert info.root != repo.resolve()
        assert info.workspace == info.root / "pkg"
        assert (info.root / "app.txt").read_text(encoding="utf-8") == "dirty tracked\n"
        assert not (info.root / "new.txt").exists()
        assert not (info.root / ".env").exists()
        assert info.parent_dirty == 1
        assert info.untracked_omitted == 1
        assert info.tracked_patch is True
        header = subagent_worktree.header(info, "implement")
        assert header.startswith("[worktree|implement] isolated=")
        assert "untracked_omitted=1" in header

        (info.workspace / "main.py").write_text("print('v2')\n", encoding="utf-8")
        diff = subagent_worktree.summarize(info, "implement")
        assert "files=2" in diff              # dirty patch + this lane's edit
        assert "pkg/main.py" in diff
        assert (repo / "pkg" / "main.py").read_text(encoding="utf-8") == "print('v1')\n"
    finally:
        _git(repo, "worktree", "remove", "--force", str(info.root))


@pytest.mark.skipif(GIT is None, reason="本机没有 git(显式跳过,不是静默)")
def test_non_repo_fails_explicitly(tmp_path):
    with pytest.raises(subagent_worktree.WorktreeUnavailable, match="不是可用的 Git 仓库"):
        subagent_worktree.create(tmp_path)


@pytest.mark.skipif(GIT is None, reason="本机没有 git(显式跳过,不是静默)")
def test_review_and_apply_isolated_changes_without_commit(tmp_path):
    repo = _repo(tmp_path)
    info = subagent_worktree.create(repo)
    try:
        (info.root / "app.txt").write_text("changed\n", encoding="utf-8")
        (info.root / "new.txt").write_text("new\n", encoding="utf-8")
        review = subagent_worktree.review(info.root)
        assert "[worktree|review]" in review
        assert "app.txt" in review and "new.txt" in review

        dry = subagent_worktree.apply_to_workspace(info.root, repo, dry_run=True)
        assert "预检通过" in dry
        assert (repo / "app.txt").read_text(encoding="utf-8") == "v1\n"
        assert not (repo / "new.txt").exists()

        applied = subagent_worktree.apply_to_workspace(info.root, repo)
        assert "已应用" in applied
        assert (repo / "app.txt").read_text(encoding="utf-8") == "changed\n"
        assert (repo / "new.txt").read_text(encoding="utf-8") == "new\n"
        assert "init" in subprocess.run(
            ["git", "log", "-1", "--pretty=%s"], cwd=repo,
            capture_output=True, text=True, check=True).stdout

        refused = subagent_worktree.apply_to_workspace(info.root, repo)
        assert "已有未提交改动" in refused
    finally:
        _git(repo, "worktree", "remove", "--force", str(info.root))


def test_worktree_apply_rejects_arbitrary_directories(tmp_path):
    assert subagent_worktree.is_isolated_path(tmp_path) is False
    assert "只能审阅本程序创建" in subagent_worktree.review(tmp_path)
