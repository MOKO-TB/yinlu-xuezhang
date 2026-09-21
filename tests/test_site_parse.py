"""校园站结构化解析(minicode/site_parse.py):通用课表格 + 版式识别 + page_parse 工具。

这一层的规矩和教务导入向导同一条口径:**认不出的行原样留在 raw / unparsed 里给人看,
绝不猜**。所以本文件一半以上的断言都是在盯"没读出来的地方是不是空着"。
夹具里的姓名、楼名、课程全为虚构。
"""
import json
from pathlib import Path

import pytest

from minicode import browser, paths, schedule, site_parse

FIXTURE = Path(__file__).parent / "fixtures" / "jw_xskb_grid.json"


@pytest.fixture(autouse=True)
def isolated_app_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "APP_DIR", tmp_path / "app")


@pytest.fixture
def grid():
    """强智那张表的实测形状(脱敏夹具),用来验证泛化后老路没走样。"""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# 一张"不是强智"的课表:第一列写时间,列头只有周一到周五,
# 格子里的行序是「课名 / 第N-M节 / 周次 / 地点 / 教师」—— 和强智的"周次(周)[01-02节]"
# 那一套完全不同,只能靠通用退路逐行贴标签。
GENERIC_ROWS = [
    ["时间", "星期一", "星期二", "星期三", "星期四", "星期五"],
    ["08:00-09:40\n第1-2节",
     "大学英语\n第1-2节\n1-16周\n主楼301\n王老师",
     "",
     "高等数学\n第1-2节\n1-16(单周)\n思齐楼B203\n李老师",
     "", "线性代数\n第1-2节\n1-8周"],
    ["10:00-11:40\n第3-4节",
     "高等体育\n第3-4节\n1-16周\n风雨馆\n赵老师",
     "高阶讲座\n每周三下午3点\n详见通知",
     "", "", ""],
]


def _page_dump(tables, **kw):
    """凑一个 TABLE_JS 的形状:{"url","title","tables","selects"}。"""
    return dict({"url": "https://example.edu/timetable", "title": "学期课表",
                 "tables": tables, "selects": {}}, **kw)


# ---------------------------------------------------------------- 通用退路:认多少算多少

def test_generic_grid_reads_a_non_qiangzhi_timetable():
    r = site_parse.parse_generic_grid({"rows": GENERIC_ROWS})
    assert r["ok"] and r["kind"] == "timetable" and r["adapter"] == "generic-table"
    by_name = {c["name"]: c for c in r["classes"]}
    assert by_name["大学英语"] == {
        "name": "大学英语", "day": 1, "periods": "1-2", "start_time": "08:00",
        "weeks": "1-16周", "location": "主楼301", "teacher": "王老师"}
    # 单周信息不能丢(它决定这门课上哪几周)
    assert by_name["高等数学"]["weeks"] == "1-16(单周)"
    assert by_name["高等数学"]["location"] == "思齐楼B203"
    # 只有 5 个星期列也认(有的学校不排周日)
    assert by_name["线性代数"]["day"] == 5 and by_name["高等体育"]["day"] == 1


def test_generic_grid_keeps_unlabelled_lines_in_raw_and_invents_nothing():
    """认不出的行既不能变成课名/地点,也不能被丢掉 —— 它得出现在 raw 里。"""
    r = site_parse.parse_generic_grid({"rows": GENERIC_ROWS})
    lecture = [c for c in r["classes"] if c["name"] == "高阶讲座"][0]
    assert lecture["location"] == "" and lecture["teacher"] == ""
    assert "raw" not in lecture                              # 交给 schedule.save 的条目是干净的
    got = [x for x in r["raw"] if x["name"] == "高阶讲座"]
    assert got and got[0]["lines"] == ["每周三下午3点", "详见通知"]
    assert any("核一遍" in n for n in r["notes"]), r["notes"]


def test_generic_grid_without_bell_column_leaves_time_empty():
    """第一列不写钟点的表:start_time 留空并提示,绝不拿"第1-2节"当 08:00。"""
    rows = [["星期", "星期一", "星期二", "星期三", "星期四", "星期五"],
            ["第1-2节", "结构力学\n第1-2节\n1-16周\n一教101", ""]]
    r = site_parse.parse_generic_grid({"rows": rows})
    assert r["ok"] and r["classes"][0]["start_time"] == ""
    assert any("不编时间" in n for n in r["notes"]), r["notes"]


