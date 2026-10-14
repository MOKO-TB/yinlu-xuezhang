"""对话型 slash 命令(v0.9.74):`/总结`、`/翻译` 这类"发提示词"的快捷方式。

与 v0.9.65 的 `/ 动作面板` 的分工:动作面板执行的是**界面动作**(点左栏
真按钮,行为零分叉);这一张表执行的是**对话请求** —— 把 `/命令` 展开成
一句完整提示词发给学长。表在这里是唯一真相(与 onboarding.py 同思路:
改文案不动前端),前端开机经 `get_slash_commands` 拉一次。

两条克制:
- **只收高频且语义成立的命令**(总结/翻译/解释/校对/精简),不做成第二套
  动作面板 —— 打开某个面板的事仍归 SLASH_ACTIONS;
- **不带参数的命令直接发送,带 $text 的先展开等用户补内容** —— 菜单开着
  时输入框必然是 "/"+token(空格即关),整段替换是安全的;展开与发送都
  走前端 send() 同一条链,零分叉。
"""
from __future__ import annotations

PROMPT_COMMANDS: list[dict] = [
    {
        "cmd": "总结",
        "label": "总结这段对话",
        "desc": "要点 + 时间地点 + 接下来做什么",
        "prompt": "请把上面的对话内容总结成简明要点:关键结论、涉及的时间/地点/"
                  "截止日期,以及接下来要做的事。",
    },
    {
        "cmd": "翻译",
        "label": "翻译内容",
        "desc": "中译英 / 英译中,保留格式",
        "prompt": "请把下面的内容翻译成中文(若原文是中文则翻译成英文),"
                  "保留原意与格式:\n\n$text",
    },
    {
        "cmd": "解释",
        "label": "解释这段话",
        "desc": "用学生能听懂的话讲清楚",
        "prompt": "请解释下面这段内容里的关键概念与术语,用学生能听懂的话,"
                  "必要时给一个例子:\n\n$text",
    },
    {
        "cmd": "校对",
        "label": "校对文字",
        "desc": "错别字 / 语病 / 标点",
        "prompt": "请校对下面这段文字:错别字、语病、标点问题逐条指出并给出改法,"
                  "不要改写文风:\n\n$text",
    },
    {
        "cmd": "精简",
        "label": "精简表达",
        "desc": "改得更短,不丢关键信息",
        "prompt": "请把下面这段话改得更简短,保留全部关键信息"
                  "(时间、地点、数字、截止日期):\n\n$text",
    },
    {
        "cmd": "init",
        "label": "生成 AGENTS.md",
        "desc": "扫工作区起草项目指令文件,先看草稿再写盘",
        "prompt": "请为当前工作区起草一份 AGENTS.md(Codex/ZCode 同款的项目指令文件,"
                  "你每一轮都会自动读到它):先用 repo_outline 扫一遍目录结构,再看 "
                  "README、配置与入口文件,写清「这个项目是什么、目录约定、怎么跑测试、"
                  "代码与文档风格、绝对不要做的事」。工作区根已有 AGENTS.md 就先读旧文件、"
                  "在它基础上补充而不是整份覆盖。**先把完整草稿给我看,我同意后"
                  "再用 write_file 写到工作区根**。",
    },
    {
        "cmd": "review",
        "label": "审查未提交改动",
        "desc": "审查工作区未提交改动(按 P0~P3 分类缺陷并给改法)",
        "prompt": "请对当前工作区的所有未提交改动进行深度代码审查(**只审查,不改任何文件**):\n"
                  "1. 查看 git 状态与变更 diff(包含已暂存与未暂存的修改);\n"
                  "2. 按严重程度分级审查并列出问题:\n"
                  "   - **P0(致命缺陷/安全隐患/凭据泄露)**: 可能导致崩溃、越界、死锁、密钥硬编码的问题;\n"
                  "   - **P1(业务与正确性缺陷)**: 逻辑错误、边界未处理、与设计/文档冲突的改动;\n"
                  "   - **P2(性能与可维护性)**: 潜在瓶颈、过度设计、冗余调用;\n"
                  "   - **P3(代码规范与坏味道)**: 命名不当、缺少注释、风格不一致;\n"
                  "3. 对每个问题指明具体【文件:行号】,讲清「原因」并附带「建议改法/修复代码」;\n"
                  "4. 若代码全无问题,如实说明并给出通过结论。",
    },
    {
        "cmd": "review-branch",
        "label": "审查指定分支",
        "desc": "看某分支相对分叉点改了什么并给审查意见",
        "prompt": "请审查分支 $text 相对我们当前进线的改动(**只审查,不改任何文件**):"
                  "先用 worktree 工具 action=branch 带 branch=$text 看它相对分叉点"
                  "改了哪些文件、提交清单与行数合计;再对值得细看的文件用同一 action "
                  "带 path=<仓库内相对路径> 看完整 diff;意见按「会出事的地方 / "
                  "和项目既有口径冲突 / 更好的写法」三组给,每个文件都点名说看过还是没有"
                  "意见;看不出来意图的就直说,绝不猜。",
    },
    {
        "cmd": "wiki",
        "label": "代码库全景百科",
        "desc": "查阅或生成项目架构概览、模块分工与常用命令",
        "prompt": "请查阅或为当前工作区生成代码库百科(Repo-Wiki):\n"
                  "调用 repo_wiki 工具(action=summary)获取架构全景、核心模块职责、"
                  "常用构建与测试命令、以及工程约定;\n"
                  "向我系统呈现当前项目的全貌大纲,并在回答最后告诉我接下来可以针对哪个模块深入开发。",
    },
    {
        "cmd": "test",
        "label": "运行项目测试",
        "desc": "自动探测测试框架并运行测试套件(回车即跑)",
        "prompt": "请为当前工作区运行测试套件:\n"
                  "1. 调用 test_runner 工具(action=run)执行测试;\n"
                  "2. 若测试全绿,如实报告测试通过统计(条数、通过率);\n"
                  "3. 若存在失败测试,精准指出失败用例名称、断言与错误堆栈核心信息,并给出针对性的排查建议。",
    },
    {
        "cmd": "fix-test",
        "label": "修复失败测试",
        "desc": "针对挂掉的测试用例进行最小闭环自愈修复",
        "prompt": "请定位并修复当前工作区中失败的测试用例:\n"
                  "1. 调用 test_runner 工具(action=last_result)查看最近一次测试失败详情,若无记录则重新运行(action=run);\n"
                  "2. 阅读相关源码与测试用例,定位失败根本原因(边界条件、类型不匹配或逻辑回归);\n"
                  "3. 采用最小修改原则修复缺陷,严禁破坏既有规范与现有测试;\n"
                  "4. 重新调用 test_runner 验证修复效果,确保测试转绿并汇报修复结论。",
    },
    {
        "cmd": "symbol",
        "label": "精准符号搜索",
        "desc": "跨文件定位类、函数、接口或变量定义",
        "prompt": "请在当前工作区搜索代码符号「$text」:\n"
                  "1. 调用 symbol_search 工具(query=\"$text\")进行跨文件符号定位;\n"
                  "2. 列出所有匹配的符号定义(文件、行号、符号类型与完整签名);\n"
                  "3. 简要说明其设计用途与关键调用上下文。",
    },
    {
        "cmd": "commit",
        "label": "生成规范提交说明",
        "desc": "智能分析当前改动并生成 Conventional Commit 建议",
        "prompt": "请为当前工作区的代码改动起草规范的 Conventional Commit 提交说明:\n"
                  "1. 分析当前暂存区与工作区的所有改动(可借助 gitpanel 或 diff 分析);\n"
                  "2. 根据变更类型(feat/fix/docs/refactor/test/chore)起草一条简练精确的提交标题;\n"
                  "3. 列出清晰的改动要点清单(包含改动动机与关键文件影响)。",
    },
    {
        "cmd": "ref",
        "label": "查找符号引用",
        "desc": "跨文件查找符号的全部调用与引用位置并评估影响面",
        "prompt": "请在当前工作区查找符号「$text」的所有引用与调用位置:\n"
                  "1. 调用 find_references 工具(symbol=\"$text\")定位全部调用、导入与类型使用;\n"
                  "2. 归纳分析该符号的改动影响面(业务模块与测试文件分布、风险级别);\n"
                  "3. 若存在潜在修改或重构风险,给出具体的测试与调用端同步建议。",
    },
    {
        "cmd": "references",
        "label": "查找符号引用与影响面",
        "desc": "查找符号在全代码库中的调用链与修改影响分析",
        "prompt": "请在当前工作区查找符号「$text」的所有引用与调用位置:\n"
                  "1. 调用 find_references 工具(symbol=\"$text\")定位全部调用、导入与类型使用;\n"
                  "2. 归纳分析该符号的改动影响面(业务模块与测试文件分布、风险级别);\n"
                  "3. 若存在潜在修改或重构风险,给出具体的测试与调用端同步建议。",
    },
    {
        "cmd": "rename",
        "label": "重命名代码符号",
        "desc": "安全跨文件重命名符号并预演改动影响: /rename <原名> <新名>",
        "prompt": "请对代码符号执行跨文件重构重命名($text):\n"
                  "1. 解析原符号名称与新名称;\n"
                  "2. 调用 rename_symbol 工具(dry_run=True)进行安全改动预演与语法检验;\n"
                  "3. 向我呈现影响文件列表、修改代码行对比与统一 diff;\n"
                  "4. 在得到我的明确确认前,不要写入修改。",
    },
    {
        "cmd": "hierarchy",
        "label": "调用层次结构分析",
        "desc": "分析函数/方法的调用者(Callers)或被调依赖(Callees)链路树: /hierarchy <符号名>",
        "prompt": "请对代码符号「$text」执行调用层次结构拓扑分析:\n"
                  "1. 调用 call_hierarchy 工具(symbol=\"$text\", direction=\"both\")分析其调用方与依赖项;\n"
                  "2. 输出清晰的缩进树形结构或 Mermaid 链路图;\n"
                  "3. 总结入度(Fan-In)与出度(Fan-Out),指明关键上游入口与核心底层依赖。",
    },
    {
        "cmd": "callers",
        "label": "查询符号调用方",
        "desc": "向上追溯谁调用了该函数或方法(Callers链路): /callers <符号名>",
        "prompt": "请查询有哪些上层模块与函数调用了「$text」:\n"
                  "1. 调用 call_hierarchy 工具(symbol=\"$text\", direction=\"incoming\", depth=3)分析调用链路;\n"
                  "2. 列出完整的调用者层级链路树与涉及文件行号;\n"
                  "3. 指出最顶层的业务入口函数与触发源。",
    },
    {
        "cmd": "def",
        "label": "查看符号定义与实现",
        "desc": "跨文件精准定位符号定义位置并预览实现代码: /def <符号名>",
        "prompt": "请查找代码符号「$text」的定义与实现:\n"
                  "1. 调用 symbol_definition 工具(symbol=\"$text\", peek=True)准确定位定义位置与代码块;\n"
                  "2. 说明其所在文件路径、行号范围、完整函数/类签名与 docstring 文档;\n"
                  "3. 简要分析其核心实现逻辑与关键参数含义。",
    },
    {
        "cmd": "definition",
        "label": "跳转符号定义",
        "desc": "跨文件查找符号定义与实现代码: /definition <符号名>",
        "prompt": "请查找代码符号「$text」的定义与实现:\n"
                  "1. 调用 symbol_definition 工具(symbol=\"$text\", peek=True)准确定位定义位置与代码块;\n"
                  "2. 呈现其所在文件路径、起止行号与完整实现代码片段;\n"
                  "3. 说明该定义的职责与实现细节。",
    },
    {
        "cmd": "outline",
        "label": "查看代码文件大纲",
        "desc": "提取指定代码文件的类、函数与成员结构大纲: /outline <文件路径>",
        "prompt": "请提取代码文件「$text」的结构大纲与类/成员树:\n"
                  "1. 调用 code_outline 工具(path=\"$text\", depth=2)提取该文件的层次结构大纲;\n"
                  "2. 输出清晰易读的 ASCII 结构树,标明类、方法、函数与关键常量的行号区间与参数签名;\n"
                  "3. 简要概括该文件包含的核心模块、核心类与主要职责划分。",
    },
    {
        "cmd": "snapshots",
        "label": "查看工作区快照",
        "desc": "查看影子 Git 检查点列表与变更时间",
        "prompt": "请帮我查看当前工作区的影子 Git 快照记录与检查点状态:\n"
                  "1. 检查影子检查点记录;\n"
                  "2. 列出最近的整树快照列表、时间与改动主题;\n"
                  "3. 告知如何使用 /restore <序号> 进行无损还原。",
    },
    {
        "cmd": "restore",
        "label": "从影子快照还原",
        "desc": "从指定快照检查点还原文件(只还原、绝不删): /restore <序号|sha>",
        "prompt": "请帮我从影子快照还原工作区文件($text):\n"
                  "1. 解析指定的快照序号或 SHA 校验和;\n"
                  "2. 按照安全还原准则进行还原(只还原受控文件,绝不删除新增文件);\n"
                  "3. 报告成功还原的文件列表与统计。",
    },
    {
        "cmd": "fork",
        "label": "分叉当前会话",
        "desc": "从指定历史会话或节点分叉出独立会话分支: /fork [序号]",
        "prompt": "请帮我将当前任务或指定会话($text)分叉出一个独立的新分支任务,保留之前的上下文并开辟新会话。",
    },
]

