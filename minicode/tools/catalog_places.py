"""馆藏与地图类工具(v0.9.39 #84 从 registry.py 搬出来)。

离线馆藏库查书与楼层指引、手绘校园地图那五个动作。

搬的是**原文**:每个 `Tool(...)` 的字段与描述一字未改,只是换了文件。
注册表里这些工具的**先后顺序**由 `registry.build_default_registry()` 决定,
并由 `tests/test_registry_split.py` 逐字钉住 —— 别在这里自己排。
"""
from __future__ import annotations

from minicode import campus, library, library_guide
from minicode.tools.base import Tool


def library_tools() -> list[Tool]:
    """馆藏查书与楼层指引。"""
    return [
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
    ]


def map_tools() -> list[Tool]:
    """手绘地图那五个动作。"""
    return [
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
