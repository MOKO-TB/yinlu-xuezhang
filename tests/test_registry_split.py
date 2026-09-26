"""`tools/registry.py` 拆成 6 份领域目录之后,这四件事必须仍然成立(v0.9.39 #84)。

拆分是**搬运**不是重写:40 个 `Tool(...)` 的字段一字未改(搬完用 ast 取每段源码
比 sha256 核过),registry.py 从 987 行收成三十来行只管装配。于是风险也从"改坏一个
工具的描述"变成了另外四种,这份文件就钉这四种:

1. **顺序**:模型每轮拿到的 tools 数组就是这个顺序。看着别扭的分组(知识库三件被
   OCR/图表隔开、`kb_suggest` 夹在绩点与馆藏之间)是历史顺序,搬动它就是改行为面。
2. **漏装配**:某份目录里新增了一个组函数却忘了在 registry 里 `*` 进去 —— 那个工具
   会**静默消失**(不报错,模型只是再也调不到它)。
3. **重复装配**:同一个工具名出现在两份目录里,后装配的那份盖住前一份。
4. **registry 又长回去**:装配文件里重新出现 `Tool(` 字面量,说明热点又回来了。
"""
import ast
import re
from pathlib import Path

from minicode.tools import registry
from minicode.tools.base import Tool

ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = ROOT / "minicode" / "tools"

# 拆分前 `build_default_registry()` 的真实顺序(2026-09-24 从 git HEAD 那份 987 行
# 逐个抄出,拆完用运行期结果逐字对过)。要重排请单独一轮并说清为什么。
# 之后新增的工具:`search_history`(跨会话记忆)插在 `search_knowledge` 后,
# `campus_notices`(v0.9.46 时效性)挨着同类插在 `get_weather` 后,其余顺序一字未动。
ORDER = [
    "read_file", "write_file", "edit_file", "bash", "py_script", "glob", "grep",
    "web_crawl", "web_search", "browser_open", "browser_eval", "browser_snapshot",
    "browser_click", "browser_fill", "browser_screenshot", "browser_close",
    "browser_status", "page_parse",
    "use_skill", "plan", "search_knowledge", "search_history",
    "ocr_image", "render_chart", "generate_image",
    "campus_calendar", "gpa_calc",
    "kb_suggest",
    "library_search", "library_guide",
    "memory",
    "reminder", "mood", "schedule", "desktop_notify", "get_weather", "campus_notices",
    "map_find_place", "map_show_list", "map_highlight_building", "map_drop_pin",
    "map_clear",
]

CATALOGS = sorted(TOOLS_DIR.glob("catalog_*.py"))


def _catalog_defs() -> dict[str, list[str]]:
    """每份目录里那些"返回一批 Tool"的组函数名。"""
    out = {}
    for path in CATALOGS:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        out[path.stem] = [n.name for n in tree.body
                          if isinstance(n, ast.FunctionDef)
                          and any(isinstance(x, ast.Return) and isinstance(x.value, ast.List)
                                  for x in ast.walk(n))]
    return out


def _catalog_tool_names() -> dict[str, str]:
    """工具名 -> 它写在哪份目录里(重复的会被这条测试抓出来)。"""
    where: dict[str, str] = {}
    for path in CATALOGS:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "Tool":
                name = next(kw.value.value for kw in node.keywords if kw.arg == "name")
                assert name not in where, f"{name} 在 {where[name]} 与 {path.stem} 里各写了一份"
                where[name] = path.stem
    return where


# ---------------- 1. 顺序与数量 ----------------

def test_registry_returns_the_same_forty_one_tools_in_the_same_order():
    assert [t.name for t in registry.build_default_registry()] == ORDER


def test_order_pin_is_not_stale():
    """防"测试自己抄错了":这份 ORDER 必须与运行期结果互为镜像,且正好 42 个不重名。"""
    assert len(ORDER) == len(set(ORDER)) == 42
    assert sorted(_catalog_tool_names()) == sorted(ORDER), \
        "目录里的工具集合与 ORDER 不一致(加了工具忘了钉顺序,或删了没同步)"


# ---------------- 2/3. 装配完整性 ----------------

def test_every_catalog_group_is_wired_into_the_registry():
    """漏装配 = 那个工具静默消失,模型再也调不到它,而且不会报错。"""
    src = (TOOLS_DIR / "registry.py").read_text(encoding="utf-8")
    for stem, fns in _catalog_defs().items():
        for fn in fns:
            assert re.search(rf"\*{re.escape(stem)}\.{re.escape(fn)}\(\)", src), \
                f"{stem}.{fn}() 没被 registry 装配进去"


def test_assembled_tools_are_real_tools_with_callable_handlers():
    tools = registry.build_default_registry()
    assert all(isinstance(t, Tool) for t in tools)
    for t in tools:
        assert callable(t.handler), f"{t.name} 的 handler 不可调用"
        # handler 一律是"模块.函数",不是就地 lambda:拆文件时 lambda 会跟着闭包漂走
        assert getattr(t.handler, "__module__", "").startswith("minicode."), \
            f"{t.name} 的 handler 来自 {getattr(t.handler, '__module__', '?')}"


# ---------------- 4. registry 不许又长回去 ----------------

def test_registry_is_assembly_only():
    src = (TOOLS_DIR / "registry.py").read_text(encoding="utf-8")
    assert "Tool(" not in src.split("def build_default_registry")[1], \
        "registry.py 里又出现 Tool(...) 字面量 —— 热点搬回来了"
    assert len(src.splitlines()) <= 60, \
        f"registry.py 又长到 {len(src.splitlines())} 行,它只该管装配顺序"
    assert len(CATALOGS) == 6, f"目录模块不是 6 份:{[p.name for p in CATALOGS]}"


def test_no_catalog_is_a_new_hotspot():
    """拆分的目的是消热点,不是把 987 行搬到另一个文件里。"""
    for path in CATALOGS:
        lines = len(path.read_text(encoding="utf-8").splitlines())
        assert lines < 400, f"{path.name} 已经 {lines} 行,该再拆一层了"
