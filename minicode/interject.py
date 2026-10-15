"""跑着的时候插一句话(v0.9.177,终端那一侧)。

**要解决的问题**:GUI 忙的时候输入框照样能发,那一句走 `Agent.queue_interjection`
(落进留档是 `role: user`,在下一个工具边界接上)。CLI 没有这条路 —— 一个回合跑在
**主线程**上,期间没有任何人读 stdin,所以学长干活时你打的字要等它跑完才现身,
按回车也一样。这一层给它补一只终端口。

三条不是显然的口径:

1. **只吃"回合进行中"按下的键**。回合一结束立刻停,下一次 `prompt()` 由
   prompt_toolkit 独占 stdin —— 历史、补全、行编辑一个字都不掉。反过来做
   (开一只常驻线程读 stdin)会把这三样一起拆掉,还要和 prompt_toolkit
   抢同一个文件描述符,表现是"输入偶尔丢一半"。
2. **非 TTY 一律不开**。管道 / 重定向进来的那份 stdin 是给 REPL 的**下一句**用的
   (`echo /exit | python main.py` 就是靠它)。这一层去读它,轻则吃掉脚本的输入,
   重则永远阻塞。测试与 `scripts/e2e_*.py` 全走这条"不开"的路,所以它们的行為
   与本轮之前一字不差 —— 这不是巧合,是这一条决定的。
3. **Ctrl+C 不在这里处理**。Windows 控制台的 SIGINT 由主线程自己收(agent 在回合
   里 catch `KeyboardInterrupt` 停下来,见 `agent.py` 那两处)。poll 这边只负责
   看到 `\\x03` 时把手上那半句丢掉,不去吞那个事件。

能力面没动:`queue_interjection` 的三条口径(空句不收 / 超上限不收**且由调用方
说给用户** / 落进留档是 `role: user`)原样沿用,这里只是多一个入口。
"""
from __future__ import annotations

import sys
import threading
from contextlib import contextmanager

POLL_SECONDS = 0.12        # 与终端流式渲染同一个节流(0.12s),手感一致
_CTRL_C = "\x03"
_SPECIAL = ("\x00", "\xe0")   # Windows 的方向键 / 功能键前缀,后续一个字节是键码


def _tty_ready() -> bool:
    """这一台现在值不值得开这条口:得是真控制台,而且 `queue_interjection` 在。"""
    try:
        return bool(sys.stdin.isatty())
    except Exception:
        return False


def _win_key():
    """Windows 控制台:有键就取一个,没有回 None(**绝不阻塞**)。"""
    import msvcrt
    if not msvcrt.kbhit():
        return None
    ch = msvcrt.getwch()
    if ch in _SPECIAL:                 # 前缀字节:后面那个才是键码,整对丢掉
        msvcrt.getwch()
        return None
    return ch


def _posix_key():
    """POSIX:`select` 探一下 stdin,不阻塞地取一截。"""
    import os
    import select
    try:
        ready, _, _ = select.select([sys.stdin], [], [], 0)
    except Exception:
        return None
    if not ready:
        return None
    try:
        data = os.read(sys.stdin.fileno(), 64)
    except OSError:
        return None
    if not data:
        return None
    text = data.decode(sys.stdin.encoding or "utf-8", "replace")
    _POSIX_LEFT.append(text)
    return None


_POSIX_LEFT: list[str] = []


def _take_key():
    if _POSIX_LEFT:
        return _POSIX_LEFT.pop(0)
    if sys.platform == "win32":
        return _win_key()
    return _posix_key()


class Interjection:
    """回合进行中开一只 poll 线程;回合结束就收干净(不留在 stdin 上)。"""

    def __init__(self, agent, note=None, take_key=None, poll=POLL_SECONDS):
        self.agent = agent
        self._note = note
        self._take = take_key or _take_key
        self._poll = poll
        self._buf = ""
        self._esc = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.sent = 0

    # ------------------------------------------------------ 半句 → 整句(可单测)

    def feed(self, ch: str) -> "str | None":
        """吃一个键。凑成一整句时回那一句(并把它交出去),否则回 None。

        `\r`/`\n` 是句尾;退格改手上那半句;Ctrl+C 把手上那半句丢掉(中断事件
        本身不归这里,见模块头第 3 条)。

        ESC 序列(POSIX 终端上方向键/功能键打出来的 `\x1b[A` 那一串)整段吞掉:
        它们不是"我要说的话"的一部分。Windows 控制台不走这条路(`_win_key` 已经把
        前缀字节连着键码一起丢了),但同一个 `feed` 两边共用,所以这里也挡一下。
        """
        if self._esc == 1:
            # ESC 后面跟 `[`(CSI)或 `O`(SS3)才是"一串"序列;单独一个 ESC 直接完
            self._esc = 2 if ch in ("[", "O") else 0
            return None
        if self._esc == 2:
            # CSI 序列的终结字节在 0x40~0x7E(参数与中间字节都排在它前面)
            if 0x40 <= ord(ch) <= 0x7E:
                self._esc = 0
            return None
        if ch == "\x1b":
            self._esc = 1
            return None
        if ch in ("\r", "\n"):
            line, self._buf = self._buf, ""
            if not line.strip():
                return None
            # 两种结局都要说话:收下了要说(用户不知道自己那句进没进去),
            # 收不下更要说(信箱满了 / 空句)—— 静默丢一句是这一层最坏的坏法。
            if self.agent.queue_interjection(line):
                self.sent += 1
                self._notice("⟫ 插话已进信箱,跑完这一步就接上:" + line)
                return line
            self._notice("没接这句(信箱满了):" + line)
            return None
        if ch == _CTRL_C:
            self._buf = ""
            return None
        if ch in ("\x08", "\x7f"):
            self._buf = self._buf[:-1]
            return None
        if ch.isprintable() or ch == " ":
            self._buf += ch
        return None

    def _notice(self, text: str) -> None:
        if self._note is not None:
            self._note(text)

    # ------------------------------------------------------------------ 线程那一面

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                ch = self._take()
            except Exception:
                return          # 取键这件事坏了就整个收摊,绝不影响正在跑的回合
            if ch is None:
                self._stop.wait(self._poll)
                continue
            # 交出去与"说一声"都在 `feed` 里做(两条路径共用同一份口径),
            # 这里只负责把键喂进去。
            self.feed(ch)

    def start(self) -> "Interjection | None":
        if not _tty_ready():
            return None         # 非控制台:一个字都不读,把 stdin 整个留给 REPL
        self._thread = threading.Thread(target=self._run, daemon=True,
                                         name="cli-interjection")
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        self._thread = None
        if t is not None:
            t.join(timeout=1.0)   # 取键是非阻塞的,等它一圈自己走完


@contextmanager
def turn_with_interjection(agent, note=None, take_key=None, enabled=True):
    """`with turn_with_interjection(agent, ui.info): agent.run_turn(text)`

    开不出去就**原样跑**(等价于本轮之前的行为),不因为这只新功能让回合多一种坏法:
    非控制台、`enabled=False`、或 `queue_interjection` 不在这个对象上时,这里
    只是一个透明的 `with`。
    """
    box = Interjection(agent, note=note, take_key=take_key) if enabled else None
    runner = box.start() if box is not None else None
    try:
        yield box
    finally:
        if runner is not None:
            runner.stop()
