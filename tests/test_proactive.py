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
