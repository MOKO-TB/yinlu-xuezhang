"""单元测试:代码结构大纲与类/成员树 (minicode/code_outline.py, v0.9.170)。"""
from pathlib import Path

from minicode.code_outline import CodeOutlineExtractor, code_outline_tool
from minicode.gui_bridge import Bridge, PublicApi


def test_extract_python_classes_and_methods(tmp_path: Path):
    extractor = CodeOutlineExtractor(tmp_path)
    py_file = tmp_path / "sample_classes.py"
    py_file.write_text(
        '"""模块文档"""\n'
        "import os\n"
        "from typing import Optional\n\n"
        "MAX_COUNT: int = 100\n"
        "DEFAULT_NAME = 'test'\n\n"
        "@dataclass\n"
        "class Animal:\n"
        '    """动物基类"""\n'
        "    name: str\n\n"
        "class Dog(Animal, object):\n"
        '    """狗类"""\n'
        "    SPECIES = 'CANINE'\n\n"
        "    def __init__(self, name: str, age: int = 1) -> None:\n"
        '        """构造函数"""\n'
        "        super().__init__(name)\n"
        "        self.age = age\n\n"
        "    @property\n"
        "    def is_puppy(self) -> bool:\n"
        "        return self.age < 2\n\n"
        "    async def bark(self, times: int = 3) -> str:\n"
        '        """叫声"""\n'
        "        return 'woof' * times\n",
        encoding="utf-8",
    )

    data = extractor.extract_outline("sample_classes.py", depth=2)
    assert data["ok"] is True
    assert data["language"] == "Python"
    assert data["total_lines"] >= 25

    items = data["items"]
    # 含有 imports
    assert items[0].kind == "imports"
    assert "2 项" in items[0].name

    # 含有 MAX_COUNT 常量
    const_item = next(it for it in items if it.name == "MAX_COUNT")
    assert const_item.kind == "const"
    assert "MAX_COUNT: int" in const_item.signature

    # 含有 Animal 类
    animal_item = next(it for it in items if it.name == "Animal")
    assert animal_item.kind == "class"
    assert animal_item.docstring == "动物基类"
    assert "dataclass" in animal_item.decorators[0]

    # 含有 Dog 类及其子方法
    dog_item = next(it for it in items if it.name == "Dog")
    assert dog_item.kind == "class"
    assert dog_item.bases == ["Animal", "object"]
    assert dog_item.docstring == "狗类"

    # Dog 的成员方法
    method_names = [m.name for m in dog_item.children]
    assert "__init__" in method_names
    assert "is_puppy" in method_names
    assert "bark" in method_names

    bark_method = next(m for m in dog_item.children if m.name == "bark")
    assert bark_method.kind == "method" or bark_method.kind == "async_func"
    assert "async def bark(self, times: int = 3) -> str" in bark_method.signature
    assert bark_method.docstring == "叫声"


def test_extract_python_functions_and_signatures(tmp_path: Path):
    extractor = CodeOutlineExtractor(tmp_path)
    py_file = tmp_path / "sample_funcs.py"
    py_file.write_text(
        "def compute_score(base: float, bonus: float = 0.0, *flags: str, **meta: Any) -> float:\n"
        '    """计算最终得分"""\n'
        "    return base + bonus\n\n"
        "async def fetch_remote(url: str, timeout: int = 30) -> dict:\n"
        '    """异步拉取远端数据"""\n'
        "    return {}\n",
        encoding="utf-8",
    )

    data = extractor.extract_outline("sample_funcs.py")
    assert data["ok"] is True

    items = data["items"]
    assert len(items) == 2

    compute_fn = items[0]
    assert compute_fn.kind == "function"
    assert compute_fn.name == "compute_score"
    assert "def compute_score(base: float, bonus: float = 0.0, *flags: str, **meta: Any) -> float" in compute_fn.signature
    assert compute_fn.docstring == "计算最终得分"

    fetch_fn = items[1]
    assert fetch_fn.kind == "async_func"
    assert fetch_fn.name == "fetch_remote"
    assert "async def fetch_remote(url: str, timeout: int = 30) -> dict" in fetch_fn.signature
    assert fetch_fn.docstring == "异步拉取远端数据"


def test_depth_folding(tmp_path: Path):
    extractor = CodeOutlineExtractor(tmp_path)
    py_file = tmp_path / "deep.py"
    py_file.write_text(
        "class Outer:\n"
        "    def method_one(self):\n"
        "        pass\n"
        "    def method_two(self):\n"
        "        pass\n",
        encoding="utf-8",
    )

    # depth = 1: 仅顶层类
    data1 = extractor.extract_outline("deep.py", depth=1)
    formatted1 = extractor.format_outline(data1)
    assert "class Outer" in formatted1
    assert "深度折叠" in formatted1

    # depth = 2: 展开方法
    data2 = extractor.extract_outline("deep.py", depth=2)
    formatted2 = extractor.format_outline(data2)
    assert "method_one" in formatted2
    assert "method_two" in formatted2


def test_multilang_typescript(tmp_path: Path):
    extractor = CodeOutlineExtractor(tmp_path)
    ts_file = tmp_path / "service.ts"
    ts_file.write_text(
        "export interface UserProfile {\n"
        "  id: string;\n"
        "  name: string;\n"
        "}\n\n"
        "export type Status = 'active' | 'inactive';\n\n"
        "export const MAX_RETRY = 5;\n\n"
        "export class AuthService {\n"
        "  login() {}\n"
        "}\n\n"
        "export async function verifyToken(token: string): Promise<boolean> {\n"
        "  return true;\n"
        "}\n",
        encoding="utf-8",
    )

    data = extractor.extract_outline("service.ts")
    assert data["ok"] is True
    assert data["language"] == "TypeScript"

    names = [it.name for it in data["items"]]
    assert "UserProfile" in names
    assert "Status" in names
    assert "MAX_RETRY" in names
    assert "AuthService" in names
    assert "verifyToken" in names


