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
from datetime import date, datetime
from pathlib import Path

from minicode import jobs as _jobs
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
    # v0.9.48:放假安排/大事件多走新闻网(实测「中秋国庆连放 13 天」那条在综合新闻,
    # 教务/学生处通知栏里没有) —— 校历放假数据缺位时,模型现拉这两栏才找得到官方原文。
    {"name": "新闻网通知", "url": "https://www.gzasc.edu.cn/news/tzgg.htm"},
    {"name": "综合新闻", "url": "https://www.gzasc.edu.cn/news/zhxw.htm"},
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
        if not isinstance(href, str) or not href.strip():
            continue
        # /info/ 深链判定必须在 urljoin **之后**做:学生处栏目改版后条目是相对路径
        # (info/1274/18641.htm,开头没有 /),在原始 href 上判 "/info/" 会把它整栏
        # 静默滤成 0 条 —— 看门狗和 campus_notices 双双瞎掉还看不出错(2026-09-26 实测)。
        full = urljoin(str(resp.url), href)
        if not full.startswith("http") or "/info/" not in full or full in seen:
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
             "days_left": int(e.get("days_left") or 0),
             "kind": str(e.get("kind") or "")} for e in rows]


# ---------------------------------------------------------------- 新版本检查

# v0.9.162+:更新通道从 gitee 换到 **GitHub Release**(用户把仓库开放了:
# https://github.com/MOKO-TB/yinlu-xuezhang)。换的原因如实记:gitee 那个仓
# (inkzero/momo)匿名已读不到(repo 页 403)—— 也就是说**旧检查一直是静默死的**,
# "维护者发 release 就亮"这句承诺从来没兑现过。GitHub 公开仓匿名可读
# (60 次/时,启动一次远远够)。
RELEASE_API = "https://api.github.com/repos/MOKO-TB/yinlu-xuezhang/releases/latest"
RELEASES_API = "https://api.github.com/repos/MOKO-TB/yinlu-xuezhang/releases"
NOTES_MAX = 6000      # 更新日志的展示上限;更长在卡片里提示去发布页看全文


def _ver_tuple(text: str) -> tuple[int, ...]:
    import re
    nums = re.findall(r"\d+", str(text or ""))[:3]
    return tuple(int(n) for n in nums) or (0,)


def _cert_variants():
    """更新通道的**证书两跳**:先是包内那份 certifi,再换系统证书库。

    理由是本机实测踩出来的:有些机器(R 里的"加速器"、公司代理、安全软件)把 HTTPS
    **接管**了 —— 它把自己那张根证书装进 **Windows 证书库**,却不会装进 Python 包里
    那份 `certifi`。表现是"浏览器上得去、Windows 处处上得去,而 python 一调就是
    `CERTIFICATE_VERIFY_FAILED`"(本机实测:签发者是某加速器的根,默认失败、系统库成功)。
    只试一次的话,这一格在那些机器上会**永远静默** —— 用户点「检查更新」得到的是
    "已是最新版本",而真相是它一次都没问出去。

    v0.9.177 起它从 `_http_get` 的私有逻辑**提成公共底座**,因为查最新 / 列历史 /
    下校验文件这三条都走 `_http_get`(本来就两跳),而**下安装包那一条是自己造
    `httpx.Client` 的**,漏了这跳 —— 本机实测:那份 exe 在默认证书链下
    `ConnectError`,换系统证书库才 206 出字节。元数据全通、下载全不通,是这一条
    最容易骗过测试的坏法(测试只 stub 了元数据那条)。
    """
    yield "certifi", None
    try:
        import ssl
        yield "系统证书库", ssl.create_default_context()
    except Exception:      # 没有 ssl 可用 = 这台机器连不上任何 https,后面照实报
        pass


def _http_get(url: str, timeout: float = 10.0,
              headers: "dict | None" = None) -> "tuple[object | None, str]":
    """取一个 HTTPS 接口,回 (响应或 None, 没拿到时的实话)。"""
    import httpx
    hd = {"Accept": "application/vnd.github+json"}
    hd.update(headers or {})
    why: list[str] = []
    resp = None
    for label, ctx in _cert_variants():
        try:
            kw = {} if ctx is None else {"verify": ctx}
            # **要跟重定向**,而且这条比 `download_asset` 那一条更容易被漏:
            # 资产 API(`…/releases/assets/<id>`)发的是 **302 + Location**,v0.9.177 把
            # 校验清单的取法改到那一跳之后,不跟跳转就拿到一个 302 的空正文 —— 表现是
            # `_expected_sha256` 回空串、`verified` 永远是 `none`、界面上那颗按钮永远灰,
            # 而**下载本身是好的**(它有 `follow_redirects=True`)。本机实测撞出来的:
            # 元数据三条路(查最新 / 列历史)本来就直接回 200,所以这个旗标以前没人在乎过。
            resp = httpx.get(url, timeout=timeout, headers=hd,
                             follow_redirects=True, **kw)
            break
        except Exception as e:
            why.append(f"{label}不通({type(e).__name__})")
            resp = None
    if resp is None:
        return None, "网络或证书不通:" + "、".join(why)
    return resp, ""


def _release_payload(timeout: float = 10.0) -> "tuple[dict | None, str]":
    """取最新 release 的 JSON,回 (数据或 None, 没拿到时的实话)。"""
    resp, why = _http_get(RELEASE_API, timeout)
    if resp is None:
        return None, why
    if resp.status_code == 404:
        return None, "这个仓库还没有发布过 Release(所以没有可比的版本)"
    if resp.status_code != 200:
        return None, f"接口回了 HTTP {resp.status_code}"
    try:
        data = resp.json()
    except Exception:
        return None, "接口回的正文不是 JSON"
    if not isinstance(data, dict):
        return None, "接口回的 JSON 形状不对"
    return data, ""


def check_update(current: str, timeout: float = 10.0, *,
                 detail: bool = False) -> "dict | None":
    """查 GitHub 公开仓库的最新 release,比当前版本新才返回;失败一律 None。

    返回里带**整段更新日志**(body,`NOTES_MAX` 截断并标 truncated)与发布日期 ——
    界面那张「更新日志」卡要的就是它们(用户图 p1:版本 + 日期 + 日志 + 打开)。
    只在"真比当前新"时返回:同版本/更旧/接口失败/没有 release(404)全部 None,
    一次网络抖动绝不该在界面上变成一句话(自动那条路上,静默就是对的)。

    `detail=True` 给**手动查**那条路用:它必须分得清"已经是最新"与"根本问不出去"
    —— 两者都回 None 的话,界面只能把失败说成"已是最新",那是这个项目不许的假绿。
    此时返回 `{"ok": bool, "update": dict|None, "why": str}`。
    """
    data, why = _release_payload(timeout)
    if data is None:
        return {"ok": False, "update": None, "why": why} if detail else None
    tag = str(data.get("tag_name") or data.get("name") or "").strip()
    url = str(data.get("html_url") or "")
    if not tag or _ver_tuple(tag) <= _ver_tuple(current):
        return {"ok": True, "update": None, "why": ""} if detail else None
    notes = str(data.get("body") or "").strip()
    published = str(data.get("published_at") or "")
    got = {"version": tag.lstrip("vV"), "url": url,
           "notes": notes[:NOTES_MAX], "truncated": len(notes) > NOTES_MAX,
           "date": published[:10],
           "name": str(data.get("name") or "").strip()}
    # v0.9.176:带上"这一台该下哪一件"。挑不出来就留空 —— 界面那颗按钮会
    # 老实变成「打开发布页」,不假装能就地装。
    # v0.9.178:先问"该下哪一件"(整包 / 仅 app / 单文件),再按那个名字去取资产。
    # 顺序很重要:先按后缀挑、再决定,会出现"判定说下 135 MB 的那件、手里拿着 878 MB
    # 的那件" —— 那还不如没有判定。
    form = running_form()
    man, man_why = fetch_release_manifest(data, str(got["version"]))
    # allow_compute:老包(0.9.178 及更早)的安装目录里没有内核凭证,不现算就只能
    # "第一次照旧下 878 MB"。实测现算是 1.0 秒的事,而它换回 743 MB。
    plan = plan_update(form, man, local_identities(allow_compute=True))
    if man_why:
        plan.setdefault("manifest_why", man_why)
    # v0.9.179:**有清单时两件的名字都从清单取**,不只取"小件"那一个。原来只在
    # `choice == "app_setup"` 时才给名字,退回整包那一路留空走后缀兜底 —— 而后缀兜底
    # 会把 `…-app-setup.exe` 当成 `…-setup.exe`(GitHub 资产按字母序,app-setup 在前),
    # 于是"判定说整包、递出来是小包"。清单在场就没有理由靠猜名字。
    choice = str(plan.get("choice") or "")
    want_name = ""
    if choice in ("app_setup", "setup") and form != "single":
        want_name = str(((man.get("assets") or {}).get(choice) or {}).get("name") or "")
    got["asset"] = pick_asset(data, form, want_name)
    if plan.get("choice") == "app_setup" and not got["asset"]:
        # 清单说可以下小件、Release 里却没这件:以**实际能下的**为准,并把矛盾说出来
        got["asset"] = pick_asset(data, form)
        plan = {"choice": "setup", "name": "",
                "reason": "清单说可以只下「仅 app」包,但发布里没有那件 —— 已按整包下",
                "skipped": []}
    got["plan"] = plan
    return {"ok": True, "update": got, "why": ""} if detail else got


