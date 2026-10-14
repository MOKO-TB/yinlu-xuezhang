"""symbol_definition 跨文件符号定义提取与实现跳转测试 (v0.9.169)。"""
from __future__ import annotations

from unittest.mock import MagicMock


from minicode.symbol_definition import (
    extract_python_definition,
    extract_multilang_definition,
    resolve_import_definition,
    find_symbol_definition,
    symbol_definition_tool,
)


def test_extract_python_function_definition():
    code = '''
def calculate_metrics(a: int, b: int = 10) -> int:
    """Calculate combined metrics."""
    res = a + b
    return res * 2

def other_func():
    pass
'''
    defs = extract_python_definition(code, "calculate_metrics", "test.py")
    assert len(defs) == 1
    d = defs[0]
    assert d["name"] == "calculate_metrics"
    assert d["kind"] == "function"
    assert d["file"] == "test.py"
    assert d["line"] == 2
    assert d["end_line"] == 5
    assert "def calculate_metrics" in d["signature"]
    assert "Calculate combined metrics" in d["docstring"]
    assert "return res * 2" in d["code"]


def test_extract_python_async_function():
    code = '''
async def fetch_remote_data(url: str) -> dict:
    """Fetch data asynchronously."""
    return {"status": "ok"}
'''
    defs = extract_python_definition(code, "fetch_remote_data", "network.py")
    assert len(defs) == 1
    d = defs[0]
    assert d["name"] == "fetch_remote_data"
    assert d["kind"] == "async_function"
    assert "async def fetch_remote_data" in d["signature"]
    assert d["line"] == 2
    assert d["end_line"] == 4


def test_extract_python_class_and_methods():
    code = '''
class OrderProcessor:
    """Processes customer orders."""

    def __init__(self, order_id: str):
        self.order_id = order_id

    def process(self) -> bool:
        """Execute processing."""
        return True
'''
    # 查类本身
    class_defs = extract_python_definition(code, "OrderProcessor", "order.py")
    assert len(class_defs) == 1
    assert class_defs[0]["kind"] == "class"
    assert class_defs[0]["line"] == 2
    assert class_defs[0]["end_line"] == 10

    # 查方法本身
    method_defs = extract_python_definition(code, "process", "order.py")
    assert len(method_defs) == 1
    assert method_defs[0]["name"] == "process"
    assert method_defs[0]["kind"] == "method"
    assert method_defs[0]["parent"] == "OrderProcessor"
    assert method_defs[0]["line"] == 8
    assert method_defs[0]["end_line"] == 10

    # 点号限定查询 Class.method
    dot_defs = extract_python_definition(code, "OrderProcessor.process", "order.py")
    assert len(dot_defs) == 1
    assert dot_defs[0]["name"] == "process"
    assert dot_defs[0]["parent"] == "OrderProcessor"


def test_extract_python_decorators():
    code = '''
@property
@lru_cache(maxsize=128)
def cached_value(self):
    """Cached computation."""
    return 42
'''
    defs = extract_python_definition(code, "cached_value", "cached.py")
    assert len(defs) == 1
    d = defs[0]
    assert d["line"] == 2  # 包含第一个装饰器行号
    assert d["end_line"] == 6
    assert "@property" in d["code"]
    assert "return 42" in d["code"]


def test_extract_python_constants():
    code = '''
DEFAULT_TIMEOUT = 30
MAX_BUFFER_SIZE: int = 1024 * 1024

def helper():
    pass
'''
    defs = extract_python_definition(code, "DEFAULT_TIMEOUT", "config.py")
    assert len(defs) == 1
    assert defs[0]["kind"] == "constant"
    assert defs[0]["line"] == 2

    ann_defs = extract_python_definition(code, "MAX_BUFFER_SIZE", "config.py")
    assert len(ann_defs) == 1
    assert ann_defs[0]["kind"] == "constant"
    assert ann_defs[0]["line"] == 3


def test_extract_multilang_js_ts():
    code = '''
export class UserService {
    getUser(id) {
        return { id };
    }
}

export function formatName(name) {
    return name.trim();
}

export const renderItem = (item) => {
    return "<div>" + item + "</div>";
};

export interface UserConfig {
    theme: string;
}
'''
    cls_defs = extract_multilang_definition(code, "UserService", "user.ts", "typescript")
    assert len(cls_defs) == 1
    assert cls_defs[0]["kind"] == "class"
    assert cls_defs[0]["line"] == 2
    assert "return { id };" in cls_defs[0]["code"]

    fn_defs = extract_multilang_definition(code, "formatName", "user.ts", "typescript")
    assert len(fn_defs) == 1
    assert fn_defs[0]["kind"] == "function"

    arrow_defs = extract_multilang_definition(code, "renderItem", "user.ts", "typescript")
    assert len(arrow_defs) == 1
    assert arrow_defs[0]["kind"] == "function"

    iface_defs = extract_multilang_definition(code, "UserConfig", "user.ts", "typescript")
    assert len(iface_defs) == 1
    assert iface_defs[0]["kind"] == "interface"


