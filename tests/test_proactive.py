"""主动推荐(proactive)测试:标题清洗 / 列表页解析 / 快照去重 / 校历提醒 / 开关。"""
import json
from datetime import date, datetime

import pytest

import httpx
from minicode import proactive

LIST_HTML = """
<html><body>
<div class="list">
  <a href="/jwc/info/1066/6852.htm"><div>09-11 2026</div>
    <div>2026年下半年全国计算机等级考试考生须知 2026年下半年全国计算机等级考试将于9月19-21日举行</div></a>
  <a href="/jwc/info/1066/5532.htm"><div>06-02 2026</div>
    <div>关于2026年上半年全国大学英语四、六级考试考生须知 现将有关事项通知如下</div></a>
  <a href="/jwc/xwdt/tzgg_2.htm"><div>下一页</div><div>翻页不是通知</div></a>
</div></body></html>
"""

SAMPLES = [
    ("09-11 2026 2026年下半年全国计算机等级考试考生须知 2026年下半年全国计算机等级考试将于",
     "2026年下半年全国计算机等级考试考生须知"),
    ("05-08 2026 关于莲花书院首批特色班开课的通知 各二级学院： 根据《管理办法》",
     "关于莲花书院首批特色班开课的通知"),
]


@pytest.fixture(autouse=True)
def _app_dir(tmp_path, monkeypatch):
    """每条测试:独立的 APP_DIR + 空白的「今天推过了」账本 + 绝不真弹桌面通知。

    `_mem_pushed` 是模块级的:不清掉的话上一条测试记的键会漏到下一条,
    "今天没推过"凭空变成"今天推过了" —— 全量跑和单跑结果不一样,最难查。
    """
    from minicode import paths, tray
    monkeypatch.setattr(paths, "APP_DIR", tmp_path / "app")
    monkeypatch.setattr(proactive, "_mem_pushed", set())
    monkeypatch.setattr(proactive, "_mem_day", "")
    monkeypatch.setattr(proactive, "_logged_since_trim", 0)
    monkeypatch.setattr(tray, "notify", lambda *a, **k: {"ok": True})
    yield


def test_clean_title_on_real_anchor_text():
    for raw, expect in SAMPLES:
        assert proactive.clean_title(raw) == expect


def test_fetch_latest_parses_section_page(monkeypatch):
    class FakeResp:
        status_code = 200
        url = "https://www.gzasc.edu.cn/jwc/xwdt/tzgg.htm"
        text = LIST_HTML

    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResp())
    items = proactive.fetch_latest("https://www.gzasc.edu.cn/jwc/xwdt/tzgg.htm")
    assert len(items) == 2                        # 翻页锚点没有 /info/ 被排除
    assert items[0]["title"] == "2026年下半年全国计算机等级考试考生须知"
    assert items[0]["url"].endswith("/jwc/info/1066/6852.htm")


def test_fetch_latest_raises_on_http_error(monkeypatch):
    class BadResp:
        status_code = 404
        url = "x"
        text = "no"

    monkeypatch.setattr(httpx, "get", lambda *a, **k: BadResp())
    with pytest.raises(RuntimeError):
        proactive.fetch_latest("https://example.cn/x.htm")


def test_check_once_reports_new_items_only_once(monkeypatch):
    payload = [{"title": "关于期末考试安排的通知", "url": "https://www.gzasc.edu.cn/a/1.htm"}]
    monkeypatch.setattr(proactive, "fetch_latest", lambda url, timeout=12: payload)
    state = proactive.load_state()
    first = proactive.check_once(state)
    assert len(first) == 1 and first[0]["source"] == "教务处通知"
    second = proactive.check_once(state)
    assert second == []                           # 同一批只报一次
    saved = json.loads(proactive.state_path().read_text(encoding="utf-8"))
    assert "关于期末考试安排的通知" in saved["seen"]["https://www.gzasc.edu.cn/jwc/xwdt/tzgg.htm"]["titles"]


def test_check_once_survives_broken_source(monkeypatch):
    def fake(url, timeout=12):
        if "xsc" in url:
            raise ConnectionError("network down")
        return [{"title": "教务处正常条目通知", "url": "https://www.gzasc.edu.cn/j/2.htm"}]

    monkeypatch.setattr(proactive, "fetch_latest", fake)
    items = proactive.check_once()
    assert [i["title"] for i in items] == ["教务处正常条目通知"]


def test_calendar_alerts_only_near_events(monkeypatch):
    from minicode import calendar as cal
    events = [{"name": "中秋节", "start": "2026-09-25", "kind": "holiday"},
              {"name": "四六级笔试", "start": "2026-12-12", "kind": "exam"}]
    monkeypatch.setattr(cal, "load_events", lambda: (events, "test"))
    alerts = proactive.calendar_alerts(today=date(2026, 9, 19))
    assert [a["name"] for a in alerts] == ["中秋节"]
    assert alerts[0]["days_left"] == 6


def test_bridge_set_notice_watch_persists(config, tmp_path):
    from tests.test_gui_bridge import FakeWindow
    from minicode.gui_bridge import create_app
    bridge, ui, _agent, _session = create_app(
        config, tmp_path, llm=FakeLLM(),
        sessions_dir=tmp_path / "sessions", config_path=tmp_path / "config.json")
    ui.attach(FakeWindow())
    assert bridge.set_notice_watch(False)["ok"] is True
    saved = json.loads(proactive.state_path().read_text(encoding="utf-8"))
    assert saved["enabled"] is False
    assert bridge.public_api().set_notice_watch(True)["enabled"] is True


class FakeLLM:
    def stream_chat(self, messages, tools, on_text=None, on_reasoning=None,
                    on_retry=None):
        from minicode.llm import AssistantTurn
        return AssistantTurn(text="")


