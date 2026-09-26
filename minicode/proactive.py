"""主动发现(主动推荐):后台看门狗把「值得此刻知道的事」推给界面。

两条线,全部只说真查到的东西,绝不编造"你可能想做的事":
- 官网新通知:启动后拉默认栏目页(教务处/学生处通知公告,博达站群静态页,
  httpx+bs4 可解析),与本地快照(notice-watch.json)diff,有新条目才推,
  推一次就记入快照 —— 同一批通知永远只打扰一次;
- 校历临近:kb/school-calendar.json 里 7 天内的事件,每次启动至多提醒一回。

三条纪律:独立 daemon 线程绝不阻塞启动;断网/改版/任何异常一律静默跳过
(锦上添花的功能不能变成报错源);用户可以一键关(notice-watch.json.enabled)。

v0.9.25 起这些推送除了落在聊天顶部那条提示栏,还会同步弹一条 **Windows 桌面通知**
(窗口最小化时才有意义):正文由 `desktop_text()` 从**同一份 payload** 生成,
不另起一套文案;窗口正显示且有焦点时默认不重复弹。"绝不编造"同样管这里 ——
payload 里没有课名/时间/地点就整条不发。
"""
from __future__ import annotations

import re
import threading
import time
from datetime import date
from pathlib import Path

from minicode.paths import app_path

WATCH_FILE = "notice-watch.json"
FIRST_DELAY_SECONDS = 15        # 启动后先让界面稳住,再开始第一轮检查
DEFAULT_INTERVAL_HOURS = 6
MAX_ITEMS_PER_SOURCE = 12
SEEN_CAP = 120                  # 每个源最多记这么多已见标题(旧的淘汰)

# 默认看门狗源:教务处/学生处的「通知公告」列表页(博达站群,静态可解析;
# 2026-09-19 实测 200,锚点结构 a>div(日期)+div(标题+摘要))。改版失效时
# 该源静默跳过,不报错;维护者可改 notice-watch.json 换源。
DEFAULT_SOURCES = [
    {"name": "教务处通知", "url": "https://www.gzasc.edu.cn/jwc/xwdt/tzgg.htm"},
    {"name": "学生处通知", "url": "https://www.gzasc.edu.cn/xsc/tzgg.htm"},
]

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# 通知标题的常见结尾:标题截断启发式用(列表页锚文本里混着日期与正文预览)
_TITLE_ENDINGS = ("考生须知", "须知", "通知", "公示", "名单", "安排", "方案",
                  "细则", "办法", "公告", "邀请函", "倡议书", "指南", "要求")
_DATE_PREFIX = re.compile(r"^\s*\d{2}-\d{2}\s+\d{4}\s*")


def state_path() -> Path:
    return app_path(WATCH_FILE)


def load_state() -> dict:
    """读看门狗状态;没有/坏了给默认(开关开、默认源、空快照)。"""
    import json
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
        if isinstance(data, dict) and "sources" in data:
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"enabled": True, "interval_hours": DEFAULT_INTERVAL_HOURS,
            "last_check": 0, "sources": [dict(s) for s in DEFAULT_SOURCES],
            "seen": {}}


