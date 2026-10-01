"""Git 工作副本(worktree)W1 测试:porcelain 解析、落点与命令、只读事实、
以及一条真 git 的端到端(init → commit → worktree add → facts 看见两份副本)。

模块的硬规矩在测试里对应成针:**任何函数都不创建目录** —— 建副本只能由
模型走 bash 确认框;`suggest` 只生成命令字符串。
"""
import shutil
import subprocess
from pathlib import Path

import pytest

from minicode import worktree as wt
from minicode.tools.base import ToolContext

GIT = shutil.which("git")


def test_parse_worktree_list_handles_all_shapes():
    sample = (
        "worktree D:/repo\n"
        "HEAD abcdef1234567890\n"
        "branch refs/heads/main\n\n"
        "worktree D:/repo__login-fix\n"
        "HEAD 1234567890abcdef\n"
        "branch refs/heads/login-fix\n\n"
        "worktree C:/bare-thing\n"
        "bare\n\n"
        "worktree D:/detached-one\n"
        "HEAD deadbeefdeadbeef\n"
        "detached\n"
    )
    rows = wt.parse_worktree_list(sample)
    assert [r["path"] for r in rows] == \
        ["D:/repo", "D:/repo__login-fix", "C:/bare-thing", "D:/detached-one"]
    assert rows[0]["branch"] == "main" and rows[0]["head"].startswith("abcdef")
    assert rows[1]["branch"] == "login-fix"
    assert rows[2].get("bare") is True and "branch" not in rows[2]
    assert rows[3].get("detached") is True
    # 空输入不给假行
    assert wt.parse_worktree_list("") == []


def test_suggest_copy_path_is_sibling_and_fold_slashes(tmp_path):
    repo = tmp_path / "my-repo"
    repo.mkdir()
    assert wt.suggest_copy_path(repo, "login-fix") == \
        tmp_path / "my-repo__login-fix"
    # 分支名里的斜杠折叠,不变成嵌套目录
    assert wt.suggest_copy_path(repo, "feature/login") == \
        tmp_path / "my-repo__feature__login"


def test_suggest_refuses_existing_target_and_needs_branch(tmp_path):
    repo = tmp_path / "my-repo"
    repo.mkdir()
    assert "分支名" in wt.suggest(repo, "")["msg"]
    target = wt.suggest_copy_path(repo, "x")
    target.mkdir()
    assert "落点已存在" in wt.suggest(repo, "x")["msg"]


def test_build_add_command_quotes_and_switches_modes(tmp_path):
    repo = tmp_path / "my repo"          # 故意带空格:引号是承诺不是装饰
    cmd = wt.build_add_command(repo, "login-fix", tmp_path / "my repo__login-fix")
    assert cmd.startswith("git -C ") and " worktree add -b login-fix " in cmd
    assert f'"{tmp_path / "my repo"}"' in cmd and '"worktree' not in cmd
    cmd2 = wt.build_add_command(repo, "main", tmp_path / "p", new_branch=False)
    assert " -b " not in cmd2


def test_facts_says_so_when_not_a_repo(tmp_path):
    r = wt.worktree_facts(tmp_path)
    assert r["ok"] is False and "不是 git 仓库" in r["msg"]
    r2 = wt.worktree_facts(tmp_path / "missing")
    assert r2["ok"] is False and "目录不存在" in r2["msg"]


def test_tool_handler_shapes(tmp_path):
    ctx = ToolContext(workspace=tmp_path, config=None)
    # 不是仓库:照实说
    assert "不是 git 仓库" in wt.worktree_tool(ctx)
    # 未知的 action 列出可用项,不猜
    assert "不认识的 action" in wt.worktree_tool(ctx, action="delete")
    # suggest:落点 + 命令 + 下一步(命令走 bash 确认)全在回话里
    (tmp_path / "repo").mkdir()
    out = wt.worktree_tool(ctx, action="suggest", branch="fix",
                           path=str(tmp_path / "repo"))
    assert "worktree add -b fix" in out and "确认框" in out


@pytest.mark.skipif(GIT is None, reason="本机没有 git(显式跳过,不是静默)")
def test_real_git_roundtrip(tmp_path):
    """真 git:init → 提交 → 走 suggest 的命令建副本 → facts 看见两份、dirty 属实。"""
    repo = tmp_path / "demo"
    repo.mkdir()
    def git(*args, cwd=repo):
        subprocess.run(["git", *args], cwd=str(cwd), check=True,
                       capture_output=True, text=True)
    git("init", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (repo / "a.txt").write_text("v1", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "init")

    facts = wt.worktree_facts(repo)
    assert facts["ok"] and len(facts["worktrees"]) == 1
    assert facts["worktrees"][0]["dirty"] == ""

    sug = wt.suggest(repo, "login-fix")
    assert sug["ok"]
    # 建副本只通过 subprocess 显式执行"建议的命令"的等价物 —— 与 bash 确认后
    # 的行为一致;worktree 模块自身没有执行入口
    subprocess.run(sug["command"], shell=True, check=True,
                   capture_output=True, text=True)
    copy_dir = Path(sug["path"])
    assert (copy_dir / "a.txt").read_text(encoding="utf-8") == "v1"

    facts2 = wt.worktree_facts(repo)
    assert len(facts2["worktrees"]) == 2
    branches = {row.get("branch") for row in facts2["worktrees"]}
    assert branches == {"main", "login-fix"}
    # 副本里改文件:那一份的 dirty 属实标出来,主仓还是干净
    (copy_dir / "a.txt").write_text("v2", encoding="utf-8")
    facts3 = wt.worktree_facts(repo)
    import os
    dirty = {os.path.normcase(os.path.normpath(row["path"])): row["dirty"]
             for row in facts3["worktrees"]}
    # git 输出的路径是正斜杠,比对前归一化(实测过:不归一化 KeyError)
    assert dirty[os.path.normcase(os.path.normpath(str(copy_dir)))] == "1 个文件"
    assert dirty[os.path.normcase(os.path.normpath(str(repo)))] == ""
