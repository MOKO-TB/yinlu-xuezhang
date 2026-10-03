"""token 用量记账与统计(minicode/usage.py)。

最后那组针盯的是一件很安静的事:**账上写的模型必须是真跑了请求那一个**。
v0.9.95 起子代理可按档位换模型、会话内也能切模型,两种情况下 `ctx.config.model`
都还是全局那一个 —— 拿它记账,「每日用量」按模型分开的那张图就会把便宜档的字
算到主模型头上,而界面那句"这个模型用了 N tokens"就成了没有来源的数字。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from minicode import usage


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path):
    usage.set_db_path(tmp_path / "tok.db")
    yield
    usage.set_db_path(None)


def test_record_and_statistics(tmp_path):
    assert usage.record_usage("gzasc", "qwen3.8-27b",
                              {"prompt_tokens": 100, "completion_tokens": 20})
    assert usage.record_usage("gzasc", "qwen3.8-27b",
                              {"prompt_tokens": 50, "completion_tokens": 10,
                               "total_tokens": 60})
    assert usage.record_usage("openai", "gpt-x",
                              {"prompt_tokens": 5, "completion_tokens": 5})
    s = usage.statistics(7)
    assert s["ok"]
    assert s["summary"] == {"prompt": 155, "completion": 35, "total": 190,
                            "requests": 3}
    # 按天补零:7 天序列,只有最后一天有量
    assert [d["total"] for d in s["days"]] == [0] * 6 + [190]
    # 模型按总额降序
    assert [(m["key"], m["total"]) for m in s["models"]] == [
        ("gzasc/qwen3.8-27b", 180), ("openai/gpt-x", 10)]
    # 当天明细含模型切片(圆环图数据源;同模型同参数各记一条,倒序在前列)
    today = s["days"][-1]
    keys = [m["key"] for m in today["models"]]
    assert keys.count("gzasc/qwen3.8-27b") == 2 and "openai/gpt-x" in keys
    assert keys[0] == "gzasc/qwen3.8-27b"


def test_record_rejects_missing_or_invalid_usage():
    """宁可不记,不猜:usage 缺失/字段非法一律跳过。"""
    assert not usage.record_usage("p", "m", None)
    assert not usage.record_usage("p", "m", {})
    assert not usage.record_usage("p", "m", {"prompt_tokens": 10})          # 缺 completion
    assert not usage.record_usage("p", "m", {"prompt_tokens": -1,
                                              "completion_tokens": 0})       # 负数
    assert not usage.record_usage("p", "m", {"prompt_tokens": True,
                                              "completion_tokens": 0})       # bool 不是数
    assert usage.statistics(7)["summary"]["total"] == 0


def test_total_derived_when_missing():
    assert usage.record_usage("p", "m", {"prompt_tokens": 7,
                                         "completion_tokens": 3})
    s = usage.statistics(1)
    assert s["summary"]["total"] == 10


def test_statistics_validates_range():
    r = usage.statistics(0)
    assert not r["ok"] and "1、7、30" in r["msg"]
    r = usage.statistics(365)
    assert not r["ok"]


def test_statistics_survives_db_error(monkeypatch):
    def boom(days):
        raise sqlite3.Error("locked")
    monkeypatch.setattr(usage, "_rows", boom)
    r = usage.statistics(7)
    assert not r["ok"] and "locked" in r["msg"]


def test_db_follows_injected_path(tmp_path):
    """库文件落在注入路径(打包版=exe 旁,跟随便携目录)。"""
    p = tmp_path / "tok.db"
    usage.set_db_path(p)
    assert usage.record_usage("p", "m", {"prompt_tokens": 1,
                                         "completion_tokens": 1})
    usage.set_db_path(None)          # 关连接后文件应已落盘
    assert p.is_file() and p.stat().st_size > 0


def test_agent_records_usage_from_turn(tmp_path, config):
    """Agent 主循环:有 usage 的回合记账,没有的不记。"""
    from minicode.agent import Agent
    from minicode.llm import AssistantTurn
    from minicode.session import Session
    from minicode.tools.registry import build_default_registry
    from minicode.tools.base import ToolContext

    usage.set_db_path(tmp_path / "tok.db")
    ctx = ToolContext(workspace=tmp_path, config=config)

    class CollectUI:
        def __getattr__(self, name):          # 吞掉所有 UI 回调
            return lambda *a, **k: None

    from minicode.permission import PermissionGate
    agent = Agent(llm=FakeLLMWithUsage([
        AssistantTurn(text="hi", usage={"prompt_tokens": 12,
                                        "completion_tokens": 3}),
        AssistantTurn(text="no usage"),       # 服务端没给 usage → 不记账
    ]), registry=build_default_registry(),
        session=Session("sys"), gate=PermissionGate(config),
        ui=CollectUI(), ctx=ctx)
    agent.run_turn("q")
    s = usage.statistics(1)
    assert s["summary"] == {"prompt": 12, "completion": 3, "total": 15,
                            "requests": 1}


class FakeLLMWithUsage:
    def __init__(self, turns):
        self.turns = list(turns)

    def stream_chat(self, messages, tools, on_text=None, on_reasoning=None,
                    on_retry=None, **kwargs):   # v0.9.108:agent 会传 fallback/on_fallback
        return self.turns.pop(0)


class _PushUI:
    """只记账的界面替身:抓 `turn_usage` 事件里的模型名。"""

    def __init__(self):
        self.events = []

    def push(self, type_, **payload):
        self.events.append(dict(payload, type=type_))


def _agent(config, llm, ui=None):
    from minicode.agent import Agent
    from minicode.permission import PermissionGate
    from minicode.session import Session
    from minicode.tools.base import ToolContext
    got = ui or _PushUI()
    return Agent(llm=llm, registry=[], session=Session(system_prompt="sys"),
                 gate=PermissionGate(config), ui=got,
                 ctx=ToolContext(workspace=Path("."), config=config, ui=got))


def _rows():
    db = usage.db_path()
    with sqlite3.connect(str(db)) as c:
        return list(c.execute("SELECT provider, model, total_tokens FROM token_usage"))


def test_record_usage_writes_the_model_that_actually_ran(config):
    """按档位换模型时,账上写的是换后的名字,不是配置里那个主模型。"""
    from minicode.llm import LLMClient, AssistantTurn
    scoped = LLMClient.scoped(config, model="cheap-fast")
    agent = _agent(config, scoped)
    assert config.model == "test-model"          # 全局那份没变(下面才看得出没记错)
    agent._record_usage(AssistantTurn(usage={"prompt_tokens": 40,
                                             "completion_tokens": 8}))
    assert _rows() == [("test", "cheap-fast", 48)]


def test_record_usage_falls_back_when_llm_has_no_config(config):
    """测试桩/假 LLM 没有 config:退回 ctx.config,不炸也不记错成空。"""
    from minicode.llm import AssistantTurn

    class _Fake:
        pass

    agent = _agent(config, _Fake())
    agent._record_usage(AssistantTurn(usage={"prompt_tokens": 1, "completion_tokens": 1}))
    assert _rows() == [("test", "test-model", 2)]


def test_turn_usage_event_reports_the_running_model(config):
    """任务面板那条 `turn_usage` 事件同一个口径:模型名跟着真客户端走。"""
    from minicode.llm import LLMClient, AssistantTurn
    ui = _PushUI()
    agent = _agent(config, LLMClient.scoped(config, model="cheap-fast"), ui=ui)
    agent._bump_turn_tokens(AssistantTurn(usage={"prompt_tokens": 10,
                                                 "completion_tokens": 5}))
    events = [e for e in ui.events if e["type"] == "turn_usage"]
    assert len(events) == 1 and events[0]["model"] == "cheap-fast"
