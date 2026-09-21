"""教务系统课表导入(本地向导):登录 → 读学期课表 → 确定性解析 → 用户确认才落盘。

为什么要这么一套(而不是"让模型看看截图"):
- 验证码只可人输 —— 学长**从不代填、也从不索要**账号密码,登录这一步必须由本人
  在弹出的浏览器窗口里做完;
- 强智教务的课表塞在**进程内 iframe**(`/gzasc_jsxsd/xskb/xskb_list.do`)里,
  它不作为 CDP target 出现,所以只看 target 会误判"这页没东西"
  (见 browser.page_frames 的说明);
- 单元格里的文本形状很固定:`课程名 / 教师 / 周次(周)[01-02节] / 地点`,
  一格多门课用一长串短横分隔 —— 有真样本就不该让模型猜,故**确定性解析优先**,
  结构变了才退回"把页面文字交给模型解析"。

流程设计:由人点三下最诚实 —— 登录是人的事,读和解析是程序的事。每一步都
**不写文件**:只有确认导入(前端另一个按钮)才调 schedule.save。
所以这里没有后台线程、没有轮询状态机,界面上每次点击拿到的都是一份完整结果。

隐私口径(与 HANDOFF §6 #15 一致):登录态不进对话、不落盘;本模块只在内存里
拿页面文本,解析完就丢;schedule.json 里只留课表本身。
"""
from __future__ import annotations

import contextlib
import json
import re
from pathlib import Path

LOGIN_URL = "https://jw.educationgroup.cn/gzasc/"
XKB_URL = "https://jw.educationgroup.cn/gzasc_jsxsd/xskb/xskb_list.do"
HOST_HINT = "educationgroup.cn"   # 教务页所在站点(接管页面时按它找)
FRAME_HINT = "xskb"               # 学期课表所在帧的 URL 片段
MODEL_TEXT_CHARS = 16000          # 兜底交给模型的页面文字上限

# 在课表帧里跑的那段 JS:把 #kbtable 拍成二维文本网格 + 顺手读出学期/周次筛选。
# 为什么读 innerText 而不是解析 HTML:innerText 只含**可见**文本,教务页那些
# display:none 的另一半单元格(单双周备用)天然被排除掉了。
GRID_JS = """(() => {
  const t = document.querySelector('#kbtable') || document.querySelector('table');
  if (!t) return null;
  const rows = [...t.querySelectorAll('tr')].map(tr =>
    [...tr.children].map(c => (c.innerText || '').replace(/\\u00a0/g, ' ')));
  const pick = (n) => { const s = document.querySelector('select[name="' + n + '"]');
                        return s ? (s.options[s.selectedIndex] || {}).value || '' : ''; };
  return JSON.stringify({term: pick('xnxq01id'), week: pick('zc'),
                         title: document.title, url: location.href, rows});
})()"""

TEXT_JS = "(() => { const b = document.body; return b ? (b.innerText || '') : ''; })()"

# 一格里的条目形状:第一串含「(周)」的行是锚点,它前面是课名/教师,后面是地点
_DASH_RE = re.compile(r"^-{3,}$")
_WEEK_LINE_RE = re.compile(r"[（(]周[)）]")
_PERIOD_RE = re.compile(r"\[(\d{1,2}(?:\s*-\s*\d{1,2})+)\s*节\]")
_BELL_RE = re.compile(r"(\d{1,2})[:：](\d{2})\s*[-~－]\s*(\d{1,2})[:：](\d{2})")
_DAY_RE = re.compile(r"^\s*(星期|礼拜)?\s*([一二三四五六日天])\s*$")


# ---------------------------------------------------------------- 确定性解析

def _cell_entries(text: str) -> list[list[str]]:
    """一个单元格 → 若干课程条目(短横线串是多门课之间的分隔)。"""
    chunks: list[list[str]] = []
    cur: list[str] = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if _DASH_RE.match(line):
            if cur:
                chunks.append(cur)
                cur = []
            continue
        cur.append(line)
    if cur:
        chunks.append(cur)
    return chunks


