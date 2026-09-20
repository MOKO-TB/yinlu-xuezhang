"""知识库成长闭环测试:未命中登记 / 候选 / 消息反馈的落盘与去重。"""
import json

import pytest

from minicode import growth
from minicode.paths import app_path
from minicode.tools.base import ToolContext


@pytest.fixture(autouse=True)
def _isolated_app_dir(tmp_path, monkeypatch):
    """growth 的三个日志都落在 APP_DIR:测试里钉到临时目录。"""
    from minicode import paths
    monkeypatch.setattr(paths, "APP_DIR", tmp_path / "app")
    growth._SEEN_GAPS.clear()
    yield
    growth._SEEN_GAPS.clear()


def _read_lines(name):
    path = app_path(name)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def test_log_kb_gap_writes_and_dedups_same_day():
    assert growth.log_kb_gap("快递站在哪") is True
    assert growth.log_kb_gap("快递站在哪") is False          # 当日去重
    assert growth.log_kb_gap("  快递站 在哪!  ") is False    # 归一化后同词也算重复
    assert growth.log_kb_gap("空调什么时候开") is True
    rows = _read_lines(growth.GAPS_FILE)
    assert [r["query"] for r in rows] == ["快递站在哪", "空调什么时候开"]
    assert all(r["reason"] == "no-hit" for r in rows)
    assert all("ts" in r for r in rows)


def test_log_kb_gap_rejects_empty():
    assert growth.log_kb_gap("   ") is False
    assert growth.log_kb_gap("") is False
    assert _read_lines(growth.GAPS_FILE) == []


def test_save_candidate_appends_with_index():
    first = growth.save_candidate("热水什么时候停", answer="22:30",
                                  source_url="https://gzasc.edu.cn/a")
    second = growth.save_candidate("校园卡怎么挂失", answer="去服务大厅",
                                   source_url="https://gzasc.edu.cn/b",
                                   source_title="挂失指南", note="肇庆校区")
    assert first["ok"] and first["index"] == 1
    assert second["ok"] and second["index"] == 2
    rows = _read_lines(growth.CANDIDATES_FILE)
    assert [r["status"] for r in rows] == ["pending", "pending"]
    assert rows[1]["source_title"] == "挂失指南"


def test_save_candidate_validations():
    assert growth.save_candidate("", "答案", source_url="https://x.cn")["ok"] is False
    assert growth.save_candidate("问题", "", source_url="https://x.cn")["ok"] is False
    bad = growth.save_candidate("问题", "答案", source_url="ftp://or-none")
    assert bad["ok"] is False and "来源网址" in bad["msg"]
    assert _read_lines(growth.CANDIDATES_FILE) == []


def test_log_feedback_rating_values():
    assert growth.log_feedback(1, question="问题", answer="答案", session="s1") is True
    assert growth.log_feedback(-1, question="问题", answer="答案") is True
    assert growth.log_feedback(0) is False
    assert growth.log_feedback(5) is True                    # 归一成 1
    rows = _read_lines(growth.FEEDBACK_FILE)
    assert [r["rating"] for r in rows] == [1, -1, 1]


def test_kb_suggest_handler_success_and_refusal(ctx):
    out = growth.kb_suggest(ctx, "热水几点停", answer="22:30",
                            source_url="https://gzasc.edu.cn/hot-water")
    assert "第 1 条" in out and "还没生效" in out and "候选" in out
    bad = growth.kb_suggest(ctx, "没有来源", answer="x", source_url="不是网址")
    assert bad.startswith("错误:")
    assert len(_read_lines(growth.CANDIDATES_FILE)) == 1


def test_search_knowledge_miss_logs_gap(tmp_path, config, monkeypatch):
    """search_knowledge 未命中时自动登记;命中时不登记。"""
    from minicode import kb
    monkeypatch.setattr(kb, "kb_dirs", lambda workspace: [tmp_path / "kb"])
    kb_dir = tmp_path / "kb"
    kb_dir.mkdir()
    (kb_dir / "knowledge.json").write_text(json.dumps([{
        "id": "n1", "title": "奖学金评定", "content": "国家奖学金评定条件",
        "credibility": "A", "url": "https://gzasc.edu.cn/scholarship",
    }], ensure_ascii=False), encoding="utf-8")
    target = ToolContext(workspace=tmp_path, config=config)

    assert "没有匹配" in kb.search_knowledge(target, "量子力学期末范围")
    rows = _read_lines(growth.GAPS_FILE)
    assert len(rows) == 1 and rows[0]["query"] == "量子力学期末范围"

    assert "奖学金" in kb.search_knowledge(target, "奖学金")
    assert len(_read_lines(growth.GAPS_FILE)) == 1           # 命中不再新增
