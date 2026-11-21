"""symbol_search 跨语言精准符号搜索单元测试(v0.9.165)。"""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from minicode import symbol_search
from minicode.agent import Agent
from minicode.tools.base import ToolContext
from minicode.tools.registry import build_default_registry


def test_python_ast_symbols(tmp_path: Path):
    py_code = '''
def global_func(a, b=1):
    pass

async def async_worker(task):
    pass

class MyService:
    def __init__(self):
        pass

    def run_job(self, x):
        pass
'''
    target = tmp_path / "service.py"
    target.write_text(py_code, encoding="utf-8")

    res = symbol_search.search_symbols(tmp_path)
    assert res["ok"] is True
    assert res["total_found"] == 5
    names = [s["name"] for s in res["symbols"]]
    assert "global_func" in names
    assert "async_worker" in names
    assert "MyService" in names
    assert "run_job" in names

    # 验证类型与所属类
    kind_map = {s["name"]: s["kind"] for s in res["symbols"]}
    assert kind_map["global_func"] == "function"
    assert kind_map["async_worker"] == "function"
    assert kind_map["MyService"] == "class"
    assert kind_map["run_job"] == "method"

    method_sym = next(s for s in res["symbols"] if s["name"] == "run_job")
    assert method_sym["parent"] == "MyService"


def test_js_ts_symbols(tmp_path: Path):
    ts_code = '''
export class ApiClient {
    connect() {}
}

export function fetchData(url: string) {
    return fetch(url);
}

export const processItem = (item) => {
    return item;
};

export interface ConfigOptions {
    debug: boolean;
}

export type Status = "ok" | "fail";
'''
    target = tmp_path / "app.ts"
    target.write_text(ts_code, encoding="utf-8")

    res = symbol_search.search_symbols(tmp_path)
    assert res["ok"] is True
    names = [s["name"] for s in res["symbols"]]
    assert "ApiClient" in names
    assert "fetchData" in names
    assert "processItem" in names
    assert "ConfigOptions" in names
    assert "Status" in names


def test_go_rust_symbols(tmp_path: Path):
    go_code = '''
package main

type Server struct {}

type Handler interface {}

func StartServer(port int) error {
    return nil
}

func (s *Server) Close() {}
'''
    (tmp_path / "main.go").write_text(go_code, encoding="utf-8")

    rust_code = '''
pub struct Config {
    val: usize,
}

pub enum Color {
    Red,
    Green,
}

pub fn run_app() {}
'''
    (tmp_path / "lib.rs").write_text(rust_code, encoding="utf-8")

    res = symbol_search.search_symbols(tmp_path)
    assert res["ok"] is True
    names = [s["name"] for s in res["symbols"]]
    assert "Server" in names
    assert "Handler" in names
    assert "StartServer" in names
    assert "Config" in names
    assert "Color" in names
    assert "run_app" in names


def test_query_filter_and_case(tmp_path: Path):
    py_code = '''
def handle_request(): pass
def handle_response(): pass
def send_notification(): pass
'''
    (tmp_path / "net.py").write_text(py_code, encoding="utf-8")

    # 大小写不敏感与子串匹配
    res1 = symbol_search.search_symbols(tmp_path, query="HANDLE")
    assert res1["ok"] is True
    assert res1["total_found"] == 2
    assert all("handle" in s["name"].lower() for s in res1["symbols"])

    res2 = symbol_search.search_symbols(tmp_path, query="notif")
    assert res2["ok"] is True
    assert res2["total_found"] == 1
    assert res2["symbols"][0]["name"] == "send_notification"


def test_kind_filter(tmp_path: Path):
    py_code = '''
class User: pass
def get_user(): pass
'''
    (tmp_path / "user.py").write_text(py_code, encoding="utf-8")

    res_cls = symbol_search.search_symbols(tmp_path, kind="class")
    assert res_cls["ok"] is True
    assert res_cls["total_found"] == 1
    assert res_cls["symbols"][0]["name"] == "User"

    res_func = symbol_search.search_symbols(tmp_path, kind="function")
    assert res_func["ok"] is True
    assert res_func["total_found"] == 1
    assert res_func["symbols"][0]["name"] == "get_user"


