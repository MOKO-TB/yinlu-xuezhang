"""领域路由(router)与领域档子代理的登记口径测试。

这里钉的是"不编造"这件事:路由登记的技能名与工具名必须真的存在,
命中/未命中的文案必须说清依据,领域档的读写属性决定它能不能并发。
"""
from pathlib import Path

from minicode import router
from minicode.skills import load_skills
from minicode.tools.registry import build_default_registry

REPO = Path(__file__).resolve().parents[1]


def _real_skill_names() -> set[str]:
    return {s.name for s in load_skills([REPO / "skills"])}


# ------------------------------------------------------------ 登记不编造

def test_referenced_skills_exist():
    """路由指向的是已有技能;技能改名/删掉时这里红,而不是运行时指向空气。"""
    have = _real_skill_names()
    assert have, "测试读不到仓库自带的 skills/ 目录"
    for key, spec in router.DOMAINS.items():
        missing = [s for s in spec.skills if s not in have]
        assert not missing, f"{key} 档指向不存在的技能:{missing}"


def test_english_domain_does_not_invent_a_skill():
    """"英语"这一档没有对应技能:靠知识库/校历,skills 必须留空而不是硬凑。"""
    assert router.DOMAINS["english"].skills == ()
    assert "search_knowledge" in router.DOMAINS["english"].tools


def test_every_repo_skill_is_claimed_by_a_domain():
    """新增一份 SKILL.md 却没被任何一档认领时这里红。

    没人认领的技能任何子代理档位都不会先加载它 —— 等于写了一份没人执行的规定。
    要么归进已有档,要么新开一档,要么在这里显式说明它为什么不需要档(比如全局话术)。
    """
    have = _real_skill_names()
    claimed = {s for spec in router.DOMAINS.values() for s in spec.skills}
    # v0.9.102 显式豁免:skill-creator 是**主代理专属**——「把重复流程沉淀成技能」
    # 这件事不该下放给子代理(它们跑在静默通道里,新技能写出去也没人过目)。
    claimed |= {"skill-creator"}
    orphan = sorted(have - claimed)
    assert not orphan, f"这些技能没有档位认领:{orphan}"
    ghost = sorted(claimed - have)
    assert not ghost, f"档位指向不存在的技能:{ghost}"


def test_referenced_tools_exist():
    names = {t.name for t in build_default_registry()}
    for key, spec in router.DOMAINS.items():
        unknown = [t for t in spec.tools if t not in names]
        assert not unknown, f"{key} 档指向不存在的工具:{unknown}"


def test_readonly_domains_only_list_readonly_tools():
    """只读档登记的常用工具必须真的 read_only。

    `subagents.sub_tool_registry` 会把非只读工具整条剪掉,登记里留着它,
    `router.extra_prompt` 就会对该档许诺一个它拿不到的工具(english 曾登记过
    render_chart,提示词写着"常用工具:render_chart"而工具表里根本没有)。
    """
    ro = {t.name for t in build_default_registry() if t.read_only}
    for key, spec in router.DOMAINS.items():
        if not spec.read_only:
            continue
        ghost = [t for t in spec.tools if t not in ro]
        assert not ghost, f"只读档 {key} 登记了非只读工具:{ghost}"


# ------------------------------------------------------------ 新增领域档与关键词归属

def test_searching_books_belongs_to_campus_not_english():
    """搜书词归 campus:english 只留四六级类词,工具表里不再带 library_search。"""
    assert "library_search" not in router.DOMAINS["english"].tools
    assert "library_search" in router.DOMAINS["campus"].tools
    assert router.best("图书馆可以借几本书/索书号在几楼").spec.key == "campus"


def test_new_domain_buckets():
    campus, material = router.DOMAINS["campus"], router.DOMAINS["material"]
    assert campus.skills == ("school-qa", "library-guide", "kb-fallback",
                             "bike-parking-guide")
    assert material.skills == ("pdf", "photo-print", "koubei-extract")
    # 两档都要联网/落盘,做成可写:v0.9.60 起写类型也能并行,只读换不来并发
    assert campus.read_only is False and material.read_only is False
    # 只读档现在只剩 english 一个(通用档另算),这条变了要同步看上面的不变量
    assert [k for k, s in router.DOMAINS.items() if s.read_only] == ["english"]


