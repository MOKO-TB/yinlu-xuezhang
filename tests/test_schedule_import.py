"""教务课表导入(minicode/schedule_import.py):真页网格夹具 + 三步向导 + 模型退路。

夹具 tests/fixtures/jw_xskb_grid.json 的结构复刻强智教务 #kbtable 的实测文法
(课程名 / 教师 / 周次(周)[NN-MM节] / 地点),姓名地点全为虚构。
"""
import json
from pathlib import Path

import pytest

from minicode import paths, schedule, schedule_import as si

FIXTURE = Path(__file__).parent / "fixtures" / "jw_xskb_grid.json"


@pytest.fixture(autouse=True)
def isolated_app_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "APP_DIR", tmp_path / "app")


@pytest.fixture
def grid():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 确定性解析

def test_parse_grid_from_real_shape(grid):
    r = si.parse_grid(grid)
    assert r["ok"] and len(r["classes"]) == 8, r["classes"]
    by_name = {c["name"]: c for c in r["classes"]}
    assert by_name["数据结构与算法"] == {
        "name": "数据结构与算法", "day": 1, "periods": "1-2",
        "start_time": "08:00", "weeks": "1-16", "location": "C-205",
        "teacher": "张老师"}
    # 三行条目(没有地点那一行):地点留空,不编
    assert by_name["离散数学"]["location"] == ""
    assert by_name["离散数学"]["weeks"] == "1-12"
    # 01-08 节连上 → 1-8;逗号周次原样交给 schedule.parse_weeks
    assert by_name["大学英语综合"]["periods"] == "1-8"
    assert by_name["大学英语综合"]["weeks"] == "1-8,9-16"
    # 双周写法保留奇偶信息,由 schedule 那边解释
    assert by_name["创新创业基础"]["weeks"] == "2-14(双)"


def test_multi_block_course_dedupes_to_earliest_bell(grid):
    """连上 8 节的课在 1-2 节和 3-4 节两行里各印一次:只留一条,钟点取最早那行。"""
    r = si.parse_grid(grid)
    hits = [c for c in r["classes"] if c["name"] == "大学英语综合"]
    assert len(hits) == 1 and hits[0]["start_time"] == "08:00"


def test_unparsable_cell_reported_not_invented(grid):
    r = si.parse_grid(grid)
    assert any("周次" in s for s in r["unparsed"]), r["unparsed"]
    assert all("讲座" not in c["name"] for c in r["classes"])


@pytest.mark.parametrize("raw,want", [("01-02", "1-2"), ("01-08", "1-8"),
                                      ("03-03", "3"), ("01-02-03-04", "1-4"),
                                      ("01-03-05-07", "1,3,5,7"), ("", "")])
def test_norm_periods_span_vs_enumeration(raw, want):
    assert si._norm_periods(raw) == want


def test_missing_bell_is_noted(grid):
    r = si.parse_grid(grid)
    assert any("钟点" in n for n in r["notes"]), r["notes"]
    assert [c for c in r["classes"] if c["name"] == "文献检索"][0]["start_time"] == ""


def test_week_filter_warns_semester_may_be_truncated(grid):
    r = si.parse_grid(dict(grid, week="3"))
    assert any("第 3 周" in n and "全部" in n for n in r["notes"]), r["notes"]
    assert not any("全部" in n for n in si.parse_grid(grid)["notes"])


def test_parse_grid_without_weekday_header_fails_loudly():
    r = si.parse_grid({"rows": [["时间", "第1节", "第2节"], ["08:00-09:40", "语文", "数学"]]})
    assert r["ok"] is False and "星期" in r["msg"]
    assert r["classes"] == []


def test_empty_or_ragged_grid_survives():
    assert si.parse_grid({})["ok"] is False
    assert si.parse_grid(None)["ok"] is False
    # 表头在第三行、行长不齐(合并单元格里层出的稀疏行)都不能抛
    rows = [["", "", ""], ["", "", ""], ["", "星期一", "星期二", "星期三", "星期四", "星期五"],
            ["08:00-09:40\n第1-2节", "数学\n老师A\n1-16(周)[01-02节]\nA101"]]
    r = si.parse_grid({"rows": rows})
    assert r["ok"] and r["classes"][0]["name"] == "数学"


