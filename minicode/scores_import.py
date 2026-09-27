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
        parsed = self._read_scores()
        if parsed.get("ok"):
            parsed["stage"] = "ready"
            s = parsed["summary"]
            lead = (f"读到 {s['entries']} 条成绩"
                    + (f",覆盖 {len(s['semesters'])} 个学期" if s["semesters"] else ""))
            if s.get("no_credit"):
                lead += f";{len(s['no_credit'])} 行没有学分(绩点会跳过它们)"
            parsed["msg"] = (lead + "。请核对后确认导入 —— 在你点确认之前,"
                             "学长不会写任何文件;确认会**整表覆盖**现有存档。")
            return parsed
        return parsed

    def read_with_model(self, complete) -> dict:
        """第 2 步的退路:整页文字交给模型整理(会真的发出这段文字)。"""
        text = self.page_text()[:MODEL_TEXT_CHARS]
        parsed = scores.parse_scores_with_model(text, complete)
        parsed["stage"] = "ready" if parsed.get("ok") else "parsed_failed"
        return parsed

    # ---- 取材复用父类的 page()/page_text();这里只负责"认不认得出成绩表"。
    def _read_scores(self) -> dict:
        parsed = scores.parse_score_table(self.page() or {})
        if parsed.get("ok"):
            return parsed
        # 表格路没认出来:给"至少读到了什么"的诚实失败原因,而不是干巴巴一句失败。
        page = self.page()
        if page:
            return {**parsed, "stage": "no_grid", "msg": parsed["msg"] + self._where()}
        return {"ok": False, "stage": "no_grid", "rows": [],
                "msg": ("当前页读不到任何表格。请先在教务系统里点开「成绩查询」,"
                        "选好学期、点了「查询」,再回来点「读取成绩」。" + self._where())}
