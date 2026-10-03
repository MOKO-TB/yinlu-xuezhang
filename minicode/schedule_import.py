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

# 文法与版式识别都在 site_parse;本模块只管"开哪一页、读哪一帧、什么时候问人"。
# 旧名字(parse_grid/_entry/_norm_periods/GRID_JS…)在这里继续转出去:
# v0.9.23 的测试、冻结清单与脚本都吃这些名字。
from minicode.site_parse import (  # noqa: F401
    GRID_JS, TABLE_JS, TEXT_JS, _cell_entries, _entry_qiangzhi, _norm_periods,
    _norm_weeks, _rows_of, load_dump, parse_page)

LOGIN_URL = "https://jw.educationgroup.cn/gzasc/"
XKB_URL = "https://jw.educationgroup.cn/gzasc_jsxsd/xskb/xskb_list.do"
HOST_HINT = "educationgroup.cn"   # 教务页所在站点(接管页面时按它找)
FRAME_HINT = "xskb"               # 学期课表所在帧的 URL 片段
MODEL_TEXT_CHARS = 16000          # 兜底交给模型的页面文字上限

# 旧名字留着当门面:v0.9.23 的测试/冻结清单/脚本都吃这几个,含义一字不改。
_entry = _entry_qiangzhi


# ---------------------------------------------------------------- 确定性解析(已下沉到 site_parse)

def _host_of(url: str) -> str:
    """取 URL 的主机名(接管页面时按它找)。坏地址回空串,不抛。"""
    from urllib.parse import urlparse
    try:
        return (urlparse(str(url or "")).hostname or "").lower()
    except Exception:
        return ""


