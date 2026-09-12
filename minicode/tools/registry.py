"""内置工具注册表。"""
from __future__ import annotations

from minicode import kb, skills
from minicode.tools import crawl, fs, search, shell
from minicode.tools.base import Tool


def build_default_registry() -> list[Tool]:
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
                },
                "required": ["command"],
            },
            handler=shell.bash,
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
            name="web_crawl",
            description="抓取网页或整站为 markdown(基于 crawl4ai)。从 url 出发同域 BFS 跟链,"
                        "可限页数/深度/限速;结果存到工作区 crawled/<域名>/ 下,可选 to_kb=True "
                        "同时写入本地知识库(credibility=A,附来源网址)供 search_knowledge 检索。",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "起始网址,须带 http:// 或 https://"},
                    "max_pages": {"type": "integer", "description": "最多抓取页数,默认 10,上限 100"},
                    "max_depth": {"type": "integer", "description": "跟随同域链接的深度,0=只抓当前页,默认 1"},
                    "match": {"type": "string", "description": "只跟进 URL 含该子串的链接,如 'zs' 只抓招生网;默认不限"},
                    "out_dir": {"type": "string", "description": "输出目录(相对工作区),默认 crawled"},
                    "to_kb": {"type": "boolean", "description": "同时写入本地知识库 kb/crawl-<域名>.json,默认 false"},
                    "kb_source": {"type": "string", "description": "知识库条目的来源名称,默认用域名"},
                    "delay": {"type": "number", "description": "两次请求间隔秒数(礼貌抓取),默认 1"},
                },
                "required": ["url"],
            },
            handler=crawl.crawl_web,
        ),
        Tool(
            name="use_skill",
            description="读取一个技能的完整指导再行动。可用技能清单见系统提示词;接到与技能相关的任务时应先调用本工具。",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "技能名,如 git-commit"},
                },
                "required": ["name"],
            },
            handler=skills.use_skill,
            read_only=True,
        ),
        Tool(
            name="search_knowledge",
            description="检索本地知识库(校园通知、奖助学金、竞赛、证书、考研就业、心理调适等资料),返回带来源网址与可信度的条目。回答相关问题时必须先用本工具,引用时给出网址供用户核实。",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "查询词,如:国家励志奖学金 / 蓝桥杯 / 考研择校"},
                    "top_k": {"type": "integer", "description": "返回条数,默认 3"},
                    "category": {"type": "string", "description": "按分类过滤,如:奖助学金 / 学科竞赛 / 考研"},
                },
                "required": ["query"],
            },
            handler=kb.search_knowledge,
            read_only=True,
        ),
    ]