# ---------------------------------------------------------------- 就地更新(v0.9.176)

# 运行形态 → 该下的那件资产的**名字后缀**。只有两件:装机/目录版吃 setup.exe,
# 单文件版吃 single-file.exe。免安装的 zip 这里**故意不给** —— 它 1.2 GB,而
# "解压覆盖正在运行的自己"这件事程序做不到,给一个按钮就是给一个会失败的承诺。
ASSET_SUFFIX = {"setup": "-setup.exe", "single": "-single-file.exe"}
# 「仅 app」包的名字**以 `-setup.exe` 结尾**(`…-app-setup.exe`),所以按后缀兜底那一路
# 必须把它明确排除掉 —— 见 `pick_asset` 里那段。
APP_ONLY_SUFFIX = "-app-setup.exe"


def running_form() -> str:
    """现在跑的是哪一版:`single`(单文件版)还是 `setup`(装机 / 目录版)。

    判据用 **exe 的文件名**,不拿 `_MEIPASS` 猜临时目录 —— 打包时单文件版那个文件
    就叫 `引路学长-单文件版.exe`(名字定死),这一条比任何运行时探测都硬。
    挑错件的代价比挑不出来大:给单文件版下 920 MB 的安装程序,用户会以为"我装了
    两个学长",而那个安装程序还会往 Program Files 写一份。
    """
    import sys
    exe = str(getattr(sys, "executable", "") or "")
    return "single" if "单文件" in Path(exe).name else "setup"


def _asset_api_url(asset: dict) -> str:
    """这只资产在 **api.github.com** 上的取件地址(拿不到就回空串)。

    为什么优先用它:`browser_download_url` 的宿主是 `github.com`,本机实测过一整类
    网络状态是 **`github.com:443` 连不上而 `api.github.com` 通**(git push 同时段也是
    这个表现)。两条路最终都 302 到同一个 `release-assets.githubusercontent.com`,
    所以 api 这条**少一跳坏的**,不是多一套机制。`browser_download_url` 留作兜底。
    """
    u = str((asset or {}).get("url") or "")
    return u if u.startswith("https://api.github.com/") else ""


def pick_asset(release: "dict | None", form: "str | None" = None,
               want_name: str = "") -> "dict | None":
    """从 release 的 assets 里挑这一台该下的那件;挑不到回 None(界面退回开网页)。

    `want_name` 是 `plan_update` 定下来的那一件的名字(仅 app 包 / 整包 / 单文件)。
    给了它就必须**按名字取** —— 按后缀取会把"仅 app 包"和"整包"混成一件事
    (两件都以 `setup.exe` 结尾!),那等于精心算出来的判定被最后一行代码抹掉。
    """
    use = form or running_form()
    if not isinstance(release, dict):
        return None
    assets = [a for a in (release.get("assets") or []) if isinstance(a, dict)]
    sha_url = ""
    for a in assets:                     # 校验清单也走 api 那一跳,理由同上
        if str(a.get("name") or "").endswith("-sha256.txt"):
            sha_url = _asset_api_url(a)
            break
    want = ASSET_SUFFIX.get(use)
    for a in assets:
        nm = str(a.get("name") or "")
        if want_name:
            if nm != want_name:
                continue
        else:
            # **没给名字才按后缀**(旧版发布没有清单)。这一路以前会选中
            # `…-app-setup.exe` —— 它的后缀兜底也是 `-setup.exe`,而 GitHub 按字母序
            # 把 app-setup 排在 setup 前面,于是"判定说下整包、递出来是 135 MB 那件":
            # 老用户(本机没有内核凭证,必然走这条)装完没有馆藏库与内核,而卡上写着
            # "按整包下"。v0.9.179 实测抓到,包内端到端那一段。
            if not (want and nm.endswith(want)):
                continue
            if nm.endswith(APP_ONLY_SUFFIX):
                continue
        api = _asset_api_url(a)
        browser = str(a.get("browser_download_url") or "")
        url = api or browser
        if not url.startswith("https://"):
            return None             # 没有可下的直链就别给按钮
        return {"name": nm, "url": url,
                "api_url": api, "browser_url": browser, "sha_url": sha_url,
                "size": int(a.get("size") or 0), "form": use}
    return None


def fetch_release_manifest(release: "dict | None", version: str,
                           timeout: float = 12.0) -> "tuple[dict, str]":
    """取那份 `yinlu-xuezhang-<版本>-assets.json`(发布清单),回 (内容或 {}, 实话)。

    为什么要有这份清单而不直接用 Release 的资产表:资产表只说"有哪些文件",**说不出
    "馆藏库与内核是哪一版"** —— 而"能不能跳过那 84.6%"要的就是后一句。
    取不到不是错误:旧版本(≤v0.9.177)的发布里根本没有这份文件,那种情况照旧下整包,
    但要把"为什么没跳过"说清,不能让用户以为是坏了。
    """
    if not isinstance(release, dict):
        return {}, "没有 release 数据"
    name = "yinlu-xuezhang-%s-assets.json" % version
    for a in (release.get("assets") or []):
        if isinstance(a, dict) and str(a.get("name") or "") == name:
            u = _asset_api_url(a) or str(a.get("browser_download_url") or "")
            if not u.startswith("https://"):
                return {}, "清单那件没有可下的地址"
            resp, why = _http_get(u, timeout,
                                  headers={"Accept": "application/octet-stream"})
            if resp is None:
                return {}, why
            if resp.status_code != 200:
                return {}, f"清单 HTTP {resp.status_code}"
            try:
                import json
                d = json.loads(resp.text)
            except Exception:
                return {}, "清单不是合法 JSON"
            return (d, "") if isinstance(d, dict) else ({}, "清单形状不对")
    return {}, f"这一版的发布里没有 {name}(旧版发布,按整包下)"


# 两份身份的**唯一投影口径**:构建端(scripts/build_exe.py)与 App 端(本文件)必须
# 走同一个函数。曾经各写一份:清单里多带了 `schema` 字段,而本机投影只有三个字段,
# 于是 `_same_identity` 永远判"不一致"—— 路 1 在源码态 45 支测试全绿,在装好的包里
# 一个字节都省不了(是包内端到端探针抓到的,不是测试)。
IDENTITY_KEYS = {"library": ("size", "count", "sha256"),
                 "browser_kernel": ("files", "bytes", "digest")}


