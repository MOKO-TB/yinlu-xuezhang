"""校历补录助手测试(v0.9.62):引用是准入门槛、可信度跟来源走、追加是唯一写法。"""
import json

import pytest

from minicode import calendar_edit
from minicode import paths
from minicode.calendar import CALENDAR_FILE


@pytest.fixture(autouse=True)
def _app_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "APP_DIR", tmp_path / "app")


def _e(name="期末考试周", start="2027-01-18", **kw):
    entry = {"name": name, "start": start,
             "quote": "期末考试定于1月18日至22日进行", "kind": "exam",
             "source": "用户提供"}
    entry.update(kw)
    return entry


def _seed(events):
    path = paths.APP_DIR / CALENDAR_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"notes": "维护说明", "events": events},
                               ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- 校验

def test_validate_requires_name_start_and_quote():
    clean, problems = calendar_edit.validate(
        [{"name": "没引用的考试", "start": "2027-01-18"},
         {"name": "", "start": "2027-01-18", "quote": "x"},
         _e()], [])
    assert len(clean) == 1 and clean[0]["name"] == "期末考试周"
    assert len(problems) == 2
    assert any("没有引用原文" in p for p in problems)


def test_validate_dates_and_kind():
    clean, problems = calendar_edit.validate([
        _e(name="坏日期", start="1月18日"),
        _e(name="倒挂", start="2027-01-22", end="2027-01-18"),
        _e(name="怪类别", start="2027-02-01", kind="运动会"),
        _e(name="正常", start="2027-02-01", end="2027-02-05"),
    ], [])
    # 日期与引用是完整性门槛;kind 只是标签:不认识的落 event 并说明
    assert [e["name"] for e in clean] == ["怪类别", "正常"]
    assert clean[0]["kind"] == "event"
    assert any("ISO 日期" in p for p in problems)
    assert any("早于 start" in p for p in problems)
    assert any("不认识" in p and "event" in p for p in problems)


def test_validate_dedups_against_existing_and_batch():
    existing = [{"id": "x", "name": "期末考试周", "start": "2027-01-18"}]
    clean, problems = calendar_edit.validate([
        _e(), _e(name="期末考试周", start="2027-01-18"),          # 与现有重复
        _e(name="第二门", start="2027-01-19"),
        _e(name="第二门", start="2027-01-19"),                    # 批内重复
    ], existing)
    assert [e["name"] for e in clean] == ["第二门"]
    assert len([p for p in problems if "重复" in p]) == 3      # 两条撞现有 + 一条批内


def test_validate_credibility_follows_source():
    clean, _ = calendar_edit.validate([
        _e(name="官网条目", start="2027-01-18",
           source="https://www.gzasc.edu.cn/jwc/info/1003/53040.htm"),
        _e(name="口头条目", start="2027-01-19", source="用户提供"),
    ], [])
    by = {e["name"]: e for e in clean}
    assert by["官网条目"]["credibility"].startswith("A")   # 域名白名单给 A
    assert by["口头条目"]["credibility"] == "B-待核实"


def test_validate_generates_unique_ids():
    clean, _ = calendar_edit.validate([
        _e(), _e(start="2027-06-15"),                          # 同名不同日
    ], [])
    assert clean[0]["id"] != clean[1]["id"]
    assert "2027-01-18" in clean[0]["id"]


# ---------------------------------------------------------------- 入库

def test_commit_appends_and_preserves_existing(tmp_path):
    _seed([{"id": "seed", "name": "已有条目", "start": "2026-09-01",
            "end": "", "kind": "term"}])
    result = calendar_edit.commit([_e()])
    assert result["ok"] is True and result["added"] == 1
    data = json.loads((paths.APP_DIR / CALENDAR_FILE).read_text(encoding="utf-8"))
    assert data["notes"] == "维护说明"                          # 原有说明不动
    assert [e["name"] for e in data["events"]] == ["已有条目", "期末考试周"]
    assert data["events"][1]["quote"].startswith("期末考试定于")


def test_commit_revalidates_and_never_writes_garbage():
    _seed([])
    result = calendar_edit.commit([{"name": "没引用", "start": "2027-01-18"}])
    assert result["ok"] is False
    data = json.loads((paths.APP_DIR / CALENDAR_FILE).read_text(encoding="utf-8"))
    assert data["events"] == []                                # 一个字都没追加


def test_commit_empty_is_rejected():
    assert calendar_edit.commit([])["ok"] is False


# ---------------------------------------------------------------- 工具

def test_tool_preview_and_commit(tmp_path):
    class Ctx:
        workspace = tmp_path

    out = calendar_edit.calendar_edit_tool(Ctx(), action="preview",
                                           entries=[_e()])
    assert "请逐条核对" in out and "期末考试定于" in out
    out2 = calendar_edit.calendar_edit_tool(Ctx(), action="commit",
                                            entries=[_e()])
    assert "已入库 1 条" in out2
    assert calendar_edit.calendar_edit_tool(Ctx(), action="法术").startswith("错误:")


def test_tool_preview_on_real_repository_calendar():
    """预览链路读的是真校历文件(仓库里那份):重复检测对真实数据生效。"""
    class Ctx:
        workspace = None

    out = calendar_edit.calendar_edit_tool(Ctx(), action="preview",
                                           entries=[_e(name="中秋节",
                                                       start="2026-09-25")])
    assert "重复" in out                                        # 与仓库里的中秋条目撞名同日
