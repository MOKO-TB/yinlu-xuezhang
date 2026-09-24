"""内置工具注册表:40 个工具的**装配顺序**在这里定,内容按领域拆在 catalog_*.py。

v0.9.39 #84 之前这里是 987 行、40 个 `Tool(...)` 挤在一个函数里 —— 加一个工具
要在一片同构的字面量里找位置,而这个文件几乎每一轮都要碰。拆完每份目录只装
一个领域,这里只剩"按什么顺序拼起来"。

**顺序不许随手改**:模型每轮拿到的 tools 数组就是这个顺序,`tests/test_registry_split.py`
把 40 个名字逐个钉住。看着别扭的分组(知识库那三件被 OCR/图表隔开、`kb_suggest`
夹在绩点与馆藏之间)是**原文件的历史顺序**,搬动它就是改行为面;真要重排请单独
一轮、说清为什么,并同步改那条测试。
"""
from __future__ import annotations

from minicode.tools import (
    catalog_files,
    catalog_web,
    catalog_knowledge,
    catalog_media,
    catalog_campus,
    catalog_places,
)
from minicode.tools.base import Tool


def build_default_registry() -> list[Tool]:
    """40 个内置工具(子代理的 `task` 由 `app_setup` 装配时再接一个)。"""
    return [
        *catalog_files.tools(),
        *catalog_web.tools(),
        *catalog_knowledge.skills_plan_search(),
        *catalog_media.tools(),
        *catalog_campus.calendar_gpa(),
        *catalog_knowledge.kb_suggest_tool(),
        *catalog_places.library_tools(),
        *catalog_knowledge.memory_tool(),
        *catalog_campus.remind_tools(),
        *catalog_places.map_tools(),
    ]
