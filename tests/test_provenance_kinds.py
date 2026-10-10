"""日志里 `gate` 那一列的**门名全集**不许静默过期(v0.9.150)。

这一支的全部意义在"**名单不手写**":`provenance.GATE_KINDS` 是给**事后排查的人**看的
(分发出去的程序没有遥测,`injection-events.log` 是唯一的事后线索,而那个人手上通常
没有源码可 grep),而"这份名单是不是真的等于日志里会出现的那些词"只能从**源码**上
对账。手抄一份名单放进测试,等于给同一件事造第二处真相 —— 下一次新增门名时两处一起
过期,而这支针照旧全绿,那正是这份清单要防的那种静默失效。

两个方向都钉:
- **不许漏**(源码里出现、名单里没有):新增一个门名却忘了登记 → 日志里多一个没人认识的词;
- **不许剩**(名单里有、源码里谁也产生不出来):名单比现实长 → 读它的人被引到不存在的门上。
"""

from __future__ import annotations

import re
from pathlib import Path

from minicode import agent, provenance

SRC = Path(__file__).resolve().parent.parent / "minicode"

# 门名只可能是小写加下划线;两条正则都只认**字面量**(变量当门名的那种,比如
# `--mcp-serve` 那条路传的 `gate_name`,由下面 `test_..._reject_...` 单独点名)。
_ASK_LITERAL = re.compile(r"\._ask\(\s*\"([a-z_]+)\"")
_ESCALATION_LITERAL = re.compile(r"log_escalation\(\s*\"([a-z_]+)\"")


def _literals(pattern: re.Pattern) -> dict:
    """{门名: 出处} —— 把整个 `minicode/` 递归扫一遍(注意是 rglob,不是 `*.py`)。

    **按整份文本扫,不逐行**:`log_escalation(` 后面跟着换行的那几处
    (`hook_rewrite` / `hook_answer`)逐行扫是抓不到的 —— 第一版就是这么写的,
    于是那两支针红在一个**假**的"谁也不产生"上。跨行是这一处的正常写法,
    不是特例。
    """
    found: dict[str, str] = {}
    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for m in pattern.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            found.setdefault(m.group(1), f"{path.relative_to(SRC.parent)}:{line}")
    return found


def test_every_ask_kind_is_declared():
    """`PermissionGate._ask("X")` 报出来的每一个门名都要在全集里。"""
    found = _literals(_ASK_LITERAL)
    assert len(found) >= 11, f"只扫到 {len(found)} 个门名,正则或目录大概错了:{found}"
    missing = {k: v for k, v in found.items() if k not in provenance.GATE_KINDS}
    assert not missing, f"这些门名没登记进 GATE_KINDS:{missing}"


def test_every_log_escalation_literal_is_declared():
    """直接调 `log_escalation("X")` 的那几处也要在全集里(第二处来源)。"""
    found = _literals(_ESCALATION_LITERAL)
    assert found, "一个 `log_escalation(\"X\"` 都没扫到,正则或目录大概错了"
    missing = {k: v for k, v in found.items() if k not in provenance.GATE_KINDS}
    assert not missing, f"这些门名没登记进 GATE_KINDS:{missing}"


def test_the_mapping_stays_inside_the_vocabulary():
    """映射的键与值都必须落在全集里 —— 否则日志里会出现一个全集解释不了的词。"""
    bad = {k: v for k, v in provenance.SECURITY_LOG_GATES.items()
           if k not in provenance.GATE_KINDS or v not in provenance.GATE_KINDS}
    assert not bad, f"映射把日志引到全集之外:{bad}"


def test_the_list_has_no_word_that_nothing_produces():
    """反向:名单里每个词都得真有出处 —— 名单比现实长,读它的人会被引到不存在的门上。

    出处有三种:① `_ask("X")` 报出来的门名;② 直接 `log_escalation("X")` 的那几处;
    ③ 映射的**值** —— `escalation` 正是只从这一处来的(它是 `external` 在日志里的
    别名,没有任何一处拿它当门名调用),所以它算"有出处",但它的出处**只有**这一种,
    下面单独钉住这件事。
    """
    produced = set(_literals(_ASK_LITERAL)) | set(_literals(_ESCALATION_LITERAL))
    produced |= set(provenance.SECURITY_LOG_GATES.values())
    produced |= {"reject"}          # `--mcp-serve` 拒外部客户端(见下一支)
    stale = provenance.GATE_KINDS - produced
    assert not stale, f"名单里有谁也不产生的词:{stale}"


def test_the_alias_never_gets_called_as_a_gate_name():
    """`escalation` 只作为**取值**存在:没有任何一处 `_ask("escalation")`。

    它是 `external` 的日志别名。哪天有人直接拿它当门名调用,那说明"门"与"日志里的词"
    这两件事又混起来了 —— 而这一列的全部价值就是"由门自己报,不从文案猜"。
    """
    assert "escalation" not in _literals(_ASK_LITERAL)
    assert "escalation" not in _literals(_ESCALATION_LITERAL)
    assert "escalation" in provenance.SECURITY_LOG_GATES.values()


def test_the_kinds_that_must_ask_a_human_are_declared():
    """「必须问人、不许钩子代答」那三类必须是全集里的词 —— 它们是被代码点名的。"""
    assert set(agent.NON_DELEGABLE_ASKS) <= provenance.GATE_KINDS


def test_the_mcp_reject_kind_is_declared():
    """`--mcp-serve` 那条路的 `reject` 走的是变量不是字面量,所以单独点名。

    它不在上面两支的扫描范围里(`log_escalation(gate_name, …)` 的第一个参数是变量),
    但它是**真会落进日志**的一个词 —— 漏登记的话,全集的第一个读者就会在日志里
    撞见一个解释不了的 `reject`。
    """
    assert "reject" in provenance.GATE_KINDS
    src = (SRC / "mcp_server.py").read_text(encoding="utf-8")
    assert 'ask_kind or "reject"' in src, "mcp_server 里那句兜底改名了,这一支要跟着改"


def test_the_vocabulary_is_a_frozenset_of_plain_words():
    """形状钉死:它是 frozenset(常量不该被就地改),每个词都是小写加下划线。"""
    assert isinstance(provenance.GATE_KINDS, frozenset)
    odd = {k for k in provenance.GATE_KINDS if not re.fullmatch(r"[a-z_]+", k)}
    assert not odd, f"这些词不像门名:{odd}"


def test_the_old_two_words_are_still_recognised():
    """兼容口径:老行里的 `credential` 与 `escalation` 两个词照旧在全集里。

    这份日志是**追加**写的,老行永远在;改名会让新旧线索对不上,所以这两个词
    是全集里唯一"不能删"的部分(与 `log_escalation` 的 docstring 同一句口径)。
    """
    assert {"credential", "escalation"} <= provenance.GATE_KINDS
    assert provenance.SECURITY_LOG_GATES["external"] == "escalation"
