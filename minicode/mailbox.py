"""递话(v0.9.173):正在跑的两个对象之间留一句话。

**这一类要解决的问题**:话要递到**别人**那里。项目里原本只有两条单向的路 ——
`Agent.queue_interjection`(用户对自己**这条**会话补一句,落进留档是 `role: user`)
与 `queue_note`(子代理 → 主会话,系统侧注记)。"我这条会话想跟**另一条**在跑的东西
说一句话"没有路:主代理调 `task` 时**阻塞等报告**,所以它自己发不出去;而别的活会话
(侧聊页那一条、后台任务)连"对面存在"都看不见。

**这一层只做一件事:把一句话放进对方的盒子里**,什么时候取、怎么取由对方说了算。

三条铁律(每一条都是既有决定的延伸,不是新立规矩):

1. **递过去的话不冒充人话**。落进对方会话时走**系统侧注记**那条通道(与
   `queue_note`、事件钩子附话同一条出口),留档里不是 `role: user`。理由与 v0.9.154
   那条一字不差:机器加的东西冒充用户说的,模型会当成"用户刚提的新要求"去办。
2. **只在请求与请求之间递**。取盒子的动作放在"发这一次请求之前",与 `_take_inbox`
   同一个位置、同一个理由:handler 中间塞进去会把 tool call/result 的配对撕开。
3. **收不下就如实说收不下**(空话 / 超上限 / 对方已经跑了 / 名字说不清是哪一个)。
   递话的人看不见对面那一侧,不回一句它就会以为自己说过了 —— 与 `notify_parent`
   那条"不说的话它会以为提醒过了"同一条口径。

**它不是信箱的四条边界**(写清楚免得被当成缺陷):

- 盒子活在**进程内存里**,进程一退全没。跨重启递话得先有落盘的会话身份,不在这一格。
- 没有回执、没有往返:对方收下之后会不会照做,这一层管不着(也不假装管)。
- 子代理的盒子随它**这一路跑完就摘掉**;会话的盒子随 `Agent.close()` 摘掉。摘了以后
  再递 = 一句"那个对象已经不在了",而不是悄悄丢。
- 只在本机进程内:不联网、不跨机器、不写盘。
"""
from __future__ import annotations

import threading
import time

MSG_CHARS = 1200        # 单条留言的上限(截了几字要在正文里写明)
BOX_MAX = 8             # 一个盒子最多攒几条;满了不收并如实说(不挤掉先来的)
TARGET_CHARS = 48       # 列表里那一行"谁"的显示宽度
LIVE_HINT_SECONDS = 600  # 超过这么久没动静的盒子在列表里标一句"可能已经停了"


class _Box:
    """一个盒子:谁在用、别人的话攒在这儿。锁在盒子自己身上(同一条会话可能被
    界面线程与回合线程同时碰)。"""

    def __init__(self, handle: str, kind: str, label: str):
        self.handle = handle
        self.kind = kind            # "子代理" / "会话"
        self.label = str(label or "").strip() or kind
        self.messages: list[str] = []
        self.lock = threading.Lock()
        self.opened = time.time()
        self.touched = time.time()  # 最近一次有人往里递话


_BOXES: dict[str, _Box] = {}
_LOCK = threading.Lock()
_SEQ = [0]


def _clip(text: str, limit: int = MSG_CHARS) -> str:
    """封顶但如实说封了几字 —— "只显示了一段"却写成"这就是全部"是这个项目不许的。"""
    body = str(text or "").strip()
    if len(body) <= limit:
        return body
    return body[:limit] + f"…(原话共 {len(body)} 字,上面只留了 {limit} 字)"


def open(kind: str, label: str) -> str:
    """开一个盒子,返回 handle。调用方负责在结束时 `close` 它。"""
    with _LOCK:
        _SEQ[0] += 1
        handle = f"{'sub' if kind == '子代理' else 'sess'}-{_SEQ[0]:x}"
        _BOXES[handle] = _Box(handle, kind, label)
        return handle


def close(handle: str) -> None:
    """摘掉盒子。**摘掉之后别人再递会得到一句实话**,不是静默丢掉。"""
    if not handle:
        return
    with _LOCK:
        _BOXES.pop(handle, None)


def post(handle: str, sender: str, text: str) -> "tuple[bool, str]":
    """往盒子里放一句话。返回(成没成, 一句给**递话方**看的实话)。

    三样收不下:空话、超 `BOX_MAX` 条、盒子已经不在了 —— 每一种都说清是哪种。
    """
    clean = " ".join(str(text or "").split())
    if not clean:
        return False, "错误: 没写内容 —— 递一句空话等于没递。"
    with _LOCK:
        box = _BOXES.get(str(handle or "").strip())
    if box is None:
        return False, (f"错误: 递不到 {handle} —— 那个对象已经不在了(子代理跑完就摘、"
                       "会话关掉就摘)。先用 action=list 看现在还有谁在。")
    with box.lock:
        if len(box.messages) >= BOX_MAX:
            return False, (f"错误: {box.label} 那边还压着 {len(box.messages)} 条没取走"
                           f"(上限 {BOX_MAX} 条)。这一条**没有**递出去 —— "
                           "等它取走一轮再说,别把话堆成墙。")
        box.messages.append(f"〔{str(sender or '别的会话')[:16]} 递话〕{_clip(clean)}")
        box.touched = time.time()
    return True, f"已把这句话放进 {box.label} 的盒子({handle}),它下一次发请求前就会看到。"


