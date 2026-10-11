# -*- coding: utf-8 -*-
"""三方扩展的发现(v0.9.159):只认数据目录,manifest 缺一样整个目录不出现。

这一族钉的是"发现层不猜":坏 manifest 要**有名字地**进 skipped(装了没
生效的人得有地方查原因),entry 越目录要拒,id 不合规要拒 —— 每一条都是
"装了没生效,谁来告诉我"的答案。
"""
from __future__ import annotations

import json
from pathlib import Path

from minicode import extensions


def _ext(root: Path, name: str, manifest: dict, entry_body: str = "// ok\n") -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (d / manifest.get("entry", "page.js")).write_text(entry_body, encoding="utf-8")
    return d


def test_valid_extension_is_discovered_with_entry_path(tmp_path):
    # conftest 的隔离夹具会在 tmp_path 里放一个 "app" 目录 —— 扩展的根要自己圈一块
    root = tmp_path / "extensions"
    root.mkdir()
    _ext(root, "dbuddy", {"title": "数据库", "entry": "page.js",
                          "version": "0.1.0", "description": "看表"})
    got = extensions.discover(root)
    assert got["ok"] and len(got["items"]) == 1 and not got["skipped"]
    item = got["items"][0]
    assert item["id"] == "dbuddy" and item["title"] == "数据库"
    assert item["entry_path"].endswith("page.js")


def test_every_bad_shape_lands_in_skipped_with_a_reason(tmp_path):
    root = tmp_path / "extensions"
    root.mkdir()
    (root / "Bad Name").mkdir()                    # 目录名不合规
    (root / "nomanifest").mkdir()                  # 缺 manifest
    _ext(root, "badjson", {"title": "x"})
    (root / "badjson" / "manifest.json").write_text("{oops", encoding="utf-8")
    _ext(root, "notitle", {"entry": "page.js"})
    _ext(root, "escape", {"title": "越狱", "entry": "../steal.js"})
    _ext(root, "missingjs", {"title": "没文件", "entry": "nope.js"})
    (root / "missingjs" / "nope.js").unlink()   # _ext 助手会把 entry 建出来;这条要它真的缺
    got = extensions.discover(root)
    assert got["ok"] and not got["items"] and len(got["skipped"]) == 6
    reasons = {s["dir"]: s["msg"] for s in got["skipped"]}
    assert "目录名" in reasons["Bad Name"]
    assert "manifest" in reasons["nomanifest"]
    assert "读不出来" in reasons["badjson"]
    assert "title" in reasons["notitle"]
    assert "越出" in reasons["escape"]
    assert "不存在" in reasons["missingjs"]


def test_missing_root_is_an_empty_answer_not_an_error(tmp_path):
    got = extensions.discover(tmp_path / "extensions")
    assert got["ok"] and got["items"] == [] and got["skipped"] == []


def test_entry_default_is_page_js_and_dots_are_refused(tmp_path):
    root = tmp_path / "extensions"
    root.mkdir()
    _ext(root, "defaultentry", {"title": "默认入口"})
    got = extensions.discover(root)
    assert len(got["items"]) == 1
    assert got["items"][0]["entry_path"].endswith("page.js")
    _ext(root, "dotted", {"title": "点", "entry": "a/../page.js"})
    got2 = extensions.discover(root)
    assert any(s["dir"] == "dotted" for s in got2["skipped"])
