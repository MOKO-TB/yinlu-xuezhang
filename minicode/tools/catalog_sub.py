"""子代理 → 主会话留话那一只工具(I,v0.9.154)。

**为什么单开一份目录**:它不属于现有任何一族 —— 不是文件、不是网络、不是校园、
不是知识库,是"子代理跟主会话说一句话"。塞进哪一份都是在改那一份的语义。

注册表里这些工具的**先后顺序**由 `registry.build_default_registry()` 决定,
并由 `tests/test_registry_split.py` 逐字钉住 —— 别在这里自己排。
"""
from __future__ import annotations

from minicode.tools.base import Tool

# 一句话的上限。它是**提醒**,不是报告(报告走 `task` 的返回值)。取 500 字:
# 够说清"哪儿有个分叉、为什么现在就得知道",又不至于把主会话的上下文挤掉。
NOTE_MAX_CHARS = 500


def notify_parent(ctx, text: str = "") -> str:
    """把一句话交给主会话的信箱(`Agent.queue_note`)。三条口径就是它的全部语义:

    ①**不许冒充用户** —— 走的是 `queue_note`(系统侧注记),**不是**
      `queue_interjection`(那条落进留档时是 `role: user`,见 `_take_inbox`)。
      这是这一只存在的全部理由:反过来做,子代理的提醒在留档里就成了"用户说过的话"。
    ②**不许拦下任何东西** —— 它只往**下一次请求**里加一句话,既不打断子代理,
      也不改变主会话那边的流程(与 `stop` 钩子"不许有否决权"同源)。
    ③**收不下要说出口** —— 超上限、或主会话那边排队满了,如实回一句。
      **子代理看不见用户那一侧**,不说的话它会以为自己提醒过了。
      (插话那条可以默默不收,因为用户看得见自己那句话已经发出去了 —— 这一条不行。)
    """
    enqueue = getattr(ctx, "notify_parent", None)
    if not callable(enqueue):
        return ("这里没有可以转达的对象 —— 你就是主会话,想跟用户说话直接在回复里说。"
                "(这一只是给**子代理**用的:它看不到用户,才需要一条路把话递回去。)")
    body = " ".join(str(text or "").split())
    if not body:
        return "没写内容,这一句没发出去(要说的话填进 text)。"
    cut = len(body) > NOTE_MAX_CHARS
    try:
        ok = bool(enqueue(body[:NOTE_MAX_CHARS] if cut else body))
    except Exception:
        ok = False
    if not ok:
        return ("**没送到**:主会话那边排队满了。它**看不到这句话** —— "
                "要紧的事请写进你的最终报告。")
    tail = f"(超过 {NOTE_MAX_CHARS} 字,只送了前面那一截)" if cut else ""
    return f"已经留给主会话了{tail},它会在下一次请求里看到。"


def tools() -> list[Tool]:
    """子代理那一族的工具。"""
    return [
        Tool(
            name="notify_parent",
            description=(
                "给**主会话**留一句话(子代理专用)。它落在主会话**下一次请求**里的一条"
                "系统侧注记上 —— 不会变成「用户说的话」,也不会被当成用户提的要求。"
                "**什么时候用**:你在执行任务时碰到一件主会话必须知道的事(一个岔路没定、"
                "一个前提不成立、一处代价远超预期),而它不该等到你结束才被看到。"
                "**什么时候别用**:正常汇报写进你的最终报告就够了;这一句是**提醒**、"
                f"不是报告(上限 {NOTE_MAX_CHARS} 字)。它**不会打断、也不会拦下任何东西**"
                " —— 主会话那边照旧等你跑完,所以别把它当提问用(问不到回答)。"
                "同一轮里不必把同一件事说两遍。主会话自己调这一只会得到一句"
                "「你就是主会话」。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string",
                             "description": "要说的话(一句话说清;超过上限会被截断并说明)"},
                },
                "required": ["text"],
            },
            handler=notify_parent,
            # **标只读**:它不碰文件、不联网、不执行任何东西,只是往内存里加一句话。
            # 让它走确认框等于"提醒一下也要点一次头",那会让这一只变成没人会用的东西
            # —— 而它要解决的那件事(子代理没有提问通道)就还在。
            read_only=True,
        ),
    ]