def _norm_periods(raw: str) -> str:
    """'01-02' / '01-08' → '1-2' / '1-8';'01-02-03-04' → '1-4'。

    恰好两个数字时按**起-止区间**解释 —— 教务把跨 8 节的课写成 [01-08节],
    若按"列举了两个节次"处理会得到 "1,8",等于把一连 8 节课说成第 1、8 两节。
    三个以上数字才是逐个列举(不连续时退化成逗号表,宁缺不猜)。
    """
    nums = [int(n) for n in re.findall(r"\d{1,2}", raw)]
    if not nums:
        return ""
    if len(nums) == 2:
        a, b = sorted(nums)
        return str(a) if a == b else (f"{a},{b}" if b - a > 12 else f"{a}-{b}")
    ordered = sorted(set(nums))
    if ordered == list(range(ordered[0], ordered[-1] + 1)):
        return f"{ordered[0]}-{ordered[-1]}" if len(ordered) > 1 else str(ordered[0])
    return ",".join(str(n) for n in ordered)


def _norm_weeks(line: str) -> str:
    """'1-6(周)' / '4,6,8,10(周)' → 交给 schedule.parse_weeks 的原样周次串。"""
    core = _WEEK_LINE_RE.split(line)[0]
    return re.sub(r"\s+", "", core)


def _entry(lines: list[str], day: int, bell: str) -> tuple[dict | None, str | None]:
    """一条课程条目 → (课表记录, 失败原因)。两者恰有一个非空。"""
    anchor = next((i for i, l in enumerate(lines) if _WEEK_LINE_RE.search(l)), None)
    if anchor is None:
        return None, f"没有「周次(周)」这一行:{' / '.join(lines)[:60]}"
    pm = _PERIOD_RE.search(lines[anchor])
    if not pm:
        return None, f"周次行里读不到节次:{lines[anchor][:60]}"
    name = " ".join(lines[:max(anchor - 1, 0)]).strip()
    teacher = lines[anchor - 1].strip() if anchor >= 2 else ""
    if not name:                        # 只有一行在周次之前:那行就是课名,教师未知
        name, teacher = " ".join(lines[:anchor]).strip(), ""
    location = ""
    for extra in lines[anchor + 1:]:
        if not _WEEK_LINE_RE.search(extra):
            location = extra
            break
    if not name:
        return None, "读不出课程名"
    return {"name": name[:60], "day": day, "periods": _norm_periods(pm.group(1)),
            "start_time": bell, "weeks": _norm_weeks(lines[anchor])[:80],
            "location": location[:60], "teacher": teacher[:40]}, None


def parse_grid(grid: dict) -> dict:
    """强智「学期理论课表」网格 → 课表记录。

    返回 {"classes", "term", "week", "notes", "unparsed", "ok", "msg"}。
    认不出表头(没有星期一~星期日那一行)时 ok=False 并说明为什么 ——
    这时候该走模型兜底,而不是硬凑一份课表出来。
    """
    rows = (grid or {}).get("rows") or []
    day_of: dict[int, int] = {}
    header_row = -1
    for ri, row in enumerate(rows[:3]):
        got = {}
        for ci, cell in enumerate(row):
            m = _DAY_RE.match(str(cell).strip())
            if m:
                got[ci] = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
                           "六": 6, "日": 7, "天": 7}[m.group(2)]
        if len(got) >= 5:
            day_of, header_row = got, ri
            break
    if not day_of:
        return {"ok": False, "msg": "认不出课表的星期列(页面结构可能和强智不一样)",
                "classes": [], "term": (grid or {}).get("term", ""),
                "week": (grid or {}).get("week", ""), "notes": [], "unparsed": []}

    classes: list[dict] = []
    seen: set[tuple] = set()
    unparsed: list[str] = []
    for row in rows[header_row + 1:]:
        cells = [str(c or "") for c in row]
        bell = ""
        bm = _BELL_RE.search(cells[0] if cells else "")
        if bm:
            bell = f"{int(bm.group(1)):02d}:{bm.group(2)}"
        for ci, day in day_of.items():
            if ci >= len(cells):
                continue
            for entry in _cell_entries(cells[ci]):
                rec, why = _entry(entry, day, bell)
                if rec is None:
                    unparsed.append(why)
                    continue
                # 同一门"连上 8 节"的课会在每个大节行里重复出现:按内容去重,
                # 保留最早那个钟点(它才是真正的第一次上课时间)。
                key = (rec["name"], rec["day"], rec["periods"], rec["weeks"],
                       rec["location"], rec["teacher"])
                if key in seen:
                    continue
                seen.add(key)
                classes.append(rec)
    classes.sort(key=lambda c: (c["day"], c["periods"], c["name"]))
    notes: list[str] = []
    if (grid or {}).get("week"):
        notes.append(f"页面当前只筛了第 {grid['week']} 周 —— 导入前请把「周次」切回(全部)"
                     "再读一次,否则读到的不是整学期。")
    no_bell = sum(1 for c in classes if not c["start_time"])
    if no_bell:
        notes.append(f"{no_bell} 门课没读到上课钟点(节次行里没有时间点),"
                     "这些课只报第几节,不编时间。")
    return {"ok": bool(classes), "msg": "" if classes else "课表格里没解析出任何一条课",
            "classes": classes, "term": (grid or {}).get("term", ""),
            "week": (grid or {}).get("week", ""), "notes": notes,
            "unparsed": sorted(set(unparsed))[:8]}


