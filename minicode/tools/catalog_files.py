"""文件与命令类工具(v0.9.39 #84 从 registry.py 搬出来)。

读写文件、按模式找文件、按正则搜内容,以及两条会动手的执行入口(bash / py_script)。

搬的是**原文**:每个 `Tool(...)` 的字段与描述一字未改,只是换了文件。
注册表里这些工具的**先后顺序**由 `registry.build_default_registry()` 决定,
并由 `tests/test_registry_split.py` 逐字钉住 —— 别在这里自己排。
"""
from __future__ import annotations

from minicode.tools import fs, search, shell
from minicode.tools.base import Tool


def tools() -> list[Tool]:
    """文件与命令类工具。"""
    return [
        Tool(
            name="read_file",
            description="读取工作区中一个文本文件,返回带行号的内容。大文件会被截断,可用 offset/limit 分段读取。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对工作区的文件路径"},
                    "offset": {"type": "integer", "description": "起始行号(从 1 开始),默认 1"},
                    "limit": {"type": "integer", "description": "最多读取行数,默认 2000"},
                },
                "required": ["path"],
            },
            handler=fs.read_file,
            read_only=True,
        ),
        Tool(
            name="write_file",
            description="创建或覆盖一个文件。仅在新文件或整文件重写时使用;局部修改请用 edit_file。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对工作区的文件路径"},
                    "content": {"type": "string", "description": "完整的文件内容"},
                    "reason": {"type": "string",
                               "description": "用一句中文向用户说明这次写文件要达成什么目的(显示在权限确认框里),如:新建地图数据结构文件"},
                },
                "required": ["path", "content"],
            },
            handler=fs.write_file,
        ),
        Tool(
            name="edit_file",
            description="对文件做精确字符串替换。old_string 必须与文件内容逐字符一致(含缩进)且唯一,否则会报错。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对工作区的文件路径"},
                    "old_string": {"type": "string", "description": "要被替换的原文(建议包含前后各几行上下文以保证唯一)"},
                    "new_string": {"type": "string", "description": "替换后的文本"},
                    "replace_all": {"type": "boolean", "description": "所有匹配处全部替换,默认 false"},
                    "reason": {"type": "string",
                               "description": "用一句中文向用户说明这次改动要达成什么目的(显示在权限确认框里),如:修掉切换会话时的多余弹窗"},
                },
                "required": ["path", "old_string", "new_string"],
            },
            handler=fs.edit_file,
        ),
        Tool(
            name="bash",
            description="在工作区目录执行一条 shell 命令并返回退出码与输出。用于运行脚本、测试、git 等只读或用户确认后的操作。",
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的命令"},
                    "timeout": {"type": "integer", "description": "超时秒数,默认 60,最大 300"},
                    "reason": {"type": "string",
                               "description": "用一句中文向用户说明这条命令要达成的目的(显示在权限确认框里),如:列出知识库里 8 月以来的新条目"},
                },
                "required": ["command"],
            },
            handler=shell.bash,
        ),
        Tool(
            name="py_script",
            description=(
                "用应用内置的 Python 运行时执行工作区里的一个脚本文件并返回输出。"
                "机器上不需要装 Python;内置环境带 python-pptx / python-docx / "
                "openpyxl / pypdf 等文档库,做 PPT/Word/Excel/PDF 转换时先按相应"
                "技能(write_file 写脚本)再调本工具运行。脚本里请 print 关键"
                "步骤与产物路径;长时间任务可调 timeout(上限 300 秒)。"
                "两条边界:①运行前会先读一遍脚本内容,出现凭据/密钥路径、工作区外"
                "路径或联网出口时会把命中的那几行摆进确认框(这一层不是隔离,是让你"
                "看一眼再决定);②子进程环境里名字像凭据的变量默认被摘掉,别指望用 "
                "os.environ 读到模型服务的 Key。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string",
                             "description": "脚本路径(相对工作区,先用 write_file 写好)"},
                    "timeout": {"type": "integer",
                                "description": "超时秒数,默认 120,上限 300"},
                    "reason": {"type": "string",
                               "description": "用一句中文说明这个脚本要达成什么目的(显示在权限确认框里)"},
                },
                "required": ["path"],
            },
            handler=shell.py_script,
        ),
        Tool(
            name="glob",
            description="按通配符模式匹配文件名(支持 ** 递归),返回相对路径列表。",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "glob 模式,如 src/**/*.py"},
                    "path": {"type": "string", "description": "起始目录,默认工作区根"},
                },
                "required": ["pattern"],
            },
            handler=search.glob,
            read_only=True,
        ),
        Tool(
            name="grep",
            description="按正则表达式搜索文件内容,输出 路径:行号: 内容。自动跳过 .git、node_modules 等目录。",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "正则表达式"},
                    "path": {"type": "string", "description": "搜索的文件或目录,默认工作区根"},
                    "glob": {"type": "string", "description": "按文件名过滤,如 *.py"},
                    "ignore_case": {"type": "boolean", "description": "忽略大小写,默认 false"},
                },
                "required": ["pattern"],
            },
            handler=search.grep,
            read_only=True,
        ),
        Tool(
            name="repo_outline",
            description=(
                "工作区结构大纲:目录树 + 每个 Python 文件的顶层 def/class/常量,一次调用看清全貌。"
                "开始一个探索/修改任务前先调它,能省掉反复的 glob+grep;可用 path 缩小到子目录。"
                "依赖目录(.venv/__pycache__ 等)、.git、隐藏文件与超大文件(如 crawled/ 馆藏库宿主)自动跳过,"
                "条目上限防大仓库刷屏。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string",
                             "description": "可选:从工作区内哪个子目录开始(默认整个工作区)"},
                    "max_entries": {"type": "integer",
                                    "description": "最多列多少项(默认 120,防大仓库刷屏)"},
                },
            },
            handler=fs.repo_outline,
            read_only=True,
        ),
    ]
