"""Bridge 层:消息反馈(rate_message)与首页倒计时(get_countdowns)。"""
import json

import pytest

from minicode import growth
from minicode.gui_bridge import create_app
from minicode.llm import AssistantTurn


@pytest.fixture(autouse=True)
def _isolated_paths(tmp_path, monkeypatch):
    """growth 落盘走 minicode.paths.APP_DIR(conftest 只钉了 gui_bridge 的)。"""
    from minicode import paths
    monkeypatch.setattr(paths, "APP_DIR", tmp_path / "app")
    growth._SEEN_GAPS.clear()
    yield
    growth._SEEN_GAPS.clear()


def make(config, tmp_path):
    bridge, ui, _agent, _session = create_app(
        config, tmp_path, llm=FakeLLM(),
        sessions_dir=tmp_path / "sessions", config_path=tmp_path / "config.json")
    return bridge


class FakeLLM:
    def stream_chat(self, messages, tools, on_text=None, on_reasoning=None, on_retry=None):
        return AssistantTurn(text="")


def test_rate_message_writes_feedback_log(config, tmp_path):
    bridge = make(config, tmp_path)
    assert bridge.rate_message(1, "宿舍几点断电", "23:00")["ok"] is True
    assert bridge.rate_message(-1, "错误答案的问题", "错误答案")["ok"] is True
    path = tmp_path / "app" / growth.FEEDBACK_FILE
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert [r["rating"] for r in rows] == [1, -1]
    assert rows[0]["question"] == "宿舍几点断电"
    assert rows[0]["session"]                     # 带上会话留档文件名便于回溯


def test_rate_message_rejects_zero(config, tmp_path):
    bridge = make(config, tmp_path)
    result = bridge.rate_message(0, "q", "a")
    assert result["ok"] is False
    assert not (tmp_path / "app" / growth.FEEDBACK_FILE).exists()


def test_rate_message_via_public_api(config, tmp_path):
    bridge = make(config, tmp_path)
    api = bridge.public_api()
    assert api.rate_message(1, "q", "a")["ok"] is True
    assert api.get_countdowns()["ok"] is True


def test_get_countdowns_ok_even_if_calendar_broken(config, tmp_path, monkeypatch):
    from minicode import calendar as cal
    bridge = make(config, tmp_path)
    assert bridge.get_countdowns()["ok"] is True          # 仓库种子文件可用
    monkeypatch.setattr(cal, "load_events", lambda: ([], "broken"))
    payload = bridge.get_countdowns()
    assert payload["ok"] is True and payload["countdowns"] == []


def test_rate_message_binds_kb_ids_from_last_user_window(config, tmp_path, monkeypatch):
    """评价绑条目(v0.9.80):取"最后一条用户消息之后"的检索命中,写进反馈记录;
    取不到(老留档没 ts)就不绑 —— 宁可不记,不硬凑归因。"""
    bridge = make(config, tmp_path)
    monkeypatch.setattr(growth, "last_hits_since", lambda ts: ["scholar-1", "guide-2"])
    bridge.session.add_user("奖学金怎么申请")          # 真实消息带 ts 才会取窗口
    result = bridge.rate_message(1, "奖学金怎么申请", "先看看条件…")
    assert result["ok"] and result["kb_ids"] == ["scholar-1", "guide-2"]
    rows = [json.loads(x) for x in
            (tmp_path / "app" / growth.FEEDBACK_FILE).read_text(
                encoding="utf-8").splitlines() if x.strip()]
    assert rows[0]["kb_ids"] == ["scholar-1", "guide-2"]

    # 会话里没有带 ts 的用户消息:窗口取不到,记录不带 kb_ids 字段
    monkeypatch.setattr(growth, "last_hits_since", lambda ts: [])
    bridge.rate_message(-1, "另一个问题", "答得不好")
    rows = [json.loads(x) for x in
            (tmp_path / "app" / growth.FEEDBACK_FILE).read_text(
                encoding="utf-8").splitlines() if x.strip()]
    assert "kb_ids" not in rows[1]
