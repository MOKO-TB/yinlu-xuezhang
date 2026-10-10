"""后台命令家族的三只手(v0.9.146 从 catalog_files.py 拆出来)。

看进度(`bash_status`)、终止(`bash_kill`)、喂输入(`bash_input`)。
派活那一步是 `bash` 的 `background=true`,本体留在 `catalog_files.py`。

**为什么单独一份**:`catalog_files.py` 撞上了「每份目录 < 400 行」那道闸
(`tests/test_registry_split.py::test_no_catalog_is_a_new_hotspot`)。那条约定的
本意写在它的 docstring 里 —— 「拆分的目的是消热点,不是把 987 行搬到另一个文件
里」—— 所以这里按**领域**拆而不是按行数拆:后台这一族的状态机、生命周期与回收
全在 `shell.py` 里,它本来就是一块。

搬的是**原文**:每个 `Tool(...)` 的字段与描述一字未改,只是换了文件。
注册表里这些工具的**先后顺序**由 `registry.build_default_registry()` 决定,
并由 `tests/test_registry_split.py` 逐字钉住 —— 别在这里自己排。
"""
from __future__ import annotations

from minicode.tools import shell
from minicode.tools.base import Tool


def tools() -> list[Tool]:
    """后台命令家族:看进度 / 终止 / 喂输入。"""
    return [
        Tool(
            name="bash_status",
            description=(
                "看后台命令跑到哪了:不带 id 列出本次运行起过的所有后台任务"
                "(在不在跑、跑了多久、输出了多少字),带 id 则回看那一个的状态"
                "与输出尾部。只读 —— 不会启动、也不会终止任何东西。"
                "两个已知边界:①输出只在内存里留尾部(超出缓冲的部分被丢掉并如实报数),"
                "学长退出就没了;②这里看不到后台任务往磁盘上写了什么,产物请用 "
                "read_file/glob 或「任务监控」的产出行确认。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "id": {"type": "string",
                           "description": "job 编号(如 j3);不传就列全部"},
                    "tail": {"type": "integer",
                             "description": "回看输出的字符数,默认 4000,上限 30000"},
                },
            },
            handler=shell.bash_status,
            read_only=True,
        ),
        Tool(
            name="bash_kill",
            description=(
                "终止一个后台命令。Windows 上走 taskkill /T 连子进程一起杀"
                "(Git Bash 那条命令真正干活的是它下面的 python/pytest,只杀 bash "
                "会留孤儿);这台机器没有 taskkill 时只能杀直接子进程,"
                "**会照实告诉你子进程可能还活着**,不假装杀干净。已经结束的不用杀,"
                "回一句退出码就完事。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "要终止的 job 编号(如 j3)"},
                },
                "required": ["id"],
            },
            handler=shell.bash_kill,
        ),
        Tool(
            name="bash_input",
            description=(
                "给一条**后台**命令的标准输入写一行(或一段,newline=false)。"
                "用它解开「下一步取决于上一步的输出」这类流程:npm init、REPL、"
                "要敲 y 的脚手架 —— 这些用预写文件加重定向是无效的。"
                "**只对后台生效**:前台命令的 stdin 是空的,而且它是同步等结果的调用,"
                "根本没有「写进去」的时机。四条边界:①一次最多 4000 字,**超了拒绝"
                "而不是截断**(喂进去半句,命令会拿它当完整输入去执行,而回话看起来"
                "一切正常);②**不能发 Ctrl+C、也不能给 EOF** —— 要停用 bash_kill,"
                "要 EOF 就把输入写成工作区文件再用重定向;③写进去的内容**不经过**"
                "命令文本那道判定(这里走的是 text 这个参数,不是 command);"
                "④这条命令此刻若没在读输入,回话会如实说「还挂着」,"
                "**不会杀它、也不会重试**。已经结束的任务写不进去,回话给退出码。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "id": {"type": "string",
                           "description": "job 编号(如 j3);只认后台任务"},
                    "text": {"type": "string",
                             "description": "要写进去的内容,最多 4000 字(超了拒绝,不截断)"},
                    "newline": {"type": "boolean",
                                "description": "true(默认)= 自动补一个换行;单键菜单、"
                                               "(y/n) 提示、raw 模式传 false"},
                    "wait_ms": {"type": "integer",
                                "description": "写完最多再等多少毫秒让输出尾巴带上对方的"
                                               "回应,默认 1500、上限 5000;这一步只是"
                                               "「更可能」,不是保证"},
                    "reason": {"type": "string",
                               "description": "用一句中文向用户说明这一步要达成什么"
                                              "(显示在权限确认框里)"},
                },
                "required": ["id", "text"],
            },
            handler=shell.bash_input,
        ),
    ]
