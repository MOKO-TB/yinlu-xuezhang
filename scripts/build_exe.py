"""打包 引路学长:PyInstaller 构建 → 清理 → zip 分发包。

用法: python scripts/build_exe.py [--no-zip]
产物: dist/引路学长/引路学长.exe 与 dist/引路学长-<版本>-win64.zip
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from minicode import __version__  # noqa: E402


def main() -> None:
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         str(ROOT / "packaging" / "gcode.spec")],
        cwd=ROOT, check=True,
    )
    dist = ROOT / "dist" / "引路学长"
    assert (dist / "引路学长.exe").exists(), "构建产物缺失(GUI 版)"
    assert (dist / "引路学长-CLI.exe").exists(), "构建产物缺失(CLI 版)"

    if "--no-zip" in sys.argv:
        print(f"完成(未打包 zip): {dist}")
        return

    # zip 前清理冒烟测试残留,并把面向用户的文件放到 exe 旁边
    shutil.rmtree(dist / "sessions", ignore_errors=True)
    (dist / "config.json").unlink(missing_ok=True)
    shutil.copy(ROOT / "config.example.json", dist / "config.example.json")
    (dist / "使用说明.txt").write_text(
        "引路学长 —— 编程引路智能体(GUI + CLI)\n"
        "======================================\n\n"
        "1. 运行 引路学长.exe 打开图形界面(推荐);引路学长-CLI.exe 是终端版。\n"
        "   首次启动会在本目录自动生成 config.json;\n"
        "2. 打开 config.json 填入 api_key,或在界面右上角「🔑 密钥」里直接\n"
        "   添加/切换供应商(智谱 GLM / DeepSeek / Kimi / Qwen 等 OpenAI 兼容接口);\n"
        "3. 重新打开应用,直接输入任务即可。顶栏可切换深色/浅色主题。\n"
        "   写文件、执行命令前会请求确认;会话留档在本目录 sessions/ 下;\n"
        "   技能放在 skills/ 目录(SKILL.md 格式),对话里输入 /skills 查看(终端版)。\n",
        encoding="utf-8",
    )

    zip_base = str(ROOT / "dist" / f"引路学长-{__version__}-win64")
    shutil.make_archive(zip_base, "zip", root_dir=ROOT / "dist", base_dir="引路学长")
    print(f"完成: {zip_base}.zip")
    print(f"    {dist / '引路学长.exe'}")


if __name__ == "__main__":
    main()
