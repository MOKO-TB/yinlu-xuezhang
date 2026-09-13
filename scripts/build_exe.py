"""打包 引路学长:PyInstaller 构建 → 清理 → zip 分发包。

用法: python scripts/build_exe.py [--no-zip]
产物: dist/引路学长/引路学长.exe 与 dist/引路学长-<版本>-win64.zip
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


def _ensure_collected(dist: Path) -> None:
    """兜住 PyInstaller 6.22 冷构建的收集竞态。

    实测:带 `--clean` 的**首次**构建里,CLI 那个 EXE 已经生成在 build/gcode/ 下,
    但 COLLECT 阶段却报 "Ignoring non-existent resource ... 引路学长-CLI.exe",
    结果 dist/引路学长/ 只有 _internal、两个 exe 都不在(--clean 冷缓存下的顺序问题,
    第二次不带 --clean 就正常)。这里发现缺失就自己补拷,免得包出来是残的。
    """
    work = ROOT / "build" / "gcode"
    for name in ("引路学长.exe", "引路学长-CLI.exe"):
        target = dist / name
        if target.exists():
            continue
        src = work / name
        if src.exists():
            shutil.copy2(src, target)
            print(f"[修复] COLLECT 漏收 {name},已从 build/gcode 补拷")
        else:
            raise AssertionError(f"构建产物缺失: {name}(build/gcode 里也没有)")


LIB_DB_SRC = ROOT / "crawled" / "mfindgzyykj.libsp.cn" / "library.db"


def bundle_library_db(dist: Path) -> None:
    """把离线馆藏库(1.3 GB)拷进便携目录,让安装包/zip 自带图书馆查询能力。

    源库是 WAL 模式,必须用 SQLite 在线备份(直接拷 .db 会丢 -wal 数据、
    复制出来是空表)。构建机没有库时只警告不失败。
    """
    if not LIB_DB_SRC.is_file():
        print(f"!! 未找到馆藏库 {LIB_DB_SRC},跳过内置(包内图书馆查询将不可用)")
        return
    import sqlite3
    dst_dir = dist / "crawled" / "mfindgzyykj.libsp.cn"
    dst = dst_dir / "library.db"
    dst_dir.mkdir(parents=True, exist_ok=True)
    if dst.is_file() and dst.stat().st_size > 0:
        print(f"馆藏库已存在,跳过: {dst}")
        return
    src = sqlite3.connect(f"file:{LIB_DB_SRC}?mode=ro", uri=True)
    out = sqlite3.connect(dst)
    with out:
        src.backup(out)
    out.close()
    src.close()
    print(f"已内置馆藏库 {dst} ({dst.stat().st_size/1e9:.2f} GB)")


def main() -> None:
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         str(ROOT / "packaging" / "gcode.spec")],
        cwd=ROOT, check=True,
    )
    dist = ROOT / "dist" / "引路学长"
    _ensure_collected(dist)
    assert (dist / "引路学长.exe").exists(), "构建产物缺失(GUI 版)"
    assert (dist / "引路学长-CLI.exe").exists(), "构建产物缺失(CLI 版)"
    onefile = ROOT / "dist" / "引路学长-单文件版.exe"
    assert onefile.exists(), "构建产物缺失(单文件版)"

    # 内置离线馆藏库:必须在打安装包/zip 之前,否则分发出去不带图书馆数据
    bundle_library_db(dist)

    # Inno Setup 编译安装包(已安装于当前用户目录或 Program Files)
    iscc = next((p for p in [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"),
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe"] if os.path.exists(p)), None)
    if iscc:
        subprocess.run([iscc, f"/DAppVersion={__version__}",
                        str(ROOT / "packaging" / "gcode.iss")],
                       check=True, cwd=str(ROOT))
        setup_exe = ROOT / "dist" / f"引路学长-{__version__}-setup.exe"
        assert setup_exe.exists(), "安装包构建失败"
        print(f"安装包: {setup_exe}")
    else:
        print("未找到 Inno Setup,跳过安装包编译(pip install -r requirements-dev 后"
              "运行 scripts/setup_inno.py 可补装)")

    if "--no-zip" in sys.argv:
        print(f"完成(未打包 zip): {dist}")
        return

    # zip 前清理冒烟测试残留,并把面向用户的文件放到 exe 旁边
    shutil.rmtree(dist / "sessions", ignore_errors=True)
    (dist / "config.json").unlink(missing_ok=True)
    shutil.copy(ROOT / "config.example.json", dist / "config.example.json")
    (dist / "使用说明.txt").write_text(
        "引路学长 —— 校园 AI 助手 / 编程引路智能体(GUI + CLI)\n"
        "====================================================\n\n"
        "【便携版用法】整个文件夹拷到任意目录或 U 盘,双击 引路学长.exe 即可;\n"
        "config.json、sessions/、memory/ 都生成在 exe 同目录,换电脑连文件夹一起搬走。\n\n"
        "1. 引路学长.exe 是图形界面(推荐);引路学长-CLI.exe 是终端版;\n"
        "   引路学长-单文件版.exe 免解压,双击直接打开。\n"
        "2. 模型已内置学校服务,打开即可用,不需要填 API Key。\n"
        "3. 想接 MCP 服务器:编辑 config.json 的 mcp_servers 数组\n"
        "   (格式见 config.example.json,支持 npx/uvx/python 启动的任意 MCP 服务器);\n"
        "4. 会话留档在 sessions/;技能放 skills/(SKILL.md 格式);知识库放 kb/;\n"
        "   长期记忆在 memory/MEMORY.md。\n",
        encoding="utf-8",
    )

    zip_base = str(ROOT / "dist" / f"引路学长-{__version__}-win64")
    shutil.make_archive(zip_base, "zip", root_dir=ROOT / "dist", base_dir="引路学长")
    print(f"完成: {zip_base}.zip")
    print(f"    {dist / '引路学长.exe'}")
    print(f"    {onefile}(免解压单文件版,双击直接打开)")


if __name__ == "__main__":
    main()
