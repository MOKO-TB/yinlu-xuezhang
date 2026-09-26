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
    out = sk.use_skill(ctx, "git-commit")
    assert "提交规范正文" in out
    assert "未找到" in sk.use_skill(ctx, "nope")
    assert "git-commit" in sk.use_skill(ctx, "nope")   # 报错里提示可用技能


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