def parse_grid(grid) -> dict:
    """一张表(强智 dump 或整页 dump)→ 课表记录。文法在 site_parse 里。"""
    return parse_page(grid if isinstance(grid, (dict, list)) else {})


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
        # 第 1 步真正开过的站点主机名(接管时优先按它找)。空 = 没开过/老配置。
        self._last_host = ""

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
        url = url or LOGIN_URL
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
        # 记住这一步真正开的是哪个站点:后面「接管那张页」按它找,比按内置的
        # HOST_HINT 找准 —— 学校换域名(或界面里填了别的地址)时 Hint 会落空。
        self._last_host = _host_of(str((res or {}).get("url") or url))
        return {"ok": True, "stage": "need_login",
                "url": str((res or {}).get("url") or url),
                "title": str((res or {}).get("title") or ""),
                "msg": ("登录页已经开在弹出的浏览器窗口里了。请**你自己**输账号密码和"
                        "验证码(学长不碰、也不问),然后在教务系统里点开"
                        "「课表查询 → 学期课表」,把周次选成(全部)。弄好了点「读取课表」。")}

    # ---- 接管(不导航)
    def _anchor_hints(self) -> list[str]:
        """按什么去找"那张教务页"。

        顺序有讲究:这一步之前真正开过的站点 > 内置站点提示 > (仅 launch 模式)任意一页。
        最后那条兜底只敢在 **launch** 用:那个浏览器实例是学长自己起的独立 profile,
        里面不该有别人的标签页;attach 模式接的是用户日常浏览器,按"任意一页"接管
        就等于可能把他正在看的网站当成教务页来读 —— 那是读错东西,不是读不到。
        """
        hints = [h for h in (getattr(self, "_last_host", ""), HOST_HINT) if h]
        if self.mode == "launch":
            hints.append("")
        return hints

    def anchor_existing(self) -> dict | None:
        """只**接管**已经开着的那一页,绝不导航、绝不新开。

        为什么每一步都要它:桥的每一次 js_api 调用跑在**自己的线程**上,而 browser
        的会话按线程 ident 分格(`_session_key`)—— 上一步「打开登录页」那条调用留下
        的会话不属于这一步的线程,直接问"我这张页"永远得到 None,报出来就是
        「BrowserError: 还没有打开的页面」。接管是按 URL 找回人的页,比"我们记得的那张"可靠。
        """
        brs = self.browser
        for hint in self._anchor_hints():
            try:
                hit = brs.adopt_page(hint, mode=self.mode)
            except Exception:
                hit = None
            if hit:
                return hit
        return None

    def no_page_msg(self, what: str) -> dict:
        """接管不到时给人看的那一句:说清"为什么读不到"和下一步点哪里,不猜、不装成功。"""
        return {"ok": False, "stage": "no_page", "rows": [], "classes": [],
                "msg": ("没找到还开着的教务页," + what + "读不到。多半是那个登录窗口"
                        "被关掉了,或者你切回了自己日常的浏览器(学长只认自己弹出来的"
                        "那个窗口)。请点「① 打开登录页」→ 在弹出的窗口里登录并点开查询"
                        "结果页 → 再点读取。")}

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
        parsed = self._parse_any()
        if parsed is None:
            return {"ok": False, "stage": "no_grid", "kind": None, "adapter": "",
                    "classes": [], "notes": [],
                    "unparsed": [], "summary": summarize([]), "diff": None,
                    "msg": ("强智那张课表表和整页通用识别都没读到表格" + self._where()
                            + "。请在教务系统里点开「课表查询 → 学期课表」(周次选全部),"
                              "然后让学长用模型整理,或者你手工贴课表。")}
        parsed["summary"] = summarize(parsed["classes"])
        if parsed["ok"]:
            parsed["stage"] = "ready"
            parsed["diff"] = self._diff(parsed["classes"])
            d = parsed["diff"]
            lead = (f"与本机课表比对:{d['msg']}" if d and d.get("has_existing")
                    else "本机还没有课表,这次是首次导入。")
            parsed["msg"] = (lead + f" 读到 {parsed['summary']['entries']} 条课"
                             f"(约 {parsed['summary']['courses']} 门)"
                             + (f",学期 {parsed['term']}" if parsed["term"] else "")
                             + (f"(按{parsed['adapter']}文法认出)" if parsed.get("adapter") else "")
                             + "。请核对后确认导入 —— 在你点确认之前,学长不会写任何文件。")
            return parsed
        parsed["diff"] = None
        if parsed.get("kind") is None and parsed.get("unparsed") == []:
            # 认不出星期列 = 这根本不是一张课表(考试安排/书目/通知)。
            # 这时候最坏的做法是"凑出一份课表存下去",所以直接不收。
            parsed["stage"] = "not_timetable"
            parsed["msg"] = ("这一页学长读到了内容,但它**不是课表**(没有星期一~星期日那一列)。"
                             + self._where() + " 这不是课表,我不替你存 —— "
                             "要读考试安排/书目这类表,直接让学长用 page_parse 看结构。")
        else:
            parsed["stage"] = "parsed_failed"
            parsed["msg"] = (parsed["msg"]
                             + " 可以把页面文字交给模型整理一次(会发给你当前配置的模型)。")
        return parsed

    def _parse_any(self) -> dict | None:
        """先按强智那张表认,认不出再把整页表格交给通用退路(见 site_parse)。

        两条路都跑完仍没认出课表时,回"至少读到了表"的那条 —— 它给的失败原因
        比"什么都没有"更有用(结构变了 vs 页面没开对,是两件不同的事)。
        """
        results: list[dict] = []
        for dump in (self.grid(), self.page()):
            if not dump:
                continue
            res = parse_page(dump)
            if res.get("ok"):
                return res
            results.append(res)
        for res in results:
            if res.get("notes") or res.get("unparsed"):
                return res
        return results[0] if results else None

    @staticmethod
    def _diff(classes) -> dict | None:
        """覆盖前先算一次变动(只读,不写任何文件)。算不动就别拦读数。"""
        from minicode import schedule
        try:
            return schedule.compare(classes)
        except Exception:
            return None

    def read_with_model(self, complete) -> dict:
        """第 2 步的退路:整页文字交给模型整理。会把手上的页面文字发给模型。"""
        text = self.page_text()
        parsed = parse_with_model(text, complete)
        parsed["stage"] = "ready" if parsed.get("ok") else "parsed_failed"
        parsed.setdefault("term", "")
        parsed.setdefault("week", "")
        parsed.setdefault("unparsed", [])
        parsed["summary"] = summarize(parsed.get("classes") or [])
        parsed["diff"] = (self._diff(parsed["classes"]) if parsed.get("ok") else None)
        return parsed

    # ---- 取材(全部按帧读,详见 browser.read_frames)
    def _anchor(self) -> dict:
        """优先**接管**用户那张教务页;没有我们自己的页时才新开一张。

        为什么:登录完成后用户可能自己点了别的标签,我们线程那张页可能已经
        不是教务页了 —— 按 URL 找回来比"我们记得的那张"可靠。
        找的顺序与 `anchor_existing` 同一份(先第 1 步真正开过的站点,再内置 Hint);
        两条路共用一份口径,免得课表认得回来的页、成绩认不得。
        """
        brs = self.browser
        hit = self.anchor_existing()
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

    def page(self):
        """通用取材:整页(含所有内嵌帧)的所有表格 + 下拉框当前值。

        与 grid() 的分工:grid() 只问强智那张 #kbtable;page() 不问学校长什么样,
        把每张表都搬回来交给 site_parse 去认。认不出就算了,绝不硬凑。
        """
        brs = self.browser
        for hit in brs.read_frames(TABLE_JS):
            dump = load_dump(hit.get("value"))
            if dump and _rows_of(dump):
                return dump
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
