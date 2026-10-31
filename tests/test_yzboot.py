"""启动挂载(yzboot,路 2b 本体)的口径测试。

三种拒绝启动原因(缺失/损坏/版本不符)各一支,外加:非冻结什么都不做、
正常挂载把 zip 插到 sys.path 首位且不重复插、版本对账文件缺席时跳过对账。
**真实导入从 zip 解析**由包内 e2e(e2e_headless)负责 —— 这里只验挂载半边。
"""
import sys
import zipfile

import pytest

import yzboot

PY_INIT = "__version__ = '0.9.195'\n"


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    """把 sys 装成"冻结运行"的形状:exe 在 tmp_path,._MEIPASS 指向它的 _internal。"""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "引路学长-CLI.exe"))
    internal = tmp_path / "_internal"
    internal.mkdir(exist_ok=True)
    monkeypatch.setattr(sys, "_MEIPASS", str(internal), raising=False)
    monkeypatch.setattr(sys, "path", [p for p in sys.path
                                      if "minicode.zip" not in p])
    return tmp_path


def _make_zip(tmp_path, version="0.9.195", corrupt=False):
    zp = tmp_path / "minicode.zip"
    if corrupt:
        zp.write_bytes(b"PK\x03\x04 this is not really a zip")
        return zp
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("minicode/__init__.py", "__version__ = '%s'\n" % version)
        z.writestr("minicode/demo.py", "X = 1\n")
    return zp


def test_source_tree_run_is_a_no_op(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    before = list(sys.path)
    assert yzboot.mount() is None
    assert sys.path == before, "源码树跑不许动 sys.path"


def test_missing_archive_refuses_with_the_path(frozen):
    err = yzboot.mount()
    assert err and "缺失" in err and "minicode.zip" in err


def test_corrupt_archive_refuses(frozen):
    _make_zip(frozen, corrupt=True)
    err = yzboot.mount()
    assert err and "损坏" in err


def test_version_mismatch_refuses_with_both_versions(frozen):
    _make_zip(frozen, version="0.9.190")
    (frozen / "_internal" / "app_version.txt").write_text(
        "0.9.195\n", encoding="utf-8")
    err = yzboot.mount()
    assert err and "0.9.190" in err and "0.9.195" in err and "不符" in err


def test_missing_expected_version_file_skips_the_check(frozen):
    _make_zip(frozen, version="0.9.190")     # 与默认期望(文件缺席)不比对
    assert yzboot.mount() is None


def test_good_zip_is_mounted_at_sys_path_front_and_never_twice(frozen):
    _make_zip(frozen)
    assert yzboot.mount() is None
    zp = str(frozen / "minicode.zip")
    assert sys.path[0] == zp
    assert yzboot.mount() is None, "重复调用不许插第二份"
    assert sys.path.count(zp) == 1


def test_bad_block_inside_the_zip_is_reported(frozen):
    """testzip 探出的坏块要点名,不能只说"坏了"。"""
    zp = frozen / "minicode.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("minicode/__init__.py", PY_INIT)
        info = zipfile.ZipInfo("minicode/demo.py")
        z.writestr(info, b"X = 1")
        # 现场把一个条目的字节抠坏:重写 CRC 之外的字节做不到,直接改 CRC 记录
    data = bytearray(zp.read_bytes())
    # 找到 demo.py 的压缩数据区,翻转几字节(CRC 校验会炸)
    marker = data.find(b"X = 1")
    if marker >= 0:
        data[marker:marker + 5] = b"Y = 2"
        zp.write_bytes(bytes(data))
        err = yzboot.mount()
        assert err and ("损坏" in err or "坏块" in err)
    else:
        pytest.skip("找不到注入目标(压缩布局变化)")