# ---------------------------------------------------------------- 新版本检查

def test_check_update_detects_newer_release(monkeypatch):
    class FakeResp:
        status_code = 200

        def json(self):
            return {"tag_name": "v0.9.18", "html_url": "https://gitee.com/inkzero/momo/releases/v0.9.18",
                    "body": "新增主动推荐与提醒"}

    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResp())
    result = proactive.check_update("0.9.17")
    assert result["version"] == "0.9.18" and result["url"].endswith("v0.9.18")


def test_check_update_ignores_same_older_and_failures(monkeypatch):
    class SameResp:
        status_code = 200

        def json(self):
            return {"tag_name": "v0.9.17", "html_url": "x", "body": ""}

    monkeypatch.setattr(httpx, "get", lambda *a, **k: SameResp())
    assert proactive.check_update("0.9.17") is None      # 同版本不提示

    class OldResp:
        status_code = 200

        def json(self):
            return {"tag_name": "0.9.10", "html_url": "x", "body": ""}

    monkeypatch.setattr(httpx, "get", lambda *a, **k: OldResp())
    assert proactive.check_update("0.9.17") is None      # 更旧不提示

    class BadResp:
        status_code = 404
        url = "x"
        text = "no"

    monkeypatch.setattr(httpx, "get", lambda *a, **k: BadResp())
    assert proactive.check_update("0.9.17") is None      # 接口失败静默

    def boom(*a, **k):
        raise ConnectionError("offline")

    monkeypatch.setattr(httpx, "get", boom)
    assert proactive.check_update("0.9.17") is None      # 断网静默


# ============================================ 桌面通知正文(v0.9.25)

def test_desktop_text_reuses_the_chat_line():
    """briefing / class / schedule 已经带 text:两处用同一行,不许各说各话。"""
    for kind in ("briefing", "class", "schedule"):
        assert proactive.desktop_text(
            kind, {"text": " 快上课了：高等数学 08:00 · B101 "}) == \
            "快上课了：高等数学 08:00 · B101"


def test_desktop_text_from_reminders_fields():
    payload = {"items": [{"title": "交奖学金材料", "due": "2026-09-22",
                          "days_left": 1},
                         {"title": "还书", "due": "2026-09-20", "days_left": -1}],
               "total": 2}
    assert proactive.desktop_text("reminders", payload) == \
        "交奖学金材料(2026-09-22,还有 1 天) 等 2 件"
    one = {"items": [{"title": "交表", "due": "2026-09-20", "days_left": -3}]}
    assert proactive.desktop_text("reminders", one) == "交表(2026-09-20,已逾期 3 天)"
    bare = {"items": [{"title": "交表"}]}                  # 没 due 就不编时间
    assert proactive.desktop_text("reminders", bare) == "交表"


def test_desktop_text_from_calendar_and_notices_and_update():
    assert proactive.desktop_text("calendar", {"name": "中秋节", "days_left": 6,
                                               "start": "2026-09-25"}) == \
        "中秋节:还有 6 天(2026-09-25)"
    assert proactive.desktop_text("calendar", {"name": "中秋节", "days_left": 6}) == \
        "中秋节:还有 6 天(日期见校历)"              # 没日期不编日期
    notices = {"items": [{"title": "期末考试安排", "source": "教务处通知"},
                         {"title": "第二条"}], "total": 5}
    assert proactive.desktop_text("notices", notices) == \
        "教务处通知:期末考试安排 等 5 条新通知"
    assert proactive.desktop_text("notices", {"items": [
        {"title": "只有一条"}]}) == "官网:只有一条"
    assert proactive.desktop_text("update", {"version": "0.9.26"}) == \
        "v0.9.26 已发布,去下载更新"


@pytest.mark.parametrize("kind,payload", [
    ("reminders", {"items": []}),
    ("reminders", {"items": [{"due": "2026-09-22"}]}),        # 连标题都没有
    ("calendar", {"days_left": 3}),                            # 叫不出事件名
    ("calendar", {"name": "校运会"}),                          # 不知道几天后
    ("notices", {"items": []}),
    ("update", {}),
    ("class", {}),                                             # 新类别没 text
    ("unknown_kind", {"foo": 1}),
])
def test_desktop_text_refuses_to_invent(kind, payload):
    """缺字段就回 None(整条不发):桌面通知比聊天消息更容易被当真。"""
    assert proactive.desktop_text(kind, payload) is None


def test_push_desktop_maps_title_and_delegates(monkeypatch):
    calls = []
    from minicode import tray
    monkeypatch.setattr(tray, "notify",
                        lambda title, body, kind="": calls.append(
                            (title, body, kind)) or {"ok": True})
    r = proactive.push_desktop("class", {"text": "快上课了：高等数学"})
    assert r == {"ok": True}
    assert calls == [("快上课了", "快上课了：高等数学", "class")]


def test_push_desktop_stays_quiet_without_text(monkeypatch):
    from minicode import tray
    monkeypatch.setattr(tray, "notify",
                        lambda *a, **k: pytest.fail("没正文不该弹"))
    assert proactive.push_desktop("calendar", {}) == \
        {"ok": False, "skipped": "no_text"}


def test_push_desktop_swallows_tray_errors(monkeypatch):
    """桌面层再怎么炸也不能影响聊天里那条推送。"""
    from minicode import tray

    def boom(*a, **k):
        raise RuntimeError("托盘没了")

    monkeypatch.setattr(tray, "notify", boom)
    assert proactive.push_desktop("class", {"text": "要上课"})["skipped"] == "error"


