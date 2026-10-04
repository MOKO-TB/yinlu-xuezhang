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
    # 同学自己登记的任务要跑的那句话(v0.9.114,图 p5/p6)。代码里登记的那几个
    # 用 handler 函数,这一份是"把这句话交给学长跑"—— 空串 = 不是用户任务。
    prompt: str = ""

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
             state: dict | None = None, disabled=None) -> list[JobSpec]:
    """到期的任务。接线时(A2)唯一的调用面。

    `disabled=None` → 读本机那份开关:用户在「自动化」面板里关掉的不该再自己跑。
    显式传 `[]` = "我要看什么都没关时它会到期什么"(测试与命令行用)。
    """
    moment = now or datetime.now()
    book = load_state() if state is None else state
    off = set(read_disabled() if disabled is None else disabled)
    return [s for s in specs
            if s.id not in off and due(s, moment, last_run(s.id, book))]


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


# ------------------------------------------------------ 界面那一屏(v0.9.112 自动化)
#
# 这之前任务表在本机只有两种问法:命令行里 `desktop_notify action=jobs` 念一段人话,
# 和跨会话看板那几行(而且**从没跑过的任务整条不列**)。用户要的「自动化」面板要的是
# 结构化的一份清单:有哪几个、按什么节奏、下一次大概什么时候、上次跑出了什么、
# 能不能关掉。下面这几个函数只做一件事 —— 把那五问从现成的账与 spec 里读出来,
# 不新起第二份真相(节奏、到期判定、结论全都复用上面那几个函数)。

PREF_FILE = "jobs-pref.json"


def pref_path() -> Path:
    return app_path(PREF_FILE)


