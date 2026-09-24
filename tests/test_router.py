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


def test_referenced_tools_exist():
    names = {t.name for t in build_default_registry()}
    for key, spec in router.DOMAINS.items():
        unknown = [t for t in spec.tools if t not in names]
        assert not unknown, f"{key} 档指向不存在的工具:{unknown}"


# ------------------------------------------------------------ 命中口径

def test_classify_picks_writing_for_thesis():
    match = router.best("帮我把这篇论文的选题和参考文献理一下")
    assert match is not None and match.spec.key == "writing"
    assert "论文" in match.hits and "参考文献" in match.hits


def test_classify_picks_each_domain():
    for text, key in (("这段代码报错帮我看看", "code"),
                      ("把这个班的成绩统计成表格导出来", "data"),
                      ("英语四级还有多久考", "english"),
                      ("我要写一份入党申请书和活动策划", "writing")):
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
    for key in ("code", "writing", "data"):
        assert type_spec(key).read_only is False, key
    assert type_spec("nonsense") is None


def test_domain_spec_prompt_loaded_from_real_skill_library(tmp_path):
    """type_spec 不带 workspace 时也要能跑(读不到技能库就照"没核对"处理)。"""
    from minicode.subagents import type_spec
    spec = type_spec("data", tmp_path)
    assert "角色:数据领域子代理" in spec.extra_prompt
    assert spec.type == "data"
