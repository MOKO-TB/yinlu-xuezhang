"""网页与浏览器类工具(v0.9.39 #84 从 registry.py 搬出来)。

抓网页、搜网页,以及 CDP 直连真实浏览器那一组(开页面 / 取数 / 交互 / 截图 / 关)。

搬的是**原文**:每个 `Tool(...)` 的字段与描述一字未改,只是换了文件。
注册表里这些工具的**先后顺序**由 `registry.build_default_registry()` 决定,
并由 `tests/test_registry_split.py` 逐字钉住 —— 别在这里自己排。
"""
from __future__ import annotations

from minicode import mcp as mcp_mod
from minicode import site_parse
from minicode.tools import crawl
from minicode.tools import browser as browser_tools
from minicode.tools.base import Tool


def tools() -> list[Tool]:
    """网页与浏览器类工具。"""
    return [
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
            description="关闭标签页。不带 target = 关当前页面(这一轮的页面看完了就顺手关掉,别让浏览器堆一屏标签);target 传 browser_status 清单里的编号 = 关那一个(可管别的会话/子代理开的页;用户自己开着的页只会脱手不会被关)。",
            parameters={
                "type": "object",
                "properties": {
                    "target": {"type": "string",
                               "description": "可选:browser_status 清单里的标签页编号(如 browser:14028);不填=关当前页面"},
                    "reason": {"type": "string", "description": "可选:一句中文说明"},
                },
            },
            handler=browser_tools.browser_close,
            trust="external",
        ),
        Tool(
            name="browser_status",
            description="检查浏览器能力是否可用(能不能连上浏览器、走哪种方式),并列出现在开着哪些标签页(标题/网址/归属,带编号)。browser_open 报错时用它自查;用户问「浏览器开了什么」也用它。",
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
    ]


def mcp_admin_tool() -> list[Tool]:
    """MCP 服务器自助管理(v0.9.61;v0.9.70 加预设模板库):list/list_presets/add/remove/enable/disable。"""
    return [
        Tool(
            name="mcp",
            description=(
                "管理 MCP 外接服务器(对话里自助,不用手编 config.json):"
                "action=list_presets 看预设模板(everything 试水 / 分步思考 / "
                "filesystem 本地文件夹 / time / fetch,每条写清本机需要什么);"
                "action=list 看已配置的与当前挂着的工具;action=add 加一个 —— "
                "**推荐用预设**:raw 写 {\"preset\": \"<id>\"},要参数的预设"
                "(filesystem)再加 path,如 {\"preset\": \"filesystem\", "
                "\"path\": \"D:/课件\"};也可以手写 raw 配置(name 必填;本地进程"
                "给 command+args,远程给 transport=http/sse 与 url,鉴权 headers "
                "写 ${环境变量} 占位)。**加之前会真连一次探测,连不上就不写入**;"
                "本机没装 npx/uvx 时探测会失败并如实说缺什么,不会假装加上了。"
                "remove/enable/disable 按 name 操作。改动立即热重载(现有会话"
                "下一轮可用)。注意:这是管理动作,不是调用 MCP 工具本身 —— "
                "外接工具以 mcp_ 开头,直接调它们即可。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string",
                               "enum": ["list", "list_presets", "add",
                                        "remove", "enable", "disable"],
                               "description": "list=看清单(默认);list_presets=看预设模板;"
                                              "add=加(先探测后写入);remove/enable/disable 按 name 操作"},
                    "name": {"type": "string",
                             "description": "remove/enable/disable 用:服务器标识(清单里能看到)"},
                    "raw": {"type": "object",
                            "description": "add 用:{\"preset\": \"<id>\", \"path\": \"占位符参数"
                                           "(filesystem 要的文件夹)\", \"name\": \"可选的自定义标识\"};"
                                           "或完整手写配置:name(必填)、command/args(stdio 本地进程)"
                                           "或 transport+url(http/sse 远程)、env/headers(值写 "
                                           "${环境变量} 占位)、read_only/trusted(可信的自建服务器才写 "
                                           "true)。**用预设时命令与安全开关一律以模板为准,raw 改不动**"},
                    "reason": {"type": "string",
                               "description": "一句中文说明这次要做什么(显示在权限确认框里)"},
                },
            },
            handler=mcp_mod.admin_tool,
        ),
    ]
