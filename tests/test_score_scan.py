"""成绩导入「翻对页、认对表头」的针(v0.9.113 那一刀)。

用户报的是第三次撞同一件事:登录好了、成绩表明明在屏幕上(图 p4),点「读取成绩」
却回「这一页没有认出成绩表 … (现在这张页是 …/framework/xsMain.jsp)」(图 p3)。

根因**不在表头关键字**,而在取材拿错了页 —— 旧的 `page()` 是"第一帧有表就交差",
而正方登录后那张框架页 `xsMain.jsp` 自己就带一堆布局表(菜单/头部),真表在它套的
子帧里、或干脆在另一张标签里。所以这里钉两层:
① 逐张候选页 × 逐帧都翻(`page_targets` + `scans`),挑认得出的那一张;
② 认列本身要扛得住真教务的三种形状:表头与表体是两张表、表头跨两行、
   「成绩标识/成绩说明」这种带"成绩"二字却不是分数的列。
外加失败时要把"翻到的表头长这样"如实说出来 —— 下一轮不该再靠猜。
"""
import json
from pathlib import Path

import pytest

from minicode import paths, scores
from minicode.scores_import import ScoresFlow

ROOT = Path(__file__).resolve().parent.parent

GRID = {"id": "DataGrid1", "rows": [
    ["序号", "课程名称", "课程性质", "学分", "成绩", "成绩标识", "绩点", "开课学期"],
    ["1", "高等数学", "必修", "4", "89", "正考", "3.9", "2024-2025-1"],
    ["2", "大学英语", "必修", "3", "76", "正考", "2.6", "2024-2025-1"],
    ["3", "体育", "必修", "1", "优", "", "", "2024-2025-1"],
]}
LAYOUT = {"id": "menu", "rows": [["学号", "姓名", "班级"], ["2024001", "张三", "计科1"]]}


def _page(*frames):
    """一页 = 一个 target,带若干帧;每帧 (url, 那一帧的整页 dump)。"""
    url, dump = frames[0]
    return {"url": url, "frames": [{"url": u, "dump": d} for u, d in frames]}


class FrameBrowser:
    """两张页 × 每页若干帧的假浏览器(只按 URL 接管,绝不导航)。"""

    MAX_WAIT_MS = 1
    FRAME_MAX_CHARS = 200_000
    BrowserUnavailable = type("BrowserUnavailable", (Exception,), {})

    def __init__(self, pages):
        self.pages = pages
        self.current = pages[0]
        self.hints = []
        self.adopted = []

    def ensure_visible(self):
        return True

    def open_page(self, url, *a, **k):
        return {"url": url, "title": "教务"}

    def page_targets(self, url_like="", **k):
        return [{"url": p["url"], "title": ""}
                for p in self.pages if url_like in p["url"]]

    def adopt_page(self, url_like="", **k):
        self.hints.append(url_like)
        hit = next((p for p in self.pages if url_like in p["url"]), None)
        if hit is None:
            return None
        self.current = hit
        self.adopted.append(hit["url"])
        return {"url": hit["url"], "title": ""}

    def current_info(self):
        return {"url": self.current["url"]}

    def read_frames(self, script, url_like="", **k):
        import minicode.site_parse as sp
        out = []
        for fr in self.current["frames"]:
            if script is sp.TABLE_JS:
                out.append({"url": fr["url"], "value": json.dumps(fr["dump"], ensure_ascii=False),
                            "truncated": False})
            elif script is sp.TEXT_JS:
                out.append({"url": fr["url"], "value": "高等数学 89 4", "truncated": False})
            else:
                out.append({"url": fr["url"], "value": None, "truncated": False})
        return out

    def evaluate(self, *a, **k):
        return self.current["url"]


FW_URL = "https://jw.educationgroup.cn/gzasc_jsxsd/framework/xsMain.jsp"
CJ_URL = "https://jw.educationgroup.cn/gzasc_jsxsd/cjcx/cjkb_cjb1.do"


@pytest.fixture(autouse=True)
def _app_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "APP_DIR", tmp_path / "app")


def _flow():
    fw = _page((FW_URL, {"tables": [LAYOUT]}), (FW_URL + "#top", {"tables": [LAYOUT]}))
    cj = _page((CJ_URL, {"tables": [LAYOUT]}),
               (CJ_URL + "#grid", {"tables": [GRID]}))
    return ScoresFlow(browser_mod=FrameBrowser([fw, cj]))


# ---------------------------------------------------------------- ① 翻对页

def test_the_grid_is_found_in_the_frame_that_actually_has_it():
    """框架页先出现、真表在它套的帧里 —— 旧写法在第一帧的菜单表上就交了差。"""
    got = _flow().read()
    assert got["ok"] is True, got["msg"]
    assert [r["name"] for r in got["rows"]] == ["高等数学", "大学英语", "体育"]
    assert got["scanned"]["frames"] >= 3, "每一页的每个帧都该翻过"


