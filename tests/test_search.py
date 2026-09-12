from minicode.tools import search


def _make_tree(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "x.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    (tmp_path / "a" / "y.txt").write_text("foo bar\n", encoding="utf-8")
    (tmp_path / "b" / "z.py").write_text("class Foo:\n    pass\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "n.js").write_text("foo()\n", encoding="utf-8")


def test_glob_recursive(ctx, tmp_path):
    _make_tree(tmp_path)
    out = search.glob(ctx, "**/*.py")
    assert "a/x.py" in out and "b/z.py" in out and "y.txt" not in out


def test_glob_no_match(ctx, tmp_path):
    _make_tree(tmp_path)
    assert "没有匹配" in search.glob(ctx, "**/*.rs")


def test_glob_cannot_escape_workspace(ctx, tmp_path):
    (tmp_path.parent / "outside.txt").write_text("x", encoding="utf-8")
    out = search.glob(ctx, "../*.txt")
    assert "outside.txt" not in out


def test_grep_basic(ctx, tmp_path):
    _make_tree(tmp_path)
    out = search.grep(ctx, "def foo")
    assert "a/x.py:1: def foo():" in out


def test_grep_ignore_case_and_glob_filter(ctx, tmp_path):
    _make_tree(tmp_path)
    out = search.grep(ctx, "foo", glob="*.py", ignore_case=True)
    assert "a/x.py" in out and "b/z.py" in out and "y.txt" not in out


def test_grep_skips_node_modules(ctx, tmp_path):
    _make_tree(tmp_path)
    out = search.grep(ctx, "foo")
    assert "node_modules" not in out


def test_grep_invalid_regex(ctx):
    assert "无效" in search.grep(ctx, "([unclosed")
