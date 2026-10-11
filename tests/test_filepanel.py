# -*- coding: utf-8 -*-
"""侧边栏「文件」页的后端(v0.9.159):围栏内的读与写。

四条边界各有一支针:围栏(敏感/凭据/工作区外)、二进制不装懂、
保存对底账(磁盘被别人改过就拒)、原子写。这一页是整个项目里
唯一一个"前端直接写盘"的口 —— 针只多不少。
"""
from __future__ import annotations

from pathlib import Path

from minicode import filepanel


def _ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    # write_bytes:Path.write_text 在 Windows 会把 \n 翻成 \r\n,而 filepanel.read
    # 的职责是**逐字节忠实** —— 夹具自己先把字节钉死,读回来才有的比。
    (ws / "hello.txt").write_bytes("你好\n".encode("utf-8"))
    (ws / "bin.dat").write_bytes(b"PK\x00\x01\x00binary")
    return ws


# ------------------------------------------------------------- 围栏

def test_fences_block_sensitive_credential_and_outside(tmp_path):
    ws = _ws(tmp_path)
    for raw in ("~/.ssh/id_rsa", "C:/Windows/win.ini", "../outside.txt",
                "config.local.json"):
        got = filepanel.read(ws, raw)
        assert not got["ok"], raw
        assert not got.get("binary"), raw


def test_read_returns_text_sha_and_size(tmp_path):
    ws = _ws(tmp_path)
    got = filepanel.read(ws, "hello.txt")
    assert got["ok"] and got["text"] == "你好\n"
    assert got["sha1"] and len(got["sha1"]) == 40 and got["size"] == 7


def test_binary_files_are_admitted_not_garbled(tmp_path):
    ws = _ws(tmp_path)
    got = filepanel.read(ws, "bin.dat")
    assert not got["ok"] and got.get("binary"), "二进制要明说,不硬解成乱码"


def test_missing_file_says_so(tmp_path):
    ws = _ws(tmp_path)
    got = filepanel.read(ws, "nope.txt")
    assert not got["ok"] and "不存在" in got["msg"]


# ------------------------------------------------------------- 写与底账

def test_write_roundtrip_updates_sha(tmp_path):
    ws = _ws(tmp_path)
    first = filepanel.read(ws, "hello.txt")
    got = filepanel.write(ws, "hello.txt", "第二行\n", first["sha1"])
    assert got["ok"] and not got["created"]
    assert (ws / "hello.txt").read_text(encoding="utf-8") == "第二行\n"
    again = filepanel.read(ws, "hello.txt")
    assert again["sha1"] == got["sha1"]


def test_write_refuses_when_disk_changed_since_open(tmp_path):
    ws = _ws(tmp_path)
    first = filepanel.read(ws, "hello.txt")
    (ws / "hello.txt").write_text("别的编辑器改的\n", encoding="utf-8")
    got = filepanel.write(ws, "hello.txt", "我要盖掉它\n", first["sha1"])
    assert not got["ok"] and got.get("conflict")
    assert (ws / "hello.txt").read_text(encoding="utf-8") == "别的编辑器改的\n", \
        "绝不静默覆盖对方字节"


def test_write_refuses_without_base_sha_on_existing_file(tmp_path):
    ws = _ws(tmp_path)
    got = filepanel.write(ws, "hello.txt", "覆盖\n", "")
    assert not got["ok"] and got.get("conflict")


def test_write_new_file_creates_parents_and_records_created(tmp_path):
    ws = _ws(tmp_path)
    got = filepanel.write(ws, "sub/dir/new.md", "# 新\n", "")
    assert got["ok"] and got["created"]
    assert (ws / "sub" / "dir" / "new.md").read_text(encoding="utf-8") == "# 新\n"
    # "新建"撞上"已经被建出来":后到的那次必须退回来
    got2 = filepanel.write(ws, "sub/dir/new.md", "后到的\n", "")
    assert not got2["ok"] and got2.get("conflict")


def test_write_refuses_fenced_paths(tmp_path):
    ws = _ws(tmp_path)
    for raw in ("C:/Temp/x.txt", "../x.txt", "secret.key"):
        got = filepanel.write(ws, raw, "x", "")
        assert not got["ok"], raw
