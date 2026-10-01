from minicode import skills as sk
from minicode.skills import (find_skill, load_skills, skill_dirs,
                             skills_prompt_section)


def _make_skill(tmp_path, name="git-commit", body="正文内容"):
    d = tmp_path / "skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: 测试技能\n---\n{body}", encoding="utf-8")
    return d


def test_load_skills_frontmatter(tmp_path):
    _make_skill(tmp_path)
    skills = load_skills([tmp_path / "skills"])
    assert len(skills) == 1
    assert skills[0].name == "git-commit"
    assert skills[0].description == "测试技能"
    assert "正文内容" in skills[0].body


def test_load_skills_single_md_file(tmp_path):
    d = tmp_path / "skills"
    d.mkdir()
    (d / "review.md").write_text("没有 frontmatter 的内容", encoding="utf-8")
    skills = load_skills([d])
    assert [s.name for s in skills] == ["review"]   # 名字回退为文件名


def test_load_skills_missing_dir(tmp_path):
    assert load_skills([tmp_path / "nope"]) == []


def test_duplicate_name_first_wins(tmp_path):
    _make_skill(tmp_path, "dup")
    d2 = tmp_path / "ws" / "skills" / "dup"
    d2.mkdir(parents=True)
    (d2 / "SKILL.md").write_text("---\nname: dup\n---\n后者", encoding="utf-8")
    skills = load_skills([tmp_path / "skills", tmp_path / "ws" / "skills"])
    assert len(skills) == 1
    assert "正文内容" in skills[0].body   # 应用目录优先


def test_find_skill():
    skills = load_skills([])
    assert find_skill(skills, "x") is None


def test_prompt_section():
    assert skills_prompt_section([]) == ""
    from pathlib import Path
    s = sk.Skill(name="git-commit", description="提交规范", path=Path("x"), body="b")
    text = skills_prompt_section([s])
    assert "- git-commit —— 提交规范" in text
    assert "use_skill" in text


def test_use_skill_tool(ctx, tmp_path, monkeypatch):
    _make_skill(tmp_path, "git-commit", body="提交规范正文")
    monkeypatch.setattr(sk, "skill_dirs", lambda ws: [tmp_path / "skills"])
    monkeypatch.setattr(sk, "usage_path", lambda: tmp_path / "skill-usage.jsonl")
    out = sk.use_skill(ctx, "git-commit")
    assert "提交规范正文" in out
    assert "未找到" in sk.use_skill(ctx, "nope")
    assert "git-commit" in sk.use_skill(ctx, "nope")   # 报错里提示可用技能
    # 成功的调用记进使用账;失败的("nope")不算用过
    summary = sk.usage_summary()
    assert [r["skill"] for r in summary] == ["git-commit"]
    assert summary[0]["uses"] == 1


def test_log_skill_use_isolates_and_aggregates(tmp_path, monkeypatch):
    monkeypatch.setattr(sk, "usage_path", lambda: tmp_path / "skill-usage.jsonl")
    assert sk.log_skill_use("论文助手") is True
    assert sk.log_skill_use("论文助手") is True          # 不去重:次数就是频次
    assert sk.log_skill_use("") is False                 # 空名不记
    summary = sk.usage_summary()
    assert summary == [{"skill": "论文助手", "uses": 2,
                        "last": summary[0]["last"]}]
    assert summary[0]["last"]                            # 最近时刻有据可查


def test_skill_dirs_includes_workspace(tmp_path):
    from minicode.paths import resource_path
    dirs = skill_dirs(tmp_path)
    assert dirs[0] == sk.app_path("skills")
    assert resource_path("skills") in dirs             # 打包内置资源(_MEIPASS)
    assert dirs[-1] == tmp_path / "skills"
    assert len(dirs) == len({str(d) for d in dirs})    # 开发态去重后不重复


# ---------------- use_skill 截断避代码块(v0.9.55) ----------------

def test_clip_body_retreats_to_closed_fence():
    """超限截断回退到最后一个闭合围栏之后:技能正文里是模型要照着抄的模板,
    硬切在 ```python 块中间等于给模型半截模板。"""
    part1 = "开头说明\n\n```python\nprint('step1')\n```\n\n中间说明\n"
    part2 = "```python\nprint('step2')\n```\n\n结尾"
    body = part1 + part2
    limit = len(part1) + len("```python\nprint('ste")   # 恰好切在第二块中间
    clipped = sk._clip_body(body, limit)
    assert clipped.count("```") % 2 == 0, "截断点落在代码块内 = 模型拿到半截模板"
    assert clipped.startswith("开头说明")


