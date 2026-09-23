"""内置工具注册表。"""
from __future__ import annotations

from minicode import growth
from minicode import calendar as school_calendar
from minicode import reminders, tray
from minicode import campus, gpa, kb, library, library_guide, memory, mood, planning, schedule, site_parse, skills, weather
from minicode.tools import browser as browser_tools
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
            name="web_crawl",
            description="抓取网页或整站为 markdown(基于 crawl4ai)。从 url 出发同域 BFS 跟链,"
                        "可限页数/深度/限速;结果存到工作区 crawled/<域名>/ 下,可选 to_kb=True "
                        "同时写入本地知识库(学校官网白名单域名标 A,其余域名自动降为 "
                        "B-抓取(未核实),附来源网址)供 search_knowledge 检索。",
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
                    "kb_category": {"type": "string", "description": "知识库条目的主题分类,如:奖助学金 / 入党 / 考研升学 / 学科竞赛(检索时标签权重×2),默认空"},
                    "kb_tags": {"type": "array", "items": {"type": "string"},
                                "description": "知识库条目的附加标签,如 [\"奖学金\",\"国家励志奖学金\"]"},
                    "delay": {"type": "number", "description": "两次请求间隔秒数(礼貌抓取),默认 1"},
                },
                "required": ["url"],
            },
            handler=crawl.crawl_web,
            trust="external",
        ),
        Tool(
            name="web_search",
            description=(
                "联网搜索(必应优先、百度兜底),返回标题+网址+摘要的结果列表。"
                "本地知识库(search_knowledge)没命中、又需要查证的事实类问题时用它;"
                "回答必须注明来源网址,与校园官方信息冲突时以官方/知识库为准。"
                "要网页正文再接 web_crawl 或 browser_open。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词(短语化查询词,不是整句)"},
                    "top_k": {"type": "integer", "description": "返回条数,默认 6,上限 10"},
                    "reason": {"type": "string",
                               "description": "用一句中文说明要查什么、为什么(显示在权限确认框里)"},
                },
                "required": ["query"],
            },
            handler=crawl.web_search,
            trust="external",
        ),
        # ---------------- 浏览器(browser_*)----------------
        # 与 web_crawl 的分工:crawl 抓静态正文入库;browser 是「真的开一个浏览器」——
        # 能看 JS 渲染后的页面、能拿登录态(连用户自己的浏览器)、能截图给用户看。
        Tool(
            name="browser_open",
            description=(
                "用真实浏览器打开一个网页,返回 JS 渲染完成后的标题与正文文本。"
                "凡是「网页抓不到内容 / 页面要靠 JS 渲染 / 要看自己写的页面"
                "(localhost)效果」都用它,而不是 web_crawl。"
                "同一会话里重复调用会复用同一个标签页(相当于刷新成新地址),"
                "所以连续翻页很便宜。"
                "默认自己起一个独立浏览器实例(零配置、不带你现有登录态);"
                "**只有要看需要登录才看得到的页面**(教务系统、我的课表、已登录的站内页)"
                "才传 mode=attach——它会用用户自己开着远程调试的浏览器,以他的身份取页。"
                "要拿精确数据(价格/表格/链接列表)接着用 browser_eval,要看画面用 browser_screenshot。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要打开的网址,须为 http/https"},
                    "wait_ms": {"type": "integer",
                                "description": "最多等页面加载多久(毫秒),默认 8000,上限 30000"},
                    "wait_for": {"type": "string",
                                 "description": "可选的 CSS 选择器:等到它出现才算加载好,如 #app .list"},
                    "max_chars": {"type": "integer",
                                  "description": "正文返回上限字符数,默认 6000,上限 20000"},
                    "mode": {"type": "string", "enum": ["auto", "attach", "launch"],
                             "description": "默认 auto=自己起一个干净实例(无登录态);attach=改用用户自己开着的浏览器(带他的登录态),只在页面必须登录才看得到时用;launch=显式要求独立实例"},
                    "headless": {"type": "boolean",
                                 "description": "mode=launch 时是否无窗口运行,默认 true"},
                    "reason": {"type": "string",
                               "description": "用一句中文向用户说明这次要打开什么页面、为什么(显示在权限确认框里)"},
                },
                "required": ["url"],
            },
            handler=browser_tools.browser_open,
            trust="external",
        ),
        Tool(
            name="browser_eval",
            description=(
                "在「当前页面」里执行一段 JavaScript 并把结果返回,用于精确取数或操作页面:"
                "取表格/价格/链接列表(返回 JSON 更好用)、读某元素的文字、判断元素是否存在、"
                "点按钮(如 document.querySelector('#btn').click())、滚动到页面底部后再取内容。"
                "需要先 browser_open 打开页面。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "js": {"type": "string",
                           "description": "要执行的 JS 表达式或语句,返回值会被带回,如 JSON.stringify([...document.querySelectorAll('a')].map(a=>a.href))"},
                    "await_promise": {"type": "boolean",
                                      "description": "表达式返回 Promise 时是否等它完成(如 fetch),默认 false"},
                    "max_chars": {"type": "integer", "description": "返回值截断长度,默认 4000"},
                    "reason": {"type": "string", "description": "用一句中文说明这次要取什么(显示在权限确认框里)"},
                },
                "required": ["js"],
            },
            handler=browser_tools.browser_eval,
            trust="external",
        ),
        Tool(
            name="browser_snapshot",
            description=(
                "列出「当前页面」上所有可见、可交互的元素(链接/按钮/输入框/下拉框),"
                "每个带序号和文字/字段名,供 browser_click 与 browser_fill 定位。"
                "准备点按钮、填表单之前先调它,不要凭空猜选择器。"
                "需要先 browser_open 打开页面。只读,不改动页面。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "max_items": {"type": "integer",
                                  "description": "最多列多少个元素,默认 120"},
                    "reason": {"type": "string", "description": "可选:一句中文说明"},
                },
            },
            handler=browser_tools.browser_snapshot,
            trust="external",
            read_only=True,
        ),
        Tool(
            name="browser_click",
            description=(
                "点击当前页面上的一个元素。target 三种写法:"
                "① 快照序号(如 \"3\",browser_snapshot 给的,最稳——页面一变序号就失效);"
                "② 按钮文字(如 \"登录\",页面上有多个同名的会报错让你先 snapshot);"
                "③ CSS 选择器(如 \"#login-btn\")。点击后自动等页面稳定并返回新页面的标题与地址。"
                "需要先 browser_open 打开页面;找不到目标会提示先 browser_snapshot。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target": {"type": "string",
                               "description": "快照序号 / 元素文字 / CSS 选择器"},
                    "by": {"type": "string", "enum": ["auto", "index", "text", "selector"],
                           "description": "定位方式;auto=纯数字按序号、否则按文字"},
                    "wait_ms": {"type": "integer",
                                "description": "点击后等多久再看页面状态,默认 1500ms"},
                    "reason": {"type": "string",
                               "description": "用一句中文说明要点什么、为什么(显示在权限确认框里)"},
                },
                "required": ["target"],
            },
            handler=browser_tools.browser_click,
            trust="external",
        ),
        Tool(
            name="browser_fill",
            description=(
                "在当前页面的表单里按字段名批量填值,可选自动点提交。"
                "fields 传 {\"字段名\": \"值\"};字段名可以是 name/id/占位符/aria/标签文字"
                "(如 {\"用户名\":\"lihua\",\"密码\":\"xx\",\"记住我\":true});"
                "下拉框按选项文字或值匹配,勾选框接受 true/false。"
                "找不到字段时先 browser_snapshot 看字段名。填完可选 submit=true 自动点提交按钮,"
                "并返回提交后的页面状态。密码框的值不会回显在结果里。"
                "需要先 browser_open 打开页面。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "fields": {"type": "object",
                               "description": "字段名到值的映射,如 {\"用户名\": \"lihua\", \"记住我\": true}"},
                    "submit": {"type": "boolean",
                               "description": "全部填好后是否自动点提交按钮,默认 false"},
                    "wait_ms": {"type": "integer",
                                "description": "提交后等多久看页面状态,默认 1500ms"},
                    "reason": {"type": "string",
                               "description": "用一句中文说明要填什么表单、为什么(显示在权限确认框里)"},
                },
                "required": ["fields"],
            },
            handler=browser_tools.browser_fill,
            trust="external",
        ),
        Tool(
            name="browser_screenshot",
            description=(
                "把「当前页面」截图保存到工作区,并在界面上直接把这张图给用户看。"
                "用户说「截图看看 / 这页面长什么样」、或需要证明页面效果/排版是否正确时用它。"
                "需要先 browser_open 打开页面。回答里请原样带上工具返回的 ![](路径) 一段,用户才能看到图。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string",
                             "description": "保存路径(相对工作区),默认 shots/网页-时间戳.png"},
                    "full_page": {"type": "boolean",
                                  "description": "整页截图(含滚动部分),默认 true;false 则只截当前可视区"},
                    "reason": {"type": "string", "description": "用一句中文说明为什么要截图(显示在权限确认框里)"},
                },
            },
            handler=browser_tools.browser_screenshot,
            trust="external",
        ),
        Tool(
            name="browser_close",
            description="关闭当前页面(浏览器里的标签页一并关掉)。这一轮的页面看完了就顺手关掉,别让浏览器堆一屏标签。",
            parameters={
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "可选:一句中文说明"},
                },
            },
            handler=browser_tools.browser_close,
            trust="external",
        ),
        Tool(
            name="browser_status",
            description="检查浏览器能力是否可用(能不能连上浏览器、走哪种方式)。browser_open 报错时用它自查。",
            parameters={
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "可选:一句中文说明"},
                },
            },
            handler=browser_tools.browser_status,
            trust="external",
            read_only=True,
        ),
        Tool(
            name="page_parse",
            description=(
                "把**当前浏览器页面**里的表格结构化(读所有内嵌帧,不挑学校):"
                "课表类表格直接拆成课程条目(课名/星期/节次/周次/地点/教师),"
                "别的表(考试安排、书目、空教室、通知)会如实告诉你「这不是课表」,"
                "并把原表的行列按文本一并给你 —— 不用再手写 browser_eval 去抠。"
                "凡是校园系统里那张表要用 browser_eval 手写 JS 去抠的,先试这个 —— "
                "它按版式识别,认得出的部分给你,认不出的行**原样列在 raw 里**,"
                "你照着问同学,绝不要自己补。只读,不写任何文件、不替你存课表。"
                "需要先 browser_open 打开页面(要登录的页面由用户自己在弹出窗口里登录)。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url_like": {"type": "string",
                                 "description": "可选:只读 URL 含这个片段的帧(如 xskb),默认读所有帧"},
                    "reason": {"type": "string", "description": "一句中文说明要解析哪一页、为什么"},
                },
            },
            handler=site_parse.page_parse,
            trust="external",
            read_only=True,
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
            trust="principal",
            read_only=True,
        ),
        Tool(
            name="plan",
            description=(
                "复杂任务的计划管理(保存在工作区 plans/plan.md)。"
                "action=write:列 2-8 步计划,然后展示给用户等待确认再执行;"
                "步骤可用 depends_on 声明依赖(如第 3 步 depends_on=[1,2]),"
                "只有依赖全部完成的步骤才是「可执行」,互不依赖的可并行派子代理;"
                "action=update:每完成一步标记 status=done(可附 note);"
                "**动手做某一步之前先标 in_progress**,聊天里的计划卡片就会把那行"
                "显示成「进行中」,做完再标 done;"
                "失败的上游步骤标 status=failed,下游会被自动判为「被卡住」;"
                "action=read:查看当前计划与调度视图(进行中/可执行/被卡住的步骤)。"
                "接到多文件/多阶段的复杂任务时应先建计划。"
                "写完不必反复 read 念给用户:计划会以一张**内联清单**出现在对话里"
                "(顶栏「🗂 计划」也同步显示每一步的状态与进度),提醒用户看那里即可。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["write", "read", "update"],
                               "description": "write=新建/覆盖计划;read=查看;update=更新某步状态"},
                    "goal": {"type": "string", "description": "action=write 时:计划目标(一句话)"},
                    "steps": {"type": "array", "description": "action=write 时:2-8 步,每步 {title, detail?, depends_on?},每步一个可独立验证的动作;depends_on 是前置步骤编号数组,如 [1,2]",
                              "items": {"type": "object", "properties": {
                                  "title": {"type": "string"},
                                  "detail": {"type": "string"},
                                  "depends_on": {"type": "array", "items": {"type": "integer"},
                                                 "description": "前置步骤编号;这些步骤完成后本步才可执行"}}}},
                    "step": {"type": "integer", "description": "action=update 时:步骤编号"},
                    "status": {"type": "string",
                               "enum": ["in_progress", "done", "pending", "failed"],
                               "description": "action=update 时:新状态,默认 done。"
                                              "动手做某一步之前先标 in_progress(聊天里的计划卡片会把它显示成"
                                              "「进行中」那一行),做完立刻标 done;failed 表示这步没做成,下游会被判为卡住"},
                    "note": {"type": "string", "description": "action=update 时:可选备注"},
                },
                "required": ["action"],
            },
            handler=planning.plan,
        ),
        Tool(
            name="search_knowledge",
            description="检索本地知识库(校园通知、奖助学金、竞赛、证书、考研就业、心理调适等资料),返回带来源网址与可信度的条目。回答相关问题时必须先用本工具,引用时给出网址供用户核实。用户问细节时把 snippet_chars 调大,直接基于原文回答,避免再去爬官网让用户等。",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "查询词,如:国家励志奖学金 / 蓝桥杯 / 考研择校"},
                    "top_k": {"type": "integer", "description": "返回条数,默认 3"},
                    "category": {"type": "string", "description": "按分类过滤,如:奖助学金 / 学科竞赛 / 考研"},
                    "snippet_chars": {"type": "integer",
                                      "description": "每条返回的正文长度,默认 160(速览);问评定条件/流程/时间等细节时用 600-1200"},
                },
                "required": ["query"],
            },
            handler=kb.search_knowledge,
            trust="external",
            read_only=True,
        ),
        Tool(
            name="campus_calendar",
            description=(
                "查校历:放假安排(国庆/中秋/寒暑假)、四六级等考试时间、报名截止等,"
                "带倒计时天数与来源。用户问「什么时候放假/考试/还有几天到 XX」时用它,"
                "不要凭印象报日期。数据里没有的日期要如实说「还没录入,以教务处通知为准」。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["upcoming", "query"],
                               "description": "upcoming=接下来有什么(默认);query=按关键词查"},
                    "query": {"type": "string",
                              "description": "action=query 时:关键词,如 国庆 / 四六级 / 考试周"},
                    "limit": {"type": "integer", "description": "最多列出几条,默认 8"},
                },
            },
            handler=school_calendar.campus_calendar,
            read_only=True,
        ),
        Tool(
            name="gpa_calc",
            description=(
                "按学分计算加权平均分与绩点(两种国内常见算法,公式随结果标明)。"
                "用户贴来一串成绩想算绩点/加权平均时,先把每门课解析成 "
                "{name, score, credit} 列表再调用;score 支持百分制数字或"
                "「优/良/中/及格」等级(工具按常见折算并注明)。"
                "回答时必须提示「以学校教务处的绩点算法为准」。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "courses": {
                        "type": "array",
                        "description": "课程成绩列表",
                        "items": {"type": "object", "properties": {
                            "name": {"type": "string", "description": "课程名"},
                            "score": {"description": "百分制分数,或 优/良/中/及格"},
                            "credit": {"description": "学分(大于 0)"},
                        }},
                    },
                },
                "required": ["courses"],
            },
            handler=gpa.gpa_calc,
            read_only=True,
        ),
        Tool(
            name="kb_suggest",
            description=(
                "把一条有长期价值的校园问答存成「知识库候选」(只写候选文件,"
                "维护者审核后才并入正式知识库,不会立即生效)。"
                "仅在同时满足时使用:①search_knowledge 没命中;②通过 web_search/browser"
                " 查到了可靠来源(官网/官方公众号优先);③用户同意存入。"
                "来源不可靠或用户没同意都不要调用;不要向用户宣称信息已进入正式知识库。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "同学的问题(口语原话或短语)"},
                    "answer": {"type": "string", "description": "核实过的答案要点(300 字内)"},
                    "source_url": {"type": "string",
                                   "description": "来源网址(必填,没有可靠来源就不要调用)"},
                    "source_title": {"type": "string", "description": "来源标题,如通知标题/文章题"},
                    "note": {"type": "string", "description": "补充说明,如适用校区/有效期"},
                    "reason": {"type": "string",
                               "description": "一句中文说明为什么要存这条(显示在权限确认框里)"},
                },
                "required": ["question", "answer", "source_url"],
            },
            handler=growth.kb_suggest,
        ),
        Tool(
            name="library_search",
            description=(
                "查询本校图书馆馆藏(本地离线全量书目库,133 万条,毫秒级返回)。"
                "用户问「图书馆有没有某本书 / 这本书在哪 / 索书号是多少 / 某作者有哪些书 / "
                "某个主题有什么书」时必须用它,不要再去访问图书馆网站让用户等。"
                "返回书名、作者、出版社、出版年、ISBN、索书号、中图法分类号、馆藏册数、"
                "在架数、馆藏地(书库/阅览室)、简介,并附「楼层提示」(中图法大类→楼层),"
                "可直接据此回答在哪一层,不必再调 library_guide。"
                "一次结果通常就够答:不要用同义词反复重查,最多补一次作者名/ISBN。"
                "kind=paper 只看纸本(有馆藏位置),kind=ebook 只看电子书,默认 all。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string",
                              "description": "查询词:书名 / 作者 / ISBN / 中图法分类号(如 I247.5)"},
                    "top_k": {"type": "integer", "description": "返回条数,默认 8,上限 30"},
                    "kind": {"type": "string", "enum": ["all", "paper", "ebook"],
                             "description": "all=全部(默认);paper=纸本(有馆藏地);ebook=电子书"},
                },
                "required": ["query"],
            },
            handler=library.library_search,
            trust="external",
            read_only=True,
        ),
        Tool(
            name="library_guide",
            description=(
                "图书馆导览模块(新生入馆教学,数据随技能打包,不依赖本地书目库)。"
                "讲「图书馆怎么用」:中图法大类→楼层/书库分布、馆舍空间与设施、"
                "入馆借还流程与官方入口、馆藏规模统计。"
                "用户问「XX 类的书在几楼 / 这个索书号在哪 / 图书馆几层有什么 / "
                "第一次怎么借书」时用它;查**具体某本书**用 library_search。"
                "开馆时间、可借册数/借期、座位预约没有官方来源,会如实提示以官方为准。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "topic": {"type": "string",
                              "enum": ["all", "floor", "space", "flow", "stats", "unverified"],
                              "description": "all=概览+完整楼层表(默认);floor=按中图法大类查楼层书库;"
                                             "space=馆舍空间;flow=入馆借还流程;stats=馆藏统计;"
                                             "unverified=列出没有官方来源的待核实项"},
                    "query": {"type": "string",
                              "description": "topic=floor 时用:中图法大类字母或类名,如 I / 文学 / TP312"},
                },
            },
            handler=library_guide.guide,
            trust="external",
            read_only=True,
        ),
        Tool(
            name="memory",
            description=(
                "长期记忆管理(跨会话)。项目级存工作区 memory/MEMORY.md,"
                "用户级(跨项目通用)存用户目录,用 scope=user 区分。"
                "action=save:用户提到偏好/项目约定/纠正/参考资料时记下来;"
                "action=list:按类别列出;action=search:按关键词检索;"
                "action=forget:让用户要求忘记某条记忆时删除;"
                "action=dream:整理去重(合并同类、删掉被后续推翻的,覆盖前自动备份)——"
                "用户说「整理记忆/记忆太多了」或发现条目明显重复时用它。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string",
                               "enum": ["save", "list", "search", "forget", "dream"],
                               "description": "save=记住;list=列出;search=检索;"
                                              "forget=忘记;dream=整理去重"},
                    "category": {"type": "string",
                                 "enum": ["user", "feedback", "project", "reference"],
                                 "description": "save/list 用:用户偏好/反馈纠正/项目约定/参考资料"},
                    "content": {"type": "string", "description": "save 用:要记住的内容(一句话)"},
                    "query": {"type": "string", "description": "search/forget 用:关键词或部分内容"},
                    "scope": {"type": "string", "enum": ["project", "user", "all"],
                              "description": "作用域:project=只在本项目生效(默认);"
                                             "user=跨项目通用(个人偏好/沟通习惯);"
                                             "dream 时传 all 表示两级都整理"},
                },
                "required": ["action"],
            },
            handler=memory.memory,
            read_only=False,
        ),
        Tool(
            name="reminder",
            description=(
                "待办/截止日期提醒(存本机 reminders.json,启动时学长会主动催)。"
                "用户说「提醒我 X 月 X 日交材料」「别忘了周五要报名」「我有哪些事没做完」时用:"
                "action=add 记一条(title 必填,due 给 2026-10-08 / 10-08 这类能确定的日期,"
                "说不准就不填——绝不猜日期);"
                "action=list 看清单;action=finish/delete 带 item_id。"
                "催的精度取决于你手上有确切信息到哪一层:只有日期 → 临期(3 天内)按天催;"
                "日期 + 钟点(at)都确凿 → 到点前 20 分钟催一次,错过了开机还能补一条;"
                "钟点说不准(用户只说「3点」没讲上午下午)就**别填 at**,按天催比猜错时间好。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["add", "list", "finish", "delete"],
                               "description": "add=记提醒;list=看清单;finish=标记完成;delete=删除"},
                    "title": {"type": "string", "description": "add 用:要记的事(如:交奖学金材料)"},
                    "due": {"type": "string",
                            "description": "add 用:截止日期(2026-10-08 / 2026/10/8 / 10-08;"
                                           "说不准就留空,不要猜)"},
                    "at": {"type": "string",
                           "description": "add 用:截止钟点(17:00 / 下午5点 / 中午12点半)。"
                                          "只在用户明确说过时间时才填;光「3点」这类歧义写法"
                                          "填了也不会生效,不如留空按天催"},
                    "note": {"type": "string", "description": "add 用:补充说明(可选)"},
                    "item_id": {"type": "string",
                                "description": "finish/delete 用:提醒的 id(如 r3,清单里能看到)"},
                    "reason": {"type": "string",
                               "description": "一句中文说明这次要做什么(显示在权限确认框里)"},
                },
                "required": ["action"],
            },
            handler=reminders.reminder,
        ),
        Tool(
            name="mood",
            description=(
                "情绪日记(存本机 mood.json,一天一条)。**这不是心理评估,也不是治疗**:"
                "只回读同学自己记的东西,绝不下诊断、不说「你这是抑郁症」、不建议吃什么药。\n"
                "action=log 记一条:note 一句话就够(「今天实验搞崩了,烦」);score 只在"
                "他自己给了 1~5 时才填,**不要替他打分**;day 可写「今天」「昨天」或 2026-09-22,"
                "认不出的日期不猜。同一天再记会覆盖前一条(回复里会说明覆盖了什么)。\n"
                "action=review 回看这一周(默认 7 天,可给 days);action=list 看清单;"
                "action=export 导出全部原文给他;action=delete 删掉某一天(day 指定);"
                "action=help 拿「学校心理健康教育中心在哪、怎么找」这段求助信息。\n"
                "两条硬规矩:①**不做连续打卡** —— 没有连续天数、没有断签,漏记不是问题,"
                "也不要催他记;②他写下自伤/活不下去这类话时,先接住人,再把 action=help "
                "那段**照原样**说给他 —— 里面的地址与号码出自学校心理中心自己的公众号原文,"
                "**照原样念、绝对不要改动任何一位,也绝对不要凭印象再补一个号码**,"
                "并告诉他此刻有危险就打 120/110 或去急诊。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string",
                               "enum": ["log", "review", "list", "export", "delete", "help"],
                               "description": "log=记一条;review=看这一周;list=清单;"
                                              "export=导出全文;delete=删某天;help=求助去处"},
                    "note": {"type": "string", "description": "log 用:今天怎么样,一句话"},
                    "score": {"type": "integer",
                              "description": "log 用:1~5,他自己给的分(别替他打)"},
                    "day": {"type": "string",
                            "description": "log/delete 用:哪天(今天/昨天/2026-09-22;"
                                           "认不出就不填,默认今天)"},
                    "days": {"type": "integer",
                             "description": "review/list 用:往回看几天(默认 7)"},
                    "reason": {"type": "string",
                               "description": "一句中文说明这次要做什么(显示在权限确认框里)"},
                },
                "required": ["action"],
            },
            handler=mood.mood,
        ),
        Tool(
            name="schedule",
            description=(
                "个人课表的登记/查看(存本机 schedule.json,启动时学长主动提示今天/明天课程)。"
                "配合 schedule-helper 技能:把同学贴来的课表解析成条目后,问清「第 1 周周一是几月几号」"
                "再 action=save 存下来(classes 每项 {name,day(1-7),periods,weeks(如 1-16 或 1-16(单周)),"
                "start_time(课表里有才填),location,teacher});之后问「今天有什么课」直接照登记回答。"
                "action=show 看某天课(when 传日期,默认今天);action=free 查空档"
                "(when 传日期看那天、传 week/留空看本周、传 next 看下下周 —— 照它算好的回答,别心算);"
                "action=risk 看哪些课之间要换教学楼(when 同 free,也认 今天/明天):"
                "它按课表地点 + 校园地图建筑索引算,只报「哪两栋、中间隔几节、填过作息表才给分钟数」,"
                "**不许替同学估「走一趟要几分钟」**(手绘地图没有比例尺);要说位置就 map_highlight_building "
                "把两栋一起框出来;action=times 看本机作息表填了没、action=save_times 把同学给的教务处作息表存下来"
                "(一次性,之后空档才会带钟点);action=diff 把 classes 与本机已存课表"
                "比对(不写文件,先让同学看清新增/变动/取消);action=undo 换回上一版课表;"
                "action=clear 清空。save 覆盖时会自动附上与上一版的变动清单,直接照着向同学念一遍。"
                "课表里没写钟点的课只报第几节,绝不编具体时间;没填作息表时空档与换楼同理只说第几节。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string",
                               "enum": ["save", "show", "free", "risk", "diff", "undo",
                                        "times", "save_times", "clear"],
                               "description": "save=存/覆盖课表;show=看某天;free=查空档;"
                                              "risk=查要换教学楼的课;diff=与已存课表比对(不写);"
                                              "undo=回滚到上一版;times=看本机作息表;"
                                              "save_times=存作息表;clear=清空"},
                    "term_start": {"type": "string",
                                   "description": "save 用:第 1 周周一的日期(2026-09-07),向同学问清,不要猜"},
                    "classes": {"type": "array",
                                "description": "save 用:课程条目列表,每项含 name/day/weeks,可选 periods/start_time/location/teacher",
                                "items": {"type": "object", "properties": {
                                    "name": {"type": "string"},
                                    "day": {"description": "1-7(周一=1),也认「周三」"},
                                    "weeks": {"type": "string", "description": "如 1-16 / 1-8,11-16 / 1-16(单周)"},
                                    "periods": {"type": "string", "description": "节次,如 1-2"},
                                    "start_time": {"type": "string", "description": "上课钟点(课表里有才填,如 08:00)"},
                                    "location": {"type": "string"},
                                    "teacher": {"type": "string"},
                                }}},
                    "when": {"type": "string",
                             "description": "show/free/risk 用:日期(2026-09-22)。free 与 risk"
                                            " 还接受 week(本周,默认)、next(下周);"
                                            "risk 另认「今天」「明天」"},
                    "periods": {"type": "array",
                                "description": "save_times 用:按第 1 节往后排的作息表,"
                                               "每项 {start,end}(如 {start:'08:00',end:'08:45'}),"
                                               "只录同学给的真实作息,不要替他编",
                                "items": {"type": "object", "properties": {
                                    "period": {"type": "integer"},
                                    "start": {"type": "string"},
                                    "end": {"type": "string"}}}},
                    "source": {"type": "string",
                               "description": "save_times 用:这份作息表的出处(如「教务处 2025-2026 校历」),没有就留空"},
                    "reason": {"type": "string",
                               "description": "一句中文说明这次要做什么(显示在权限确认框里)"},
                },
                "required": ["action"],
            },
            handler=schedule.schedule,
        ),
        Tool(
            name="desktop_notify",
            description=(
                "桌面提醒开关(Windows 右下角通知):课前 20 分钟、今日简报、临期待办、"
                "官网新通知这些主动提醒,以前只在聊天窗口顶部那条提示栏里,窗口一最小化"
                "就等于没提醒;开了之后同时弹到桌面。用户说「提醒直接弹桌面」「别弹通知」"
                "「提醒做成两种:栏里一条 + 桌面一条」「看不到通知」时用:action=status 看"
                "现状(哪些类别会弹、托盘在不在),action=on/off 开或关(kind 可只动一类:"
                "briefing/class/schedule/reminders/calendar/notices/update),action=test "
                "当场发一条测试通知(**排查「看不到通知」先走这条**:回了「已发出」"
                "就说明是系统挡的,要用户去 设置→系统→通知 里放行本应用)。"
                "另外管开机自启:action=startup_on / startup_off 开或关「开机自动启动学长」"
                "(用户说「开机后忘了打开学长所以没收到提醒」「让它开机就自己起来」时用;"
                "实现是在当前用户的「启动」文件夹放一个快捷方式,不写注册表,用户能自己"
                "看见并删掉;建完会读回指向核对,没核对上不会说已开启)。"
                "**用户报「提醒没发」「同一条弹了好几遍」时用 action=log**:读看门狗的运行"
                "日志(数据目录 watchdog.log),里面每一行是「哪一类、推了还是被哪道闸门拦下"
                "了、拦下或失败的原因」,只有类别与去重键、不含通知正文;拿着它就能说清"
                "是「今天已推过」「今日简报已覆盖」「没内容」还是「两路都没送到」。"
                "只有桌面版弹得出去,命令行/网页版会如实说明弹不了,绝不假装已开启。"
                "两条默认行为要说清:窗口正在前台时桌面不重复弹(聊天栏那条已经看得见),"
                "同一条内容 ~90 秒内只响一次(免得重复推的提醒刷屏)。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string",
                               "enum": ["status", "on", "off", "test",
                                        "startup_on", "startup_off", "log"],
                               "description": "status=看状态(默认);on/off=开关;test=发一条测试;"
                                              "startup_on/startup_off=开机自启开/关;"
                                              "log=读看门狗运行日志(查「没提醒」或「重复弹」)"},
                    "kind": {"type": "string",
                             "description": "可选:只开关这一类提醒(briefing 今日简报 / class 课前 / "
                                            "schedule 课程 / reminders 待办 / calendar 校历 / "
                                            "notices 校园通知 / update 新版本)"},
                    "reason": {"type": "string",
                               "description": "一句中文说明这次要做什么(显示在权限确认框里)"},
                },
            },
            handler=tray.desktop_notify,
        ),
        Tool(
            name="get_weather",
            description=(
                "查学校所在地(肇庆鼎湖)的实况与未来几天天气预报(Open-Meteo,免费数据源)。"
                "用户问「今天/这周末下不下雨、要不要带伞、降温吗」时用;days=今天起往后几天(0-3)。"
                "查不到就如实说查不到,绝不编数字;首页已有天气 chip,日常一句话不必总调本工具。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "days": {"type": "integer",
                             "description": "连同今天往后报几天(0-3,默认 1)"},
                },
            },
            handler=weather.get_weather,
            trust="external",
            read_only=True,
        ),
        # ---------------- 校园地图(map_*)----------------
        # 数据是线下交付包(手绘地图 + 36 张单栋标注图)的索引:是「位置图」,
        # 没有路网拓扑,所以只有"在哪、旁边是什么",没有"怎么走、几分钟"。
        # 这几个工具只影响界面上的地图显示,不碰工作区文件,因此标 read_only=True:
        # 否则"框一栋楼"也会弹权限确认框,用户每次问地点都要点两次允许。
        Tool(
            name="map_find_place",
            description=(
                "按用户的口语说法在地图上定位一处地点:命中就把该建筑框出来并显示它的单栋标注图。"
                "用户问「XX 在哪 / 怎么走 / 带我去 XX / XX 远不远」时用它——这是回答位置类问题的第一步。"
                "支持学生俗称(旧图=图书馆、综合馆=体育综合馆、E栋、s5)。"
                "注意:「A栋~E栋」在地图上是两栋楼(学思苑X 与 集贤苑X座),工具会返回 ambiguous,"
                "此时必须把候选摆给用户确认,不要自己挑一个。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "用户说的地点,如 图书馆 / E栋 / S5 / 正校门"},
                    "color": {"type": "string", "enum": list(campus.COLORS),
                              "description": "标注框颜色,默认 orange"},
                },
                "required": ["query"],
            },
            handler=campus.map_find_place,
            read_only=True,
        ),
        Tool(
            name="map_show_list",
            description=(
                "列出地图上已标注的全部地点,并在地图面板打开列表。"
                "回答「学校有哪些楼 / 有哪些食堂 / 宿舍都有哪几栋」这类问题,或需要确认准确名称时用它。"
                "可按 category(学生宿舍/教学楼/公共建筑/体育场馆/食堂/校门/行政办公)或 query 关键词筛选。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "category": {"type": "string",
                                 "description": "按类别筛选,如 学生宿舍 / 教学楼 / 食堂"},
                    "query": {"type": "string", "description": "按名称关键词筛选,如 学思苑"},
                },
            },
            handler=campus.map_show_list,
            read_only=True,
        ),
        Tool(
            name="map_highlight_building",
            description=(
                "一次在地图上框出多处地点,适合批量请求(如「标出所有食堂」「学思苑都有哪几栋」)。"
                "单个地点、或用户用俗称提问时请优先用 map_find_place(它会处理歧义并出图)。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "names": {"type": "array", "items": {"type": "string"},
                              "description": "要框出的地点名,如 [\"桃园饭堂\",\"桃蹊饭堂\"]"},
                    "color": {"type": "string", "enum": list(campus.COLORS),
                              "description": "标注框颜色,默认 orange"},
                },
                "required": ["names"],
            },
            handler=campus.map_highlight_building,
            read_only=True,
        ),
        Tool(
            name="map_drop_pin",
            description=(
                "在地图上钉一个图钉:给地点名(用索引里的中心点)或直接给底图坐标。"
                "适合标集合点、活动地点、约见位置这类「就这一个点」的场景。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "地点名(优先用这个,如 图书馆);也可改传 x/y"},
                    "x": {"type": "number", "description": "底图 x 坐标(0-2559),仅在不用 name 时传"},
                    "y": {"type": "number", "description": "底图 y 坐标(0-1279),仅在不用 name 时传"},
                    "label": {"type": "string", "description": "图钉上的文字,默认用地点名"},
                    "color": {"type": "string", "enum": list(campus.COLORS),
                              "description": "图钉颜色,默认 red"},
                    "note": {"type": "string", "description": "可选:图钉下方的补充说明"},
                },
            },
            handler=campus.map_drop_pin,
            read_only=True,
        ),
        Tool(
            name="map_clear",
            description="清除地图上已有的标记。开始讲一个新地点前,若地图上还留着上一次的标记,先用它清一下。",
            parameters={
                "type": "object",
                "properties": {
                    "scope": {"type": "string", "enum": ["all", "highlight", "pin", "list"],
                              "description": "清除范围,默认 all(全部)"},
                },
            },
            handler=campus.map_clear,
            read_only=True,
        ),
    ]