def identity_of(raw: "dict | None", kind: str) -> dict:
    """从一份凭证 JSON 里取出"身份"——只留内容字段,**时间戳与 schema 都不进**。

    `built_at` 每次构建都变(进了就永远不一致);`schema` 是**文档格式版本**不是内容身份,
    两侧各自决定带不带,一旦不对称比对就永久失败 —— 所以两边都从这里取。
    """
    raw = raw or {}
    return {k: raw[k] for k in IDENTITY_KEYS.get(kind, ()) if k in raw}


KERNEL_DIR_NAME = "ms-playwright"
KERNEL_STAMP_NAME = "browser-kernel.stamp.json"


def kernel_roots() -> "list[Path]":
    """内核与它的凭证可能躺在的**两处**根:exe 旁(随包装过去的那份)、用户数据目录。

    为什么两处:装进 `Program Files` 时 exe 目录只读,`paths.APP_DIR` 会整体搬到
    `%LOCALAPPDATA%`(v0.9.22 那条),而凭证是安装包写在 **exe 旁**的 —— 只认 APP_DIR
    的那版代码在这类机器上永远读不到凭证、于是永远下整包(安全,但一分钱都没省)。
    """
    from minicode.paths import APP_DIR, RESOURCE_DIR
    out = [Path(RESOURCE_DIR), Path(APP_DIR)]
    return [p for i, p in enumerate(out) if p not in out[:i]]


def kernel_dir() -> "Path | None":
    """真正装着内核的那个目录(两处的第一个非空 `ms-playwright/`),没有就回 None。"""
    for root in kernel_roots():
        d = root / KERNEL_DIR_NAME
        if d.is_dir() and next(d.iterdir(), None) is not None:
            return d
    return None


def kernel_identity(kdir: Path) -> dict:
    """**构建端与 App 端共用这一份算法**:身份 = 排序后的「相对路径 | 每文件 sha256」再摘要。

    为什么不能按目录字节流哈希:几百个文件拷贝一次时间戳就全变,同一份内核在两次构建里
    会得到两个身份 → "跳过没变的那件"永远跳不过去。所以身份只由**内容与名字**决定。
    放在这里(而不是留在 `scripts/build_exe.py`)是因为现在有两边要用它 —— 一件事两处各写
    一份就一定会漂,这一格今天已经付过两次学费(§7 (o)(r))。
    """
    import hashlib
    rows = []
    total = 0
    for f in sorted(kdir.rglob("*")):
        if not f.is_file():
            continue
        h = hashlib.sha256()
        with f.open("rb") as fh:
            for blk in iter(lambda: fh.read(1 << 20), b""):
                h.update(blk)
        rows.append("%s|%s" % (f.relative_to(kdir).as_posix(), h.hexdigest()))
        total += f.stat().st_size
    return {"files": len(rows), "bytes": total,
            "digest": hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()}


def read_kernel_identity(allow_compute: bool = False) -> dict:
    """本机内核的身份:先读凭证(几十个字节的 JSON);读不到而**内核本体在场**时,
    按 `allow_compute` 决定要不要现算一次并落盘。

    为什么要允许现算(实测,不是我猜的):697 MB / 603 个文件在这台机器上 **1.0 秒**算完,
    而它换来的是一次 743 MB 的下载省下来 —— 老包(0.9.178 及更早)的安装目录里本来就没有
    这份凭证,不现算就只能"第一次照旧下整包"。三条边界:①**只在更新判定这条路上开**
    (`check_update`),启动路径不开;②算一次就写盘,以后只读那几十个字;③慢机器上
    这是十秒级的事,所以界面那句理由会写"本机现算"而不是假装它一直是凭证。
    """
    import json
    kdir = kernel_dir()
    if kdir is None:
        return {}          # 没有内核本体 —— 有凭证也不算(§6 第 80 条⑥:凭证要配得上本体)
    for root in kernel_roots():
        p = root / KERNEL_STAMP_NAME
        if not p.is_file():
            continue
        try:
            got = identity_of(json.loads(p.read_text(encoding="utf-8")), "browser_kernel")
        except Exception:
            continue
        if got:
            return got
    if not allow_compute:
        return {}
    try:
        ident = kernel_identity(kdir)
    except Exception:
        return {}
    for root in kernel_roots():        # exe 旁能写就写那儿(与安装包同一个位置),否则退到数据目录
        try:
            (root / KERNEL_STAMP_NAME).write_text(json.dumps(
                dict(ident, schema=1, kind="browser-kernel", computed_at=time.strftime(
                    "%Y-%m-%d %H:%M:%S"), computed_by="本机现算"),
                ensure_ascii=False, indent=1), encoding="utf-8")
            break
        except Exception:
            continue
    return ident


def local_identities(allow_compute: bool = False) -> dict:
    """本机那两块大数据的身份(馆藏库 / 浏览器内核)——**读凭证文件**,内核那份缺了可现算。

    为什么默认不现算:馆藏库 1.3 GB、内核 731 MB,而这件事也发生在启动那条路上;凭证是
    构建时算好、随包一起装到本机的,读它就是读几十个字节的 JSON。
    `allow_compute=True` 只给更新判定那条路用(见 `read_kernel_identity`:实测 1.0 秒,
    换的是少下 743 MB)。**馆藏库那一半不在现算之列** —— 它的凭证从 0.9.177 就往包里带、
    实测与这一版要的一致,现场没有这台机器需要,不去动它。

    少了凭证文件(老包、或用户自己动过目录)就回缺项 —— 调用方**必须**因此退回整包:
    "证明不了一致"和"不一致"在处理上是一回事,但在**说给用户听**上是两回事。

    ⚠ **凭证还要配得上本体**:光有 `browser-kernel.stamp.json` 而 `ms-playwright/` 已经
    不在(用户为了腾磁盘删的,或有人拿"仅 app"包去新机器上装),按"证明不了"处理。
    不然表现是"每次更新都开心地说跳过了内核,而抓取功能悄悄是坏的" —— 那正是这一格
    唯一不许犯的错(把证明不了一致当一致)。
    """
    out: dict = {"library": {}, "browser_kernel": {}}
    try:
        from minicode import library
        db = library.db_path(None)
        if db is not None and db.is_file():
            out["library"] = identity_of(library.read_stamp(db), "library")
    except Exception:
        pass
    try:
        out["browser_kernel"] = read_kernel_identity(allow_compute)
    except Exception:
        pass
    return out


def _same_identity(local: dict, want: dict) -> bool:
    """两份身份是否逐字段相等。**空 dict 一律算不等** —— "没凭证"不能当"对上了"。"""
    if not local or not want:
        return False
    keys = set(want)
    return bool(keys) and all(local.get(k) == want.get(k) for k in keys)


