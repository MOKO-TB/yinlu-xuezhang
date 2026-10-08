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
    (os.path.join(ROOT, "assets", "toast-icon.png"), "assets"),
    (os.path.join(ROOT, "frontend"), "frontend"),
    (os.path.join(ROOT, "skills"), "skills"),
    (os.path.join(ROOT, "kb"), "kb"),
]

# 内置校园模型的 Key 不放进仓库(公开仓库):打包时从本地未跟踪的
# config.local.json 注入,运行时由 minicode.config._builtin_api_key() 从
# _MEIPASS 读出。构建机缺这个文件也能出包,只是内置模型不可用。
# v0.9.110:优先用 build_exe.py 备好的**临时目录**(里面就是一个 config.local.json)。
# 缘由是 v0.9.107 的 S1 把 Key 迁进 ~/.yinluxs 并**读到就抹掉工作区那一份**,于是下面
# 这个默认路径从此是空的 —— 构建一路绿而发出去的包开箱不能用,整条链上没有一步会红。
# 为什么给目录而不是直接给临时文件:PyInstaller 的 datas 按**源文件名**落包,而运行时
# 读的是 `resource_path("config.local.json")`;给一个 mktemp 出来的随机名,内容再对也读不到。
_secrets_dir = os.environ.get("YINLUXS_BUILD_SECRETS_DIR")
if _secrets_dir and os.path.isdir(_secrets_dir):
    datas.append((_secrets_dir, "."))
else:
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
# websockets 同理:minicode/browser.py 在函数内才 import(为了测试可替身与
# “缺依赖时只报错不阻断启动”),不声明的话打包版跑浏览器能力会 ModuleNotFoundError。
# 它是纯 Python(+一个可选 C 扩展),几 MB,不像 playwright 那样背 270MB 内核。
hiddenimports += ["bs4", "soupsieve", "httpx", "httpcore", "h11", "websockets"]

# v0.9.15 文档转换库(py_script 工具的内置运行时):只在工具 handler 函数内
# 间接使用,静态分析看不见,必须显式声明。lxml/Pillow 已随其他依赖收进包里
# (python-pptx/python-docx 靠它们),这里只补四个纯 Python 包,增量约 10MB。
hiddenimports += ["pptx", "docx", "openpyxl", "pypdf"]

# v0.9.22 系统托盘:pystray 按平台在**函数里**动态挑后端(`from . import _win32`),
# 静态分析只看得见 `import pystray` 那一行 —— 漏了后端就是"托盘图标创建失败",
# 而托盘起不来时关窗会退回直接退出(见 minicode/tray.py),症状很隐蔽。
# 用 collect_all 连 _util.win32 一起收;pystray 0.19 在 Windows 只用 ctypes + six,
# 不需要 pywin32。
try:
    d, b, h = collect_all("pystray")
    datas += d
    binaries += b
    hiddenimports += h
except Exception:
    pass
hiddenimports += ["six", "minicode.tray",
                  # v0.9.111 p9:环境信息那一节的取材模块。桥里那句
                  # `from minicode import envinfo` 写在方法体内(与 tray 同一个形状),
                  # 显式声明一次更稳 —— 漏收的表现不是报错,而是装好的包里
                  # 「环境信息」那一节永远不出现,而源码测试全绿。
                  "minicode.envinfo"]

# v0.9.48 crawl4ai 内置:两包都在 crawl.py 的**函数内**延迟 import,静态分析收不到
# (首包实测 PYZ 里没有 crawl4ai/playwright,ms-playwright 内核白拷) —— 必须用
# collect_all 强制收全:playwright 的 driver(node.exe + package)在 binaries/datas 里,
# 漏了它 import playwright 直接 ModuleNotFoundError。litellm/numpy 等重依赖在
# excludes 里,collect_all 不收被排除的模块,不冲突。
try:
    d, b, h = collect_all("crawl4ai")
    datas += d
    binaries += b
    hiddenimports += h
except Exception:
    pass
try:
    d, b, h = collect_all("playwright")
    datas += d
    binaries += b
    hiddenimports += h
except Exception:
    pass

