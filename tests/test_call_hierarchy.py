"""代码调用层次结构与依赖拓扑分析测试集 (v0.9.168)。"""

from types import SimpleNamespace

import pytest

from minicode.agent import Agent
from minicode.call_hierarchy import (
    extract_python_calls,
    build_call_hierarchy,
    format_hierarchy_tree,
    format_hierarchy_mermaid,
    call_hierarchy_tool,
)
from minicode.tools.registry import build_default_registry


def test_extract_python_calls_single_file():
    code = """
class Worker:
    def execute(self):
        self.prepare()
        run_job()

    def prepare(self):
        log_message("preparing")

def run_job():
    log_message("running")

def log_message(msg):
    pass
"""
    visitor = extract_python_calls(code, "test_file.py")
    # 验证定义的类与函数
    assert "Worker" in visitor.definitions
    assert "Worker.execute" in visitor.definitions
    assert "Worker.prepare" in visitor.definitions
    assert "run_job" in visitor.definitions
    assert "log_message" in visitor.definitions

    # 验证 outgoing
    assert ("prepare", 4) in visitor.outgoing["Worker.execute"]
    assert ("run_job", 5) in visitor.outgoing["Worker.execute"]
    assert ("log_message", 8) in visitor.outgoing["Worker.prepare"]
    assert ("log_message", 11) in visitor.outgoing["run_job"]

    # 验证 incoming
    callees = [item[0] for item in visitor.incoming["log_message"]]
    assert "Worker.prepare" in callees
    assert "run_job" in callees


def test_incoming_call_hierarchy_multi_level(tmp_path):
    # 模拟多文件调用链路: a.py -> b.py -> c.py
    f1 = tmp_path / "a.py"
    f1.write_text("from b import step_b\ndef entry_point():\n    step_b()\n", encoding="utf-8")

    f2 = tmp_path / "b.py"
    f2.write_text("from c import core_action\ndef step_b():\n    core_action()\n", encoding="utf-8")

    f3 = tmp_path / "c.py"
    f3.write_text("def core_action():\n    pass\n", encoding="utf-8")

    # 查询 core_action 的 incoming (谁调用了它)
    res = build_call_hierarchy(tmp_path, "core_action", direction="incoming", depth=3)
    assert res["symbol"] == "core_action"
    assert res["fan_in"] == 1
    tree = res["incoming_tree"]
    assert len(tree["calls"]) == 1
    caller_b = tree["calls"][0]
    assert caller_b["symbol"] == "step_b"
    assert len(caller_b["calls"]) == 1
    caller_a = caller_b["calls"][0]
    assert caller_a["symbol"] == "entry_point"


def test_outgoing_call_hierarchy_multi_level(tmp_path):
    f1 = tmp_path / "main.py"
    f1.write_text("def main():\n    init()\n    run()\n\ndef init():\n    load_config()\n\ndef run():\n    pass\n\ndef load_config():\n    pass\n", encoding="utf-8")

    res = build_call_hierarchy(tmp_path, "main", direction="outgoing", depth=2)
    assert res["symbol"] == "main"
    assert res["fan_out"] == 2
    tree = res["outgoing_tree"]
    callees = [c["symbol"] for c in tree["calls"]]
    assert "init" in callees
    assert "run" in callees

    init_node = next(c for c in tree["calls"] if c["symbol"] == "init")
    assert any(c["symbol"] == "load_config" for c in init_node["calls"])


