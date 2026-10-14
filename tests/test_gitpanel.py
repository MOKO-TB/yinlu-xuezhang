# -*- coding: utf-8 -*-
"""侧边栏「Git」页的后端(v0.9.159):薄皮 git CLI + 四条铁律。

这一族钉的不是"git 命令对不对"(那是 git 自己的事),是**这一层有没有越界**:
路径围栏挡不挡得住穿越、身份设置碰不碰、网络动作出不出去、未跟踪文件退回
会不会误删。每一支都拿一个真 git 仓库(temp 里 git init)撞。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from minicode import gitpanel

pytestmark = pytest.mark.skipif(
    not gitpanel.available(), reason="这台机器没有 git,整族跳过")


def _git(repo: Path, *args: str) -> str:
    git = gitpanel.git_bin()
    r = subprocess.run([git, *args], cwd=str(repo), capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    assert r.returncode == 0, f"git {args} 失败: {r.stderr}"
    return r.stdout


def _repo(tmp_path: Path, *files: str) -> Path:
    repo = tmp_path / "ws"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    for name in files:
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"内容:{name}\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    return repo


# ------------------------------------------------------------- 路径围栏

def test_traversal_and_absolute_and_dotgit_are_refused(tmp_path):
    repo = _repo(tmp_path, "a.txt")
    for bad in ("../outside.txt", "C:/Windows/system32", ".git/config",
                "sub/../../a.txt", "", "a/./b", "./a.txt"):
        assert gitpanel._safe_rel(repo, bad) is None, bad
    assert gitpanel._safe_rel(repo, "a.txt") == "a.txt"
    assert gitpanel._safe_rel(repo, "sub/dir/f.txt") == "sub/dir/f.txt"


def test_stage_refuses_the_whole_batch_on_one_bad_path(tmp_path):
    repo = _repo(tmp_path, "a.txt", "b.txt")
    (repo / "a.txt").write_text("改了\n", encoding="utf-8")
    got = gitpanel.stage(repo, ["a.txt", "../escape.txt"])
    assert not got["ok"]
    st = gitpanel.status(repo)
    assert not any(r["staged"] for r in st["rows"]), "一个坏路径不该让任何东西进暂存区"


# ------------------------------------------------------------- 状态与人类话

def test_status_speaks_human_and_splits_staged(tmp_path):
    repo = _repo(tmp_path, "a.txt", "b.txt")
    (repo / "a.txt").write_text("改了\n", encoding="utf-8")
    (repo / "new.txt").write_text("新文件\n", encoding="utf-8")
    _git(repo, "rm", "-q", "--cached", "b.txt")
    st = gitpanel.status(repo)
    assert st["ok"] and st["branch"]
    rows = st["rows"]
    a = [r for r in rows if r["path"] == "a.txt" and r["x"] == " "]
    assert a and a[0]["staged"] is False and "修改" in a[0]["word"]
    # b.txt 出现两行:暂存区里的删除(D, staged)+ 工作区里的未跟踪 —— 同一个
    # 文件两笔账都要如实报,合并掉任何一笔都是骗人。
    b_staged = [r for r in rows if r["path"] == "b.txt" and r["staged"]]
    b_untracked = [r for r in rows if r["path"] == "b.txt" and r["word"] == "未跟踪"]
    assert b_staged and "删除" in b_staged[0]["word"]
    assert b_untracked
    assert any(r["path"] == "new.txt" and r["word"] == "未跟踪" for r in rows)


# ------------------------------------------------------------- 暂存 / 提交

def test_stage_commit_roundtrip_without_identity_injection(tmp_path):
    repo = _repo(tmp_path, "a.txt")
    (repo / "a.txt").write_text("第二版\n", encoding="utf-8")
    assert gitpanel.stage(repo, ["a.txt"])["ok"]
    got = gitpanel.commit(repo, "改 a")
    assert got["ok"], got["msg"]
    log = gitpanel.log(repo, 2)
    assert log["ok"] and log["rows"][0]["subject"] == "改 a"
    # 身份来自仓库自己的 config(上面 _repo 配的),不是这一层注入的 ——
    # 断法:环境里没有 user.name 时提交要**报错**,而不是替用户编一个。
    bare = tmp_path / "bare"
    bare.mkdir()
    _git(bare, "init", "-q")
    (bare / "x.txt").write_text("x\n", encoding="utf-8")
    gitpanel.stage(bare, ["x.txt"])
    env_clean = subprocess.run(
        [gitpanel.git_bin(), "-c", "user.useConfigOnly=true", "commit", "-m", "t"],
        cwd=str(bare), capture_output=True, text=True,
        env={"PATH": __import__("os").environ.get("PATH", ""), "SYSTEMROOT":
             __import__("os").environ.get("SYSTEMROOT", "")})
    assert env_clean.returncode != 0, "git 层的 useConfigOnly 都拦得住,这一层没资格绕"


def test_commit_refuses_empty_and_only_takes_staged(tmp_path):
    repo = _repo(tmp_path, "a.txt", "b.txt")
    (repo / "a.txt").write_text("改了\n", encoding="utf-8")
    (repo / "b.txt").write_text("也改了\n", encoding="utf-8")
    assert not gitpanel.commit(repo, "  ")["ok"]
    assert gitpanel.stage(repo, ["a.txt"])["ok"]
    assert gitpanel.commit(repo, "只提 a")["ok"]
    st = gitpanel.status(repo)
    paths = {r["path"] for r in st["rows"]}
    assert "b.txt" in paths, "没暂存的 b.txt 必须还在工作区,不许被 -A 顺手带走"


def test_unstage_works_in_a_repo_with_no_commits(tmp_path):
    repo = tmp_path / "fresh"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    (repo / "n.txt").write_text("新\n", encoding="utf-8")
    _git(repo, "add", "-A")
    assert gitpanel.unstage(repo, ["n.txt"])["ok"]
    st = gitpanel.status(repo)
    assert st["rows"][0]["word"] == "未跟踪"


# ------------------------------------------------------------- 差异与退回

def test_diff_shows_change_and_untracked_gets_an_honest_note(tmp_path):
    repo = _repo(tmp_path, "a.txt")
    (repo / "a.txt").write_text("第一行\n第二行\n", encoding="utf-8")
    got = gitpanel.diff(repo, "a.txt")
    assert got["ok"] and "+第二行" in got["text"]
    (repo / "new.txt").write_text("新\n", encoding="utf-8")
    got2 = gitpanel.diff(repo, "new.txt")
    assert got2["ok"] and "未跟踪" in got2["note"] and got2["text"] == ""


def test_restore_refuses_untracked_instead_of_deleting(tmp_path):
    repo = _repo(tmp_path, "a.txt")
    (repo / "new.txt").write_text("新\n", encoding="utf-8")
    got = gitpanel.restore(repo, "new.txt")
    assert not got["ok"] and (repo / "new.txt").exists(), "未跟踪文件不许被这层删掉"
    (repo / "a.txt").write_text("改了\n", encoding="utf-8")
    assert gitpanel.restore(repo, "a.txt")["ok"]
    assert (repo / "a.txt").read_text(encoding="utf-8") == "内容:a.txt\n"


# ------------------------------------------------------------- 网络动作只堵不设

def test_network_verbs_are_not_in_this_layer(tmp_path):
    import inspect
    src = inspect.getsource(gitpanel)
    for verb in ("push", "pull", "fetch", "remote"):
        # 出现在文档字符串里没关系,出现在真正的命令表里不行
        calls = [l for l in src.splitlines()
                 if "_run(workspace" in l or '_run(repo' in l]
        assert not any(f'"{verb}"' in l for l in calls), f"{verb} 不该出现在命令表里"
        assert not any(f"'{verb}'" in l for l in calls), f"{verb} 不该出现在命令表里"


def test_uncommitted_diff_detects_changes_and_clean_workspace(tmp_path):
    repo = _repo(tmp_path, "a.txt")
    clean_got = gitpanel.uncommitted_diff(repo)
    assert clean_got["ok"] is True
    assert clean_got["has_changes"] is False

    (repo / "a.txt").write_text("改动内容\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("新文件\n", encoding="utf-8")
    dirty_got = gitpanel.uncommitted_diff(repo)
    assert dirty_got["ok"] is True
    assert dirty_got["has_changes"] is True
    assert "+改动内容" in dirty_got["diff"]
    assert "untracked.txt" in dirty_got["summary"]
    assert dirty_got["untracked_count"] == 1


def test_suggest_commit_message(tmp_path):
    repo = _repo(tmp_path, "a.py")
    clean = gitpanel.suggest_commit_message(repo)
    assert clean["ok"] is True
    assert clean["has_changes"] is False

    # 改动测试文件
    (repo / "test_a.py").write_text("def test_foo(): pass\n", encoding="utf-8")
    _git(repo, "add", "test_a.py")
    got_test = gitpanel.suggest_commit_message(repo)
    assert got_test["ok"] is True
    assert got_test["has_changes"] is True
    assert got_test["type"] == "test"
    assert got_test["message"].startswith("test(")

    # 改动文档文件
    (repo / "README.md").write_text("# Doc\n", encoding="utf-8")
    got_doc = gitpanel.suggest_commit_message(repo)
    assert got_doc["ok"] is True
    assert got_doc["has_changes"] is True


