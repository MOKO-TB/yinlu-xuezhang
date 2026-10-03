"""监控栏「环境信息」那一屏的数据 —— 每个数字都要能追到一次真 git 调用。

这一节的规矩(用户点名对标 Qoder 的那块):宁可少一行,不摆一个装饰数字。
所以测试的重点不是"有没有渲染",而是**给不出的时候它是不是真的不说**:
不是仓库 → 不报分支也不报 +N −M;未跟踪文件 git 给不出行数 → 合计必须标"只算了列出的那些",
绝不把 None 当 0 加成一个看着完整的数。
"""
import shutil
import subprocess

import pytest

from minicode import envinfo

GIT = shutil.which("git")
needs_git = pytest.mark.skipif(not GIT, reason="这台机器上没有 git 可跑")


def _repo(tmp_path):
    repo = tmp_path / "demo"
    repo.mkdir()

    def git(*args):
        r = subprocess.run(["git", *args], cwd=str(repo), capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        assert r.returncode == 0, r.stderr
    git("init", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    return repo, git


@needs_git
def test_branch_and_line_counts_come_from_real_git(tmp_path):
    repo, git = _repo(tmp_path)
    (repo / "a.py").write_text("1\n2\n3\n", encoding="utf-8")
    git("add", "a.py")
    git("commit", "-m", "init")
    (repo / "a.py").write_text("1\n2\n3\n4\n5\n", encoding="utf-8")   # +2

    got = envinfo.collect(repo)
    assert got["is_repo"] is True
    assert got["branch"] == "main", got
    assert (got["added"], got["removed"]) == (2, 0), got
    assert got["partial"] is False, "只有已跟踪文件的改动:行数算得全,不该标部分"
    assert got["total"] == 1 and got["workspace"].endswith("demo")


@needs_git
def test_untracked_files_make_the_total_partial_instead_of_filling_zero(tmp_path):
    """未跟踪文件 git 给不出行数 —— 拿 0 补就是一个假完整的 +N −M。"""
    repo, git = _repo(tmp_path)
    (repo / "a.py").write_text("1\n", encoding="utf-8")
    git("add", "a.py")
    git("commit", "-m", "init")
    (repo / "a.py").write_text("1\n2\n", encoding="utf-8")
    (repo / "新笔记.md").write_text("x\ny\nz\n", encoding="utf-8")     # 未跟踪

    got = envinfo.collect(repo)
    assert got["total"] == 2
    assert got["partial"] is True, "有一条给不出行数却没标出来 = 报了一个假总数"
    assert got["added"] == 1 and got["removed"] == 0, "已跟踪那条照实算,未跟踪的不补 0"


@needs_git
def test_beyond_one_screen_is_called_out_in_the_summary(tmp_path):
    repo, git = _repo(tmp_path)
    (repo / "seed").write_text("s\n", encoding="utf-8")
    git("add", "seed")
    git("commit", "-m", "init")
    for i in range(35):
        (repo / f"f{i}.py").write_text("a\nb\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "many")
    for i in range(35):
        (repo / f"f{i}.py").write_text("a\nb\nc\n", encoding="utf-8")

    got = envinfo.collect(repo)
    assert got["total"] == 35 and got["more"] == 5, got
    assert got["partial"] is True, "只列了前 30 个,合计就必须说明不完整"
    assert len(got["files"]) == 30, "递给界面的清单本身也得被截住"


@needs_git
def test_a_non_repo_says_so_instead_of_inventing_a_branch(tmp_path):
    plain = tmp_path / "flat"
    plain.mkdir()
    got = envinfo.collect(plain)
    assert got["is_repo"] is False
    assert got["branch"] == "" and got["added"] == 0 and got["total"] == 0
    assert got["msg"], "不是仓库要给一句原因,不能默默给一张空表"


def test_missing_workspace_and_bad_path_do_not_raise(tmp_path):
    got = envinfo.collect(None)
    assert got["jobs"] == [] and got["is_repo"] is False
    got2 = envinfo.collect(tmp_path / "does-not-exist")
    assert got2["msg"] and got2["is_repo"] is False


def test_background_jobs_come_from_the_same_source_as_the_board(monkeypatch, tmp_path):
    """后台进程不许有第二份真相:看板和这一节都读 `shell.bg_facts()`。"""
    from minicode.tools import shell

    monkeypatch.setattr(shell, "bg_facts", lambda: [
        {"id": "j1", "command": "pytest -q", "running": True, "exit_code": None,
         "chars": 120, "elapsed": 7}])
    repo, git = _repo(tmp_path)
    (repo / "a").write_text("1\n", encoding="utf-8")
    git("add", "a")
    git("commit", "-m", "i")
    got = envinfo.collect(repo)
    assert got["jobs"] == [{"id": "j1", "command": "pytest -q", "running": True,
                            "exit_code": None, "chars": 120, "elapsed": 7}]


def test_command_text_is_clipped_for_the_panel(tmp_path, monkeypatch):
    from minicode.tools import shell
    monkeypatch.setattr(shell, "bg_facts", lambda: [
        {"id": "j2", "command": "x" * 400, "running": False, "exit_code": 1,
         "chars": 0, "elapsed": 3}])
    got = envinfo.collect(tmp_path)
    assert len(got["jobs"][0]["command"]) <= 120, "一屏里不许塞进整条 400 字的命令"