def test_it_walks_into_the_other_tab_too():
    """真表在**另一张标签**里时也要够得着(逐张候选页,而不是只读接管到的第一张)。"""
    fw = _page((FW_URL, {"tables": [LAYOUT]}))
    cj = _page((CJ_URL, {"tables": [LAYOUT]}), (CJ_URL + "#g", {"tables": [GRID]}))
    flow = ScoresFlow(browser_mod=FrameBrowser([fw, cj]))
    got = flow.read()
    assert got["ok"] is True, got["msg"]
    assert CJ_URL in flow.browser.adopted, "没换过标签就只可能撞上框架页"


def test_the_picked_table_is_the_one_with_the_most_rows():
    """两张页都长得像成绩表时取条数多的那条 —— 少的那张多半是"本学期已修"摘要。"""
    small = {"rows": [["课程名称", "成绩"], ["重修提示", "0"]]}
    fw = _page((FW_URL, {"tables": [small]}))
    cj = _page((CJ_URL, {"tables": [LAYOUT], "selects": {}}), (CJ_URL + "#g", {"tables": [GRID]}))
    got = ScoresFlow(browser_mod=FrameBrowser([fw, cj])).read()
    assert got["ok"] and len(got["rows"]) == 3


# ---------------------------------------------------------------- ② 认对表头

def test_the_score_column_is_not_the_flag_column():
    """「成绩标识」里写的是"正考"这种字,不是分数 —— 认列要挑最具体那一列。"""
    r = scores.parse_score_table({"tables": [GRID]})
    assert r["ok"] and r["rows"][0]["score"] == "89"
    assert r["rows"][2]["score"] == "优"
    assert all("正考" not in row["score"] for row in r["rows"])


def test_credit_and_semester_come_from_their_own_columns():
    r = scores.parse_score_table({"tables": [GRID]})
    assert r["rows"][0]["credit"] == "4"
    assert r["rows"][0]["semester"] == "2024-2025-1"


def test_header_and_body_split_across_two_tables():
    """冻结表头的网格把表头和表体渲染成**两张表**:配对要认得出。"""
    head = {"id": "grid_head", "rows": [["序号", "课程名称", "学分", "成绩"]]}
    body = {"id": "grid_body", "rows": [["1", "数据结构", "3.5", "91"],
                                        ["2", "毛概", "2", "82"]]}
    r = scores.parse_score_table({"tables": [LAYOUT, head, body]})
    assert r["ok"] is True, r["msg"]
    assert [x["name"] for x in r["rows"]] == ["数据结构", "毛概"]
    assert r["rows"][0]["credit"] == "3.5"


def test_two_row_merged_header():
    """表头跨两行:上下两半各自都不齐("课程"在上、"名称"在下)—— 合并才认得出。"""
    page = {"tables": [{"rows": [
        ["序号", "课程", "学分", "考核"],
        ["", "名称", "", "成绩"],
        ["1", "编译原理", "3", "85"],
    ]}]}
    r = scores.parse_score_table(page)
    assert r["ok"] is True, r["msg"]
    assert r["rows"][0]["name"] == "编译原理"
    assert r["rows"][0]["score"] == "85"


def test_a_split_header_second_row_is_not_stored_as_a_course():
    """表头单行就认得出时,它下面那半行(「名称 / 总评」)不能混进课程里。"""
    page = {"tables": [{"rows": [
        ["序号", "课程", "学分", "成绩"],
        ["", "名称", "", "总评"],
        ["1", "编译原理", "3", "85"],
    ]}]}
    r = scores.parse_score_table(page)
    assert r["ok"] and [x["name"] for x in r["rows"]] == ["编译原理"]



def test_a_repeated_header_row_is_not_stored_as_a_course():
    """表体里又贴了一遍表头(冻结列的表常见)—— 那行不能当一门课存下去。"""
    page = {"tables": [{"rows": [
        ["课程名称", "成绩"],
        ["课程名称", "成绩"],          # 表体开头重复的表头
        ["离散数学", "78"],
    ]}]}
    r = scores.parse_score_table(page)
    assert r["ok"] and [x["name"] for x in r["rows"]] == ["离散数学"]


def test_semester_filled_from_the_page_filter():
    """没有学期列时用页面筛学期的下拉框补 —— 值就写在页上,不是猜。"""
    page = {"tables": [{"rows": [["课程名称", "成绩", "学分"], ["概率论", "88", "3"]]}],
            "selects": {"xnxq01id": "2023-2024-2"}}
    r = scores.parse_score_table(page)
    assert r["ok"] and r["rows"][0]["semester"] == "2023-2024-2"


def test_a_junk_select_value_does_not_become_a_semester():
    page = {"tables": [{"rows": [["课程名称", "成绩"], ["概率论", "88"]]}],
            "selects": {"type": "全部"}}
    r = scores.parse_score_table(page)
    assert r["ok"] and r["rows"][0]["semester"] == ""


# ---------------------------------------------------------------- 失败要看得见

