"""打包「完整版」便携包:内置 crawl4ai + headless 浏览器内核。

与 scripts/build_exe.py(轻量版)的区别:
  - 用 packaging/gcode_crawl4ai.spec,不排除 crawl4ai/playwright/patchright/numpy;
  - 构建后把浏览器内核复制到 exe 同级的 ms-playwright/(crawl.py 的
    _frozen_browser_dir() 会认这个目录),而不是塞进 _internal —— 单文件版
    启动时不必解压 270MB;
  - 只出 onedir 便携版 + zip,不做单文件版与安装包。

用法: python scripts/build_crawl4ai_exe.py
产物: dist/引路学长-crawl4ai/ 与 dist/引路学长-<版本>-crawl4ai-win64.zip
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from minicode import __version__  # noqa: E402

BROWSER_SRC = Path(os.path.expandvars(r"%LOCALAPPDATA%")) / "ms-playwright"


def _browser_dirs() -> list[Path]:
    """要随包分发的浏览器内核目录。

    只要 headless shell:实测 crawl4ai(无头模式)用它就够,272MB;
    完整 chromium 是 428MB,无头抓取用不到。
    """
    if not BROWSER_SRC.is_dir():
        return []
    return [p for p in sorted(BROWSER_SRC.glob("chromium_headless_shell-*")) if p.is_dir()]


def main() -> None:
    dist = ROOT / "dist" / "引路学长-crawl4ai"
    browsers = _browser_dirs()
    if not browsers:
        print(f"!! 没找到浏览器内核({BROWSER_SRC}/chromium_headless_shell-*)。\n"
              f"   先在当前环境执行:crawl4ai-setup(或 playwright install chromium)")
        return

    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    str(ROOT / "packaging" / "gcode_crawl4ai.spec")],
                   cwd=ROOT, check=True)
    assert (dist / "引路学长.exe").exists(), "构建产物缺失(GUI 版)"
    assert (dist / "引路学长-CLI.exe").exists(), "构建产物缺失(CLI 版)"

    # 浏览器内核放在 exe 同级(运行时 _frozen_browser_dir() 找的就是这里)
    target = dist / "ms-playwright"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for src in browsers:
        print(f"复制浏览器内核 {src.name} …")
        shutil.copytree(src, target / src.name)

    # 面向用户的文件与轻量版保持一致
    shutil.rmtree(dist / "sessions", ignore_errors=True)
    (dist / "config.json").unlink(missing_ok=True)
    shutil.copy(ROOT / "config.example.json", dist / "config.example.json")
    (dist / "使用说明.txt").write_text(_readme(), encoding="utf-8")

    zip_base = str(ROOT / "dist" / f"引路学长-{__version__}-crawl4ai-win64")
    shutil.make_archive(zip_base, "zip", root_dir=ROOT / "dist",
                        base_dir="引路学长-crawl4ai")
    print(f"便携版: {zip_base}.zip")

    setup_exe = _compile_installer()
    print(f"完成: {dist / '引路学长.exe'}")
    print(f"    浏览器内核: {[b.name for b in browsers]}")
    if setup_exe:
        print(f"安装包: {setup_exe}")


def _readme() -> str:
    return (
        "引路学长·完整版 —— 内置 crawl4ai 联网抓取能力(GUI + CLI)\n"
        "==========================================================\n\n"
        "这个版本和轻量版的区别:内置了 crawl4ai 与无头浏览器内核(ms-playwright/),\n"
        "可以抓取动态网页(学校官网、B 站等),不需要额外安装任何东西。\n\n"
        "两种用法:\n"
        "1. 便携版(免安装):整个文件夹拷到 U 盘/任意目录,双击 引路学长.exe 即可;\n"
        "   配置、会话、记忆都生成在 exe 同目录,换电脑直接连文件夹一起搬走。\n"
        "2. 安装包:运行 引路学长-<版本>-crawl4ai-setup.exe,按向导安装;\n"
        "   卸载时保留用户数据,重装不丢对话记录。\n\n"
        "其他:\n"
        "- 引路学长-CLI.exe 是终端版;\n"
        "- 首次启动自动生成 config.json,默认使用学校内置模型;\n"
        "- 想接 MCP 服务器:编辑 config.json 的 mcp_servers 数组(见 config.example.json);\n"
        "- 技能放 skills/ 目录,知识库放 kb/ 目录,会话留档在 sessions/。\n"
    )


def _find_iscc() -> str | None:
    """定位 Inno Setup 编译器(未安装则跳过安装包,不视为构建失败)。"""
    candidates = [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"),
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def _compile_installer() -> Path | None:
    iscc = _find_iscc()
    if not iscc:
        print("未找到 Inno Setup,跳过完整版安装包编译")
        return None
    subprocess.run([iscc, f"/DAppVersion={__version__}",
                    str(ROOT / "packaging" / "gcode_crawl4ai.iss")],
                   cwd=str(ROOT), check=True)
    setup = ROOT / "dist" / f"引路学长-{__version__}-crawl4ai-setup.exe"
    return setup if setup.exists() else None


if __name__ == "__main__":
    main()