def test_multilang_golang(tmp_path: Path):
    extractor = CodeOutlineExtractor(tmp_path)
    go_file = tmp_path / "main.go"
    go_file.write_text(
        "package main\n\n"
        "const Version = \"1.0.0\"\n\n"
        "type Server struct {\n"
        "    port int\n"
        "}\n\n"
        "type Handler interface {\n"
        "    ServeHTTP()\n"
        "}\n\n"
        "func (s *Server) Start() error {\n"
        "    return nil\n"
        "}\n\n"
        "func Main() {\n"
        "}\n",
        encoding="utf-8",
    )

    data = extractor.extract_outline("main.go")
    assert data["ok"] is True
    assert data["language"] == "Go"

    names = [it.name for it in data["items"]]
    assert "Server" in names
    assert "Handler" in names
    assert "Start" in names
    assert "Main" in names


def test_multilang_rust(tmp_path: Path):
    extractor = CodeOutlineExtractor(tmp_path)
    rs_file = tmp_path / "lib.rs"
    rs_file.write_text(
        "pub struct Config {\n"
        "    pub debug: bool,\n"
        "}\n\n"
        "pub enum State {\n"
        "    Idle,\n"
        "    Running,\n"
        "}\n\n"
        "pub trait Runner {\n"
        "    fn run(&self);\n"
        "}\n\n"
        "impl Runner for Config {\n"
        "    fn run(&self) {}\n"
        "}\n\n"
        "pub async fn init_app() {}\n",
        encoding="utf-8",
    )

    data = extractor.extract_outline("lib.rs")
    assert data["ok"] is True
    assert data["language"] == "Rust"

    names = [it.name for it in data["items"]]
    assert "Config" in names
    assert "State" in names
    assert "Runner" in names
    assert "init_app" in names


def test_multilang_java(tmp_path: Path):
    extractor = CodeOutlineExtractor(tmp_path)
    java_file = tmp_path / "App.java"
    java_file.write_text(
        "public class App {\n"
        "    public static void main(String[] args) {\n"
        "    }\n"
        "}\n\n"
        "interface Greeter {\n"
        "    void greet();\n"
        "}\n",
        encoding="utf-8",
    )

    data = extractor.extract_outline("App.java")
    assert data["ok"] is True
    assert data["language"] == "Java"

    names = [it.name for it in data["items"]]
    assert "App" in names
    assert "Greeter" in names


def test_syntax_error_graceful_fallback(tmp_path: Path):
    extractor = CodeOutlineExtractor(tmp_path)
    py_file = tmp_path / "broken.py"
    py_file.write_text(
        "class BrokenClass:\n"
        "    def good(self):\n"
        "        pass\n"
        "def syntax_oops(:\n",  # 故意语法错误
        encoding="utf-8",
    )

    data = extractor.extract_outline("broken.py")
    assert data["ok"] is True
    assert data["syntax_error"] is not None
    assert "语法解析提示" in data["syntax_error"]

    formatted = extractor.format_outline(data)
    assert "BrokenClass" in formatted
    assert "语法解析提示" in formatted


def test_nonexistent_file_handling(tmp_path: Path):
    extractor = CodeOutlineExtractor(tmp_path)
    data = extractor.extract_outline("not_found.py")
    assert data["ok"] is False
    assert "文件不存在" in data["error"]

    formatted = extractor.format_outline(data)
    assert "❌ 无法提取大纲" in formatted


def test_tool_invocation(tmp_path: Path):
    test_file = tmp_path / "test_tool.py"
    test_file.write_text(
        "def hello_world():\n"
        '    """打招呼函数"""\n'
        "    return 'hello'\n",
        encoding="utf-8",
    )

    class FakeCtx:
        workspace = tmp_path

    ctx = FakeCtx()
    # 正常调用
    out = code_outline_tool(ctx, path="test_tool.py")
    assert "📄 文件大纲: test_tool.py" in out
    assert "hello_world" in out
    assert "打招呼函数" in out

    # 缺省路径调用
    err_out = code_outline_tool(ctx, path="")
    assert "❌ 请指定要提取大纲的文件路径" in err_out


def test_gui_bridge_get_code_outline(tmp_path: Path):
    from unittest.mock import MagicMock
    test_file = tmp_path / "bridge_test.py"
    test_file.write_text(
        "class BridgeSample:\n"
        "    def run(self):\n"
        "        pass\n",
        encoding="utf-8",
    )

    bridge = Bridge(config=MagicMock(), workspace=str(tmp_path), ui=MagicMock())
    api = PublicApi(bridge)

    res = bridge.get_code_outline("bridge_test.py")
    assert res["ok"] is True
    assert "BridgeSample" in res["text"]

    m_res = api.get_code_outline("bridge_test.py")
    assert m_res["ok"] is True
    assert "BridgeSample" in m_res["text"]

    cmd_cat = bridge.get_commands_catalog()
    assert cmd_cat["ok"] is True
    assert any(c["id"] == "slash_outline" for c in cmd_cat["commands"])
