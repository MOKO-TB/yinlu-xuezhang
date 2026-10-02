"""成绩存档与导入测试(v0.9.59 H 件):表头认列、存档诚实、流程不导航、工具口径。

钉四件事:①解析只在**表头**上做文章 —— 认不出列名就是认不出,绝不把第 3 列
硬当成绩;②存档只有用户确认后才落盘,整表覆盖要说清;③ScoresFlow 读的是
**当前**那张页,绝不导航不提交表单(选学期是同学的事);④工具三动作的口径:
view/gpa 都从同一份存档来,clear 要过确认框(非只读)。
"""
import json

import pytest

from minicode import paths, scores
from minicode.schedule_import import ImportFlow
from minicode.scores_import import ScoresFlow


@pytest.fixture(autouse=True)
def _app_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "APP_DIR", tmp_path / "app")


DUMP = {"tables": [{"id": "DataGrid1", "rows": [
    ["课程", "成绩", "学分", "学年"],
    ["高等数学", "89", "4", "2025-2026-1"],
    ["大学物理", "76", "3", "2025-2026-1"],
    ["大学体育", "优", "1", "2025-2026-1"],
    ["选修坏行", "", "2", ""],                       # 没成绩:跳过,不硬凑
    ["平均分", "85", "", ""],                        # 表尾统计行:跳过
]}]}


# ---------------------------------------------------------------- 解析

def test_parse_score_table_recognizes_by_header_and_skips_junk():
    r = scores.parse_score_table(DUMP)
    assert r["ok"] is True and len(r["rows"]) == 3
    assert r["rows"][0] == {"name": "高等数学", "score": "89",
                            "credit": "4", "semester": "2025-2026-1"}
    assert r["rows"][2]["score"] == "优"              # 等级照原文收,折算归 gpa
    assert all("平均分" not in row["name"] for row in r["rows"])


def test_parse_score_table_refuses_what_is_not_a_score_table():
    no_header = {"tables": [{"rows": [["A", "B"], ["1", "2"]]}]}
    assert scores.parse_score_table(no_header)["ok"] is False
    empty = {"tables": []}
    assert scores.parse_score_table(empty)["ok"] is False
    bare = [["课程", "成绩"], ["高数", "89"]]          # 裸 rows 也要吃
    assert scores.parse_score_table(bare)["ok"] is True


def test_parse_score_table_without_a_credit_column_still_works():
    """有的学校成绩页没有学分列:课程+成绩就够认,学分留空(绩点会如实跳过)。"""
    page = {"tables": [{"rows": [["课程名称", "总评成绩"],
                                 ["线性代数", "91"]]}]}
    r = scores.parse_score_table(page)
    assert r["ok"] and r["rows"][0]["credit"] == ""
    assert r["summary"]["no_credit"] == ["线性代数"]


# ---------------------------------------------------------------- 存档

def test_save_requires_confirmation_semantics_and_normalizes():
    ok, msg = scores.save([{"name": "高数", "score": "89", "credit": "4"}])
    assert ok and "已导入" in msg and "不上传" in msg
    data = scores.load()
    assert data["rows"][0]["name"] == "高数" and data["source"]
    ok, msg = scores.save([{"name": "  ", "score": ""}])   # 全是无效行:不写文件
    assert ok is False and scores.load()["rows"][0]["name"] == "高数"


def test_clear_and_load_missing():
    assert "本来就没有" in scores.clear()
    scores.save([{"name": "高数", "score": "89", "credit": "4"}])
    assert "已清空" in scores.clear()
    assert scores.load() is None


def test_courses_for_gpa_feeds_the_gpa_tool():
    scores.save([{"name": "高数", "score": "89", "credit": "4"},
                 {"name": "体育", "score": "优", "credit": "1"}])
    courses = scores.courses_for_gpa()
    out = scores.scores_tool(None, action="gpa")
    assert "加权平均分" in out and "高数" in out and "体育" in out
    assert "以学校教务处的绩点算法为准" in out
    assert len(courses) == 2


def test_view_says_what_to_do_when_empty():
    out = scores.scores_tool(None, action="view")
    assert "还没有成绩存档" in out and "从教务导入成绩" in out
    scores.save([{"name": "高数", "score": "89", "credit": "4"}])
    assert "存档里没有的成绩不要编" in scores.scores_tool(None, action="view")


def test_tool_unknown_action_and_clear():
    assert scores.scores_tool(None, action="fabulous").startswith("错误:")
    scores.save([{"name": "高数", "score": "89", "credit": "4"}])
    assert "已清空" in scores.scores_tool(None, action="clear")
    assert scores.load() is None


# ---------------------------------------------------------------- 流程(假浏览器)