def summarize(classes) -> dict:
    """给确认页用的一眼摘要:多少条、多少门、哪些缺信息。

    为什么要单独算这个:`classes` 是"节次条目"数,同一门课一周两上就是两条。
    同学对着"18 条"和"我明明只有 11 门课"会以为读错了 —— 先把这两个数分开摆出来。
    """
    items = list(classes or [])
    courses: dict[str, int] = {}
    for c in items:
        courses[str(c.get("name", ""))] = courses.get(str(c.get("name", "")), 0) + 1
    return {
        "entries": len(items),
        "courses": len(courses),
        "no_location": sorted({str(c.get("name", "")) for c in items
                               if not str(c.get("location", "")).strip()}),
        "no_time": sorted({str(c.get("name", "")) for c in items
                           if not str(c.get("start_time", "")).strip()}),
    }


# ---------------------------------------------------------------- 模型兜底

MODEL_SYSTEM = (
    "你把教务系统页面上的课程文字整理成 JSON 课表。只输出 JSON,不要解释。"
    "每条:{"
    "\"name\":\"课程名\",\"day\":1到7的整数,\"periods\":\"1-2\",\"start_time\":\"08:20\","
    "\"weeks\":\"1-16\",\"location\":\"上课地点\",\"teacher\":\"教师\"}。"
    "页面上没有的信息一律留空字符串,绝不编造;同一门课不同周次要拆成多条。")


def parse_with_model(text: str, complete) -> dict:
    """确定性解析失败时的兜底:把页面文字交给模型,拿回同结构的 classes。

    `complete` 是 `LLMClient.complete` 那样的可调用(system, user) -> str,
    注入进来是为了能在测试里换掉,也为了这一条路上"要不要把页面文字发给模型"
    由调用方决定(学长默认走确定性解析,不发页面文字)。
    """
    body = (text or "")[:MODEL_TEXT_CHARS]
    if not body.strip():
        return {"ok": False, "msg": "页面没有可读文字", "classes": [], "notes": []}
    try:
        raw = complete(MODEL_SYSTEM, body)
    except Exception as exc:                       # 网络/额度/超时都在这一条路上
        return {"ok": False, "msg": f"模型解析失败:{type(exc).__name__}",
                "classes": [], "notes": []}
    m = re.search(r"\[.*\]", raw or "", re.S)
    if not m:
        return {"ok": False, "msg": "模型没给出课程列表", "classes": [], "notes": []}
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"ok": False, "msg": "模型给的 JSON 读不懂", "classes": [], "notes": []}
    rows = []
    for item in data if isinstance(data, list) else []:
        if isinstance(item, dict):
            rows.append({"name": str(item.get("name", ""))[:60],
                         "day": item.get("day"), "periods": str(item.get("periods", ""))[:40],
                         "start_time": str(item.get("start_time", ""))[:5],
                         "weeks": str(item.get("weeks", ""))[:80],
                         "location": str(item.get("location", ""))[:60],
                         "teacher": str(item.get("teacher", ""))[:40]})
    return {"ok": bool(rows), "msg": "" if rows else "模型说没找到课",
            "classes": rows, "notes": ["这份结果出自模型整理,请对着教务页核一遍"]}


