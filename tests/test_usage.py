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

from minicode import pricing, usage


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
    # W0/W2 起摘要多带耗时/重试/会话数与估算费用:记账核心照旧钉全等;
    # 费用两个键只钉"在"(数值口径归 test_pricing,不在这张表里重复一把尺)。
    core = {k: v for k, v in s["summary"].items()
            if k not in ("cost_usd", "cost_cny", "unpriced_models")}
    assert core == {"prompt": 155, "completion": 35, "total": 190,
                    "requests": 3, "elapsed_ms": 0, "retries": 0,
                    "sessions": 0}
    assert "cost_usd" in s["summary"] and "unpriced_models" in s["summary"]
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
    core = {k: v for k, v in s["summary"].items()
            if k not in ("cost_usd", "cost_cny", "unpriced_models")}
    assert core == {"prompt": 12, "completion": 3, "total": 15,
                    "requests": 1, "elapsed_ms": 0, "retries": 0,
                    "sessions": 0}


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


# ------------------------------------------------------------------ W0 三列

def test_old_database_gains_the_new_columns_on_reopen(tmp_path):
    """W0 迁移:三列之前的库挂进来,自动补列 —— 老行照读、新行照记,不重建。"""
    import sqlite3
    import time as _time

    p = tmp_path / "old.db"
    conn = sqlite3.connect(p)
    conn.execute(
        "CREATE TABLE token_usage ("
        "  ts INTEGER NOT NULL, date TEXT NOT NULL,"
        "  provider TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '',"
        "  prompt_tokens INTEGER NOT NULL DEFAULT 0,"
        "  completion_tokens INTEGER NOT NULL DEFAULT 0,"
        "  total_tokens INTEGER NOT NULL DEFAULT 0)")
    conn.execute(
        "INSERT INTO token_usage (ts, date, provider, model, prompt_tokens,"
        " completion_tokens, total_tokens) VALUES (0, ?, 'p', 'm', 10, 5, 15)",
        (_time.strftime("%Y-%m-%d"),))
    conn.commit()
    conn.close()
    usage.set_db_path(p)
    try:
        assert usage.record_usage("p", "m",
                                  {"prompt_tokens": 1, "completion_tokens": 1},
                                  session_id="session_x", elapsed_ms=1500,
                                  retries=2, ttft_ms=420, reasoning_tokens=9)
        s = usage.statistics(7)
        assert s["ok"]
        assert s["summary"]["retries"] == 2
        assert s["summary"]["elapsed_ms"] == 1500
        assert s["summary"]["sessions"] == 1
        assert s["summary"]["total"] == 17          # 老行(10+5) + 新行(1+1) 都在
        # W5/W6 两列同样要补上:老库缺谁 ALTER 谁
        m = s["models"][0]
        assert m["ttft_samples"] == 1 and m["ttft_ms"] == 420
        assert m["reasoning_samples"] == 1 and m["reasoning_tokens"] == 9
    finally:
        usage.set_db_path(None)


def test_session_count_skips_rows_without_an_archive(tmp_path):
    """「有账的会话数」只数有留档身份的行:子代理/纯内存会话不进来凑数。"""
    usage.set_db_path(tmp_path / "tok.db")
    try:
        usage.record_usage("p", "m", {"prompt_tokens": 1, "completion_tokens": 1},
                           session_id="s1")
        usage.record_usage("p", "m", {"prompt_tokens": 1, "completion_tokens": 1},
                           session_id="s1")
        usage.record_usage("p", "m", {"prompt_tokens": 1, "completion_tokens": 1},
                           session_id="s2")
        usage.record_usage("p", "m",
                           {"prompt_tokens": 1, "completion_tokens": 1})  # 无留档
        assert usage.statistics(7)["summary"]["sessions"] == 2
    finally:
        usage.set_db_path(None)


