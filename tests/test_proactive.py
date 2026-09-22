"""主动推荐(proactive)测试:标题清洗 / 列表页解析 / 快照去重 / 校历提醒 / 开关。"""
import json
from datetime import date

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
    from minicode import paths
    monkeypatch.setattr(paths, "APP_DIR", tmp_path / "app")
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