def test_path_filter(tmp_path: Path):
    sub = tmp_path / "pkg"
    sub.mkdir()
    (tmp_path / "a.py").write_text("def func_a(): pass\n", encoding="utf-8")
    (sub / "b.py").write_text("def func_b(): pass\n", encoding="utf-8")

    res_pkg = symbol_search.search_symbols(tmp_path, path="pkg")
    assert res_pkg["ok"] is True
    assert res_pkg["total_found"] == 1
    assert res_pkg["symbols"][0]["name"] == "func_b"


def test_ignore_directories(tmp_path: Path):
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "hook.py").write_text("def hidden_func(): pass\n", encoding="utf-8")

    node_modules = tmp_path / "node_modules"
    node_modules.mkdir()
    (node_modules / "pkg.js").write_text("function ignoredFunc() {}\n", encoding="utf-8")

    src = tmp_path / "src"
    src.mkdir()
    (src / "main.py").write_text("def main(): pass\n", encoding="utf-8")

    res = symbol_search.search_symbols(tmp_path)
    assert res["ok"] is True
    assert res["total_found"] == 1
    assert res["symbols"][0]["name"] == "main"


def test_unchanged_files_are_parsed_once_and_copies_are_independent(tmp_path, monkeypatch):
    from minicode import symbol_search as ss
    calls = []
    real = ss.extract_python_symbols

    def spy(code, rel):
        calls.append(rel)
        return real(code, rel)

    monkeypatch.setattr(ss, "extract_python_symbols", spy)
    ss._extract_cached.cache_clear()
    (tmp_path / "mod.py").write_text("def target_action():\n    pass\n", encoding="utf-8")
    first = ss.search_symbols(tmp_path, query="target")
    first["symbols"][0]["name"] = "mutated"
    second = ss.search_symbols(tmp_path, query="target")
    assert second["symbols"][0]["name"] == "target_action"
    assert calls == ["mod.py"], "同一份没变的文件不该被重复解析"

    old = (tmp_path / "mod.py").stat()
    (tmp_path / "mod.py").write_text("def target_action():\n    pass\ndef target_new():\n    pass\n",
                                     encoding="utf-8")
    import os
    os.utime(tmp_path / "mod.py", ns=(old.st_atime_ns, old.st_mtime_ns + 1_000_000_000))
    third = ss.search_symbols(tmp_path, query="target")
    assert calls == ["mod.py", "mod.py"] and third["total_found"] == 2


def test_symbol_index_survives_restart_and_invalidates_changed_files(tmp_path, monkeypatch):
    import os
    from minicode import symbol_index
    from minicode import symbol_search as ss
    calls = []
    real = ss.extract_python_symbols

    def spy(code, rel):
        calls.append(rel)
        return real(code, rel)

    monkeypatch.setattr(ss, "extract_python_symbols", spy)
    ss._extract_cached.cache_clear()
    symbol_index._CACHE.clear()
    source = tmp_path / "mod.py"
    source.write_text("def alpha():\n    pass\n", encoding="utf-8")
    assert ss.search_symbols(tmp_path)["total_found"] == 1
    assert calls == ["mod.py"]

    # 模拟换一个进程：内存缓存全清，磁盘索引还在。
    ss._extract_cached.cache_clear()
    symbol_index._CACHE.clear()
    assert ss.search_symbols(tmp_path, query="alpha")["total_found"] == 1
    assert calls == ["mod.py"], "重启后不应重新解析未变化的文件"

    old = source.stat()
    source.write_text("def beta():\n    pass\n", encoding="utf-8")
    os.utime(source, ns=(old.st_atime_ns, old.st_mtime_ns + 1_000_000_000))
    ss._extract_cached.cache_clear()
    symbol_index._CACHE.clear()
    assert ss.search_symbols(tmp_path, query="beta")["total_found"] == 1
    assert calls == ["mod.py", "mod.py"], "变化的文件必须重新解析"

    source.unlink()
    ss._extract_cached.cache_clear()
    symbol_index._CACHE.clear()
    assert ss.search_symbols(tmp_path)["total_found"] == 0
    assert symbol_index._target(tmp_path).is_file()


