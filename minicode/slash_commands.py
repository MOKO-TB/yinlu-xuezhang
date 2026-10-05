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
        "label": "审查一个分支",
        "desc": "看某分支相对分叉点改了什么并给审查意见",
        "prompt": "请审查分支 $text 相对我们当前进线的改动(**只审查,不改任何文件**):"
                  "先用 worktree 工具 action=branch 带 branch=$text 看它相对分叉点"
                  "改了哪些文件、提交清单与行数合计;再对值得细看的文件用同一 action "
                  "带 path=<仓库内相对路径> 看完整 diff;意见按「会出事的地方 / "
                  "和项目既有口径冲突 / 更好的写法」三组给,每个文件都点名说看过还是没有"
                  "意见;看不出来意图的就直说,绝不猜。",
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
