"""启动挂载(路 2b,v0.9.195):把 minicode 源码归档挂上 sys.path。

拆 PYZ 之后,`minicode.*` 不再嵌在 exe 的 PYZ 里,而以源码归档
`minicode.zip` 随包走(exe 旁)。这个模块**自己不 import minicode**,所以它
可以被 PyInstaller 打进 PYZ、在 minicode 可用之前先跑 —— 三个入口
(main.py / gui_main.py / web_main.py)的第一件事就是调 `mount()`。

三种失败各有明确的拒绝启动原因(计划书阶段 2 第 5 条):
- **缺失**:归档不在 exe 旁(安装包不完整/被杀软误删);
- **损坏**:zip 打不开或校验出坏块;
- **版本不符**:归档自称的版本与 exe 构建时写下的 `app_version.txt` 不一致
  (新旧安装混装的形状)。

非冻结(源码树直接跑)什么都不做 —— 源码树里 minicode 就在盘上。
"""
from __future__ import annotations

APP = "引路学长"


def mount() -> "str | None":
    """回 None = 已挂好(或非冻结,无事可做);回一句话 = 拒绝启动的原因。"""
    import re
    import sys
    from pathlib import Path
    import zipfile

    if not getattr(sys, "frozen", False):
        return None                       # 源码树:minicode 就在盘上,无事可做
    exe_dir = Path(sys.executable).resolve().parent
    zip_path = exe_dir / "minicode.zip"
    if not zip_path.is_file():
        return ("代码归档 minicode.zip 缺失(应在 %s)。安装包不完整"
                "或被杀软误删,请重新安装/重新解压一次。" % zip_path)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            bad = zf.testzip()
            init_src = zf.read("minicode/__init__.py").decode("utf-8", "replace")
    except (zipfile.BadZipFile, OSError, KeyError):
        return ("代码归档 minicode.zip 已损坏,无法读取。请重新安装/重新解压一次;"
                "若反复出现,先暂停杀软实时防护再试。")
    if bad is not None:
        return ("代码归档 minicode.zip 已损坏(坏块:%s)。"
                "请重新安装/重新解压一次。" % bad)
    m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', init_src, re.M)
    zip_ver = m.group(1) if m else ""
    expected = ""
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        try:
            expected = (Path(meipass) / "app_version.txt").read_text(
                encoding="utf-8").strip()
        except OSError:
            expected = ""
    if expected and zip_ver and expected != zip_ver:
        return ("代码归档版本(%s)与本程序(%s)不符 —— 多半是新旧安装混装了。"
                "请重新安装同版本。" % (zip_ver, expected))
    if str(zip_path) in sys.path:
        return None                       # 已挂过(重复调用无害)
    sys.path.insert(0, str(zip_path))
    return None
