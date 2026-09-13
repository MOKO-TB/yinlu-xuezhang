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

# 内置校园模型的 Key 不放进仓库(公开仓库):打包时从本地未跟踪的
# config.local.json 注入,运行时由 minicode.config._builtin_api_key() 从
# _MEIPASS 读出。构建机缺这个文件也能出包,只是内置模型不可用。
_local_secrets = os.path.join(ROOT, "config.local.json")
if os.path.exists(_local_secrets):
    datas.append((_local_secrets, "."))
binaries = []
hiddenimports = []

# venv 源自 Anaconda:stdlib 扩展(pyexpat/_ssl/_lzma/_ctypes 等)依赖的
# DLL 在 Library/bin 下,PyInstaller 不会自动收集,这里显式补齐
for _name in ("libexpat.dll", "liblzma.dll", "libbz2.dll",
              "libcrypto-3-x64.dll", "libssl-3-x64.dll", "zlib.dll",
              "ffi.dll", "sqlite3.dll"):
    for _dir in (os.path.join(sys.base_prefix, "Library", "bin"), sys.base_prefix):
        _p = os.path.join(_dir, _name)
        if os.path.exists(_p):
            binaries.append((_p, "."))
            break

# openai/httpx2 等依赖有大量动态导入,整包收集最稳妥
for pkg in ("openai", "httpx2", "pydantic", "pydantic_core",
            "anyio", "certifi", "rich", "prompt_toolkit", "webview",
            "clr_loader", "pythonnet"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass  # 环境里缺哪个就跳过哪个

# 内置轻量抓取器(打包版联网爬取的引擎):bs4 解析 HTML、httpx 取页面。
# 它们在函数内部 import,PyInstaller 静态分析能发现,这里再显式声明一次更稳。
# 不要把 crawl4ai/patchright/playwright 加回来:浏览器内核 270MB+、驱动 100MB,
# 便携包放不下(会把 79MB 的 zip 撑到 500MB),抓取改用内置轻量抓取器。
hiddenimports += ["bs4", "soupsieve", "httpx", "httpcore", "h11"]

common = dict(
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hookspath=[],
    runtime_hooks=[],
    # crawl4ai 也在排除之列:crawl.py 里留着"优先用 crawl4ai"的 import,
    # 不排除的话 PyInstaller 会把 crawl4ai 的模块收进包(约 4MB),但它的运行依赖
    # (numpy)与浏览器驱动(patchright)都被排除了,import 必然失败 —— 收进去纯属死代码,
    # 还会让人误以为打包版能用 crawl4ai。冻结版实际走 crawl.py 的内置轻量抓取器。
    excludes=["pytest", "tkinter", "setuptools", "pip", "nltk",
                   "crawl4ai", "patchright", "scipy", "numpy", "tokenizers",
                   "pandas", "matplotlib", "playwright"],
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

# ---- 单文件版(onefile):免解压,双击直接打开;启动稍慢(需解压到临时目录) ----
a_one = Analysis([os.path.join(ROOT, "gui_main.py")], **common)
pyz_one = PYZ(a_one.pure)
exe_one = EXE(
    pyz_one,
    a_one.scripts,
    a_one.binaries,
    a_one.datas,
    [],
    name="引路学长-单文件版",
    debug=False, strip=False, upx=False,
    console=False,
    icon=ICON,
)
