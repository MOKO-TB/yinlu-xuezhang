"""tests/test_find_references.py: 跨文件代码引用查找与影响面分析测试 (v0.9.166)。"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from minicode.agent import Agent
from minicode.find_references import (
    analyze_impact,
    extract_python_references,
    extract_regex_references,
    find_references_tool,
    find_symbol_references,
)
from minicode.tools.registry import build_default_registry


def test_extract_python_references_calls():
    code = """
def main():
    calculate_total(10)
    obj.calculate_total(20)
    res = other_func()
"""
    refs = extract_python_references(code, "app/main.py", "calculate_total")
    assert len(refs) == 2
    assert refs[0]["line"] == 3
    assert refs[0]["kind"] == "call"
    assert "calculate_total(10)" in refs[0]["snippet"]
    assert refs[1]["line"] == 4
    assert refs[1]["kind"] == "call"


def test_extract_python_references_imports():
    code = """
import my_service
from utils import helper, my_service as ms
from my_service.sub import thing
"""
    refs = extract_python_references(code, "app/routes.py", "my_service")
    assert len(refs) == 3
    kinds = [r["kind"] for r in refs]
    assert all(k == "import" for k in kinds)


def test_extract_python_references_inherit_and_types():
    code = """
from base import BaseHandler

class CustomHandler(BaseHandler):
    handler: BaseHandler

    def process(self, h: BaseHandler) -> BaseHandler:
        pass
"""
    refs = extract_python_references(code, "app/handler.py", "BaseHandler")
    assert len(refs) >= 4
    kinds = [r["kind"] for r in refs]
    assert "import" in kinds
    assert "inherit" in kinds
    assert "type" in kinds


def test_extract_python_references_assign():
    code = """
app_state = "init"
app_state = "running"
print(app_state)
"""
    refs = extract_python_references(code, "app/state.py", "app_state")
    assert len(refs) == 3
    assert refs[0]["kind"] == "assign"
    assert refs[1]["kind"] == "assign"
    assert refs[2]["kind"] == "read"


def test_extract_regex_references_js_ts():
    ts_code = """
import { UserService } from "./user";
class SpecialUser extends UserService {}
function check(u: UserService) {
    UserService(u);
}
"""
    refs = extract_regex_references(ts_code, "src/index.ts", "UserService", ".ts")
    assert len(refs) == 4
    kinds = [r["kind"] for r in refs]
    assert "import" in kinds
    assert "inherit" in kinds
    assert "type" in kinds
    assert "call" in kinds


def test_extract_regex_references_go_rust():
    go_code = """
import "github.com/pkg/errors"
func run() {
    errors.New("fail")
}
"""
    refs = extract_regex_references(go_code, "main.go", "errors", ".go")
    assert len(refs) == 2
    assert refs[0]["kind"] == "import"
    assert refs[1]["kind"] == "call" or refs[1]["kind"] == "read"


def test_word_boundary_no_partial_match():
    code = """