def _push_bridge(broken: bool = False):
    """够用的 bridge 替身:_push_proactive 只碰 bridge.ui.push。"""
    from types import SimpleNamespace

    class UI:
        def __init__(self):
            self.events = []

        def push(self, type_, **payload):
            if broken:
                raise RuntimeError("窗口已销毁")
            payload["type"] = type_
            self.events.append(payload)

    ui = UI()
    return SimpleNamespace(ui=ui), ui


def test_push_proactive_pushes_ui_and_desktop(monkeypatch):
    desktop = []
    from minicode import tray
    monkeypatch.setattr(tray, "notify",
                        lambda title, body, kind="": desktop.append(
                            (title, body, kind)) or {"ok": True})
    bridge, ui = _push_bridge()
    proactive._push_proactive(bridge, "notices", items=[
        {"title": "调课通知", "source": "教务处通知"}], total=1)
    assert ui.events == [{"kind": "notices", "items": [
        {"title": "调课通知", "source": "教务处通知"}], "total": 1,
        "type": "proactive"}]
    assert desktop == [("校园新通知", "教务处通知:调课通知", "notices")]


def test_push_proactive_survives_ui_error_and_still_desktop(monkeypatch):
    """聊天栏推失败(窗口正关着)时,桌面那条仍要发出去。"""
    desktop = []
    from minicode import tray
    monkeypatch.setattr(tray, "notify",
                        lambda title, body, kind="": desktop.append(body))
    bridge, _ui = _push_bridge(broken=True)
    proactive._push_proactive(bridge, "calendar", name="国庆", days_left=5,
                              start="2026-10-01")
    assert desktop == ["国庆:还有 5 天(2026-10-01)"]


def test_push_proactive_ignores_desktop_layer_exceptions(monkeypatch):
    """托盘那侧的桌面通知没装成也不能影响聊天里那条推送。"""
    from minicode import tray

    def boom(*a, **k):
        raise ImportError("托盘模块装不起来")

    monkeypatch.setattr(tray, "notify", boom)
    bridge, ui = _push_bridge()
    proactive._push_proactive(bridge, "class", text="要上课了")
    assert ui.events and ui.events[0]["text"] == "要上课了"


# ============================== 落盘去重账本 + 开机补发(v0.9.26)

CLASS_KEY = "2026-09-22T08:00:高等数学"
YESTERDAY = date(2026, 9, 21)


def _restart():
    """模拟"关掉学长再打开":内存集全清,只有落盘的账本还在。"""
    proactive._mem_pushed.clear()
    proactive._mem_day = ""


def _ledger() -> dict:
    return json.loads(proactive.pushed_path().read_text(encoding="utf-8"))


def _fake_briefing(monkeypatch, payload=None):
    """`payload=None` 才用默认内容 —— 传 {} 就是"今天真没内容",别被 or 吃掉。"""
    from minicode import briefing
    monkeypatch.setattr(briefing, "build", lambda *a, **k: (
        {"text": "今天三节课,午后有雨"} if payload is None else payload))


# ---------------------------------------------------------------- 账本本身

def test_mark_pushed_survives_a_restart():
    """去重集原来只在内存里:重启后同一节课会再弹一次。落盘就是为了这条。"""
    assert proactive.mark_pushed("class", CLASS_KEY) is True
    assert _ledger()["keys"] == [f"{date.today().isoformat()}|class|{CLASS_KEY}"]
    _restart()
    assert proactive.was_pushed("class", CLASS_KEY) is True


def test_ledger_is_day_scoped_and_tolerates_garbage():
    """昨天的账不挡今天;文件坏了/有杂项一律当"没推过"(宁可重弹不可漏弹)。"""
    assert proactive.mark_pushed("class", CLASS_KEY, day=YESTERDAY) is True
    assert proactive.was_pushed("class", CLASS_KEY, day=YESTERDAY) is True
    assert proactive.was_pushed("class", CLASS_KEY) is False      # 今天没推过

    proactive.pushed_path().write_text("这不是 JSON", encoding="utf-8")
    assert proactive.load_pushed() == []
    proactive.pushed_path().write_text(json.dumps({"date": "1999-01-01",
                                                   "keys": ["x"]}),
                                       encoding="utf-8")
    assert proactive.load_pushed() == []                          # 跨天整份作废
    proactive.pushed_path().write_text(json.dumps(
        {"date": proactive._day_key(), "keys": ["好", 42, None]}), encoding="utf-8")
    assert proactive.load_pushed() == ["好"]                      # 非字符串条目丢掉


def test_ledger_caps_the_file_length():
    """一天最多记 PUSHED_CAP 条:账本不能无限长,淘汰最老的。"""
    for i in range(proactive.PUSHED_CAP + 20):
        assert proactive.mark_pushed("class", f"k{i}") is True
    keys = _ledger()["keys"]
    assert len(keys) == proactive.PUSHED_CAP
    assert keys[-1] == f"{proactive._day_key()}|class|k{proactive.PUSHED_CAP + 19}"
    assert not any(k.endswith("|k0") for k in keys)


def test_mark_pushed_falls_back_to_memory_when_the_file_is_unwritable(monkeypatch,
                                                                      tmp_path):
    """只读目录/装在 Program Files 里时写不进账本:如实返回 False,但本次运行
    仍然不重复弹 —— 不能假装"记住了",也不能因此漏推。"""
    blocker = tmp_path / "blocker"
    blocker.write_text("我是文件不是目录", encoding="utf-8")
    monkeypatch.setattr(proactive, "pushed_path",
                        lambda: blocker / "pushed-today.json")
    assert proactive.mark_pushed("class", CLASS_KEY) is False
    assert proactive.was_pushed("class", CLASS_KEY) is True       # 内存兜底
    _restart()
    assert proactive.was_pushed("class", CLASS_KEY) is False      # 重启后确实忘了


