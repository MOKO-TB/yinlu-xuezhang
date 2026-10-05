"""N1(v0.9.119):分支审查(worktree action=branch)—— 解析器、真 git 端到端、错误口径。

这一档回答的是"这条分支相对分叉点改了什么":提交清单、文件账、行数合计,
再逐文件看 diff。测试钉四件事:
①基准必须是 merge-base(分叉之后主线自己新走的提交不算它的账);
②git 给不出的(二进制)不编行数;
③分支名安全闸(以 - 开头/带空白的一律不收 —— 不给选项注入留门);
④自审退化:人当前就在这条分支上时 merge-base 就是分支顶点,自动改用
  main/master 当参考线并带说明;两条默认线都不存在则如实说,不猜。
"""
import shutil
import subprocess

import pytest

from minicode import worktree as wt
from minicode.tools.base import ToolContext

GIT = shutil.which("git")


def _git_repo(tmp_path):
    repo = tmp_path / "demo"
    repo.mkdir()

    def git(*args, cwd=repo):
        r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        assert r.returncode == 0, r.stderr
    git("init", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (repo / "base.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "base")
    return repo, git


def _feature_branch(repo, git):
    """分叉后开分支、两个提交、三种文件形状(改/新增/二进制);主线再自己走一笔。"""
    git("checkout", "-b", "feat/x")
    (repo / "base.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    (repo / "new.py").write_text("print(1)\n", encoding="utf-8")
    (repo / "img.bin").write_bytes(b"\x00\x01\x02")
    git("add", ".")
    git("commit", "-m", "feat: add f and assets")
    git("checkout", "main")
    (repo / "main_only.txt").write_text("main\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "main moves on")


# ---------------------------------------------------------------- 两个解析器

def test_safe_rev_rejects_option_injection_and_blank():
    """git 走参数列表不经 shell,这里挡的是选项注入:`-c x=y` 会被当成 git 的选项。"""
    assert wt._safe_rev("  feat/x ") == "feat/x"
    assert wt._safe_rev("-c x=y") is None
    assert wt._safe_rev("a b") is None
    assert wt._safe_rev("") is None
    assert wt._safe_rev(None) is None


def test_parse_name_status_takes_the_new_name_on_rename():
    got = wt.parse_name_status(
        "A\tnew.py\nM\ta.py\nD\told.py\nR100\told.md\tnew.md\n")
    assert got == {"new.py": "A", "a.py": "M", "old.py": "D", "new.md": "R"}, \
        "改名行要取**现在的**名字"
    assert wt.parse_name_status("") == {}


# ---------------------------------------------------------------- 真 git 那一半

@pytest.mark.skipif(GIT is None, reason="本机没有 git(显式跳过,不是静默)")
def test_branch_overview_uses_merge_base_not_head(tmp_path):
    """基准是分叉点:分叉之后主线自己新走的那笔(main_only)不算这条分支的账。"""
    repo, git = _git_repo(tmp_path)
    _feature_branch(repo, git)
    r = wt.branch_overview(repo, "feat/x")
    assert r["ok"] and r["branch"] == "feat/x"
    assert r["commits_total"] == 1 and r["commits_more"] == 0
    assert [c["subject"] for c in r["commits"]] == ["feat: add f and assets"]
    paths = {f["path"]: f for f in r["files"]}
    assert paths["base.py"]["state"] == "M"
    assert (paths["base.py"]["added"], paths["base.py"]["removed"]) == (1, 1)
    assert paths["new.py"]["state"] == "A"
    assert paths["img.bin"]["binary"] is True, "二进制 git 不数行"
    assert paths["img.bin"]["added"] is None, "没算过的不写 0"
    assert "main_only.txt" not in paths, "主线后来的提交不是这条分支的账"
    assert r["uncounted"] == 1 and r["more"] == 0


@pytest.mark.skipif(GIT is None, reason="本机没有 git(显式跳过,不是静默)")
def test_reviewing_the_branch_you_are_on_falls_back_to_main(tmp_path):
    """自审退化:HEAD 就在 feat/x 上,merge-base 就是分支顶点 ——
    自动改用 main 当参考线并带说明;main 的那笔依然不算分支的账。"""
    repo, git = _git_repo(tmp_path)
    _feature_branch(repo, git)
    git("checkout", "feat/x")            # 人当前就在这条分支上
    r = wt.branch_overview(repo, "feat/x")
    assert r["ok"] and r["commits_total"] == 1 and r["total"] == 3
    assert "参考线自动改用 main" in r["note"]
    assert "main_only.txt" not in [f["path"] for f in r["files"]]
    out = wt.worktree_tool(ToolContext(workspace=repo, config=None),
                           action="branch", branch="feat/x")
    assert "(当前就在 feat/x 上" in out and "参考线自动改用 main" in out


@pytest.mark.skipif(GIT is None, reason="本机没有 git(显式跳过,不是静默)")
def test_branch_file_diff_returns_content_and_truncates(tmp_path, monkeypatch):
    repo, git = _git_repo(tmp_path)
    _feature_branch(repo, git)
    r = wt.branch_file_diff(repo, "feat/x", "base.py")
    assert r["ok"] and "+    return 2" in r["diff"] and "-    return 1" in r["diff"]
    assert r["truncated"] is False and r["lines_total"] > 0
    rb = wt.branch_file_diff(repo, "feat/x", "img.bin")
    assert rb["ok"] and "Binary" in rb["diff"], "二进制照 git 原话转述,不假装能看"
    monkeypatch.setattr(wt, "BRANCH_DIFF_LIMIT", 2)
    r2 = wt.branch_file_diff(repo, "feat/x", "base.py")
    assert r2["truncated"] is True and len(r2["diff"].splitlines()) == 2


@pytest.mark.skipif(GIT is None, reason="本机没有 git(显式跳过,不是静默)")
def test_branch_errors_say_why(tmp_path):
    repo, git = _git_repo(tmp_path)
    bad = wt.branch_overview(repo, "-c x=y")
    assert bad["ok"] is False and "不合法" in bad["msg"]
    gone = wt.branch_overview(repo, "nope")
    assert gone["ok"] is False and "不存在" in gone["msg"]
    assert "不是 git 仓库" in wt.branch_overview(tmp_path, "main")["msg"]
    # 没给文件名:单文件那半明说要 file,不回一份空 diff
    assert "file" in wt.branch_file_diff(repo, "main", "")["msg"]


@pytest.mark.skipif(GIT is None, reason="本机没有 git(显式跳过,不是静默)")
def test_tool_branch_action_end_to_end(tmp_path):
    """handler 全链:概览 → 单文件 diff;不是仓库时照实说错误。"""
    repo, git = _git_repo(tmp_path)
    _feature_branch(repo, git)
    ctx = ToolContext(workspace=repo, config=None)
    out = wt.worktree_tool(ctx, action="branch", branch="feat/x")
    assert "feat/x" in out and "1 个提交" in out and "3 个文件" in out
    assert "feat: add f and assets" in out and "img.bin" in out and "二进制" in out
    single = wt.worktree_tool(ctx, action="branch", branch="feat/x", file="base.py")
    assert "+    return 2" in single and "return 1" in single
    nope = wt.worktree_tool(ToolContext(workspace=tmp_path, config=None),
                            action="branch", branch="main")
    assert nope.startswith("错误:")