def test_parsed_classes_pass_schedule_validation(grid):
    """解析结果必须能直接喂给 schedule.save 而不被丢掉 —— 否则向导是假的。"""
    classes = si.parse_grid(grid)["classes"]
    ok, msg = schedule.save("2026-09-07", classes)
    assert ok and "没通过校验" not in msg, msg
    data = schedule.load()
    assert len(data["classes"]) == len(classes)


# ---------------------------------------------------------------- 摘要

def test_summarize_separates_entries_from_courses(grid):
    s = si.summarize(si.parse_grid(grid)["classes"])
    assert s["entries"] == 8 and s["courses"] == 8
    assert s["no_location"] == ["离散数学"]
    assert s["no_time"] == ["文献检索"]
    assert si.summarize([]) == {"entries": 0, "courses": 0,
                                "no_location": [], "no_time": []}


def test_summarize_counts_same_course_twice_as_one():
    s = si.summarize([{"name": "毛概", "location": "一教305", "start_time": "14:00"},
                      {"name": "毛概", "location": "", "start_time": ""}])
    assert (s["entries"], s["courses"]) == (2, 1)


# ---------------------------------------------------------------- 模型退路

def test_model_fallback_accepts_json_wrapped_in_prose():
    def complete(system, user):
        assert "JSON" in system and system and user
        return '好的:' + json.dumps([{"name": "某课", "day": "3", "periods": "5-6",
                                      "weeks": "1-8", "location": "", "teacher": ""}]) + "。"
    r = si.parse_with_model("某课 星期三 第5-6节 1-8周", complete)
    assert r["ok"] and r["classes"][0]["name"] == "某课"
    assert any("核一遍" in n for n in r["notes"])       # 出自模型的结果要标出来


def test_model_fallback_three_ways_out():
    assert si.parse_with_model("   ", lambda s, u: "[]")["ok"] is False
    r = si.parse_with_model("页面文字", lambda s, u: "我看不明白")
    assert r["ok"] is False and "没给出" in r["msg"]
    r2 = si.parse_with_model("页面文字", lambda s, u: '[{"name": ]')
    assert r2["ok"] is False and "JSON" in r2["msg"]

    def boom(system, user):
        raise TimeoutError("网络不行")
    r3 = si.parse_with_model("页面文字", boom)
    assert r3["ok"] is False and "TimeoutError" in r3["msg"]


# ---------------------------------------------------------------- 三步向导
# 假浏览器:只记录调用,不碰真浏览器。三个动作各验一条主路 + 一条退路。

class FakeBrowser:
    BrowserUnavailable = RuntimeError
    MAX_WAIT_MS = 30000
    FRAME_MAX_CHARS = 200000

    def __init__(self, *, grid_json=None, frames_error=False):
        self.grid_json = grid_json
        self.frames_error = frames_error
        self.calls = []
        self.visible = False

    def ensure_visible(self):
        self.calls.append(("ensure_visible",))
        self.visible = True

    def last_note(self):
        # 真模块有这一只(复用/换配置那句话要从这里出来),替身缺它就是把
        # 第 1 步那句回话里新加的那一段测成了空气。
        return ""

    def open_page(self, url, **kw):
        self.calls.append(("open_page", url))
        return {"url": url, "title": "教务系统", "mode": kw.get("mode", "launch")}

    def adopt_page(self, hint, **kw):
        self.calls.append(("adopt_page", hint))
        return {"url": "https://jw.educationgroup.cn/xskb", "title": "学期课表"}

    def evaluate(self, expr, **kw):
        return "https://jw.educationgroup.cn/gzasc/xsMain.jsp"

    def read_frames(self, js, **kw):
        self.calls.append(("read_frames",))
        if self.frames_error:
            return [{"url": "x", "error": "帧没了"}]
        return [{"url": "x", "value": self.grid_json}]


def test_open_login_makes_the_window_visible_first():
    fb = FakeBrowser()
    r = si.ImportFlow(browser_mod=fb).open_login()
    assert r["ok"] and r["stage"] == "need_login"
    assert [c[0] for c in fb.calls] == ["ensure_visible", "open_page"]
    assert fb.calls[1][1] == si.LOGIN_URL
    assert "验证码" in r["msg"] or "你" in r["msg"]      # 登录由人来,不含糊