def test_fallen_back_keys_get_written_when_the_dir_recovers(monkeypatch, tmp_path):
    """目录恢复可写后,兜底过的键要补进账本 —— 不能"能写了却把之前记的丢了"。"""
    blocker = tmp_path / "blocker"
    blocker.write_text("我是文件不是目录", encoding="utf-8")
    real = proactive.pushed_path
    monkeypatch.setattr(proactive, "pushed_path",
                        lambda: blocker / "pushed-today.json")
    assert proactive.mark_pushed("class", CLASS_KEY) is False     # 只能靠内存
    monkeypatch.setattr(proactive, "pushed_path", real)           # 现在能写了
    assert proactive.mark_pushed("class", "k2") is True
    assert proactive._mem_pushed == set()                         # 写成功就交还给账本
    proactive._mem_day = ""
    assert proactive.was_pushed("class", CLASS_KEY) is True       # 兜底那条真进了账本


# ---------------------------------------------------------------- 简报按天一条

def test_push_briefing_pushes_once_per_day(monkeypatch):
    _fake_briefing(monkeypatch)
    bridge, ui = _push_bridge()
    assert proactive.push_briefing(bridge) is True
    assert ui.events[0]["kind"] == "briefing"
    assert proactive.briefing_pushed_today() is True

    assert proactive.push_briefing(bridge) is False        # 下午再开不重来第二条
    _restart()
    assert proactive.briefing_pushed_today() is True       # 账本落了盘
    assert proactive.push_briefing(bridge) is False
    assert proactive.push_briefing(bridge, force=True) is True   # 手动物力推
    assert len(ui.events) == 2


def test_yesterdays_briefing_does_not_silence_today(monkeypatch):
    proactive.mark_pushed("briefing", "2026-09-21", day=YESTERDAY)
    _fake_briefing(monkeypatch)
    bridge, ui = _push_bridge()
    assert proactive.push_briefing(bridge) is True
    assert len(ui.events) == 1


def test_a_failed_briefing_does_not_consume_the_day(monkeypatch):
    """没真推成就不许占用"今天已推":否则简报一崩,今天再也不会有一条简报。"""
    from minicode import briefing

    def boom(*a, **k):
        raise ConnectionError("聚合炸了")

    monkeypatch.setattr(briefing, "build", boom)
    bridge, _ui = _push_bridge()
    assert proactive.push_briefing(bridge) is False
    assert proactive.briefing_pushed_today() is False
    monkeypatch.setattr(briefing, "build", lambda *a, **k: {})
    assert proactive.push_briefing(bridge) is False              # 今天真没内容
    assert proactive.briefing_pushed_today() is False
    _fake_briefing(monkeypatch)
    assert proactive.push_briefing(bridge) is True


# ---------------------------------------------------------------- 开机补发

def _missed_class(monkeypatch):
    """一节课已经开课 12 分钟、且没提醒过 —— 正是"8:50 才开机"的现场。"""
    from minicode import schedule
    monkeypatch.setattr(schedule, "late_class_note", lambda *a, **k: {
        "text": "错过的提醒:高等数学 08:00 已开课(过了约 12 分钟)",
        "title": "错过的提醒", "key": CLASS_KEY})


def _missed_due(monkeypatch, note=None):
    """一条刚过期还在窗口内的待办(「17:00 前交」结果 17:30 才打开学长)。"""
    from minicode import reminders
    monkeypatch.setattr(reminders, "late_due", lambda *a, **k: note)


DUE_LATE = {"text": "已过点:交实验报告 截止 17:00(过了约 30 分钟)",
            "title": "待办已过期", "key": "r1|2026-09-22T17:00"}


def test_catch_up_backfills_briefing_and_the_missed_class_once(monkeypatch):
    _fake_briefing(monkeypatch)
    _missed_class(monkeypatch)
    _missed_due(monkeypatch)
    bridge, ui = _push_bridge()
    assert proactive.catch_up(bridge) == {"briefing": True, "class": CLASS_KEY,
                                          "due": ""}
    assert [e["kind"] for e in ui.events] == ["briefing", "class"]
    assert proactive.was_pushed("class", CLASS_KEY) is True

    _restart()                                                   # 手滑又开一次
    assert proactive.catch_up(bridge) == {"briefing": False, "class": "",
                                          "due": ""}
    assert len(ui.events) == 2                                   # 一条没重复


def test_catch_up_backfills_an_expired_todo(monkeypatch):
    """到点没赶上(机器没开),开机第一条就该补上 —— 和错过的课同一条路。"""
    _fake_briefing(monkeypatch, payload={})
    from minicode import schedule
    monkeypatch.setattr(schedule, "late_class_note", lambda *a, **k: None)
    _missed_due(monkeypatch, DUE_LATE)
    bridge, ui = _push_bridge()
    assert proactive.catch_up(bridge) == {"briefing": False, "class": "",
                                          "due": DUE_LATE["key"]}
    assert [e["kind"] for e in ui.events] == ["reminders"]
    assert ui.events[0]["title"] == "待办已过期"                 # 卡片抬头说清是哪类


def test_catch_up_skips_a_todo_the_on_time_reminder_already_gave(monkeypatch):
    """补发到点提醒过的待办不能再补一条过期 —— 两边共用那个 `id|到期时刻` 键。"""
    _fake_briefing(monkeypatch, payload={})
    from minicode import schedule
    monkeypatch.setattr(schedule, "late_class_note", lambda *a, **k: None)
    _missed_due(monkeypatch, DUE_LATE)
    assert proactive.mark_pushed("reminders", DUE_LATE["key"]) is True
    bridge, ui = _push_bridge()
    assert proactive.catch_up(bridge)["due"] == ""
    assert ui.events == []


