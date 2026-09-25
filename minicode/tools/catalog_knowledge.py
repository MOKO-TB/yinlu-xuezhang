"""知识、技能与计划类工具(v0.9.39 #84 从 registry.py 搬出来)。

技能按需读取、计划的建与改、知识库检索与候选登记、长期记忆。

搬的是**原文**:每个 `Tool(...)` 的字段与描述一字未改,只是换了文件。
注册表里这些工具的**先后顺序**由 `registry.build_default_registry()` 决定,
并由 `tests/test_registry_split.py` 逐字钉住 —— 别在这里自己排。
"""
from __future__ import annotations

from minicode import growth, kb, memory, planning, session, skills
from minicode.tools.base import Tool


def skills_plan_search() -> list[Tool]:
    """技能 / 计划 / 知识库检索三件(它们在注册表里是连着的)。"""
    return [
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
            description="检索本地知识库(校园通知、奖助学金、竞赛、证书、考研就业、心理调适等资料),返回带来源网址与可信度的条目,并附「检索口径」说明这次靠什么命中的。回答相关问题时必须先用本工具,引用时给出网址供用户核实。用户问细节时把 snippet_chars 调大,直接基于原文回答,避免再去爬官网让用户等。",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "查询词,如:国家励志奖学金 / 蓝桥杯 / 考研择校"},
                    "top_k": {"type": "integer", "description": "返回条数,默认 3"},
                    "category": {"type": "string", "description": "按分类过滤,如:奖助学金 / 学科竞赛 / 考研"},
                    "snippet_chars": {"type": "integer",
                                      "description": "每条返回的正文长度,默认 160(速览);问评定条件/流程/时间等细节时用 600-1200"},
                    "source": {"type": "string",
                               "description": "按来源过滤(渠道名或网址里的子串),如 微信公众号 / gzasc.edu.cn;默认不限"},
                    "since": {"type": "string",
                              "description": "只看该日期之后发布的条目,格式 2026-01-01;问「今年/最近」时用,不填即不限时间"},
                    "semantic": {"type": "boolean",
                                 "description": "要不要用向量(语义)档。默认留空=自动:向量索引已建好才用;没建好结果里的「检索口径」会照实告诉你"},
                    "build_index": {"type": "boolean",
                                    "description": "显式建/补向量索引——要给几百条真发 embedding 请求,既慢又花使用者的额度,只有用户明确要求时才传 true"},
                },
                "required": ["query"],
            },
            handler=kb.search_knowledge,
            trust="external",
            read_only=True,
        ),
    ]


def history_tool() -> list[Tool]:
    """跨会话历史检索。单独一组:它挨着 `search_knowledge` 装配,但不属于上面那三件
    的历史顺序 —— 把它们四个并成一个函数会改动 `registry.py` 里已钉住的装配序列。"""
    return [
        Tool(
            name="search_history",
            description=(
                "在**你自己本机**的历史会话留档里搜关键词,接住「上次那个报错我们后来怎么解决的」"
                "「之前聊的择校结论是什么」这类跨会话的问题。用户提到「上次 / 之前 / 我们说过 / "
                "你还记得」时**先调本工具再回答**:知识库(search_knowledge)装的是校园公开资料,"
                "长期记忆(memory)只装着被显式记下的那几句,两者都不含整段对话。"
                "返回按命中次数排序的原文片段,附会话时间与工作区名;没查到就照实说没查到,"
                "绝不据此编内容。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string",
                              "description": "关键词,取用户原话里最特别的词,如:报错 / 蓝桥杯 / 择校"},
                    "limit": {"type": "integer",
                              "description": "返回几条命中,默认 3,最多 5"},
                },
                "required": ["query"],
            },
            # trust=local 的依据:search_logs 只取 role 为 user/assistant 的行,
            # 工具往返(外部网页原文)根本不进结果 —— 剩下的都是用户说的话与助手的回话。
            handler=session.search_history,
            trust="local",
            read_only=True,
        ),
    ]


def kb_suggest_tool() -> list[Tool]:
    """知识库候选登记(原文件里它夹在绩点与馆藏之间,搬位置会改顺序,所以单独一组)。"""
    return [
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
    ]


def memory_tool() -> list[Tool]:
    """长期记忆(同上:原位夹在馆藏与提醒之间)。"""
    return [
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
    ]
