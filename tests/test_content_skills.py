"""v0.9.30 D 批:内容技能(论文/题库/笔记/简历定向)的护栏与代码模板.

技能正文是给模型读的提示词,但它同时**带着要真跑的脚本**:模板语法错了、
诚实口径写漏了,表现是"用户机器上一次运行报 SyntaxError"或"学长替他编了
参考文献"。所以这里两件事都当测试钉住:
1. 口径关键字必须在正文里(不代写/不抓真题/扫描版先走 OCR 且走不通才如实说/不许编数字);
2. 正文里的 python 块必须能编译,模板函数还要 exec 出来真调用一次。
"""
from __future__ import annotations

import re
from pathlib import Path

from minicode import skills as sk

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"


def _skill(name: str) -> sk.Skill:
    loaded = sk.load_skills([SKILLS])
    found = sk.find_skill(loaded, name)
    assert found is not None, f"内置技能 {name} 没被加载(目录名/frontmatter 错了?)"
    return found


def _blocks(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return re.findall(r"```python\n(.*?)```", text, re.S)


def _run(path: Path, marker: str) -> dict:
    """把含 marker 的那个 python 块 exec 成命名空间(只用 stdlib 的块才这么测)."""
    src = next((b for b in _blocks(path) if marker in b), None)
    assert src, f"{path.name} 里没有含 {marker!r} 的 python 块 —— 模板改名了要说清"
    ns: dict = {}
    exec(compile(src, str(path), "exec"), ns)
    return ns


# ---------------- 加载与形态 ----------------

def test_the_four_content_skills_are_all_installed():
    names = {s.name for s in sk.load_skills([SKILLS])}
    assert {"paper-writing", "question-bank", "lecture-notes"} <= names
    assert "resume" in names                      # 定向优化改的是老技能


def test_name_matches_folder_and_description_is_clean():
    for name in ("paper-writing", "question-bank", "lecture-notes", "resume"):
        s = _skill(name)
        assert s.name == name, "frontmatter 的 name 必须与目录名一致(否则 use_skill 找不到)"
        assert len(s.description) > 20, "description 是给模型看的触发说明,不能写成一句话"
        assert "**" not in s.description, "清单注入的是纯文本,写进 ** 会露在侧栏和技能面板里"
        assert s.body.count("\n# ") >= 3, "正文要分节(什么时候用/口径/模板/坑),一屏提示词没用地"


def test_bodies_fit_the_skill_limit():
    """use_skill 回填全文时按 SKILL_BODY_LIMIT 截断 —— 超了就是**尾部口径被静默砍掉**。"""
    for name in ("paper-writing", "question-bank", "lecture-notes", "resume"):
        s = _skill(name)
        assert len(s.body) < sk.SKILL_BODY_LIMIT, (
            f"{name} 正文 {len(s.body)} 字 ≥ 上限 {sk.SKILL_BODY_LIMIT},"
            "模型拿到的会是截断版,后面的坑与口径等于没写")


# ---------------- 每一技能的诚实口径 ----------------

def test_paper_skill_refuses_ghostwriting_and_invented_references():
    s = _skill("paper-writing")
    assert "不代写正文" in s.description, "边界要写在 description 里,模型才知道什么时候该退"
    body = s.body
    assert "替他写正文" in body and "做不了" in body
    assert "绝不编造文献" in body
    assert "【待补" in body, "缺字段要写成待补,而不是替他猜一个页码"
    assert "GB/T 7714" in body and "顺序编码" in body
    assert "同义词硬替换" in body, "降重要给出不能做的做法(同义词替换最容易被系统判得更差)"


def test_question_bank_only_formats_what_he_gave():
    body = _skill("question-bank").body
    assert "不去抓别人的真题" in _skill("question-bank").description
    assert "做不了" in body and "帮我把某某大学某某课十年真题" in body
    assert "绝不替他补答案" in body
    for fmt in ("Anki", "GIFT", "Aiken"):
        assert fmt in body
    assert "答案错位" in body, "错位(背下错误答案)比缺答案更危险,必须有这一条"


def test_lecture_notes_routes_scanned_pdf_to_ocr_then_stays_honest():
    body = _skill("lecture-notes").body
    assert "扫描版" in body
    # v0.9.172+:扫描版不再是一句"做不了"就推掉 —— 先交给 ocr_image 逐页抽图识别,
    # 而且它给的页号才是真的页码锚点。这条测试因此从"钉住拒绝"改成"钉住路由"。
    assert "ocr_image" in body and ".pdf" in body, \
        "扫描版 PDF 要先喂 ocr_image,而不是直接告诉用户做不了"
    assert "OCR 结果只当待核对草稿" in body, "OCR 出来的是草稿,公式/上下标必须让他自己核"
    # 但 OCR 也走不通(没引擎、模型看不见图)时仍要如实说,不许"按章节名编一版"。
    assert "OCR 也走不通" in body and "虚构内容" in body, \
        "兜底那句诚实口径不能因为接了 OCR 就删掉"
    assert "【此处有一张图片" in body, "抽不到的地方要留标记,不能空着让他以为课件没写"
    assert "补充,非课件原文" in body
    assert "不编考点" in body


def test_resume_has_two_tracks_and_no_number_invention():
    body = _skill("resume").body
    assert "## 技术岗" in body and "## 非技术岗" in body
    assert "一份简历只投一类岗位" in body
    assert "不许编" in body and "绝不虚构" in body
    assert "绝不能给一版加经历" in body, "定向只能重排与换重点,不能加内容"


# ---------------- 代码模板:能编译 + 真跑一次 ----------------

def test_every_python_block_in_every_skill_compiles():
    """技能脚本是模型要照抄去 py_script 跑的,少一个括号就变成用户机器上的报错。"""
    bad = []
    for path in sorted(SKILLS.glob("*/SKILL.md")):
        for i, src in enumerate(_blocks(path), 1):
            try:
                compile(src, f"{path}[{i}]", "exec")
            except SyntaxError as e:
                bad.append(f"{path.parent.name} 第 {i} 块:{e.msg}(第 {e.lineno} 行)")
    assert not bad, "下列技能里的 python 模板有语法错误:\n" + "\n".join(bad)


def test_gbt7714_renderer_marks_missing_fields_instead_of_guessing():
    path = SKILLS / "paper-writing" / "SKILL.md"
    ns = _run(path, "def fmt(r, n)")
    full = ns["fmt"]({"type": "J", "authors": ["张三", "李四"], "title": "空间利用率调查",
                      "source": "大学图书馆学报", "year": "2023",
                      "vol": "41", "issue": "3", "pages": "55-63"}, 1)
    assert full.startswith("[1] 张三，李四.")
    assert "[J]" in full and "大学图书馆学报, 2023, 41(3): 55-63." in full
    # 缺页码/缺作者:必须是待补,不能是空字符串,也不能冒出一个像样的数字
    gap = ns["fmt"]({"type": "J", "authors": [], "title": "无题"}, 2)
    assert "【待补:页码】" in gap and "【待补:作者】" in gap
    assert not re.search(r"\d{4}, \d+\(\d+\): \d+", gap)
    four = ns["fmt"]({"type": "M", "authors": list("abcd"), "title": "书名",
                      "pub_place": "北京", "publisher": "某社", "year": "2021",
                      "pages": "1-9"}, 3)
    assert four.startswith("[3] a，b，c，等.")          # 4 位以上只列前三 + 等
    assert "[M]" in four and "北京: 某社, 2021: 1-9." in four


def test_question_bank_validator_catches_the_two_ways_to_lose_answer():
    path = SKILLS / "question-bank" / "SKILL.md"
    ns = _run(path, "def check(qs)")
    ok = {"type": "single", "stem": "题干?",
          "options": {"A": "1", "B": "2"}, "answer": "A"}
    assert ns["check"]([ok]) == []
    # ① 答案不在选项里(导进去 = 他背下一个错答案)② 选项不从 A 起连续(Aiken 必错位)
    bad = [dict(ok, answer="C"),
           dict(ok, options={"B": "1", "C": "2"}, answer="B"),
           dict(ok, type="single", answer="AB"),
           {"type": "judge", "stem": "判断?", "answer": "对"}]
    msgs = ns["check"](bad)
    assert len(msgs) == 4 and all(m.startswith(("第 1", "第 2", "第 3", "第 4")) for m in msgs)
    assert any("不在选项" in m for m in msgs) and any("不连续" in m for m in msgs)
    assert any("判断题答案要写 True/False" in m for m in msgs)


def test_anki_exporter_keeps_one_line_per_record():
    path = SKILLS / "question-bank" / "SKILL.md"
    ns = _run(path, "def anki_tsv(")
    q = {"stem": "第一行\t第二行\n第三行", "type": "single",
         "options": {"A": "甲", "B": "乙"}, "answer": "A",
         "explain": "解析一\n解析二", "tags": ["第一章", "错题"]}
    text = ns["anki_tsv"]([q])
    lines = text.split("\n")
    assert lines[0] == "#separator:tab" and "#html:true" in lines[1]
    data = [ln for ln in lines if ln and not ln.startswith("#")]
    assert len(data) == 1, f"一条记录必须只占一行(多出的是没逃掉的换行):{data!r}"
    record = data[0]
    assert record.count("\t") == 2, "字段里的 Tab 必须换成空格,否则一条记录变成三列"
    assert "<br>" in record
    assert "第一行 第二行<br>第三行" in record
    assert record.endswith("第一章 错题") and "解析一<br>解析二" in record


def test_overlap_checker_finds_the_contiguous_run():
    path = SKILLS / "paper-writing" / "SKILL.md"
    ns = _run(path, "def overlap(a, b, n=8)")
    overlap, norm = ns["overlap"], ns["norm"]
    copied = "调查结果显示,图书馆地下一层的座位长期处于空置状态,而二楼则一座难求。"
    src = "调查发现,图书馆地下一层的座位长期处于空置状态,而二楼则一座难求。"
    rate, frag = overlap(copied, src)
    assert 0.5 < rate < 1, f"只换了开头几个字要报得出高重合(实测 {rate:.0%}),但不能报成 100%"
    assert frag == "图书馆地下一层的座位长期处于空置状态而二楼则一座难求", \
        f"该抓的是那段连续照抄:{frag!r}"
    # 真改写过的(换了主语、加了自家数据)不该被抓出来
    rewritten = "我们在 3 月对 120 名学生的问卷里看到,地下一层常常整排座位空着。"
    assert overlap(rewritten, src) == (0.0, "")
    assert norm("调 查（结果）,\n") == "调查结果", "标点与空白要先去掉,否则重合率被标点稀释"


# ---------------- 随包分发 ----------------

def test_skills_ship_with_the_package():
    """技能整目录进包;新技能只要落在 skills/ 就随包,不需要改 spec —— 但要说清这点。"""
    spec = (ROOT / "packaging" / "gcode.spec").read_text(encoding="utf-8")
    assert '"skills"' in spec
    for name in ("paper-writing", "question-bank", "lecture-notes"):
        assert (SKILLS / name / "SKILL.md").exists()
