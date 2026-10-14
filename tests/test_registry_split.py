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
    "read_file", "write_file", "edit_file",
    # v0.9.134 统一 diff 落盘:插在 edit_file 之后 —— 它与文件组同一家族,而模型选工具
    # 时"改一处"与"改一包"是挨着的两个决定,离远了会两边都试一遍浪费回合。
    "apply_patch",
    "bash",
    # v0.9.84:后台命令的两只眼睛与一只手 —— 挨着 bash,模型先派活、再看进度、才终止
    "bash_status", "bash_kill",
    # v0.9.146:后台命令的"嘴"。插在 bash_kill 之后、py_script 之前 ——
    # 后台那一族(派活/看进度/终止/喂输入)必须连在一起,而 py_script 是另一族。
    "bash_input",
    # v0.9.173:递话(把一句话放进正在跑的子代理 / 另一条活会话的盒子)。它写在与
    # 上面三只**同一份目录**里 —— catalog_bg 这一族的题目是"多任务并行时的手":
    # 那三只管命令、这一只管会话与子代理。位置因此紧随 bash_input(目录顺序 = 注册表
    # 顺序,`test_every_catalog_group_is_wired_into_the_registry` 盯着这条)。
    "session_message",
    "py_script", "glob", "grep",
    "repo_outline",
    # v0.9.147:诊断工具(按扩展名跑检查器)。挨着 repo_outline —— 同属"看代码"一族,
    # 而模型改完一处代码后紧接着想问的正是"这下对不对"。
    "diagnostics",
    # v0.9.164:测试套件运行与自愈诊断(执行工作区测试),挨着 diagnostics
    "test_runner",
    # v0.9.165:代码库符号索引精准搜索(对标 Cursor/Codex),挨着 test_runner/diagnostics
    "symbol_search",
    # v0.9.166:跨文件代码引用查找与影响面分析(对标 Cursor/Codex),挨着 symbol_search
    "find_references",
    # v0.9.167:跨文件符号重命名与重构预演(对标 Cursor/Codex),挨着 find_references
    "rename_symbol",
    # v0.9.168:跨文件调用层次结构与依赖拓扑分析(对标 VS Code/Cursor/Codex),挨着 rename_symbol
    "call_hierarchy",
    # v0.9.169:跨文件符号定义提取与实现跳转(对标 VS Code/Cursor/Codex),挨着 call_hierarchy
    "symbol_definition",
    # v0.9.170:代码结构大纲与类/成员树(对标 VS Code/Cursor/Codex),挨着 symbol_definition
    "code_outline",
    # v0.9.87:项目级权限白名单(看 = 只读,批准 = 要确认),紧挨着文件/命令那组
    "permissions", "permissions_trust",
    # v0.9.116 L3:钩子配方(查/预览 = 只读,挂上 = 要确认),挨着权限那组走
    "hooks", "hooks_apply",
    # v0.9.98:git 工作副本(隔离工作区 W1),挨着项目权限那组
    "worktree",
    "web_crawl", "web_search", "browser_open", "browser_eval", "browser_snapshot",
    "browser_click", "browser_fill", "browser_screenshot", "browser_close",
    "browser_status", "page_parse",
    # v0.9.61:MCP 自助管理,挨着它服务的外接家族(web/浏览器)
    "mcp",
    # v0.9.102:MCP resources(read 过确认框)
    "mcp_resource",
    "use_skill", "plan", "ask_user", "search_knowledge",
    # v0.9.173:按需加载的门(tool_search)。上面那一族是"找东西"(技能/计划/提问/
    # 检索),它补上"找能力"这一格;下面 export_trace 起是"会话记录"另一族。
    # 它**永远常驻** —— 门不在,分层里那些工具谁也进不来。
    "tool_search",
    # v0.9.98:运行轨迹导出,挨着历史检索;v0.9.99:反馈包(同一族)
    "export_trace", "export_feedback", "search_history",
    "ocr_image", "render_chart", "generate_image",
    # v0.9.173:看图,挨着它对着的那三只"出图/取字"的工具
    "view_image",
    "campus_calendar",
    # v0.9.62:校历补录(贴通知→核对→入库),挨着它读写的 campus_calendar
    "calendar_edit",
    "gpa_calc",
    # v0.9.59:成绩存档(看/算/清),挨着它配套的 gpa_calc
    "scores",
    "kb_suggest",
    "library_search", "library_guide",
    "memory",
    # v0.9.163:代码库全景百科(对标 ZCode/Qoder)
    "repo_wiki",
    "reminder", "mood", "schedule", "desktop_notify", "get_weather", "campus_notices",
    # v0.9.58:.ics 兜底导出,挨着它吃的三路数据(课表/待办/校历)所在的组
    "export_ics",
    "map_find_place", "map_show_list", "map_highlight_building", "map_drop_pin",
    "map_clear",
    # v0.9.154 I:子代理 → 主会话留话。排在**最后**:只有子代理那边有对象可转达,
    # 主会话调它得到的是"你就是主会话"那句如实回话 —— 它不是常用路,不该占前面。
    "notify_parent",
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

def test_registry_returns_the_same_tools_in_the_same_order():
    assert [t.name for t in registry.build_default_registry()] == ORDER


def test_order_pin_is_not_stale():
    """防"测试自己抄错了":这份 ORDER 必须与运行期结果互为镜像,且不重名。

    v0.9.96 加 `ask_user` 时它的位置就钉在 `plan` 后面 —— 模型每轮拿到的 tools 数组
    是这个顺序,挪位置等于改行为面,所以新工具插在谁后面都要在这里写明。
    """
    assert len(ORDER) == len(set(ORDER)) == 73      # v0.9.173:70 + view_image + session_message + tool_search
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
    # v0.9.98:catalog_remind 从 catalog_campus 拆出来,6 份 → 7 份
    # v0.9.146:catalog_bg 从 catalog_files 拆出来(它撞上了下面那道 400 行的闸),
    # 7 份 → 8 份;v0.9.147:catalog_diag(诊断)是主动新开的一份,8 份 → 9 份;
    # v0.9.154:catalog_sub(子代理 → 主会话留话)同样是主动新开的,9 份 → 10 份
    # —— 它不属于现有任何一族(不是文件/网络/校园/知识库),塞进哪一份都是在改
    # 那一份的语义。
    assert len(CATALOGS) == 10, f"目录模块不是 10 份:{[p.name for p in CATALOGS]}"


def test_no_catalog_is_a_new_hotspot():
    """拆分的目的是消热点,不是把 987 行搬到另一个文件里。"""
    for path in CATALOGS:
        lines = len(path.read_text(encoding="utf-8").splitlines())
        assert lines < 400, f"{path.name} 已经 {lines} 行,该再拆一层了"
