"""校历与成绩类工具(v0.9.39 #84 从 registry.py 搬出来;v0.9.98 提醒一族
搬到 catalog_remind.py —— 本份曾长到 399 行,离「单份目录 <400」的热点闸
只剩 1 行,先拆掉它,后面加校园类工具才有地方)。

校历(查 + 补录)、绩点、成绩存档。

搬的是**原文**:每个 `Tool(...)` 的字段与描述一字未改,只是换了文件。
注册表里这些工具的**先后顺序**由 `registry.build_default_registry()` 决定,
并由 `tests/test_registry_split.py` 逐字钉住 —— 别在这里自己排。
"""
from __future__ import annotations

from minicode import calendar_edit, gpa, scores
from minicode import calendar as school_calendar
from minicode.tools.base import Tool


def calendar_gpa() -> list[Tool]:
    """校历与绩点(原文件里它们在 OCR/图表那三件之前)。"""
    return [
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
            name="calendar_edit",
            description=(
                "校历维护助手(补录 + 删除 + 修订):把用户贴来的教务处通知/校历文字里"
                "抽出的**日期候选**校验后入库 kb/school-calendar.json。全部两步走:"
                "action=preview 系列生成核对单(每条带通知原文引用与可信度,删除/修订"
                "列条目全文),完整念给用户、得到明确同意,action=commit 系列才写文件。"
                "三条硬边界:entries 每条必须带 quote——通知原文里支撑这个日期的那一句,"
                "没有引用的日期就是编的;改写前自动备份到 kb/history(备份写不进去就"
                "不动数据);kind 写错、end 早于 start、改后撞名直接拒绝不静默。"
                "kind 用 holiday/exam/term/deadline/event;可信度按来源定,拿不准就"
                "B-待核实。入库后考试倒计时/考前提醒等既有机制自动生效。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string",
                               "enum": ["preview", "commit", "remove_preview",
                                        "remove_commit", "revise_preview",
                                        "revise_commit"],
                               "description": "preview/commit=新增(先核对单后入库);"
                                              "remove_preview/remove_commit=删除(核对单列条目全文);"
                                              "revise_preview/revise_commit=修订(一次一条,必须给 reason)"},
                    "entries": {"type": "array",
                                "description": "新增用:候选条目数组,每条:{name(必填), start(ISO 日期,必填),"
                                               "quote(通知原文引用,必填), end(可选), kind(可选),"
                                               "source(通知网址或「用户提供」), note(可选),"
                                               "credibility(可选,默认按来源定)}"},
                    "targets": {"type": "array",
                                "description": "删除/修订用:选择器数组,每条:{id: 条目 id}(核对单里能看到)"
                                               "或 {name, start}(名字+开始日期);删错一条真日历代价很大,"
                                               "选择器必须来自核对单,不要凭记忆拼"},
                    "patch": {"type": "object",
                              "description": "修订用:要改的字段(start/end/kind/name/note/quote/"
                                             "source/credibility 任选若干)+ reason(必填,一句为什么改;"
                                             "日期来自新通知时把那句话放进 quote)"},
                    "reason": {"type": "string",
                               "description": "一句中文说明这次要做什么(显示在权限确认框里)"},
                },
            },
            handler=calendar_edit.calendar_edit_tool,
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
            name="scores",
            description=(
                "成绩存档(本机 scores.json,从教务导入的正式成绩)。用户问「我绩点多少」"
                "「加权平均多少」「哪门课拖了绩点」时:action=gpa 直接按存档算(两种常见"
                "算法 + 「以学校教务处绩点算法为准」口径),不用再让用户贴一遍成绩;"
                "action=view 看存档明细;action=trend 按学期算加权平均分并画趋势图"
                "(≥2 学期才有趋势,单学期如实说);action=clear 清空(要用户明确说清才做)。"
                "导入成绩走「📅 课表」面板的「从教务导入成绩」三步向导 —— "
                "**工具不导入**:登录必须由用户本人在弹出的浏览器里完成,工具做不到也不该做。"
                "存档里没有的成绩不要编;不及格/缺考的折算按学校规定,如实说算不准。(本工具刻意非只读:action=clear 会删整份存档,每一次都要过确认框。)"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["view", "gpa", "trend", "clear"],
                               "description": "view=看存档(默认);gpa=按存档算绩点;"
                                              "trend=按学期加权平均分(≥2 学期画折线图);clear=清空存档"},
                    "reason": {"type": "string",
                               "description": "一句中文说明这次要做什么(显示在权限确认框里)"},
                },
            },
            handler=scores.scores_tool,
            # 刻意非只读(与 mood 同一条理由):action=clear 会删掉整份成绩存档,
            # 每一次落盘/删档都该在确认框里看得见,不能因为 view/gpa 是读就整棵放行。
        ),
    ]