def test_retries_ride_on_the_next_recorded_request(tmp_path, config):
    """W1:重试真实发生过 —— 这次请求没 usage,计数挂到下一条有账的记录上,
    不让它凭空消失;记上账的那一刻清零。"""
    from minicode.llm import AssistantTurn

    class CollectUI:
        def __getattr__(self, name):
            return lambda *a, **k: None

    class RetryThenUsage:
        def __init__(self):
            self.n = 0

        def stream_chat(self, messages, tools, on_text=None, on_reasoning=None,
                        on_retry=None, **kwargs):
            self.n += 1
            if self.n == 1:
                if on_retry is not None:
                    on_retry(RuntimeError("连接断了"), 1)
                return AssistantTurn(text="掉线重来")        # 无 usage → 不记账
            return AssistantTurn(text="ok", usage={"prompt_tokens": 5,
                                                   "completion_tokens": 1})

    usage.set_db_path(tmp_path / "tok.db")
    try:
        agent = _agent(config, RetryThenUsage(), ui=CollectUI())
        agent.run_turn("q1")
        assert usage.statistics(1)["summary"]["requests"] == 0   # 没账可记
        agent.run_turn("q2")
        s = usage.statistics(1)
        assert s["summary"]["requests"] == 1
        assert s["summary"]["retries"] == 1          # 上一轮那次重试挂在这一条上
    finally:
        usage.set_db_path(None)


def test_session_id_is_the_archive_stem_or_empty(tmp_path, config):
    """W0:会话身份 = 留档文件名;没有留档的运行(子代理)是空串。"""
    import sqlite3
    from minicode.llm import AssistantTurn
    from minicode.session import Session
    from minicode.tools.base import ToolContext
    from minicode.tools.registry import build_default_registry
    from minicode.permission import PermissionGate
    from minicode.agent import Agent

    class CollectUI:
        def __getattr__(self, name):
            return lambda *a, **k: None

    usage.set_db_path(tmp_path / "tok.db")
    ctx = ToolContext(workspace=tmp_path, config=config)
    try:
        logged = Session("sys", log_dir=tmp_path / "logs")
        agent = Agent(llm=FakeLLMWithUsage([
            AssistantTurn(text="hi", usage={"prompt_tokens": 4,
                                            "completion_tokens": 2}),
        ]), registry=build_default_registry(), session=logged,
            gate=PermissionGate(config), ui=CollectUI(), ctx=ctx)
        agent.run_turn("q")
        conn = sqlite3.connect(tmp_path / "tok.db")
        (sid,) = conn.execute("SELECT session_id FROM token_usage").fetchone()
        conn.close()
        assert sid == logged.log_path.stem           # session_20260xx_xxxxxx

        # 没有留档的运行(子代理那一档):空串,不冒充一个会话
        bare = Session("sys")
        agent = Agent(llm=FakeLLMWithUsage([
            AssistantTurn(text="hi", usage={"prompt_tokens": 1,
                                            "completion_tokens": 1}),
        ]), registry=build_default_registry(), session=bare,
            gate=PermissionGate(config), ui=CollectUI(), ctx=ctx)
        agent.run_turn("q")
        conn = sqlite3.connect(tmp_path / "tok.db")
        rows = conn.execute("SELECT session_id FROM token_usage").fetchall()
        conn.close()
        assert rows[-1] == ("",)
    finally:
        usage.set_db_path(None)


def test_statistics_carries_estimated_cost(tmp_path, monkeypatch):
    """W2:statistics 的摘要带估算费用;缺价模型列进 unpriced,不当 $0。"""
    monkeypatch.setattr(pricing, "override_path",
                        lambda: tmp_path / "pricing-override.json")
    monkeypatch.setattr(pricing, "_OVERRIDE", None)
    usage.set_db_path(tmp_path / "tok.db")
    try:
        usage.record_usage("deepseek", "deepseek-chat",
                           {"prompt_tokens": 1_000_000, "completion_tokens": 0})
        usage.record_usage("p", "mystery-x",
                           {"prompt_tokens": 10, "completion_tokens": 0})
        s = usage.statistics(7)
        assert s["summary"]["cost_usd"] == 0.15
        assert s["summary"]["unpriced_models"] == ["mystery-x"]
        m = {x["model"]: x for x in s["models"]}
        assert m["deepseek-chat"]["priced"] is True
        assert m["mystery-x"]["cost_usd"] is None
    finally:
        usage.set_db_path(None)


# ------------------------------------------------------------------ W5/W6 实测列

