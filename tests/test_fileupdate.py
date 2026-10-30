"""增量更新·路 2(文件级清单 + 暂存 + 独立应用器)的针(v0.9.183)。

计划书 §阶段 1 点名的六条,一条不少;外加两条这条路上最容易假绿的:
zip slip 成员、以及"应用脚本长什么样"的形状针。
应用器那两支要真起 powershell 在假安装树上跑 —— 更新类功能只验源码不算验过。
"""
import json
import subprocess
import sys
import zipfile
from pathlib import Path

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


def test_the_updater_waits_for_our_pid_before_touching_any_file(tmp_path):
    """应用脚本必须等学长自己退出完再搬文件 —— 边跑边搬的结果是半新半旧。"""
    ps1 = tmp_path / "apply.ps1"
    ps1.write_text("exit 0", encoding="utf-8")
    cmd = fu.apply_command(ps1, 4321)
    assert cmd[-2:] == ["-WaitPid", "4321"], cmd
    assert "param([int]$WaitPid" in fu._PS1 and "Get-Process -Id $WaitPid" in fu._PS1, \
        "脚本里没有等待那一段,命令带个 pid 也没用"


def test_the_plan_is_absolute_even_when_the_caller_handed_relative_paths(tmp_path, monkeypatch):
    """PowerShell 的 `FullName` 永远是绝对的 —— 相对前缀会让回滚**切错位置**。

    这条是包内探针抓到的真 bug:回滚按 `$_.FullName.Substring($plan.rollback.Length + 1)`
    求相对名,给它一个相对前缀就从错误处切,切出来的"相对名"再拼回安装树,
    结果是回滚文件被搬进一个凭空造出来的子目录、原件根本没回来。
    开发态测试传的都是绝对 tmp_path,所以它一路绿到包内才现形。
    """
    monkeypatch.chdir(tmp_path)
    Path("install").mkdir()
    Path("install/a.txt").write_bytes(b"OLD-A")
    Path("updates/staged/9.9.9").mkdir(parents=True)
    Path("updates/staged/9.9.9/a.txt").write_bytes(b"CORRUPT")
    files = [{"p": "a.txt", "s": 5, "h": _h(b"NEW-A")}]
    ps1 = fu.write_apply_script(Path("updates"), "9.9.9", Path("install"), files, [])
    plan = json.loads((ps1.parent / "plan.json").read_text(encoding="utf-8"))
    for key in ("install", "staged", "rollback", "state"):
        assert Path(plan[key]).is_absolute(), (key, plan[key])
    r = subprocess.run(fu.apply_command(ps1), capture_output=True, text=True, timeout=180)
    assert r.returncode != 0
    assert Path("install/a.txt").read_bytes() == b"OLD-A", "相对路径也不能让回滚搬错地方"
    assert not (tmp_path / "install" / "9.9.9").exists(), "不许在安装树里凭空造目录"


# ---------------------------------------------------------------- v0.9.187:数据件进清单

def test_product_data_paths_are_allowed_while_user_data_stays_banned():
    """路 2 第二半的边界:产品数据按**精确形状**放行,用户数据照旧一条不进。

    放行的三种:馆藏库(`crawled/<站点>/library.db` 与它的凭证)、内核子树、
    内核凭证。别的形状——`crawled/` 根下、`crawled/<站点>/别的文件`、
    `sessions/`、`config.json`——一个都不许。
    """
    ok = ["crawled/mfindgzyykj.libsp.cn/library.db",
          "crawled/mfindgzyykj.libsp.cn/library.db.stamp.json",
          "ms-playwright/chromium-1234/chrome-win/chrome.exe",
          "browser-kernel.stamp.json"]
    bad = ["crawled/library.db",
           "crawled/mfindgzyykj.libsp.cn/notes.txt",
           "crawled/mfindgzyykj.libsp.cn/library.db.bak",
           "sessions/2026.jsonl", "config.json", "token-usage.db"]
    for p in ok:
        assert fu.data_path_ok(p), p
    for p in bad:
        assert not fu.data_path_ok(p), p
        # 同一批路径塞进清单:产品数据被收下,其余必须变成 problems
        files, _drop, problems = fu.validate_manifest(
            {"files": [{"p": p, "s": 1, "h": _h(b"x")}], "drop": [p]})
        if fu.data_path_ok(p):
            assert not problems, (p, problems)
        else:
            assert problems and not files, (p, problems)


