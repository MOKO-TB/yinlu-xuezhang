"""代码调用层次结构与依赖拓扑分析测试集 (v0.9.168)。"""

from minicode.call_hierarchy import (
    extract_python_calls,
    build_call_hierarchy,
    format_hierarchy_tree,
    format_hierarchy_mermaid,
    call_hierarchy_tool,
)


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


def test_call_hierarchy_tool_end_to_end(tmp_path):
    # 空符号拦截
    assert "错误" in call_hierarchy_tool("")

    # 真实调用
    f = tmp_path / "worker.py"
    f.write_text("def start():\n    execute()\ndef execute():\n    pass\n", encoding="utf-8")

    class FakeCtx:
        workspace = tmp_path

    out_tree = call_hierarchy_tool("execute", direction="incoming", depth=2, format="tree", ctx=FakeCtx())
    assert "调用层次结构拓扑分析" in out_tree
    assert "execute" in out_tree
    assert "start" in out_tree

    out_mermaid = call_hierarchy_tool("execute", direction="incoming", depth=2, format="mermaid", ctx=FakeCtx())
    assert "```mermaid" in out_mermaid
    assert "start" in out_mermaid
