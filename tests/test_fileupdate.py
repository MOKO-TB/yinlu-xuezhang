"""增量更新·路 2(文件级清单 + 暂存 + 独立应用器)的针(v0.9.183)。

计划书 §阶段 1 点名的六条,一条不少;外加两条这条路上最容易假绿的:
zip slip 成员、以及"应用脚本长什么样"的形状针。
应用器那两支要真起 powershell 在假安装树上跑 —— 更新类功能只验源码不算验过。
"""
import subprocess
import sys
import zipfile

import pytest

from minicode import fileupdate as fu

pytestmark = pytest.mark.skipif(sys.platform != "win32",
                                reason="应用器是 PowerShell,只在 Windows 上有意义")


def _h(body: bytes) -> str:
    import hashlib
    return hashlib.sha256(body).hexdigest()


def _manifest(pairs, drop=()):
    return {"schema": 1, "version": "9.9.9",
            "files": [{"p": p, "s": len(b), "h": _h(b)} for p, b in pairs],
            "drop": list(drop)}


def test_manifest_rejects_traversal_absolute_duplicates_and_data_dirs():
    """清单能指挥删除与覆盖,所以它的边界就是这次更新的爆炸半径。"""
    man = {"files": [
        {"p": "../evil.txt", "s": 1, "h": _h(b"x")},
        {"p": "C:/Windows/x.dll", "s": 1, "h": _h(b"x")},
        {"p": "/etc/passwd", "s": 1, "h": _h(b"x")},
        {"p": "a/../../b.txt", "s": 1, "h": _h(b"x")},
        {"p": "ok.txt", "s": 1, "h": _h(b"x")},
        {"p": "ok.txt", "s": 1, "h": _h(b"x")},
        {"p": "crawled/library.db", "s": 1, "h": _h(b"x")},
        {"p": "config.json", "s": 1, "h": _h(b"x")},
        {"p": "short.txt", "s": 1, "h": "nothex"},
    ], "drop": ["../gone.txt", "sessions/x.jsonl", "fine/old.pyd"]}
    files, drop, problems = fu.validate_manifest(man)
    assert [f["p"] for f in files] == ["ok.txt"], files
    assert drop == ["fine/old.pyd"], drop
    assert len(problems) == 10, problems   # 8 条文件 + 2 条删除清单


def test_an_install_tree_that_already_matches_downloads_and_replaces_nothing(tmp_path):
    """全量哈希相等时 fetch 必须是空的 —— 这一条红了,路 2 就变成"每次重下整份载荷"。"""
    root = tmp_path / "install"
    (root / "sub").mkdir(parents=True)
    (root / "a.txt").write_bytes(b"hello")
    (root / "sub" / "b.txt").write_bytes(b"world")
    (root / "user_put.txt").write_bytes(b"mine")      # 清单没有、也不在 drop 里
    man = _manifest([("a.txt", b"hello"), ("sub/b.txt", b"world")])
    files, drop, problems = fu.validate_manifest(man)
    assert not problems
    diff = fu.diff_install(files, root)
    assert diff["fetch"] == [], diff["fetch"]
    assert sorted(diff["same"]) == ["a.txt", "sub/b.txt"]
    assert diff["extra_local"] == ["user_put.txt"], "本机多出来的只许报告,不许动"


def test_one_bad_staged_hash_rolls_the_whole_apply_back(tmp_path):
    """一件验不过 = 整次回滚:更新失败可以是"没更新",不能是半新半旧。"""
    install = tmp_path / "install"
    (install / "sub").mkdir(parents=True)
    (install / "a.txt").write_bytes(b"OLD-A")
    (install / "sub" / "b.txt").write_bytes(b"OLD-B")
    updates = tmp_path / "updates"
    staged = fu.staged_root(updates, "9.9.9")
    (staged / "sub").mkdir(parents=True)
    (staged / "a.txt").write_bytes(b"NEW-A")
    (staged / "sub" / "b.txt").write_bytes(b"CORRUPT")     # 与清单哈希对不上
    files = [{"p": "a.txt", "s": 5, "h": _h(b"NEW-A")},
             {"p": "sub/b.txt", "s": 7, "h": _h(b"NEW-B")}]
    good, bad = fu.verify_staged(files, staged)
    assert [f["p"] for f in good] == ["a.txt"] and bad == ["sub/b.txt"]
    # 这里**故意**把验不过的那件也塞进应用清单:模拟"调用方没先验"的坏调用,
    # 应用脚本自己那一道哈希闸必须兜住并整体回滚。
    ps1 = fu.write_apply_script(updates, "9.9.9", install, files, [])
    r = subprocess.run(fu.apply_command(ps1), capture_output=True, text=True, timeout=120)
    state = fu.read_state(updates, "9.9.9")
    assert r.returncode != 0, r.stdout
    assert state.get("phase") == "rolledback", state
    assert (install / "a.txt").read_bytes() == b"OLD-A", "回滚必须把已搬走的原件搬回来"
    assert not (install / "sub" / "b.txt").exists() or \
        (install / "sub" / "b.txt").read_bytes() == b"OLD-B"


