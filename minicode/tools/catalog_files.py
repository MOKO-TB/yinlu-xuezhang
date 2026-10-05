"""文件与命令类工具(v0.9.39 #84 从 registry.py 搬出来)。

读写文件、按模式找文件、按正则搜内容,以及三条会动手的执行入口(bash / py_script
/ bash_kill),外加一条只读的 bash_status。

搬的是**原文**:每个 `Tool(...)` 的字段与描述一字未改,只是换了文件。
注册表里这些工具的**先后顺序**由 `registry.build_default_registry()` 决定,
并由 `tests/test_registry_split.py` 逐字钉住 —— 别在这里自己排。
"""
from __future__ import annotations

from minicode import hook_packs, permspec, worktree as wt
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
            description=(
                "在工作区目录执行一条 shell 命令并返回退出码与输出。用于运行脚本、"
                "测试、git 等只读或用户确认后的操作。"
                "**预计要跑超过 300 秒的活儿(全量测试、装依赖、构建、大数据批处理)"
                "请传 background=true**:命令放后台跑、立刻返回一个 job 编号,"
                "之后用 bash_status 看进度与输出尾部、bash_kill 终止。"
                "后台任务不会因为这一轮结束而停止,也不受 timeout 限制 —— "
                "没人替它超时收手,要停必须显式 kill;输出只存在内存里,"
                "学长退出或重启就没了,要留档请自己重定向到工作区文件。"
                "**命令不会等你输入**:它的标准输入是空的 —— 任何「按任意键继续」、"
                "密码提示、交互提问都会立刻拿到 EOF(然后要么直接结束要么报错)。"
                "需要喂输入就把内容写成工作区文件,再用重定向交给命令。"
                "只要还有后台任务,学长会把它们的真实状态(poll 到的,不是猜的)附在"
                "下一次请求末尾的一行里 —— 不必每一步都轮询 bash_status;但那行只说"
                "「进程结束了、退出码是几」,活儿对不对仍要看 bash_status 的输出,"
                "不许看到那行就替用户宣布「跑完了」。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的命令"},
                    "timeout": {"type": "integer", "description": "超时秒数,默认 60,最大 300(background=true 时不适用)"},
                    "background": {"type": "boolean",
                                   "description": "true = 放后台跑,立刻返回 job 编号(长任务用这个,别把 timeout 顶到 300 干等)"},
                    "reason": {"type": "string",
                               "description": "用一句中文向用户说明这条命令要达成的目的(显示在权限确认框里),如:列出知识库里 8 月以来的新条目"},
                },
                "required": ["command"],
            },
            handler=shell.bash,
        ),
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
def perms_tools() -> list[Tool]:
    """项目级白名单的两只工具(v0.9.87):看 = 只读,批准/撤销 = 要确认。

    分两只而不是一个 tool 带四个 action:按动作切读写属性是这里唯一能把
    「查一眼」和「从此免确认放行」区分开的地方 —— 合成一只就得二选一,
    要么批准不弹框(危险),要么看一眼也弹框(没人愿意看)。
    """
    return [
        Tool(
            name="permissions",
            description=(
                "看这个项目的**权限白名单**现状:有没有 permissions.json、几条规则、"
                "用户核对批准过没有、没生效的原因、装载时拒掉了哪几条。"
                "只读,不改任何东西。用户问「为什么这次没问我」「这个项目批了什么」时用。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["show", "verify"],
                               "description": "show 看现状;verify 逐条列出规则与文件指纹"},
                },
            },
            handler=permspec.permissions,
            read_only=True,
        ),
        Tool(
            name="permissions_trust",
            description=(
                "把当前这份 permissions.json 的**内容指纹**记进本机信任库(或撤销)。"
                "没批准过的白名单一条都不生效 —— 这是刻意的:clone 别人的仓库带进来一份、"
                "或者网页里的内容鼓动我自己写一份,都跟用户自己想批权限长得一样,"
                "只能由用户在会话之外点这一次头。文件之后改一个字,批准自动失效。"
                "必须由用户明确说「批准这个项目的权限」才调用,不要自己决定去批准。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["trust", "revoke"],
                               "description": "trust 批准当前文件;revoke 撤销批准"},
                },
            },
            handler=permspec.permissions_trust,
        ),
    ]