def test_generic_result_is_saveable():
    """通用退路的输出必须能直接喂 schedule.save 而不被校验丢掉 —— 否则向导是假的。"""
    classes = site_parse.parse_generic_grid({"rows": GENERIC_ROWS})["classes"]
    ok, msg = schedule.save("2026-09-07", classes)
    assert ok and "没通过校验" not in msg, msg
    assert len(schedule.load()["classes"]) == len(classes)


def test_generic_grid_rejects_a_non_timetable():
    """馆藏书目 / 考试安排:没有星期列就是没有,别硬凑一张课表出来。"""
    rows = [["序号", "书名", "索书号", "馆藏地"],
            ["1", "活着", "I247.5/12", "三楼社科阅览室"],
            ["2", "平凡的世界", "I247.5/34", "三楼社科阅览室"]]
    r = site_parse.parse_generic_grid({"rows": rows})
    assert r["ok"] is False and r["kind"] is None and r["classes"] == []
    assert "星期" in r["msg"]


# ---------------------------------------------------------------- 版式识别与派发

def test_parse_page_picks_the_real_table_out_of_a_page_dump():
    """一个页面里通常好几张表(布局表、导航表)。挑有星期列的那张。"""
    dump = _page_dump([
        {"id": "layout", "cls": "", "rows": [["logo"], ["导航"]]},
        {"id": "", "cls": "kb", "rows": GENERIC_ROWS},
    ])
    r = site_parse.parse_page(dump)
    assert r["ok"] and r["adapter"] in ("generic-table", "qiangzhi-xskb")
    assert {c["name"] for c in r["classes"]} >= {"大学英语", "高等数学"}


def test_parse_page_prefers_qiangzhi_grammar_and_tags_the_adapter(grid):
    """强智那张表仍然走强智文法(它能把周次和节次写在一行里,认得更准)。"""
    r = site_parse.parse_page(_page_dump([{"id": "kbtable", "cls": "", "rows": grid["rows"]}],
                                         selects={"xnxq01id": "2026-2027-1", "zc": ""}))
    assert r["ok"] and r["adapter"] == "qiangzhi-xskb"
    assert r["term"] == "2026-2027-1"
    assert all(c["raw"] == [] if "raw" in c else True for c in r["classes"])


def test_parse_page_on_an_empty_or_headerless_page():
    assert site_parse.parse_page({})["ok"] is False
    assert site_parse.parse_page({"tables": []})["kind"] is None
    assert site_parse.parse_page(None)["msg"]
    # 有表但没有一行读得通:报"没认出课表",而不是返回半张假课表
    r = site_parse.parse_page(_page_dump([{"id": "", "rows": [["a", "b"], ["c", "d"]]}]))
    assert r["ok"] is False and r["kind"] is None and r["classes"] == []


def test_non_timetable_still_returns_the_table_it_read():
    """认不出课表 ≠ 读不到内容:整张表按原样回吐,但要有上限。"""
    rows = [["书名", "索书号\n状态"], ["活着", "在架"], ["", ""],
            ["长" * 200, "I247.5/x"]]
    r = site_parse.parse_page(_page_dump([{"id": "", "rows": rows}]))
    assert r["kind"] is None
    assert r["rows"] == [["书名", "索书号 / 状态"], ["活着", "在架"],
                         ["长" * 60, "I247.5/x"]], r["rows"]
    big = _page_dump([{"id": "", "rows": [["a", "b"]] * (site_parse.ROWS_CAP + 9)}])
    r2 = site_parse.parse_page(big)
    assert r2["rows_truncated"] and len(r2["rows"]) == site_parse.ROWS_CAP


def test_weekday_header_needs_five_columns():
    """三五个"星期"字样不算课表:至少 5 个星期列才认(避开"星期一节课"这类句子)。"""
    assert len(site_parse._weekday_header(GENERIC_ROWS)[0]) == 5
    assert site_parse._weekday_header([["时间", "星期一", "星期二"]])[0] == {}
    assert site_parse._weekday_header([["姓名", "学号"], ["张三", "202601"]])[0] == {}
    r = site_parse.parse_generic_grid({"rows": [["时间", "星期一", "星期二"],
                                                ["08:00", "课A", "课B"]]})
    assert r["ok"] is False and r["kind"] is None


def test_norm_weeks_and_periods_stay_consistent_between_adapters():
    assert site_parse._norm_periods("01-08") == "1-8"
    assert site_parse._norm_periods("第3-4节") == "3-4"
    assert site_parse._norm_weeks("1-16(周)[01-02节]") == "1-16"
    assert site_parse._norm_weeks("4,6,8(周)") == "4,6,8"


