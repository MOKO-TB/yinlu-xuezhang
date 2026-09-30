"""技能正文引用的工具名必须真的还在注册表里(静态闸,同款手法见 check_frozen_version)。

技能是给模型看的散文,里面写着「先 `use_skill` 读技能,再用 `py_script` 跑脚本」。哪天
工具改名或删掉,技能不会报错,只会**静默把模型指到一扇不存在的门**;而
`test_content_skills.py` 那套只 compile 代码块与跑关键函数,测不到正文里的名字。钉两张:

1. `PIN` 里每个名字必须仍在注册表 —— 改名/删除当天就红;
2. 全量扫出来的引用集合必须**正好等于** `PIN` —— 技能新依赖了一个工具也要红一次,
   逼人来确认"这个工具真存在、这条技能从此绑上它了"。

**已知边界**:只认「带下划线的整词」与「行内代码开头的标识符」。单词型工具名
(`bash`/`plan`/`memory`/`mood`)只有写成行内代码才算命中 —— 它们混在中文句子里当普通
词用时扫不出来,这是**刻意换来的零误报**,不是漏。
"""
import re
from pathlib import Path

from minicode.tools.registry import build_default_registry

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"

# 2026-09-25 扫全量 22 份 SKILL.md 得到的引用清单(见 test 里的扫描器)。
PIN = {
    "browser_open", "grep", "kb_suggest", "library_guide", "library_search",
    "py_script", "read_file", "search_knowledge", "use_skill", "web_crawl",
    "web_search", "write_file",
    # v0.9.58:ics-calendar 技能开头指向内置 export_ics(定制场景才走手写模板)
    "export_ics",
    # v0.9.90:career-timeline 让同学查校历/就业通知、记到点提醒(工具都真实存在,
    # 确认过加进清单)
    "campus_calendar", "campus_notices", "reminder",
}

_SNAKE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_LEADING_ID = re.compile(r"([a-z][a-z0-9_]{1,30})(?=[^a-z0-9_]|$)")


def cited_tools(body: str) -> set[str]:
    """一份技能正文里点名用到的工具(结果只与真实注册表求交,所以不会报出杂词)。"""
    registry = {t.name for t in build_default_registry()} | {"task"}
    found = set(_SNAKE.findall(body))
    for code in _INLINE_CODE.findall(body):
        m = _LEADING_ID.match(code + " ")
        if m:
            found.add(m.group(1))
    return {name for name in found if name in registry}


def test_every_pinned_tool_still_exists():
    registry = {t.name for t in build_default_registry()}
    missing = sorted(PIN - registry)
    assert not missing, (
        f"这些工具已经不在了,但还有技能在指名用它: {missing}"
        " —— 改名就把所有引用它的技能一起改掉,别留静默失效")


def test_the_set_of_cited_tools_has_not_drifted():
    seen: set[str] = set()
    for path in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        seen |= cited_tools(path.read_text(encoding="utf-8"))
    assert seen == PIN, (
        f"技能里新出现的工具引用: {sorted(seen - PIN)};"
        f" 已经没人引用的 PIN 项: {sorted(PIN - seen)} —— 确认过再改这份清单")


def test_the_scanner_actually_catches_a_tool_name():
    """防这条闸自己烂成空转:扫描器必须真能认出被点名的工具。"""
    assert "py_script" in cited_tools("先 write_file 写脚本,再用 `py_script` 跑一遍。")
    assert "bash" in cited_tools("用 `bash -c` 跑测试")            # 行内代码带参数也算
    assert cited_tools("这段只提到 plan 和 memory 两个普通词") == set()
