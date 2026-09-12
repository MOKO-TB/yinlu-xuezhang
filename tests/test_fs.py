from minicode.tools import fs


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