def plan_update(form: str, manifest: dict, local: "dict | None" = None,
                full_size: int = 0) -> dict:
    """这一台该下哪一件,以及**为什么是这件**。回 {choice, name, reason, skipped}。

    三条判据按最"贵"的失败排:①清单缺失/旧版发布 → 整包;②本机证明不了一致 → 整包;
    ③两块都证明一致 → 仅 app 包(实测 878 MiB → 135 MiB)。

    `reason` 不是日志,是**要显示给用户看的那一句**:跳过就说跳过了多少,退回就说
    为什么退 —— "悄悄下了整包"是这一格最容易被误会成坏法的地方。
    """
    local = local if local is not None else local_identities()
    ident = (manifest or {}).get("identity") or {}
    assets = (manifest or {}).get("assets") or {}
    if form == "single":
        return {"choice": "single_file", "name": "",
                "reason": "单文件版本来就不带馆藏库与浏览器内核,这一件没有可跳过的部分",
                "skipped": []}
    want_lib = ident.get("library") or {}
    want_ker = ident.get("browser_kernel") or {}
    app = assets.get("app_setup") or {}
    skipped: list[dict] = []
    blockers: list[str] = []
    if not manifest:
        blockers.append("这一版的发布里没有清单文件(旧版发布),按整包下")
    else:
        if not app.get("name"):
            blockers.append("这一版没产出「仅 app」安装包,按整包下")
        if not want_lib:
            blockers.append("发布清单里没有馆藏库身份")
        elif not _same_identity(local.get("library") or {}, want_lib):
            blockers.append("本机馆藏库与这一版要的不一致(或本机没有凭证),"
                            "这一件里带着新库")
        else:
            skipped.append({"kind": "library", "bytes": int(want_lib.get("size") or 0)})
        if not want_ker:
            blockers.append("发布清单里没有浏览器内核身份")
        elif not _same_identity(local.get("browser_kernel") or {}, want_ker):
            blockers.append("本机浏览器内核与这一版要的不一致(或本机没有凭证、"
                            "内核目录不在了),这一件里带着新内核")
        else:
            skipped.append({"kind": "browser_kernel",
                            "bytes": int(want_ker.get("bytes") or 0)})
    if blockers:
        return {"choice": "setup", "name": "", "reason": "；".join(blockers),
                "skipped": []}
    app_bytes = int(app.get("size") or 0)
    full = int(full_size or 0) or int((assets.get("setup") or {}).get("size") or 0)
    return {"choice": "app_setup", "name": str(app.get("name") or ""),
            "reason": "馆藏库与浏览器内核你机器上已有," + _skip_phrase(
                sum(int(s.get("bytes") or 0) for s in skipped), app_bytes, full),
            "skipped": skipped}


def _skip_phrase(skip_bytes: int, app_bytes: int, full_bytes: int) -> str:
    """卡上那句"跳过多少"要说**少下的下载量**,不能说那两块数据的字节数。

    这两个数是不同的东西(包内探针实测):那两件解压后合计 **1946 MB**,
    而整包 setup 与小件 app-setup 的差是 **743 MB** —— 后者才是用户少付的流量。
    把 1946 写在卡上、旁边再配一句"这次要下 135 MB",两个数字就各说各话了。
    """
    if full_bytes and app_bytes and full_bytes > app_bytes:
        return "这次跳过它们能少下 %s(整包 %s)" % (
            _fmt_mb(full_bytes - app_bytes), _fmt_mb(full_bytes))
    return "这次跳过它们(那两件解压后共 %s,小包里没有)" % _fmt_mb(skip_bytes)


def _fmt_mb(n: int) -> str:
    n = int(n or 0)
    return f"{n / 1048576:.0f} MB" if n >= 1048576 else f"{n // 1024} KB"


def list_releases(limit: int = 8, timeout: float = 12.0) -> dict:
    """历史更新内容(用户图 p8 那张「历史更新内容」的列表)。

    只读公开接口、匿名 GET,与 `_release_payload` 共用同一套两跳证书。
    **拿不到不等于没有更新**:这一屏打不开只说明这一屏打不开,`why` 要写得 out
    是哪一种(网络 / 证书 / 接口形状),不许在界面上留一句"暂无历史"糊过去。
    """
    n = max(1, min(int(limit or 8), 20))
    resp, why = _http_get("%s?per_page=%d" % (RELEASES_API, n), timeout)
    if resp is None:
        return {"ok": False, "releases": [], "why": why}
    if resp.status_code != 200:
        return {"ok": False, "releases": [], "why": f"接口回了 HTTP {resp.status_code}"}
    try:
        data = resp.json()
    except Exception:
        return {"ok": False, "releases": [], "why": "接口回的正文不是 JSON"}
    if not isinstance(data, list):
        return {"ok": False, "releases": [], "why": "接口回的 JSON 不是清单"}
    out = []
    for r in data:
        if not isinstance(r, dict):
            continue
        tag = str(r.get("tag_name") or r.get("name") or "").strip()
        if not tag:
            continue
        notes = str(r.get("body") or "").strip()
        out.append({"version": tag.lstrip("vV"), "tag": tag,
                    "date": str(r.get("published_at") or "")[:10],
                    "name": str(r.get("name") or "").strip(),
                    "notes": notes[:NOTES_MAX], "truncated": len(notes) > NOTES_MAX,
                    "url": str(r.get("html_url") or "")})
    return {"ok": True, "releases": out, "why": ""}


def _expected_sha256(ver: str, asset_name: str, sha_url: str = "",
                     timeout: float = 12.0) -> "tuple[str, str]":
    """去那份随包的 `*-sha256.txt` 里取这个文件的期望哈希,回 (sha 或 "", 实话)。

    校验文件由发布脚本一并传上去,格式是 `<64位十六进制>  <文件名>` 一行一件。
    **取不到时回空串而不是猜一个** —— 调用方必须把"没验过"如实标出来,
    不许把它混进"验过了"。

    `sha_url` 是这份清单在 api.github.com 上的取件地址(v0.9.177 起由 `pick_asset`
    带下来):优先走它,因为 `github.com` 那一跳在实测的网络状态里会整台连不上而
    `api.github.com` 是通的。**取 api 地址时 `Accept` 必须是 `application/octet-stream`**
    —— 这个接口按 Accept 决定给你 JSON 元数据还是给你文件正文,给错了拿到的是
    一份元数据,里面**什么哈希都没有**,于是"校验文件里没有这一件"会说成一句假话。
    """
    got_why: list[str] = []
    for url, hd in ((sha_url, {"Accept": "application/octet-stream"}),
                    ("https://github.com/MOKO-TB/yinlu-xuezhang/releases/download/"
                     "v%s/yinlu-xuezhang-%s-sha256.txt" % (ver, ver),
                     {"Accept": "text/plain"})):
        if not url:
            continue
        resp, why = _http_get(url, timeout, headers=hd)
        if resp is None:
            got_why.append(why)
            continue
        if resp.status_code != 200:
            got_why.append(f"校验文件 HTTP {resp.status_code}")
            continue
        body = str(resp.text or "")
        want = str(asset_name or "").strip().lower()
        for line in body.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1].strip().lower() == want:
                got = parts[0].strip().lower()
                if len(got) == 64 and all(c in "0123456789abcdef" for c in got):
                    return got, ""
                return "", "校验文件里那一行的形状不对"
        got_why.append("校验文件里没有这一件")
        break        # 拿到了正文却没有这一行:换另一条路取同一份清单没有意义
    return "", "、".join(got_why) or "没有可用的校验文件"


def _host_of(url: str) -> str:
    """实话里要说"哪一台没通",但**只说主机名** —— 那份带签名参数的临时地址不该
    落到日志或事件里(它是一个能下载 exe 的凭证 URL,虽然短命)。"""
    m = re.match(r"https?://([^/]+)", str(url or ""))
    return m.group(1) if m else "那个地址"


def _sidecar(part: Path) -> Path:
    """半截文件的那张"我是谁的"条子。"""
    return part.with_name(part.name + ".meta")


