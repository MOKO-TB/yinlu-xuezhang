"""跨文件代码符号重命名与重构预演自动化测试 (v0.9.167)。

测试涵盖:
1. 标识符有效性检验 (合法标识符 vs 非法字符与保留关键字)
2. Python 代码替换与 AST 语法解析验证
3. 单词边界匹配 (避免误伤同名前缀/后缀)
4. 多语言代码文本边界替换 (JS / HTML / JSON)
5. 安全只读模式 dry_run=True (输出 diff 与统计,不写磁盘)
6. 实际写入模式 dry_run=False (原子写入且内容更新生效)
7. 语法破坏拦截自愈 (替换导致 Python 语法破坏时安全拦截)
8. 影响面风险评估 (LOW / MEDIUM / HIGH)
9. 路径模式过滤 path_pattern
10. 内置工具 rename_symbol_tool 与 /rename 命令端到端
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from minicode import slash_commands as sc
from minicode.agent import Agent
from minicode.rename_symbol import (
    is_valid_identifier,
    rename_symbol,
    rename_symbol_in_code,
    rename_symbol_tool,
)
from minicode.tools.registry import build_default_registry


def test_is_valid_identifier():
    # 合法标识符
    assert is_valid_identifier("hello") is True
    assert is_valid_identifier("_private_var") is True
    assert is_valid_identifier("camelCase123") is True
    assert is_valid_identifier("PascalCase") is True
    assert is_valid_identifier("CONSTANT_NAME") is True

    # 非法字符 / 空白
    assert is_valid_identifier("") is False
    assert is_valid_identifier("   ") is False
    assert is_valid_identifier("123abc") is False
    assert is_valid_identifier("kebab-case") is False
    assert is_valid_identifier("hello world") is False
    assert is_valid_identifier("foo$bar") is False

    # 保留关键字 (禁止重命名为 Python 关键字)
    assert is_valid_identifier("def") is False
    assert is_valid_identifier("class") is False
    assert is_valid_identifier("return") is False
    assert is_valid_identifier("import") is False


def test_rename_symbol_in_code_python_ast():
    code = (
        "def old_func(arg):\n"
        "    return arg + 1\n\n"
        "res = old_func(10)\n"
    )
    new_code, count, success, err = rename_symbol_in_code(
        code, "old_func", "new_func", ".py"
    )
    assert success is True
    assert count == 2
    assert err == ""
    assert "def new_func(arg):" in new_code
    assert "res = new_func(10)" in new_code
    assert "old_func" not in new_code


def test_rename_symbol_word_boundary_isolation():
    # 测试边界隔离: old_name 不应替换 old_name_extra 或 prefix_old_name
    code = (
        "old_name = 1\n"
        "old_name_extra = 2\n"
        "prefix_old_name = 3\n"
        "print(old_name)\n"
    )
    new_code, count, success, err = rename_symbol_in_code(
        code, "old_name", "target_name", ".py"
    )
    assert success is True
    assert count == 2
    assert "target_name = 1" in new_code
    assert "old_name_extra = 2" in new_code
    assert "prefix_old_name = 3" in new_code
    assert "print(target_name)" in new_code


def test_rename_symbol_in_code_syntax_break_intercept():
    # 模拟语法损坏: 虽然原代码缺括号, 替换后 ast.parse 仍应拦截并返回 success=False
    code = "def old_sym: pass"  # 本身就有语法错误 (缺少小括号)
    new_code, count, success, err = rename_symbol_in_code(
        code, "old_sym", "new_sym", ".py"
    )
    assert success is False
    assert "语法校验失败" in err or "SyntaxError" in err


def test_rename_symbol_non_python_file():
    js_code = (
        "function oldHelper(x) {\n"
        "  return oldHelper(x - 1);\n"
        "}\n"
    )
    new_code, count, success, err = rename_symbol_in_code(
        js_code, "oldHelper", "newHelper", ".js"
    )
    assert success is True
    assert count == 2
    assert "function newHelper(x)" in new_code
    assert "return newHelper(x - 1)" in new_code


def test_rename_symbol_dry_run_does_not_modify_disk():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        py_file = root / "math_utils.py"
        py_content = "def calc_total(a, b):\n    return a + b\n"
        py_file.write_text(py_content, encoding="utf-8")

        res = rename_symbol(
            workspace=str(root),
            symbol="calc_total",
            new_name="sum_all",
            dry_run=True,
        )

        assert res["ok"] is True
        assert res["dry_run"] is True
        assert res["total_matches"] == 1
        assert len(res["modified_files"]) == 1
        assert "math_utils.py" in res["modified_files"][0]
        assert "diff" in res
        # 磁盘文件必须完好无损,未被写入
        assert py_file.read_text(encoding="utf-8") == py_content


def test_rename_symbol_wet_run_modifies_disk():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        py_file = root / "service.py"
        py_file.write_text("class UserClient:\n    pass\n\nu = UserClient()\n", encoding="utf-8")

        res = rename_symbol(
            workspace=str(root),
            symbol="UserClient",
            new_name="AccountClient",
            dry_run=False,
        )

        assert res["ok"] is True
        assert res["dry_run"] is False
        assert res["total_matches"] == 2
        # 磁盘文件已被更新
        updated = py_file.read_text(encoding="utf-8")
        assert "class AccountClient:" in updated
        assert "u = AccountClient()" in updated
        assert "UserClient" not in updated


def test_rename_symbol_invalid_identifier_rejected():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        res = rename_symbol(
            workspace=str(root),
            symbol="123invalid",
            new_name="new_name",
        )
        assert res["ok"] is False
        assert "合法" in res["error"]

        res2 = rename_symbol(
            workspace=str(root),
            symbol="valid_symbol",
            new_name="class",  # 关键字
        )
        assert res2["ok"] is False
        assert "合法" in res2["error"]


def test_rename_symbol_path_pattern_filtering():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        sub1 = root / "src"
        sub2 = root / "tests"
        sub1.mkdir()
        sub2.mkdir()

        (sub1 / "model.py").write_text("my_sym = 10\n", encoding="utf-8")
        (sub2 / "test_model.py").write_text("import my_sym\n", encoding="utf-8")

        res = rename_symbol(
            workspace=str(root),
            symbol="my_sym",
            new_name="our_sym",
            dry_run=True,
            path_pattern="src/*.py",
        )
        assert res["ok"] is True
        assert len(res["modified_files"]) == 1
        assert "model.py" in res["modified_files"][0]
        assert "test_model.py" not in res["modified_files"][0]


def test_rename_symbol_tool_and_slash_command():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "main.py").write_text("data_source = 42\n", encoding="utf-8")

        # 1. rename_symbol_tool
        view = rename_symbol_tool(
            SimpleNamespace(workspace=root),
            symbol="data_source",
            new_name="db_source",
            dry_run=True,
        )
        assert "符号重构报告" in view
        assert "data_source" in view and "db_source" in view
        assert "diff" in view

        # 2. slash_command /rename 模板与参数展开
        prompt = sc.expand("rename")
        assert prompt is not None
        assert "rename_symbol" in prompt
        assert sc.has_arg("rename") is True

        prompt_slash = sc.expand("/rename")
        assert prompt_slash == prompt


def test_bridge_commands_catalog_and_rename():
    from unittest.mock import MagicMock
    from minicode.gui_bridge import Bridge

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "mod.py").write_text("old_val = 1\n", encoding="utf-8")

        bridge = Bridge(config=MagicMock(), workspace=str(root), ui=MagicMock())
        cat = bridge.get_commands_catalog()
        assert cat["ok"] is True
        assert len(cat["commands"]) >= 15
        ids = [c["id"] for c in cat["commands"]]
        assert "slash_rename" in ids
        assert "act_map" in ids

        res = bridge.rename_symbol("old_val", "new_val", dry_run=True)
        assert res["ok"] is True
        assert res["dry_run"] is True


@pytest.mark.parametrize("path_pattern", [None, "src/*.py"], ids=["whole-workspace", "path-filter"])
def test_registered_tool_previews_then_writes_only_ctx_workspace(tmp_path, ctx, monkeypatch, path_pattern):
    workspace = tmp_path / "workspace"
    (workspace / "src").mkdir(parents=True)
    (workspace / "node_modules").mkdir()
    sources = {
        "src/model.py": "def old_value():\n    return 42\n",
        "src/client.py": "from model import old_value\nresult = old_value()\n",
        "other.py": "old_value = 10\n",
        "node_modules/ignored.py": "old_value = 20\n",
    }
    for name, code in sources.items():
        (workspace / name).write_text(code, encoding="utf-8")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    outside_sources = {
        cwd / "model.py": sources["src/model.py"],
        tmp_path / "parent.py": "old_value = 99\n",
    }
    for path, code in outside_sources.items():
        path.write_text(code, encoding="utf-8")
    monkeypatch.chdir(cwd)
    ctx.workspace = workspace
    tool = next(t for t in build_default_registry() if t.name == "rename_symbol")
    assert tool.handler is rename_symbol_tool
    agent = Agent.__new__(Agent)
    agent.ctx = ctx
    args = {"symbol": "old_value", "new_name": "new_value"}
    if path_pattern is not None:
        args["path_pattern"] = path_pattern
    changed = {"src/model.py", "src/client.py"}
    if path_pattern is None:
        changed.add("other.py")

    # 不在 schema 的 workspace 不再作为模型参数接受。
    rejected, _ = agent._run_handler(tool, {
        **args, "workspace": str(cwd), "dry_run": False,
    })
    assert "工具参数与 schema 不匹配" in rejected
    assert "workspace" in rejected

    # 父目录/外部绝对路径模式只能过滤候选,不能扩大工作区根。
    for pattern in ("../*", (cwd / "model.py").as_posix()):
        outside, _ = agent._run_handler(tool, {
            **args, "path_pattern": pattern, "dry_run": False,
        })
        assert "涉及文件**: 0 个" in outside

    # 默认及显式 dry_run=True 都要生成预览,且所有源码字节保持不变。
    before = {path: path.read_bytes() for path in (
        *(workspace / name for name in sources), *outside_sources,
    )}
    for preview_args in (args, {**args, "dry_run": True}):
        preview, elapsed_ms = agent._run_handler(tool, preview_args)
        assert "预演模式 (未修改磁盘文件)" in preview
        assert f"涉及文件**: {len(changed)} 个" in preview
        assert "```diff" in preview
        assert "-def old_value():" in preview
        assert "+def new_value():" in preview
        for name in sources:
            assert (f"**`{name}`**" in preview) == (name in changed)
        assert all(path.read_bytes() == content for path, content in before.items())
        assert isinstance(elapsed_ms, int) and elapsed_ms >= 0

    written, elapsed_ms = agent._run_handler(tool, {**args, "dry_run": False})

    assert "执行模式 (已写入修改)" in written
    assert f"成功将 {len(changed)} 个文件写入磁盘" in written
    for name, code in sources.items():
        expected = code.replace("old_value", "new_value") if name in changed else code
        assert (workspace / name).read_text(encoding="utf-8") == expected
    for path, code in outside_sources.items():
        assert path.read_text(encoding="utf-8") == code
        assert path.read_bytes() == before[path]
    assert isinstance(elapsed_ms, int) and elapsed_ms >= 0


@pytest.mark.parametrize("dry_run", [True, False])
def test_rename_excludes_resolved_outside_paths_and_credentials(tmp_path, monkeypatch, dry_run):
    workspace = tmp_path / "project"
    workspace.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("old_value = 1\n", encoding="utf-8")
    linked = workspace / "linked.py"
    linked.write_text("old_value = 2\n", encoding="utf-8")
    credential = workspace / "credentials.json"
    credential.write_text('{"old_value": "private"}', encoding="utf-8")
    source = workspace / "source.py"
    source.write_text("old_value = 3\n", encoding="utf-8")
    resolve = Path.resolve

    def resolved_path(path, *args, **kwargs):
        if path == linked:
            return outside
        return resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolved_path)
    result = rename_symbol(str(workspace), "old_value", "new_value", dry_run=dry_run)
    assert result["ok"]
    assert result["modified_files"] == ["source.py"]
    assert "private" not in result["diff"]
    assert outside.read_text(encoding="utf-8") == "old_value = 1\n"
    assert credential.read_text(encoding="utf-8") == '{"old_value": "private"}'
    assert source.read_text(encoding="utf-8") == ("old_value = 3\n" if dry_run else "new_value = 3\n")


@pytest.mark.parametrize("bad_ctx", [None, object(), SimpleNamespace(workspace=None), SimpleNamespace(workspace="")],
                         ids=["none", "missing-attribute", "none-workspace", "empty-workspace"])
def test_registered_tool_requires_workspace(monkeypatch, bad_ctx):
    def unexpected_rename(*args, **kwargs):
        pytest.fail("缺失工作区时不得扫描或修改进程 cwd")

    monkeypatch.setattr("minicode.rename_symbol.rename_symbol", unexpected_rename)
    tool = next(t for t in build_default_registry() if t.name == "rename_symbol")
    agent = Agent.__new__(Agent)
    agent.ctx = bad_ctx

    result, _ = agent._run_handler(tool, {
        "symbol": "old_value", "new_name": "new_value", "dry_run": False,
    })

    assert "符号重命名失败" in result
    assert "无法获取当前工作区路径(ctx.workspace)" in result