# v0.9.123 本地语义向量档进包:enable 包(bge-small-zh-v1.5 那份 ONNX)要在**装好的**
# 包里跑得起来,而不只是源码运行能跑。三件事缺一不可:
#   1) onnxruntime 的 .pyd 与旁边的 onnxruntime.dll 必须一起收 —— 只收到 py 包的话,
#      embed.detect() 用 find_spec 判"有这个包"照样报 ready,第一次取向量才炸;
#   2) numpy 从下面的 excludes 里摘掉(_embed_local 第一步就 import numpy,而
#      onnxruntime 自己也靠它传张量;numpy 有 PyInstaller 自带的 hook,不用这里点名);
#   3) VC 运行库要钉成构建机 System32 那一份新的(见下面 _VC_RUNTIME)。
#
# **为什么不用 collect_all / hiddenimports**:PyInstaller 在"把收集到的原生库归属到
# 某个包"那一步(build_main.find_binary_dependencies → import_library)必须在隔离子
# 进程里**真的 import 一次这个包** —— 而构建机的 Anaconda 基底解释器一 import
# onnxruntime 就 access violation(2026-10-06 实测:collect_all 与只写 hiddenimports
# 两种写法都死在同一行,而段错误不是异常,PyInstaller 没有第二条路)。它内部那个
# `suppressed_imports` 名单是写死的(pyqtgraph.canvas 就在里面),没有对外开关。
#
# 所以这一档**当数据收**:整包按文件拷进 `_internal/onnxruntime/`,并用 excludes
# 把静态图挡开(不让它出现在 PYZ 里,也就不触发那次 import)。运行时标准 import
# 机制从磁盘把它认回来 —— 包自己的 `__init__` 会 `os.add_dll_directory(capi)`,
# 那三件原生库就在旁边,不需要这边补任何路径动作。
# 2026-10-06 实测过依赖面:`import onnxruntime` 只牵进 stdlib(socket/selectors 等),
# **不牵 protobuf、也不牵 flatbuffers**,建会话之后同样没有新第三方包 —— 所以
# 除了这个目录本身,不用再点名别的包。
import importlib.util as _ilu

try:
    _ort_spec = _ilu.find_spec("onnxruntime")
except (ImportError, ValueError):        # 构建机没装这个包:这一档不存在,别硬拦出包
    _ort_spec = None
_ort_dir = ""
if _ort_spec and _ort_spec.submodule_search_locations:
    _ort_dir = list(_ort_spec.submodule_search_locations)[0]
    datas.append((_ort_dir, "onnxruntime"))
    print("onnxruntime 按数据整包收:%s" % _ort_dir)
else:
    print("警告:构建机没有 onnxruntime —— 这次出的包不带本地语义档,"
          "capabilities.embeddings.backend=local 会说它缺运行时。")

# onnxruntime 1.30 是 VS2022 编的,要 msvcp140 14.4x+;而构建机的 Anaconda 基底里
# Library/bin 躺着一份 14.29(那份会让 onnxruntime 的 pyd 在 PyInit 里直接段错误)。
# 这里显式把 System32 那一份收进包里:一是让包不依赖使用者装没装 VC++ 运行库
# (分享给同学最常见的一种"打不开"),二是把版本钉死 —— build_exe._verify_bundle_onnx()
# 会回头比 sha256,被 PyInstaller 自己收集那份顶掉了会红。
_VC_RUNTIME = ("msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
               "vcruntime140.dll", "vcruntime140_1.dll", "vcruntime140_threads.dll")
for _name in _VC_RUNTIME:
    _p = os.path.join(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                                   "System32"), _name)
    if os.path.exists(_p):
        binaries.append((_p, "."))

# v0.9.48 起用户拍板:打包版**内置 crawl4ai** —— exe 旁 ms-playwright/ 放浏览器内核
# (build_exe.py 从构建机的 ms-playwright 拷 playwright 1.62 对应的 chromium-1234,
# 约 430MB 原始/zip 后 ~200MB),crawl.py 的引擎链路看到它就自动走 crawl4ai。
# 这里只放行 crawl4ai 与 playwright 两个 python 包(~10MB);LLM 提取用的重依赖
# (litellm/nltk/shapely 等)实测抓取主链不需要(2026-09-26 逐个禁 import 验证通过),
# 继续排除 —— 抓取主链 = playwright 起无头 chromium,渲染后交 crawl4ai 抽正文。
# 没带内核的单文件版会自动降级内置轻量抓取器(crawl.py _engine,不报错)。
# (这一条原本还写着"numpy 也不需要所以排除":v0.9.123 起 numpy 进包了,因为
#  本地向量档要它 —— 排除的理由是"抓取主链不需要",不是"整个应用不需要"。)

common = dict(
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,   # 必须显式传入:bs4/websockets 靠静态分析侥幸
    hookspath=[],                  # 收进包,pptx/docx/openpyxl/pypdf 无静态引用,
    runtime_hooks=[],              # 全靠这里强制收集 —— 漏传 = 静默丢依赖(v0.9.16 踩过)
    excludes=["pytest", "tkinter", "setuptools", "pip", "nltk",
              "patchright", "scipy", "tokenizers",
              "pandas", "matplotlib", "litellm", "shapely", "alphashape",
              "rank_bm25", "snowballstemmer",
              # v0.9.123:onnxruntime 走上面那段"当数据收"的路,静态图必须挡开它 ——
              # 一旦进了图,PyInstaller 就要在子进程里 import 它,而那一步在本机是段错误。
              # 漏了这一条的表现不是构建失败,是**构建失败在同一个地方却没人知道为什么**。
              "onnxruntime"],
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
