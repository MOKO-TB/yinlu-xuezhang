import pytest

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


# ---------------------------------------------------------------- 行尾保真(#49a)
#
# 两面一起钉:一是静默把 CRLF 转成 LF(同学的 git diff 于是写着"每一行都改过"),
# 二是 CRLF 文件上多行 old_string 逐字匹配根本匹配不上 —— 模型从 read_file
# 看到的是 LF,而文件里存的是 CR+LF,那面今天坏得更彻底。

def _raw(p):
    return p.read_bytes()


def test_edit_crlf_file_matches_with_lf_and_keeps_bytes(ctx, tmp_path):
    (tmp_path / "crlf.txt").write_bytes(b"line1\r\nline2\r\nline3\r\n")
    out = fs.edit_file(ctx, "crlf.txt", "line1\nline2", "lineA\nlineB")
    assert out.startswith("已编辑") and "CRLF" in out, out
    # 没被碰的那一行一个字节都没变:还是三条 CRLF 收尾
    assert _raw(tmp_path / "crlf.txt") == b"lineA\r\nlineB\r\nline3\r\n"


def test_write_crlf_file_stays_crlf(ctx, tmp_path):
    (tmp_path / "c.txt").write_bytes(b"a\r\nb\r\n")
    out = fs.write_file(ctx, "c.txt", "x\ny")
    assert "CRLF 行尾" in out
    assert _raw(tmp_path / "c.txt") == b"x\r\ny"


def test_lf_file_gains_no_extra_words(ctx, tmp_path):
    (tmp_path / "l.txt").write_bytes(b"a\nb\n")
    out = fs.edit_file(ctx, "l.txt", "a", "z")
    assert out == "已编辑 l.txt(替换 1 处)。", "LF 文件不该多出一句话"
    assert _raw(tmp_path / "l.txt") == b"z\nb\n"


def test_new_file_is_lf_as_before(ctx, tmp_path):
    fs.write_file(ctx, "fresh.txt", "a\nb")
    assert _raw(tmp_path / "fresh.txt") == b"a\nb"


def test_cr_only_file_keeps_its_own_ending(ctx, tmp_path):
    (tmp_path / "cr.txt").write_bytes(b"a\rb\r")
    fs.write_file(ctx, "cr.txt", "x\ny")
    assert _raw(tmp_path / "cr.txt") == b"x\ry"


def test_mixed_eol_file_is_not_normalized_and_says_so(ctx, tmp_path):
    raw = b"one\r\ntwo\nthree\r\n"
    (tmp_path / "mix.txt").write_bytes(raw)
    out = fs.edit_file(ctx, "mix.txt", "one\ntwo", "X")
    assert "未在文件中找到" in out and "混用" in out, out
    assert _raw(tmp_path / "mix.txt") == raw, "混用的稿子不许被学长统一"


def test_mixed_eol_editable_byte_exactly(ctx, tmp_path):
    (tmp_path / "mix2.txt").write_bytes(b"one\r\ntwo\n")
    out = fs.edit_file(ctx, "mix2.txt", "one\r\ntwo", "1\r\n2")
    assert out.startswith("已编辑"), out
    assert _raw(tmp_path / "mix2.txt") == b"1\r\n2\n"


def test_dominant_eol_reads_mind(ctx):
    assert fs._dominant_eol("a\r\nb\r\n") == "\r\n"
    assert fs._dominant_eol("a\nb\n") == "\n"
    assert fs._dominant_eol("a\r\nb\n") is None, "混用就别说自己有某种"
    assert fs._dominant_eol("no ending at all") is None


def test_gbk_file_stays_gbk_after_an_edit(ctx, tmp_path):
    """#49a 的另一面:旧写法读按 GBK 解、写一律 UTF-8 —— 同学那份中文稿被改一行
    就整份换编码,记事本打开变乱码。这里改一行,其余字节必须一个字都没变。"""
    raw = "第一行中文\r\n第二行中文\r\n".encode("gbk")
    (tmp_path / "gbk.txt").write_bytes(raw)
    out = fs.edit_file(ctx, "gbk.txt", "第二行中文", "第三行中文")
    assert "gbk 编码" in out, out
    assert _raw(tmp_path / "gbk.txt") == "第一行中文\r\n第三行中文\r\n".encode("gbk")


def test_gbk_write_file_keeps_encoding_and_says_it(ctx, tmp_path):
    (tmp_path / "g2.txt").write_bytes("甲\r\n".encode("gbk"))
    out = fs.write_file(ctx, "g2.txt", "乙")
    assert _raw(tmp_path / "g2.txt") == "乙".encode("gbk")
    assert "CRLF" in out and "gbk" in out


def test_unreadable_bytes_are_refused_not_guessed(ctx, tmp_path):
    """解不动就说解不动:把 U+FFFD 写回用户文件是永久损坏,比不改糟得多。"""
    bad = b"\xff\xfe\x00\x01abc"          # utf-8 解不动、gbk 也解出替换符的一串
    (tmp_path / "odd.bin.txt").write_bytes(bad)
    if "\ufffd" not in fs._read_text(tmp_path / "odd.bin.txt"):
        pytest.skip("这串字节在本机解得动,不构成「读不出」的样本")
    before = _raw(tmp_path / "odd.bin.txt")
    out = fs.edit_file(ctx, "odd.bin.txt", "abc", "xyz")
    assert "读不出" in out, out
    assert _raw(tmp_path / "odd.bin.txt") == before, "拒写就得真没写"