def test_catch_up_skips_a_class_the_on_time_reminder_already_gave(monkeypatch):
    """补发与"快上课了"共用去重键:先到点提醒过的课,开机不再补一条。"""
    _fake_briefing(monkeypatch)
    _missed_class(monkeypatch)
    _missed_due(monkeypatch)
    assert proactive.mark_pushed("class", CLASS_KEY) is True     # 到点提醒弹过了
    bridge, ui = _push_bridge()
    out = proactive.catch_up(bridge)
    assert out == {"briefing": True, "class": "", "due": ""}
    assert [e["kind"] for e in ui.events] == ["briefing"]


def test_catch_up_has_nothing_to_say_and_stays_quiet(monkeypatch):
    """没课/课早就上完/今天没简报内容 → 三个都不推,也不报错。"""
    _fake_briefing(monkeypatch, payload={})
    from minicode import schedule
    monkeypatch.setattr(schedule, "late_class_note", lambda *a, **k: None)
    _missed_due(monkeypatch)
    bridge, ui = _push_bridge()
    assert proactive.catch_up(bridge) == {"briefing": False, "class": "",
                                          "due": ""}
    assert ui.events == []


def test_catch_up_never_breaks_the_watchdog(monkeypatch):
    """补发是加分项:三个数据源一起炸也只能静默,不能把看门狗线程带走。"""
    from minicode import briefing, reminders, schedule

    def boom(*a, **k):
        raise RuntimeError("数据源炸了")

    monkeypatch.setattr(briefing, "build", boom)
    monkeypatch.setattr(schedule, "late_class_note", boom)
    monkeypatch.setattr(reminders, "late_due", boom)
    bridge, ui = _push_bridge()
    assert proactive.catch_up(bridge) == {"briefing": False, "class": "",
                                          "due": ""}
    assert ui.events == []


# ============ 每类都走按天账本 + 看门狗运行日志(v0.9.28 A 批)
#
# 这一节钉的是同一件失败:提醒**只按"本次运行"去重**,于是"重启一次就再弹一遍"
# 和"连着开三天只弹第一天"同时成立。v0.9.26 给今日简报和课前提醒上了按天账本,
# 但 校历/待办/今天的课 三类当时留在内存标志里 —— 这里把剩下的半条路补完。

ALERT = {"name": "国庆", "days_left": 5, "start": "2026-10-01"}
TODO = [{"id": "r1", "title": "交实验报告", "due": "2026-09-25",
         "days_left": 3, "note": ""}]
FIRST_CLASS = {"text": "课表:今天第一节课 08:00 高等数学", "ask": "把今天的课列全",
               "day": "2026-09-22", "head": "今天"}


def _stub_round_sources(monkeypatch, *, near=None, note=None, alerts=None):
    """把 `_round` 的六个外部源都按住,只留"该不该推"这一件真的东西。"""
    monkeypatch.setattr(proactive, "briefing_pushed_today", lambda day=None: False)
    monkeypatch.setattr(proactive, "push_briefing", lambda b, force=False: False)
    monkeypatch.setattr(proactive, "calendar_alerts",
                        lambda *a, **k: list(alerts or []))
    monkeypatch.setattr(proactive, "check_once", lambda state=None: [])
    monkeypatch.setattr(proactive, "check_update", lambda cur: None)
    from minicode import reminders, schedule
    monkeypatch.setattr(reminders, "due_soon", lambda *a, **k: list(near or []))
    monkeypatch.setattr(schedule, "next_class_note", lambda today=None: note)


def test_push_once_is_day_scoped_for_the_three_late_kinds():
    """重启不能再让 校历/待办/今天的课 各多弹一条(以前只有简报和课前有这层锁)。"""
    bridge, ui = _push_bridge()
    assert proactive._push_once(bridge, "calendar", "国庆|2026-10-01", **ALERT) is True
    assert proactive._push_once(bridge, "reminders", "r1", items=TODO) is True
    assert proactive._push_once(bridge, "schedule", "2026-09-22|今天", **FIRST_CLASS) is True
    assert len(ui.events) == 3

    _restart()                                  # 关掉学长再打开
    for kind, key, payload in (("calendar", "国庆|2026-10-01", ALERT),
                               ("reminders", "r1", {"items": TODO}),
                               ("schedule", "2026-09-22|今天", FIRST_CLASS)):
        assert proactive._push_once(bridge, kind, key, **payload) is False
    assert len(ui.events) == 3


def test_a_new_day_asks_again(monkeypatch):
    """账本还是昨天的 → 今天算"没推过":连着开着的进程第二天仍会催一次。"""
    bridge, ui = _push_bridge()
    proactive._push_once(bridge, "calendar", "国庆|2026-10-01", **ALERT)
    data = json.loads(proactive.pushed_path().read_text(encoding="utf-8"))
    assert data["date"] == date.today().isoformat()
    data["date"] = YESTERDAY.isoformat()        # 时间往前走了一天
    proactive.pushed_path().write_text(json.dumps(data), encoding="utf-8")
    assert proactive._push_once(bridge, "calendar", "国庆|2026-10-01", **ALERT) is True
    assert len(ui.events) == 2


def test_push_once_does_not_consume_the_day_when_nothing_arrived(monkeypatch):
    """两路都没送到就不许占用今天这一格 —— 否则这一类今天永远补不回来。"""
    monkeypatch.setattr(proactive, "push_desktop",
                        lambda kind, payload: {"ok": False, "skipped": "no_channel"})
    bridge, ui = _push_bridge(broken=True)
    assert proactive._push_once(bridge, "reminders", "r1", items=TODO) is False
    assert ui.events == []
    assert proactive.was_pushed("reminders", "r1") is False


