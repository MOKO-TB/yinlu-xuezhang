# -*- coding: utf-8 -*-
"""规划锁：这张计划没被人批准之前，不许动手（v0.9.129）。

补的是差距矩阵 §10D 那一格：整单核对单以前批完**只给模型看一句**"用户已批准"，
`permission.py` 里没有任何一处执行它 —— 批准与没批准，写盘走的是同一套档位。
这一族针钉的四件事，每件都是"这道门有没有真的挡住人"：

① 锁上之后写类与命令一律 REJECT，而**读与改计划本身照常**（否则没法给人批）；
② 排在档位之前：`accept_all` / `full` / 项目白名单 / 「本轮总是允许」都放不过它；
③ **只有人能解**：核对单批准、计划卡片那颗、命令行 `/plan approve` 三条入口，
   模型侧没有任何一条解得开的路（所以也不许有 `plan(action=unlock)`）；
④ 锁传得进子代理 —— 状态住 `SandboxState` 而不是住 gate 的全部理由。
"""
import pytest

from minicode.llm import AssistantTurn, ToolCall
from minicode.permission import PermissionGate
from minicode.sandbox import SandboxPolicy, SandboxState
from minicode.tools.base import ToolContext

from test_agent import FakeLLM, FakeUI


@pytest.fixture
def ws_ctx(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return ws


@pytest.fixture
def registry():
    from minicode.tools.registry import build_default_registry
    return {t.name: t for t in build_default_registry()}


def _gate(ws, mode="confirm"):
    from minicode.config import Config
    sb = SandboxState(SandboxPolicy(), ws)
    return sb, PermissionGate(Config(permission_mode=mode), sandbox=sb, workspace=ws)


# ------------------------------------------------------------ ① 拒什么、放什么

def test_locked_gate_denies_writes_and_commands_but_not_reads(ws_ctx, registry):
    sb, gate = _gate(ws_ctx)
    sb.lock_plan("把解析器改一遍")
    for name, args in (("write_file", {"path": "a.txt", "content": "x"}),
                       ("edit_file", {"path": "a.txt"}),
                       ("bash", {"command": "pytest -q"}),
                       ("py_script", {"path": "run.py"})):
        assert gate.check(registry[name], args) == "reject", name
    # 读与"把这张单子本身写出来"必须放行 —— 不然人永远看不到可批的东西
    for name, args in (("read_file", {"path": "a.txt"}),
                       ("glob", {"pattern": "*.py"}),
                       ("grep", {"pattern": "x"}),
                       ("plan", {"action": "write"}),
                       ("write_file", {"path": "plans/plan.md", "content": "x"})):
        assert gate.check(registry[name], args) != "reject", name


def test_plan_file_exemption_accepts_dot_slash_and_windows_separators(ws_ctx, registry):
    """例外只认那一个路径,所以路径口径要写宽:./plans\\plan.md 也算它。"""
    sb, gate = _gate(ws_ctx)
    sb.lock_plan()
    for raw in ("plans/plan.md", "./plans/plan.md", "plans\\plan.md",
                "/plans/plan.md"):
        assert gate.check(registry["write_file"],
                          {"path": raw, "content": "x"}) != "reject", raw
    # 但"像它"不算:这条是那条例外的边界
    assert gate.check(registry["write_file"],
                      {"path": "plans/plan.md.bak", "content": "x"}) == "reject"
    assert gate.check(registry["write_file"],
                      {"path": "other/plan.md", "content": "x"}) == "reject"


# ------------------------------------------------- ② 排在档位之前：高挡位放不过

@pytest.mark.parametrize("mode", ["accept_edits", "accept_all", "full"])
def test_no_permission_mode_walks_past_the_lock(ws_ctx, registry, mode):
    sb, gate = _gate(ws_ctx, mode=mode)
    sb.lock_plan()
    assert gate.check(registry["write_file"],
                      {"path": "a.txt", "content": "x"}) == "reject", mode


def test_turn_always_allow_does_not_walk_past_it_either(ws_ctx, registry):
    """「本轮总是允许」记的是**路径**可以访问,不是"这人今天不想再被问"。"""
    sb, gate = _gate(ws_ctx)
    gate.approve_outside(registry["write_file"], {"path": str(ws_ctx / "a.txt")},
                         session=True)
    sb.lock_plan()
    assert gate.check(registry["write_file"],
                      {"path": "a.txt", "content": "x"}) == "reject"


def test_the_probe_leaves_no_lock_trace_behind(ws_ctx, registry):
    """预演(并发筛子)会走同一条判断链,而锁那条拒**会写一份报告字段** ——
    还原清单里漏了它,表现是"下一步的日志与界面多出一句规划锁",而那一步根本没跑。
    (顺带说明为什么锁着的写不需要筛子挡:它不是 read_only,本来就进不了线程池。)"""
    sb, gate = _gate(ws_ctx)
    sb.lock_plan()
    assert registry["write_file"].read_only is False
    baseline = (gate.ask_note, gate.ask_kind, gate.project_allow_reason,
                gate.reject_reason, gate.plan_lock_reason)
    assert gate.would_ask(registry["write_file"],
                          {"path": "a.txt", "content": "x"}) is False   # 拒不是问
    assert (gate.ask_note, gate.ask_kind, gate.project_allow_reason,
            gate.reject_reason, gate.plan_lock_reason) == baseline, \
        "预演把报告字段写了"
    # 真判才留下理由
    assert gate.check(registry["write_file"],
                      {"path": "a.txt", "content": "x"}) == "reject"
    assert gate.plan_lock_reason


# --------------------------------------------------------- ③ 只有人解得开

def test_unlock_is_idempotent_and_reports_whether_it_changed(ws_ctx):
    sb = SandboxState(SandboxPolicy(), ws_ctx)
    assert sb.unlock_plan() is False          # 没锁着,解它不算做成任何事
    assert sb.lock_plan("甲") is True
    assert sb.lock_plan("乙") is False        # 已经锁着:目标跟着换,但"变锁"没发生
    assert sb.plan_lock_goal == "乙"
    assert sb.unlock_plan() is True
    assert sb.unlock_plan() is False
    assert sb.plan_lock is False


def test_a_new_turn_does_not_release_the_lock(ws_ctx, registry):
    """批准不是"第几回合"的事:回合边界不许把它冲掉。"""
    sb, gate = _gate(ws_ctx)
    sb.lock_plan()
    gate.new_turn()
    assert gate.check(registry["bash"], {"command": "ls"}) == "reject"


def test_the_model_has_no_way_to_unlock(config, ctx, ws_ctx, tmp_path):
    """plan 工具的 action 表里没有 unlock / approve —— 模型自己能解的锁不是锁。"""
    from minicode.planning import plan
    sb = SandboxState(SandboxPolicy(), ws_ctx)
    ctx.sandbox = sb
    sb.lock_plan("测试")
    for action in ("unlock", "approve", "clear", "release"):
        out = plan(ctx, action=action)
        assert "未知 action" in out, action
    assert sb.plan_lock is True               # 一个都没解开


def _run_write_turn(config, ctx, tmp_path):
    """真跑一回合:模型要写 a.txt,门拒了 —— 看它回给模型的是什么话。

    自己造 gate/ctx:`make_agent` 那道门不带 sandbox,而锁就住在 sandbox 上 ——
    拿它跑等于测了一条"永远没锁"的路径,针会假绿。
    """
    from minicode.agent import Agent
    from minicode.session import Session
    from minicode.tools.registry import build_default_registry

    ui = FakeUI()
    sb = SandboxState(SandboxPolicy(), ctx.workspace)
    agent = Agent(llm=FakeLLM([
        AssistantTurn(tool_calls=[ToolCall(
            id="c1", name="write_file",
            arguments='{"path": "a.txt", "content": "hi"}')]),
        AssistantTurn(text="好")]),
        registry=build_default_registry(), session=Session(system_prompt="sys"),
        gate=PermissionGate(config, sandbox=sb, workspace=ctx.workspace),
        ui=ui, ctx=ToolContext(workspace=ctx.workspace, config=config, sandbox=sb))
    sb.lock_plan("把解析器改一遍")
    agent.run_turn("开工")
    tool_msg = next(m for m in agent.session.messages if m["role"] == "tool")
    return agent, tool_msg["content"], not (ctx.workspace / "a.txt").exists()


def test_the_rejection_tells_the_model_the_one_way_out(config, ctx, tmp_path):
    """拒了要给出口,但给的必须是"问人"那一条 —— 不是 HARD_REJECT_NOTE 那句
    "去改 config 的权限档位"(那条路对这把锁根本不成立)。"""
    _agent, text, not_written = _run_write_turn(config, ctx, tmp_path)
    assert not_written, "锁着还写出来了,这把针就没意义"
    assert "还没被用户批准" in text and "/plan approve" in text
    assert "不要换一种写法绕过" in text
    assert "permissions.json" not in text, "说成项目规则拒 = 让模型去劝用户改错地方"


def test_a_project_deny_still_speaks_as_a_project_deny(config, ctx, tmp_path):
    """反面对照:两种拒的话术不能互相串。项目规则那句得说"你自己写的规则",
    规划锁那句得说"请用户点头"。"""
    agent, text, _ = _run_write_turn(config, ctx, tmp_path)
    assert agent.gate.reject_reason == ""     # 这条拒不是项目 deny 说的


# --------------------------------------------------- ④ 锁传得进子代理

def test_the_lock_is_visible_to_a_gate_that_shares_the_sandbox(ws_ctx, registry):
    """子代理各自新建 PermissionGate、但 `sandbox=ctx.sandbox` 是共用父会话那一份。
    所以锁必须住在 sandbox 上 —— 住 gate 上等于给写型子代理留一条后门。"""
    _sb, parent = _gate(ws_ctx)
    child = PermissionGate(parent.config, sandbox=parent.sandbox, workspace=ws_ctx)
    parent.sandbox.lock_plan()
    assert child.check(registry["write_file"],
                       {"path": "a.txt", "content": "x"}) == "reject", \
        "父会话锁着、派出去那一路照样能写"
