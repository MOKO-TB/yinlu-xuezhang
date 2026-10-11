"""内置工具注册表:工具的(v0.9.98 定下的)**装配顺序**在这里定,内容按领域拆在 catalog_*.py。

v0.9.39 #84 之前这里是 987 行、40 个 `Tool(...)` 挤在一个函数里 —— 加一个工具
要在一片同构的字面量里找位置,而这个文件几乎每一轮都要碰。拆完每份目录只装
一个领域,这里只剩"按什么顺序拼起来"。

**顺序不许随手改**:模型每轮拿到的 tools 数组就是这个顺序,`tests/test_registry_split.py`
把每一个名字逐个钉住。看着别扭的分组(知识库那三件被 OCR/图表隔开、`kb_suggest`
夹在绩点与馆藏之间)是**原文件的历史顺序**,搬动它就是改行为面;真要重排请单独
一轮、说清为什么,并同步改那条测试。
"""
from __future__ import annotations

from minicode.tools import (catalog_files, catalog_bg, catalog_diag, catalog_web,
                            catalog_knowledge, catalog_media, catalog_campus,
                            catalog_remind, catalog_places, catalog_sub)
from minicode.tools import concurrency
from minicode.tools.base import Tool


def build_default_registry() -> list[Tool]:
    """内置工具清单(子代理的 `task` 由 `app_setup` 装配时再接一个)。

    这里**不写条数**:同一个数在三处(HANDOFF §1、README、这句 docstring)迟早会漂,
    而漂掉的那处正是"看着一切正常、装好的包里少一只工具"的来源。当前条数由
    `tests/test_registry_split.py` 拿实测钉住,文档数以 `check_docs.py --collect` 对账。
    """
    tools = [
        *catalog_files.tools(),
        # v0.9.146:后台命令三只(bash_status / bash_kill / bash_input)拆到独立目录,
        # 但**必须插在这里** —— 它们要挨着 `bash`(派活),而 `py_script` 是另一族。
        # 顺序即行为面,见文件 docstring。
        *catalog_bg.tools(),
        *catalog_files.script_tools(),
        # v0.9.147:诊断(按扩展名跑检查器),挨着 `repo_outline` —— 同属"看代码"一族,
        # 而且模型改完一处代码后紧接着想问的正是"这下对不对"。
        *catalog_diag.tools(),
        *catalog_files.perms_tools(),
        # v0.9.116 L3:钩子配方两只,挨着权限那组(都是"用户点头才改配置"的一族)
        *catalog_files.hooks_tools(),
        # v0.9.98:git 工作副本(隔离工作区 W1),挨着项目权限那组
        *catalog_files.worktree_tools(),
        *catalog_web.tools(),
        *catalog_web.mcp_admin_tool(),
        *catalog_knowledge.skills_plan_search(),
        # v0.9.98:运行轨迹导出,挨着历史检索(同属会话记录一族)
        *catalog_knowledge.trace_tool(),
        *catalog_knowledge.history_tool(),
        *catalog_media.tools(),
        *catalog_campus.calendar_gpa(),
        *catalog_knowledge.kb_suggest_tool(),
        *catalog_places.library_tools(),
        *catalog_knowledge.memory_tool(),
        *catalog_remind.remind_tools(),
        *catalog_places.map_tools(),
        # v0.9.154 I:子代理 → 主会话留话(只读;只有子代理那边有对象可转达)
        *catalog_sub.tools(),
    ]
    concurrency.mark_concurrent(tools)   # 资格表在 tools/concurrency.py
    return tools