def hooks_tools() -> list[Tool]:
    """内置钩子配方(L3,v0.9.116):查与挂分两只,理由与 perms_tools 那一对同一份。

    写的是**用户级 config.json** 的 hooks 字段 —— 那是任意命令执行的口子,所以
    `hooks_apply` 必须 read_only=False(要弹框),且只接受 `hooks` 预览时给的一次性
    凭据;而"用户明确说要挂哪条"这件事由提示词管(描述里写着不许自己决定去挂)。
    """
    return [
        Tool(
            name="hooks",
            description=(
                "看**工具钩子**现状与可挂的内置配方:现在 config.json 里挂着哪几条、"
                "有哪两条配方(git 命令审计 / 改完 Python 跑一次 pytest)、挂上去各会"
                "发生什么、风险是什么。preview 会把命令原文与范围摊开并给一次性的凭据,"
                "但 preview 本身**不写任何东西**。用户问「我配了钩子吗」「有没有现成的"
                "自动化可以开」时用。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "preview"],
                               "description": "list 看现状与清单;preview 摊开一条配方"},
                    "template": {"type": "string",
                                 "description": "preview 要哪条配方(git-audit / run-tests)"},
                },
                "required": ["action"],
            },
            handler=lambda ctx, action="list", template="": (
                hook_packs.preview(ctx, template) if str(action).strip().lower() == "preview"
                else hook_packs.list_line(ctx)),
            read_only=True,
        ),
        Tool(
            name="hooks_apply",
            description=(
                "把一条已 preview 过的钩子配方**挂进用户 config.json**(先备份旧配置,"
                "run-tests 还会写一只可见的助手脚本)。必须由用户明确说「挂上这条」"
                "并给出 preview 里那个凭据才调用 —— **绝不要自己决定去开自动化**,"
                "更不许没 preview 就凭记忆填 nonce。挂上之后立刻生效,不用重启;"
                "要摘回来说一声。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "template": {"type": "string",
                                 "description": "配方名,须与 preview 的那一条一致"},
                    "nonce": {"type": "string",
                              "description": "preview 给的一次性凭据(十分钟有效)"},
                },
                "required": ["template", "nonce"],
            },
            handler=hook_packs.apply,
        ),
    ]


def worktree_tools() -> list[Tool]:
    """Git 工作副本(v0.9.98,roadmap-git-worktree隔离 W1):只报事实与建议,
    建副本本身必须走 bash 那条确认框 —— 本工具绝不自己建/删目录。"""
    return [
        Tool(
            name="worktree",
            description=(
                "Git 工作副本(隔离工作区):同一个仓库要同时做两件事、两边互不踩"
                "文件时,在仓库同级开一份 `<项目>__<分支>/` 工作副本。"
                "action=list(默认)报现状:现在这份是不是 git 仓库、有哪几份副本、"
                "各在哪个分支、有没有未提交改动(逐行都有 git 现查的事实撑着);"
                "action=changes 列**相对 HEAD 改了哪些文件**(含命令与编辑器改的,那些不在"
                "「本轮改动」那份账里;看得见但退不掉,这句由后端带着回);"
                "action=suggest 给 branch(如 login-fix)算出落点与**要执行的命令**:"
                "命令必须走 bash 执行(会弹确认框,绝不偷偷建目录),建好后告诉用户"
                "点顶栏「📁 工作区」把它选成工作区。三条口径照实说:①副本是普通"
                "工作区 —— 沙箱与项目白名单对它一视同仁,白名单默认**不生效要重批**"
                "(隔离出来的目录不继承原目录的授权);②长期记忆跟工作区走,"
                "课表/待办/心情是全局一份;③本工具只读,**删除副本永远不做**"
                "(那是别人正在改的东西)。不是 git 仓库时照实说,不硬凑。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "suggest", "changes"],
                               "description": "list=现状(默认,查当前工作区);"
                                              "suggest=给 branch 算落点与命令"},
                    "branch": {"type": "string",
                               "description": "suggest 用:副本对应的分支名(如 login-fix)"},
                    "path": {"type": "string",
                             "description": "可选:仓库路径;不填=当前工作区"},
                    "exists": {"type": "boolean",
                               "description": "suggest 用:分支已存在时传 true,改成直接检出而不是新建"},
                },
            },
            handler=wt.worktree_tool,
            # 刻意只读(v0.9.99 拍板,§6 #45 那笔"全表唯一写盘却免确认"的疑案):
            # handler 只跑 `git worktree list --porcelain` / `git status --porcelain`
            # 两条查事实的命令,自己一个字节都不写;真正建副本的是它**建议**的那条
            # `git worktree add` —— 那条走 bash 的确认框,两条口径并不打架。
            # 这也是它该进只读子代理工具集的原因:audit 档查"现在有几份副本、
            # 各自干净不干净"是正经的只读侦察。
            read_only=True,
        ),
    ]
