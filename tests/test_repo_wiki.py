"""`minicode/repo_wiki.py` 的单元测试。

验证:
1. 代码库目录扫描与模块归纳(忽略项、顶层子目录分类);
2. 架构概览与配置命令嗅探(pyproject.toml, package.json 等);
3. 约定提取(AGENTS.md, CONTRIBUTING.md);
4. 缓存落盘与加载(APP_DIR/repo-wiki/<hash>.json);
5. 视图格式化(summary, module, commands, conventions, refresh);
6. 工具调用(repo_wiki_tool)正常回执与容错。
"""

from minicode import repo_wiki
from minicode.config import Config
from minicode.tools.base import ToolContext


def test_scan_workspace_empty_directory(tmp_path):
    data = repo_wiki.scan_workspace(tmp_path)
    assert data["name"] == tmp_path.name
    assert isinstance(data["modules"], list)
    assert isinstance(data["commands"], dict)
    assert "暂无" in data["overview"] or "暂无 README" in data["overview"]


def test_scan_workspace_detects_project_info_and_commands(tmp_path):
    # 模拟真实项目结构
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo-agent"\nversion = "1.0.0"\n[tool.pytest]\n',
        encoding="utf-8"
    )
    (tmp_path / "README.md").write_text(
        "# Demo Agent\n\n这是一个用于测试的代码库百科生成工具。\n核心业务为智能体编排与执行。",
        encoding="utf-8"
    )
    (tmp_path / "AGENTS.md").write_text(
        "# 开发者守则\n- 1. 不要使用绝对路径\n- 2. 必须编写单元测试\n",
        encoding="utf-8"
    )

    src = tmp_path / "src"
    src.mkdir()
    (src / "main.py").write_text("print('hello')\n", encoding="utf-8")
    (src / "utils.py").write_text("def add(a, b): return a + b\n", encoding="utf-8")

    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_main.py").write_text("def test_it(): pass\n", encoding="utf-8")

    # 忽略项应该被跳过
    ignored = tmp_path / ".git"
    ignored.mkdir()
    (ignored / "config").write_text("dummy", encoding="utf-8")

    tmp_dir = tmp_path / "_tmp_build"
    tmp_dir.mkdir()
    (tmp_dir / "cache.bin").write_text("dummy", encoding="utf-8")

    data = repo_wiki.scan_workspace(tmp_path)
    assert data["name"] == "demo-agent"
    assert "这是一个用于测试的代码库百科生成工具" in data["overview"]
    assert "pytest" in data["commands"].get("test", "")
    assert "不要使用绝对路径" in data["conventions"]

    mod_names = [m["name"] for m in data["modules"]]
    assert "src" in mod_names
    assert "tests" in mod_names
    assert ".git" not in mod_names
    assert "_tmp_build" not in mod_names

    # 验证测试目录推断
    tests_mod = next(m for m in data["modules"] if m["name"] == "tests")
    assert "测试" in tests_mod["purpose"]
    assert tests_mod["file_count"] == 1


def test_save_and_load_wiki_cache(tmp_path):
    data = {
        "name": "cached-proj",
        "workspace": str(tmp_path),
        "languages": ["Python"],
        "overview": "测试缓存",
        "commands": {"test": "pytest"},
        "modules": [{"name": "core", "path": "core", "file_count": 2, "purpose": "核心", "key_files": []}],
        "conventions": "测试约定",
        "updated_at": "2026-10-12 12:00:00",
    }
    assert repo_wiki.save_wiki(tmp_path, data) is True
    loaded = repo_wiki.load_wiki(tmp_path)
    assert loaded is not None
    assert loaded["name"] == "cached-proj"
    assert loaded["commands"]["test"] == "pytest"

    # 清除生成的缓存文件
    wiki_file = repo_wiki.get_wiki_file(tmp_path)
    if wiki_file.is_file():
        wiki_file.unlink()


def test_format_wiki_view_all_actions(tmp_path):
    data = {
        "name": "my-tool",
        "workspace": str(tmp_path),
        "languages": ["Python", "JavaScript"],
        "total_files": 42,
        "overview": "通用工程脚手架",
        "commands": {"test": "pytest -q", "build": "npm run build"},
        "modules": [
            {"name": "backend", "path": "backend", "file_count": 20, "purpose": "后端服务", "key_files": ["api.py"]},
            {"name": "frontend", "path": "frontend", "file_count": 22, "purpose": "前端展示", "key_files": ["app.js"]},
        ],
        "conventions": "统一使用 UTF-8 编码",
        "updated_at": "2026-10-12 12:00:00",
    }

    # 1. summary
    summary = repo_wiki.format_wiki_view(data, action="summary")
    assert "代码库全景百科: my-tool" in summary
    assert "后端服务" in summary
    assert "pytest -q" in summary

    # 2. commands
    cmds = repo_wiki.format_wiki_view(data, action="commands")
    assert "pytest -q" in cmds
    assert "npm run build" in cmds

    # 3. conventions
    convs = repo_wiki.format_wiki_view(data, action="conventions")
    assert "统一使用 UTF-8 编码" in convs

    # 4. module - 命中
    mod_info = repo_wiki.format_wiki_view(data, action="module", target="backend")
    assert "后端服务" in mod_info
    assert "api.py" in mod_info

    # 5. module - 未命中
    mod_missing = repo_wiki.format_wiki_view(data, action="module", target="unknown")
    assert "未找到名为「unknown」的模块" in mod_missing


def test_repo_wiki_tool_execution(tmp_path):
    (tmp_path / "README.md").write_text("# Demo Tool\n\n简易演示", encoding="utf-8")
    ctx = ToolContext(workspace=tmp_path, config=Config())

    # 首次执行自动扫描并返回 summary
    res = repo_wiki.repo_wiki_tool(ctx, action="summary")
    assert "代码库全景百科: " in res
    assert "简易演示" in res

    # 刷新测试
    res_refresh = repo_wiki.repo_wiki_tool(ctx, action="refresh")
    assert "代码库全景百科: " in res_refresh

    # 清理缓存
    wiki_file = repo_wiki.get_wiki_file(tmp_path)
    if wiki_file.is_file():
        wiki_file.unlink()
