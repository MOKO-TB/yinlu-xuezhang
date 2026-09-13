# -*- mode: python ; coding: utf-8 -*-
"""引路学长 完整版打包配置(内置 crawl4ai,能抓需要 JS 渲染的页面)。

与 gcode.spec(轻量版)的差异:
  1. 不排除 crawl4ai / playwright / patchright / numpy,并把它们(含驱动)收集进包;
  2. 浏览器内核不进 exe:构建脚本会把 chromium_headless_shell-* 复制到
     dist/引路学长-crawl4ai/ms-playwright/(exe 同级),运行时 crawl.py 的
     _frozen_browser_dir() 会认这个目录并设置 PLAYWRIGHT_BROWSERS_PATH。
     这样可以只出 onedir 便携版,单文件版不用每次启动解压 270MB。
  3. 只构建 onedir(单文件版 + 500MB 负载启动太慢,不做)。

构建: python scripts/build_crawl4ai_exe.py
"""
import os
import sys
from PyInstaller.utils.hooks import collect_all, collect_data_files

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
APP_NAME = "引路学长-crawl4ai"

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
# _MEIPASS 读出。与 gcode.spec 保持一致。
_local_secrets = os.path.join(ROOT, "config.local.json")
if os.path.exists(_local_secrets):
    datas.append((_local_secrets, "."))
binaries = []
hiddenimports = []

# venv 源自 Anaconda:stdlib 扩展依赖的 DLL 在 Library/bin 下,显式补齐。
# sqlite3.dll 是给 _sqlite3.pyd 用的 —— crawl4ai 的导入链会引到 sqlite3,
# 缺它就会 ImportError: DLL load failed while importing _sqlite3,然后静默降级。
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
        pass

# ---- crawl4ai 及其浏览器驱动(完整版的核心) ----
# 说明:这里不用 collect_all("crawl4ai")。collect_all 会把 crawl4ai/cli.py 也收进来,
# 而它顶层 `from litellm import completion`,会连带把 74MB 的 litellm 及其依赖拖进包;
# 命令行入口我们不用,只收数据文件 + 让静态分析跟随 crawl4ai/__init__.py 的导入链。
hiddenimports += ["crawl4ai", "bs4", "soupsieve", "lxml", "tiktoken"]
datas += collect_data_files("crawl4ai")
for pkg in ("patchright", "playwright", "playwright_stealth", "numpy"):
    try:
        d, b, h = collect_all(pkg)          # 驱动(node.exe 等)必须整包收集
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

# 内置轻量抓取器(降级用)也需要这两个
hiddenimports += ["httpx", "httpcore", "h11"]

common = dict(
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hookspath=[],
    runtime_hooks=[],
    # 注意:这里不能排除 crawl4ai/patchright/playwright/numpy(轻量版才排)
    excludes=["pytest", "tkinter", "setuptools", "pip", "nltk", "scipy",
              "tokenizers", "pandas", "matplotlib", "selenium", "torch"],
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
    console=False,
    icon=ICON,
)

coll = COLLECT(
    exe_gui, exe_cli,
    a_gui.binaries,
    a_gui.datas,
    strip=False,
    upx=False,
    name=APP_NAME,
)
