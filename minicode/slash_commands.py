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
]

ARG_PLACEHOLDER = "$text"


def has_arg(cmd: str) -> bool:
    return ARG_PLACEHOLDER in str(next(
        (c["prompt"] for c in PROMPT_COMMANDS if c["cmd"] == cmd), ""))


def expand(cmd: str) -> "str | None":
    """`/cmd` → 提示词模板;不认识的命令给 None(前端当普通文本处理)。

    $text 占位保留在返回值里(带参命令由前端把占位删掉、光标停在该处
    等用户续写);无参命令返回的就是可直接发送的完整提示词。
    """
    text = str(cmd or "").strip().lstrip("/")
    for c in PROMPT_COMMANDS:
        if c["cmd"] == text:
            return c["prompt"]
    return None