def test_material_beats_writing_on_document_work():
    """冲突一:PDF/合并/证件照 归 material,写作档不该抢走排版动作。"""
    assert router.best("帮我把这份 PDF 合并成一个文件").spec.key == "material"
    assert router.best("证件照换蓝底排版打印").spec.key == "material"
    assert router.best("奖助学金申请材料合并成一份 pdf").spec.key == "material"
    # 反过来,纯排版动作文档材料档不该赢
    assert router.best("帮我把这份简历改写成投递版").spec.key == "writing"


def test_campus_beats_writing_on_errands():
    """冲突二:证明/盖章 归 campus;申请书仍是 writing。"""
    assert router.best("综测加分的证明去哪里盖章").spec.key == "campus"
    assert router.best("我要写一份入党申请书和活动策划").spec.key == "writing"


def test_campus_wins_on_school_questions():
    for text in ("明年几号放假", "自行车被拖走了怎么领回",
                 "图书馆可以借几本书/索书号在几楼"):
        assert router.best(text).spec.key == "campus", text


# ------------------------------------------------------------ 命中口径

def test_classify_picks_writing_for_thesis():
    match = router.best("帮我把这篇论文的选题和参考文献理一下")
    assert match is not None and match.spec.key == "writing"
    assert "论文" in match.hits and "参考文献" in match.hits


def test_classify_picks_each_domain():
    for text, key in (("这段代码报错帮我看看", "code"),
                      ("把这个班的成绩统计成表格导出来", "data"),
                      ("英语四级还有多久考", "english"),
                      ("我要写一份入党申请书和活动策划", "writing"),
                      ("图书馆期末自习室开到几点", "campus"),
                      ("把课件 pdf 拆成单页", "material")):
        assert router.best(text).spec.key == key, text


def test_ascii_keyword_respects_word_boundary():
    # "git" 不该在 logistics 里被抠出来;整句没领域特征时就不命中
    assert router.classify("the logistics company") == []
    assert router.best("github 上有个合并冲突") is not None


def test_longer_hit_absorbs_shorter_one():
    """"考研英语"命中时不再把"英语"另算一次分(否则每句英语类都同分)。"""
    match = router.best("考研英语怎么准备")
    assert match.spec.key == "english"
    assert "考研英语" in match.hits and "英语" not in match.hits


def test_empty_text_has_no_match():
    assert router.classify("") == [] and router.best("   ") is None


def test_multi_domain_matches_are_all_reported():
    """"错题本导成 anki" 同时像数据也像别的:按分数排,不吞掉第二档。"""
    matches = router.classify("帮我把这份表格里的错题导出成 anki 卡片")
    assert matches and matches[0].spec.key == "data"
    assert matches[0].score >= (matches[1].score if len(matches) > 1 else 0)


# ------------------------------------------------------------ 技能核对与降级

def test_extra_prompt_lists_only_existing_skills():
    spec = router.DOMAINS["writing"]
    prompt = router.extra_prompt(spec, ["paper-writing", "reply-tone"])
    assert "先逐个调用 use_skill" in prompt
    assert "paper-writing" in prompt and "reply-tone" in prompt
    assert "docx" in prompt                     # 本机没有的:改成明说加载不到
    assert "本机技能库里加载不到" in prompt


def test_extra_prompt_without_checking_keeps_the_list():
    """available=None = 没核对过,不该说出"本机没有"这种结论。"""
    prompt = router.extra_prompt(router.DOMAINS["data"], None)
    assert "xlsx" in prompt and "加载不到" not in prompt


def test_extra_prompt_with_empty_skill_library():
    prompt = router.extra_prompt(router.DOMAINS["code"], [])
    assert "本机技能库里加载不到" in prompt and "不要凭名字猜" in prompt