# ---------------------------------------------------------------- 三步向导
# 浏览器操作集中在这个小对象里:标签页**属于打开它的那条线程**,所以前端的三步
# 都走同一个 ImportFlow 实例(桥里存着),换线程就读不到用户那一页了。
# `browser_mod` 可以换成假实现,让解析与流程在没有浏览器的测试里跑通。

class ImportFlow:
    """点一下开登录页 → 你自己登录并点开「学期课表」→ 点一下读取。"""

    def __init__(self, *, mode: str = "launch", browser_mod=None):
        self.mode = mode
        self._br = browser_mod

    # ---- 浏览器模块(延迟取,方便测试注入)
    @property
    def browser(self):
        if self._br is None:
            from minicode import browser as b
            self._br = b
        return self._br

    # ---- 第 1 步
    def open_login(self, url: str = LOGIN_URL) -> dict:
        """把教务登录页开在**看得见**的窗口里,然后交给用户。"""
        brs = self.browser
        try:
            brs.ensure_visible()
            res = brs.open_page(url, mode=self.mode, headless=False,
                                wait_ms=brs.MAX_WAIT_MS,
                                max_chars=brs.FRAME_MAX_CHARS)
        except brs.BrowserUnavailable as exc:
            return {"ok": False, "stage": "failed", "msg": str(exc)}
        except Exception as exc:
            return {"ok": False, "stage": "failed",
                    "msg": f"打不开教务登录页:{type(exc).__name__}: {exc}"}
        return {"ok": True, "stage": "need_login",
                "url": str((res or {}).get("url") or url),
                "title": str((res or {}).get("title") or ""),
                "msg": ("登录页已经开在弹出的浏览器窗口里了。请**你自己**输账号密码和"
                        "验证码(学长不碰、也不问),然后在教务系统里点开"
                        "「课表查询 → 学期课表」,把周次选成(全部)。弄好了点「读取课表」。")}

    # ---- 第 2 步
    def read(self, *, goto_table: bool = True) -> dict:
        """读当前那张教务页的学期课表并确定性解析。不写任何文件。"""
        brs = self.browser
        with contextlib.suppress(Exception):
            self._anchor()
        if goto_table:
            # 直接把我们那张页开到学期课表地址,省掉"你自己找菜单"一步;
            # 用户已经停在课表页上时这一步即使失败也无所谓,继续读。
            with contextlib.suppress(Exception):
                brs.open_page(XKB_URL, mode=self.mode, headless=False,
                              wait_ms=brs.MAX_WAIT_MS, max_chars=brs.FRAME_MAX_CHARS)
        grid = self.grid()
        if not grid:
            return {"ok": False, "stage": "no_grid", "classes": [], "notes": [],
                    "unparsed": [], "summary": summarize([]),
                    "msg": ("没在页面上找到强智那张课表表格。"
                            + self._where()
                            + " 请在教务系统里点开「课表查询 → 学期课表」(周次选全部),"
                              "然后让学长用模型整理,或者你手工贴课表。")}
        parsed = parse_grid(grid)
        parsed["stage"] = "ready" if parsed["ok"] else "parsed_failed"
        parsed["summary"] = summarize(parsed["classes"])
        if parsed["ok"]:
            parsed["msg"] = (f"读到 {parsed['summary']['entries']} 条课"
                             f"(约 {parsed['summary']['courses']} 门)"
                             + (f",学期 {parsed['term']}" if parsed["term"] else "")
                             + "。请核对后确认导入 —— 在你点确认之前,学长不会写任何文件。")
        else:
            parsed["msg"] = (parsed["msg"]
                             + " 可以把页面文字交给模型整理一次(会发给你当前配置的模型)。")
        return parsed

    def read_with_model(self, complete) -> dict:
        """第 2 步的退路:整页文字交给模型整理。会把手上的页面文字发给模型。"""
        text = self.page_text()
        parsed = parse_with_model(text, complete)
        parsed["stage"] = "ready" if parsed.get("ok") else "parsed_failed"
        parsed.setdefault("term", "")
        parsed.setdefault("week", "")
        parsed.setdefault("unparsed", [])
        parsed["summary"] = summarize(parsed.get("classes") or [])
        return parsed

    # ---- 取材(全部按帧读,详见 browser.read_frames)
    def _anchor(self) -> dict:
        """优先**接管**用户那张教务页;没有我们自己的页时才新开一张。

        为什么:登录完成后用户可能自己点了别的标签,我们线程那张页可能已经
        不是教务页了 —— 按 URL 找回来比"我们记得的那张"可靠。
        """
        brs = self.browser
        hit = brs.adopt_page(HOST_HINT, mode=self.mode)
        if hit is None:
            hit = brs.open_page(LOGIN_URL, mode=self.mode, headless=False,
                                wait_ms=brs.MAX_WAIT_MS,
                                max_chars=brs.FRAME_MAX_CHARS)
        return hit or {}

    def page_url(self) -> str:
        """当前那张教务页的地址(用来说清"学长现在看到的是哪一页")。"""
        brs = self.browser
        with contextlib.suppress(Exception):
            return str(brs.evaluate("location.href", timeout=10) or "")
        return ""

    def _where(self) -> str:
        href = ""
        with contextlib.suppress(Exception):
            href = self.page_url()
        return f"(现在这张页是 {href})" if href else ""

    def grid(self) -> dict | None:
        """在课表帧里取 #kbtable 网格;没有就读不到(返回 None,不猜)。"""
        brs = self.browser
        for hit in brs.read_frames(GRID_JS, url_like=FRAME_HINT):
            val = hit.get("value")
            if not val:
                continue
            try:
                data = val if isinstance(val, dict) else json.loads(val)
            except (TypeError, json.JSONDecodeError):
                continue
            if data and (data.get("rows") or []):
                return data
        return None

    def page_text(self) -> str:
        brs = self.browser
        best = ""
        for hit in brs.read_frames(TEXT_JS):
            val = str(hit.get("value") or "")
            if len(val) > len(best):
                best = val
        return best[:MODEL_TEXT_CHARS]