def test_ttft_and_reasoning_are_recorded_when_reported(tmp_path):
    """端点回报了才落库;这份账喂风云榜的「极致打字机」与「深度沉思狂」。"""
    usage.set_db_path(tmp_path / "tok.db")
    try:
        usage.record_usage("p", "m", {"prompt_tokens": 10, "completion_tokens": 5},
                           ttft_ms=640, reasoning_tokens=31)
        usage.record_usage("p", "m",
                           {"prompt_tokens": 10, "completion_tokens": 5})  # 端点没回报
        m = usage.statistics(7)["models"][0]
        assert m["ttft_samples"] == 1 and m["ttft_ms"] == 640
        assert m["reasoning_samples"] == 1 and m["reasoning_tokens"] == 31
    finally:
        usage.set_db_path(None)


def test_profile_averages_ttft_across_samples(tmp_path):
    """两次实测 600/800 → 均值 700;NULL 样本不进分母(没回报的不凑数)。"""
    usage.set_db_path(tmp_path / "tok.db")
    try:
        for ttft in (600, 800, None):
            usage.record_usage("p", "m",
                               {"prompt_tokens": 1, "completion_tokens": 1},
                               ttft_ms=ttft)
        m = usage.statistics(7)["models"][0]
        assert m["ttft_samples"] == 2 and m["ttft_ms"] == 700
    finally:
        usage.set_db_path(None)


def test_unreported_ttft_and_reasoning_stay_none_not_zero(tmp_path):
    """端点从没回报 → 均值与总量都是 None:「未记录」不是「瞬间首字/零思考」,
    绝不落 0 —— 0 是端点说了"零",None 是端点根本没说,两回事。"""
    usage.set_db_path(tmp_path / "tok.db")
    try:
        usage.record_usage("p", "m", {"prompt_tokens": 1, "completion_tokens": 1})
        m = usage.statistics(7)["models"][0]
        assert m["ttft_samples"] == 0 and m["ttft_ms"] is None
        assert m["reasoning_samples"] == 0 and m["reasoning_tokens"] is None
    finally:
        usage.set_db_path(None)


# ------------------------------------------------- v0.9.117 缓存列 + 仪表盘

def test_cached_tokens_landed_when_reported(tmp_path):
    """缓存列同一个 NULL 口径:7=端点给的数,0=端点明说零命中,None=没说。"""
    import sqlite3
    usage.record_usage("p", "m", {"prompt_tokens": 10, "completion_tokens": 5},
                       cached_tokens=7)
    usage.record_usage("p", "m", {"prompt_tokens": 10, "completion_tokens": 5},
                       cached_tokens=0)
    usage.record_usage("p", "m", {"prompt_tokens": 10, "completion_tokens": 5})
    with sqlite3.connect(str(usage.db_path())) as conn:
        vals = [r[0] for r in conn.execute(
            "SELECT cached_tokens FROM token_usage ORDER BY ts")]
    assert vals == [7, 0, None]


def test_old_database_gains_cached_tokens_column(tmp_path):
    """pre-0.9.117 老库(无缓存列)挂进来自动补列:老行照读、新行照记。"""
    import sqlite3
    p = tmp_path / "old.db"
    conn = sqlite3.connect(p)
    conn.execute(
        "CREATE TABLE token_usage ("
        "  ts INTEGER NOT NULL, date TEXT NOT NULL,"
        "  provider TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '',"
        "  prompt_tokens INTEGER NOT NULL DEFAULT 0,"
        "  completion_tokens INTEGER NOT NULL DEFAULT 0,"
        "  total_tokens INTEGER NOT NULL DEFAULT 0)")
    conn.commit()
    conn.close()
    usage.set_db_path(p)
    try:
        assert usage.record_usage("p", "m",
                                  {"prompt_tokens": 2, "completion_tokens": 1},
                                  cached_tokens=0)
        d = usage.dashboard("all")
        assert d["ok"] and d["summary"]["cached_tokens"] == 0   # 端点说了零 → 0
    finally:
        usage.set_db_path(None)