ARG_PLACEHOLDER = "$text"
ARGS_PLACEHOLDER = "$ARGS"   # 工作区自定义命令的占位(.commands/*.md)


COMMANDS_DIR = ".commands"   # 工作区自定义命令的落点(每个 .md 一条)


def _parse_front(text: str) -> "tuple[dict, str]":
    """frontmatter(name/description 一段,10 行)。不复用 skills 的私有解析:
    那是 SKILL.md 的形状(未来要加字段会各自变),这里只认自己这条最简的。"""
    if text.startswith("---"):
        import re
        m = re.match(r"^---{BS}s*{BS}n(.*?){BS}n---{BS}s*{BS}n?(.*)$".replace("{BS}", chr(92)), text, re.S)
        if m:
            meta = {}
            for line in m.group(1).splitlines():
                if ":" in line:
                    key, _, value = line.partition(":")
                    meta[key.strip()] = value.strip()
            return meta, m.group(2)
    return {}, text


def workspace_commands(workspace) -> "list[dict]":
    """工作区自定义命令(F2,v0.9.100):`.commands/<名字>.md` = description +
    提示词模板($ARGS 是用户补的内容)。社团/班级可以预置「周报模板」「请假条」
    这类高频请求,一条文件一个命令。**同名的自定义覆盖内置表**(项目定制
    优先);frontmatter 坏的如实标注,不静默丢。"""
    from pathlib import Path as _Path
    d = _Path(workspace) / COMMANDS_DIR if workspace else None
    if d is None or not d.is_dir():
        return []
    out = []
    for entry in sorted(d.glob("*.md")):
        try:
            text = entry.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        meta, body = _parse_front(text)
        body = body.strip()
        if not body:
            out.append({"cmd": entry.stem, "label": entry.stem,
                        "desc": "(frontmatter 坏或缺正文)",
                        "prompt": "", "custom": True, "broken": True})
            continue
        name = str(meta.get("name") or entry.stem).strip() or entry.stem
        out.append({"cmd": entry.stem,
                    "label": name,
                    "desc": str(meta.get("description") or ""),
                    "prompt": body,
                    "custom": True, "broken": False})
    return out


def has_arg(cmd: str) -> bool:
    return ARG_PLACEHOLDER in str(next(
        (c["prompt"] for c in PROMPT_COMMANDS if c["cmd"] == cmd), ""))


def expand(cmd: str, workspace=None) -> "str | None":
    """`/cmd` → 提示词模板;不认识的命令给 None(前端当普通文本处理)。

    $text 占位保留在返回值里(带参命令由前端把占位删掉、光标停在该处
    等用户续写);无参命令返回的就是可直接发送的完整提示词。
    """
    text = str(cmd or "").strip().lstrip("/")
    # 工作区自定义**优先于内置表**(项目定制覆盖默认);broken 的条目不展开
    if workspace is not None:
        for c in workspace_commands(workspace):
            if c["cmd"] == text and not c.get("broken"):
                return c["prompt"]
    for c in PROMPT_COMMANDS:
        if c["cmd"] == text:
            return c["prompt"]
    return None