# ---------------------------------------------------------------- 收尾:清登录态

def clear_login_state(*, browser_mod=None) -> dict:
    """交完表把教务登录态从本机抹掉:关实例 + 删那个**独立** profile 目录。

    为什么要这个按钮:导入必须真登录,而登录态会留在学长自己用的 browser-profile
    里(不碰你日常浏览器的配置)。学校公用电脑上的人想要"交完就干净",点这个。
    护栏:只删落在数据目录里的那个;被 YINLUXS_BROWSER_PROFILE 指到别处时
    宁可拒绝,也不去 rmtree 一个来路不明的目录。
    """
    import os
    import shutil

    from minicode import paths
    brs = browser_mod
    if brs is None:
        from minicode import browser as brs            # type: ignore[no-redef]
    with contextlib.suppress(Exception):
        brs.shutdown()
    try:
        p = Path(os.environ.get(brs.ENV_PROFILE) or brs.app_path("browser-profile"))
        target = p.resolve()
        root = paths.APP_DIR.resolve()
    except Exception as exc:
        return {"ok": False, "msg": f"读不到登录态目录:{type(exc).__name__}"}
    if root not in target.parents:
        return {"ok": False,
                "msg": f"登录态目录不在学长数据目录里({target}),不替乱删;"
                       "确认后可以手动删这一个文件夹。"}
    if not target.exists():
        return {"ok": True, "msg": "本机没有教务登录态(窗口已关,目录本来就不存在)。"}
    shutil.rmtree(target, ignore_errors=True)
    left = "还剩一点没删掉的(有文件正开着),重开后再点一次。" if target.exists() else ""
    return {"ok": True,
            "msg": ("已关掉导入用的浏览器窗口,并清掉了它的登录状态(独立 profile 已删)。"
                    + left)}
