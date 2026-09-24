"""`minicode/state_files.py` 那份「不外发清单」的回归(v0.9.39 R 批)。

清单本身是数据,数据最怕两件事:**漏一项**和**有一项是假的**。这两组测试各管一头:

- `test_remove_from_*`:在一个假的 dist 目录里把每一项都摆出来,叫清单去删,
  删不干净 = 这个函数只是看起来在工作;误删邻居 = 它把包里的真东西删了。
- `test_kind_agrees_with_mydata_table`:和「我的数据」面板那份 `TABLE` 对一遍
  "这是文件还是目录" —— 两份清单回答的是两个问题(见模块 docstring),
  但同一个名字在一个里说"目录"、另一个里说"文件",必有一处写错了。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from minicode import mydata, state_files  # noqa: E402

# 删完还必须留在包里的东西:少了任何一个,同学拿到的就不是一个能跑的程序
MUST_SURVIVE = ("config.example.json", "引路学长.exe", "_internal/base_library.zip",
                "kb/school-calendar.json", "使用说明.txt")


def _make_fake_dist(dist: Path) -> list[str]:
    """按清单摆出一个"构建机跑过一遍之后"的产物目录,返回摆出来的名字。"""
    placed: list[str] = []
    for entry in state_files.SHIP_NEVER:
        path = dist.joinpath(*entry.name.split("/"))
        if entry.kind == state_files.DIR:
            path.mkdir(parents=True, exist_ok=True)
            (path / "内容.jsonl").write_text("本机数据", encoding="utf-8")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("本机数据", encoding="utf-8")
        placed.append(entry.name)
    for name in MUST_SURVIVE:
        keep = dist.joinpath(*name.split("/"))
        keep.parent.mkdir(parents=True, exist_ok=True)
        keep.write_text("要留在包里", encoding="utf-8")
    return placed


def test_manifest_is_wellformed():
    names = [e.name for e in state_files.SHIP_NEVER]
    assert len(names) == len(set(names)), "有重名条目:后一条会静默盖掉前一条"
    for e in state_files.SHIP_NEVER:
        assert e.kind in (state_files.FILE, state_files.DIR), f"{e.name}: kind 写错"
        assert not e.name.startswith("/") and ".." not in e.name, \
            f"{e.name}: 只准写数据目录内的相对路径"
        assert len(e.why) >= 8, f"{e.name}: why 太空,下一个人没法判断该不该删这条"


def test_remove_from_deletes_every_listed_path(tmp_path):
    """清单里每一项都必须"摆得出来也删得掉" —— 漏一项就是把那份数据发给别人。"""
    dist = tmp_path / "引路学长"
    placed = _make_fake_dist(dist)
    removed = state_files.remove_from(dist)
    assert sorted(removed) == sorted(placed), "返回的删除记录和清单不一致"
    for e in state_files.SHIP_NEVER:
        path = dist.joinpath(*e.name.split("/"))
        assert not path.exists(), f"{e.name} 还在:打包会把它随包外发"


def test_remove_from_leaves_the_real_package_alone(tmp_path):
    dist = tmp_path / "引路学长"
    _make_fake_dist(dist)
    state_files.remove_from(dist)
    for name in MUST_SURVIVE:
        assert (dist / name).exists() or dist.joinpath(*name.split("/")).exists(), \
            f"{name} 被误删了 —— 那同学拿到的包是残的"


def test_remove_from_is_safe_on_a_clean_dir(tmp_path):
    """构建机压根没跑过网页版是常态:没得删不该红,也不该顺手删掉别的。"""
    dist = tmp_path / "引路学长"
    dist.mkdir()
    (dist / "config.example.json").write_text("样例", encoding="utf-8")
    assert state_files.remove_from(dist) == []
    assert (dist / "config.example.json").read_text(encoding="utf-8") == "样例"


def test_kind_agrees_with_mydata_table():
    """同一个名字在两份清单里不能一个是文件、一个是目录。"""
    mine = {e.name: e.kind for e in state_files.SHIP_NEVER}
    conflicts = []
    for entry in mydata.TABLE:
        if entry.root != "app":            # 密钥与跨项目记忆在 ~/.yinluxs,不归打包删
            continue
        key = entry.key
        if key not in mine:
            continue
        want_dir = entry.kind in ("dir", "family")
        if want_dir != (mine[key] == state_files.DIR):
            conflicts.append(f"{key}: SHIP_NEVER 说 {mine[key]},mydata 说 {entry.kind}")
    assert not conflicts, "、".join(conflicts)


def test_never_exported_secrets_are_still_out_of_the_package():
    """`mydata` 标成「永不导出」的东西,清单这边要么真删、要么有明确理由不删。

    `config.local.json` 是唯一故意留在包里的(构建机注入的内置 Key,让同学开箱即用),
    它由 check_docs 的 SHIP_INTENTIONALLY 单独交代 —— 这条测试把它钉死在这里,
    免得下次有人以为"密钥文件肯定在清单里"而不去查真正的处理语句。
    """
    never = {e.key for e in mydata.TABLE if e.category == mydata.NEVER and e.root == "app"}
    ship = {e.name for e in state_files.SHIP_NEVER}
    assert "browser-profile" in never and "browser-profile" in ship
    assert "config.local.json" not in ship, "它若在删除清单里,开箱即用就没了"


def test_scan_still_finds_the_known_local_state():
    """防"清单收口后扫描器变成空转":对账闸扫得到的本机状态必须还在清单里。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("check_docs", ROOT / "scripts" / "check_docs.py")
    check_docs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(check_docs)
    found = check_docs.state_targets()
    assert found, "扫描器一个本机状态都没扫到 = 这条检查在空转"
    ship = {e.name for e in state_files.SHIP_NEVER}
    orphans = {n for n in found
               if n not in ship and n not in check_docs.SHIP_INTENTIONALLY}
    assert not orphans, f"代码会写它、清单却不管它:{sorted(orphans)}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
