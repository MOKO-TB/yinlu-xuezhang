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

from minicode import mailbox
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

        # --------------------- 递话(v0.9.173):正在跑的对象之间留一句话
        #
        # 与上面三只(看后台命令 / 杀 / 喂输入)同属"多任务并行时的手"这一族:
        # 那三只管**命令**,这一只管**会话与子代理** —— 一条在跑的东西想让
        # 另一条知道点事。完整的边界写在 `minicode/mailbox.py` 的模块说明里,
        # 这里的 description 只讲用法与"谁真的用得上",不把它吹成常驻双向通道。
        SESSION_MESSAGE,
    ]


SESSION_MESSAGE = Tool(
    name="session_message",
    description=(
        "递话 / 看在跑的对象:把一句话放进**正在跑的子代理**或**另一条活会话**的"
        "盒子里,它下一次发请求前就会读到(读到的形态是系统侧注记,"
        "**不会冒充用户说的话**)。action=list 先看现在有谁在跑"
        "(带 handle 与各自己压了几条);action=send 递一句话,目标写 list 里"
        "那个 handle(标签重名时只认 handle —— 递错人比多打几个字坏得多)。\n"
        "**四条边界,先说清楚**:①主代理调 `task` 时**阻塞等报告**,所以它自己"
        "多半调不了这只工具 —— 真正即时的投递方是**另一条并发会话**"
        "(侧聊页那条、后台任务)与界面上的人;②只在对方**两步之间**递,不入它的"
        "handler 中间(那会把调用与结果拆对);③**没有回执**,对方收下了会不会照做"
        "这一层管不着;④盒子活在内存里,对方跑完/关掉就摘 —— 摘了以后递会得到"
        "一句实话,不是静默丢掉。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "send"],
                       "description": "list(默认)看在跑的谁;send 递一句话"},
            "target": {"type": "string",
                       "description": "send 时必填:list 里那个 handle(如 sub-1 / sess-2)"},
            "text": {"type": "string",
                     "description": "要递的那句话;上限 1200 字,超了会截断并写明截了几字"},
        },
        "required": ["action"],
    },
    handler=mailbox.session_message,
    # **只读**:它不写盘、不起进程、不改本机任何东西 —— 只把一句话放进另一个盒子。
    # 不需要确认框的理由与 `notify_parent` 一字不差:那张框会弹在**递话那一侧**,
    # 而点头的人管不了对面要不要照做 —— 那是给了个假的把关。对面真去写文件时,
    # **它自己的**权限门照样拦(所以这条链没有提权)。真正要堵的是对外形态:
    # 访客绝不能往主人的会话里递话(`web_profile` 里写死了排除)。
    read_only=True,
)
