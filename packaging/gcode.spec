# -*- mode: python ; coding: utf-8 -*-
"""引路学长 (mini-code) PyInstaller 打包配置:双 exe。

产物(dist/引路学长/ 绿色便携目录):
  引路学长.exe      —— 图形界面版(窗口,无控制台)
  引路学长-CLI.exe  —— 终端版(控制台)

构建: python scripts/build_exe.py
"""
import os
import sys
from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

datas = [
    (os.path.join(ROOT, "system_prompt.md"), "."),
    (os.path.join(ROOT, "config.example.json"), "."),
    (os.path.join(ROOT, "assets", "gcode.ico"), "assets"),
    (os.path.join(ROOT, "frontend"), "frontend"),
    (os.path.join(ROOT, "skills"), "skills"),
    (os.path.join(ROOT, "kb"), "kb"),
]
binaries = []
hiddenimports = []

# venv 源自 Anaconda:stdlib 扩展(pyexpat/_ssl/_lzma/_ctypes 等)依赖的
# DLL 在 Library/bin 下,PyInstaller 不会自动收集,这里显式补齐
for _name in ("libexpat.dll", "liblzma.dll", "libbz2.dll",
              "libcrypto-3-x64.dll", "libssl-3-x64.dll", "zlib.dll",
              "ffi.dll"):
    for _dir in (os.path.join(sys.base_prefix, "Library", "bin"), sys.base_prefix):
        _p = os.path.join(_dir, _name)
        if os.path.exists(_p):
            binaries.append((_p, "."))
            break

# openai/httpx2 等依赖有大量动态导入,整包收集最稳妥
for pkg in ("openai", "httpx2", "pydantic", "pydantic_core",
            "anyio", "certifi", "rich", "prompt_toolkit", "webview",
            "clr_loader", "pythonnet", "pystray", "PIL"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass  # 环境里缺哪个就跳过哪个

common = dict(
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest", "tkinter", "setuptools", "pip", "nltk"],
    noarchive=False,
)

ICON = os.path.join(ROOT, "assets", "gcode.ico")

a_cli = Analysis([os.path.join(ROOT, "main.py")], **common)
pyz_cli = PYZ(a_cli.pure)
exe_cli = EXE(
    pyz_cli, a_cli.scripts, [],
    exclude_binaries=True,
    name="引路学长-CLI",
    debug=False, strip=False, upx=False,
    console=True,
    icon=ICON,
)

a_gui = Analysis([os.path.join(ROOT, "gui_main.py")], **common)
pyz_gui = PYZ(a_gui.pure)
exe_gui = EXE(
    pyz_gui, a_gui.scripts, [],
    exclude_binaries=True,
    name="引路学长",
    debug=False, strip=False, upx=False,
    console=False,          # 窗口应用,不弹黑色控制台
    icon=ICON,
)

coll = COLLECT(
    exe_gui, exe_cli,
    a_gui.binaries,
    a_gui.datas,
    strip=False,
    upx=False,
    name="引路学长",
)
