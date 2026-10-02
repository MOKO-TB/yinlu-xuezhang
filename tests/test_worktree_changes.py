"""P2:相对 HEAD 的只读改动清单(worktree action=changes)+ 工具参数对账。

这一档存在的理由是「本轮改动」那份账只认 write_file / edit_file 的快照,命令与编辑器
改的东西不在里面。测试要钉的不是"能列出来",而是三句容易说过头的话:
①没算过行数的不写 0;②看得见 ≠ 退得掉,这句必须在回话里;③新加的 action 忘了进
工具参数 enum = 模型根本叫不到它,所以两处必须对得上。
"""
import shutil
import subprocess
from pathlib import Path

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
    return repo, git


# ---------------------------------------------------------------- 两个解析器

def test_parse_status_porcelain_states_and_rename():
    rows = {r["path"]: r for r in wt.parse_status_porcelain(
        "?? 新建/未跟踪.txt\n"
        " M a.py\n"
        "M  b.py\n"
        "MM c.py\n"
        " D d.py\n"
        "R  old.md -> new.md\n"
        "UU e.py\n")}
    assert rows["新建/未跟踪.txt"]["state"] == "未跟踪"
    assert rows["new.md"]["state"] == "改名", "改名行要取**现在的**名字"
    assert rows["a.py"]["state"] == "改" and rows["a.py"]["staged"] is False
    assert rows["b.py"]["staged"] is True, "X 位是索引侧(已暂存)"
    assert rows["c.py"]["staged"] is True, "MM 只算一条,但要标出已暂存"
    assert rows["d.py"]["state"] == "删除"
    assert rows["e.py"]["state"] == "冲突未解决"
    assert wt.parse_status_porcelain("") == []


def test_parse_numstat_keeps_binary_as_none():
    got = wt.parse_numstat("3\t1\ta.py\n-\t-\timg.png\n")
    assert got["a.py"] == {"added": 3, "removed": 1, "binary": False}
    # 二进制 git 给的是 `-`:留 None + 打标,**不是** 0 行
    assert got["img.png"]["added"] is None and got["img.png"]["binary"] is True
    assert wt.parse_numstat("坏行\n") == {}


# ---------------------------------------------------------------- 真 git 那一半