def test_clip_body_hard_cut_when_one_giant_fence():
    """整段就是一个超长围栏时保持硬切(没有可回退的闭合点),别死循环。"""
    body = "```python\n" + "x" * 9000 + "\n```"
    clipped = sk._clip_body(body, 8000)
    assert len(clipped) == 8000


# ------------------------------------------- 依赖声明与真核对(v0.9.98)

def _decl_skill(tmp_path, body="正文", **meta):
    d = tmp_path / "skills" / "dep-skill"
    d.mkdir(parents=True)
    front = "".join(f"{k}: {v}\n" for k, v in meta.items())
    (d / "SKILL.md").write_text(f"---\nname: dep-skill\n{front}---\n{body}",
                                encoding="utf-8")
    return load_skills([tmp_path / "skills"])[0]


def test_frontmatter_declares_tools_and_libs(tmp_path):
    s = _decl_skill(tmp_path, tools="write_file, edit_file", libs="pptx, pypdf ，PIL")
    assert s.tools == ("write_file", "edit_file")
    assert s.libs == ("pptx", "pypdf", "PIL")     # 全角逗号也认,空项丢弃


def test_no_declaration_means_no_check(tmp_path):
    s = _decl_skill(tmp_path)
    r = sk.dependency_report(s, available_tools=set(),
                             probe=lambda m: False)
    assert r == {"missing_tools": [], "missing_libs": []}
    assert sk._dependency_warning(s, available_tools=set()) == ""


def test_missing_tools_none_vs_empty_two_semantics(tmp_path):
    s = _decl_skill(tmp_path, tools="write_file")
    # None = 这次没核对;[] = 核对过、都齐 —— 两种语义不许合并(router 同一条老规矩)
    assert sk.dependency_report(s, available_tools=None)["missing_tools"] is None
    assert sk.dependency_report(s, available_tools={"write_file"})["missing_tools"] == []


def test_missing_lib_reports_top_level_package(tmp_path):
    s = _decl_skill(tmp_path, libs="pptx.utils, ghostlib")
    # pptx.utils 查的是顶层包 pptx;ghostlib 探不到 → 缺
    r = sk.dependency_report(s, probe=lambda m: m == "pptx")
    assert r["missing_libs"] == ["ghostlib"]


def test_use_skill_prepends_warning_when_deps_missing(ctx, tmp_path, monkeypatch):
    from minicode.tools.base import Tool
    d = tmp_path / "skills" / "dep-skill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: dep-skill\ndescription: 测试\nlibs: ghostlib, nosuchlib\n---\n正文第一行",
        encoding="utf-8")
    monkeypatch.setattr(sk, "skill_dirs", lambda ws=None: [tmp_path / "skills"])
    monkeypatch.setattr(sk, "usage_path", lambda: tmp_path / "usage.jsonl")
    out = sk.use_skill(ctx, "dep-skill")
    assert out.index("⚠ 依赖缺") < out.index("正文第一行"), "警语必须在正文之前"
    assert "ghostlib、nosuchlib" in out and "照实" in out
    # 齐了(或没声明)时输出与旧版逐字一致
    (d / "SKILL.md").write_text("---\nname: dep-skill\ndescription: 测试\n---\n正文第一行",
                                encoding="utf-8")
    assert sk.use_skill(ctx, "dep-skill") == "# 技能: dep-skill\n\n正文第一行"


def test_declared_tools_must_exist_in_the_registry():
    """仓库里现役技能声明的 tools 必须真在注册表里(声明写错=红,不是静默落空)。"""
    from minicode.tools.registry import build_default_registry
    names = {t.name for t in build_default_registry()}
    for s in load_skills(skill_dirs(None)):
        r = sk.dependency_report(s, available_tools=names)
        assert r["missing_tools"] == [], (s.name, r["missing_tools"])


def test_declared_libs_are_importable_on_this_machine():
    """仓库里现役技能声明的 libs 必须本机真装得到(requirements 与打包清单都管着)。"""
    for s in load_skills(skill_dirs(None)):
        r = sk.dependency_report(s, available_tools=set())
        assert r["missing_libs"] == [], (s.name, r["missing_libs"])


def test_content_skills_declare_the_libs_their_templates_import():
    """声明是承诺:带 python 模板的技能,模板里 import 的第三方库必须已声明
    (反方向:没 import 的不许乱声明)。这里逐个 grep 模板行核对,防漂。"""
    import re
    third_party = {"openpyxl", "docx", "pptx", "pypdf", "PIL"}
    for s in load_skills(skill_dirs(None)):
        found = set()
        for m in re.finditer(r"^\s*(?:from|import)\s+([A-Za-z_][A-Za-z0-9_]*)",
                             s.body, re.M):
            if m.group(1) in third_party:
                found.add(m.group(1))
        assert found <= set(s.libs), (s.name, sorted(found - set(s.libs)))
