"""主动发现(主动推荐):后台看门狗把「值得此刻知道的事」推给界面。

两条线,全部只说真查到的东西,绝不编造"你可能想做的事":
- 官网新通知:启动后拉默认栏目页(教务处/学生处通知公告,博达站群静态页,
  httpx+bs4 可解析),与本地快照(notice-watch.json)diff,有新条目才推,
  推一次就记入快照 —— 同一批通知永远只打扰一次;
- 校历临近:kb/school-calendar.json 里 7 天内的事件,每次启动至多提醒一回。

三条纪律:独立 daemon 线程绝不阻塞启动;断网/改版/任何异常一律静默跳过
(锦上添花的功能不能变成报错源);用户可以一键关(notice-watch.json.enabled)。
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


def fetch_latest(url: str, timeout: float = 12.0) -> list[dict]:
    """拉一个栏目列表页,返回 [{title, url}](本页可见的通知条目)。

    只认 /info/ 深链的锚点(正文页),导航/翻页链接一律不要。
    任何网络/解析异常向上抛,由 check_once 统一吞掉。
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
        if len(divs) >= 2 and _DATE_PREFIX.match(divs[0].get_text(" ", strip=True)):
            raw = divs[1].get_text(" ", strip=True)       # 站群模板:日期div+标题div
        else:
            raw = a.get_text(" ", strip=True)
        title = clean_title(raw)
        if len(title) < 8:
            continue
        seen.add(full)
        items.append({"title": title, "url": full})
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
    """7 天内要到点的校历事件(倒计时提醒的数据源)。"""
    from minicode import calendar as campus_calendar
    events, _src = campus_calendar.load_events()
    today = today or date.today()
    rows = campus_calendar.upcoming(events, today, limit=3,
                                    within_days=within_days)
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


def _push_proactive(bridge, kind: str, **payload) -> None:
    try:
        bridge.ui.push("proactive", kind=kind, **payload)
    except Exception:
        pass


def start_loop(bridge, interval_hours: float | None = None) -> None:
    """启动后台看门狗线程(gui_main 调一次;测试/CLI 不调它就不存在)。"""

    def run() -> None:
        time.sleep(FIRST_DELAY_SECONDS)
        calendar_done = False
        reminders_done = False
        update_done = False
        while True:
            try:
                state = load_state()
                if state.get("enabled", True):
                    if not calendar_done:
                        for alert in calendar_alerts():
                            _push_proactive(
                                bridge, "calendar", name=alert["name"],
                                days_left=alert["days_left"],
                                start=alert["start"])
                        calendar_done = True       # 每次运行只提醒一回
                    items = check_once(state)
                    if items:
                        shown = items[:4]
                        _push_proactive(bridge, "notices", items=shown,
                                        total=len(items))
                if not update_done:                # 新版本检查与通知开关无关
                    from minicode import __version__
                    update = check_update(__version__)
                    if update:
                        _push_proactive(bridge, "update", **update)
                    update_done = True
                if not reminders_done:             # 临期/逾期待办:每次运行一回
                    from minicode import reminders as _reminders
                    near = _reminders.due_soon()
                    if near:
                        _push_proactive(bridge, "reminders", items=[
                            {"id": i["id"], "title": i["title"],
                             "due": i.get("due", ""),
                             "days_left": i["days_left"],
                             "note": i.get("note", "")} for i in near[:5]])
                    reminders_done = True
            except Exception:
                pass                        # 看门狗的任何意外都不许冒出来
            hours = interval_hours or DEFAULT_INTERVAL_HOURS
            time.sleep(max(1, float(hours)) * 3600)

    thread = threading.Thread(target=run, daemon=True,
                              name="yinluxs-proactive")
    thread.start()