def test_english_prompt_says_no_skill_and_gives_boundary():
    prompt = router.extra_prompt(router.DOMAINS["english"], _real_skill_names())
    assert "先逐个调用 use_skill" not in prompt
    assert "分数线" in prompt                   # 边界照登记写进提示词


# ------------------------------------------------------------ auto 解析与理由

def test_explicit_type_is_not_rewritten():
    assert router.resolve_type("review", "帮我写论文") == ("review", "")


def test_auto_routes_and_explains():
    key, note = router.resolve_type("auto", "帮我把这份简历改写成投递版",
                                    ["resume", "docx"])
    assert key == "writing"
    assert note.startswith("路由") or "派「写作」档" in note
    assert "命中「" in note and "计分" in note
    assert "会先加载技能" in note and "resume" in note


def test_auto_falls_back_to_explore_when_nothing_matches():
    key, note = router.resolve_type("auto", "你看下这个")
    assert key == "explore"
    assert "没命中任何领域关键词" in note


def test_missing_skill_shown_in_route_note():
    _key, note = router.resolve_type("auto", "写论文提纲", ["reply-tone"])
    assert "本机没有的技能已跳过" in note and "paper-writing" in note


# ------------------------------------------------------------ 领域档规格

def test_domain_types_derive_specs_and_read_only_flags():
    from minicode.subagents import type_spec
    assert type_spec("explore").read_only is True
    english = type_spec("english")
    assert english is not None and english.read_only is True
    assert english.label == "英语"
    for key in ("code", "writing", "data", "campus", "material"):
        assert type_spec(key).read_only is False, key
    assert type_spec("campus").label == "校园办事"
    assert type_spec("material").label == "文档材料"
    assert type_spec("nonsense") is None


def test_domain_spec_prompt_loaded_from_real_skill_library(tmp_path):
    """type_spec 不带 workspace 时也要能跑(读不到技能库就照"没核对"处理)。"""
    from minicode.subagents import type_spec
    spec = type_spec("data", tmp_path)
    assert "角色:数据领域子代理" in spec.extra_prompt
    assert spec.type == "data"


# ---------------------------------------------------------------- career 就业档(v0.9.90)

def test_career_keywords_route_to_career():
    """秋招/考公这类求职过程问题归 career 档。"""
    for text in ("帮我排一下秋招的时间线",
                 "国考报名什么时候截止",
                 "三方协议里的违约金怎么看"):
        top = router.classify(text)[0]
        assert top.spec.key == "career", (text, top.spec.key, top.score)


def test_resume_writing_stays_in_writing():
    """简历文书归 writing(v0.9.68 钉过的老边界);career 管投递过程不管文书。"""
    top = router.classify("帮我把简历改写成投递版")[0]
    assert top.spec.key == "writing"


def test_career_is_writable_and_claims_its_skills():
    spec = router.DOMAINS["career"]
    assert spec.read_only is False
    assert "career-timeline" in spec.skills and "resume" in spec.skills


# ---------------------------------------------------------------- study 备考档(v0.9.92)

def test_study_keywords_route_to_study():
    """复习计划/错题本这类备考过程归 study 档。"""
    for text in ("帮我按期末考试日期倒排一个复习计划",
                 "高数错题本整理一下",
                 "下周期中考试,帮我排背诵节奏"):
        top = router.classify(text)[0]
        assert top.spec.key == "study", (text, top.spec.key, top.score)


def test_study_boundaries_with_data_english_campus():
    """三个相邻档的边界:题库导出归 data、背单词归 english、学校考试安排归 campus
    —— study 只管备考的"过程",不抢既有档的地盘。"""
    for text, expect in (("错题导出成 anki 卡片", "data"),
                         ("背单词计划", "english"),
                         ("考试周学校有什么安排", "campus")):
        top = router.classify(text)[0]
        assert top.spec.key == expect, (text, top.spec.key, top.score)


def test_study_is_writable_and_claims_its_skills():
    spec = router.DOMAINS["study"]
    assert spec.read_only is False
    assert "study-prep" in spec.skills and "question-bank" in spec.skills