def test_read_returns_parsed_grid_and_writes_nothing():
    grid = json.loads(FIXTURE.read_text(encoding="utf-8"))
    fb = FakeBrowser(grid_json=json.dumps(grid, ensure_ascii=False))
    r = si.ImportFlow(browser_mod=fb).read()
    assert r["ok"] and r["stage"] == "ready"
    assert r["summary"]["entries"] == 8
    assert "8 条课" in r["msg"] and "不会写任何文件" in r["msg"]
    assert schedule.load() is None and not (paths.APP_DIR / "schedule.json").exists(),         "预览阶段一个字都不写(R1 总闸会预建 APP_DIR 目录,目录存在不算写)"


def test_read_without_table_says_so_and_points_at_the_menu():
    fb = FakeBrowser(frames_error=True)
    r = si.ImportFlow(browser_mod=fb).read()
    assert r["ok"] is False and r["stage"] == "no_grid"
    assert "学期课表" in r["msg"]
    assert "xsMain.jsp" in r["msg"]                     # 告诉用户学长看到的是哪页


def test_read_with_model_sends_page_text_and_labels_the_source():
    class TextBrowser(FakeBrowser):
        def read_frames(self, js, **kw):
            return [{"url": "x", "value": "毛概 星期五 第5-6节 1-14周 一教305"}]

    seen = {}

    def complete(system, user):
        seen["user"] = user
        return json.dumps([{"name": "毛概", "day": 5, "periods": "5-6",
                            "weeks": "1-14", "location": "一教305", "teacher": "刘教授"}])

    r = si.ImportFlow(browser_mod=TextBrowser()).read_with_model(complete)
    assert r["ok"] and "毛概" in seen["user"]
    assert r["stage"] == "ready" and r["summary"]["courses"] == 1
    assert any("模型" in n for n in r["notes"])


def test_flow_survives_browser_refusing_to_start():
    class Dead(FakeBrowser):
        def open_page(self, url, **kw):
            raise RuntimeError("没有可用的浏览器")

    r = si.ImportFlow(browser_mod=Dead()).open_login()
    assert r["ok"] is False and r["stage"] == "failed" and "浏览器" in r["msg"]


# ---------------------------------------------------------------- 清登录态

class FakeBrForProfile(FakeBrowser):
    ENV_PROFILE = "YINLUXS_TEST_PROFILE"

    def __init__(self, root):
        super().__init__()
        self.root = Path(root)
        self.shutdown_called = False

    def app_path(self, *parts):
        return self.root.joinpath(*parts)

    def shutdown(self):
        self.shutdown_called = True


def test_clear_login_state_deletes_only_the_own_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "APP_DIR", tmp_path)
    monkeypatch.delenv(FakeBrForProfile.ENV_PROFILE, raising=False)
    prof = tmp_path / "browser-profile" / "Default"
    prof.mkdir(parents=True)
    (prof / "Cookies").write_text("cookie", encoding="utf-8")
    fb = FakeBrForProfile(tmp_path)
    r = si.clear_login_state(browser_mod=fb)
    assert r["ok"] and fb.shutdown_called
    assert not (tmp_path / "browser-profile").exists()


def test_clear_login_state_refuses_to_rmtree_somewhere_else(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "APP_DIR", tmp_path / "app")
    elsewhere = tmp_path / "someone-elses-profile"
    (elsewhere / "Default").mkdir(parents=True)
    monkeypatch.setenv(FakeBrForProfile.ENV_PROFILE, str(elsewhere))
    r = si.clear_login_state(browser_mod=FakeBrForProfile(tmp_path / "app"))
    assert r["ok"] is False and "不替乱删" in r["msg"]
    assert elsewhere.exists(), "越界的目录被删了"


# ---------------------------------------------------------------- 桥接层

def _wizard_bridge(tmp_path, monkeypatch):
    """一个只够跑这三步的桥:真 ImportFlow + 假浏览器,不碰 webview 也不碰真浏览器。"""
    from types import MethodType, SimpleNamespace

    from minicode.gui_bridge import Bridge
    monkeypatch.setattr(paths, "APP_DIR", tmp_path / "app")
    grid = json.loads(FIXTURE.read_text(encoding="utf-8"))
    b = SimpleNamespace(_jw_import_flow=si.ImportFlow(
        browser_mod=FakeBrowser(grid_json=json.dumps(grid, ensure_ascii=False))),
        _session_llm=lambda runner=None: None, _shared_llm=None)
    b._jw_flow = MethodType(Bridge._jw_flow, b)     # 连"整程共用一个 flow"一起测
    return b, Bridge