def test_both_directions(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text("def middle():\n    downstream()\n\ndef upstream():\n    middle()\n\ndef downstream():\n    pass\n", encoding="utf-8")

    res = build_call_hierarchy(tmp_path, "middle", direction="both", depth=2)
    assert "incoming_tree" in res
    assert "outgoing_tree" in res
    assert any(c["symbol"] == "upstream" for c in res["incoming_tree"]["calls"])
    assert any(c["symbol"] == "downstream" for c in res["outgoing_tree"]["calls"])


def test_cycle_detection(tmp_path):
    # 互相调用的死循环递归
    f = tmp_path / "cycle.py"
    f.write_text("def ping():\n    pong()\n\ndef pong():\n    ping()\n", encoding="utf-8")

    res = build_call_hierarchy(tmp_path, "ping", direction="outgoing", depth=4)
    tree = res["outgoing_tree"]
    assert len(tree["calls"]) == 1
    pong_node = tree["calls"][0]
    assert pong_node["symbol"] == "pong"
    assert len(pong_node["calls"]) == 1
    ping_again = pong_node["calls"][0]
    assert ping_again["symbol"] == "ping"
    assert ping_again["cycle"] is True
    assert len(ping_again["calls"]) == 0  # 循环截断, 无更深子节点


def test_depth_capping(tmp_path):
    f = tmp_path / "chain.py"
    f.write_text("def d1():\n    d2()\ndef d2():\n    d3()\ndef d3():\n    d4()\ndef d4():\n    pass\n", encoding="utf-8")

    # 深度为 1
    res1 = build_call_hierarchy(tmp_path, "d1", direction="outgoing", depth=1)
    d2_node = res1["outgoing_tree"]["calls"][0]
    assert d2_node["symbol"] == "d2"
    assert len(d2_node["calls"]) == 0  # 深度截断

    # 深度为 2
    res2 = build_call_hierarchy(tmp_path, "d1", direction="outgoing", depth=2)
    d2_node = res2["outgoing_tree"]["calls"][0]
    assert len(d2_node["calls"]) == 1
    assert d2_node["calls"][0]["symbol"] == "d3"


def test_regex_fallback_non_python(tmp_path):
    js_file = tmp_path / "app.js"
    js_file.write_text("""
function handleRequest() {
    validateToken();
    renderPage();
}
function validateToken() {
    checkDB();
}
function renderPage() {}
function checkDB() {}
""", encoding="utf-8")

    res = build_call_hierarchy(tmp_path, "handleRequest", direction="outgoing", depth=2)
    tree = res["outgoing_tree"]
    callees = [c["symbol"] for c in tree["calls"]]
    assert "validateToken" in callees
    assert "renderPage" in callees


def test_format_hierarchy_tree(tmp_path):
    f = tmp_path / "sample.py"
    f.write_text("def caller():\n    target()\ndef target():\n    pass\n", encoding="utf-8")

    res = build_call_hierarchy(tmp_path, "target", direction="incoming", depth=2)
    text = format_hierarchy_tree(res["incoming_tree"], "incoming")
    assert "Call Hierarchy [Incoming" in text
    assert "target" in text
    assert "caller" in text


def test_format_hierarchy_mermaid(tmp_path):
    f = tmp_path / "sample.py"
    f.write_text("def caller():\n    target()\ndef target():\n    pass\n", encoding="utf-8")

    res = build_call_hierarchy(tmp_path, "target", direction="incoming", depth=2)
    mermaid = format_hierarchy_mermaid(res["incoming_tree"], "incoming")
    assert "```mermaid" in mermaid
    assert "graph TD" in mermaid
    assert 'caller["caller"] --> target["target"]' in mermaid


def test_call_hierarchy_tool_end_to_end(tmp_path, ctx):
    # 空符号拦截
    assert "错误" in call_hierarchy_tool(ctx, "")

    # 真实调用
    f = tmp_path / "worker.py"
    f.write_text("def start():\n    execute()\ndef execute():\n    pass\n", encoding="utf-8")

    out_tree = call_hierarchy_tool(ctx, "execute", direction="incoming", depth=2, format="tree")
    assert "调用层次结构拓扑分析" in out_tree
    assert "execute" in out_tree
    assert "start" in out_tree

    out_mermaid = call_hierarchy_tool(ctx, "execute", direction="incoming", depth=2, format="mermaid")
    assert "```mermaid" in out_mermaid
    assert "start" in out_mermaid


@pytest.mark.parametrize("args,callers,has_definition", [
    ({"symbol": "target_action"}, ("caller", "other_caller"), True),
    ({"symbol": "target_action", "direction": "both", "depth": 2,
      "path_pattern": "src/*.py", "format": "mermaid"}, ("caller", "leaf"), True),
    ({"symbol": "target_action", "direction": "outgoing"}, ("leaf",), True),
    ({"symbol": "target_action", "path_pattern": "mod.py"}, ("caller",), True),
    ({"symbol": "missing_symbol"}, (), False),
    ({"symbol": "target_action", "path_pattern": "../*"}, (), False),
], ids=["required", "all-properties", "outgoing", "basename-pattern", "no-match", "parent-pattern"])
def test_registered_tool_runs_through_agent(tmp_path, ctx, monkeypatch, args, callers, has_definition):
    workspace = tmp_path / "workspace"
    (workspace / "src").mkdir(parents=True)
    sources = {
        "src/mod.py": "def target_action():\n    leaf()\ndef caller():\n    target_action()\ndef leaf():\n    pass\n",
        "other.py": "def other_caller():\n    target_action()\n",
    }
    for name, code in sources.items():
        (workspace / name).write_text(code, encoding="utf-8")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    decoy = cwd / "cwd_only.py"
    decoy_code = "def cwd_caller():\n    target_action()\n"
    decoy.write_text(decoy_code, encoding="utf-8")
    monkeypatch.chdir(cwd)
    ctx.workspace = workspace
    tool = next(t for t in build_default_registry() if t.name == "call_hierarchy")
    assert tool.handler is call_hierarchy_tool
    agent = Agent.__new__(Agent)
    agent.ctx = ctx

    result, elapsed_ms = agent._run_handler(tool, args)

    assert "调用层次结构拓扑分析" in result
    assert ("符号定义: src/mod.py:1" in result) == has_definition
    # 使用定义行/图节点判断 caller,避免与 other_caller 子串混淆。
    if args.get("format") == "mermaid":
        assert "```mermaid" in result
        assert 'caller["caller"] --> target_action["target_action"]' in result
        assert 'target_action["target_action"] --> leaf["leaf"]' in result
    else:
        assert ("caller (src/mod.py:4)" in result) == ("caller" in callers)
    for name in ("other_caller", "leaf"):
        assert (name in result) == (name in callers)
    assert "cwd_caller" not in result
    for name, code in sources.items():
        assert (workspace / name).read_text(encoding="utf-8") == code
    assert decoy.read_text(encoding="utf-8") == decoy_code
    assert isinstance(elapsed_ms, int) and elapsed_ms >= 0

    outside, _ = agent._run_handler(tool, {
        "symbol": "target_action", "path_pattern": decoy.as_posix(),
    })
    assert "未在工作区内找到直接定义" in outside
    assert "cwd_caller" not in outside


@pytest.mark.parametrize("bad_ctx", [None, object(), SimpleNamespace(workspace=None), SimpleNamespace(workspace="")],
                         ids=["none", "missing-attribute", "none-workspace", "empty-workspace"])
def test_registered_tool_requires_workspace(monkeypatch, bad_ctx):
    def unexpected_scan(*args, **kwargs):
        pytest.fail("缺失工作区时不得扫描进程 cwd")

    monkeypatch.setattr("minicode.call_hierarchy.build_call_hierarchy", unexpected_scan)
    tool = next(t for t in build_default_registry() if t.name == "call_hierarchy")
    agent = Agent.__new__(Agent)
    agent.ctx = bad_ctx

    result, _ = agent._run_handler(tool, {"symbol": "target_action"})

    assert "错误" in result
    assert "无法获取当前工作区路径(ctx.workspace)" in result