def save_state(state: dict) -> None:
    import json
    try:
        state_path().parent.mkdir(parents=True, exist_ok=True)
        state_path().write_text(
            json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass          # 状态写不进去就下次重新 diff,功能不受影响


# -------------------------------------------------- 今天已经推过哪些提醒(按天)
#
# 为什么单独一份:`start_tick_loop`(v0.9.29 前叫 `start_class_loop`)的去重集原来是
# **内存里的 set**,`last_day`
# 也是内存的 —— 一重启就清空。于是两种失败同时发生:①8:40 那条课前提醒没赶上
# (机器开着机/学长没在跑),重启后**永远不会补**;②8:50 重启,同一节课**再弹一次**。
# 落盘 + 按天滚动同时解决这两件:补发知道谁没弹过,去重知道谁弹过了。
PUSHED_FILE = "pushed-today.json"
PUSHED_CAP = 200                      # 一天内最多记这么多条(防文件无限长)
_push_lock = threading.RLock()        # 两条看门狗线程都会碰它;mark_pushed 里
                                      # 还要再调 load_pushed → 必须是可重入锁
_mem_pushed: set[str] = set()         # 写不进文件时的兜底(至少本次运行不重复)
_mem_day = ""


def pushed_path() -> Path:
    return app_path(PUSHED_FILE)


def _day_key(day: "date | None" = None) -> str:
    return (day or date.today()).isoformat()


def _pushed_mem(day: str) -> list[str]:
    """内存兜底里今天的键 —— 只在账本写不进去时才有东西。

    取**快照**比对:另一条线程正跨天 `clear()` 时直接迭代会炸
    "Set changed size during iteration",而调用方是守护线程,炸了就是静默漏推。
    """
    with _push_lock:
        return [k for k in list(_mem_pushed) if k.startswith(f"{day}|")]


def load_pushed(day: str = "") -> list[str]:
    """今天已推过的提醒键,**只含落盘那一份**,顺序 = 推送顺序(最老的在前)。

    顺序是有意义的:`mark_pushed` 用 `keys[-PUSHED_CAP:]` 淘汰,混进无序的内存集
    会让淘汰丢掉随机几条而不是最老的,还会把已经淘汰掉的键复活。想知道"到底推没
    推过"用 `was_pushed()` —— 它两份都查。跨天、坏文件、缺字段一律给空
    (宁可重弹不可漏弹)。
    """
    import json
    day = day or _day_key()
    try:
        data = json.loads(pushed_path().read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("date") == day:
            return [k for k in (data.get("keys") or []) if isinstance(k, str)]
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return []


def was_pushed(kind: str, key: str = "", day: "date | None" = None) -> bool:
    """这条提醒今天推过了吗。`key` 空 = 按"类别 + 天"去重(如今日简报)。

    落盘账本和内存兜底**两份都查**:后者专门装"这次写不进文件但至少本次运行
    别重复弹"的那几条。
    """
    d = _day_key(day)
    full = f"{d}|{kind}|{key}"
    return full in load_pushed(d) or full in _pushed_mem(d)


def mark_pushed(kind: str, key: str = "", day: "date | None" = None) -> bool:
    """记下"这条推过了",返回**是否真的落了盘**。

    写不进文件(只读目录、装在 Program Files 里)时退回内存集并如实返回 False
    —— 本次运行仍然不会重复弹,只是重启后可能再弹一次,不能假装已经记住了。
    写成功就把内存集清空:兜底的那几条已经跟着这次一起落盘了,留着反而会让
    账本里淘汰掉的键被复活。
    """
    import json
    global _mem_day
    d = _day_key(day)
    full = f"{d}|{kind}|{key}"
    with _push_lock:
        if _mem_day != d:
            _mem_pushed.clear()
            _mem_day = d
        keys = load_pushed(d)
        for k in _pushed_mem(d) + [full]:          # 兜底过的键一起补进账本
            if k not in keys:
                keys.append(k)
        keys = keys[-PUSHED_CAP:]
        try:
            pushed_path().parent.mkdir(parents=True, exist_ok=True)
            tmp = pushed_path().with_suffix(".tmp")
            tmp.write_text(json.dumps({"date": d, "keys": keys},
                                      ensure_ascii=False, indent=1),
                           encoding="utf-8")
            tmp.replace(pushed_path())
            _mem_pushed.clear()
            return True
        except OSError:
            _mem_pushed.add(full)
            return False


# ------------------------------------------------- 看门狗运行日志(v0.9.28 A 批)
#
# 为什么需要它:`start_loop` 的循环体是 `except Exception: pass`,6 小时一轮,
# 原来一整轮跑完**什么现场都不留** —— 用户报"课前提醒没发"或"同一条弹了三遍",
# 唯一能看的东西是内存里那几个已经随重启消失的 `*_done` 标志。这份日志只回答
# 三个问题:这一轮跑了哪几类、每类推没推、没推是哪一道闸门拦的(今天已推过 /
# 简报已覆盖 / 没内容 / 界面推炸 / 桌面没弹 / 账本没落盘 / 这轮抛异常)。
# **只记类别 + 去重键 + 结论,绝不记正文**(课名、通知标题是个人内容,而这份
# 日志是最可能被截图发给维护者看的文件)。
WATCHDOG_LOG = "watchdog.log"
LOG_CAP_LINES = 400              # 一轮十几行,400 行 ≈ 几天;超了只留最近的
LOG_TRIM_EVERY = 20              # 每写这么多行才回头收一次盘(不必每行重读文件)
_log_lock = threading.Lock()
_logged_since_trim = 0


def watchdog_log_path() -> Path:
    return app_path(WATCHDOG_LOG)


def wd_log(msg: str) -> None:
    """记一行现场。写不进去就丢掉 —— 日志不能把提醒本身弄崩。"""
    global _logged_since_trim
    try:
        with _log_lock:
            path = watchdog_log_path()
            path.parent.mkdir(parents=True, exist_ok=True)      # 首启可能还没有任何文件
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
            _logged_since_trim += 1
            if _logged_since_trim >= LOG_TRIM_EVERY:
                _logged_since_trim = 0
                lines = path.read_text(encoding="utf-8").splitlines()
                if len(lines) > LOG_CAP_LINES:
                    tmp = path.with_suffix(".tmp")
                    tmp.write_text("\n".join(lines[-LOG_CAP_LINES:]) + "\n",
                                   encoding="utf-8")
                    tmp.replace(path)
    except Exception:
        pass


def wd_log_tail(lines: int = 30) -> list[str]:
    """看门狗日志最后几行;没有日志回空列表(不是报错)。"""
    try:
        rows = watchdog_log_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return rows[-max(1, int(lines)):]


def _desktop_outcome(result: dict) -> str:
    """把 push_desktop 的返回值拍成日志里那几个字,别把整坨 dict 写进日志。"""
    if result.get("ok"):
        return f"桌面{'已弹' if result.get('verified') else '交给系统'}({result.get('channel') or '?'})"
    return f"桌面没弹({result.get('skipped') or result.get('msg') or '?'})"


def clean_title(raw: str) -> str:
    """从列表页锚文本里提出干净标题:剥日期前缀,在通知类结尾词处截断。

    博达站群的锚文本是「日期 + 标题 + 正文预览」连在一起,而正文往往以标题
    同款开头,无法按重复切;这里按通知文种的结尾词截断,截不出就取前 30 字。
    """
    text = _DATE_PREFIX.sub("", str(raw or "").strip())
    text = re.sub(r"\s+", " ", text)
    window = text[:48]
    # 取**最早**出现的结尾词:标题先于正文,正文里常再出现"办法/安排"等词,
    # 取最晚会把截断点推过标题(实测"…开课的通知 各二级学院:…管理办法")。
    best: tuple[int, str] | None = None
    for ending in _TITLE_ENDINGS:
        pos = window.find(ending)
        if pos >= 0 and (best is None or pos < best[0]):
            best = (pos, ending)
    if best is not None and best[0] + len(best[1]) > 6:
        return text[:best[0] + len(best[1])].strip()
    return (text[:30] + "…") if len(text) > 30 else text


def _norm_notice_date(raw: str) -> str:
    """列表页日期「09-11 2026」→「2026-09-11」;认不出返回空串(不编)。"""
    nums = re.findall(r"\d+", str(raw or ""))
    if len(nums) >= 3:
        mm, dd, yyyy = nums[0], nums[1], nums[2]
        if len(yyyy) == 4 and 1 <= int(mm) <= 12 and 1 <= int(dd) <= 31:
            return f"{yyyy}-{int(mm):02d}-{int(dd):02d}"
    return ""


def fetch_latest(url: str, timeout: float = 12.0) -> list[dict]:
    """拉一个栏目列表页,返回 [{date, title, url}](本页可见的通知条目)。

    只认 /info/ 深链的锚点(正文页),导航/翻页链接一律不要。
    任何网络/解析异常向上抛,由 check_once 统一吞掉。
    date 是列表页标注的发布日期(站群模板第一个 div);没有结构化日期时留空
    —— 宁可没有,不拿"看不出日期"硬凑一个。
    """
    import httpx
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin

    resp = httpx.get(url, headers={"User-Agent": _UA}, timeout=timeout,
                     follow_redirects=True)
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}")
    soup = BeautifulSoup(resp.text, "html.parser")
    items: list[dict] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a.get("href")
        if not isinstance(href, str) or "/info/" not in href:
            continue
        full = urljoin(str(resp.url), href)
        if not full.startswith("http") or full in seen:
            continue
        divs = a.find_all("div")
        date = ""
        if len(divs) >= 2:
            head = divs[0].get_text(" ", strip=True)
            if _DATE_PREFIX.match(head):
                date = _norm_notice_date(head)
                raw = divs[1].get_text(" ", strip=True)   # 站群模板:日期div+标题div
            else:
                raw = a.get_text(" ", strip=True)
        else:
            raw = a.get_text(" ", strip=True)
        title = clean_title(raw)
        if len(title) < 8:
            continue
        seen.add(full)
        items.append({"date": date, "title": title, "url": full})
        if len(items) >= MAX_ITEMS_PER_SOURCE:
            break
    return items


def check_once(state: dict | None = None) -> list[dict]:
    """查一轮所有源,返回**新**通知(并更新快照与 last_check)。

    同一批新条目只返回一次:推过就进 seen。某个源挂了只跳过它,不影响别的源;
    全部失败时不动 last_check(下次启动/下轮再试)。
    """
    state = state if state is not None else load_state()
    ok_sources = 0
    fresh: list[dict] = []
    reported: set[str] = set()          # 跨源同名只报一次(同通知挂两个栏目页)
    for source in state.get("sources") or []:
        url = str(source.get("url") or "")
        name = str(source.get("name") or "官网通知")
        if not url.startswith("http"):
            continue
        try:
            latest = fetch_latest(url)
        except Exception:
            continue                       # 断网/改版:静默跳过这个源
        ok_sources += 1
        seen = set((state.setdefault("seen", {}).get(url) or {}).get("titles")
                   or [])
        for item in latest:
            if item["title"] in seen or item["title"] in reported:
                continue
            reported.add(item["title"])
            fresh.append({**item, "source": name})
        seen.update(i["title"] for i in latest)
        titles = sorted(seen)[-SEEN_CAP:]
        state["seen"][url] = {"titles": titles,
                              "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    if ok_sources:
        state["last_check"] = time.time()
        save_state(state)
    return fresh


def calendar_alerts(today: date | None = None,
                    within_days: int = 7) -> list[dict]:
    """7 天内要到点的校历事件(倒计时提醒的数据源)。

    **只认学校定过的日子**:校历里 `repeat: yearly` 那些"按往年月日推出来的"下一次
    (calendar.expand_repeats 标的 projected)在这里一律剔掉。列表里带着口径展示没问题,
    但拿一个我们自己推的日子去"还有 3 天"主动推人,就是在替学校编日期。
    """
    from minicode import calendar as campus_calendar
    events, _src = campus_calendar.load_events()
    today = today or date.today()
    rows = [e for e in campus_calendar.upcoming(events, today, limit=3,
                                                within_days=within_days)
            if not e.get("projected")]
    return [{"name": str(e.get("name") or ""),
             "start": str(e.get("start") or ""),
             "days_left": int(e.get("days_left") or 0)} for e in rows]


# ---------------------------------------------------------------- 新版本检查

RELEASE_API = "https://gitee.com/api/v5/repos/inkzero/momo/releases/latest"


def _ver_tuple(text: str) -> tuple[int, ...]:
    import re
    nums = re.findall(r"\d+", str(text or ""))[:3]
    return tuple(int(n) for n in nums) or (0,)


def check_update(current: str, timeout: float = 10.0) -> dict | None:
    """查 gitee 公开仓库的最新 release,比当前版本新才返回;失败一律 None。

    维护者在 gitee 发 release(tag 形如 v0.9.17)后,同学那边的启动检查就会亮。
    """
    import httpx
    try:
        resp = httpx.get(RELEASE_API, timeout=timeout)
    except Exception:
        return None
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except Exception:
        return None
    tag = str(data.get("tag_name") or data.get("name") or "").strip()
    url = str(data.get("html_url") or "")
    if not tag or _ver_tuple(tag) <= _ver_tuple(current):
        return None
    return {"version": tag.lstrip("vV"), "url": url,
            "notes": str(data.get("body") or "").strip()[:120]}


# ---------------------------------------------------------------- 桌面通知文案

# 标题只说"这是哪一类提醒",内容全在正文里;正文能复用聊天那行 text 就复用,
# 免得同一件事两处各写一遍、日后各说各话。
KIND_TITLES = {"briefing": "今日简报", "class": "快上课了", "schedule": "今天的课",
               "reminders": "待办临期", "calendar": "校历提醒",
               "notices": "校园新通知", "update": "引路学长有新版本",
               "weather": "天气提醒"}


def desktop_text(kind: str, payload: dict) -> str | None:
    """把一条主动推送拍成桌面通知正文;拍不出来就回 None(**不发**)。

    只准用 payload 里真实存在的字段:课名、时间、地点这些缺了就整条不发,
    绝不为了"通知看着饱满"编一个出来 —— 桌面通知比聊天消息更容易被当真。
    """
    text = str(payload.get("text") or "").strip()
    if text:
        return text
    if kind == "reminders":
        items = payload.get("items") or []
        if not items:
            return None
        first = items[0] if isinstance(items[0], dict) else {}
        title = str(first.get("title") or "").strip()
        if not title:
            return None
        due = (str(first.get("due_at") or "").replace("T", " ").strip()
               or str(first.get("due") or "").strip())
        left = first.get("days_left")
        tail = ""
        if due and isinstance(left, int):
            tail = f"({due}" + (f",还有 {left} 天" if left >= 0 else f",已逾期 {-left} 天") + ")"
        more = f" 等 {payload.get('total') or len(items)} 件" if len(items) > 1 else ""
        return f"{title}{tail}{more}"
    if kind == "calendar":
        name = str(payload.get("name") or "").strip()
        days = payload.get("days_left")
        if not name or not isinstance(days, int):
            return None
        return f"{name}:还有 {days} 天({payload.get('start') or '日期见校历'})"
    if kind == "notices":
        items = payload.get("items") or []
        if not items:
            return None
        first = items[0] if isinstance(items[0], dict) else {}
        title = str(first.get("title") or "").strip()
        if not title:
            return None
        src = str(first.get("source") or "官网").strip() or "官网"
        total = payload.get("total") or len(items)
        return f"{src}:{title}" + (f" 等 {total} 条新通知" if total > 1 else "")
    if kind == "update":
        version = str(payload.get("version") or "").strip()
        return f"v{version} 已发布,去下载更新" if version else None
    return None


def push_desktop(kind: str, payload: dict) -> dict:
    """把一条推送同步发到桌面;任何失败都不许冒出来影响聊天里的推送。"""
    try:
        from minicode import tray
        body = desktop_text(kind, payload)
        if not body:
            return {"ok": False, "skipped": "no_text"}
        return tray.notify(str(payload.get("title") or "").strip()
                           or KIND_TITLES.get(kind, "引路学长"), body, kind=kind)
    except Exception as e:
        return {"ok": False, "skipped": "error", "msg": f"{type(e).__name__}"}


def _push_proactive(bridge, kind: str, **payload) -> dict:
    """把一条推送同时发到「聊天顶部提示栏」和「桌面通知」,返回两路各自的结果。

    两路彼此独立:界面推炸了桌面照发,桌面炸了也不影响聊天里那条 —— 返回值
    只给日志用(v0.9.28),调用方原先都忽略它,所以改成有返回值不影响老代码。
    """
    ui_ok = True
    try:
        bridge.ui.push("proactive", kind=kind, **payload)
    except Exception:
        ui_ok = False
    return {"ui": ui_ok, "desktop": push_desktop(kind, payload)}


def _note_payload(note: dict) -> dict:
    """课前提醒那条 dict 里的 `key` 是**去重键**,不是要显示的内容。

    `_push_once` 的 key 是位置参数,再把整个 note 当 `**payload` 传进去就会撞成
    "got multiple values for argument 'key'" —— 而且炸在守护线程里,表现为
    "课前提醒一条都没发",这正是本轮要消灭的那类故障。
    """
    return {k: v for k, v in note.items() if k != "key"}


def _push_once(bridge, kind: str, key: str, *, log_skip: bool = True,
               **payload) -> bool:
    """今天没推过的才推这一条,并把结论记进看门狗日志;返回是否真推了。

    去重键**按天**落在 `pushed-today.json`(v0.9.26 起有这份账本),所以:
    - 同一天内重启三次学长 → 只弹一条(原来重启一次就再来一条);
    - 进程连着开三天 → 每天各催一次(原来只在启动那一回提醒,第二天就哑了)。

    一条硬规矩:**什么都没送到用户手里时不占用今天这一格**(界面推炸且桌面
    也没弹)—— 否则这一类今天永远补不回来。

    `log_skip=False` 给**高频轮询**的调用方(课前那条线程每 5 分钟问一次):
    已经推过之后的每次"跳过"都记一行,一天几百行会把 400 行的日志上限刷满,
    真正有用的那几行反而被挤出去 —— 第一次的 `push class [...]` 已经说明问题了。
    """
    if was_pushed(kind, key):
        if log_skip:
            wd_log(f"skip {kind} [{key}]:今天已推过")
        return False
    r = _push_proactive(bridge, kind, **payload)
    desk = r["desktop"]
    if not r["ui"] and not desk.get("ok"):
        wd_log(f"fail {kind} [{key}]:两路都没送到({_desktop_outcome(desk)}),"
               "不占用今天这一格")
        return False
    line = (f"push {kind} [{key}]:提示栏{'已推' if r['ui'] else '推失败'} · "
            f"{_desktop_outcome(desk)}")
    if mark_pushed(kind, key):
        wd_log(line)
    else:
        wd_log(line + " · 账本没落盘(重启可能再弹)")
    return True


def briefing_pushed_today(day: "date | None" = None) -> bool:
    """今天的简报是否已经推过(重启/重开一轮都不该再来一条,也不该被当成没推)。"""
    return was_pushed("briefing", _day_key(day), day=day)


def push_briefing(bridge, force: bool = False) -> bool:
    """推一次今日简报;返回是否推成功(调用方据此跳过重复的单条推送)。

    聚合失败/无内容一律 False,主循环照旧单条推 —— 简报是加分项,不能
    因为它的异常把原有提醒全吞掉。

    **每天最多一条**(`pushed-today.json` 按天去重):去重集原来在内存里,
    所以"上午开过一次学长,下午再开又弹一条今日简报"是常态;开机补发也走这里,
    两条线程同时启动不会抢出两条(闸门在锁里判)。
    """
    key = _day_key()
    if not force and briefing_pushed_today():
        wd_log("skip briefing [今日]:账本说今天已推过")
        return False
    try:
        from minicode import briefing as _briefing
        b = _briefing.build()
    except Exception as e:
        wd_log(f"fail briefing [今日]:聚合抛异常 {type(e).__name__}(不占用今天)")
        return False
    if not b:
        wd_log("skip briefing [今日]:今天没课/没待办/没校历,不推空卡")
        return False
    with _push_lock:
        if not force and was_pushed("briefing", key):
            wd_log("skip briefing [今日]:另一条线程刚推过")
            return False         # 另一条线程刚推过
        mark_pushed("briefing", key)
    r = _push_proactive(bridge, "briefing", **b)
    wd_log(f"push briefing [今日]:提示栏{'已推' if r['ui'] else '推失败'} · "
           + _desktop_outcome(r["desktop"]))
    return True


def _push_weather(bridge) -> bool:
    """明天有雨的前一晚提醒:只读天气缓存,绝不为提醒联网(与今日简报同纪律)。

    缓存过期/没有就这一轮跳过 —— 6 小时后下一轮缓存大概率是新的,那时的判断
    才是站在真数据上的;"有雨"的口径与今天带伞那条同一条线(weather.tomorrow_rain,
    降水概率 ≥40% 或预报码是雨类),文案带"预报"字样,不下保证。
    去重键带明天的日期:今天推过,明天(以及同一天再开机)都不会再弹。
    """
    try:
        from minicode import weather as _weather
        data = _weather.cached_data()
        if not data:
            wd_log("skip weather [-]:天气缓存没有或已过期,不为提醒联网")
            return False
        tip = _weather.tomorrow_rain(data)
    except Exception as e:
        wd_log(f"fail weather [-]:{type(e).__name__} {e}")
        return False
    if not tip:
        return False
    when = str(tip.get("date") or "")[5:]
    pct = tip.get("pct")
    body = f"明天({when})预报有雨,今晚把伞放到包里"
    if pct is not None:
        body += f"(降水概率 {pct}%)"
    return _push_once(bridge, "weather", f"rain|{tip.get('date')}", text=body)


def _push_calendar(bridge) -> int:
    """校历倒计时:7 天内的事件,每个事件每天最多催一条。"""
    pushed = 0
    for alert in calendar_alerts():
        if _push_once(bridge, "calendar", f"{alert.get('name')}|{alert.get('start')}",
                      name=alert["name"], days_left=alert["days_left"],
                      start=alert["start"]):
            pushed += 1
    return pushed


def _push_reminders(bridge) -> bool:
    """临期/逾期待办:按「今天弹的是哪几件」去重 —— 清单变了就该再催一次。"""
    try:
        from minicode import reminders as _reminders
        near = _reminders.due_soon()
    except Exception as e:
        wd_log(f"fail reminders [{_day_key()}]:清单读不出来 {type(e).__name__}")
        return False
    if not near:
        return False
    shown = near[:5]
    key = "+".join(str(i.get("id", "")) for i in shown)
    return _push_once(bridge, "reminders", key, items=[
        {"id": i["id"], "title": i["title"], "due": i.get("due", ""),
         "due_at": i.get("due_at", ""),
         "days_left": i["days_left"], "note": i.get("note", "")} for i in shown])


def _push_today_class(bridge) -> bool:
    """今天/明天第一节课:去重键带上具体那一天,所以连着开机三天会催三天。"""
    try:
        from minicode import schedule as _schedule
        note = _schedule.next_class_note()
    except Exception as e:
        wd_log(f"fail schedule [{_day_key()}]:课表读不出来 {type(e).__name__}")
        return False
    if not note:
        return False
    return _push_once(bridge, "schedule", f"{note.get('day')}|{note.get('head')}",
                      **note)


def _push_class(bridge, now=None) -> bool:
    """课前 20 分钟那一条:内容全来自课表,课表没写钟点的课不参与。"""
    try:
        from minicode import schedule as _schedule
        note = _schedule.upcoming_class(now=now, lead_minutes=CLASS_LEAD_MINUTES)
    except Exception as e:
        wd_log(f"fail class [-]:{type(e).__name__} {e}")
        return False
    if not note:
        return False
    return _push_once(bridge, "class", note["key"], log_skip=False,
                      **_note_payload(note))


def _push_due(bridge, now=None) -> bool:
    """待办到点:`reminder` 记的事只要写了钟点,也走这条细轮询,和上课一个节奏。

    类别仍用 `reminders` 而不是新起一个 `due` —— 用户 `desktop_notify` 里能开关的
    那七类是既有事实,凭空多一类他关不掉(set_notify 认不出新 kind),而"到点催"
    和"临期催"本来就是一回事,只是精确程度不同。
    """
    try:
        from minicode import reminders as _reminders
        note = _reminders.upcoming_due(now=now)
    except Exception as e:
        wd_log(f"fail reminders [-]:{type(e).__name__} {e}")
        return False
    if not note:
        return False
    return _push_once(bridge, "reminders", note["key"], log_skip=False,
                      **_note_payload(note))


def _tick(bridge, now=None) -> dict:
    """细轮询一轮:课程与待办各自判、各自记日志,一边抛异常不拖累另一边。

    抽成函数是 v0.9.28 那条老规矩的延续:**循环体里的判断必须能直接测**,
    否则"到点没催"这种问题只能真的等到点。
    """
    return {"class": _push_class(bridge, now=now),
            "due": _push_due(bridge, now=now)}


def _round(bridge) -> dict:
    """看门狗一轮:每一类各自判「今天没推过才推」,结论都记进 watchdog.log。

    单独抽出来是**为了能直接测**:这些判断原来全埋在 `run()` 的 `while True` +
    `sleep(6h)` 里,想验一条"重启后会不会再弹一遍"只能真的反复开关窗口 ——
    这就是"提醒出问题难定位"的根。

    今日简报排最前:今天推过简报(本次推的、或上一回运行推的),课表/待办/校历
    三条单条就不再重复打扰 —— 内容已经在卡里了。
    """
    out: dict[str, object] = {}
    briefing_covered = briefing_pushed_today() or push_briefing(bridge)
    out["briefing"] = briefing_covered
    state = load_state()
    if state.get("enabled", True):
        if briefing_covered:
            wd_log("skip calendar/reminders/schedule [今日]:今日简报已覆盖")
        else:
            out["calendar"] = _push_calendar(bridge)
        items = check_once(state)          # 新通知按"已见标题"去重(另一份持久快照)
        if items:
            r = _push_proactive(bridge, "notices", items=items[:4],
                                total=len(items))
            out["notices"] = len(items)
            wd_log(f"push notices [{len(items)} 条]:提示栏"
                   f"{'已推' if r['ui'] else '推失败'} · " + _desktop_outcome(r["desktop"]))
        out["weather"] = _push_weather(bridge)     # 明天有雨(独立于简报:简报管今天)
    from minicode import __version__
    update = check_update(__version__)     # 内部已把断网/改版全吞成 None
    if update:
        out["update"] = _push_once(bridge, "update",
                                   str(update.get("version") or ""), **update)
    if not briefing_covered:
        out["reminders"] = _push_reminders(bridge)
        out["schedule"] = _push_today_class(bridge)
    return out


def start_loop(bridge, interval_hours: float | None = None) -> None:
    """启动后台看门狗线程(gui_main 调一次;测试/CLI 不调它就不存在)。

    这里只管延迟、节奏和"别把窗口弄崩",一轮里做什么全在 `_round()`。
    """

    def run() -> None:
        time.sleep(FIRST_DELAY_SECONDS)
        hours = interval_hours or DEFAULT_INTERVAL_HOURS
        wd_log(f"loop start [间隔 {hours}h]")
        while True:
            try:
                _round(bridge)
            except Exception as e:
                # 看门狗的任何意外都不许冒出来影响窗口,**但必须留下一行** ——
                # 静默吞掉异常正是"提醒没发却查不到"的那半边原因。
                wd_log(f"round error [-]:{type(e).__name__} {e}")
            time.sleep(max(1, float(hours)) * 3600)

    thread = threading.Thread(target=run, daemon=True,
                              name="yinluxs-proactive")
    thread.start()


CLASS_TICK_SECONDS = 5 * 60         # 课前提醒单独一条细粒度轮询,不打扰 6 小时主循环节奏
CLASS_LEAD_MINUTES = 20             # 开课前多久提醒(一节课通常 40-45 分钟,20 分钟够从宿舍出门)


def catch_up(bridge) -> dict:
    """开机补发(启动后第一轮做一次):把"今天该知道但没赶上"的三件补上。

    - **今日简报**:今天从来没推过就补一条(早上没开机,下午打开学长还看得到
      今天几节课、要不要带伞)。已经推过就绝不再来一条。
    - **错过的课**:有节课**已经开课但还在补发窗口内**(默认 60 分钟)且没提醒过,
      补一条「错过的提醒」。同一节课与到点提醒共用去重键,先弹了"快上课了"
      就不会再弹这条,反过来也一样。
    - **刚过期的待办**:同上,只不过对象是 `reminder` 里写了钟点的那几条
      (「17:00 前交」结果 17:30 才打开学长)。已经到点催过的不再补。

    只做这三件,不把过去每节课/每条逾期都补一遍(那只会变成刷屏);任何异常
    静默 —— 补发是加分项,不能把看门狗弄崩。
    """
    out = {"briefing": False, "class": "", "due": ""}
    try:
        out["briefing"] = push_briefing(bridge)
    except Exception as e:
        wd_log(f"fail catch_up [简报]:{type(e).__name__}")
    try:
        from minicode import schedule as _schedule
        note = _schedule.late_class_note()
        if not note:
            wd_log("skip catch_up [错过的课]:没有已开课且还在补发窗口内的课")
        elif was_pushed("class", note["key"]):
            wd_log(f"skip catch_up [{note['key']}]:到点提醒已弹过")
        elif _push_once(bridge, "class", note["key"], **_note_payload(note)):
            out["class"] = note["key"]
    except Exception as e:
        wd_log(f"fail catch_up [错过的课]:{type(e).__name__}")
    try:
        from minicode import reminders as _reminders
        note = _reminders.late_due()
        if not note:
            wd_log("skip catch_up [过期待办]:没有刚过期还在窗口内的事项")
        elif was_pushed("reminders", note["key"]):
            wd_log(f"skip catch_up [{note['key']}]:到点提醒已弹过")
        elif _push_once(bridge, "reminders", note["key"], **_note_payload(note)):
            out["due"] = note["key"]
    except Exception as e:
        wd_log(f"fail catch_up [过期待办]:{type(e).__name__}")
    return out


def start_tick_loop(bridge) -> None:
    """到点提醒线程:每 ~5 分钟看一次「有没有课在 20 分钟内开课 / 有事在 20 分钟内到期」。

    v0.9.29 起这条线程不再只盯着课表:`reminder` 里写了钟点的待办也走同一个节奏
    (见 `_tick`)。与 start_loop 分开跑(主循环 6 小时一次,太粗做不到"快上课了")。

    去重键落盘在 `pushed-today.json`(按天滚动),**不再用内存 set** —— 内存集
    一重启就空,于是"错过的那条永远不补"和"同一节课重启后再弹一次"会同时发生。
    异常不往外抛,但**每轮结果都往 watchdog.log 记一行**(v0.9.28:这条线程
    出问题时的表现是"什么也没发生",不留现场就永远只能猜)。
    """
    def run() -> None:
        time.sleep(FIRST_DELAY_SECONDS)
        wd_log(f"tick loop start [每 {CLASS_TICK_SECONDS // 60} 分钟看一次,"
               f"提前 {CLASS_LEAD_MINUTES} 分钟:课前 + 待办到点]")
        try:
            catch_up(bridge)             # 先补今天错过的,再进入正常轮询
        except Exception as e:
            wd_log(f"round error [补发]:{type(e).__name__} {e}")
        while True:
            try:
                _tick(bridge)
            except Exception as e:
                wd_log(f"round error [到点]:{type(e).__name__} {e}")
            time.sleep(CLASS_TICK_SECONDS)

    threading.Thread(target=run, daemon=True, name="yinluxs-due").start()
