"""library_guide 工具:图书馆导览模块的单元测试(用临时数据包,不依赖真实数据)。"""
import json
from pathlib import Path

from minicode import library_guide
from minicode.tools.base import ToolContext

FACTS = {
    "meta": {"catalog_fetched_at": "2026-09-13"},
    "catalog": {"total_records": 1331774, "paper": 350619, "ebook": 981155,
                "item_records": 75412, "class_numbers": 97593},
    "live_snapshot": {"as_of": "2026-09-13", "on_shelf": 75041, "borrowed": 353,
                      "overdue": 48, "lost": 18},
    "class_distribution": [
        {"code": "I", "name": "文学", "total": 217231, "paper": 75238, "ebook": 141993},
        {"code": "T", "name": "工业技术", "total": 174193, "paper": 43764, "ebook": 130429},
    ],
    "class_floor_map": [
        {"code": "I", "name": "文学",
         "floors": [["二楼", 9394], ["三楼", 3353]],
         "locations": [{"name": "广应科图书馆-201 文学新书区", "items": 9378}]},
        {"code": "T", "name": "工业技术",
         "floors": [["八楼", 8561]],
         "locations": [{"name": "广应科图书馆-805 计算机新书区", "items": 3361}]},
    ],
    "spaces": [{"name": "图书馆主楼", "fact": "共九层。", "source": "学校官方微信号"}],
    "official_channels": [{"name": "师生图书服务平台",
                           "url": "https://findgzyykj.libsp.cn/#/Home", "use": "查书"}],
    "unverified": [{"item": "开馆/闭馆时间", "why": "无官方来源", "how": "补 open_hours"}],
}


def _ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(workspace=tmp_path, config=None)


def _isolate(monkeypatch, tmp_path: Path) -> None:
    """把 app/resource 回退路径指到空目录:否则开发态项目根下真实存在的数据包
    会让「数据包不存在」的用例误命中。"""
    empty = tmp_path / "no-app-data"
    empty.mkdir(exist_ok=True)
    monkeypatch.setattr(library_guide, "app_path", lambda *a: empty.joinpath(*a))
    monkeypatch.setattr(library_guide, "resource_path", lambda *a: empty.joinpath(*a))


def _make_facts(tmp_path: Path) -> Path:
    d = tmp_path / "skills" / "library-guide" / "data"
    d.mkdir(parents=True)
    p = d / "library-facts.json"
    p.write_text(json.dumps(FACTS, ensure_ascii=False), encoding="utf-8")
    return p


def test_facts_path_found_under_workspace(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    p = _make_facts(tmp_path)
    assert library_guide.facts_path(tmp_path) == p


def test_facts_path_none_when_missing(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    assert library_guide.facts_path(tmp_path) is None


def test_guide_reports_missing_pack(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    assert "不存在" in library_guide.guide(_ctx(tmp_path))


def test_floor_by_class_letter(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    _make_facts(tmp_path)
    out = library_guide.guide(_ctx(tmp_path), topic="floor", query="I")
    assert "二楼" in out and "201" in out


def test_floor_by_class_name(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    _make_facts(tmp_path)
    assert "二楼" in library_guide.guide(_ctx(tmp_path), topic="floor", query="文学")


def test_floor_by_call_no(tmp_path, monkeypatch):
    """索书号 I247.5/868 取首字母 I。"""
    _isolate(monkeypatch, tmp_path)
    _make_facts(tmp_path)
    out = library_guide.guide(_ctx(tmp_path), topic="floor", query="I247.5/868")
    assert "二楼" in out


def test_floor_unknown_lists_classes(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    _make_facts(tmp_path)
    out = library_guide.guide(_ctx(tmp_path), topic="floor", query="qqq")
    assert "文学" in out and "工业技术" in out


def test_flow_has_official_entry_and_no_hard_numbers(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    _make_facts(tmp_path)
    out = library_guide.guide(_ctx(tmp_path), topic="flow")
    assert "师生图书服务平台" in out
    assert "以图书馆系统" in out


def test_space_carries_source(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    _make_facts(tmp_path)
    assert "学校官方微信号" in library_guide.guide(_ctx(tmp_path), topic="space")


def test_stats_reports_real_counts(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    _make_facts(tmp_path)
    out = library_guide.guide(_ctx(tmp_path), topic="stats")
    assert "1,331,774" in out and "75,041" in out


def test_unverified_lists_gaps(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    _make_facts(tmp_path)
    out = library_guide.guide(_ctx(tmp_path), topic="unverified")
    assert "开馆" in out and "不得编造" in out


def test_unknown_topic_falls_back_to_overview(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    _make_facts(tmp_path)
    out = library_guide.guide(_ctx(tmp_path), topic="nonsense")
    assert "图书馆导览" in out and "中图法大类→楼层" in out


def test_prompt_section_silent_without_pack(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    assert library_guide.library_guide_prompt_section(tmp_path) == ""


def test_prompt_section_mentions_tool(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    _make_facts(tmp_path)
    sec = library_guide.library_guide_prompt_section(tmp_path)
    assert "library_guide" in sec and "1,331,774" in sec
    # 包内条数是抓取快照,必须声明以 library_search 实时结果为准
    assert "快照" in sec and "以 library_search 的实时结果为准" in sec


def test_tool_registered_and_read_only():
    from minicode.tools.registry import build_default_registry
    tools = {t.name: t for t in build_default_registry()}
    assert "library_guide" in tools
    assert tools["library_guide"].read_only
