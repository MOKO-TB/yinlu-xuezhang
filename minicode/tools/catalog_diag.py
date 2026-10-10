"""诊断类工具(v0.9.147):按文件类型跑一次检查器,把结论回灌给模型。

为什么单开一份目录,而不是塞进 `catalog_files.py`:后者刚在 v0.9.146 撞过
「每份目录 < 400 行」那道闸(见 `catalog_bg.py` 的开头),而这一只的描述要写清
"哪些语言能检、哪些检查器这台机器上没有、粒度是文件还是包还是整个 crate",
本身就够长。按**领域**拆:诊断与"看代码"同族,所以它排在 `repo_outline` 旁边。

注册表里这些工具的**先后顺序**由 `registry.build_default_registry()` 决定,
并由 `tests/test_registry_split.py` 逐字钉住 —— 别在这里自己排。
"""
from __future__ import annotations

from minicode import diagnostics
from minicode.tools.base import Tool


def tools() -> list[Tool]:
    """诊断类工具。"""
    return [
        Tool(
            name="diagnostics",
            description=(
                "改完代码后按文件类型跑一次检查器,把报出的问题原文拿回来。"
                "认得这些类型:`.py` → ruff(本机装了才跑,只报这一个文件);"
                "`.ts`/`.tsx` → 工作区自己的 tsc(报的是**整个项目**的类型图);"
                "`.js` → `node --check`(只语法);`.go` → `go vet`"
                "(粒度是**包**);`.rs` → `cargo check`(粒度是整个 crate,"
                "**会执行工作区代码**如 build.rs)。"
                "**它自己会跑,不必你每次都调** —— `write_file`/`edit_file` 落盘后"
                "如果这个类型有检查器,结论会自动附在那一步的结果后面。"
                "显式调它的用途是:改完一阵子想复查、或想先看看这台机器上有哪些"
                "检查器可用(`action=list`)。"
                "**三条必须如实转述的边界**:①检查器不存在(打包版里没有 ruff、"
                "工作区没装 tsc)或超时,回话会明说「这次没做检查」—— "
                "**那不是「检查过了没问题」**;②没有这个文件类型的检查器时同样明说,"
                "不要自己脑补一个结论;③它**不修代码**(没有 `--fix`),"
                "也**不联网装检查器**。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["check", "list"],
                               "description": "check = 检查一个文件(要传 path);"
                                              "list = 列出这台机器上有哪些检查器可用"},
                    "path": {"type": "string",
                             "description": "要检查的文件路径(工作区内,相对工作区或绝对都行)"},
                    "reason": {"type": "string",
                               "description": "用一句中文向用户说明这一步要达成什么"
                                              "(显示在权限确认框里)"},
                },
                "required": ["action"],
            },
            handler=diagnostics.diagnostics,
            # **不标只读**:它会执行工作区里的检查器(`cargo check` 会跑 `build.rs`),
            # 与 `hooks`(只读列清单)正好形成对照 —— 列清单只读,真跑检查器要点头。
            read_only=False,
        ),
    ]