@pytest.mark.skipif(GIT is None, reason="本机没有 git(显式跳过,不是静默)")
def test_changes_lists_what_the_undo_stack_cannot_see(tmp_path):
    """命令/编辑器改的文件在 git 这一档看得见 —— 那正是它存在的理由。"""
    repo, git = _git_repo(tmp_path)
    (repo / "tracked.py").write_text("a\nb\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "base")
    # 三种形状各来一个:改过的、**不是走 write_file 写出来的**(命令/编辑器),和已暂存的
    (repo / "tracked.py").write_text("a\nB changed\n\nd\n", encoding="utf-8")
    (repo / "from_script.txt").write_text("made by a command\n", encoding="utf-8")
    paths = {f["path"]: f for f in wt.changes(repo)["files"]}
    assert paths["tracked.py"]["state"] == "改"
    # a/b → a/B changed/空行/d:git 算的是 +3 −1,这里照抄它的数,不自己数一遍
    assert (paths["tracked.py"]["added"], paths["tracked.py"]["removed"]) == (3, 1)
    assert paths["from_script.txt"]["state"] == "未跟踪"
    assert paths["from_script.txt"]["added"] is None, "git 没存过它,不编行数"
    r = wt.changes(repo)
    assert r["total"] == 2 and r["uncounted"] == 1 and r["more"] == 0


@pytest.mark.skipif(GIT is None, reason="本机没有 git(显式跳过,不是静默)")
def test_clean_repo_says_clean_not_an_empty_table(tmp_path):
    repo, git = _git_repo(tmp_path)
    (repo / "a.txt").write_text("x", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "one")
    r = wt.changes(repo)
    assert r["ok"] and r["files"] == [] and r["total"] == 0
    # "干净"这句话在回话里,不在字典里再存一份(两处说同一件事总有一天不一致)
    out = wt.worktree_tool(ToolContext(workspace=repo, config=None), action="changes")
    assert "干净的" in out and "没有一个文件与上次提交不一样" in out
    assert ".gitignore" in out, "顺手说清哪些不算改动 —— 这句容易被当成「什么都没改」"


@pytest.mark.skipif(GIT is None, reason="本机没有 git(显式跳过,不是静默)")
def test_changes_before_the_first_commit_says_so(tmp_path):
    """零提交的仓库:`git diff HEAD` 必然失败,要说"行数没算出来",不是"没有改动"。"""
    repo, git = _git_repo(tmp_path)
    (repo / "only.txt").write_text("x\n", encoding="utf-8")
    r = wt.changes(repo)
    assert r["ok"] and r["no_line_counts"] is True and r["total"] == 1
    out = wt.worktree_tool(ToolContext(workspace=repo, config=None), action="changes")
    assert "一次提交都还没有" in out and "改了 1 个文件" in out


def test_changes_refuses_a_non_repo(tmp_path):
    """不是仓库/目录不存在都说得出为什么,不回一份空清单装成"干净"。"""
    got = wt.changes(tmp_path)
    assert got["ok"] is False and "不是 git 仓库" in got["msg"]
    missing = wt.changes(tmp_path / "nope")
    assert missing["ok"] is False and "目录不存在" in missing["msg"]
    out = wt.worktree_tool(ToolContext(workspace=tmp_path, config=None), action="changes")
    assert out.startswith("错误:")


# ---------------------------------------------------------------- 回话里那两句

@pytest.mark.skipif(GIT is None, reason="本机没有 git(显式跳过,不是静默)")
def test_the_receipt_says_seeing_is_not_reverting(tmp_path):
    """边界句必须在回话里:多出来的那些**看得见但退不掉**。

    少了这句,这一档就会被当成"第二份回滚清单"用 —— 而它一份都不退。
    """
    repo, git = _git_repo(tmp_path)
    (repo / "a.py").write_text("x\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "base")
    (repo / "a.py").write_text("y\n", encoding="utf-8")
    out = wt.worktree_tool(ToolContext(workspace=repo, config=None), action="changes")
    assert "看得见不等于退得掉" in out
    assert "git restore" in out and "我不替你做" in out


@pytest.mark.skipif(GIT is None, reason="本机没有 git(显式跳过,不是静默)")
def test_the_list_is_capped_and_says_how_many_are_left_out(tmp_path):
    """一屏列 30 个,剩下的说"另有 N 个" —— 报总数与列出的条数不能混成一件事。"""
    repo, git = _git_repo(tmp_path)
    (repo / "seed.txt").write_text("x", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "base")
    for i in range(wt.CHANGE_ROW_LIMIT + 4):
        (repo / f"n{i}.txt").write_text("new\n", encoding="utf-8")
    r = wt.changes(repo)
    assert r["total"] == wt.CHANGE_ROW_LIMIT + 4
    assert len(r["files"]) == wt.CHANGE_ROW_LIMIT and r["more"] == 4
    out = wt.worktree_tool(ToolContext(workspace=repo, config=None), action="changes")
    assert f"另有 {r['more']} 个没列" in out


def test_action_list_is_one_source_and_the_tool_can_reach_every_one():
    """加一类 action 忘了进工具参数 enum = 模型叫不到它,所以两处必须相等。

    这条闸的形状与"新类别必须进 NOTIFY_KINDS"那几根同一家:枚举只有一份,
    另一处由它派生或对它核。
    """
    from minicode.tools.catalog_files import worktree_tools
    tool = next(t for t in worktree_tools() if t.name == "worktree")
    enum = set(tool.parameters["properties"]["action"]["enum"])
    assert enum == set(wt.ACTIONS), f"工具 enum 与 ACTIONS 漂了:{enum ^ set(wt.ACTIONS)}"
    # 描述里点名的 action 也要真存在(别让模型照描述去调一个没有的)
    for a in wt.ACTIONS:
        assert a in tool.description or a == "list"
    # 未知 action 的提示是把这张表念出来,不是另抄一份
    msg = wt.worktree_tool(ToolContext(workspace=Path("."), config=None), action="delete")
    assert "不认识的 action" in msg
    for a in wt.ACTIONS:
        assert a in msg, f"提示里没念到 {a} —— 那张表又漂了"