def test_dashboard_shape_and_range_semantics(tmp_path):
    """仪表盘一揽子:区间语义/会话跨天去重/占比/来源卡/全期砖/热力图,一次钉全。

    历史行用原始 SQL 回填(record_usage 只记"今天"),四天有账:今天、昨天、
    31 天前、100 天前 —— 周/月档吃不到后两天的量,全期砖与热力图吃得到。
    """
    import sqlite3
    from datetime import date as _date, timedelta as _td

    today = _date.today()
    day = lambda delta: (today - _td(days=delta)).isoformat()
    usage.record_usage("zhipu", "glm",
                       {"prompt_tokens": 900, "completion_tokens": 100},
                       session_id="s1", ttft_ms=500, cached_tokens=400)
    usage.record_usage("zhipu", "glm",
                       {"prompt_tokens": 150, "completion_tokens": 50},
                       session_id="s1")
    usage.record_usage("other", "o", {"prompt_tokens": 100,
                                      "completion_tokens": 0})
    with sqlite3.connect(str(usage.db_path())) as conn:
        for delta, total, sid in ((1, 250, "s1"), (31, 999, "s2"), (100, 50, "")):
            conn.execute(
                "INSERT INTO token_usage (ts, date, provider, model,"
                " prompt_tokens, completion_tokens, total_tokens, session_id)"
                " VALUES (0, ?, 'zhipu', 'glm', ?, 0, ?, ?)",
                (day(delta), total, total, sid))
        conn.commit()

    d = usage.dashboard("week")
    assert d["ok"]
    assert d["range"] == {"kind": "week", "start_date": day(6),
                          "end_date": day(0)}
    s = d["summary"]
    assert (s["prompt"], s["completion"], s["total"], s["requests"]) == \
        (1400, 150, 1550, 4)
    assert s["cached_tokens"] == 400 and s["cached_samples"] == 1
    assert s["reasoning_tokens"] is None and s["reasoning_samples"] == 0
    assert s["sessions"] == 1            # s1 跨两天只算一次;无留档的不进来
    assert s["cost_usd"] is None         # glm/o 都没价目:未知,不当 $0
    assert set(s["unpriced_models"]) == {"glm", "o"}
    assert [(m["key"], m["share"]) for m in d["models"]] == [
        ("zhipu/glm", 93.5), ("other/o", 6.5)]
    assert d["models"][0]["ttft_samples"] == 1 and d["models"][0]["ttft_ms"] == 500
    assert d["providers"] == [
        {"provider": "zhipu", "total": 1450, "share": 93.5, "models": 1},
        {"provider": "other", "total": 100, "share": 6.5, "models": 1}]
    assert [(x["date"], x["total"], x["sessions"]) for x in d["days"]] == [
        (day(1), 250, 1), (day(0), 1300, 1)]
    # 全期口径的砖与热力图:讲"这个库的一生",不随区间缩水
    assert d["tiles"] == {"d7": 1550, "d30": 1550, "daily_avg": 51,
                          "sessions": 2}
    assert d["meta"]["first_date"] == day(100)
    assert d["meta"]["active_days"] == 4
    assert [(h["date"], h["total"]) for h in d["heatmap"]] == [
        (day(100), 50), (day(31), 999), (day(1), 250), (day(0), 1300)]

    # 同一份账的三种切法
    assert usage.dashboard("day")["summary"]["total"] == 1300
    assert usage.dashboard("all")["summary"]["total"] == 2599
    d = usage.dashboard("custom", day(1), day(0))
    assert d["ok"] and d["summary"]["total"] == 1550
    # 区间不合法:倒着/胡写/整段未来/陌生档 —— 明说不合法,绝不猜一个区间
    assert not usage.dashboard("custom", day(0), day(1))["ok"]
    assert not usage.dashboard("custom", "不是日期", day(0))["ok"]
    assert not usage.dashboard("bad")["ok"]
    # 合法但没账的窗:ok + 诚实的 0,不是报错
    d = usage.dashboard("custom", day(9), day(3))
    assert d["ok"] and d["summary"]["total"] == 0 and d["days"] == []
    # 止落在未来收敛到今天,照常出数
    assert usage.dashboard("custom", day(6), "2099-01-01")["summary"]["total"] == 1550


def test_dashboard_empty_db_is_an_honest_zero(tmp_path):
    """空库:ok、全 0、first_date=None、热力图空 —— 一页能画的空图,不是报错。"""
    d = usage.dashboard("all")
    assert d["ok"]
    assert d["summary"]["total"] == 0 and d["models"] == []
    assert d["meta"]["first_date"] is None and d["meta"]["active_days"] == 0
    assert d["heatmap"] == [] and d["days"] == []