def test_corrupt_symbol_index_is_ignored(tmp_path, monkeypatch):
    from minicode import symbol_index
    from minicode import symbol_search as ss
    ss._extract_cached.cache_clear()
    symbol_index._CACHE.clear()
    (tmp_path / "mod.py").write_text("def alpha():\n    pass\n", encoding="utf-8")
    target = symbol_index._target(tmp_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{broken", encoding="utf-8")
    symbol_index._CACHE.clear()
    assert ss.search_symbols(tmp_path, query="alpha")["total_found"] == 1


def test_symbol_index_isolated_between_workspaces(tmp_path):
    from minicode import symbol_index
    from minicode import symbol_search as ss
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    (first / "one.py").write_text("def only_here():\n    pass\n", encoding="utf-8")
    (second / "two.py").write_text("def somewhere_else():\n    pass\n", encoding="utf-8")
    ss._extract_cached.cache_clear()
    symbol_index._CACHE.clear()
    assert ss.search_symbols(first, query="only_here")["total_found"] == 1
    assert ss.search_symbols(second, query="only_here")["total_found"] == 0
    assert symbol_index._target(first) != symbol_index._target(second)


def test_tool_no_ctx():
    got = symbol_search.symbol_search_tool(None, query="test")
    assert "无法获取当前工作区路径" in got


def test_tool_format(tmp_path: Path):
    (tmp_path / "mod.py").write_text("def do_action(): pass\n", encoding="utf-8")
    ctx = MagicMock(spec=ToolContext)
    ctx.workspace = tmp_path

    got = symbol_search.symbol_search_tool(ctx, query="action")
    assert "匹配 1 项" in got
    assert "mod.py:1" in got
    assert "do_action" in got

    got_none = symbol_search.symbol_search_tool(ctx, query="nonexistent")
    assert "未在工作区找到匹配的符号" in got_none


@pytest.mark.parametrize("args,summary,names", [
    ({}, "匹配 4 项", ("target_action", "target_extra", "TargetService", "target_outside")),
    ({"query": "action"}, "匹配 1 项", ("target_action",)),
    ({"query": "target", "kind": "function", "path": "src", "max_results": 1},
     "匹配 2 项, 显示前 1 项", ("target_action",)),
    ({"query": "nonexistent"}, "未在工作区找到匹配的符号", ()),
], ids=["defaults", "query", "all-parameters", "no-match"])
def test_registered_tool_runs_through_agent(tmp_path: Path, ctx: ToolContext, args, summary, names):
    src = tmp_path / "src"
    src.mkdir()
    (src / "mod.py").write_text(
        "def target_action(): pass\ndef target_extra(): pass\nclass TargetService: pass\n",
        encoding="utf-8",
    )
    (tmp_path / "outside.py").write_text("def target_outside(): pass\n", encoding="utf-8")
    tool = next(t for t in build_default_registry() if t.name == "symbol_search")
    assert tool.handler is symbol_search.symbol_search_tool
    # 只初始化执行入口所需的 ctx,不启动 Agent 会话或读取真实配置。
    agent = Agent.__new__(Agent)
    agent.ctx = ctx

    result, elapsed_ms = agent._run_handler(tool, args)

    assert summary in result
    for name in ("target_action", "target_extra", "TargetService", "target_outside"):
        assert (name in result) == (name in names)
    if "target_action" in names:
        assert "src/mod.py:1" in result
    assert isinstance(elapsed_ms, int)
    assert elapsed_ms >= 0