def test_push_once_logs_the_decision_and_keeps_the_content_out():
    """日志要说清"推没推、为什么",但通知正文(课名/待办标题)一个字都不进。"""
    bridge, _ui = _push_bridge()
    proactive._push_once(bridge, "reminders", "r1", items=TODO)
    proactive._push_once(bridge, "reminders", "r1", items=TODO)
    raw = proactive.watchdog_log_path().read_text(encoding="utf-8")
    rows = proactive.wd_log_tail(10)
    assert any(r.split()[2] == "push" and "reminders" in r for r in rows)
    assert any("今天已推过" in r for r in rows)
    assert "交实验报告" not in raw and "高等数学" not in raw


def test_watchdog_log_trims_to_the_cap(monkeypatch):
    """日志能救命,但不能无限长:超上限只留最近的 LOG_CAP_LINES 行。"""
    monkeypatch.setattr(proactive, "LOG_CAP_LINES", 30)
    monkeypatch.setattr(proactive, "LOG_TRIM_EVERY", 5)
    for i in range(40):
        proactive.wd_log(f"round line {i}")
    rows = proactive.watchdog_log_path().read_text(encoding="utf-8").splitlines()
    assert len(rows) == 30
    assert rows[0].endswith("round line 10") and rows[-1].endswith("round line 39")
    assert proactive.wd_log_tail(3) == rows[-3:]


def test_wd_log_tail_is_quiet_before_the_first_line():
    assert proactive.wd_log_tail(30) == []


def test_round_nags_once_per_day_not_once_per_round(monkeypatch):
    """一轮推齐三类,同一天的第二轮一条都不再来。"""
    _stub_round_sources(monkeypatch, near=TODO, note=FIRST_CLASS, alerts=[ALERT])
    bridge, ui = _push_bridge()
    out = proactive._round(bridge)
    assert sorted(e["kind"] for e in ui.events) == ["calendar", "reminders",
                                                    "schedule"]
    assert out["calendar"] == 1 and out["reminders"] is True and out["schedule"] is True

    proactive._round(bridge)                    # 6 小时后的下一轮
    _restart()
    proactive._round(bridge)                    # 中间还重启过一次
    assert len(ui.events) == 3


def test_round_lets_the_briefing_cover_the_singles_and_says_so(monkeypatch):
    """简报已推过 → 三条单条让路,但**要在日志里写明是被简报顶掉的**,不是没跑。"""
    _stub_round_sources(monkeypatch, near=TODO, note=FIRST_CLASS, alerts=[ALERT])
    monkeypatch.setattr(proactive, "briefing_pushed_today", lambda day=None: True)
    bridge, ui = _push_bridge()
    out = proactive._round(bridge)
    assert out["briefing"] is True
    assert "calendar" not in out and "reminders" not in out and "schedule" not in out
    assert "weather" in out                # v0.9.43 起「明天有雨」独立于简报(简报管今天)
    assert ui.events == []
    assert any("今日简报已覆盖" in r for r in proactive.wd_log_tail(10))


def test_round_logs_a_broken_source_instead_of_vanishing(monkeypatch):
    """数据源炸了表现为"什么也没发生" —— 至少要在日志里留下是哪一类炸的。"""
    _stub_round_sources(monkeypatch, alerts=[ALERT])
    from minicode import reminders, schedule

    def boom(*a, **k):
        raise RuntimeError("文件坏了")

    monkeypatch.setattr(reminders, "due_soon", boom)
    monkeypatch.setattr(schedule, "next_class_note", boom)
    bridge, ui = _push_bridge()
    out = proactive._round(bridge)
    assert out["reminders"] is False and out["schedule"] is False
    assert out["calendar"] == 1                 # 一类炸不影响另一类
    rows = "\n".join(proactive.wd_log_tail(10))
    assert "fail reminders" in rows and "fail schedule" in rows


# ============ 到点提醒器:课前 + 待办到点同一条细轮询(v0.9.29 B 批)
#
# 这一节钉的是"扩展到所有临期事项"那半条路。最容易做错的四件:
# ①新那一路忘了走按天账本(重启就重复催);②把待办到点发成用户关不掉的第八类;
# ③`key` 混进 payload 撞成守护线程里的 TypeError(课前提醒整条消失,还看不见报错);
# ④两路里的一路炸了把另一路一起带走。

DUE_SOON_NOTE = {"text": "快到点了:交实验报告 截止 17:00(还有约 10 分钟)",
                 "ask": "交实验报告 在 17:00 截止,还有约 10 分钟,我现在该做什么?",
                 "title": "待办到点", "id": "r1", "name": "交实验报告",
                 "time": "17:00", "minutes_left": 10,
                 "key": "r1|2026-09-22T17:00"}
CLASS_SOON = {"text": "快上课了:高等数学 08:00 开始(还有约 15 分钟)",
              "ask": "把要带的东西列一下", "title": "快上课了",
              "name": "高等数学", "time": "08:00", "location": "A203",
              "minutes_left": 15, "key": "2026-09-22T08:00:高等数学"}


def _stub_tick(monkeypatch, *, cls=CLASS_SOON, due=DUE_SOON_NOTE):
    """按住到点提醒器的两个数据源,只留"该不该推"这一件真的东西。"""
    from minicode import reminders, schedule
    monkeypatch.setattr(schedule, "upcoming_class", lambda *a, **k: cls)
    monkeypatch.setattr(reminders, "upcoming_due", lambda *a, **k: due)


