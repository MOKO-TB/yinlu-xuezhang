"""提醒一族工具(v0.9.98 从 catalog_campus.py 搬出来 —— 那份 399 行,离「单份
目录 <400」的热点闸只剩 1 行,先拆掉这道挡路的墙,后面加校园类工具才有地方)。

待办提醒、情绪日记、课表(含走堂)、桌面通知、天气、官网通知、.ics 导出。

搬的是**原文**:每个 `Tool(...)` 的字段与描述一字未改,只是换了文件。
注册表里这些工具的**先后顺序**由 `registry.build_default_registry()` 决定,
并由 `tests/test_registry_split.py` 逐字钉住 —— 别在这里自己排。
"""
from __future__ import annotations

from minicode import ics_export, mood, notices, reminders, schedule, tray, weather
from minicode.tools.base import Tool


def remind_tools() -> list[Tool]:
    """待办 / 心情 / 课表 / 桌面通知 / 天气(提醒这一族)。"""
    return [
        Tool(
            name="reminder",
            description=(
                "待办/截止日期提醒(存本机 reminders.json,启动时学长会主动催)。"
                "用户说「提醒我 X 月 X 日交材料」「别忘了周五要报名」「我有哪些事没做完」时用:"
                "action=add 记一条(title 必填,due 给 2026-10-08 / 10-08 这类能确定的日期,"
                "说不准就不填——绝不猜日期);"
                "循环的事(「每周三交周记」「每3天背单词」)再给 repeat=「每周」或「每N天」,"
                "并尽量配上一个能确定的日期作锚点;完成后自动排下一次,停掉用 delete;"
                "action=list 看清单;action=finish/delete 带 item_id。"
                "催的精度取决于你手上有确切信息到哪一层:只有日期 → 临期(3 天内)按天催;"
                "日期 + 钟点(at)都确凿 → 到点前 20 分钟催一次,错过了开机还能补一条;"
                "钟点说不准(用户只说「3点」没讲上午下午)就**别填 at**,按天催比猜错时间好;"
                "repeat 认不出或没有锚点日期时,会按一次性提醒记下并说明,不会猜一个规则。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["add", "list", "finish", "delete"],
                               "description": "add=记提醒;list=看清单;finish=标记完成"
                                              "(循环待办是完成这一次、自动排下一次);delete=删除"},
                    "title": {"type": "string", "description": "add 用:要记的事(如:交奖学金材料)"},
                    "due": {"type": "string",
                            "description": "add 用:截止日期(2026-10-08 / 2026/10/8 / 10-08;"
                                           "说不准就留空,不要猜;循环提醒要尽量给确凿日期作锚点)"},
                    "at": {"type": "string",
                           "description": "add 用:截止钟点(17:00 / 下午5点 / 中午12点半)。"
                                          "只在用户明确说过时间时才填;光「3点」这类歧义写法"
                                          "填了也不会生效,不如留空按天催"},
                    "repeat": {"type": "string",
                               "description": "add 用:循环规则,只认「每周」和「每N天」(如「每3天」);"
                                              "「每月」这类认不准的不要猜,留空按一次性提醒记"},
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
                "briefing/class/schedule/reminders/calendar/notices/update/weather/review/"
                "gaps/recovery),action=test "
                "当场发一条测试通知(**排查「看不到通知」先走这条**:回了「已发出」"
                "就说明是系统挡的,要用户去 设置→系统→通知 里放行本应用)。"
                "另外管开机自启:action=startup_on / startup_off 开或关「开机自动启动学长」"
                "(用户说「开机后忘了打开学长所以没收到提醒」「让它开机就自己起来」时用;"
                "实现是在当前用户的「启动」文件夹放一个快捷方式,不写注册表,用户能自己"
                "看见并删掉;建完会读回指向核对,没核对上不会说已开启)。"
                "**用户报「提醒没发」「同一条弹了好几遍」时用 action=log**:读看门狗的运行"
                "日志。**action=jobs 回答「定时任务跑没跑」**:心情周复盘/缺口周报这些"
                "周期任务各自的节奏、上次何时跑、结论是什么、现在到没到点 —— "
                "用户问「周报怎么没来」「这周任务跑没跑」时用它。",
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
                               "enum": list(tray.NOTIFY_ACTIONS),   # 一处真相,不手抄第二份
                               "description": "status=看状态(默认);on/off=开关;test=发一条测试;"
                                              "startup_on/startup_off=开机自启开/关;"
                                              "log=读看门狗运行日志(查「没提醒」或「重复弹」);"
                                              "jobs=定时任务跑没跑(查周报/缺口周报);"
                                              "tray_log=读托盘与通知诊断日志(查「通知没弹」「点 X 直接退」"
                                              "「开机自启没生效」;无正文,有本机路径);"
                                              "gui_log=读界面层警告(查「对话框右键没有粘贴」"
                                              "「Ctrl+C/V 没用」——那一段原生菜单开关的成败就在这份里)"},
                    "kind": {"type": "string",
                             "description": "可选:只开关这一类提醒(briefing 今日简报 / class 课前 / "
                                            "schedule 课程 / reminders 待办 / calendar 校历 / "
                                            "notices 校园通知 / update 新版本 / weather 天气提醒 / "
                                            "review 心情周复盘 / gaps 知识库缺口周报 / "
                                            "recovery 模型恢复提醒)"},
                    "reason": {"type": "string",
                               "description": "一句中文说明这次要做什么(显示在权限确认框里)"},
                },
            },
            handler=tray.desktop_notify,
        ),
        Tool(
            name="get_weather",
            description=(
                "查天气实况与未来几天预报(Open-Meteo,免费数据源),默认是学校所在地(肇庆鼎湖)。"
                "用户问「今天/这周末下不下雨、要不要带伞、降温吗」时用;days=今天起往后几天(0-3)。"
                "用户问的是另一个地方(家/另一个校区)时给 place,名字须在已配置地点里,"
                "报不出可用地点就是没配置,不要猜经纬度。"
                "查不到就如实说查不到,绝不编数字;首页已有天气 chip,日常一句话不必总调本工具。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "days": {"type": "integer",
                             "description": "连同今天往后报几天(0-3,默认 1)"},
                    "place": {"type": "string",
                              "description": "可选:地点名(需已在 weather.json 里配置,"
                                             "报错时会列出可用的);不填=默认地点(学校)"},
                },
            },
            handler=weather.get_weather,
            trust="external",
            read_only=True,
        ),
        Tool(
            name="campus_notices",
            description=(
                "现拉学校官网通知公告栏目的最新标题与链接(教务处/学生处等栏目,博达站群静态页,"
                "免登录)。用户问「最近/最新/近日 学校有什么事 / 大事件 / 通知 / 活动」时"
                "**必须先调本工具**,以现拉结果为准 —— 本地知识库是爬虫旧语料,回答不了时效问题。"
                "返回的是标题+链接,不是正文:要讲某件事的详情,再用 web_crawl 抓那条链接,"
                "抓不到就如实说,绝不按标题编内容;拉不到(断网/改版)就如实说这次查不到,"
                "工具会自动退回看门狗上次抓到的快照并注明。action=cached 只读快照不联网。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["latest", "cached"],
                               "description": "latest=现拉官网(默认);cached=只读看门狗快照(不联网)"},
                    "limit": {"type": "integer", "description": "最多列几条,默认 10,上限 15"},
                },
            },
            handler=notices.campus_notices,
            trust="external",
            read_only=True,
        ),
        Tool(
            name="export_ics",
            description=(
                "把课表、待办截止、校历导出成一个 .ics 日历文件(工作区「引路学长日历.ics」),"
                "同学双击导进 Windows 日历或发到手机导入 —— 到点响的是系统,学长没在跑也提醒得到"
                "(v0.9.42 方案 A5 的兜底半件)。三条诚实边界:没填作息表的课按**全天事件**导"
                "(那天有课,但不编一个钟点,回话里点名哪几门、怎么补);校历里按往年推的投影日子"
                "**不导**(写进系统日历就成了每年照着过的事实);没有日期的待办跳过并报数。"
                "用户说「把课表导进手机日历」「导出个日历文件」时用。重复导出按 UID 更新不堆重。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "reason": {"type": "string",
                               "description": "一句中文说明这次要做什么(显示在权限确认框里)"},
                },
            },
            handler=ics_export.export_ics_tool,
        ),
    ]
