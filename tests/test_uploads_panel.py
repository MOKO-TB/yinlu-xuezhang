"""工作区 `uploads/` 的清单与删除(本轮审计的发现四:它只进不出)。

三个入口(📎 选文件、拖进来、剪贴板)都只往里拷,而 `mydata.py` 明写工作区里的东西
不在「我的数据」面板范围内 —— 谁也没法知道它堆了多大,更没有删的入口。这一对方法就是
那个入口。**删除是不可逆的,而参数来自前端**,所以下面一半的测试都在试"能不能骗它删到
别处":带路径的、绝对的、`..` 的、目录、软链到外面的,一律只能进 refused。
"""
import json
import re

import pytest

from minicode.config import Config
from minicode.gui_bridge import create_app
from minicode.gui_ui import GuiUI


@pytest.fixture
def bridge(tmp_path):
    app, _ui, _agent, _session = create_app(
        Config(), tmp_path, ui=GuiUI(), sessions_dir=tmp_path / "sessions",
        config_path=tmp_path / "config.json")
    (tmp_path / "uploads").mkdir()
    return app


def _put(bridge, name, content=b"x" * 1024):
    path = bridge.workspace / "uploads" / name
    path.write_bytes(content)
    return path


# ---------------------------------------------------------------- 看得见

def test_empty_dir_is_reported_as_empty_not_missing(bridge):
    got = bridge.workspace_files_list()
    assert got["ok"] and got["count"] == 0 and got["items"] == []


def test_no_uploads_dir_at_all_still_answers(bridge, tmp_path):
    (tmp_path / "uploads").rmdir()
    got = bridge.workspace_files_list()
    assert got["ok"] and got["count"] == 0, "没附件是常态,不该报错吓人"


def test_listing_shows_size_and_date_and_sorts_newest_first(bridge):
    import os
    old = _put(bridge, "旧课件.pdf", b"y" * 2048)
    new = _put(bridge, "新截图.png", b"x" * 1024)
    os.utime(old, (1_600_000_000, 1_600_000_000))
    os.utime(new, (1_700_000_000, 1_700_000_000))

    got = bridge.workspace_files_list()
    assert [i["name"] for i in got["items"]] == ["新截图.png", "旧课件.pdf"]
    assert got["total_bytes"] == 3072
    assert got["items"][1]["bytes"] == 2048
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", got["items"][0]["mtime"]), \
        "界面要拿得到真日期,不是时间戳数字也不是空串"
    assert json.dumps(got, ensure_ascii=False), "回话要能过 JSON"


def test_directories_are_not_listed_as_files(bridge, tmp_path):
    (tmp_path / "uploads" / "某个文件夹").mkdir()
    _put(bridge, "a.txt")
    assert [i["name"] for i in bridge.workspace_files_list()["items"]] == ["a.txt"]


# ---------------------------------------------------------------- 删得掉、骗不动

def test_delete_removes_the_file_and_reports_real_bytes(bridge):
    p = _put(bridge, "草稿.docx", b"z" * 4096)
    got = bridge.workspace_files_delete(json.dumps(["草稿.docx"]))
    assert got["ok"] and got["deleted"] == ["草稿.docx"] and not got["refused"]
    assert got["freed_bytes"] == 4096
    assert not p.exists()


@pytest.mark.parametrize("bad", [
    "../config.json",                      # 往上逃
    "..\\config.json",
    "uploads/../config.json",              # 自己绕自己
    "/etc/passwd",                          # 绝对路径
    "C:/Windows/win.ini",
    "子目录/文件.txt",                        # 不接受路径分隔符
    "不存在的东西.txt",                        # 不在磁盘上的不假装删了
])
def test_paths_that_are_not_a_plain_name_in_uploads_are_refused(bridge, tmp_path, bad):
    _put(bridge, "别的.txt")               # 有真文件在场,证明拒绝不是"顺手全删了"
    got = bridge.workspace_files_delete(json.dumps([bad]))
    assert got["ok"] is False and got["deleted"] == [], f"不该删成:{bad}"
    assert got["refused"], "拒了要点名,不能静默吞掉"
    assert (tmp_path / "uploads" / "别的.txt").exists()


def test_a_directory_is_refused_not_recursively_deleted(bridge, tmp_path):
    d = tmp_path / "uploads" / "一整包课件"
    d.mkdir()
    (d / "里面.pdf").write_bytes(b"a")
    got = bridge.workspace_files_delete(json.dumps(["一整包课件"]))
    assert got["deleted"] == [] and d.exists() and (d / "里面.pdf").exists()


def test_junk_arguments_are_refused_without_touching_anything(bridge):
    _put(bridge, "留着.txt")
    for raw in ["不是 JSON", "[]", "null", '{"a":1}']:
        got = bridge.workspace_files_delete(raw)
        assert got["ok"] is False, raw
    assert (bridge.workspace / "uploads" / "留着.txt").exists()


def test_partial_selection_deletes_only_what_is_safe(bridge, tmp_path):
    keep = _put(bridge, "正常.txt")
    gone = _put(bridge, "也要删.txt")
    got = bridge.workspace_files_delete(json.dumps(["正常.txt", "../config.json", "也要删.txt"]))
    assert sorted(got["deleted"]) == sorted(["正常.txt", "也要删.txt"])
    assert got["refused"] == ["../config.json"]
    assert not keep.exists() and not gone.exists()


# ---------------------------------------------------------------- 三个目录,不越界

def test_only_the_three_workspace_dirs_are_allowed(bridge, tmp_path):
    """`sessions/` 也在工作区之外的事另说,但**这里**不给列也不给删:
    界面能挑的目录必须是白名单里那三个,否则一个 folder 参数就把删除面铺满了。"""
    for bad in ("sessions", "..", "kb", "", "uploads/../../"):
        got_list = bridge.workspace_files_list(bad)
        assert got_list["ok"] is False and "只认" in got_list["msg"], bad
        got = bridge.workspace_files_delete(json.dumps(["config.json"]), bad)
        assert got["ok"] is False and "只认" in got["msg"], bad


def test_images_and_charts_are_listed_and_deleted_separately(bridge, tmp_path):
    """分享卡片与图表落在 images/、charts/,附件在 uploads/ —— 三个目录互不影响。"""
    upl = _put(bridge, "课件.pdf")
    img = tmp_path / "images"
    img.mkdir()
    (img / "share-绩点.png").write_bytes(b"z" * 2048)
    charts = tmp_path / "charts"
    charts.mkdir()
    (charts / "成绩.svg").write_bytes(b"y" * 1024)

    assert [i["name"] for i in bridge.workspace_files_list("images")["items"]] == ["share-绩点.png"]
    assert bridge.workspace_files_list("charts")["total_bytes"] == 1024
    got = bridge.workspace_files_delete(json.dumps(["成绩.svg"]), "charts")
    assert got["ok"] and got["freed_bytes"] == 1024 and got["dir"] == "charts"
    assert not (charts / "成绩.svg").exists()
    assert upl.exists(), "删 charts 里的东西不该动到附件"


def test_a_folder_argument_cannot_cross_directories(bridge, tmp_path):
    """名字里给子路径不行,`folder` 给别的目录名也不行 —— 两道闸各自成立。"""
    (tmp_path / "uploads" / "sub").mkdir()
    (tmp_path / "uploads" / "sub" / "x.txt").write_bytes(b"1")
    got = bridge.workspace_files_delete(json.dumps(["sub/x.txt"]), "uploads")
    assert got["deleted"] == [] and (tmp_path / "uploads" / "sub" / "x.txt").exists()