def test_push_due_goes_out_as_the_reminders_kind_the_user_can_toggle(monkeypatch):
    """类别必须是既有的 `reminders`:凭空多一类他关不掉,到点催就成了强制打扰。"""
    _stub_tick(monkeypatch, cls=None)
    bridge, ui = _push_bridge()
    assert proactive._push_due(bridge) is True
    assert ui.events[0]["kind"] == "reminders"
    assert ui.events[0]["title"] == "待办到点"
    assert "17:00" in ui.events[0]["text"]
    assert "key" not in ui.events[0]                 # 去重键不是要显示的内容


def test_push_due_is_day_scoped_so_a_restart_cannot_double_pop(monkeypatch):
    _stub_tick(monkeypatch, cls=None)
    bridge, ui = _push_bridge()
    assert proactive._push_due(bridge) is True
    _restart()
    assert proactive._push_due(bridge) is False      # 重启:同一刻不再催第二遍
    assert proactive.was_pushed("reminders", DUE_SOON_NOTE["key"]) is True
    assert len(ui.events) == 1


def test_a_new_deadline_time_rearms_the_push(monkeypatch):
    """去重键带到期时刻:用户把 17:00 改成 18:00,那条新截止还得催得到。"""
    later = {**DUE_SOON_NOTE, "key": "r1|2026-09-22T18:00"}
    _stub_tick(monkeypatch, cls=None, due=later)
    bridge, ui = _push_bridge()
    assert proactive._push_due(bridge) is True
    assert proactive._push_due(bridge) is False
    assert len(ui.events) == 1


def test_push_due_stays_quiet_without_a_clock(monkeypatch):
    """只有日期的事项由按天那一路管,细轮询这里不能凭空造一个时刻出来。"""
    _stub_tick(monkeypatch, cls=None, due=None)
    bridge, ui = _push_bridge()
    assert proactive._push_due(bridge) is False
    assert ui.events == []


def test_tick_pushes_class_and_todo_independently(monkeypatch):
    """两路各推各的:一节课和一条待办同时在 20 分钟内,该听到两条。"""
    _stub_tick(monkeypatch)
    bridge, ui = _push_bridge()
    assert proactive._tick(bridge) == {"class": True, "due": True, "jobs": []}
    assert [e["kind"] for e in ui.events] == ["class", "reminders"]


def test_tick_survives_one_broken_source(monkeypatch):
    """课表读炸了不能连累待办到点 —— 一条线程管两类,就得两类各自兜住。"""
    from minicode import reminders, schedule
    monkeypatch.setattr(schedule, "upcoming_class",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("课表坏了")))
    monkeypatch.setattr(reminders, "upcoming_due", lambda *a, **k: DUE_SOON_NOTE)
    bridge, ui = _push_bridge()
    assert proactive._tick(bridge) == {"class": False, "due": True, "jobs": []}
    assert [e["kind"] for e in ui.events] == ["reminders"]
    assert any("fail class" in r for r in proactive.wd_log_tail(10))


def test_the_tick_loop_is_what_the_gui_starts():
    """改名的那一头:线程改名了却没改调用点 = 打包版没有任何到点提醒。"""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "gui_main.py"
           ).read_text(encoding="utf-8")
    assert "start_tick_loop" in src and "start_class_loop" not in src


def test_desktop_text_shows_the_clock_when_the_todo_has_one():
    """同一件事两处各写一套会各说各话:桌面通知跟着 `due_at` 走。"""
    items = [{"id": "r1", "title": "交实验报告", "due": "2026-09-22",
              "due_at": "2026-09-22T17:00", "days_left": 0, "note": ""}]
    assert proactive.desktop_text("reminders", {"items": items}) \
        == "交实验报告(2026-09-22 17:00,还有 0 天)"
    # 只有日期的条目不编钟点
    items[0]["due_at"] = ""
    assert proactive.desktop_text("reminders", {"items": items}) \
        == "交实验报告(2026-09-22,还有 0 天)"


# ---------------------------------------------------------------- 明日雨提醒(v0.9.43)

def _write_weather_cache(tmp_path, *, rain=True):
    """按真实今天造 4 天预报缓存(明日雨 → 明天那格给雨码+高概率)。"""
    from datetime import timedelta
    from minicode import paths, weather
    import time as _time
    today = date.today()
    days = [(today + timedelta(days=i)).isoformat() for i in range(4)]
    data = {"current": {"temperature_2m": 27, "apparent_temperature": 28,
                        "precipitation": 0.0},
            "daily": {"time": days,
                      "weather_code": [2, 61 if rain else 0, 0, 0],
                      "temperature_2m_max": [31, 30, 30, 30],
                      "temperature_2m_min": [24, 24, 24, 24],
                      "precipitation_probability_max": [10, 70 if rain else 10, 20, 20]}}
    p = paths.APP_DIR / weather.CACHE_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"ts": _time.time(), "data": data}), encoding="utf-8")


def test_push_weather_pushes_once_and_dedupes(tmp_path):
    _write_weather_cache(tmp_path, rain=True)
    bridge, ui = _push_bridge()
    assert proactive._push_weather(bridge) is True
    assert [e["kind"] for e in ui.events] == ["weather"]
    assert "预报有雨" in ui.events[0]["text"] and "伞" in ui.events[0]["text"]
    # 同一天再查(重启后同日):账本去重,不再弹
    assert proactive._push_weather(bridge) is False
    assert len(ui.events) == 1


def test_push_weather_skips_without_cache(tmp_path):
    """没缓存/过期就跳过 —— 提醒绝不为检查天气联网。"""
    bridge, ui = _push_bridge()
    assert proactive._push_weather(bridge) is False
    assert ui.events == []
    assert any("不为提醒联网" in r for r in proactive.wd_log_tail(10))


def test_push_weather_quiet_when_no_rain_tomorrow(tmp_path):
    _write_weather_cache(tmp_path, rain=False)
    bridge, ui = _push_bridge()
    assert proactive._push_weather(bridge) is False
    assert ui.events == []


