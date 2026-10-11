"""Shadow Git 检查点(H,v0.9.157)。

这一族的第一支是**安全边界**,而不是"能不能快照":

`restore_only` 用 `git checkout <sha> -- .`,它只写"那次快照里有"的路径 ——
工作区里**之后新建的文件原样留着**。用户点的是"只还原,绝不删",而这条边界正好就是
那个 git 子命令的行为。**所以必须有一支针拿一个"快照之后新建的文件"去撞它** ——
否则哪天有人为了"更彻底"把 `checkout` 换成 `reset --hard`,整族还是绿的。

其余几支各钉一处:间接改过的文件(就是这一件存在的理由)、影子库不进用户仓库历史、
一个工作区一本账、以及 `drop` 真的删得掉(git 的对象文件是只读的,第一版删不掉)。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from minicode import paths, shadow_git

pytestmark = pytest.mark.skipif(not shadow_git.available(),
                                reason="这台机器上没有 git")


@pytest.fixture
def store(tmp_path, monkeypatch):
    """把影子库钉到 tmp 里,别写进仓库(源码态 APP_DIR 就是仓库)。"""
    root = tmp_path / "appdata"
    monkeypatch.setattr(paths, "app_path",
                        lambda *p: root.joinpath(*p))
    return root


@pytest.fixture
def ws(tmp_path):
    d = tmp_path / "proj"
    (d / "sub").mkdir(parents=True)
    (d / "a.txt").write_text("原始 A\n", encoding="utf-8")
    (d / "sub" / "b.txt").write_text("原始 B\n", encoding="utf-8")
    return d


def _snap(ws, label=""):
    sha = shadow_git.snapshot(ws, label=label)
    assert sha, "快照没成功(这一族的地基,先修它)"
    return sha


# ------------------------------------------------------- ① 安全边界:绝不删
def test_restoring_never_deletes_a_newer_file(ws, store):
    """**这一支是整族的地基。**

    快照之后新建的文件,回滚时必须**原样留着** —— 那是用户自己建的东西,不是 agent
    改出来的。`git checkout <sha> -- .` 恰好是这个行为,而"完整恢复到那一刻"不是。
    """
    sha = _snap(ws)
    (ws / "a.txt").write_text("改过\n", encoding="utf-8")
    (ws / "新文件.txt").write_text("快照之后建的\n", encoding="utf-8")
    n, why = shadow_git.restore_only(ws, sha)
    assert why == "" and n >= 2
    assert (ws / "a.txt").read_text(encoding="utf-8") == "原始 A\n"
    assert (ws / "新文件.txt").exists(), "**绝不删** —— 这是用户点的那条"
    assert (ws / "新文件.txt").read_text(encoding="utf-8") == "快照之后建的\n"


def test_restoring_brings_back_a_file_that_was_deleted(ws, store):
    """反向那一半也要有:快照里**有**、而之后被删掉的,回滚要把它写回来。"""
    (ws / "gone.txt").write_text("马上要删\n", encoding="utf-8")
    sha = _snap(ws)
    os.remove(ws / "gone.txt")
    assert not (ws / "gone.txt").exists()
    shadow_git.restore_only(ws, sha)
    assert (ws / "gone.txt").read_text(encoding="utf-8") == "马上要删\n"


def test_a_file_changed_by_a_subprocess_comes_back(ws, store):
    """**这一件存在的全部理由**:`bash` / `py_script` 间接改过的文件,自建 undo 记不住。

    这里用真子进程改一份文件(不是 `write_text`),再回滚 —— 它必须回来。
    """
    sha = _snap(ws)
    subprocess.run(["cmd", "/c", "echo changed > sub\\b.txt"], cwd=str(ws), check=False)
    (ws / "sub" / "b.txt").write_text("被子进程改过\n", encoding="utf-8")
    shadow_git.restore_only(ws, sha)
    assert (ws / "sub" / "b.txt").read_text(encoding="utf-8") == "原始 B\n"


# ------------------------------------------------- ② 绝不碰用户仓库的历史
def test_the_shadow_store_never_touches_the_users_repo(ws, store):
    """影子库在**数据目录**里,用户的 `git log` 一条都不许多。

    `ROADMAP` 原文那句"最低实现:`git add -N` + `git commit --no-verify`"**是错的** ——
    那样提交进的是**用户自己的历史**。
    """
    subprocess.run(["git", "init", "--quiet", str(ws)], check=False)
    _snap(ws, label="不该进用户仓库")
    log = subprocess.run(["git", "log", "--oneline"], cwd=str(ws),
                         capture_output=True)
    assert log.returncode != 0, "用户仓库里不该有任何提交"
    assert shadow_git.shadow_dir(ws).is_relative_to(store)


def test_two_workspaces_do_not_share_a_store(ws, store, tmp_path):
    """一个工作区一本账 —— 共用的话,回滚会把另一个项目的文件也写回去。"""
    other = tmp_path / "proj2"
    other.mkdir()
    (other / "c.txt").write_text("另一个项目\n", encoding="utf-8")
    assert shadow_git.shadow_dir(ws) != shadow_git.shadow_dir(other)
    _snap(ws)
    _snap(other)
    assert len(shadow_git.snapshots(ws)) == 1
    assert len(shadow_git.snapshots(other)) == 1


def test_the_same_path_always_gets_the_same_store(ws, store):
    assert shadow_git.shadow_dir(ws) == shadow_git.shadow_dir(Path(str(ws)))


# ------------------------------------------------------------- ③ 其余口径
def test_snapshots_lists_newest_first_with_a_subject(ws, store):
    _snap(ws, label="第一次")
    (ws / "a.txt").write_text("又改了\n", encoding="utf-8")
    _snap(ws, label="第二次")
    got = shadow_git.snapshots(ws)
    assert len(got) == 2
    assert "第二次" in got[0]["subject"] and "第一次" in got[1]["subject"]
    assert got[0]["sha"] != got[1]["sha"] and got[0]["ts"] >= got[1]["ts"]


def test_a_snapshot_with_nothing_changed_still_works(ws, store):
    """没改东西也要能快照(`--allow-empty`)—— 否则"这一轮没改文件"与"快照失败"
    在报表里长得一样。"""
    assert shadow_git.snapshot(ws)
    assert shadow_git.snapshot(ws)


def test_tracked_paths_lists_what_a_restore_would_write(ws, store):
    sha = _snap(ws)
    got = shadow_git.tracked_paths(ws, sha)
    assert "a.txt" in got and "sub/b.txt" in got


def test_heavy_directories_are_excluded(ws, store):
    """排除表只排"大且必然不是源码"的那几类 —— 漏进去的代价是快照慢,漏掉的代价是
    回滚不回来,所以**别的都进**。"""
    heavy = ws / "node_modules" / "x"
    heavy.mkdir(parents=True)
    (heavy / "huge.js").write_text("console.log(1)\n", encoding="utf-8")
    (ws / "keep.txt").write_text("要进快照\n", encoding="utf-8")
    got = shadow_git.tracked_paths(ws, _snap(ws))
    assert "keep.txt" in got
    assert not any(p.startswith("node_modules/") for p in got)


def test_drop_really_removes_the_store(ws, store):
    """**第一版这一支是红的**:git 的对象文件是**只读**的,Windows 上 `shutil.rmtree`
    直接 `WinError 5` —— `drop()` 回 False 而影子库还好好躺在那儿(现象是"我说删了,
    它还在",比报错更难查)。"""
    d = shadow_git.shadow_dir(ws)
    _snap(ws)
    assert d.exists()
    assert shadow_git.drop(ws) is True
    assert not d.exists()


def test_an_empty_sha_restores_nothing(ws, store):
    n, why = shadow_git.restore_only(ws, "")
    assert n == 0 and why
