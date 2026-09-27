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


# ------------------------------------------------------------ 候选审核(v0.9.21)

def test_list_candidates_order_and_filter():
    growth.save_candidate("热水几点停", answer="22:30", source_url="https://x.cn/a")
    growth.save_candidate("校园卡挂失", answer="服务大厅", source_url="https://x.cn/b")
    allc = growth.list_candidates()
    assert [c["index"] for c in allc] == [2, 1]              # 新的在前
    assert len(growth.list_candidates("pending")) == 2
    assert growth.list_candidates("merged") == []


def test_review_merge_writes_kb_discoverable(monkeypatch, tmp_path):
    from minicode import kb
    growth.save_candidate("图书馆几点闭馆", answer="22:00 闭馆",
                          source_url="https://x.cn/lib", source_title="图书馆公告",
                          note="期末延长到 23:00")
    ok, msg = growth.review_candidate(1, "merge")
    assert ok and "并入知识库" in msg

    # 候选状态改成 merged,不再出现在 pending
    assert growth.list_candidates("pending") == []
    merged = growth.list_candidates("merged")
    assert len(merged) == 1 and merged[0]["reviewed_at"]

    # 并入写进了 app_path('kb')/reviewed.json,且能被 kb.load_kb 扫到
    reviewed = app_path("kb") / growth.REVIEWED_KB_FILE
    assert reviewed.exists()
    entries = kb.load_kb([app_path("kb")])
    hit = [e for e in entries if e.title == "图书馆几点闭馆"]
    assert len(hit) == 1
    assert hit[0].credible is True                           # credibility A-已审核
    assert "22:00" in hit[0].content and "期末延长" in hit[0].content
    assert hit[0].url == "https://x.cn/lib"


def test_review_merge_is_idempotent_on_remerge():
    growth.save_candidate("问题A", answer="答案A", source_url="https://x.cn/a")
    growth.review_candidate(1, "merge")
    # 已处理:不重复动作
    ok, msg = growth.review_candidate(1, "merge")
    assert ok is False and "已经是 merged" in msg


def test_review_reject_marks_status():
    growth.save_candidate("低质量候选", answer="x", source_url="https://x.cn/z")
    ok, msg = growth.review_candidate(1, "reject")
    assert ok and "拒绝" in msg
    assert growth.list_candidates("rejected")[0]["index"] == 1
    assert growth.list_candidates("pending") == []


def test_review_bad_index_and_action():
    growth.save_candidate("问题", answer="答案", source_url="https://x.cn/a")
    assert growth.review_candidate(999, "merge")[0] is False   # 不存在的编号
    assert growth.review_candidate("abc", "merge")[0] is False # 非数字
    assert growth.review_candidate(1, "explode")[0] is False   # 非法 action
    # 上述失败都没改动状态
    assert len(growth.list_candidates("pending")) == 1


def test_review_survives_broken_line(monkeypatch):
    """候选文件里混进坏行也能正常读取与审核(容错)。"""
    path = app_path(growth.CANDIDATES_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"index": 1, "question": "好行", "answer": "a",
                    "source_url": "https://x.cn", "status": "pending"},
                   ensure_ascii=False) + "\n"
        + "这不是 JSON\n",
        encoding="utf-8")
    assert [c["index"] for c in growth.list_candidates()] == [1]
    ok, _ = growth.review_candidate(1, "reject")
    assert ok is True
    # 重写后坏行被丢弃,只剩合法记录
    rows = _read_lines(growth.CANDIDATES_FILE)
    assert len(rows) == 1 and rows[0]["status"] == "rejected"


def test_log_kb_gap_dedups_across_processes(tmp_path, monkeypatch):
    """跨进程当日去重:e2e 每轮都是新进程,_SEEN_GAPS 会清空 —— 但账本里
    今天已有同一词就不该再记(一场评测跑 20 遍不是 20 个同学在问)。"""
    import time as _time
    q = "社团招新"
    assert growth.log_kb_gap(q) is True
    # 模拟"重启":清空进程内集合,账本文件还在
    growth._SEEN_GAPS.clear()
    growth._seen_cache_key = ""          # 缓存也失效,强制重读账本
    assert growth.log_kb_gap(q) is False          # 账本里今天有了 → 不再记
    assert growth.log_kb_gap("社团 招新!") is False   # 归一化后同词同理
    assert growth.log_kb_gap("另一个问题") is True   # 新词照记
    # 昨天的行不算:把文件里那行改成昨天,同词应该能再记
    path = app_path(growth.GAPS_FILE)
    rows = path.read_text(encoding="utf-8").splitlines()
    old_day = _time.strftime("%Y-%m-%d", _time.localtime(_time.time() - 86400))
    rows = [r.replace(_time.strftime("%Y-%m-%d"), old_day) if q in r else r
            for r in rows]
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    growth._seen_cache_key = ""
    growth._SEEN_GAPS.clear()
    assert growth.log_kb_gap(q) is True


# ---------------------------------------------------------------- 缺口周报聚合(v0.9.57 A4)

def _write_gap_lines(rows):
    path = app_path(growth.GAPS_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def test_gap_digest_groups_by_normalized_query_and_counts_days():
    """同一问法的变体归一条;days 数**不同日期**,同日重复不虚报。"""
    import time as _time
    fresh = _time.strftime("%Y-%m-%d %H:%M:%S")
    yesterday = _time.strftime(
        "%Y-%m-%d %H:%M:%S", _time.localtime(_time.time() - 86400))
    _write_gap_lines([
        json.dumps({"ts": fresh, "query": "学校快递站在哪", "reason": "no-hit"}),
        json.dumps({"ts": fresh, "query": "学校 快递站 在哪!", "reason": "no-hit"}),
        json.dumps({"ts": fresh, "query": "学校快递站在哪", "reason": "no-hit"}),
        json.dumps({"ts": yesterday, "query": "学校快递站在哪", "reason": "no-hit"}),
    ])
    rows = growth.gap_digest(days=7)
    assert rows is not None and len(rows) == 1        # 变体归组,不数行数
    assert rows[0]["days"] == 2                       # 两个不同日期;同日三条只算一天
    assert rows[0]["query"] == "学校快递站在哪"         # 展示最早那条原文


def test_gap_digest_ranks_tops_and_keeps_the_first_wording():
    import time as _time
    fresh = _time.strftime("%Y-%m-%d %H:%M:%S")
    _write_gap_lines([
        json.dumps({"ts": fresh, "query": "放假安排", "reason": "no-hit"}),
        json.dumps({"ts": fresh, "query": "四六级报名时间", "reason": "no-hit"}),
    ])
    rows = growth.gap_digest(days=7, top=1)
    assert [r["query"] for r in rows] == ["放假安排"]   # 同为 1 天取先登的,不瞎排序


def test_gap_digest_ignores_stale_and_broken_lines():
    """窗口外的旧账与半截行都不进周报;坏行跳过,不把整份聚合弄崩。"""
    _write_gap_lines([
        json.dumps({"ts": "2000-01-01 08:00:00", "query": "三年前的问题", "reason": "no-hit"}),
        "{半截的 json",
        json.dumps({"ts": "", "query": "", "reason": "no-hit"}),
    ])
    assert growth.gap_digest(days=7) is None


def test_gap_digest_returns_none_without_a_file():
    assert growth.gap_digest(days=7) is None