def _read_sidecar(part: Path) -> dict:
    try:
        import json
        d = json.loads(_sidecar(part).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _write_sidecar(part: Path, data: dict) -> None:
    try:
        import json
        _sidecar(part).write_text(json.dumps(data, ensure_ascii=False),
                                  encoding="utf-8")
    except OSError:
        pass          # 记不下就只是不能续传,不该让下载失败


def _drop_part(part: Path) -> None:
    for p in (part, _sidecar(part)):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass      # 删不掉(占用/权限)由调用方那份字节数检查兜住


def _hash_prefix(path: Path, n: int) -> "object":
    """回一个已经喂进前 n 字节的 sha256 对象。

    为什么必须这样:哈希对象的中间状态**序列化不了**,所以"上次算到哪了"没法存盘。
    续传时把本机已有那一段重新喂一遍,是为了让最后那个摘要覆盖**整个文件**而不是
    只覆盖新下来的那一段 —— 后者会把"前半截被人换过"验不出来。
    代价实测:920 MB 约几秒(SSD),比重下一遍快两个数量级。
    """
    import hashlib
    h = hashlib.sha256()
    with path.open("rb") as f:
        left = n
        while left > 0:
            chunk = f.read(min(1048576, left))
            if not chunk:
                raise OSError(f"半截文件只有 {n - left:,} 字节,和记下的对不上")
            h.update(chunk)
            left -= len(chunk)
    return h


def download_asset(asset: dict, dest_dir: Path, on_progress=None,
                   should_stop=None, verify: bool = True,
                   connect_timeout: float = 20.0) -> dict:
    """把一件发布资产下到本机:流式写 `.part` → 校验 sha256 → 改名成交付名。

    六条不是显然的:
    1. **先下成 `.part` 再改名** —— 没验过的东西不该长得像"可以安装的包"。名字里
       带版本号,所以上一次的半截和这一次的半截永远不会撞成同一个文件。
    2. **校验拿不到就说拿不到**(`verified="none"` + `why`),绝不静默当通过 ——
       下载回来的是用户会**双击执行**的 exe,"没验过"说成"验过了"是这一路最坏的假绿。
    3. **两条直链都试**:v0.9.177 起优先 `api.github.com` 那个资产地址,再退
       `browser_download_url`。本机实测过 `github.com:443` 整台连不上而
       `api.github.com` 通的网络状态(同一时刻 `git push` 也是这个表现),而两条
       最终都 302 到同一个 `release-assets.githubusercontent.com` —— 换的是第一跳,
       不是换一套机制。
    4. **证书两跳**:与 `_http_get` 同一份底座。v0.9.176 只在元数据那三条有它,
       下载这一条自己造 client 漏了,于是那种机器上的表现是"卡开得出、进度条不动"。
    5. **断点续传**:`Range: bytes=N-` 配 `If-Range: <上次的 ETag/Last-Modified>`。
       服务器不认 Range(回 200)就**从 0 重来**,绝不把两份不同的字节接在一起。
       取消与中断这次**留着** `.part`(这正是续传的意义),它由 `prune_updates()`
       收掉 —— "残骸没人管"和"残骸不能留"是两件事,前一种才是 bug。
    6. 进度只在**整数百分点**变化时回调 —— 920 MB 按块发会把事件总线淹掉。
    """
    import httpx
    name = str((asset or {}).get("name") or "update.bin")
    # 只收 https,**外加一条回环**:本地测试要有一个能真发字节的服务器,而起一套
    # 自签 TLS 不值。这不削弱安全性 —— 地址本来就来自 api.github.com 那份经过校验的
    # 响应,而"能不能装"从来不是这个前缀决定的,是下面那道 sha256 决定的
    # (对不上就删文件、界面上那颗按钮是灰的)。
    urls: list[str] = []
    for key in ("url", "browser_url"):
        u = str((asset or {}).get(key) or "")
        if u and u not in urls and (
                u.startswith("https://") or u.startswith("http://127.0.0.1")
                or u.startswith("http://localhost")):
            urls.append(u)
    if not urls:
        return {"ok": False, "verified": "none", "why": "没有可下载的直链"}
    safe = Path(name).name                       # 资产名来自接口:只取最后一段
    m = re.search(r"-(\d+\.\d+\.\d+)-", safe)    # 校验文件按版本号取
    ver = m.group(1) if m else ""
    dest_dir = Path(dest_dir)
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return {"ok": False, "verified": "none", "why": f"下载目录建不出来:{e}"}
    final = dest_dir / safe
    part = dest_dir / (safe + ".part")
    total = int((asset or {}).get("size") or 0)
    expected, vwhy = ("", "")
    if verify and ver:
        expected, vwhy = _expected_sha256(ver, safe, str((asset or {}).get("sha_url") or ""))

    # 这一台该从第几字节接着要:半截文件 + 一张对得上的条子,才叫"可续传"。
    # 条子对不上(说不清这半截是谁的、或它比整件还长)就删掉重来 —— 把两份不同
    # 来源的字节接在一起,最后那个 sha256 会指向一个谁都没发布过的文件。
    last_pct = -1
    resumed_from = 0

    def prog(got: int) -> None:
        nonlocal last_pct
        if on_progress is None:
            return
        pct = int(got * 100 / total) if total else -1
        if pct == last_pct:                     # 只在整数百分点上说话
            return
        last_pct = pct
        on_progress({"name": safe, "got": got, "total": total, "pct": pct,
                     "resumed": resumed_from > 0, "start_at": resumed_from})

    def _resumable() -> "tuple[int, str]":
        """本机那份半截能不能接着用:名字与总大小对得上才行,对不上就删。"""
        if not part.exists():
            return 0, ""
        rec = _read_sidecar(part)
        have = part.stat().st_size
        ok = (rec.get("name") == safe and have > 0
              and int(rec.get("total") or 0) == total
              and (not total or have <= total))
        if not ok:
            _drop_part(part)
        return (have, str(rec.get("validator") or "")) if ok else (0, "")

    def attempt(u: str, label: str, ctx, start_at: int, validator: str):
        """一条路线一次尝试,回 (结局, 落盘字节, 摘要或 "", 服务器给的验证器, 实话)。

        结局 ∈ {"done", "retry", "stop"}:每次尝试**自己重新看本机有多少字节**并重新
        喂哈希 —— 上一次尝试可能把半截截断过(服务器不认 Range),沿用旧的那个
        哈希对象会算出错的摘要。

        第六个值是"这一次是**从第几字节接上去的**"(0 = 从 0 重写)。它比
        "落盘多少字节"更适合当 `resumed` 的判据:被打回整件重下的那一次,
        落盘字节照样大于旧偏移,拿落盘数说话会把"重下"报成"续传成功"。
        """
        import hashlib
        kw = {} if ctx is None else {"verify": ctx}
        hd = {"Accept": "application/octet-stream"}
        if start_at:
            hd["Range"] = "bytes=%d-" % start_at
            if validator:
                hd["If-Range"] = validator
        try:
            with httpx.Client(timeout=httpx.Timeout(connect_timeout, read=120.0),
                              follow_redirects=True, **kw) as c:
                with c.stream("GET", u, headers=hd) as r:
                    if r.status_code == 416:
                        # 我们要的那段偏移已经超出这件文件了:半截比整件还长,留着
                        # 只会永远续不上,删掉从 0 重来(换一条路线再试)。
                        _drop_part(part)
                        return ("retry", 0, "", "", "本机那份半截比整件还长,已重来", 0)
                    if r.status_code not in (200, 206):
                        return ("retry", start_at, "", validator,
                                f"{r.url.host} HTTP {r.status_code}({label})", 0)
                    # 200 而不是 206 = 这台服务器不认我那份 Range(或没带 Range)。
                    restart = (r.status_code == 200 and start_at > 0)
                    if restart:
                        base = hashlib.sha256()
                    else:
                        base = (_hash_prefix(part, start_at) if start_at
                                else hashlib.sha256())
                    pos = 0 if restart else start_at
                    mode = "wb" if restart else ("ab" if start_at else "wb")
                    with part.open(mode) as f:
                        for chunk in r.iter_bytes():
                            if should_stop is not None and should_stop():
                                f.flush()
                                return ("stop", pos, "", validator, "",
                                        0 if restart else start_at)
                            f.write(chunk)
                            base.update(chunk)
                            pos += len(chunk)
                            prog(pos)
                    return ("done", pos, base.hexdigest(),
                            str(r.headers.get("etag")
                                or r.headers.get("last-modified") or ""), "",
                            0 if restart else start_at)
        except Exception as e:
            return ("retry", part.stat().st_size if part.exists() else 0, "",
                    validator, f"{_host_of(u)} {type(e).__name__}({label})", 0)

    last_pct = -1
    variants = list(_cert_variants())
    if urls[0].startswith("http://"):
        variants = variants[:1]        # 回环测试服务器没有证书,第二跳无从可验
    # 半截已经齐了(上次下完没来得及校验就断了):不出网,直接进校验。
    start_at, validator = _resumable()
    if start_at and total and start_at >= total:
        sha, resumed = _hash_prefix(part, start_at).hexdigest(), True
    else:
        sha, resumed = None, False
        attempt_why: list[str] = []
        for u in urls:
            for label, ctx in variants:
                start_at, validator = _resumable()
                resumed_from = start_at      # 进度里要说"这 N MB 是本机原有的"
                outcome, pos, digest, v, why, joined_at = attempt(
                    u, label, ctx, start_at, validator)
                if outcome == "stop":
                    _write_sidecar(part, {"name": safe, "total": total,
                                          "validator": v or validator, "bytes": pos})
                    return {"ok": False, "cancelled": True, "verified": "none",
                            "partial": pos, "resumable": True,
                            "why": "已取消,已下的 %s 留着,下次点下载接着走"
                                   % fmt_bytes(pos)}
                if outcome == "done":
                    sha, validator = digest, v
                    resumed = joined_at > 0
                    break
                if why:
                    attempt_why.append(why)
            if sha is not None:
                break
        if sha is None:
            # 中断/失败的半截**留着**:记下它现在的字节数与那把验证器,下次接着下
            have = part.stat().st_size if part.exists() else 0
            if have:
                _write_sidecar(part, {"name": safe, "total": total,
                                      "validator": validator, "bytes": have})
            return {"ok": False, "verified": "none", "resumable": bool(have),
                    "partial": have,
                    "why": ("下载没通:" + "；".join(attempt_why[:4])) if attempt_why
                            else "已取消,已下的 %s 留着,下次接着走" % fmt_bytes(have)}

    on_disk = part.stat().st_size
    if total and on_disk != total:
        _write_sidecar(part, {"name": safe, "total": total,
                              "validator": validator, "bytes": on_disk})
        return {"ok": False, "verified": "none", "resumable": True,
                "partial": on_disk, "total": total,
                "why": f"这一趟只拿到 {fmt_bytes(on_disk)} / {fmt_bytes(total)},"
                       f"半截留着了,再点一次接着下"}
    verified = "none"
    if verify and ver and expected:
        verified = "ok" if sha == expected else "mismatch"
    if verified == "mismatch":
        # **必须删**:留着它等于下次续传时把那段坏字节继续接在新字节后面,
        # 于是"重下一遍"永远修不好 —— 坏的前提要断根。
        _drop_part(part)
        return {"ok": False, "verified": "mismatch", "sha256": sha, "name": safe,
                "why": "sha256 与发布时那份不一致 —— 这份**不能装**,已删掉重来"}
    try:
        final.unlink(missing_ok=True)            # 同名旧件先让路(覆盖是用户点出来的)
    except OSError:
        pass
    part.replace(final)
    try:
        _sidecar(part).unlink(missing_ok=True)
    except OSError:
        pass
    return {"ok": True, "path": str(final), "name": safe, "size": on_disk,
            "sha256": sha, "verified": verified, "version": ver,
            "resumed": bool(resumed),
            "why": "" if verified != "none" else (vwhy or "没有可用的校验文件")}


def fmt_bytes(n: int) -> str:
    n = int(n or 0)
    for unit, div in (("GB", 1073741824), ("MB", 1048576), ("KB", 1024)):
        if n >= div:
            return "%.1f %s" % (n / div, unit)
    return "%d B" % n


# ------------------------------------------------------- updates/ 的那些旧件(v0.9.177)

# 只认**这一条更新通道自己写下**的文件名形状。凡是形状不对的一律不碰 ——
# 这个目录是用户可写的本机目录,谁也可能往里放别的东西;"清理"的权限来自
# "这是我写的",不来自"它在我的目录下"。
_UPDATE_NAME = re.compile(
    r"^yinlu-xuezhang-\d+\.\d+\.\d+-(setup\.exe|app-setup\.exe|single-file\.exe"
    r"|win64\.zip|sha256\.txt|assets\.json)(\.part(\.meta)?)?$")
_VER_IN_NAME = re.compile(r"yinlu-xuezhang-(\d+\.\d+\.\d+)-")


def _cur_version() -> str:
    """正在跑的这一版(懒导入:`minicode/__init__` 不引 proactive,但绕一下更省心)。"""
    from minicode import __version__
    return str(__version__)


def pending_part(dest_dir: Path) -> dict:
    """本机有没有一份"下了一半、下次能接着下"的更新包(重启后界面该知道这事)。

    只认**带着条子**的那半截:没有 `.part.meta` 就说不清它是谁的、总长多少,那种
    情况报出来只会误导("还有 300 MB 可以续"然后从 0 重下)。所以那种直接不算。
    """
    newest: "tuple[int, Path] | None" = None
    try:
        for p in Path(dest_dir).glob("*.exe.part"):
            if not _sidecar(p).exists():
                continue
            try:
                mt = int(p.stat().st_mtime)
            except OSError:
                continue
            if newest is None or mt > newest[0]:
                newest = (mt, p)
    except OSError:
        return {}
    if newest is None:
        return {}
    p = newest[1]
    rec = _read_sidecar(p)
    m = _VER_IN_NAME.search(p.name)
    total = int(rec.get("total") or 0)
    have = int(rec.get("bytes") or 0) or (p.stat().st_size if p.exists() else 0)
    return {"name": p.name, "version": m.group(1) if m else "",
            "bytes": have, "total": total,
            "pct": int(have * 100 / total) if total else -1,
            "size": fmt_bytes(have)}


def prune_updates(dest_dir: Path, keep_version: str = "",
                  keep_current: bool = True) -> dict:
    """收掉 `updates/` 里**被取代的**旧安装包与续传半截,回清了什么、省了多少字节。

    留下的三样:①`keep_version` 那一版(它是当前要装/在下的那一件);②正在运行的
    这一版自己的交付件(装了它的那份包不必再留);③**名字形状不是发布件的一切**
    (这条比前两条重要:误删别人的东西不是"清理",是事故)。

    为什么值得单独写一只手:920 MB 一件,用户升级三次就是 2.7 GB,而这一路此前
    只会往里写、从不删 —— 「我的数据」里连个影子都没有(所以是"看不见的增长")。
    """
    freed = 0
    removed: list[dict] = []
    kept: list[str] = []
    try:
        entries = sorted(Path(dest_dir).iterdir())
    except OSError:
        return {"ok": True, "removed": [], "freed": 0,
                "why": "下载目录还没建出来(那就没有要清的)"}
    for p in entries:
        if not p.is_file() or not _UPDATE_NAME.match(p.name):
            continue
        v = _VER_IN_NAME.search(p.name)
        v = v.group(1) if v else ""
        if keep_current and v and v == _cur_version():
            kept.append(p.name)
            continue
        if keep_version and v and v == keep_version:
            kept.append(p.name)
            continue
        size = 0
        try:
            size = p.stat().st_size
            p.unlink()
        except OSError as e:
            removed.append({"name": p.name, "bytes": size, "why": f"没删掉:{e}"})
            continue
        removed.append({"name": p.name, "bytes": size, "why": ""})
        freed += size
    return {"ok": True, "removed": removed, "freed": freed, "kept": kept, "why": ""}


# ---------------------------------------------------------------- 桌面通知文案

# 标题只说"这是哪一类提醒",内容全在正文里;正文能复用聊天那行 text 就复用,
# 免得同一件事两处各写一遍、日后各说各话。
KIND_TITLES = {"briefing": "今日简报", "class": "快上课了", "schedule": "今天的课",
               "reminders": "待办临期", "calendar": "校历提醒",
               "notices": "校园新通知", "update": "引路学长有新版本",
               "weather": "天气提醒", "review": "每周心情回顾",
               "gaps": "知识库缺口周报"}


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
    """校历倒计时:7 天内的事件,每个事件每天最多催一条。

    考试类(kind=exam)多带一句**可复制的追问**(排复习计划)—— 这是 A5 考试
    闭环的最后一格:**只说不做**,计划由用户把追问发回来、模型走 plan 工具排,
    这里绝不自动生成(自动排计划 = 替用户决定怎么复习)。
    """
    pushed = 0
    for alert in calendar_alerts():
        extra = {}
        if alert.get("kind") == "exam":
            extra["ask"] = (f"帮我给{alert.get('name')}排一份复习计划,"
                            "按剩余天数分配到每天")
        if _push_once(bridge, "calendar", f"{alert.get('name')}|{alert.get('start')}",
                      name=alert["name"], days_left=alert["days_left"],
                      start=alert["start"], **extra):
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
    `tray.NOTIFY_KINDS` 那张表是既有事实,凭空多一类他关不掉(set_notify 认不出新 kind),而"到点催"
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


# ------------------------------------------------- 定时任务表(v0.9.56 A2 接线,v0.9.57 A4 两个真任务)
#
# 底座(`jobs.py` 的 JobSpec/due/账本)是 v0.9.42 落的纯函数层,A2(v0.9.56)把两条
# 心跳接上:`_tick`(5 分钟)与 `_round`(6 小时)各过一遍到期任务。**课前提醒与待办
# 到点不进表** —— 它们的语义是"提前量 + 补发窗口",不是 cadence,硬塞进任务表会把
# `test_proactive*` 那批既有针全推翻,收益却是零(docs/roadmap-v0.9.42 §A2 的原话)。
# 登记新任务:往 JOB_SPECS 加一个 `JobSpec`、往 JOB_HANDLERS 挂一个
# `handler(bridge) -> str`(返回一句结论进账本与看门狗日志,**内容不进日志**)。
#
# A4 的两个任务都是**只说不做**(方案 §A4:不自动调模型、不自动爬 —— 那会花同学的
# Key 额度,补什么必须由人点头):推一条带「追问」的通知,做不做由用户把追问发回来。
# 节奏刻意错开:周复盘在周日晚上(一周收尾回看心情),缺口周报在周五傍晚(趁周末
# 补语料)。**已知边界**:weekly 到期要求"当天 + 到点",机器整晚没开这一班就跳过
# —— due() 的"宁可少跑一次"口径,下周同一时间自然再来;临时想看随时能问。
def _job_weekly_review(bridge) -> str:
    """周复盘:回读 mood 每周回顾(与 mood 工具同一份真相),到点说一句。

    这一周一条都没记就**不发** —— 心情模块的硬口径是不做打卡催促,
    "你什么都没记"这种通知是压力不是帮助,哪怕措辞再软。
    """
    from minicode import mood as _mood
    review = _mood.weekly_review(days=7)
    if not review.get("count"):
        return "没记就不打扰"
    key = f"review|{_day_key()}"
    if was_pushed("review", key):
        return "今天已推过"
    ok = _push_once(bridge, "review", key,
                    title=KIND_TITLES["review"],
                    text=str(review.get("text") or ""),
                    ask="把每周回顾里值得注意的地方展开说说",
                    count=review.get("count"))
    return "已推" if ok else "没送出"


def _job_gap_digest(bridge) -> str:
    """缺口周报:聚合 kb-gaps.jsonl 最近一周的问法,报"哪些问题没接住"。

    **只报不补**:补语料是显式动作(追问发回来才动),且不管补不补,
    这份账都让"库里缺什么"从维护者的私 knowing 变成用户看得见的一行行。
    """
    from minicode import growth as _growth
    gaps = _growth.gap_digest(days=7, top=5)
    if not gaps:
        return "这周没有缺口"
    lines = [f"最近 7 天有 {len(gaps)} 个问法学长在知识库里没接住,问得最多的:"]
    lines += [f"- {g['query']}(出现 {g['days']} 天)" for g in gaps]
    lines.append("(这些是检索没命中的原话;要不要查、查什么,你来定 —— 学长不会自己联网填库)")
    key = f"gaps|{_day_key()}"
    if was_pushed("gaps", key):
        return "今天已推过"
    ok = _push_once(bridge, "gaps", key,
                    title=KIND_TITLES["gaps"],
                    text="\n".join(lines),
                    ask="把这几个问题查证后补进知识库,补之前先给我看来源")
    return "已推" if ok else "没送出"


def _job_backup(bridge) -> str:
    """周期自动备份(v0.9.98,§6 #17C 剩的「没有定时/增量导出」)。

    把 pack=true 的本机数据(含聊天记录,超过 mydata 的 2 GB 上限就如实报
    备份没成)按周打成一份迁移包,落到 config.json 的 `backup_dir` ——
    **没配置就跳过并说清**,绝不自作主张往用户盘上写。复用
    `mydata.export_package` 同一条路:密钥/教务登录态出不了包那道内层黑名单
    闸原样生效,`.tmp` 先写后换名的原子性也一样。

    成功与否只进账本与看门狗日志(问学长「定时任务跑没跑」能查到),
    **不弹通知** —— 备份是背景事,每周弹一条"我备份好了"就是新的打扰;
    失败也不弹:失败结论同样可查,而"能定时弹的类别必须先进通知开关表"
    (§3 决策 19),为一条失败提示开新类别不值。
    """
    from minicode import mydata as _mydata
    cfg = getattr(bridge, "config", None)
    raw = str(getattr(cfg, "backup_dir", "") or "").strip() if cfg is not None else ""
    if not raw:
        return "没配置 backup_dir,这次跳过(config.json 里填 backup_dir 才会真备份)"
    dest_dir = Path(raw).expanduser()
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return f"备份目录建不出来({type(e).__name__}: {e}),这次跳过"
    dest = dest_dir / f"{_BACKUP_PREFIX}{_day_key()}.zip"   # 同一天重跑覆盖同一份
    r = _mydata.export_package(dest, include_sessions=True)
    if not r.get("ok"):
        return f"备份没成:{r.get('msg') or '原因不明'}"
    rotate = _rotate_backups(dest_dir)
    return (f"已备份 {len(r.get('files') or [])} 项 → {dest.name};{rotate}")


_BACKUP_PREFIX = "引路学长-自动备份-"
BACKUP_KEEP = 4     # 保留最近几份;更老的删掉(只删我们自己命名的那一族)


def _rotate_backups(dest_dir: Path) -> str:
    """把备份目录轮转到最近 BACKUP_KEEP 份,返回一句给人看的结论。

    目录是用户配的,里面可能有他自己的东西 —— 只按
    「前缀 + YYYY-MM-DD + .zip」的**完整形状**匹配(整串 fullmatch,
    不是前缀 startswith):`引路学长-自动备份-2026-01-01.txt`、
    `引路学长-自动备份-旧.zip`、别的名字一概不碰。删不掉(被占用/只读)
    就留着下次再试,绝不为轮转把一次成功的备份标成失败。
    """
    ours: list[Path] = []
    for p in dest_dir.iterdir():
        if not p.is_file() or not p.name.startswith(_BACKUP_PREFIX):
            continue
        body = p.name[len(_BACKUP_PREFIX):p.name.rindex(".zip")] \
            if p.name.endswith(".zip") else ""
        if len(body) == 10 and body.count("-") == 2 \
                and all(x.isdigit() for x in body.split("-")):
            ours.append(p)
    ours.sort(key=lambda p: p.name)         # ISO 日期按名字排序就是按时间
    excess = ours[:-BACKUP_KEEP] if len(ours) > BACKUP_KEEP else []
    removed = 0
    for old in excess:
        try:
            old.unlink()
            removed += 1
        except OSError:
            pass
    line = f"保留最近 {BACKUP_KEEP} 份"
    if removed:
        line += f",删了 {removed} 份更早的"
    stuck = len(excess) - removed
    if stuck:
        line += f";另有 {stuck} 份想删没删掉(可能被占用)"
    return line


JOB_SPECS: "list[_jobs.JobSpec]" = [
    _jobs.JobSpec("mood_weekly_review", cadence="weekly", weekday=6, hour=20,
                  label="周复盘"),
    # v0.9.98 修一处 off-by-one:文档口径一直是「周五 18:00」,这里却登记
    # weekday=5 —— Python weekday 5 是**周六**,缺口周报从 v0.9.57 起一直
    # 晚一天跑(测试还把错值钉住了)。周五是 4。
    _jobs.JobSpec("kb_gap_digest", cadence="weekly", weekday=4, hour=18,
                  label="知识库缺口周报"),
    # 周期自动备份(v0.9.98,§6 #17C 剩):周六上午 10 点,与两条周报错开。
    _jobs.JobSpec("data_backup_weekly", cadence="weekly", weekday=5, hour=10,
                  label="每周自动备份"),
]
JOB_HANDLERS: "dict[str, object]" = {
    "mood_weekly_review": _job_weekly_review,
    "kb_gap_digest": _job_gap_digest,
    "data_backup_weekly": _job_backup,
}


def all_specs() -> "list[_jobs.JobSpec]":
    """代码登记的那几个 + 同学自己在面板里加的那几个(v0.9.114,图 p5/p6)。

    每次**现读盘**:面板里刚加完就能看到"下一班什么时候",不用重启进程。
    """
    return list(JOB_SPECS) + _jobs.user_specs()


def _job_user(bridge, spec) -> str:
    """用户自己登记的定时任务:把写好的那句话交给学长跑一遍。

    走 `bridge.send_task` 而不是另起一条通道 —— 这样**权限档位照常生效**:
    到点要跑的事如果涉及改文件,照样弹确认框等你点头(没人点就这一班不跑)。
    自动化不许成为绕过确认的第二条路。当前会话正忙时不排队也不插队:
    记一句「跳过」并照常记账,等下一班 —— 免得每 5 分钟重试一次把会话挤满。
    """
    text = str(getattr(spec, "prompt", "") or "").strip()
    if not text:
        return "没跑:这条任务没写要做什么"
    if not hasattr(bridge, "send_task"):
        return "没跑:这一侧没有可发任务的会话(只在桌面版有)"
    res = bridge.send_task(text)
    if not isinstance(res, dict):
        return "没跑:学长这边没接上"
    if not res.get("ok"):
        return f"跳过:{str(res.get('msg') or '当前跑不了')[:60]}"
    # 结论里**不回抄同学写的那句话**:`jobs-state.json` 的 result 会顺带进
    # watchdog.log,而这条模块的规矩一直是"内容不进日志"(上面 _run_due_jobs 那段)。
    return f"已交给学长跑这一条({len(text)} 字)"


def _run_due_jobs(bridge, now=None) -> list[str]:
    """过一遍任务表,到期的跑一次并记账;返回真跑过的任务 id 列表。

    - handler 返回一句**结论**(如「已汇总」),存进 jobs-state.json 的 result
      与 watchdog.log —— 跟提醒同一条口径,**内容(问题原文/报告正文)不进日志**。
    - handler 抛异常也算"跑过这一班"(result 记异常名):宁可这一期不发,
      也不要每 5 分钟重试一次直到刷屏 —— 与 due() 的"宁可少跑一次"同方向。
    - 没接线的 spec 如实记一行「未跑[没接线]」且**不记账**:下一轮还会看到,
      不会静默吞掉一个登记了却没实现的任务。
    - 整段拿 `_push_lock` 串行:两条心跳同时到期时不会双跑(handler 自己的
      推送去重另有一层,这层管的是"任务表这一格只记一次")。
    """
    specs = all_specs()
    if not specs:
        return []
    now = now or datetime.now()
    ran: list[str] = []
    with _push_lock:
        for spec in _jobs.due_jobs(specs, now=now):
            handler = JOB_HANDLERS.get(spec.id)
            try:
                if handler is not None:
                    result = str(handler(bridge) or "")
                elif getattr(spec, "prompt", ""):
                    result = str(_job_user(bridge, spec) or "")
                else:
                    _jobs.log_outcome(spec, ran=False, why="没接线")
                    continue
            except Exception as e:
                result = f"异常 {type(e).__name__}"
            landed = _jobs.mark_run(spec.id, when=now, result=result)
            why = result
            if not landed:
                why = f"{result} · 账本没落盘" if result else "账本没落盘"
            _jobs.log_outcome(spec, ran=True, why=why)
            ran.append(spec.id)
    return ran


def _tick(bridge, now=None) -> dict:
    """细轮询一轮:循环待办先滚账,课程与待办各自判、各自记日志,任务表过一遍。

    抽成函数是 v0.9.28 那条老规矩的延续:**循环体里的判断必须能直接测**,
    否则"到点没催"这种问题只能真的等到点。
    """
    try:
        from minicode import reminders as _reminders
        rolled = _reminders.roll_repeats(now=now)
    except Exception as e:
        rolled = 0
        wd_log(f"fail repeat [-]:{type(e).__name__} {e}")
    if rolled:
        wd_log(f"repeat [{rolled} 条]:循环待办滚到下一次(只记条数,不记内容)")
    return {"class": _push_class(bridge, now=now),
            "due": _push_due(bridge, now=now),
            "jobs": _run_due_jobs(bridge, now=now)}


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
    # 可关(v0.9.162+):config.json 的 `update_check: false` 就一次都不出网。
    # 默认开 —— 分发出的 setup.exe 升不了级才是常态问题。
    if getattr(getattr(bridge, "config", None), "update_check", True):
        update = check_update(__version__)   # 内部已把断网/改版全吞成 None
        if update:
            cfg = getattr(bridge, "config", None)
            skipped = str(getattr(cfg, "update_skip_version", "") or "").lstrip("vV")
            ver = str(update.get("version") or "")
            if skipped and skipped == ver:
                # 「跳过此版本」:这一版一个字都不提,但**下一版照样弹** ——
                # 它跳的是那一版,不是"以后都别吵我"(那是 update_popup:false)。
                out["update"] = "skipped:" + ver
            else:
                out["update"] = _push_once(bridge, "update", ver, **update)
                # v0.9.176:除了聊天栏顶部那条提示栏(用户图 p1 说它"要用户自己
                # 去点"才看得见),再推一个 `update_prompt` 让界面**自己弹卡**。
                # 为什么分两条事件而不是把提示栏改成弹窗:提示栏是所有主动推送
                # 共用的那一条路(简报/课/通知都走它),为更新一个类别改它的形状,
                # 代价是每一类都要重新验一遍。
                if bool(getattr(cfg, "update_popup", True)):
                    try:
                        bridge.ui.push("update_prompt", **update)
                    except Exception:
                        pass    # 弹窗失败不影响上面那条已经送到的提示栏
    out["jobs"] = _run_due_jobs(bridge)    # 主循环兜一遍任务表(5 分钟那路没开也到期)
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