def test_weather_kind_in_notify_kinds():
    """新类别必须进 tray 的类别表,否则用户关不掉(七类扩成八类)。"""
    from minicode import tray
    assert "weather" in tray.NOTIFY_KINDS
    assert tray.KIND_LABELS.get("weather") == "天气提醒"


# ============ 定时任务表接线(v0.9.56 A2)
#
# 底座(v0.9.42 的 jobs.py)只有纯函数和账本,这里钉的是接线本身:两条心跳各过
# 一遍任务表、跑了记账、没接线的如实说、异常也算"跑过这一班"。表在 A4 之前是
# 空的 —— 所以先钉"空表零开销",再用假 spec 把整条路走到。

def test_run_due_jobs_is_a_no_op_with_an_empty_table():
    """现状(还没有登记任何任务)连账本文件都不许碰。"""
    from minicode import jobs as _jobs
    bridge, _ui = _push_bridge()
    assert proactive._run_due_jobs(bridge, now=datetime(2026, 9, 28, 9, 0)) == []
    assert not _jobs.state_path().exists()


def _register_job(monkeypatch, spec, handler=None):
    monkeypatch.setattr(proactive, "JOB_SPECS", [spec])
    monkeypatch.setattr(proactive, "JOB_HANDLERS",
                        {spec.id: handler} if handler else {})


def test_a_due_job_runs_its_handler_and_books_the_ledger(monkeypatch):
    from minicode import jobs as _jobs
    seen = []
    _register_job(monkeypatch, _jobs.JobSpec("digest", cadence="daily", hour=8),
                  handler=lambda b: (seen.append(b), "已汇总")[1])
    bridge, _ui = _push_bridge()
    now = datetime(2026, 9, 28, 9, 0)
    assert proactive._run_due_jobs(bridge, now=now) == ["digest"]
    assert seen == [bridge]
    assert _jobs.last_run("digest") == now
    row = _jobs.load_state()["digest"]
    assert row["runs"] == 1 and row["result"] == "已汇总"
    assert proactive._run_due_jobs(bridge, now=now) == []     # 同一天不再到期


def test_a_job_before_its_hour_waits(monkeypatch):
    from minicode import jobs as _jobs
    _register_job(monkeypatch, _jobs.JobSpec("digest", cadence="daily", hour=8),
                  handler=lambda b: "已汇总")
    bridge, _ui = _push_bridge()
    assert proactive._run_due_jobs(bridge, now=datetime(2026, 9, 28, 7, 59)) == []
    assert _jobs.last_run("digest") is None


def test_a_spec_without_a_handler_is_reported_not_swallowed(monkeypatch):
    """登记了却没接线的任务要留一行现场,且不记账 —— 不会假装跑过。"""
    from minicode import jobs as _jobs
    _register_job(monkeypatch, _jobs.JobSpec("digest", cadence="daily", hour=8))
    bridge, _ui = _push_bridge()
    assert proactive._run_due_jobs(bridge, now=datetime(2026, 9, 28, 9, 0)) == []
    assert any("未跑[没接线]" in r for r in proactive.wd_log_tail(10))
    assert _jobs.last_run("digest") is None


def test_a_broken_handler_still_counts_as_run(monkeypatch):
    """宁可这一期不发,也不要每 5 分钟重试一次:异常也记账,result 留异常名。"""
    from minicode import jobs as _jobs

    def boom(b):
        raise RuntimeError("源炸了")

    _register_job(monkeypatch, _jobs.JobSpec("digest", cadence="daily", hour=8),
                  handler=boom)
    bridge, _ui = _push_bridge()
    now = datetime(2026, 9, 28, 9, 0)
    assert proactive._run_due_jobs(bridge, now=now) == ["digest"]
    row = _jobs.load_state()["digest"]
    assert row["runs"] == 1 and "RuntimeError" in row["result"]
    assert _jobs.last_run("digest") == now


def test_a_failed_ledger_write_is_reported_in_the_log(monkeypatch, tmp_path):
    """handler 成了但账本写不进去,日志要如实说 —— 不能看起来一切正常。"""
    from minicode import jobs as _jobs
    _register_job(monkeypatch, _jobs.JobSpec("digest", cadence="daily", hour=8),
                  handler=lambda b: "已汇总")
    blocker = tmp_path / "blocker"
    blocker.write_text("我是个文件,不是目录", encoding="utf-8")
    monkeypatch.setattr(_jobs, "state_path",
                        lambda: blocker / "sub" / _jobs.STATE_FILE)
    bridge, _ui = _push_bridge()
    assert proactive._run_due_jobs(bridge, now=datetime(2026, 9, 28, 9, 0)) == ["digest"]
    assert any("账本没落盘" in r for r in proactive.wd_log_tail(10))


def test_tick_sweeps_repeats_and_the_job_table(monkeypatch):
    """_tick 一轮的三件事:循环待办滚账、课前/待办照旧、任务表过一遍。"""
    from minicode import reminders
    _stub_tick(monkeypatch, cls=None, due=None)
    reminders.add("交周记", due="2026-10-07", at="17:00", repeat="每周")
    bridge, _ui = _push_bridge()
    out = proactive._tick(bridge, now=datetime(2026, 10, 9, 10, 0))
    assert out == {"class": False, "due": False, "jobs": []}
    item = reminders.list_items()[0]
    assert item["due"] == "2026-10-14" and item["projected"] is True


def test_round_also_sweeps_the_job_table(monkeypatch):
    _stub_round_sources(monkeypatch)
    bridge, _ui = _push_bridge()
    assert proactive._round(bridge)["jobs"] == []
