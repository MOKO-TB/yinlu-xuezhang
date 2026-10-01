"""定时任务底座 A0/A1:任务表、到期判定、"上次何时跑"的账本(v0.9.42 起)。

设计文档在 `docs/roadmap-v0.9.42-定时任务底座.md`。这一层**只有纯函数 + 一个 JSON 账本**,
不起线程 —— 接线(A2)于 v0.9.56 落在 `proactive._run_due_jobs`:`_tick`(5 分钟)与
`_round`(6 小时)两条心跳各过一遍到期任务;这个模块本身仍然不碰任何线程。

为什么要这一层:`proactive.py` 里的心跳其实早就齐了(6 小时一轮的主循环 + 5 分钟一轮的
细轮询 + `pushed-today.json` 按天账本 + `watchdog.log` 现场)。缺的是**任务本身的记录**:
"这件事多久跑一次、上次什么时候跑的"。所以从前每加一种周期(周复盘=每周、缺口周报=
每周日、循环待办=每 N 天),都得在 `proactive.py` 里新写一段 if 再自造一个去重键,
加到第四种时那段循环自己就成了热点。

三条口径跟着仓库既有规矩走,不自创:
- **宁可少跑一次,不要多刷屏**:`last_run` 在未来(时钟回拨、账本被手改)一律判"没到期";
  账本读不出来就当没有记录(方向是"到点会跑一次",不是崩)。
- **写失败不谎报**:`mark_run` 返回布尔,调用方据此决定要不要说"已记录"。
- **日志只记结论**:`wd_log` 那本照旧不记任务正文,与提醒看门狗同口径。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from minicode.paths import app_path

STATE_FILE = "jobs-state.json"
TIME_FMT = "%Y-%m-%d %H:%M:%S"
CADENCES = ("daily", "weekly", "every_days", "interval", "on_demand")


@dataclass(frozen=True)
class JobSpec:
    """一个周期任务长什么样。`cadence` 之外全是可选的"到点"约束。"""

    id: str
    cadence: str = "daily"
    weekday: int | None = None      # 0=周一 … 6=周日(weekly 必填)
    hour: int | None = None         # 几点之后才算到期;None = 当天任何时候
    minute: int = 0
    every: int = 1                  # every_days 的天数 / interval 的分钟数
    on_boot_catchup: bool = True    # 开机时补不补今天漏掉的那一次
    # 给人看的名(跨会话看板那一行用它):没有就退回 id。加字段是补账,
    # 不是改行为 —— 老代码一律按 id 记账与调度,一块表都不动。
    label: str = ""

    def __post_init__(self) -> None:
        if self.cadence not in CADENCES:
            raise ValueError(
                f"不认识的 cadence {self.cadence!r};可用:{'/'.join(CADENCES)}")
        if self.cadence == "weekly" and self.weekday not in range(7):
            raise ValueError("weekly 任务必须给 weekday=0..6,否则永远等不到那一天")
        if self.cadence in ("every_days", "interval") and int(self.every) < 1:
            raise ValueError(f"{self.cadence} 的 every 要 >= 1(0 会变成每刻都跑)")
        if self.hour is not None and not 0 <= int(self.hour) <= 23:
            raise ValueError("hour 要在 0..23")
        if not 0 <= int(self.minute) <= 59:
            raise ValueError("minute 要在 0..59")


# ---------------------------------------------------------------- A0 · 判定

def _week_start(moment: datetime) -> datetime:
    """本周一 00:00 —— "本周跑过没有"的参照点。"""
    return (moment - timedelta(days=moment.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)


def due(spec: JobSpec, now: datetime, last_run: datetime | None) -> bool:
    """这个任务此刻该不该跑。不读盘、不起线程,喂两个时间就能判定(所以能喂文本测)。"""
    if spec.cadence == "on_demand":
        return False                      # 只由人或界面点名触发,永不自动到期
    if spec.cadence == "interval":
        if last_run is None:
            return True
        return (now - last_run).total_seconds() >= spec.every * 60
    if spec.hour is not None and (now.hour, now.minute) < (spec.hour, spec.minute):
        return False                      # 还没到点
    if last_run is None:
        return True
    if last_run > now:
        return False                      # 时钟回拨/账本被改:宁可少跑,不要刷屏
    if spec.cadence == "daily":
        return now.date() > last_run.date()
    if spec.cadence == "every_days":
        return (now.date() - last_run.date()).days >= spec.every
    # weekly:必须是那一天,而且本周还没跑过
    return now.weekday() == spec.weekday and last_run < _week_start(now)


def due_jobs(specs: list[JobSpec], now: datetime | None = None,
             state: dict | None = None) -> list[JobSpec]:
    """到期的任务。接线时(A2)唯一的调用面。"""
    moment = now or datetime.now()
    book = load_state() if state is None else state
    return [s for s in specs if due(s, moment, last_run(s.id, book))]


# ---------------------------------------------------------------- A1 · 账本

def state_path() -> Path:
    return app_path(STATE_FILE)


def load_state() -> dict:
    """读账本。文件不在、坏了、形状不对 —— 一律当"没有记录",绝不往外抛。

    坏了当没记录是**安全方向**:后果是到点的任务跑一次,不是看门狗崩在半路。
    """
    try:
        raw = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _parse(stamp) -> datetime | None:
    try:
        return datetime.strptime(str(stamp), TIME_FMT)
    except (TypeError, ValueError):
        return None


def last_run(job_id: str, state: dict | None = None) -> datetime | None:
    book = load_state() if state is None else state
    row = book.get(job_id)
    if not isinstance(row, dict):
        return None
    return _parse(row.get("last_run"))


def runs_of(job_id: str, state: dict | None = None) -> int:
    book = load_state() if state is None else state
    row = book.get(job_id)
    try:
        return int(row.get("runs") or 0) if isinstance(row, dict) else 0
    except (TypeError, ValueError):
        return 0


def mark_run(job_id: str, when: datetime | None = None, result: str = "") -> bool:
    """记一笔"这个任务跑过了",返回**是否真落盘**。

    写不进去要如实回 False:调用方据此说话,不能一边没记上一边告诉用户"已记录"——
    那样下一轮会重复跑,而重复跑周报这种东西就是刷屏。
    """
    moment = when or datetime.now()
    book = load_state()
    row = book.get(job_id) if isinstance(book.get(job_id), dict) else {}
    row = {**row, "last_run": moment.strftime(TIME_FMT),
           "runs": (runs_of(job_id, book) + 1), "result": str(result or "")[:200]}
    book[job_id] = row
    path = state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(book, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
    except OSError:
        return False
    return True


def log_outcome(spec: JobSpec, ran: bool, why: str = "") -> None:
    """往看门狗那本日志记一行结论。只记任务名与结论,不记正文(同提醒那套口径)。"""
    from minicode import proactive
    proactive.wd_log(f"job {spec.id}: {'跑了' if ran else '未跑'}"
                     + (f"[{why}]" if why else ""))


# ---------------------------------------------------------------- 可观测(v0.9.79)

_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def rhythm_text(spec: JobSpec) -> str:
    """节奏的人话:"每周日 20 点后" / "每 3 天" / "每天"。"""
    if spec.cadence == "weekly":
        base = f"每周{_WEEKDAYS[spec.weekday]}"
        return base + (f" {spec.hour} 点后" if spec.hour is not None else "")
    if spec.cadence == "daily":
        return "每天" + (f" {spec.hour} 点后" if spec.hour is not None else "")
    if spec.cadence == "every_days":
        return f"每 {spec.every} 天"
    if spec.cadence == "interval":
        return f"每 {spec.every} 分钟"
    return "按需触发"


def snapshot(specs: list[JobSpec], state: dict | None = None,
             now: datetime | None = None) -> str:
    """回答「这周的任务跑没跑、为什么没跑」:每个任务一段人话,纯函数。

    数据源是 jobs-state.json 那本账(mark_run 写的 last_run/result/runs)+
    此刻的 due() 判定;**结论原文随行摆出**("已推"/"没记就不打扰"/"异常 X")
    —— handler 异常算跑过这一班是 A2 的拍板(宁缺不刷屏),但"跑失败了"
    必须能被问出来,不能只躺在日志里。
    """
    moment = now or datetime.now()
    book = load_state() if state is None else state
    lines = [f"定时任务 {len(specs)} 个:"]
    for spec in specs:
        row = book.get(spec.id) if isinstance(book.get(spec.id), dict) else {}
        last = _parse(row.get("last_run"))
        result = str(row.get("result") or "")
        runs = runs_of(spec.id, book)
        if last is None:
            ran_line = "从未跑过"
        else:
            failed = result.startswith("异常")
            ran_line = (f"上次 {last.strftime('%m-%d %H:%M')} 跑过"
                        + (f",{'上次跑失败:' if failed else '结论:'}{result}"
                           if result else "")
                        + f",共 {runs} 次")
        if due(spec, moment, last):
            state_line = "现在已到期,下一次心跳(≤5 分钟)就会跑"
        elif spec.cadence == "weekly":
            day = _WEEKDAYS[spec.weekday]
            if last and last >= _week_start(moment):
                state_line = f"本周{day}那一班已跑过"
            else:
                state_line = f"本周还没到{day}那一班"
        else:
            state_line = "还没到点"
        lines.append(f"- {spec.id}({rhythm_text(spec)}):{ran_line};{state_line}")
    lines.append("机器没开机的那一班会跳过,下一班自然再来;上面的结论不含任务正文。")
    return "\n".join(lines)