def test_extract_multilang_go_and_rust():
    go_code = '''
package main

type Engine struct {
    Power int
}

func StartEngine(e *Engine) bool {
    return e.Power > 0
}
'''
    go_struct = extract_multilang_definition(go_code, "Engine", "engine.go", "go")
    assert len(go_struct) == 1
    assert go_struct[0]["kind"] == "struct"

    go_func = extract_multilang_definition(go_code, "StartEngine", "engine.go", "go")
    assert len(go_func) == 1
    assert go_func[0]["kind"] == "function"
    assert "return e.Power > 0" in go_func[0]["code"]

    rs_code = '''
pub struct Config {
    pub port: u16,
}

pub async fn run_server(cfg: Config) -> Result<(), ()> {
    Ok(())
}
'''
    rs_struct = extract_multilang_definition(rs_code, "Config", "server.rs", "rust")
    assert len(rs_struct) == 1
    assert rs_struct[0]["kind"] == "struct"

    rs_fn = extract_multilang_definition(rs_code, "run_server", "server.rs", "rust")
    assert len(rs_fn) == 1
    assert rs_fn[0]["kind"] == "function"


def test_resolve_import_definition(tmp_path):
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    
    calc_py = pkg_dir / "calculator.py"
    calc_py.write_text('''
def add_numbers(x: int, y: int) -> int:
    """Add two integers."""
    return x + y
''', encoding="utf-8")

    main_py = tmp_path / "main.py"
    main_py.write_text('''
from pkg.calculator import add_numbers

def run():
    print(add_numbers(1, 2))
''', encoding="utf-8")

    resolved = resolve_import_definition(tmp_path, "main.py", "add_numbers")
    assert resolved is not None
    assert resolved["name"] == "add_numbers"
    assert resolved["file"] == "pkg/calculator.py"
    assert resolved["line"] == 2
    assert resolved["end_line"] == 4
    assert resolved.get("resolved_from_import") is True


def test_find_symbol_definition_workspace(tmp_path):
    (tmp_path / "a.py").write_text('''
def my_target_symbol():
    return "alpha"
''', encoding="utf-8")
    (tmp_path / "b.py").write_text('''
class MyContainer:
    def my_target_symbol(self):
        return "beta"
''', encoding="utf-8")

    res = find_symbol_definition(tmp_path, "my_target_symbol")
    assert res["ok"] is True
    assert res["count"] == 2
    files = {d["file"] for d in res["definitions"]}
    assert "a.py" in files
    assert "b.py" in files

    # 未找到符号
    not_found = find_symbol_definition(tmp_path, "non_existent_symbol")
    assert not_found["ok"] is True
    assert not_found["count"] == 0


def test_symbol_definition_tool_output(tmp_path):
    (tmp_path / "core.py").write_text('''
def perform_action(action_id: str) -> str:
    """Perform core action."""
    return f"Done: {action_id}"
''', encoding="utf-8")

    class FakeCtx:
        workspace = tmp_path

    ctx = FakeCtx()
    output = symbol_definition_tool(ctx, "perform_action")
    assert "找到 1 处符号 `perform_action` 的定义与实现位置" in output
    assert "core.py:2" in output
    assert "```python" in output
    assert "return f\"Done: {action_id}\"" in output

    # 未找到符号
    missing_output = symbol_definition_tool(ctx, "missing_symbol")
    assert "未找到符号 `missing_symbol` 的定义" in missing_output


def test_symbol_definition_peek_false(tmp_path):
    (tmp_path / "core.py").write_text('''
def do_work():
    pass
''', encoding="utf-8")

    res = find_symbol_definition(tmp_path, "do_work", peek=False)
    assert res["ok"] is True
    assert res["count"] == 1
    assert "code" not in res["definitions"][0]


def test_bridge_get_symbol_definition(tmp_path):
    (tmp_path / "app.py").write_text("def run_app():\n    return 1\n", encoding="utf-8")
    from minicode.gui_bridge import Bridge

    bridge = Bridge(config=MagicMock(), workspace=tmp_path, ui=MagicMock())
    res = bridge.get_symbol_definition("run_app")
    assert res["ok"] is True
    assert res["count"] == 1
    assert res["definitions"][0]["name"] == "run_app"

    # PublicApi 门面测试
    api = bridge.public_api()
    api_res = api.get_symbol_definition("run_app")
    assert api_res["ok"] is True
    assert api_res["count"] == 1

