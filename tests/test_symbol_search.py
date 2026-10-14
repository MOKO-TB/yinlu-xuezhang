"""symbol_search 跨语言精准符号搜索单元测试(v0.9.165)。"""
from pathlib import Path
from unittest.mock import MagicMock

from minicode import symbol_search
from minicode.tools.base import ToolContext


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


def test_tool_no_ctx():
    got = symbol_search.symbol_search_tool(query="test", ctx=None)
    assert "无法获取当前工作区路径" in got


def test_tool_format(tmp_path: Path):
    (tmp_path / "mod.py").write_text("def do_action(): pass\n", encoding="utf-8")
    ctx = MagicMock(spec=ToolContext)
    ctx.workspace = tmp_path

    got = symbol_search.symbol_search_tool(query="action", ctx=ctx)
    assert "匹配 1 项" in got
    assert "mod.py:1" in got
    assert "do_action" in got

    got_none = symbol_search.symbol_search_tool(query="nonexistent", ctx=ctx)
    assert "未在工作区找到匹配的符号" in got_none
