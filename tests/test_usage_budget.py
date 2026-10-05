"""日预算(C1,v0.9.108)测试:状态三档、当天去重、CLI/GUI 载荷。"""

import pytest

from minicode import usage as usage_mod
from minicode.config import Config, Provider


@pytest.fixture
def _usage_db(tmp_path, monkeypatch):
    monkeypatch.setattr(usage_mod, "set_db_path", None) if False else None
    monkeypatch.setattr(usage_mod, "db_path", lambda: tmp_path / "usage.db")
    usage_mod.set_db_path(tmp_path / "usage.db")
    yield tmp_path
    usage_mod.set_db_path(None)


def test_budget_state_off_and_levels(tmp_path, monkeypatch):
    monkeypatch.setattr(usage_mod, "db_path", lambda: tmp_path / "u.db")
    usage_mod.set_db_path(tmp_path / "u.db")
    usage_mod.record_usage("p", "m", {"prompt_tokens": 500, "completion_tokens": 300})
    # 0 = 不启用:只报今天的数
    st = usage_mod.budget_state(0)
    assert st == {"enabled": False, "spent": 800, "limit": 0,
                  "ratio": 0.0, "level": "off"}
    # 80%~100% = near
    st = usage_mod.budget_state(1000)
    assert st["level"] == "near" and st["remaining"] == 200
    # >=100% = over
    st = usage_mod.budget_state(800)
    assert st["level"] == "over" and st["remaining"] == 0
    # <80% = ok
    st = usage_mod.budget_state(5000)
    assert st["level"] == "ok"


def test_agent_notes_budget_once_per_level(tmp_path, monkeypatch):
    """回合开始提醒:接近/超支各一次,当天不重复;预算未启用零动作。"""
    from minicode.agent import Agent
    from minicode.config import Config, Provider
    from minicode.permission import PermissionGate
    from minicode.session import Session
    from minicode.tools.base import ToolContext
    from minicode import usage as usage_mod

    class UI:
        def __init__(self): self.infos = []
        def push(self, type_, **kw):
            if type_ == "info": self.infos.append(kw.get("text", ""))
        def __getattr__(self, name): return lambda *a, **k: None

    cfg = Config(providers=[Provider(id="t", name="T", base_url="https://x/v1",
                                     api_key="k", model="m")],
                 active_provider_id="t", daily_token_budget=1000)
    monkeypatch.setattr(usage_mod, "db_path", lambda: tmp_path / "u.db")
    usage_mod.set_db_path(tmp_path / "u.db")
    usage_mod.record_usage("p", "m", {"prompt_tokens": 700, "completion_tokens": 150})
    ui = UI()
    agent = Agent(llm=None, registry=[],
                  session=Session(system_prompt="s", log_dir=None),
                  gate=PermissionGate(cfg), ui=ui,
                  ctx=ToolContext(workspace=tmp_path, config=cfg), max_steps=1)
    agent._note_daily_budget()
    agent._note_daily_budget()
    near = [t for t in ui.infos if "日预算提醒" in t]
    assert len(near) == 1 and "还剩 150" in near[0]   # 850/1000 = 85%,near 档只提醒一次
    # 越过 100%:超支一档另行提醒
    usage_mod.record_usage("p", "m", {"prompt_tokens": 200, "completion_tokens": 100})
    agent._note_daily_budget()
    over = [t for t in ui.infos if "超支" in t]
    assert len(over) == 1
    agent._note_daily_budget()
    assert len([t for t in ui.infos if "日预算提醒" in t or "超支" in t]) == 2


def test_get_token_usage_carries_today_and_budget(tmp_path, monkeypatch):
    from minicode.gui_bridge import Bridge
    from minicode import usage as usage_mod
    monkeypatch.setattr(usage_mod, "db_path", lambda: tmp_path / "u.db")
    usage_mod.set_db_path(tmp_path / "u.db")
    usage_mod.record_usage("p", "test-model", {"prompt_tokens": 100,
                                               "completion_tokens": 50})
    cfg = Config(providers=[Provider(id="t", name="T", base_url="https://x/v1",
                                     api_key="k", model="test-model")],
                 active_provider_id="t", daily_token_budget=5000)
    bridge = Bridge.__new__(Bridge)
    bridge.config = cfg
    out = bridge.get_token_usage(7)
    assert out["ok"] is True
    assert out["today"]["total"] == 150
    assert out["today"]["budget"]["enabled"] is True and out["today"]["budget"]["limit"] == 5000


def test_get_usage_dashboard_carries_today_and_budget(tmp_path, monkeypatch):
    """v0.9.117 用量示意改版门面:dashboard 一揽子 + 同一份今日预算。"""
    from minicode.gui_bridge import Bridge
    from minicode import usage as usage_mod
    monkeypatch.setattr(usage_mod, "db_path", lambda: tmp_path / "u.db")
    usage_mod.set_db_path(tmp_path / "u.db")
    usage_mod.record_usage("p", "test-model", {"prompt_tokens": 100,
                                               "completion_tokens": 50})
    cfg = Config(providers=[Provider(id="t", name="T", base_url="https://x/v1",
                                     api_key="k", model="test-model")],
                 active_provider_id="t", daily_token_budget=5000)
    bridge = Bridge.__new__(Bridge)
    bridge.config = cfg
    out = bridge.get_usage_dashboard("week")
    assert out["ok"] is True
    assert out["summary"]["total"] == 150
    assert out["today"]["total"] == 150
    assert out["today"]["budget"]["enabled"] is True
    assert out["today"]["budget"]["limit"] == 5000