foo = 1
foobar = 2
sub_foo = 3
calculate_foo()
"""
    refs = extract_python_references(code, "mod.py", "foo")
    # 只有第一行是精准全词 foo
    assert len(refs) == 1
    assert refs[0]["line"] == 2
    assert refs[0]["snippet"] == "foo = 1"


def test_analyze_impact_levels():
    # 1. NONE
    res_none = analyze_impact([])
    assert res_none["level"] == "NONE"

    # 2. LOW
    low_refs = [
        {"file": "app/a.py", "line": 1, "kind": "call", "is_test": False},
        {"file": "app/a.py", "line": 5, "kind": "read", "is_test": False},
    ]
    res_low = analyze_impact(low_refs)
    assert res_low["level"] == "LOW"

    # 3. MEDIUM
    med_refs = [
        {"file": f"app/m{i}.py", "line": 1, "kind": "call", "is_test": False}
        for i in range(3)
    ]
    res_med = analyze_impact(med_refs)
    assert res_med["level"] == "MEDIUM"

    # 4. HIGH
    high_refs = [
        {"file": f"app/pkg{i}.py", "line": i, "kind": "call", "is_test": False}
        for i in range(8)
    ]
    res_high = analyze_impact(high_refs)
    assert res_high["level"] == "HIGH"


def test_find_symbol_references_workspace_filtering(tmp_path: Path):
    # 创建模拟工作区文件结构
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "service.py").write_text("class MyService:\n    pass\n", encoding="utf-8")
    (pkg / "client.py").write_text("from pkg.service import MyService\ns = MyService()\n", encoding="utf-8")

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_service.py").write_text("from pkg.service import MyService\ndef test_s():\n    assert MyService()\n", encoding="utf-8")

    # 1. 全量查找
    res_all = find_symbol_references(tmp_path, "MyService", include_tests=True)
    assert res_all["ok"] is True
    assert res_all["total"] >= 4

    # 2. 排除测试
    res_no_test = find_symbol_references(tmp_path, "MyService", include_tests=False)
    assert res_no_test["ok"] is True
    assert all("tests" not in r["file"] for r in res_no_test["references"])

    # 3. 路径模式过滤
    res_path = find_symbol_references(tmp_path, "MyService", path_pattern="pkg/*")
    assert res_path["ok"] is True
    assert all(r["file"].startswith("pkg/") for r in res_path["references"])

    # 4. 类型过滤
    res_calls = find_symbol_references(tmp_path, "MyService", kind="call")
    assert res_calls["ok"] is True
    assert all(r["kind"] == "call" for r in res_calls["references"])


def test_find_references_tool_e2e(tmp_path: Path, ctx):
    file_a = tmp_path / "demo.py"
    file_a.write_text("target_function()\n", encoding="utf-8")

    out = find_references_tool(ctx, "target_function")
    assert "符号引用检索: `target_function`" in out
    assert "demo.py" in out
    assert "调用" in out

    # 空结果测试
    out_empty = find_references_tool(ctx, "nonexistent_xyz")
    assert "未找到符号 `nonexistent_xyz`" in out_empty


@pytest.mark.parametrize("args,files,total", [
    ({"symbol": "target_action"}, ("src/mod.py", "tests/test_mod.py", "other.py"), 5),
    ({"symbol": "target_action", "include_tests": False}, ("src/mod.py", "other.py"), 3),
    ({"symbol": "target_action", "path_pattern": "src/*.py", "kind": "call",
      "include_tests": False, "max_results": 1}, ("src/mod.py",), 1),
    ({"symbol": "missing_symbol"}, (), 0),
    ({"symbol": "target_action", "path_pattern": "../*"}, (), 0),
], ids=["required", "exclude-tests", "all-properties", "no-match", "parent-pattern"])
def test_registered_tool_runs_through_agent(tmp_path, ctx, monkeypatch, args, files, total):
    workspace = tmp_path / "workspace"
    (workspace / "src").mkdir(parents=True)
    (workspace / "tests").mkdir()
    sources = {
        "src/mod.py": "def target_action():\n    pass\ndef caller():\n    target_action()\n    target_action()\n",
        "tests/test_mod.py": "from src.mod import target_action\ndef test_action():\n    target_action()\n",
        "other.py": "target_action()\n",
    }
    for name, code in sources.items():
        (workspace / name).write_text(code, encoding="utf-8")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    decoy = cwd / "cwd_only.py"
    decoy.write_text("target_action()\n", encoding="utf-8")
    monkeypatch.chdir(cwd)
    ctx.workspace = workspace
    tool = next(t for t in build_default_registry() if t.name == "find_references")
    assert tool.handler is find_references_tool
    agent = Agent.__new__(Agent)
    agent.ctx = ctx

    result, elapsed_ms = agent._run_handler(tool, args)

    if total:
        assert f"共命中 {total} 处引用" in result
    else:
        assert "未找到符号" in result
    for name in sources:
        assert (name in result) == (name in files)
        assert (workspace / name).read_text(encoding="utf-8") == sources[name]
    assert "cwd_only.py" not in result
    assert decoy.read_text(encoding="utf-8") == "target_action()\n"
    assert isinstance(elapsed_ms, int) and elapsed_ms >= 0

    # path_pattern 仍是相对路径的过滤器,绝对路径不能把扫描根切到工作区外。
    outside, _ = agent._run_handler(tool, {
        "symbol": "target_action", "path_pattern": decoy.as_posix(),
    })
    assert "未找到符号" in outside


@pytest.mark.parametrize("bad_ctx", [None, object(), SimpleNamespace(workspace=None), SimpleNamespace(workspace="")],
                         ids=["none", "missing-attribute", "none-workspace", "empty-workspace"])
def test_registered_tool_requires_workspace(monkeypatch, bad_ctx):
    def unexpected_scan(*args, **kwargs):
        pytest.fail("缺失工作区时不得扫描进程 cwd")

    monkeypatch.setattr("minicode.find_references.find_symbol_references", unexpected_scan)
    tool = next(t for t in build_default_registry() if t.name == "find_references")
    agent = Agent.__new__(Agent)
    agent.ctx = bad_ctx

    result, _ = agent._run_handler(tool, {"symbol": "target_action"})

    assert "引用查找失败" in result
    assert "无法获取当前工作区路径(ctx.workspace)" in result
