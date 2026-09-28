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
    entry = {"id": f"{name}-{start}",       # id 选择器要在核对单里找得到
             "name": name, "start": start,
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


# ---------------------------------------------------------------- 删除与修订(v0.9.73)

def test_remove_by_id_and_by_name_start():
    _seed([_e(), _e(name="校运会", start="2026-11-20")])
    data, _ = calendar_edit.load_events_file()
    hits, missing = calendar_edit._resolve_targets(
        [{"id": "期末考试周-2027-01-18"}], data["events"])
    assert len(hits) == 1 and not missing
    hits, missing = calendar_edit._resolve_targets(
        [{"name": "校运会", "start": "2026-11-20"}], data["events"])
    assert len(hits) == 1 and hits[0][1]["name"] == "校运会"


def test_resolve_missing_is_listed_never_guessed():
    _seed([_e()])
    data, _ = calendar_edit.load_events_file()
    hits, missing = calendar_edit._resolve_targets(
        [{"name": "不存在的活动", "start": "2026-01-01"},
         {"name": "只有名字"}, {}], data["events"])
    assert hits == []
    assert len(missing) == 3
    assert any("不猜" in m for m in missing)


def test_remove_preview_lists_full_entries():
    _seed([_e(note="含元旦假期安排")])
    out = calendar_edit.remove_preview_text([{"id": "期末考试周-2027-01-18"}])
    assert "将从校历删除" in out
    assert "期末考试周" in out and "2027-01-18" in out
    assert "期末考试定于" in out            # 原文引用跟着上核对单
    assert "用户提供" in out and "备注" in out
    assert "确认删除" in out


def test_remove_commit_deletes_and_backs_up():
    _seed([_e(), _e(name="校运会", start="2026-11-20")])
    out = calendar_edit.calendar_edit_tool(
        None, action="remove_commit", targets=[{"name": "校运会",
                                                "start": "2026-11-20"}])
    assert "已删除 1 条" in out and "备份" in out
    data, _ = calendar_edit.load_events_file()
    names = [e["name"] for e in data["events"]]
    assert names == ["期末考试周"]
    hist = list((paths.APP_DIR / "kb" / "history").glob("school-calendar-*.json"))
    assert len(hist) == 1
    kept = json.loads(hist[0].read_text(encoding="utf-8"))
    assert [e["name"] for e in kept["events"]] == ["期末考试周", "校运会"]


def test_remove_commit_refuses_when_backup_cannot_be_written(monkeypatch):
    _seed([_e()])
    monkeypatch.setattr(calendar_edit, "_backup", lambda path: None)
    out = calendar_edit.calendar_edit_tool(
        None, action="remove_commit", targets=[{"id": "期末考试周-2027-01-18"}])
    assert "备份写不进去" in out and "没动" in out
    data, _ = calendar_edit.load_events_file()
    assert len(data["events"]) == 1         # 一个字都没动


def test_remove_commit_dedupes_repeated_selectors():
    _seed([_e()])
    out = calendar_edit.calendar_edit_tool(
        None, action="remove_commit",
        targets=[{"id": "期末考试周-2027-01-18"},
                 {"name": "期末考试周", "start": "2027-01-18"}])
    assert "已删除 1 条" in out
    data, _ = calendar_edit.load_events_file()
    assert data["events"] == []


def test_revise_preview_shows_old_and_new():
    _seed([_e()])
    out = calendar_edit.calendar_edit_tool(
        None, action="revise_preview",
        targets=[{"id": "期末考试周-2027-01-18"}],
        patch={"start": "2027-01-20", "end": "2027-01-24",
               "quote": "期末考试调整至1月20日", "reason": "教务处发了调整通知"})
    assert "当前这一条(改前)" in out and "2027-01-18" in out
    assert "修订后" in out and "2027-01-20" in out
    assert "确认修订" in out


def test_revise_commit_updates_and_records_trail():
    _seed([_e(), _e(name="校运会", start="2026-11-20")])
    out = calendar_edit.calendar_edit_tool(
        None, action="revise_commit",
        targets=[{"id": "期末考试周-2027-01-18"}],
        patch={"start": "2027-01-20", "reason": "教务处调整"})
    assert "已修订" in out
    data, _ = calendar_edit.load_events_file()
    e = data["events"][0]
    assert e["start"] == "2027-01-20"
    assert "修订" in e["note"] and "start" in e["note"] and "教务处调整" in e["note"]
    assert e["name"] == "期末考试周"        # 没动的字段原样
    assert len(data["events"]) == 2         # 一次一条,别的不碰


def test_revise_requires_reason():
    _seed([_e()])
    out = calendar_edit.calendar_edit_tool(
        None, action="revise_commit",
        targets=[{"id": "期末考试周-2027-01-18"}],
        patch={"start": "2027-01-20"})
    assert "reason" in out
    data, _ = calendar_edit.load_events_file()
    assert data["events"][0]["start"] == "2027-01-18"


def test_revise_rejects_silent_corruption():
    """修订把好数据改坏是真事故:kind 写错 / 日期倒挂 / 撞名同日,全都拒绝。"""
    _seed([_e(), _e(name="校运会", start="2026-11-20")])
    tid = {"id": "期末考试周-2027-01-18"}
    for patch, needle in (
            ({"kind": "放假", "reason": "r"}, "kind"),
            ({"end": "2027-01-10", "reason": "r"}, "早于"),
            ({"name": "校运会", "start": "2026-11-20", "reason": "r"}, "撞名同日"),
            ({"start": "不是日期", "reason": "r"}, "ISO"),
            ({"格子": "1", "reason": "r"}, "改不了的字段")):
        out = calendar_edit.calendar_edit_tool(
            None, action="revise_commit", targets=[tid], patch=patch)
        assert needle in out, (patch, out)
    data, _ = calendar_edit.load_events_file()
    assert data["events"][0]["start"] == "2027-01-18"   # 全部被拒,文件原样


def test_revise_multi_match_requires_single_id():
    _seed([_e(), _e(name="期末考试周", start="2027-06-20")])
    out = calendar_edit.calendar_edit_tool(
        None, action="revise_commit",
        targets=[{"name": "期末考试周"}],       # 名字匹配两条,没给日期
        patch={"start": "2027-01-20", "reason": "r"})
    assert "一次只改一条" in out


def test_revise_source_change_rederives_credibility():
    _seed([_e()])
    out = calendar_edit.calendar_edit_tool(
        None, action="revise_commit",
        targets=[{"id": "期末考试周-2027-01-18"}],
        patch={"source": "https://forum.example.com/t/123", "reason": "换成新链接"})
    assert "已修订" in out
    data, _ = calendar_edit.load_events_file()
    assert data["events"][0]["credibility"] == "B-抓取(未核实)"   # 非白名单域名,跟来源走


def test_backup_same_second_writes_distinct_files():
    """同一秒内两次改写 = 两个独立回滚点,不许互相覆盖;总量守住 KEEP。"""
    _seed([_e()])
    path = paths.APP_DIR / CALENDAR_FILE
    # 每次备份前源文件都在变:剩下的 KEEP 份必须各对应一个"改写前状态"
    for n in range(5):
        data = json.loads(path.read_text(encoding="utf-8"))
        data["events"].append(_e(name=f"活动{n}", start=f"2026-12-0{n + 1}"))
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        calendar_edit._backup(path)
    bdir = paths.APP_DIR / "kb" / "history"
    kept = sorted(bdir.glob("school-calendar-*.json"))
    assert len(kept) == calendar_edit.BACKUP_KEEP
    sizes = [len(json.loads(p.read_text(encoding="utf-8"))["events"])
             for p in kept]
    assert len(set(sizes)) == calendar_edit.BACKUP_KEEP, \
        f"剩下的备份内容各不相同才是真回滚点(实测 {sizes})"
