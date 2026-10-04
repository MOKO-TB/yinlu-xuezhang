"""教务成绩导入(v0.9.59,H 件):复用课表导入那套浏览器舞蹈,只换读什么、存什么。

流程与 v0.9.23 的课表导入同一条规矩(由人点三下最诚实):
①把教务登录页开在**看得见**的窗口里,登录由同学本人在那个窗口完成 ——
学长从不代填、也从不索要账号密码;②同学自己在教务系统里点开「成绩查询」、
选好学期、点了「查询」,再点「读取成绩」—— **我们不导航**:强智的成绩页地址
各校不同,还要选学期、传表单,替同学点就是在猜;③预览核对才落盘 scores.json。

隐私口径与课表导入一字不差:登录态不进对话、不落盘;页面文本只在内存里,
解析完就丢。解析是确定性优先(表头认列,见 scores.parse_score_table),
认不出才退"把页面文字交给模型整理"—— 那一步会把页面文字真的发给你配的模型,
所以必须是人点了按钮才发生。
"""
from __future__ import annotations

from minicode import scores
from minicode.schedule_import import (  # noqa: F401
    LOGIN_URL, MODEL_TEXT_CHARS, ImportFlow)


class ScoresFlow(ImportFlow):
    """①开登录页 → 你自己登录并打开「成绩查询」(选学期、点查询) → ②读取。"""

    def open_login(self, url: str = "") -> dict:
        """与课表导入同一个登录页;差别只在下一步要同学去哪儿。"""
        res = super().open_login(url or LOGIN_URL)
        if res.get("ok"):
            res["msg"] = ("登录页已经开在弹出的浏览器窗口里了。请**你自己**输账号密码和"
                          "验证码(学长不碰、也不问),然后在教务系统里点开「成绩查询」,"
                          "**选好学期、点了「查询」**,让成绩表出现在页面上 —— "
                          "弄好了点「读取成绩」。")
        return res

    def read(self) -> dict:
        """读**当前**那张教务页的成绩表。绝不导航、绝不提交表单 —— 那是同学的事。"""
        # 先接管,再问页:桥的每一次 js_api 调用跑在**自己的线程**上,而 browser 的
        # 会话按线程 ident 分格(`_session_key`)—— ①「打开登录页」那条调用留下的会话
        # 根本不属于②这条线程,直接问"我这张页"永远拿到 None,报出来的就是用户
        # 截图里那句「BrowserError: 还没有打开的页面」。课表那条路每一步都 _anchor()
        # 所以没暴露;成绩这条路**不能导航**(替同学跳回登录页会把他刚查出来的那张
        # 表丢掉),所以只接管、不新开 —— 接管不到就明说,不猜、也不装成"没有成绩"。
        if self.anchor_existing() is None:
            return self.no_page_msg("成绩")
        parsed = self._read_scores()
        if parsed.get("ok"):
            parsed["stage"] = "ready"
            s = parsed["summary"]
            lead = (f"读到 {s['entries']} 条成绩"
                    + (f",覆盖 {len(s['semesters'])} 个学期" if s["semesters"] else ""))
            if s.get("no_credit"):
                lead += f";{len(s['no_credit'])} 行没有学分(绩点会跳过它们)"
            if (parsed.get("scanned") or {}).get("frames", 0) > 1:
                lead += f"(翻了 {parsed['scanned']['frames']} 个帧)"
            parsed["msg"] = (lead + "。请核对后确认导入 —— 在你点确认之前,"
                             "学长不会写任何文件;确认会**整表覆盖**现有存档。")
            return parsed
        return parsed

    def read_with_model(self, complete) -> dict:
        """第 2 步的退路:整页文字交给模型整理(会真的发出这段文字)。"""
        if self.anchor_existing() is None:
            return self.no_page_msg("要交给模型整理的那页文字")
        text = self.page_text()[:MODEL_TEXT_CHARS]
        parsed = scores.parse_scores_with_model(text, complete)
        parsed["stage"] = "ready" if parsed.get("ok") else "parsed_failed"
        return parsed

    # ---- 取材:逐张页 × 逐帧(父类 scans()),这里只负责"认不认得出成绩表"。
    def _read_scores(self) -> dict:
        """把**每张还开着的教务页、每个帧**都试一遍,取认得出的那一张表。

        为什么不是"读当前那张页的第一张表"(v0.9.112 之前就是这么写的):
        同学登录后人往往停在框架页 `framework/xsMain.jsp`,它自己就带着菜单表,
        于是旧路在第一帧上交了差 → 报「这一页没有认出成绩表」,而屏幕上明明有那张
        成绩表(用户 2026-10-04 的图 p3/p4 正是这个形状)。挑哪张表是"看得懂表头的人"
        的事,不能由取材那一层代劳。
        """
        shots = [d for d in self.scans()
                 if any(isinstance(t, dict) and (t.get("rows") or [])
                        for t in (d.get("tables") or []))]
        if not shots:
            return {"ok": False, "stage": "no_grid", "rows": [],
                    "msg": ("还开着的教务页里一个表格都没捞到(把每一页的每个内嵌帧"
                            "都翻过了)。请先在教务系统里点开「成绩查询」,选好学期、"
                            "点了「查询」,让成绩表出现在页面上,再点「读取成绩」。"
                            + self._where())}
        best = None
        best_at = ""
        seen: list[str] = []
        pages: set[str] = set()
        for dump in shots:
            pages.add(str(dump.get("_page") or ""))
            parsed = scores.parse_score_table(dump)
            if parsed.get("ok"):
                if best is None or len(parsed["rows"]) > len(best["rows"]):
                    best = parsed
                    best_at = str(dump.get("_frame") or dump.get("_page") or "")
            else:
                seen += [s for s in (parsed.get("seen") or []) if s]
        if best is not None:
            best["scanned"] = {"pages": len(pages), "frames": len(shots)}
            best["from"] = best_at[:120]
            return best
        tip = (" 学长翻到的表头长这样:" + " ｜ ".join(seen[:4]) + "。") if seen else ""
        return {"ok": False, "stage": "no_grid", "rows": [], "seen": seen[:6],
                "scanned": {"pages": len(pages), "frames": len(shots)},
                "msg": (f"翻过 {len(pages)} 张页、{len(shots)} 个帧,都没认出成绩表"
                        "(要有「课程/成绩」这样的表头)。请确认成绩查询页还开着、"
                        "学期已选好、点了「查询」。" + tip + self._where())}

