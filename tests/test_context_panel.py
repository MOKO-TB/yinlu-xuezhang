"""上下文窗口面板:session_metrics 的占用明细 + compact_now 的真实压缩与忙时守卫。

这一屏的全部价值在于"数字与压缩用的是同一把尺子",所以这里的断言宁可写得死:
四栏之和与顶部的占用必须严丝合缝,注册表换了内容两处要一起动,
实测与本地估不许互相冒充。
"""
from dataclasses import replace

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
    # 占用基数 = 消息估算 + 工具定义,四栏之和与它**严丝合缝**(同一把粗估尺子)
    total = sum(x["tokens"] for x in b["buckets"])
    assert total == b["estimated"] == b["used"], (
        f"四栏之和 {total} / 本地估 {b['estimated']} / 顶部占用 {b['used']} 不相等")
    assert b["source"] == "estimated", "没量过实测时不许把来源写成实测"
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


def test_compact_now_reports_that_it_did_nothing(config, tmp_path):
    """新会话没有可折叠的早期轮次:如实回"没压掉",不许报 ok=True。

    原来这条无论压没压都 ok True —— 摘编失败与"根本没东西可压"在界面上长得一样,
    用户只能猜。占用数字也一并带回去(before == after 就是"没动"的证据)。
    """
    from tests.test_gui_bridge import FakeWindow
    from minicode.gui_bridge import create_app
    bridge, ui, _agent, _session = create_app(
        config, tmp_path, llm=FakeLLM(),
        sessions_dir=tmp_path / "sessions", config_path=tmp_path / "config.json")
    ui.attach(FakeWindow())
    result = bridge.compact_now()
    assert result["ok"] is False and "没压掉" in result["msg"]
    assert result["before"] == result["after"]
    # 没压成也要回推一次面板数据:按钮按下的那一下必须有反应
    assert any(e["type"] == "session_metrics" for e in ui.window.events)


def test_compact_now_reports_the_occupancy_it_freed(config, tmp_path):
    from tests.test_gui_bridge import FakeWindow
    from minicode.gui_bridge import create_app
    bridge, ui, _agent, session = create_app(
        config, tmp_path, llm=FakeLLM(),
        sessions_dir=tmp_path / "sessions", config_path=tmp_path / "config.json")
    ui.attach(FakeWindow())
    for i in range(4):        # compact 保留最近 2 轮,4 问才有一轮可折
        session.add_user(f"第 {i} 问:" + "请把这段占用撑大一些," * 40)
        session.messages.append({"role": "assistant", "content": "收到。" * 80})
    result = bridge.compact_now()
    assert result["ok"] is True, result["msg"]
    assert result["before"] > result["after"]
    assert result["saved"] == result["before"] - result["after"] > 0
    assert "→" in result["msg"]


def test_the_panel_and_the_trigger_move_together_when_the_registry_changes(config,
                                                                            tmp_path):
    """换了工具清单之后,面板那一栏、压缩触发器、能调用的工具必须是同一份现实。

    v0.9.105 之前 `tools_tokens` 是装配时算好存下的副本:MCP 热重载(gui_bridge)
    往注册表里并新工具、对外模式(web_profile)把它收窄成只读那一档,两处都只换列表、
    没人重算 —— 于是面板按新清单报、触发器按旧清单压。同一份漏更还带走一个更狠的:
    `_tool_map` 没跟上,新工具出现在发给模型的定义里、一调用就"未找到工具"。
    """
    from tests.test_gui_bridge import FakeWindow
    from minicode.gui_bridge import create_app
    bridge, ui, agent, _session = create_app(
        config, tmp_path, llm=FakeLLM(),
        sessions_dir=tmp_path / "sessions", config_path=tmp_path / "config.json")
    ui.attach(FakeWindow())

    def tools_bucket():
        return _metrics(bridge)["context_breakdown"]["buckets"][1]["tokens"]

    before = tools_bucket()
    extra = replace(agent.registry[0], name="mcp_demo_echo")
    agent.registry[:] = list(agent.registry) + [extra]   # 热重载就是这么做 reg 的
    agent.refresh_registry()
    after = tools_bucket()
    assert after > before, "多一个工具的定义,面板那一栏必须跟着涨"
    assert after == agent.tools_tokens == schema_tokens(agent.registry)
    assert "mcp_demo_echo" in agent._tool_map, (
        "定义了却调不动 —— 界面上那句「现有会话下一轮即可用,不用重启」就是假的")

    keep = agent.registry[:3]               # 对外模式:注册表收窄
    agent.registry = keep
    agent.refresh_registry()
    b = _metrics(bridge)["context_breakdown"]
    assert b["buckets"][1]["tokens"] == schema_tokens(keep) < after
    assert "mcp_demo_echo" not in agent._tool_map
    assert b["used"] == b["estimated"] == sum(x["tokens"] for x in b["buckets"]), \
        "少掉的那份工具定义不许继续算进占用 —— 那会让压缩提前来"


def test_a_measured_total_does_not_inflate_the_message_bucket(config, tmp_path):
    """服务端实测顶上来的时候,四栏仍按本地估 —— 差额不许悄悄记进「对话消息」。

    实测与本地估是两把尺子:拿估算去减实测,量出来的"消息"里就混进了分词差异,
    而那一栏正是谁都不会去核对的。两个数各报各的,来源写清楚。
    """
    from tests.test_gui_bridge import FakeWindow
    from minicode.gui_bridge import create_app
    from minicode.compaction import estimate_tokens
    bridge, ui, agent, session = create_app(
        config, tmp_path, llm=FakeLLM(),
        sessions_dir=tmp_path / "sessions", config_path=tmp_path / "config.json")
    ui.attach(FakeWindow())
    session.add_user("这一个问题问得比较长一点。" * 30)
    local = estimate_tokens(session.messages) + agent.tools_tokens
    agent._real_prompt = (len(session.messages), local + 40_000)   # 上次请求实测
    b = _metrics(bridge)["context_breakdown"]
    assert b["source"] == "real"
    assert b["used"] == local + 40_000 > b["estimated"] == local
    assert sum(x["tokens"] for x in b["buckets"]) == local, "那 40K 差额没进任何一栏"
    msgs = next(x for x in b["buckets"] if x["key"] == "messages")
    assert msgs["tokens"] < 40_000, "对话消息那一栏是按消息本体量的,不是按差额倒推的"


class FakeLLM:
    def stream_chat(self, messages, tools, on_text=None, on_reasoning=None,
                    on_retry=None):
        from minicode.llm import AssistantTurn
        return AssistantTurn(text="")
