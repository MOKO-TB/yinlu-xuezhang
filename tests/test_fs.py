from minicode.tools import fs
from minicode.tools.fs import repo_outline


def test_write_then_read(ctx, tmp_path):
    assert fs.write_file(ctx, "a/b.txt", "hello\nworld").startswith("已创建")
    out = fs.read_file(ctx, "a/b.txt")
    assert "1\thello" in out
    assert "2\tworld" in out


def test_read_missing_and_dir(ctx, tmp_path):
    assert "不存在" in fs.read_file(ctx, "nope.txt")
    (tmp_path / "d").mkdir()
    assert "目录" in fs.read_file(ctx, "d")


def test_outside_workspace_rejected(ctx):
    assert "超出工作区" in fs.write_file(ctx, "../evil.txt", "x")
    assert "超出工作区" in fs.read_file(ctx, "C:/Windows/win.ini")


def test_read_offset_and_truncation(ctx):
    fs.write_file(ctx, "big.txt", "\n".join(f"line{i}" for i in range(1, 51)))
    out = fs.read_file(ctx, "big.txt", offset=45, limit=3)
    assert "line45" in out and "已截断" in out
    out_tail = fs.read_file(ctx, "big.txt", offset=45, limit=10)
    assert "line50" in out_tail and "已截断" not in out_tail
    out2 = fs.read_file(ctx, "big.txt", offset=999)
    assert "超出范围" in out2


def test_edit_requires_unique(ctx):
    fs.write_file(ctx, "f.py", "a = 1\na = 1\n")
    assert "不唯一" in fs.edit_file(ctx, "f.py", "a = 1", "a = 2")
    assert "已编辑" in fs.edit_file(ctx, "f.py", "a = 1", "a = 2", replace_all=True)
    assert fs.read_file(ctx, "f.py").count("a = 2") == 2


def test_edit_not_found_and_same(ctx):
    fs.write_file(ctx, "f.py", "x = 1\n")
    assert "未在文件中找到" in fs.edit_file(ctx, "f.py", "zzz", "y")
    assert "相同" in fs.edit_file(ctx, "f.py", "x = 1", "x = 1")


def test_edit_multiline_exact_match(ctx):
    fs.write_file(ctx, "f.py", "def foo():\n    return 1\n")
    assert "已编辑" in fs.edit_file(ctx, "f.py", "    return 1", "    return 2")
    assert "return 2" in fs.read_file(ctx, "f.py")


# ---------------- repo_outline(v0.9.52,借鉴 aider repo map 的 stdlib 简化版) ----------------

def test_repo_outline_lists_py_symbols_and_skips_junk(ctx, tmp_path):
    (tmp_path / "alpha.py").write_text(
        "MAX = 3\n\ndef load():\n    pass\n\nclass Loader:\n    pass\n",
        encoding="utf-8")
    sub = tmp_path / "pkg"
    sub.mkdir()
    (sub / "beta.py").write_text("def run():\n    pass\n", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "hidden.py").write_text("def hidden():\n    pass\n",
                                                  encoding="utf-8")
    (tmp_path / "README.md").write_text("# 项目\n", encoding="utf-8")

    out = repo_outline(ctx, "")
    assert "alpha.py" in out and "def load" in out and "class Loader" in out
    assert "MAX" in out
    assert "pkg/" in out and "beta.py" in out and "def run" in out
    assert "README.md" in out
    assert "hidden" not in out                       # .venv 与隐藏文件不成大纲


def test_repo_outline_truncates_and_rejects_non_dir(ctx, tmp_path):
    for i in range(30):
        (tmp_path / f"f{i:02d}.py").write_text("x = 1\n", encoding="utf-8")
    out = repo_outline(ctx, "", max_entries=10)
    assert "超出条目上限" in out
    assert repo_outline(ctx, "no_such_dir").startswith("错误")


def test_repo_outline_reports_unparsable_py(ctx, tmp_path):
    (tmp_path / "broken.py").write_text("def oops(:\n", encoding="utf-8")
    out = repo_outline(ctx, "")
    assert "解析不了" in out                          # 照实说,不硬猜


def test_edit_miss_reports_nearest_lines(ctx):
    """锚点打不中时给"最接近的行"(行号+相似度),模型一回合自我修正 —— 只报告不猜着改。"""
    fs.write_file(ctx, "app.py", 'def hello():\n    print("hi")\n    return None\n')
    out = fs.edit_file(ctx, "app.py",
                       'def hello():      \n    print("hi")', "x")
    assert "未在文件中找到" in out
    assert "最接近的几行" in out and "第 1 行" in out
    # 完全不沾边的锚点:如实说找不到相近的行
    out2 = fs.edit_file(ctx, "app.py", "class TotallyDifferent:\n    pass", "x")
    assert "找不到相近的行" in out2
    # 文件内容没被偷偷改掉
    assert "def hello():" in fs.read_file(ctx, "app.py")


def test_edit_ambiguous_lists_line_numbers(ctx):
    fs.write_file(ctx, "dup.txt", "alpha\nmid\nalpha\ntail\nalpha\n")
    out = fs.edit_file(ctx, "dup.txt", "alpha", "beta")
    assert "出现 3 次" in out and "第 1 行" in out and "第 3 行" in out and "第 5 行" in out



def test_search_files_filters_and_skips_junk_dirs(ctx, tmp_path):
    """@ 引用的数据源:按名字过滤、跳过依赖与产物目录、浅层优先。"""
    (tmp_path / "agent.py").write_text("x", encoding="utf-8")
    (tmp_path / "docs" / "notes.md").parent.mkdir(parents=True)
    (tmp_path / "docs" / "notes.md").write_text("x", encoding="utf-8")
    (tmp_path / ".venv").mkdir(parents=True)
    (tmp_path / ".venv" / "lib.py").write_text("x", encoding="utf-8")
    (tmp_path / "uploads").mkdir(parents=True)
    (tmp_path / "uploads" / "big.bin").write_text("x", encoding="utf-8")
    hits = fs.search_files(ctx, "py")
    assert hits == ["agent.py"]                       # .venv/uploads 不出现
    all_hits = fs.search_files(ctx, "")
    assert "docs/notes.md" in all_hits and "agent.py" in all_hits
    assert not any(".venv" in h or "uploads" in h for h in all_hits)
    assert all_hits.index("agent.py") < all_hits.index("docs/notes.md")  # 浅层优先


def test_search_files_empty_workspace(tmp_path):
    from minicode.tools.base import ToolContext
    ctx = ToolContext(workspace=tmp_path / "nope", config=None)
    assert fs.search_files(ctx, "") == []
