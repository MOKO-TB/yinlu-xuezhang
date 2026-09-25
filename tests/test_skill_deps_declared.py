"""技能教模型现写的脚本 import 了什么库,这两条路都得提前备好(本轮审计的发现二)。

技能正文里的 python 代码块不是装饰 —— 模型会照抄进 `py_script` 真去跑。而**没有任何
静态引用**指向这些库:打包那条路靠 `packaging/gcode.spec` 的 `hiddenimports` 显式收进
exe,源码运行那条路靠 `requirements.txt` 声明。两边都是"漏一行不报错、同学第一次用才炸"
的形态:v0.9.40 之前 `requirements.txt` 里 `pptx/docx/openpyxl/pypdf` 一个都没有,按
README 快速开始装依赖的人,跑 `lecture-notes` 第一行 `from pypdf import PdfReader`
就是 ModuleNotFoundError —— 而所有测试全绿。

这份文件钉三件事:
1. 技能 import 的每个非 stdlib 模块,`requirements.txt` 里必须有它的发行包名;
2. `packaging/gcode.spec` 的 `hiddenimports` 里也必须有(它没有静态引用,不显式收就漏);
3. 出现清单外的模块名要红,不准静默放过 —— 加库的人顺手把映射补上,顺便去看打包收没收。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"

# 顶层模块名 -> 发行包名(PyPI 上装的那个名字)。两边对不上就等于有人在技能里
# import 了一个没人准备的库。
DIST = {
    "PIL": "pillow",
    "docx": "python-docx",
    "openpyxl": "openpyxl",
    "pptx": "python-pptx",
    "pypdf": "pypdf",
}

_IMPORT = re.compile(r"^\s*(?:import|from)\s+([A-Za-z_][\w.]*)", re.M)
_BLOCK = re.compile(r"```[a-zA-Z0-9]*\n(.*?)```", re.S)


def skill_imports() -> set[str]:
    """所有技能代码块里 import 的顶层模块名。"""
    mods: set[str] = set()
    for path in sorted(SKILLS.glob("*/SKILL.md")):
        for block in _BLOCK.findall(path.read_text(encoding="utf-8")):
            for raw in _IMPORT.findall(block):
                mods.add(raw.split(".")[0])
    return mods


def requirements() -> str:
    return (ROOT / "requirements.txt").read_text(encoding="utf-8")


def third_party_imports() -> set[str]:
    return skill_imports() - set(sys.stdlib_module_names) - {"minicode"}


def test_the_scan_actually_finds_something():
    """扫描器自己烂了这条闸就是空转 —— 先确认它真看得见那四个。"""
    assert {"pptx", "docx", "openpyxl", "pypdf"} <= third_party_imports()


def test_every_third_party_module_is_named_in_the_map():
    unknown = sorted(m for m in third_party_imports() if m not in DIST)
    assert not unknown, (
        f"技能 import 了没登记过的模块: {unknown} —— 在 DIST 里补上发行包名,"
        "并确认 requirements.txt 与 gcode.spec 两头都有它")


def test_requirements_declares_them_for_source_runs():
    text = requirements()
    missing = sorted(DIST[m] for m in third_party_imports()
                     if not re.search(rf"^{re.escape(DIST[m])}[>=<\s]", text, re.M))
    assert not missing, (
        f"源码用户装不到: {missing} —— 按 README 快速开始的人跑技能第一步就 "
        "ModuleNotFoundError,而打包出来的 exe 一切正常,最难被发现问题")


def test_the_spec_still_bundles_them_for_packaged_runs():
    spec = (ROOT / "packaging" / "gcode.spec").read_text(encoding="utf-8")
    line = next((ln for ln in spec.splitlines()
                 if "hiddenimports +=" in ln and "pptx" in ln), "")
    # PIL 不在名单里:它 minicode 自己就 import 了(截图/预览),有静态引用,
    # PyInstaller 自己能收到,不需要那行显式清单
    for mod in sorted(third_party_imports() - {"PIL"}):
        assert f'"{mod}"' in line or f"'{mod}'" in line, (
            f"{mod} 没有静态引用,不进 hiddenimports 就不会进包 —— 界面上表现为"
            f"同学装好的包里技能跑不动")
