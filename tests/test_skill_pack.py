"""技能分享包(v0.9.114,用户点名的新功能③):打包 / 预览 / 落盘三步各钉什么。

同学之间互发技能是真实需求("你那个搜书的发我"),而迁移包按设计只带
`skills/*.md` 那种平铺文件 —— 带 `assets/` 的技能整包带不走。所以这一层只做一件窄事:
**装一个技能的那一个目录**。三条硬规矩全在这里:
① 先看后写:预览一个字都不写,`apply` 再校验一次(nonce 会过期);
② 覆盖之前先整目录备份,退得回去;
③ 越界路径整份拒收(zip 路径穿越),清单 sha256 对不上就停手。
"""
import json
import zipfile
from pathlib import Path

import pytest

from minicode import paths, skill_pack


@pytest.fixture(autouse=True)
def _app_dir(tmp_path, monkeypatch):
    """技能与分享包都落在 APP_DIR:钉到临时目录,别碰真实数据目录里的技能。"""
    monkeypatch.setattr(paths, "APP_DIR", tmp_path / "app")


def _make_skill(root: Path, name="搜书指南", body="# 怎么用\n先搜再借。",
                extra=None):
    d = root / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: 在馆藏里找一本书\ntools: library\n---\n{body}",
        encoding="utf-8")
    for rel, text in (extra or {"assets/map.md": "馆位图"}).items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return d


# ---------------------------------------------------------------- 打包

def test_pack_carries_the_skill_and_its_asset_folder(tmp_path):
    ws = tmp_path / "ws"
    _make_skill(ws)
    got = skill_pack.pack(ws, "搜书指南", dest=tmp_path / "out.zip")
    assert got["ok"] is True, got["msg"]
    with zipfile.ZipFile(got["path"]) as zf:
        names = [Path(n).name for n in zf.namelist()]
    assert "SKILL.md" in names and "map.md" in names
    assert skill_pack.MANIFEST in names
    assert got["files"] == 2


def test_a_pack_never_carries_secrets_or_dotfiles(tmp_path):
    ws = tmp_path / "ws"
    _make_skill(ws, extra={".env": "TOKEN=abc", "sub/.hidden.md": "x"})
    got = skill_pack.pack(ws, "搜书指南", dest=tmp_path / "out2.zip")
    with zipfile.ZipFile(got["path"]) as zf:
        blob = zf.read([n for n in zf.namelist() if n.endswith("SKILL.md")][0])
        names = zf.namelist()
    assert not any(".env" in n for n in names), names
    assert not any("/.hidden" in n or n.startswith(".") for n in names), names
    assert b"TOKEN" not in blob


def test_packing_a_skill_that_is_not_there_says_which_names_exist(tmp_path):
    ws = tmp_path / "ws"
    _make_skill(ws)
    got = skill_pack.pack(ws, "没这个", dest=tmp_path / "x.zip")
    assert got["ok"] is False and "没这个" in got["msg"]


# ---------------------------------------------------------------- 预览:一个字都不写

def test_preview_lists_files_without_writing_anything(tmp_path):
    ws = tmp_path / "ws"
    _make_skill(ws)
    zip_path = skill_pack.pack(ws, "搜书指南", dest=tmp_path / "p.zip")["path"]
    target = Path(paths.app_path("skills")) / "搜书指南"
    got = skill_pack.preview(zip_path)
    assert got["ok"] is True, got["msg"]
    assert got["count"] == 2 and got["conflict"] is False
    assert got["nonce"] and "skill-share.json" not in got["files"]
    assert not target.exists(), "预览就落盘 = 用户还没点头文件已经进来了"


def test_preview_warns_about_an_existing_skill_of_the_same_name(tmp_path):
    ws = tmp_path / "ws"
    _make_skill(ws)
    zip_path = skill_pack.pack(ws, "搜书指南", dest=tmp_path / "p.zip")["path"]
    mine = Path(paths.app_path("skills")) / "搜书指南"
    mine.mkdir(parents=True, exist_ok=True)
    (mine / "SKILL.md").write_text("# 我自己改过的版本", encoding="utf-8")
    got = skill_pack.preview(zip_path)
    assert got["conflict"] is True and "备份" in got["msg"]