def test_bridge_reads_without_saving_and_saves_only_on_confirm(tmp_path, monkeypatch):
    b, Bridge = _wizard_bridge(tmp_path, monkeypatch)
    r = Bridge.schedule_import_read(b)
    assert r["ok"] and r["summary"]["entries"] == 8
    assert schedule.load() is None, "还没确认导入就把课表写盘了"

    bad = Bridge.schedule_import_confirm(b, "")          # 教务页上没有开学日期
    assert bad["ok"] is False and "term_start" in bad["msg"]

    ok = Bridge.schedule_import_confirm(b, "2026-09-07")
    assert ok["ok"] and len(schedule.load()["classes"]) == 8
    assert ok["schedule"]["registered"] is True
    # 一次性:确认过就交出去,再点一次不该拿旧数据覆盖
    assert Bridge.schedule_import_confirm(b, "2026-09-07")["ok"] is False


def test_bridge_model_route_needs_a_configured_model(tmp_path, monkeypatch):
    b, Bridge = _wizard_bridge(tmp_path, monkeypatch)
    r = Bridge.schedule_import_model(b)
    assert r["ok"] is False and "模型" in r["msg"]

    class L:
        def complete(self, system, user):
            return '[{"name": "毛概", "day": 5, "periods": "5-6", "weeks": "1-14",' \
                   ' "location": "一教305", "teacher": "刘教授"}]'
    b._session_llm = lambda runner=None: L()
    r2 = Bridge.schedule_import_model(b)
    assert r2["ok"] and r2["classes"][0]["name"] == "毛概"
    assert Bridge.schedule_import_confirm(b, "2026-09-07")["ok"]


def test_bridge_clear_login_state_is_reachable(tmp_path, monkeypatch):
    b, Bridge = _wizard_bridge(tmp_path, monkeypatch)
    b._jw_import_flow = None
    monkeypatch.delenv("YINLUXS_BROWSER_PROFILE", raising=False)
    assert Bridge.schedule_import_clear(b)["ok"] is True   # 本机本来就没有登录态


def test_public_api_delegates_the_wizard_with_string_params():
    """桥门面:六个方法都得在;参数是 str(JS 数组会原样变成 list,桥不接)。"""
    import inspect

    from minicode.gui_bridge import PublicApi

    class Recorder:
        def __init__(self):
            self.seen = []

        def __getattr__(self, name):
            def call(*args):
                self.seen.append((name, args))
                return {"ok": True}
            return call

    names = ["schedule_import_open", "schedule_import_read", "schedule_import_model",
             "schedule_import_confirm", "schedule_import_clear"]
    b = Recorder()
    api = PublicApi(b)
    sigs = {n: list(inspect.signature(getattr(PublicApi, n), eval_str=True)
                    .parameters.values())[1:] for n in names}   # 桥文件用了
    #  `from __future__ import annotations`,不 eval 的话 annotation 是字符串 'str'
    for n in names:
        getattr(api, n)(*[""] * len(sigs[n]))
    assert [s[0] for s in b.seen] == names
    assert all(isinstance(a, str) for _, args in b.seen for a in args)
    for n, params in sigs.items():
        for p in params:
            assert p.annotation is str, f"{n}({p.name}) 的参数该是 str,不是 {p.annotation}"


# ---------------------------------------------------------------- 变动自动比对(v0.9.24)

def _grid_json(grid):
    return json.dumps(grid, ensure_ascii=False)


def test_wizard_read_reports_first_import(grid):
    """本机没课表时第②步要明说"首次导入",不要报一堆"新增 N 条"吓人。"""
    r = si.ImportFlow(browser_mod=FakeBrowser(grid_json=_grid_json(grid))).read()
    assert r["ok"] and r["diff"]["has_existing"] is False
    assert "首次导入" in r["msg"]


def test_wizard_read_shows_what_would_change(grid):
    """已经存过表:第②步就得报出"换教室"这类差,再让人点覆盖。"""
    assert schedule.save("2026-09-07", si.parse_grid(grid)["classes"])[0]
    data = schedule.load()                       # 把已存的第一门课教室改掉
    data["classes"][0]["location"] = "思齐楼Z999"
    schedule.state_path().write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    r = si.ImportFlow(browser_mod=FakeBrowser(grid_json=_grid_json(grid))).read()
    assert r["diff"]["has_existing"] is True
    assert r["diff"]["counts"]["changed"] == 1
    assert "变动 1 条" in r["msg"]


