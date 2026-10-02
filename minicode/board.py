"""跨会话看板(B1,v0.9.101):把四处现成的结论拼成「在跑 / 等你 / 完成」三态。

定位与 `health.py` 同一条:**只聚合不重算**。这一屏不许有自己的账 —— 会话列表来自
`session.list_sessions`、在跑来自桥内存里的 `running_sessions()`、后台命令来自
`shell`、计划状态来自 `planning.plan_state`、定时任务来自 `jobs` 那本账。新开一份
"任务状态"落盘,就得回答"会话被杀后这份账谁清",还要交个人数据四处登记税,
并且立刻造出第二份真相。

三条写死的口径:

1. **不谎报"在跑"**:`running` 的真源是桥的 `running_sessions()` —— **只在进程内存里**,
   跨进程看不见(退出重开后就不知道谁在跑了)。所以这一屏带一句边界说明,
   拿不到确证的会话不进"在跑",也不进"等你",宁可少列。
2. **每一行都能追到一条真来源**:取不到就不出现,绝不画一个空的"暂无任务"占位撑版面。
3. **上限要说出口**:`list_sessions` 是逐行读 jsonl 的,会话多起来就是几十上百个文件 I/O,
   所以只扫最近 `SESSION_SCAN` 份,并把"只列最近 N 个"写在载荷里而不是悄悄截。
"""
from __future__ import annotations

import time

# 一屏每态最多几行;超了在 `more` 里报数,界面写"另有 N 条没列出"。
ROW_LIMIT = 8
# 会话清单的扫描上限:这是文件 I/O 的预算,不是"用户的任务就这么多"。
SESSION_SCAN = 20

STATES = ("running", "waiting", "done")

BOUNDARY_NOTE = ("「在跑」只知道这个程序开着期间的事 —— 那份标记在桥的内存里,"
                 "退出重开之后就看不见谁在跑了(边界写在方案 §七.4,已拍板接受)。")


def _row(state: str, title: str, sub: str = "", action=None, source: str = "") -> dict:
    return {"state": state, "title": str(title or "")[:80], "sub": str(sub or "")[:160],
            "action": action or {}, "source": source}


# 来源那一侧用的公开名字:看板不猜标题、不补事实,给出的每个字段都要能追回真账。
row = _row


def aggregate(sources: dict, *, scan: int = SESSION_SCAN) -> dict:
    """`sources` 是 {来源名: 无参函数},每个函数返回 row 列表(或抛异常)。

    某个来源抛了 → 那一行改口"没读到:原因",**不许整屏崩、也不许显示成"没有任务"**:
    读不到与没有是两件事,把它们画成同一屏就是这项目反复清退的那类假话。
    """
    rows: list[dict] = []
    failed: list[str] = []
    for name, fn in (sources or {}).items():
        try:
            got = fn() or []
        except Exception as exc:            # 一个来源坏了不影响别的
            failed.append(name)
            rows.append(_row("waiting", f"{name}:没读到",
                             f"{type(exc).__name__}: {exc}", source=name))
            continue
        for r in got:
            if isinstance(r, dict) and r.get("state") in STATES:
                r.setdefault("source", name)
                # B2(v0.9.102):行可带 ws(哪个工作区/副本);来源没给的按"全局"记
                # —— 全局一份的数据(定时任务这类)不属于任何副本,不该被算两遍。
                r.setdefault("ws", "全局")
                rows.append(r)
    groups, dropped = {}, {}
    for state in STATES:
        picked = [r for r in rows if r["state"] == state]
        groups[state] = picked[:ROW_LIMIT]
        dropped[state] = max(0, len(picked) - ROW_LIMIT)
    return {"ok": True, "at": time.strftime("%H:%M:%S"),
            "groups": groups, "counts": {s: sum(1 for r in rows if r["state"] == s)
                                         for s in STATES},
            "more": dropped, "failed_sources": failed,
            "scan": scan, "only": len(rows),
            "scan_note": f"只列最近 {scan} 个会话(不是全部)",
            "boundary": BOUNDARY_NOTE,
            "empty": not rows}