def test_failure_says_which_headers_it_saw():
    """下一轮不该再猜:把翻到的表头原样报回去,并说清翻了几张页几个帧。"""
    noise = {"id": "n", "rows": [["选课序号", "教学活动", "上课时间"], ["1", "A", "周一"]]}
    fw = _page((FW_URL, {"tables": [noise]}))
    got = ScoresFlow(browser_mod=FrameBrowser([fw])).read()
    assert got["ok"] is False and got["stage"] == "no_grid"
    assert "教学活动" in got["msg"], got["msg"]
    assert "帧" in got["msg"] and "xsMain.jsp" in got["msg"]


def test_no_table_at_all_is_a_different_sentence():
    empty = {"tables": []}
    fw = _page((FW_URL, empty))
    got = ScoresFlow(browser_mod=FrameBrowser([fw])).read()
    assert got["ok"] is False
    assert "一个表格都没捞到" in got["msg"], got["msg"]


# ---------------------------------------------------------------- 没有新能力时不许变窄

def test_flow_still_works_without_page_targets():
    """假浏览器没有 page_targets(v0.9.112 那批测试的形状)→ 退回"就用接管到的那张"。"""
    from tests.test_import_anchor import DUMP, PAGE, RecBrowser

    fb = RecBrowser(adopt_hits={"educationgroup.cn": PAGE}, dump=DUMP)
    got = ScoresFlow(browser_mod=fb).read()
    assert got["ok"] is True and got["summary"]["entries"] == 2


# ---------------------------------------------------------------- 面板那四个数

def test_stats_counts_from_the_archive_only():
    scores.save([
        {"name": "高等数学", "score": "89", "credit": "4", "semester": "2024-2025-1"},
        {"name": "大学英语", "score": "76", "credit": "3", "semester": "2024-2025-1"},
        {"name": "体育", "score": "优", "credit": "1", "semester": "2023-2024-2"},
    ])
    s = scores.stats()
    assert s["ok"] and s["entries"] == 3 and s["courses"] == 3
    assert s["credits"] == 8
    assert s["avg"] == pytest.approx((89 + 76 + 95) / 3, 0.01)
    assert s["weighted"] == pytest.approx((89 * 4 + 76 * 3 + 95 * 1) / 8, 0.01)
    assert s["semesters"] == ["2023-2024-2", "2024-2025-1"]
    assert "以学校教务处" in s["note"] or "校教务处" in s["note"]


def test_stats_narrows_by_semester():
    scores.save([
        {"name": "高数", "score": "89", "credit": "4", "semester": "2024-2025-1"},
        {"name": "英语", "score": "76", "credit": "3", "semester": "2023-2024-2"},
    ])
    s = scores.stats("2023-2024-2")
    assert s["entries"] == 1 and s["credits"] == 3 and s["semester"] == "2023-2024-2"


def test_stats_gpa_matches_the_gpa_tool_exactly():
    """面板与 gpa_calc 必须同一个数(两套绩点比没有绩点更坏)。"""
    from minicode import gpa

    scores.save([{"name": "高数", "score": "89", "credit": "4"},
                 {"name": "英语", "score": "76", "credit": "3"}])
    s = scores.stats()
    assert s["gpa5"] == round((gpa._gpa5(89) * 4 + gpa._gpa5(76) * 3) / 7, 2)
    assert s["gpa4"] == round((gpa._gpa4(89) * 4 + gpa._gpa4(76) * 3) / 7, 2)


def test_stats_reports_rows_it_could_not_use():
    scores.save([{"name": "高数", "score": "89", "credit": "4"},
                 {"name": "讲座", "score": "通过", "credit": ""}])
    s = scores.stats()
    assert s["entries"] == 2 and s["skipped"] == 1
    assert s["weighted"] == 89          # 缺学分那行不参与加权,但条数照实
    assert s["credits"] == 4


def test_stats_on_an_empty_machine_points_at_the_import():
    s = scores.stats()
    assert s["ok"] is False and s["empty"] is True and s["entries"] == 0
    assert "从教务导入成绩" in s["note"]


# ---------------------------------------------------------------- 认不出表头不是死路

def test_a_page_we_cannot_read_offers_to_show_it_to_the_model():
    """「页在、表在、学长认不出列名」这一种失败,界面必须给下一步而不只是红字。

    同学看得见那张表,学长看不见规律 —— 最有用的下一步是把这一页**摊进对话**
    让模型用 page_parse 读结构(只读:不导航、不提交表单、不替同学存任何东西)。
    """
    fe = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    assert 'stage === "no_grid" || stage === "not_timetable"' in fe
    assert "让学长看这一页的表格结构" in fe
    assert "function jwPeekPage()" in fe
    assert 'pk.addEventListener("click", jwPeekPage)' in fe
    assert "page_parse" in fe and "先别存任何东西" in fe
    # 「再读一次」与「重新打开登录页」两种失败给的话不同,这一条不能互相吃掉
    assert 'lost ? "① 重新打开登录页" : "再读一次"' in fe