def read_disabled() -> list[str]:
    """这台机器上被关掉的任务 id。读不出来 = 一个都没关(方向:照登记跑)。"""
    try:
        raw = json.loads(pref_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    ids = raw.get("disabled") if isinstance(raw, dict) else None
    return [str(x) for x in ids] if isinstance(ids, list) else []


def set_enabled(job_id: str, on: bool) -> dict:
    """开/关一个定时任务。写完之后**读回来**再说成没成 —— 写不进去不能假成功。

    关掉的效力只有一处:`due_jobs` 不再把它算作到期。记账、看板、这一屏都还列得出它,
    所以"关掉了"不等于"消失了",用户能随时再开回来。
    """
    off = set(read_disabled())
    if on:
        off.discard(str(job_id))
    else:
        off.add(str(job_id))
    want = {"disabled": sorted(off),
            "_说明": "这些定时任务不再自动到期跑;记账与界面仍然列得出来。"
                   "删掉这个文件 = 全部恢复。"}
    try:
        pref_path().write_text(json.dumps(want, ensure_ascii=False, indent=1),
                               encoding="utf-8")
    except OSError as e:
        return {"ok": False, "disabled": sorted(off),
                "msg": f"本机偏好写不进去({type(e).__name__}),这次开关没生效"}
    return {"ok": True, "disabled": read_disabled(), "msg": ""}


# ---------------------------------------------------------------- 用户自己登记的任务
#
# 图 p5/p6 点名的缺口:这一屏以前只能"看 + 关",要加一条得改 Python。
# 存 `jobs-user.json`(本机个人数据 —— 进 .gitignore 与打包清理清单),
# 开关沿用上面那一份 `jobs-pref.json` 的 disabled(一套机制,不新起第二份)。

USER_FILE = "jobs-user.json"
MAX_USER_JOBS = 20
MAX_PROMPT = 2000


def user_path() -> Path:
    return app_path(USER_FILE)


def load_user() -> list[dict]:
    """同学自己加的任务定义。读不出来 = 一条都没有(不猜、不建默认)。"""
    try:
        raw = json.loads(user_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = raw.get("items") if isinstance(raw, dict) else None
    return [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []


def save_user(items: list[dict]) -> tuple[bool, str]:
    """原子写(.tmp → replace):这是同学自己配的清单,写坏了没有第二份能恢复。"""
    try:
        user_path().parent.mkdir(parents=True, exist_ok=True)
        tmp = user_path().with_name(user_path().name + ".tmp")
        tmp.write_text(json.dumps(
            {"items": items,
             "_说明": "你自己登记的定时任务。删掉这个文件就全没了;"
                     "代码里登记的那几个不受影响,开关在 jobs-pref.json。"},
            ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(user_path())
    except OSError as e:
        return False, f"写不进去({type(e).__name__}),本机原有清单保持不动。"
    return True, ""


def spec_of(row: dict) -> "JobSpec | None":
    """一行清单 → JobSpec。坏行回 None,由调用方如实报数 —— 不替同学猜一个节奏。"""
    try:
        wd = row.get("weekday")
        hr = row.get("hour")
        return JobSpec(id=str(row.get("id") or ""),
                       cadence=str(row.get("cadence") or "daily"),
                       weekday=None if wd in (None, "") else int(wd),
                       hour=None if hr in (None, "") else int(hr),
                       minute=int(row.get("minute") or 0),
                       every=int(row.get("every") or 1),
                       label=str(row.get("label") or ""),
                       prompt=str(row.get("prompt") or ""))
    except (ValueError, TypeError):
        return None


def user_specs() -> list[JobSpec]:
    return [s for s in (spec_of(r) for r in load_user()) if s is not None]


def add_user(label: str = "", prompt: str = "", cadence: str = "daily",
             weekday=None, hour=None, minute=0) -> dict:
    """加一条自己的定时任务:**先按 JobSpec 校验、过了才落盘**(校验不过一个字不写)。"""
    text = str(prompt or "").strip()
    name = str(label or "").strip()[:40]
    if not name:
        return {"ok": False, "msg": "先给这条任务起个名 —— 列表里要点的是它。"}
    if not text:
        return {"ok": False, "msg": "要学长做什么还没写 —— 空任务我不替你编。"}
    if len(text) > MAX_PROMPT:
        return {"ok": False, "msg": f"内容 {len(text)} 字,超过上限 {MAX_PROMPT} 字。"}
    rows = load_user()
    if len(rows) >= MAX_USER_JOBS:
        return {"ok": False, "msg": f"最多 {MAX_USER_JOBS} 条自己的任务,先删一条再加。"}
    try:
        spec = JobSpec(id="_check", cadence=str(cadence or "daily"),
                       weekday=None if weekday in (None, "") else int(weekday),
                       hour=None if hour in (None, "") else int(hour),
                       minute=int(minute or 0), label=name, prompt=text)
    except (ValueError, TypeError) as exc:
        return {"ok": False, "msg": f"时间没填对:{exc}"}
    ids = {str(r.get("id")) for r in rows}
    n = 1
    while f"u{n}" in ids:
        n += 1
    row = {"id": f"u{n}", "label": spec.label, "cadence": spec.cadence,
           "weekday": spec.weekday, "hour": spec.hour, "minute": spec.minute,
           "prompt": text, "created": datetime.now().strftime(TIME_FMT)}
    ok, msg = save_user(rows + [row])
    if not ok:
        return {"ok": False, "msg": msg}
    return {"ok": True, "id": row["id"],
            "msg": f"已加上「{spec.label}」({rhythm_text(spec)})—— "
                   "到点它会把你写的那句话交给学长跑一遍。"}


def remove_user(job_id: str) -> dict:
    """删一条自己的任务(代码登记的那几条删不掉,由 set_job_enabled 那边拒)。"""
    rows = load_user()
    left = [r for r in rows if str(r.get("id")) != str(job_id)]
    if len(left) == len(rows):
        return {"ok": False, "msg": f"你自己登记的任务里没有 {job_id} —— 没删任何东西。"}
    ok, msg = save_user(left)
    if not ok:
        return {"ok": False, "msg": msg}
    return {"ok": True, "msg": "已删掉这一条(记账里那几次的记录还留着)。"}


def next_due(spec: JobSpec, now: datetime,
             last: datetime | None) -> datetime | None:
    """下一次大概什么时候该跑。

    **算不出确切钟点的一律回 None**,由调用方说"下一次心跳"—— 没有 hour 的 daily
    只保证"当天",编一个 00:00 摆给人看是一句假话;on_demand 永远不会自己跑,更是 None。
    """
    if spec.cadence == "on_demand":
        return None
    if spec.cadence == "interval":
        base = last or now
        return base + timedelta(minutes=max(1, int(spec.every)))
    if spec.hour is None:
        return None
    at = now.replace(hour=spec.hour, minute=spec.minute, second=0, microsecond=0)
    if spec.cadence == "daily":
        return at if at > now else at + timedelta(days=1)
    if spec.cadence == "every_days":
        base = last or now
        cand = base.replace(hour=spec.hour, minute=spec.minute, second=0,
                            microsecond=0) + timedelta(days=max(1, int(spec.every)))
        return cand if cand > now else cand + timedelta(days=1)
    # weekly:往后数到那一天(最多七天),钟点过了就再等一周
    for back in range(0, 8):
        cand = (now + timedelta(days=back)).replace(
            hour=spec.hour, minute=spec.minute, second=0, microsecond=0)
        if cand.weekday() == spec.weekday and cand > now:
            return cand
    return None


def facts(specs: list[JobSpec], now: datetime | None = None,
          state: dict | None = None, disabled=None) -> list[dict]:
    """给「自动化」那一屏的结构化清单(顺序 = 登记顺序,不按时序排)。"""
    moment = now or datetime.now()
    book = load_state() if state is None else state
    off = set(read_disabled() if disabled is None else disabled)
    rows = []
    for spec in specs:
        entry = book.get(spec.id) if isinstance(book.get(spec.id), dict) else {}
        last = _parse(entry.get("last_run"))
        nxt = next_due(spec, moment, last)
        rows.append({
            "id": spec.id,
            "label": spec.label or spec.id,
            "cadence": spec.cadence,
            "rhythm": rhythm_text(spec),
            # 这条是同学自己加的还是代码登记的:界面据此才敢给「删掉」那颗
            # (内置的那几条删不掉,给了就是引导人点一次失败)。
            "user": bool(getattr(spec, "prompt", "")),
            "hour": spec.hour, "minute": spec.minute,
            "weekday": spec.weekday, "every": spec.every,
            "enabled": spec.id not in off,
            "due_now": (spec.id not in off) and due(spec, moment, last),
            "next": (nxt.strftime("%m-%d %H:%M") if nxt else ""),
            "next_hint": ("按需触发,不会自己跑" if spec.cadence == "on_demand"
                          else "当天到点前(没有固定钟点)" if nxt is None else ""),
            "last": last.strftime("%Y-%m-%d %H:%M") if last else "",
            "runs": runs_of(spec.id, book),
            "result": str(entry.get("result") or ""),
        })
    return rows
