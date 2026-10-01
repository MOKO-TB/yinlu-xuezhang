"""「我的数据」面板与迁移包(v0.9.30 C 批)测试。

这组用例守的是三件事,按重要性排:
1. **密钥与教务登录态进不了包** —— 哪怕将来有人把它们写进白名单(内层黑名单闸);
2. **导入绝不覆盖比它新的本机数据**,而且不删任何东西;
3. zip 里的路径是**外来输入**:穿越、绝对路径、不认识的数据项一律拒。
"""
import hashlib
import json
import os
import re
import time
import zipfile
from pathlib import Path

import pytest

from minicode import config as config_mod
from minicode import mydata, paths

ROOT = Path(__file__).resolve().parent.parent     # 前端接线用例要读 index.html


@pytest.fixture(autouse=True)
def _app_dir(tmp_path, monkeypatch):
    """数据目录钉到临时目录;home 根(conftest 已把 CREDENTIALS_PATH 钉到
    tmp_path/credentials.json)就是 tmp_path 本身。"""
    root = tmp_path / "app"
    monkeypatch.setattr(paths, "APP_DIR", root)
    return root


def _write(rel: str, text: str = "x", root: str = "app", mtime=None) -> Path:
    path = (paths.app_path() if root == "app"
            else Path(config_mod.CREDENTIALS_PATH).parent).joinpath(*rel.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _home() -> Path:
    return Path(config_mod.CREDENTIALS_PATH).parent


def _write_credentials(text: str) -> Path:
    """密钥文件走绝对路径(它是 conftest 钉到临时目录的那个具体文件)。"""
    path = Path(config_mod.CREDENTIALS_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _seed_typical():
    """造一份"用过一阵子"的机器:课表 + 待办 + 记忆 + 聊天记录 + 配置 + 日志。"""
    _write("schedule.json", '{"rows":[{"name":"高数"}]}')
    _write("reminders.json", '{"items":[]}')
    _write("period-times.json", '{"periods":{}}')
    _write("sessions/s1.jsonl", '{"role":"user"}\n{"role":"assistant"}\n')
    _write("sessions/s2.jsonl", '{"role":"user"}\n')
    _write("memory/MEMORY.md", "- 用户喜欢 pytest", root="home")
    _write("config.json", json.dumps({
        "providers": [{"id": "z", "api_key": "sk-REAL-SECRET"}, {"id": "d"}],
        "mcp_servers": [{"headers": {"Authorization": "${MY_TOKEN}",
                                     "X-API-Key": "plain-secret"}}]}))
    _write("watchdog.log", "push class [x]:桌面已弹")
    _write("pushed-today.json", '{"date":"2026-09-22","keys":[]}')
    _write_credentials('{"z":"sk-NEVER-SHIP"}')
    (paths.app_path() / "browser-profile").mkdir(parents=True)
    _write("browser-profile/Cookies", "session-cookie-value")


# ---------------------------------------------------------------- 数据一览


def test_inventory_covers_every_table_entry_once():
    keys = [i["key"] for g in mydata.inventory()["groups"] for i in g["items"]]
    assert len(keys) == len(set(keys)) == len(mydata.TABLE)
    assert {"schedule.json", "sessions", "credentials.json"} <= set(keys)


def test_inventory_reports_missing_files_instead_of_erroring():
    inv = mydata.inventory()
    assert inv["ok"] is True
    row = next(i for g in inv["groups"] for i in g["items"] if i["key"] == "schedule.json")
    assert row["exists"] is False and row["size"] == "0 B"
    assert row["pack"] is False        # 没有的东西不进包(而不是"进包但空")


def test_inventory_counts_chat_sessions_with_size():
    p1 = _write("sessions/s1.jsonl", "{}\n{}\n{}\n")   # 3 行
    p2 = _write("sessions/s2.jsonl", "{}\n")
    row = next(i for g in mydata.inventory()["groups"]
               for i in g["items"] if i["key"] == "sessions")
    # 目录类条目的"几项"数的是**文件**:一个 jsonl 就是一段会话(不是行数)。
    # 字节数按文件的实际大小断言:write_text 在 Windows 上会把 \n 变成 \r\n。
    assert row["count"] == 2 and row["bytes"] == p1.stat().st_size + p2.stat().st_size
    assert row["pack"] is True


def test_inventory_separates_what_the_pack_will_carry():
    _seed_typical()
    rows = {i["key"]: i for g in mydata.inventory()["groups"] for i in g["items"]}
    assert rows["schedule.json"]["pack"] and rows["config.json"]["pack"]
    assert not rows["watchdog.log"]["pack"] and not rows["pushed-today.json"]["pack"]
    assert not rows["credentials.json"]["pack"]
    assert not rows["browser-profile"]["pack"]


def test_inventory_home_scope_lists_only_named_files():
    """`~/.yinluxs` 里既有能带的记忆也有不能带的密钥:只按名字点,绝不整目录扫。"""
    _write("memory/MEMORY.md", "mem", root="home")
    _write_credentials('{"k":"secret"}')
    _write("sibling-private.txt", "not listed", root="home")
    paths_in_panel = {i["path"] for g in mydata.inventory()["groups"] for i in g["items"]}
    assert str(_home() / "sibling-private.txt") not in paths_in_panel
    assert str(_home() / "memory" / "MEMORY.md") in paths_in_panel


def test_inventory_warns_honestly_when_dir_is_readonly(monkeypatch):
    monkeypatch.setattr(paths, "diagnostics", lambda: {
        "app_dir": "Z:/nowhere", "config": "", "backend": "local",
        "frozen": True, "writable": False})
    inv = mydata.inventory()
    assert inv["writable"] is False and any("写不进去" in w for w in inv["warnings"])


def test_inventory_never_category_explains_itself():
    group = next(g for g in mydata.inventory()["groups"] if g["category"] == "never")
    assert any("密钥" in i["note"] for i in group["items"])


# ---------------------------------------------------------------- 导出


def _pack_names(dest) -> list[str]:
    with zipfile.ZipFile(dest) as zf:
        return zf.namelist()


def test_export_packs_only_personal_and_pref(tmp_path):
    _seed_typical()
    out = mydata.export_package(tmp_path / "pack.zip")
    assert out["ok"] and out["files"] > 0
    names = _pack_names(out["path"])
    assert "files/app/schedule.json" in names
    assert "files/app/sessions/s1.jsonl" in names
    assert "files/home/memory/MEMORY.md" in names
    for banned in ("watchdog.log", "pushed-today.json", "web-usage.log",
                   "token-usage.db", "credentials.json", "Cookies", "browser-profile"):
        assert not any(banned in n for n in names), banned


def test_export_manifest_carries_version_and_file_table(tmp_path):
    _seed_typical()
    out = mydata.export_package(tmp_path / "pack.zip")
    with zipfile.ZipFile(out["path"]) as zf:
        m = json.loads(zf.read("manifest.json"))
    assert m["format"] == mydata.FORMAT and m["format_version"] == mydata.FORMAT_VERSION
    assert m["app_version"] and m["created_at"] and m["state_backend"]
    assert {f["key"] for f in m["files"]} >= {"schedule.json", "reminders.json"}
    assert all(f["sha256"] and f["bytes"] > 0 and f["mtime"] for f in m["files"])


def test_export_scrubs_api_keys_but_keeps_env_placeholders(tmp_path):
    _seed_typical()
    out = mydata.export_package(tmp_path / "pack.zip")
    with zipfile.ZipFile(out["path"]) as zf:
        blob = zf.read("files/app/config.json").decode("utf-8")
        m = json.loads(zf.read("manifest.json"))
    assert "sk-REAL-SECRET" not in blob and "plain-secret" not in blob
    assert "${MY_TOKEN}" in blob                    # 占位不是密钥,清掉只会少一段配置
    assert m["scrubbed_secret_fields"] == 2
    assert out["scrubbed"] == 2


def test_export_records_sha_of_the_shipped_bytes(tmp_path):
    """洗过 Key 的 config.json 内容已经变了:清单必须按**包里那份**算哈希,
    否则导入端逐件校验会对不上,唯一被洗过的文件反而永远导不进去。"""
    _seed_typical()
    out = mydata.export_package(tmp_path / "pack.zip")
    with zipfile.ZipFile(out["path"]) as zf:
        shipped = zf.read("files/app/config.json")
        m = json.loads(zf.read("manifest.json"))
    row = next(f for f in m["files"] if f["key"] == "config.json")
    assert row["sha256"] == hashlib.sha256(shipped).hexdigest()
    assert row["bytes"] == len(shipped)


def test_export_can_skip_chat_sessions(tmp_path):
    _seed_typical()
    out = mydata.export_package(tmp_path / "pack.zip", include_sessions=False)
    names = _pack_names(out["path"])
    assert not any(n.startswith("files/app/sessions/") for n in names)
    assert not any("MEMORY.md" in n for n in names)


def test_export_refuses_when_there_is_nothing_to_pack(tmp_path):
    out = mydata.export_package(tmp_path / "pack.zip")
    assert out["ok"] is False and "没有可导出的数据" in out["msg"]
    assert not (tmp_path / "pack.zip").exists()


def test_export_reports_write_failure_without_leaving_partial_pack(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a dir", encoding="utf-8")
    _seed_typical()
    out = mydata.export_package(blocker / "pack.zip")
    assert out["ok"] is False and "失败" in out["msg"]
    assert not list(tmp_path.glob("blocker*tmp"))


def test_export_refuses_oversized_pack(tmp_path, monkeypatch):
    _seed_typical()
    monkeypatch.setattr(mydata, "MAX_PACK_BYTES", 10)
    out = mydata.export_package(tmp_path / "pack.zip")
    assert out["ok"] is False and "聊天记录" in out["msg"]


def test_export_order_is_stable(tmp_path):
    """清单按 (root, key) 排序:同一台机器两次导出的条目顺序必须一样,
    不然"比对两份包差在哪"这种最基本的问题都没法回答。"""
    _seed_typical()
    a = mydata.export_package(tmp_path / "a.zip")
    b = mydata.export_package(tmp_path / "b.zip")
    with zipfile.ZipFile(a["path"]) as za, zipfile.ZipFile(b["path"]) as zb:
        na = [n for n in za.namelist() if n != "manifest.json"]
        nb = [n for n in zb.namelist() if n != "manifest.json"]
        fa = [(f["root"], f["key"]) for f in json.loads(za.read("manifest.json"))["files"]]
        fb = [(f["root"], f["key"]) for f in json.loads(zb.read("manifest.json"))["files"]]
    assert na == nb and fa == fb
    assert fa == sorted(fa)               # 键序可复现,不是遍历顺序的副产品


# ---------------------------------------------------- 两道闸:内层黑名单


def test_denied_entry_is_never_packed_even_if_declared_in_the_table(tmp_path,
                                                                    monkeypatch):
    """白名单之外还得有一道闸:下一个人往 TABLE 里加数据文件时写错名字,
    密钥也带不出去。这条测的就是**内层闸真的会响**。"""
    _seed_typical()
    sneaky = mydata.Entry("credentials.json", "手滑加进来的密钥", mydata.PERSONAL,
                          True, root="home")
    monkeypatch.setattr(mydata, "TABLE", mydata.TABLE + (sneaky,))
    out = mydata.export_package(tmp_path / "pack.zip")
    names = _pack_names(out["path"])
    assert not any("credentials" in n for n in names)
    assert any(d["key"] == "credentials.json" for d in out["skipped"])
    # 登录态同理:声明了也没用
    profile = mydata.Entry("browser-profile/Cookies", "手滑加进来的登录态",
                           mydata.PERSONAL, True)
    monkeypatch.setattr(mydata, "TABLE", mydata.TABLE + (profile,))
    out2 = mydata.export_package(tmp_path / "pack2.zip")
    assert not any("Cookies" in n for n in _pack_names(out2["path"]))


def test_pack_plan_excludes_every_non_pack_category():
    keys = {e.key for e in mydata._packable()}
    assert {"schedule.json", "config.json", "sessions"} <= keys
    assert not keys & {"watchdog.log", "token-usage.db", "browser-profile",
                       "credentials.json", "pushed-today.json", "web-sessions"}


# ---------------------------------------------------------------- 导入


def _make_pack(tmp_path, name, files: dict, manifest_files=None, version=1,
               fmt=None, created=None):
    """手搓一个迁移包:files 是 {zip内key: 内容}。"""
    dest = tmp_path / name
    rows = manifest_files if manifest_files is not None else [
        {"key": k.split("/")[-1] if k.startswith("app/") else k,
         "root": k.split("/")[0], "category": "personal",
         "bytes": len(v.encode()), "sha256": hashlib.sha256(v.encode()).hexdigest(),
         "mtime": created if created is not None else time.time() - 500}
        for k, v in files.items()]
    with zipfile.ZipFile(dest, "w") as zf:
        zf.writestr("manifest.json", json.dumps({
            "format": fmt or mydata.FORMAT, "format_version": version,
            "app_version": "0.9.30", "created_at": "2026-09-20T10:00:00",
            "files": rows}))
        for k, v in files.items():
            root, _, key = k.partition("/")
            zf.writestr(f"files/{root}/{key}", v)
    return dest


def test_read_package_rejects_non_zip_and_missing_file(tmp_path):
    junk = tmp_path / "junk.zip"
    junk.write_text("not a zip at all", encoding="utf-8")
    assert mydata.read_package(junk)["ok"] is False
    assert mydata.read_package(tmp_path / "nope.zip")["ok"] is False


def test_read_package_rejects_foreign_and_future_packs(tmp_path):
    foreign = _make_pack(tmp_path, "foreign.zip", {"app/schedule.json": "{}"},
                         manifest_files=[], fmt="someone-elses-backup")
    assert "不是引路学长" in mydata.read_package(foreign)["msg"]
    no_manifest = tmp_path / "bare.zip"
    with zipfile.ZipFile(no_manifest, "w") as zf:
        zf.writestr("files/app/schedule.json", "{}")
    assert "manifest" in mydata.read_package(no_manifest)["msg"]
    future = _make_pack(tmp_path, "future.zip", {"app/schedule.json": "{}"},
                        manifest_files=[], version=99)
    assert "升级" in mydata.read_package(future)["msg"]


def test_read_package_blocks_zip_slip_and_unknown_keys(tmp_path):
    manifest = {"format": mydata.FORMAT, "format_version": 1, "files": [
        {"key": "../evil.json", "root": "app", "bytes": 2, "sha256": "z", "mtime": 1},
        {"key": "/etc/passwd", "root": "app", "bytes": 2, "sha256": "z", "mtime": 1},
        {"key": "c:/windows/system32/x.json", "root": "app", "bytes": 2,
         "sha256": "z", "mtime": 1},
        {"key": "not-a-real-data-item.json", "root": "app", "bytes": 2,
         "sha256": "z", "mtime": 1},
        {"key": "schedule.json", "root": "app", "bytes": 2,
         "sha256": hashlib.sha256(b"{}").hexdigest(), "mtime": time.time() - 500},
    ]}
    dest = _make_pack(tmp_path, "evil.zip", {"app/schedule.json": "{}"},
                      manifest_files=manifest["files"])
    result = mydata.read_package(dest)
    actions = {r["key"]: r for r in result["rows"]}
    assert actions["../evil.json"]["action"] == "blocked"
    assert actions["/etc/passwd"]["action"] == "blocked"
    assert actions["c:/windows/system32/x.json"]["action"] == "blocked"
    assert actions["not-a-real-data-item.json"]["action"] == "blocked"
    assert actions["schedule.json"]["action"] in ("written", "replaced")
    mydata.import_apply("whatever")
    assert not (paths.app_path().parent / "evil.json").exists()
    assert not list(paths.app_path().rglob("evil.json"))


def test_read_package_blocks_declared_but_denied_paths(tmp_path, monkeypatch):
    """包里塞一条看着合法的 credentials.json:白名单不认识 + 黑名单再拦一道。"""
    files = [{"key": "credentials.json", "root": "home", "bytes": 6,
              "sha256": hashlib.sha256(b"secret").hexdigest(), "mtime": 1}]
    dest = _make_pack(tmp_path, "cre.zip", {"home/credentials.json": "secret"},
                      manifest_files=files)
    row = mydata.read_package(dest)["rows"][0]
    assert row["action"] == "blocked"


def test_import_pick_writes_nothing(tmp_path):
    dest = _make_pack(tmp_path, "p.zip", {"app/schedule.json": '{"rows":[]}'})
    picked = mydata.import_pick(dest)
    assert picked["ok"] and picked["counts"]["written"] == 1
    # 预览阶段一个字都不写(目录本身允许存在:测试总闸会预建 tmp/app)
    assert not list(paths.app_path().glob("**/*")) or all(
        p.name in ("web-usage.log",) for p in paths.app_path().glob("**/*"))
    assert mydata.import_apply(picked["nonce"])["written"] == 1


def test_kept_newer_when_local_copy_is_newer(tmp_path):
    """要求里最硬的一句:本机更新的那份**保留本机的**,而且要说清为什么不写。"""
    newer = time.time() + 3600
    _write("schedule.json", '{"rows":[{"name":"本机刚改的"}]}', mtime=newer)
    dest = _make_pack(tmp_path, "p.zip", {"app/schedule.json": '{"rows":[]}'},
                      created=time.time() - 500)
    result = mydata.read_package(dest)
    row = result["rows"][0]
    assert row["action"] == "kept-newer" and "保留本机" in row["reason"]
    applied = mydata.import_apply(mydata.import_pick(dest)["nonce"])
    assert applied["kept_newer"] == 1 and applied["written"] == 0
    assert "本机刚改的" in (paths.app_path() / "schedule.json").read_text("utf-8")


def test_import_replaces_older_local_and_backs_the_original_up(tmp_path):
    _write("schedule.json", '{"rows":[{"name":"旧版"}]}', mtime=time.time() - 7200)
    dest = _make_pack(tmp_path, "p.zip", {"app/schedule.json": '{"rows":[{"name":"包里版"}]}'}
                      , created=time.time() - 500)
    applied = mydata.import_apply(mydata.import_pick(dest)["nonce"])
    assert applied["replaced"] == 1 and applied["backup"]
    assert "包里版" in (paths.app_path() / "schedule.json").read_text("utf-8")
    backed = Path(applied["backup"]) / "app" / "schedule.json"
    assert "旧版" in backed.read_text("utf-8")           # 原件真的留下了
    assert (Path(applied["backup"]) / "decisions.json").is_file()


def test_import_is_additive_never_deletes(tmp_path):
    """导入不会删本机任何东西:包里没有的文件在导入后必须还在。"""
    _write("reminders.json", '{"items":[{"id":"r1"}]}')
    _write("watchdog.log", "本机日志")
    dest = _make_pack(tmp_path, "p.zip", {"app/schedule.json": "{}"})
    mydata.import_apply(mydata.import_pick(dest)["nonce"])
    assert (paths.app_path() / "reminders.json").is_file()
    assert (paths.app_path() / "watchdog.log").is_file()


def test_import_sha_mismatch_blocks_only_that_file(tmp_path):
    good = '{"rows":[]}'
    files = [{"key": "schedule.json", "root": "app", "bytes": 10,
              "sha256": hashlib.sha256(good.encode()).hexdigest(),
              "mtime": time.time() - 500},
             {"key": "reminders.json", "root": "app", "bytes": 2,
              "sha256": "0" * 64, "mtime": time.time() - 500}]
    dest = _make_pack(tmp_path, "p.zip",
                      {"app/schedule.json": good, "app/reminders.json": "tampered"},
                      manifest_files=files)
    applied = mydata.import_apply(mydata.import_pick(dest)["nonce"])
    assert applied["written"] == 1 and applied["blocked"] == 1
    assert applied["blocked_keys"] == ["reminders.json"]
    assert (paths.app_path() / "schedule.json").is_file()
    assert not (paths.app_path() / "reminders.json").exists()


def test_import_apply_requires_a_live_preview(tmp_path):
    assert mydata.import_apply("never-existed")["ok"] is False
    dest = _make_pack(tmp_path, "p.zip", {"app/schedule.json": "{}"})
    picked = mydata.import_pick(dest)
    mydata.forget_preview(picked["nonce"])
    assert mydata.import_apply(picked["nonce"])["ok"] is False   # 作废后写不进去


def test_import_apply_marks_restart_needed(tmp_path):
    """配置/课表这类有内存缓存的要提示"重启后生效",否则用户以为立刻变了。"""
    dest = _make_pack(tmp_path, "p.zip",
                      {"app/schedule.json": "{}", "app/period-times.json": "{}"})
    applied = mydata.import_apply(mydata.import_pick(dest)["nonce"])
    assert applied["restart_needed"] is True
    dest2 = _make_pack(tmp_path, "p2.zip", {"app/sessions/s9.jsonl": "{}\n"})
    assert mydata.import_apply(mydata.import_pick(dest2)["nonce"])["restart_needed"] \
        is False


def test_import_backups_prune_to_the_newest_three(tmp_path):
    old = [paths.app_path(mydata.BACKUP_DIR, f"2026010{i}-000000") for i in (1, 2, 3)]
    for d in old:
        (d / "app").mkdir(parents=True)
        (d / "app" / "schedule.json").write_text("x", encoding="utf-8")
    _write("schedule.json", "旧", mtime=time.time() - 7200)
    dest = _make_pack(tmp_path, "p.zip", {"app/schedule.json": "新"},
                      created=time.time() - 500)
    mydata.import_apply(mydata.import_pick(dest)["nonce"])
    left = sorted(p.name for p in (paths.app_path() / mydata.BACKUP_DIR).iterdir())
    assert len(left) == mydata.BACKUP_KEEP and left[-1] > old[-1].name


def test_round_trip_between_two_machines(tmp_path, monkeypatch):
    """A 机导出 → B 机导入:内容必须一模一样,而且 B 机不会被塞进日志与密钥。"""
    _seed_typical()
    a_dir = paths.app_path()
    credentials = Path(config_mod.CREDENTIALS_PATH)
    credentials_before = credentials.read_bytes()
    pack = mydata.export_package(tmp_path / "move.zip")
    assert pack["ok"]
    # 密钥连包本身都进不去:解压每一份内容来查(压缩后再查字面量等于没查)
    with zipfile.ZipFile(tmp_path / "move.zip") as zf:
        assert all(b"sk-NEVER-SHIP" not in zf.read(n) for n in zf.namelist())
        assert not any("credentials" in n or "browser-profile" in n
                       for n in zf.namelist())
    (_home() / "memory" / "MEMORY.md").unlink()   # B 机是新机器:记忆还没落过盘
    (a_dir / "watchdog.log").unlink()            # 连 A 机的日志都不该跟着走
    b_dir = tmp_path / "B机"
    b_dir.mkdir()
    monkeypatch.setattr(paths, "APP_DIR", b_dir)
    picked = mydata.import_pick(tmp_path / "move.zip")
    applied = mydata.import_apply(picked["nonce"])
    assert applied["ok"] and applied["blocked"] == 0
    assert (b_dir / "schedule.json").read_text("utf-8") == \
        (a_dir / "schedule.json").read_text("utf-8")
    assert (b_dir / "sessions" / "s1.jsonl").is_file()
    assert "pytest" in (_home() / "memory" / "MEMORY.md").read_text("utf-8")
    cfg = json.loads((b_dir / "config.json").read_text("utf-8"))
    assert cfg["providers"][0]["api_key"] == ""
    assert "sk-NEVER-SHIP" not in json.dumps(applied)
    # 新机器的数据目录**只有**该带的东西:日志/缓存/统计/登录态一份都没跟过来
    assert sorted(p.name for p in b_dir.iterdir()) == [
        "config.json", "period-times.json", "reminders.json", "schedule.json",
        "sessions"]
    assert credentials.read_bytes() == credentials_before   # 导入不碰 B 机的密钥
    assert not (b_dir / "browser-profile").exists()


# ---------------------------------------------------------------- 桥接层


class _Ui:
    """假窗口层:只要能判断"窗口在不在",并把 ui.push 的话记下来。"""

    def __init__(self, window=None):
        self.window = window
        self.seen = []

    def push(self, kind, text=""):
        self.seen.append((kind, text))


def _md_bridge(tmp_path, monkeypatch, window=None):
    from types import SimpleNamespace
    from minicode.gui_bridge import Bridge
    return SimpleNamespace(ui=_Ui(window), workspace=tmp_path / "ws"), Bridge


def _save_dialog_picks(b, dest):
    """系统「另存为」的替身:返回 None 等于用户按了取消。"""
    b._ask_save_path = lambda name, file_types=(): dest


def _open_dialog_picks(b, src):
    b._ask_open_path = lambda file_types=(): src


def test_bridge_refuses_without_a_window_and_writes_nothing(tmp_path, monkeypatch):
    """无窗口(pywebview 还没就绪)时不能崩,也不能凭空落一个包。"""
    b, Bridge = _md_bridge(tmp_path, monkeypatch)
    previews = len(mydata._previews)
    for name in ("mydata_export", "mydata_import_pick"):
        r = getattr(Bridge, name)(b)
        assert r["ok"] is False and "窗口" in r["msg"], name
    assert not list(tmp_path.glob("*.zip"))
    assert len(mydata._previews) == previews    # 拒绝分支连预览都不该登记


def test_cancelled_export_is_not_an_error_and_leaves_no_file(tmp_path, monkeypatch):
    b, Bridge = _md_bridge(tmp_path, monkeypatch, window=object())
    _save_dialog_picks(b, None)
    r = Bridge.mydata_export(b)
    assert r["ok"] is False and "取消" in r["msg"]
    assert list(tmp_path.glob("*.zip")) == []
    assert not b.ui.seen        # 取消不该弹一条"已导出"


def test_import_pick_comes_from_the_open_dialog_and_writes_nothing(tmp_path,
                                                                   monkeypatch):
    """第 1 步只给预览:路径由系统文件框决定(前端只能拿到 nonce),一个字都不写。"""
    pack = _make_pack(tmp_path, "in.zip", {"app/schedule.json": "包里的内容"})
    b, Bridge = _md_bridge(tmp_path, monkeypatch, window=object())
    previews = len(mydata._previews)
    _open_dialog_picks(b, None)
    assert Bridge.mydata_import_pick(b)["ok"] is False
    assert len(mydata._previews) == previews        # 按取消不该留下待写的预览

    _open_dialog_picks(b, pack)
    r = Bridge.mydata_import_pick(b)
    assert r["ok"] and r["nonce"] and len(r["rows"]) == 1
    assert not paths.app_path().joinpath("schedule.json").exists()
    assert r["rows"][0]["action"] == "written"
    # 第 2 步只要 nonce:前端拿到的是一整份可展示的预览,不需要也没法再传路径
    assert {"nonce", "rows", "counts", "size"} <= set(r)
    assert len(mydata._previews) == previews + 1


def test_export_goes_where_the_dialog_said(tmp_path, monkeypatch):
    _write("schedule.json", '{"classes":[]}')
    dest = tmp_path / "自选目录" / "move.zip"
    b, Bridge = _md_bridge(tmp_path, monkeypatch, window=object())
    _save_dialog_picks(b, dest)
    r = Bridge.mydata_export(b)
    assert r["ok"] and Path(r["path"]) == dest and dest.is_file()
    assert b.ui.seen and "迁移包已导出" in b.ui.seen[0][1]


def test_js_false_string_does_not_pack_chat_history(tmp_path, monkeypatch):
    """勾选框传过来可能是字符串 "false":按 Python 真值判会把用户明确去掉的
    勾选当成真,聊天记录就这么被塞进要发给别人的包里。"""
    _write("sessions/s1.jsonl", "{}\n")
    _write("schedule.json", "x")
    dest = tmp_path / "p.zip"
    b, Bridge = _md_bridge(tmp_path, monkeypatch, window=object())
    _save_dialog_picks(b, dest)
    for raw in ("false", "False", "0", "no", ""):
        dest.unlink(missing_ok=True)
        assert Bridge.mydata_export(b, raw)["ok"], raw
        with zipfile.ZipFile(dest) as zf:
            assert not any("/sessions/" in n for n in zf.namelist()), raw
    dest.unlink(missing_ok=True)
    assert Bridge.mydata_export(b, True)["ok"]
    with zipfile.ZipFile(dest) as zf:
        assert any("/sessions/" in n for n in zf.namelist())


def test_import_apply_reports_the_four_numbers_to_the_user(tmp_path, monkeypatch):
    pack = _make_pack(tmp_path, "in.zip",
                      {"app/schedule.json": "新内容", "app/weather.json": "{}"})
    b, Bridge = _md_bridge(tmp_path, monkeypatch)
    r = Bridge.mydata_import_apply(b, mydata.import_pick(pack)["nonce"])
    assert r["ok"] and r["written"] == 2
    assert len(b.ui.seen) == 1 and "迁移包已导入" in b.ui.seen[0][1]
    assert "重启" in b.ui.seen[0][1]      # 课表有内存缓存,不重启看不到
    before = len(b.ui.seen)
    assert Bridge.mydata_import_apply(b, "乱写的编号")["ok"] is False
    assert len(b.ui.seen) == before       # 没写成就别推一条成功提示


def test_forget_import_invalidates_the_preview(tmp_path, monkeypatch):
    pack = _make_pack(tmp_path, "in.zip", {"app/schedule.json": "x"})
    b, Bridge = _md_bridge(tmp_path, monkeypatch)
    nonce = mydata.import_pick(pack)["nonce"]
    assert Bridge.mydata_forget_import(b, nonce)["ok"] is True
    assert mydata.import_apply(nonce)["ok"] is False


def test_open_dir_opens_only_the_data_directory(tmp_path, monkeypatch):
    """只开目录,并且路径来自 `paths.app_path()`(不是前端字符串)。"""
    b, Bridge = _md_bridge(tmp_path, monkeypatch)
    called = []
    monkeypatch.setattr(os, "startfile", lambda p: called.append(p), raising=False)
    r = Bridge.mydata_open_dir(b)
    if not hasattr(os, "startfile"):            # 非 Windows:明说开不了,而不是抛
        assert r["ok"] is False and "平台" in r["msg"]
        return
    assert r["ok"] and r["path"] == str(paths.app_path())
    assert called == [str(paths.app_path())]


def test_open_dir_reports_a_refusal_instead_of_throwing(tmp_path, monkeypatch):
    b, Bridge = _md_bridge(tmp_path, monkeypatch)

    def boom(_p):
        raise OSError(5, "拒绝访问")
    monkeypatch.setattr(os, "startfile", boom, raising=False)
    r = Bridge.mydata_open_dir(b)
    if not hasattr(os, "startfile"):
        pytest.skip("这个平台压根没有 startfile,走不到 OSError 分支")
    assert r["ok"] is False and "打不开" in r["msg"]


def test_facade_forwards_every_mydata_method_with_safe_params():
    """漏一条转发 = 前端点了「数据」什么也没发生;参数类型 = pywebview 的契约。"""
    import inspect

    from minicode.gui_bridge import PublicApi

    names = ["mydata_inventory", "mydata_export", "mydata_import_pick",
             "mydata_import_apply", "mydata_forget_import", "mydata_open_dir"]

    class Recorder:
        def __init__(self):
            self.seen = []

        def __getattr__(self, name):
            def call(*args):
                self.seen.append((name, args))
                return {"ok": True}
            return call

    b = Recorder()
    api = PublicApi(b)
    sigs = {n: list(inspect.signature(getattr(PublicApi, n), eval_str=True)
                    .parameters.values())[1:] for n in names}
    want = {"mydata_export": "bool(勾选框)", "mydata_import_apply": "str(nonce)",
            "mydata_forget_import": "str(nonce)"}
    for n in names:                       # 只按签名补参数调用,不猜前端的形状
        getattr(api, n)(*(True if p.annotation is bool else "" for p in sigs[n]))
    assert [s[0] for s in b.seen] == names, "门面漏转发 = 前端点了没反应"
    for n, params in sigs.items():
        for p in params:
            # pywebview 只把 JS 标量可靠地送过来:路径/对象一律不下发
            assert p.annotation in (str, bool), f"{n}({p.name}): {p.annotation}"
            assert (p.annotation is bool) == (n == "mydata_export"), \
                f"{n}({p.name}) 该是 {want.get(n, 'str')}"


# ---------------------------------------------------------------- 网页版与前端接线


def test_web_facade_refuses_the_mydata_panel():
    """网页版列的是服务器本机的文件,对访客既没意义也是路径泄露 —— 整组明确拒绝。"""
    from minicode.web_profile import PublicFacade

    facade = PublicFacade(object())
    for name in ("mydata_inventory", "mydata_export", "mydata_import_pick",
                 "mydata_import_apply", "mydata_forget_import", "mydata_open_dir"):
        assert name in facade.allowed_methods(), f"{name} 不在册:前端会拿到 undefined"
        r = facade.call(name)
        assert r["ok"] is False and "我的数据" in r["msg"]
        assert "desktop" not in r["msg"]      # 拒绝原因要能直接给人看


def test_frontend_only_calls_mydata_methods_the_facade_has():
    """前端写错一个方法名 = 静默失败(只有 catch 里一句"没接上")。当场对账。"""
    from minicode.gui_bridge import PublicApi

    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    used = set(re.findall(r"\.(mydata_[a-z_]+)\(", html))
    exposed = {n for n in dir(PublicApi) if n.startswith("mydata_")}
    assert used == exposed, f"前端调用与门面方法不是一回事:{used ^ exposed}"


def test_mydata_panel_ids_used_by_js_all_exist():
    """`$("mdTotals")` 拼错一个字母 → 面板空白,而 node --check 一律发现不了。"""
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    used = set(re.findall(r'\$\("(md[A-Za-z]*|btnMd[A-Za-z]*|btnMyData|'
                          r'myDataOverlay)"\)', html))
    declared = set(re.findall(r'id="(md[A-Za-z]*|btnMd[A-Za-z]*|btnMyData|'
                              r'myDataOverlay)"', html))
    assert used and used <= declared, f"JS 引用了不存在的 id:{sorted(used - declared)}"


def test_public_web_build_hides_the_data_button():
    """对外模式连入口都不该有:面板列的是服务器自己机器上的文件路径。"""
    js = (ROOT / "frontend" / "bridge-web.js").read_text(encoding="utf-8")
    assert "#btnMyData" in js, "对外模式没把「数据」按钮藏起来"


# ------------------------------------------- 数据归属标注(W2,v0.9.99)

def test_inventory_rows_carry_scope(tmp_path, monkeypatch):
    """面板逐行带 scope:课表/成绩/作息表=全局一份(校园数据与仓库无关),
    面板把它写出来,不让用户猜『开了工作副本后课表会不会跟过去』。"""
    from minicode import mydata
    monkeypatch.setattr(mydata.paths, "APP_DIR", tmp_path / "app")
    rows = mydata.inventory(workspace=str(tmp_path))
    all_items = [it for g in rows.get("groups", []) for it in g.get("items", [])]
    by_key = {it["key"]: it for it in all_items}
    assert by_key["schedule.json"]["scope"] == "global"
    assert by_key["scores.json"]["scope"] == "global"
    assert by_key["sessions"]["scope"] == ""          # 留档没有归属一说


def test_scope_marks_are_complete_for_campus_data():
    """声明完整性:凡校园类个人数据必须标 global,漏标=面板上那一行不显示归属。"""
    from minicode import mydata
    for e in mydata.TABLE:
        if e.key in ("schedule.json", "schedule.prev.json", "scores.json",
                     "period-times.json", "workspaces.json", "memory/MEMORY.md"):
            assert e.scope == "global", (e.key, e.scope)
