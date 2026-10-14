"""诊断类工具(v0.9.147):按文件类型跑一次检查器,把结论回灌给模型。

为什么单开一份目录,而不是塞进 `catalog_files.py`:后者刚在 v0.9.146 撞过
「每份目录 < 400 行」那道闸(见 `catalog_bg.py` 的开头),而这一只的描述要写清
"哪些语言能检、哪些检查器这台机器上没有、粒度是文件还是包还是整个 crate",
本身就够长。按**领域**拆:诊断与"看代码"同族,所以它排在 `repo_outline` 旁边。

注册表里这些工具的**先后顺序**由 `registry.build_default_registry()` 决定,
并由 `tests/test_registry_split.py` 逐字钉住 —— 别在这里自己排。
"""
from __future__ import annotations

from minicode import diagnostics, test_runner, symbol_search, find_references, rename_symbol, call_hierarchy, symbol_definition, code_outline
from minicode.tools.base import Tool


def tools() -> list[Tool]:
    """诊断与测试类工具。"""
    return [
        Tool(
            name="diagnostics",
            description=(
                "改完代码后按文件类型跑一次检查器,把报出的问题原文拿回来。"
                "认得这些类型:`.py` → ruff(本机装了才跑,只报这一个文件);"
                "`.ts`/`.tsx` → 工作区自己的 tsc(报的是**整个项目**的类型图);"
                "`.js` → `node --check`(只语法);`.go` → `go vet`"
                "(粒度是**包**);`.rs` → `cargo check`(粒度是整个 crate,"
                "**会执行工作区代码**如 build.rs)。"
                "**它自己会跑,不必你每次都调** —— `write_file`/`edit_file` 落盘后"
                "如果这个类型有检查器,结论会自动附在那一步的结果后面。"
                "显式调它的用途是:改完一阵子想复查、或想先看看这台机器上有哪些"
                "检查器可用(`action=list`)。"
                "**三条必须如实转述的边界**:①检查器不存在(打包版里没有 ruff、"
                "工作区没装 tsc)或超时,回话会明说「这次没做检查」—— "
                "**那不是「检查过了没问题」**;②没有这个文件类型的检查器时同样明说,"
                "不要自己脑补一个结论;③它**不修代码**(没有 `--fix`),"
                "也**不联网装检查器**。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["check", "list"],
                               "description": "check = 检查一个文件(要传 path);"
                                              "list = 列出这台机器上有哪些检查器可用"},
                    "path": {"type": "string",
                             "description": "要检查的文件路径(工作区内,相对工作区或绝对都行)"},
                    "reason": {"type": "string",
                               "description": "用一句中文向用户说明这一步要达成什么"
                                              "(显示在权限确认框里)"},
                },
                "required": ["action"],
            },
            handler=diagnostics.diagnostics,
            # **不标只读**:它会执行工作区里的检查器(`cargo check` 会跑 `build.rs`),
            # 与 `hooks`(只读列清单)正好形成对照 —— 列清单只读,真跑检查器要点头。
            read_only=False,
        ),
        Tool(
            name="test_runner",
            description=(
                "受控运行当前工作区测试套件,并获取结构化通过/失败与错误堆栈报告(v0.9.164)。"
                "自动识别 Python(pytest/unittest)、Node(npm test)、Rust(cargo test)、Go(go test)。"
                "支持完整跑测试(action='run')、指定单个测试文件或用例(如 target='tests/test_x.py::test_y')、"
                "探测当前环境测试器(action='detect')、以及快速读取最近一次测试报告(action='last_result')。"
                "**不标只读**:执行测试会运行工作区代码,需经权限确认。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["run", "detect", "last_result"],
                        "description": "run = 执行测试; detect = 探测测试框架与命令; last_result = 读取最近一次测试结果快照",
                    },
                    "target": {
                        "type": "string",
                        "description": "可选: 要测试的目标文件或用例标识(相对工作区路径,如 tests/test_repo_wiki.py)",
                    },
                    "reason": {
                        "type": "string",
                        "description": "用一句中文向用户说明这一步要达成什么(显示在权限确认框里)",
                    },
                },
                "required": ["action"],
            },
            handler=test_runner.test_runner_tool,
            read_only=False,
        ),
        Tool(
            name="symbol_search",
            description=(
                "在工作区源码中精准搜索类、函数、方法、接口、结构体、类型定义与常量(v0.9.165)。"
                "支持跨语言(Python AST深度解析、JS/TS、Go、Rust、Java等稳健提取)。"
                "可传 query 模糊或精确搜索符号名, 可传 kind(class/function/method/interface/struct/type/constant/all)过滤类型,"
                "支持通过 path 限定子目录或单个文件。返回带相对路径、行号、函数签名与文档摘要的结果。"
                "只读安全, 无副作用。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "要搜索的符号名称或关键词(不区分大小写模糊匹配)",
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["all", "class", "function", "method", "interface", "struct", "type", "constant"],
                        "description": "可选: 符号类别过滤, 默认 all",
                    },
                    "path": {
                        "type": "string",
                        "description": "可选: 限定相对工作区的子目录或单个文件路径",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "可选: 最多返回匹配符号条数, 默认 50",
                    },
                },
            },
            handler=symbol_search.symbol_search_tool,
            read_only=True,
        ),
        Tool(
            name="find_references",
            description=(
                "跨文件查找类、函数、变量或符号的所有引用与调用位置, 并分析改动影响面(v0.9.166)。"
                "支持 Python AST 语法树高精度分析与跨语言(JS/TS、Go、Rust、Java等)语法边界提取。"
                "精准识别调用(call)、导入(import)、继承基类(inherit)、类型注解(type)、写入赋值(assign)与读取(read)。"
                "返回带行号、代码片段、引用类型与影响面风险评估(LOW/MEDIUM/HIGH)的结果。只读安全。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "必需: 要查找引用的符号名称(如函数名、类名、变量名)",
                    },
                    "path_pattern": {
                        "type": "string",
                        "description": "可选: 路径通配过滤模式(如 'minicode/**' 或 '*.py')",
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["", "call", "import", "inherit", "type", "assign", "read"],
                        "description": "可选: 过滤特定引用类型(留空查全部)",
                    },
                    "include_tests": {
                        "type": "boolean",
                        "description": "可选: 是否包含测试代码中的引用, 默认 True",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "可选: 最多返回匹配条数, 默认 50",
                    },
                },
                "required": ["symbol"],
            },
            handler=find_references.find_references_tool,
            read_only=True,
        ),
        Tool(
            name="rename_symbol",
            description=(
                "跨文件重命名函数、类、变量或符号名称, 并预演改动影响(v0.9.167)。"
                "基于全代码库引用定位(call, import, inherit, type, assign, read)与全词边界匹配。"
                "默认 dry_run=True 预演模式: 仅输出涉及文件、改动统计、风险评估与 Unified Diff, 不改写磁盘。"
                "执行模式 dry_run=False: 替换前在内存进行 Python AST 语法校验, 确保无语法冲突后原子写入磁盘。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "必需: 当前需要被重命名的原符号名称",
                    },
                    "new_name": {
                        "type": "string",
                        "description": "必需: 目标新名称(必须为合法编程语言标识符)",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "可选: 是否仅为只读预演(默认 True 仅输出 diff 预览, False 执行写入)",
                    },
                    "path_pattern": {
                        "type": "string",
                        "description": "可选: 路径通配过滤模式(如 'minicode/**' 或 '*.py')",
                    },
                },
                "required": ["symbol", "new_name"],
            },
            handler=rename_symbol.rename_symbol_tool,
            read_only=False,
        ),
        Tool(
            name="call_hierarchy",
            description=(
                "分析函数、方法或符号的跨文件调用拓扑与依赖层次结构(v0.9.168)。"
                "支持分析谁调用了该符号(direction='incoming' 即 Callers/向上追踪调用链)"
                "或该符号内部调用了谁(direction='outgoing' 即 Callees/向下依赖分析),以及双向('both')。"
                "支持递归深度(depth 1..4,默认 2)、防环检测、入度出度统计及 Mermaid 流程图输出(format='mermaid'/'tree')。"
                "只读安全,无副作用。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "必需: 要分析调用关系的函数、方法或类名",
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["incoming", "outgoing", "both"],
                        "description": "可选: 调用分析方向(incoming=调用方, outgoing=被调依赖, both=双向, 默认 incoming)",
                    },
                    "depth": {
                        "type": "integer",
                        "description": "可选: 递归调用层级深度(1~4, 默认 2)",
                    },
                    "path_pattern": {
                        "type": "string",
                        "description": "可选: 路径通配过滤模式(如 'minicode/**' 或 '*.py')",
                    },
                    "format": {
                        "type": "string",
                        "enum": ["tree", "mermaid"],
                        "description": "可选: 输出格式(tree=缩进文本树, mermaid=Mermaid流程图, 默认 tree)",
                    },
                },
                "required": ["symbol"],
            },
            handler=call_hierarchy.call_hierarchy_tool,
            read_only=True,
        ),
        Tool(
            name="symbol_definition",
            description=(
                "跨文件精确提取函数、类、方法、类型与常量的定义位置与实现代码(v0.9.169)。"
                "基于 Python AST 语法树与多语言词法分析，高精度提取起止行(lineno~end_lineno)与完整代码块。"
                "支持从导入语句直接跳转至源定义文件(resolve_import=True)，支持无需额外读文件直接预览实现(peek=True)。"
                "只读安全，无副作用。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "必需: 要查找定义的符号名称(如函数名、类名、方法名或 Class.method)",
                    },
                    "path": {
                        "type": "string",
                        "description": "可选: 限定在指定相对路径文件中查找定义或追踪导入",
                    },
                    "peek": {
                        "type": "boolean",
                        "description": "可选: 是否直接返回定义的完整实现代码片段(默认 True)",
                    },
                    "resolve_import": {
                        "type": "boolean",
                        "description": "可选: 若符号在目标文件中由 import 导入，是否自动追踪至源文件(默认 True)",
                    },
                },
                "required": ["symbol"],
            },
            handler=symbol_definition.symbol_definition_tool,
            read_only=True,
        ),
        Tool(
            name="code_outline",
            description=(
                "提取指定源码文件的结构大纲与类/成员树(v0.9.170)。"
                "基于 Python AST 语法树与多语言解析，提取类定义、继承基类、类装饰器、成员方法、函数签名、常量与文档摘要。"
                "支持折叠深度控制(depth 1..3)、函数参数签名与文档摘要显示开关。"
                "快速纵览文件骨架与精确行号(#Lxx-Lxx)，免读全量源码。只读安全。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "必需: 要提取大纲的代码文件路径(相对工作区或绝对路径)",
                    },
                    "depth": {
                        "type": "integer",
                        "description": "可选: 大纲展开深度(1=仅顶级类/函数, 2=含类成员方法, 3=含嵌套, 默认 2)",
                    },
                    "show_signatures": {
                        "type": "boolean",
                        "description": "可选: 是否显示函数/方法完整参数签名与返回值类型(默认 True)",
                    },
                    "show_docstrings": {
                        "type": "boolean",
                        "description": "可选: 是否显示类与函数的文档摘要注释(默认 True)",
                    },
                },
                "required": ["path"],
            },
            handler=code_outline.code_outline_tool,
            read_only=True,
        ),
    ]