def test_data_files_are_hashed_staged_and_diffed_like_any_other(tmp_path):
    """数据件在清单里就是普通文件:变了才 fetch、没变算 same。"""
    root = tmp_path / "install"
    (root / "crawled" / "site").mkdir(parents=True)
    (root / "crawled" / "site" / "library.db").write_bytes(b"OLD-DB")
    (root / "ms-playwright" / "k" / "x.bin").parent.mkdir(parents=True)
    (root / "ms-playwright" / "k" / "x.bin").write_bytes(b"SAME")
    man = _manifest([("crawled/site/library.db", b"NEW-DB"),
                     ("ms-playwright/k/x.bin", b"SAME")])
    files, _drop, problems = fu.validate_manifest(man)
    assert not problems
    diff = fu.diff_install(files, root)
    assert [f["p"] for f in diff["fetch"]] == ["crawled/site/library.db"]
    assert diff["same"] == ["ms-playwright/k/x.bin"]


def test_a_changed_library_is_swapped_in_by_the_apply_script(tmp_path):
    """数据件换位走同一个应用器:原件进回滚目录,新件进安装树,drop 也照走。"""
    install = tmp_path / "install"
    libdir = install / "crawled" / "site"
    libdir.mkdir(parents=True)
    (libdir / "library.db").write_bytes(b"OLD-LIB")
    (install / "browser-kernel.stamp.json").write_bytes(b'{"old":1}')
    updates = tmp_path / "updates"
    staged = fu.staged_root(updates, "9.9.9")
    (staged / "crawled" / "site").mkdir(parents=True)
    (staged / "crawled" / "site" / "library.db").write_bytes(b"NEW-LIB")
    (staged / "browser-kernel.stamp.json").write_bytes(b'{"new":1}')
    files = [{"p": "crawled/site/library.db", "s": 7, "h": _h(b"NEW-LIB")},
             {"p": "browser-kernel.stamp.json", "s": 7, "h": _h(b'{"new":1}')}]
    ps1 = fu.write_apply_script(updates, "9.9.9", install, files, [])
    r = subprocess.run(fu.apply_command(ps1), capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert (libdir / "library.db").read_bytes() == b"NEW-LIB"
    assert (install / "browser-kernel.stamp.json").read_bytes() == b'{"new":1}'
    rb = fu.staged_root(updates, "9.9.9") / "_apply" / "rollback"
    assert (rb / "crawled" / "site" / "library.db").read_bytes() == b"OLD-LIB"


def test_the_patch_is_skipped_when_it_is_not_smaller_than_the_whole_package():
    """两块数据同时变时补丁可能比整包还大 —— 那时按整包下才对。"""
    assert fu.patch_worth_it(100, 200) is True
    assert fu.patch_worth_it(200, 200) is False
    assert fu.patch_worth_it(300, 200) is False
    assert fu.patch_worth_it(0, 200) is True, "不知道补丁多大就不拦"
    assert fu.patch_worth_it(100, 0) is True, "不知道整包多大就不拦"


def test_full_size_picks_the_real_setup_not_the_appupdate():
    rel = {"assets": [
        {"name": "yinlu-xuezhang-9.9.9-appupdate.exe", "size": 135},
        {"name": "yinlu-xuezhang-9.9.9-setup.exe", "size": 878},
    ]}
    assert fu._full_size(rel) == 878


def test_the_build_manifest_carries_data_files_but_not_user_data(tmp_path):
    """构建端与客户端用**同一个** data_path_ok 说了算:数据件进清单,用户数据不进。"""
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import build_exe

    dist = tmp_path / "dist" / "引路学长"
    (dist / "crawled" / "site").mkdir(parents=True)
    (dist / "crawled" / "site" / "library.db").write_bytes(b"LIB")
    (dist / "crawled" / "site" / "library.db.stamp.json").write_bytes(b"{}")
    (dist / "ms-playwright" / "k").mkdir(parents=True)
    (dist / "ms-playwright" / "k" / "chrome.exe").write_bytes(b"EXE")
    (dist / "browser-kernel.stamp.json").write_bytes(b"{}")
    (dist / "sessions").mkdir()
    (dist / "sessions" / "s.jsonl").write_bytes(b"USER")
    (dist / "config.json").write_bytes(b"USER")
    (dist / "app.txt").write_bytes(b"APP")

    _path, files = build_exe.files_manifest(dist)
    names = {f["p"] for f in files}
    assert names == {"crawled/site/library.db",
                     "crawled/site/library.db.stamp.json",
                     "ms-playwright/k/chrome.exe",
                     "browser-kernel.stamp.json",
                     "app.txt"}, names


def test_prune_also_reclaims_old_staged_dirs(tmp_path):
    """数据件更新会在 staged/<版本>/ 里留一份旧库副本 —— 那是回滚本钱,不是永久留档。

    漏了这一条的表现:每升一次级盘上多 0.8 GB,而「清理旧安装包」报出来的字节数
    只有安装包那几件(用户以为清干净了)。
    """
    from minicode import proactive
    cur = proactive._cur_version()
    keep = tmp_path / "staged" / "9.9.100"
    old = tmp_path / "staged" / "0.9.100"
    curdir = tmp_path / "staged" / cur
    for d in (keep, old, curdir):
        (d / "_apply").mkdir(parents=True)
        (d / "crawled" / "site").mkdir(parents=True)
        (d / "crawled" / "site" / "library.db").write_bytes(b"x" * 100)
    stranger = tmp_path / "staged" / "not-a-version"
    stranger.mkdir(parents=True)
    (stranger / "keep.txt").write_bytes(b"y")
    r = proactive.prune_updates(tmp_path, keep_version="9.9.100")
    assert r["ok"]
    assert not old.exists(), "被取代那一版的暂存目录没清掉(旧库副本会一直攒)"
    assert keep.exists() and curdir.exists(), "keep_version 与正在跑的这一版要留下"
    assert stranger.exists(), "不是版本形状的子目录一概不碰"
    assert r["freed"] == 100, r


def test_prune_recognizes_patch_zips_written_by_this_channel(tmp_path):
    """补丁 zip 也是这条通道写下来的 —— v0.9.183 起就有,而当年那道文件名闸漏了它。

    现在补丁里会带数据件(几百 MB 一件),漏认一个字就是每升一次级在盘上攒一份。
    """
    from minicode import proactive
    cur = proactive._cur_version()
    old_patch = tmp_path / "yinlu-xuezhang-0.9.100-patch-from-0.9.99.zip"
    cur_patch = tmp_path / f"yinlu-xuezhang-{cur}-patch-from-0.9.100.zip"
    half = tmp_path / "yinlu-xuezhang-0.9.100-patch-from-0.9.99.zip.part"
    for p in (old_patch, cur_patch, half):
        p.write_bytes(b"z" * 10)
    r = proactive.prune_updates(tmp_path, keep_version="")
    removed = {x["name"] for x in r["removed"]}
    assert "yinlu-xuezhang-0.9.100-patch-from-0.9.99.zip" in removed
    assert "yinlu-xuezhang-0.9.100-patch-from-0.9.99.zip.part" in removed
    assert cur_patch.exists(), "正在跑的这一版的补丁要留着"
    assert r["freed"] == 20, r