def take(handle: str) -> list[str]:
    """取空盒子并返回那几句(**取走即消费**,与 `_take_notes` 同一条口径)。

    攒着不取的后果是"这句到底哪一轮看到的"说不清 —— 那比晚一轮更糟。
    """
    if not handle:
        return []
    with _LOCK:
        box = _BOXES.get(handle)
    if box is None:
        return []
    with box.lock:
        if not box.messages:
            return []
        items, box.messages = box.messages, []
    return items


def listing() -> list[dict]:
    """现在有哪些盒子(新开的在前),以及各自压了几条没取走。"""
    now = time.time()
    with _LOCK:
        boxes = list(_BOXES.values())
    out = []
    for b in sorted(boxes, key=lambda x: x.opened, reverse=True):
        with b.lock:
            pending = len(b.messages)
        out.append({"handle": b.handle, "kind": b.kind, "label": b.label,
                    "pending": pending, "age": int(now - b.opened),
                    "idle": int(now - b.touched),
                    "maybe_stopped": (now - b.touched) > LIVE_HINT_SECONDS})
    return out


def find(target: str) -> "tuple[str | None, str]":
    """按 handle 或标签找一个盒子。返回(handle 或 None, 说不清时给的那句话)。

    标签只做**唯一匹配**:同名两条回一句"说不清是哪个,用 handle"。
    猜一个然后递错人,比回一句"你说的是哪个"坏得多。
    """
    want = str(target or "").strip()
    if not want:
        return None, "错误: 没说是递给谁。先用 action=list 看现在有谁在。"
    with _LOCK:
        boxes = list(_BOXES.values())
    for b in boxes:
        if b.handle == want:
            return b.handle, ""
    hits = [b for b in boxes if b.label == want]
    if len(hits) == 1:
        return hits[0].handle, ""
    if not hits:
        hits = [b for b in boxes if want in b.label]
    if len(hits) == 1:
        return hits[0].handle, ""
    if not hits:
        return None, (f"错误: 现在没有叫「{want}」的对象。"
                      "先用 action=list 看一遍 —— 子代理跑完就摘、会话关掉就摘,"
                      "所以「刚才还在」的东西可能已经没了。")
    names = "、".join(f"{b.label}({b.handle})" for b in hits)
    return None, (f"错误: 「{want}」同时对上 {len(hits)} 个:{names} —— "
                  "说不清递哪一个。请用括号里那个 handle 指定到具体一个。")


def _fmt_age(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} 秒"
    if seconds < 3600:
        return f"{seconds // 60} 分钟"
    return f"{seconds // 3600} 小时"


def session_message(ctx, action: str = "list", target: str = "",
                    text: str = "") -> str:
    """`session_message` 工具的 handler:list 看在跑的谁 / send 递一句话。

    它**不是**给主代理操纵自己的子代理用的:主代理调 `task` 时正阻塞等报告,
    那时候它连这一次工具调用都还没返回。真正用得上的投递方是**并发的另一条会话**
    (侧聊页那一条、后台任务)与**界面上的人** —— 工具描述里就这么写,不吹。
    """
    act = str(action or "list").strip().lower()
    mine = str(getattr(ctx, "mail_handle", "") or "")
    if act == "list":
        boxes = listing()
        if not boxes:
            return ("现在没有能递话的对象。子代理跑完就摘盒、会话关掉就摘盒 —— "
                    "正在跑的子代理与开着的会话才会出现在这里。")
        lines = [f"现在能递话的对象({len(boxes)} 个):"]
        for b in boxes:
            who = f"{b['label']} ({b['handle']})"
            if b["handle"] == mine:
                who += " —— **就是你自己这条**"
            wait = (f",盒子里压着 {b['pending']} 条没取走" if b["pending"]
                    else ",盒子里没话等着")
            stopped = ",可能已经停了(很久没动静)" if b["maybe_stopped"] else ""
            lines.append(f"  · {who} —— 开了 {_fmt_age(b['age'])}{wait}{stopped}")
        lines.append('递一句话:action="send", target=<上面括号里那个>, text="…"。')
        return "\n".join(lines)
    if act != "send":
        return f'错误: action 只认 list / send,收到的是「{action}」。'
    handle, why = find(target)
    if handle is None:
        return why
    if handle == mine:
        return ("错误: 那一条就是你自己。递给自己只会让这句话在下一轮请求里"
                "以「别人递话」的口气出现 —— 要给自己记一件事请用 memory。")
    ok, said = post(handle, "这条会话", text)
    return said if ok else said