def test_a_good_staged_set_is_applied_and_the_drop_goes_to_rollback(tmp_path):
    install = tmp_path / "install"
    (install / "gone").mkdir(parents=True)
    (install / "a.txt").write_bytes(b"OLD-A")
    (install / "gone" / "old.pyd").write_bytes(b"OLD-PYD")
    updates = tmp_path / "updates"
    staged = fu.staged_root(updates, "9.9.9")
    staged.mkdir(parents=True)
    (staged / "a.txt").write_bytes(b"NEW-A")
    files = [{"p": "a.txt", "s": 5, "h": _h(b"NEW-A")}]
    ps1 = fu.write_apply_script(updates, "9.9.9", install, files, ["gone/old.pyd"])
    r = subprocess.run(fu.apply_command(ps1), capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert fu.read_state(updates, "9.9.9").get("phase") == "done"
    assert (install / "a.txt").read_bytes() == b"NEW-A"
    assert not (install / "gone" / "old.pyd").exists(), "drop 里的要撤走"
    rb = fu.staged_root(updates, "9.9.9") / "_apply" / "rollback" / "gone" / "old.pyd"
    assert rb.read_bytes() == b"OLD-PYD", "撤走的原件进回滚目录,不是消失"


def test_the_drop_list_cannot_reach_outside_the_install_tree(tmp_path):
    """删除权只给清单里验过边界的 drop;越界与数据目录在 validate 那一道就没了。"""
    man = _manifest([("a.txt", b"x")], drop=["../outside.txt", "crawled/library.db"])
    _files, drop, problems = fu.validate_manifest(man)
    assert drop == [] and len(problems) == 2, (drop, problems)
    # 应用脚本那一道也只认 plan.json 里写死的相对路径:手工塞一个越界项进去,
    # 脚本拼出来仍落在安装树内(Join-Path 不会把 .. 当出口),原件不动。
    install = tmp_path / "install"
    install.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"keep me")
    updates = tmp_path / "updates"
    ps1 = fu.write_apply_script(updates, "9.9.9", install, [], ["..\\outside.txt"])
    subprocess.run(fu.apply_command(ps1), capture_output=True, text=True, timeout=120)
    assert outside.read_bytes() == b"keep me"


def test_a_left_behind_running_state_is_never_read_as_done(tmp_path):
    """中断的判据是 state:running 留在盘上 = 上一次没走完,不许当成功。"""
    updates = tmp_path / "updates"
    adir = fu.staged_root(updates, "9.9.9") / "_apply"
    adir.mkdir(parents=True)
    assert fu.read_state(updates, "9.9.9") == {}
    (adir / "state.json").write_text('{"phase":"running"}', encoding="utf-8")
    st = fu.read_state(updates, "9.9.9")
    assert st.get("phase") == "running" and st.get("phase") != "done"
    (adir / "state.json").write_text("not json", encoding="utf-8")
    assert fu.read_state(updates, "9.9.9") == {}, "坏 state 当没有,不许猜"


def test_a_version_without_a_patch_falls_back_to_the_whole_package():
    """本机版本不在补丁表里 = 这条路不覆盖,退回整件;**不是失败,也不许冒充成功**。"""
    man = {"patches": {"0.9.181": {"asset": "p.zip", "sha256": _h(b"z"), "size": 3}}}
    assert fu.pick_patch(man, "0.9.181")["asset"] == "p.zip"
    assert fu.pick_patch(man, "v0.9.181")["asset"] == "p.zip", "带不带 v 都得认"
    assert fu.pick_patch(man, "0.9.177") is None
    assert fu.pick_patch({}, "0.9.181") is None
    assert fu.pick_patch({"patches": {"0.9.181": {"asset": "p.zip"}}}, "0.9.181") is None, \
        "缺 sha256 的补丁等于没验过的补丁,不认"


def test_unpack_patch_refuses_zip_slip_members(tmp_path):
    zp = tmp_path / "p.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("../evil.txt", b"x")
    with pytest.raises(ValueError):
        fu.unpack_patch(zp, tmp_path / "out")
    assert not (tmp_path.parent / "evil.txt").exists()
    assert not (tmp_path / "evil.txt").exists()


def test_the_apply_command_is_a_detached_powershell_on_the_generated_script(tmp_path):
    """形状针:换成了别的执行器(或把脚本路径拼进 -Command 字符串)这一支就该红 ——
    那条路上引号与路径注入都是真的会发生的事故。"""
    ps1 = tmp_path / "apply.ps1"
    ps1.write_text("exit 0", encoding="utf-8")
    cmd = fu.apply_command(ps1)
    assert cmd[0] == "powershell" and "-File" in cmd and cmd[-1] == str(ps1)
    assert "-ExecutionPolicy" in cmd and "Bypass" in cmd