class FakeBrowser:
    """与 test_schedule_import 同一套注入法:read_frames 返回预录的 dump。"""

    def __init__(self, dump=None, text=""):
        self.dump = dump
        self.text = text
        self.MAX_WAIT_MS = 1
        self.FRAME_MAX_CHARS = 1000

    def ensure_visible(self):
        return True

    def open_page(self, *a, **k):
        return {"url": a[0] if a else "", "title": "教务"}

    def adopt_page(self, *a, **k):
        return {"url": "x", "title": "教务"}

    def read_frames(self, script, url_like=None):
        import minicode.site_parse as sp
        if script is sp.TABLE_JS and self.dump is not None:
            return [{"value": json.dumps(self.dump, ensure_ascii=False)}]
        if script is sp.TEXT_JS:
            return [{"value": self.text}]
        return []

    def evaluate(self, *a, **k):
        return ""

    class BrowserUnavailable(Exception):
        pass


def test_scores_flow_reads_the_current_page_without_navigating():
    fb = FakeBrowser(dump=DUMP)
    flow = ScoresFlow(browser_mod=fb)
    r = flow.read()
    assert r["ok"] is True and r["stage"] == "ready"
    assert len(r["rows"]) == 3
    assert "不会写任何文件" in r["msg"] and "整表覆盖" in r["msg"]


def test_scores_flow_honest_failure_when_no_table():
    fb = FakeBrowser(dump={"tables": []})
    flow = ScoresFlow(browser_mod=fb)
    r = flow.read()
    assert r["ok"] is False and r["stage"] == "no_grid"
    assert "成绩查询" in r["msg"]


def test_scores_flow_open_login_prompts_for_scores_page():
    fb = FakeBrowser()
    r = ScoresFlow(browser_mod=fb).open_login()
    assert r["ok"] is True
    assert "成绩查询" in r["msg"] and "选好学期" in r["msg"]


def test_scores_flow_model_fallback_parses_text():
    class TextBrowser(FakeBrowser):
        def read_frames(self, script, url_like=None):
            import minicode.site_parse as sp
            if script is sp.TEXT_JS:
                return [{"value": "课程 成绩 学分\n高等数学 89 4"}]
            return []

    def fake_complete(system, user):
        assert "成绩" in system
        return '[{"name":"高等数学","score":"89","credit":"4","semester":""}]'

    r = ScoresFlow(browser_mod=TextBrowser()).read_with_model(fake_complete)
    assert r["ok"] is True and r["rows"][0]["name"] == "高等数学"
    assert any("模型整理" in n for n in r.get("notes") or [])


def test_scores_flow_is_still_an_import_flow():
    """复用而不是另起炉灶:浏览器舞蹈(open_login/_anchor/page)都是 ImportFlow 那一套。"""
    assert issubclass(ScoresFlow, ImportFlow)


# ------------------------------------------- 成绩趋势(W3,v0.9.105)

def _seed_semesters(monkeypatch, rows):
    from minicode import scores as sc
    monkeypatch.setattr(sc, "load", lambda: {"rows": rows, "source": "教务导入",
                                             "updated_at": "2026-10-03 10:00"})


def test_trend_two_semesters_draws_chart(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from minicode import scores as sc
    out_path = []
    monkey_charts = SimpleNamespace(chart_file=lambda ctx, kind, data, **kw:
                                    (out_path.append((kind, data, kw.get("title")))
                                     or f"已画 {kind} → {kw.get('title')}"))
    rows = [
        {"name": "高等数学", "score": "90", "credit": "5", "semester": "2023-2024-1"},
        {"name": "大学英语", "score": "80", "credit": "3", "semester": "2023-2024-1"},
        {"name": "数据结构", "score": "85", "credit": "4", "semester": "2024-2025-1"},
        {"name": "体育", "score": "优", "credit": "1", "semester": "2024-2025-1"},
        {"name": "讲座", "score": "合格", "credit": "", "semester": "2024-2025-1"},
    ]
    _seed_semesters(monkeypatch, rows)
    ctx = SimpleNamespace(workspace=tmp_path)
    import minicode.charts as _charts_mod
    real = _charts_mod.chart_file
    _charts_mod.chart_file = monkey_charts.chart_file
    try:
        out = sc.trend(ctx)
    finally:
        _charts_mod.chart_file = real
    assert "2023-2024-1: 86.25" in out          # (90*5+80*3)/8
    assert "2024-2025-1: 87.0" in out           # (85*4+95*1)/5,讲座无学分跳过
    assert "1 行缺学分或成绩无效已跳过" in out
    assert "以学校教务处绩点算法为准" in out
    assert out_path and out_path[0][0] == "line" and len(out_path[0][1]) == 2


def test_trend_single_semester_honest(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from minicode import scores as sc
    _seed_semesters(monkeypatch, [
        {"name": "高等数学", "score": "90", "credit": "5", "semester": "2023-2024-1"}])
    out = sc.trend(SimpleNamespace(workspace=tmp_path))
    assert "画不出趋势" in out and "90.0" in out


def test_trend_no_archive_points_to_import():
    from minicode import scores as sc
    orig = sc.load
    sc.load = lambda: None
    try:
        out = sc.trend(None)
        assert "还没有成绩存档" in out
    finally:
        sc.load = orig
