# -*- coding: utf-8 -*-
"""界面边界的两条防回归:子进程别闪控制台、UI 文案别再把 emoji 带回来。

为什么要单独钉:
- 桌面版是 **GUI 子系统**进程。`subprocess.run/Popen` 只要没挂
  `CREATE_NO_WINDOW`,每调一次就会在用户屏幕上闪一个 cmd/黑框 —— 钩子
  (每一步工具调用都可能跑一次)和影子 git(文件一改就跑)是最容易踩的两处。
  这类问题不报错、单测也不红,只有用户看见"命令台一直弹"。
- 第二轮 UI 清理后,右侧底座标签、天气、活动分组、代码大纲这些**真的会显示
  在界面上**的地方都不再带 emoji。这里把几条关键常量钉住,防止下次改文案时
  又把图标带回来。

扫描口径只覆盖 `minicode/` 的源码行(注释不算);`explorer /select` 是显式
打开文件管理器,不是控制台,单独放行。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MINICODE = ROOT / "minicode"
HTML = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")

SUBPROCESS_RE = re.compile(r"subprocess\.(?:run|Popen|call|check_output)\(")
EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D]")


def _calls_without_console_flag() -> list[str]:
    offenders: list[str] = []
    for path in sorted(MINICODE.rglob("*.py")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if not SUBPROCESS_RE.search(line):
                continue
            if "explorer" in "\n".join(lines[i:i + 3]):
                continue                      # 显式打开文件管理器,不是控制台
            context = "\n".join(lines[i:i + 12])
            if "creationflags" not in context:
                offenders.append(f"{path.relative_to(ROOT)}:{i + 1}: {line.strip()}")
    return offenders


def test_noninteractive_subprocesses_suppress_the_console_window():
    """所有非交互子进程都要挂 CREATE_NO_WINDOW(或等价 flags)。"""
    offenders = _calls_without_console_flag()
    assert not offenders, (
        "这些子进程没挂 creationflags,GUI 子系统里会闪控制台窗口:\n"
        + "\n".join(offenders))


def test_terminal_dock_is_not_auto_opened_on_startup():
    """终端坞是"要用才叫出来"的面板,不能因为上次开着就在每次开机弹出来。"""
    assert 'localStorage.getItem("dock.open") === "1"' not in HTML, (
        "终端坞又在开机时自动恢复了 —— 用户点名不要一直弹命令台")
    assert 'localStorage.setItem("dock.open"' in HTML, (
        "坞的开关偏好不该整段删掉;只去掉开机自动恢复即可")


def test_weather_and_group_constants_have_no_emoji():
    """天气图标、活动/健康分组图标都已改成纯文字。"""
    from minicode import activity, health, weather
    blobs = [repr(weather._WMO), repr(activity.GROUPS), repr(health.GROUPS)]
    bad = [b for b in blobs if EMOJI_RE.search(b)]
    assert not bad, f"这些 UI 常量里还有 emoji:{bad}"


def test_code_outline_output_has_no_emoji(tmp_path: Path):
    """代码大纲会直接铺在工具卡片里;输出正文不许再带图标。"""
    from minicode.code_outline import CodeOutlineExtractor

    f = tmp_path / "sample.py"
    f.write_text("class A:\n    \"\"\"doc\"\"\"\n    def run(self):\n        pass\n",
                 encoding="utf-8")
    out = CodeOutlineExtractor(tmp_path).format_outline(
        CodeOutlineExtractor(tmp_path).extract_outline("sample.py"))
    assert not EMOJI_RE.search(out), out[:200]
