"""上下文窗口面板:session_metrics 的占用明细 + compact_now 的真实压缩与忙时守卫。"""
from minicode.compaction import schema_tokens
from minicode.model_catalog import session_metrics


def _metrics(bridge):
    return session_metrics(bridge.agent, bridge.session, bridge.config,
                           bridge._current.llm_override)


def test_metrics_contain_context_breakdown(config, tmp_path):
    from tests.test_gui_bridge import FakeWindow
    from minicode.gui_bridge import create_app
    bridge, ui, _agent, _session = create_app(
        config, tmp_path, llm=FakeLLM(),
        sessions_dir=tmp_path / "sessions", config_path=tmp_path / "config.json")
    ui.attach(FakeWindow())
    m = _metrics(bridge)
    b = m["context_breakdown"]
    assert b is not None and b["window"] > 0
    keys = [x["key"] for x in b["buckets"]]
    assert keys == ["system", "tools", "skills", "messages"]
    labels = [x["label"] for x in b["buckets"]]
    assert labels == ["系统提示词", "工具定义", "Skill", "对话消息"]
    # 占用基数 = 消息估算 + 工具定义,四项之和与它闭合(同一把粗估尺子)
    total = sum(x["tokens"] for x in b["buckets"])
    assert abs(total - b["used"]) <= 8
    assert b["buckets"][1]["tokens"] > 0      # 工具定义(含 v0.9.15 新工具)
    skills_bucket = b["buckets"][2]
    assert skills_bucket["count"] >= 10       # 仓库现有技能(含 v0.9.14/15 新增)
    assert skills_bucket["tokens"] > 0


def test_tool_bucket_is_the_same_number_the_compact_trigger_uses(config, tmp_path):
    """面板上"工具定义"那个数,和决定什么时候压缩的那把尺子,必须是同一个来源。

    v0.9.40 D2 之前是两套算法(面板按单条相加、触发器压根没算),于是"面板说还没到
    七成,压缩却提前来了"或反过来都对不上账。
    """
    from tests.test_gui_bridge import FakeWindow
    from minicode.gui_bridge import create_app
    bridge, ui, agent, _session = create_app(
        config, tmp_path, llm=FakeLLM(),
        sessions_dir=tmp_path / "sessions", config_path=tmp_path / "config.json")
    ui.attach(FakeWindow())
    b = _metrics(bridge)["context_breakdown"]
    tools = next(x for x in b["buckets"] if x["key"] == "tools")
    assert tools["tokens"] == schema_tokens(agent.registry) == agent.tools_tokens


def test_metrics_breakdown_none_without_ctx(config, tmp_path):
    """没有 ctx 的测试桩(以及一切拿不到工作区的场景)给 None,前端不渲染。"""
    class _StubAgent:
        registry = []
        turn_tokens = 0
    from minicode.session import Session
    s = Session(system_prompt="x")
    m = session_metrics(_StubAgent(), s, config, None)
    assert m["context_breakdown"] is None


def test_compact_now_refuses_while_busy(config, tmp_path):
    from minicode.gui_bridge import create_app
    bridge, _ui, _agent, _session = create_app(
        config, tmp_path, llm=FakeLLM(),
        sessions_dir=tmp_path / "sessions", config_path=tmp_path / "config.json")
    bridge._current.busy = True
    result = bridge.compact_now()
    assert result["ok"] is False and "任务进行中" in result["msg"]


def test_compact_now_runs_and_pushes_metrics(config, tmp_path):
    from tests.test_gui_bridge import FakeWindow
    from minicode.gui_bridge import create_app
    bridge, ui, _agent, _session = create_app(
        config, tmp_path, llm=FakeLLM(),
        sessions_dir=tmp_path / "sessions", config_path=tmp_path / "config.json")
    ui.attach(FakeWindow())
    result = bridge.compact_now()
    assert result["ok"] is True
    # 压缩后即时回推一次任务面板数据(占用数字马上更新)
    assert any(e["type"] == "session_metrics" for e in ui.window.events)


class FakeLLM:
    def stream_chat(self, messages, tools, on_text=None, on_reasoning=None,
                    on_retry=None):
        from minicode.llm import AssistantTurn
        return AssistantTurn(text="")