def test_load_dump_is_forgiving():
    assert site_parse.load_dump("") is None
    assert site_parse.load_dump("不是JSON") is None
    assert site_parse.load_dump("[1,2]") is None        # 顶层不是 dict 一律不猜
    assert site_parse.load_dump({"rows": []}) == {"rows": []}


# ---------------------------------------------------------------- page_parse 工具

class FakeFrames:
    """假浏览器:只回"某一帧读到了这段 JS 的结果",不碰真浏览器。"""

    def __init__(self, hits, *, exc=None):
        self.hits = hits
        self.exc = exc
        self.seen_js = []
        self.seen_url_like = []

    def read_frames(self, js, **kw):
        self.seen_js.append(js)
        self.seen_url_like.append(kw.get("url_like"))
        if self.exc:
            raise self.exc
        return self.hits


@pytest.fixture
def fake_frames(monkeypatch):
    def install(hits, *, exc=None):
        f = FakeFrames(hits, exc=exc)
        monkeypatch.setattr(browser, "read_frames", f.read_frames)
        return f
    return install


def test_page_parse_returns_structured_json(fake_frames):
    fake = fake_frames([{"url": "https://example.edu/kb",
                         "value": json.dumps(_page_dump(
                             [{"id": "", "rows": GENERIC_ROWS}],
                             selects={"zc": ""}), ensure_ascii=False)}])
    out = site_parse.page_parse(None, url_like="example.edu")
    assert "版式: timetable" in out, out
    assert "大学英语" in out and "主楼301" in out
    assert "没替你存" not in out
    assert fake.seen_js[0] == site_parse.TABLE_JS          # 通吃全表的那段 JS
    assert fake.seen_url_like == ["example.edu"]
    assert schedule.load() is None and not paths.APP_DIR.exists(), "只读工具写了盘"


def test_page_parse_says_it_did_not_store_a_non_timetable(fake_frames):
    rows = [["考试科目", "时间", "考场"], ["高等数学", "9月30日 09:00", "思齐楼B203"]]
    fake_frames([{"url": "https://example.edu/exam",
                  "value": json.dumps(_page_dump([{"id": "", "rows": rows}]),
                                      ensure_ascii=False)}])
    out = site_parse.page_parse(None)
    assert "认不出是课表" in out and "没替你存" in out
    assert "思齐楼B203" in out and "9月30日 09:00" in out      # 原表行列如实给回去
    assert "别自己补" in out
    assert schedule.load() is None


def test_page_parse_exits_are_all_actionable(fake_frames):
    fake_frames([])
    assert "没有可读的页面" in site_parse.page_parse(None)
    fake_frames([], exc=browser.BrowserError("还没有打开的页面:先 browser_open"))
    assert "没有可读的页面" in site_parse.page_parse(None)     # 真浏览器是抛错,不是回空
    fake_frames([{"url": "u", "error": "帧没了", "value": None}])
    assert "没有读到任何表格" in site_parse.page_parse(None)
    fake_frames([], exc=browser.BrowserUnavailable("没有可用的浏览器"))
    assert site_parse.page_parse(None).startswith("错误:没有可用的浏览器")


def test_page_parse_shows_raw_and_unparsed_lines(fake_frames):
    dump = _page_dump([{"id": "", "rows": GENERIC_ROWS}])
    fake_frames([{"url": "u", "value": json.dumps(dump, ensure_ascii=False)}])
    out = site_parse.page_parse(None)
    assert "详见通知" in out                               # 归不了类的行要露面
    assert "人工核" in out or "别猜" in out


def test_page_parse_caps_the_output_and_says_so(fake_frames):
    rows = [["时间", "星期一", "星期二", "星期三", "星期四", "星期五"]]
    for i in range(9):
        rows.append([f"0{i}:00-0{i+1}:40\n第{2*i+1}-{2*i+2}节"]
                    + [f"课{i}{d}\n第{2*i+1}-{2*i+2}节\n1-16周\n主楼{i}0{d}" for d in range(1, 6)])
    fake_frames([{"url": "u", "value": json.dumps(_page_dump(
        [{"id": "", "rows": rows}]), ensure_ascii=False)}])
    out = site_parse.page_parse(None)
    assert f"只列前 {site_parse.PAGE_MAX_CLASSES} 条" in out
    assert json.loads(out.split("\n", 2)[2].split("(只列前")[0])