def test_wizard_diff_never_writes(grid):
    """比对全程只读:第②步之后盘上不该出现任何课表文件。"""
    r = si.ImportFlow(browser_mod=FakeBrowser(grid_json=_grid_json(grid))).read()
    assert r["ok"]
    assert schedule.load() is None
    assert not schedule.prev_path().exists()


def test_wizard_survives_a_corrupt_local_schedule(grid):
    """本机课表文件坏了也不能拦第②步:顶多退化成"没有可比的"。"""
    schedule.state_path().parent.mkdir(parents=True, exist_ok=True)
    schedule.state_path().write_text("{坏文件", encoding="utf-8")
    r = si.ImportFlow(browser_mod=FakeBrowser(grid_json=_grid_json(grid))).read()
    assert r["ok"] and r["diff"]["has_existing"] is False


def test_wizard_reads_a_non_qiangzhi_timetable():
    """学校不是强智也能走第②步:通用退路认出什么就报什么,并说明按哪条文法认的。"""
    dump = {"url": "https://jw.example.edu/tb", "title": "学期课表", "selects": {},
            "tables": [{"id": "", "cls": "", "rows": [
                ["时间", "星期一", "星期二", "星期三", "星期四", "星期五"],
                ["08:00-09:40\n第1-2节", "大学英语\n第1-2节\n1-16周\n主楼301\n王老师",
                 "", "", "", ""]]}]}
    r = si.ImportFlow(browser_mod=FakeBrowser(grid_json=_grid_json(dump))).read()
    assert r["ok"] and r["stage"] == "ready"
    assert r["adapter"] == "generic-table" and "generic-table" in r["msg"]
    assert r["classes"][0]["name"] == "大学英语"
    assert any("核一遍" in n for n in r["notes"])           # 通用退路要标出来


def test_wizard_refuses_to_import_a_table_that_is_not_a_timetable():
    """读到表格但不是课表(考试安排/书目):明说"这不是课表,我不替你存"。"""
    dump = {"url": "https://jw.example.edu/ks", "title": "考试安排", "selects": {},
            "tables": [{"id": "", "cls": "", "rows": [
                ["考试科目", "时间", "考场"], ["高等数学", "9月30日 09:00", "思齐楼B203"]]}]}
    r = si.ImportFlow(browser_mod=FakeBrowser(grid_json=_grid_json(dump))).read()
    assert r["ok"] is False and r["stage"] == "not_timetable"
    assert "不是课表" in r["msg"] and "page_parse" in r["msg"]
    assert r["diff"] is None                                # 没课表可比,别装出"零变动"
    assert schedule.load() is None and not schedule.prev_path().exists()


def test_wizard_falls_back_to_the_model_when_the_timetable_is_unreadable():
    """有星期列却一行课都读不出来:结构变了,该给模型整理那条退路。"""
    dump = {"url": "u", "title": "t", "selects": {}, "tables": [{"id": "", "rows": [
        ["时间", "星期一", "星期二", "星期三", "星期四", "星期五"],
        ["08:00-09:40", "1-16周", "", "", "", ""]]}]}
    r = si.ImportFlow(browser_mod=FakeBrowser(grid_json=_grid_json(dump))).read()
    assert r["ok"] is False and r["stage"] == "parsed_failed"
    assert "模型整理" in r["msg"] and r["unparsed"]


def test_bridge_undo_returns_fresh_view():
    """课表面板「换回上一版」:桥要把回滚后的视图一起回给前端。"""
    from types import SimpleNamespace

    from minicode.gui_bridge import Bridge
    assert schedule.save("2026-09-07", [{"name": "高等数学", "day": 1,
                                         "periods": "1-2", "location": "B203"}])[0]
    assert schedule.save("2026-09-07", [{"name": "高等数学", "day": 1,
                                         "periods": "1-2", "location": "A101"}])[0]
    r = Bridge.schedule_undo(SimpleNamespace())
    assert r["ok"] and r["schedule"]["registered"] is True
    assert r["schedule"]["can_undo"] is True          # 对调后还能再换回去
    assert schedule.load()["classes"][0]["location"] == "B203"