def test_apply_backs_up_the_existing_one_before_overwriting(tmp_path):
    ws = tmp_path / "ws"
    _make_skill(ws)
    zip_path = skill_pack.pack(ws, "搜书指南", dest=tmp_path / "p.zip")["path"]
    mine = Path(paths.app_path("skills")) / "搜书指南"
    mine.mkdir(parents=True, exist_ok=True)
    (mine / "SKILL.md").write_text("# 我自己改过的版本", encoding="utf-8")
    nonce = skill_pack.preview(zip_path)["nonce"]
    got = skill_pack.apply(nonce)
    assert got["ok"] is True, got["msg"]
    assert (mine / "SKILL.md").read_text(encoding="utf-8").startswith("---")
    backup = Path(got["backup"])
    assert backup.is_dir()
    assert "我自己改过的版本" in (backup / "SKILL.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------- 拒收的几种包

def _write_zip(path: Path, entries: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for name, text in entries.items():
            zf.writestr(name, text)


def test_a_pack_with_a_traversal_path_is_refused_wholesale(tmp_path):
    bad = tmp_path / "bad.zip"
    man = {"kind": "yinluxs-skill-pack", "name": "坏包", "files": []}
    _write_zip(bad, {"坏包/skill-share.json": json.dumps(man),
                     "坏包/../../evil.md": "写到你工作区外面"})
    got = skill_pack.preview(bad)
    assert got["ok"] is False and "越界" in got["msg"]


def test_a_pack_without_a_manifest_is_not_a_skill_pack(tmp_path):
    p = tmp_path / "no.zip"
    _write_zip(p, {"某技能/SKILL.md": "# 没有清单"})
    got = skill_pack.preview(p)
    assert got["ok"] is False and skill_pack.MANIFEST in got["msg"]


def test_a_manifest_with_a_tampered_file_stops_mid_write(tmp_path):
    ws = tmp_path / "ws"
    _make_skill(ws)
    zip_path = Path(skill_pack.pack(ws, "搜书指南", dest=tmp_path / "p.zip")["path"])
    # 改包里那一份 SKILL.md 的内容,清单上的 sha256 就对不上了
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        data = {n: zf.read(n) for n in names}
    skill_key = [n for n in names if n.endswith("SKILL.md")][0]
    data[skill_key] = "# 被换过的内容\n".encode()
    with zipfile.ZipFile(zip_path, "w") as zf:
        for n in names:
            zf.writestr(n, data[n])
    nonce = skill_pack.preview(zip_path)["nonce"]
    got = skill_pack.apply(nonce)
    assert got["ok"] is False and "sha256" in got["msg"]


def test_an_expired_or_unknown_nonce_cannot_write(tmp_path):
    assert skill_pack.apply("nope")["ok"] is False
    ws = tmp_path / "ws"
    _make_skill(ws)
    zip_path = skill_pack.pack(ws, "搜书指南", dest=tmp_path / "p.zip")["path"]
    nonce = skill_pack.preview(zip_path)["nonce"]
    skill_pack._previews[nonce]["at"] = 0        # 假装是 15 分钟前看的
    got = skill_pack.apply(nonce)
    assert got["ok"] is False and "过期" in got["msg"]


def test_a_pack_replaced_on_disk_after_the_preview_is_refused(tmp_path):
    """预览看过之后文件被人换掉:不能拿旧 nonce 写新内容。"""
    ws = tmp_path / "ws"
    _make_skill(ws)
    zip_path = Path(skill_pack.pack(ws, "搜书指南", dest=tmp_path / "p.zip")["path"])
    nonce = skill_pack.preview(zip_path)["nonce"]
    zip_path.write_bytes(b"not a zip anymore at all")
    got = skill_pack.apply(nonce)
    assert got["ok"] is False and "变了" in got["msg"]


def test_the_size_caps_are_the_same_numbers_the_messages_quote(tmp_path):
    """界面/日志里报的数字必须就是判断用的那个常量(抄一份就会漂)。"""
    src = Path("minicode/skill_pack.py").read_text(encoding="utf-8")
    assert "{MAX_FILES} 个的不收" in src, "拒收那一句把上限写成了字面量"
    assert "{MAX_FILE_BYTES // 1000} KB" in src
    assert skill_pack.MAX_FILES == 60 and skill_pack.BACKUP_KEEP == 10
