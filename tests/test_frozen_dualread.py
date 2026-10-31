"""冻结验证双读(路 2b 硬前置)的口径测试。

双读的验收写在计划书里:「同时支持读取 PYZ 与新的 minicode.zip;双读过渡期
必须在包里证明两份来源逐字一致」。可操作口径 = ①针在**每一个装着它的源**里
都要命中;②两份源自称同一个版本号。全部用合成夹具(内存源 + 真 zip),
不需要 PyInstaller,也不需要真包 —— 出包后的真验由 build 那一轮跑
`check_frozen_version.py` 对 dist 完成。
"""
import zipfile
from pathlib import Path

import pytest

from scripts.check_frozen_version import (_MemSource, _ZipSource,
                                          check_module, check_version_identity)

PYZ_MODULES = {
    "minicode.__init__": compile("__version__ = '0.9.191'", "<pyz-init>", "exec"),
    "minicode.demo": compile('TAG_ONE = "alpha"\nTAG_TWO = "beta"', "<pyz-demo>", "exec"),
}


def _zip_modules(version="0.9.191", demo='TAG_ONE = "alpha"\nTAG_TWO = "beta"'):
    """zip 里的键必须是**归档内路径**(点分名会写成没有目录的散文件,
    _ZipSource 的 toc 就再也看不见它 —— 这版夹具自己踩过一次)。"""
    return {
        "minicode/__init__.py": "__version__ = '%s'\n" % version,
        "minicode/demo.py": demo,
    }


def _make_zip(tmp_path: Path, modules) -> Path:
    zp = tmp_path / "minicode.zip"
    with zipfile.ZipFile(zp, "w") as z:
        for name, text in modules.items():
            z.writestr(name, text)
    return zp


@pytest.fixture
def zip_source(tmp_path):
    return _ZipSource(_make_zip(tmp_path, _zip_modules()))


NEEDLES = ("TAG_ONE", "TAG_TWO")


# ---------------------------------------------------------------- 检查

def test_needles_hit_in_both_sources_pass(zip_source):
    pyz = _MemSource("PYZ", PYZ_MODULES)
    oks, fails = check_module([pyz, zip_source], "minicode.demo", NEEDLES)
    assert fails == []
    # 双源:每一根针在两个源里各有一条 ✓,带源标签
    assert len(oks) == 4
    assert "minicode.demo(PYZ): TAG_ONE" in oks
    assert "minicode.demo(minicode.zip): TAG_ONE" in oks


def test_needle_missing_in_the_zip_copy_fails_with_the_source_label(tmp_path):
    """zip 那份缺针 = 两份来源不一致,必须点名是哪个源(这是双读存在的全部理由)。"""
    pyz = _MemSource("PYZ", PYZ_MODULES)
    stale = _ZipSource(_make_zip(tmp_path, _zip_modules(
        demo='TAG_TWO = "beta"')))                      # 旧内容:没有 TAG_ONE
    oks, fails = check_module([pyz, stale], "minicode.demo", ("TAG_ONE", "TAG_TWO"))
    assert "minicode.demo(PYZ): TAG_ONE" in oks
    assert fails == ["minicode.demo(minicode.zip): TAG_ONE"], fails


def test_module_in_neither_source_fails():
    oks, fails = check_module(
        [_MemSource("PYZ", PYZ_MODULES)], "minicode.ghost", ("X",))
    assert oks == [] and fails == ["minicode.ghost:不在任何来源里"]


def test_module_only_in_the_zip_is_checked_there(zip_source):
    """拆分落地的中间态:搬了一半也行,但搬过的那半必须对得上。"""
    oks, fails = check_module([zip_source], "minicode.demo", NEEDLES)
    assert fails == [] and len(oks) == 2


def test_single_source_output_matches_the_old_shape_verbatim(zip_source):
    """单源时输出与旧版逐字一致(`mod:needle`,不带源标签)—— 拆分落地前
    这道闸的每次输出都不该变样。"""
    oks, fails = check_module([zip_source], "minicode.demo", NEEDLES)
    assert oks == ["minicode.demo: TAG_ONE", "minicode.demo: TAG_TWO"]


def test_zip_source_reads_a_real_zip(tmp_path):
    zp = _make_zip(tmp_path, _zip_modules())
    src = _ZipSource(zp)
    assert src.has("minicode.demo") and src.has("minicode.__init__")
    assert not src.has("minicode.ghost")
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from check_frozen_version import _code_names
    names = _code_names(src.code_of("minicode.demo"))
    assert "alpha" in names and "beta" in names


def test_zip_source_ignores_pycache_and_non_package_entries(tmp_path):
    zp = tmp_path / "minicode.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("minicode/__init__.py", "__version__ = '0.9.191'\n")
        z.writestr("minicode/__pycache__/demo.cpython-312.pyc", b"junk")
        z.writestr("other/tool.py", "x = 1")
    src = _ZipSource(zp)
    assert src.has("minicode.__init__")
    assert not src.has("minicode.__pycache__.demo")
    assert not src.has("other.tool")


# ---------------------------------------------------------------- 版本号对账

def test_version_identity_passes_when_both_sources_agree(zip_source):
    pyz = _MemSource("PYZ", PYZ_MODULES)
    assert check_version_identity([pyz, zip_source]) == []


def test_version_identity_fails_when_sources_disagree(tmp_path):
    pyz = _MemSource("PYZ", PYZ_MODULES)
    other = _ZipSource(_make_zip(tmp_path, _zip_modules(version="0.9.190")))
    fails = check_version_identity([pyz, other])
    assert len(fails) == 1 and "版本号不一致" in fails[0]
    assert "0.9.191" in fails[0] and "0.9.190" in fails[0]


def test_version_identity_needs_at_least_two_sources():
    pyz = _MemSource("PYZ", PYZ_MODULES)
    assert check_version_identity([pyz]) == [], "单源没有可对账的另一方"
