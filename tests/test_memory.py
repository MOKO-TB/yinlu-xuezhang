"""长期记忆测试:保存/列出/检索/忘记/去重/上限/提示词注入。"""

from minicode.memory import (inject_line, list_entries, load_entries, memory,
                             memory_path, prompt_section)
from minicode.tools.base import ToolContext


def _ctx(tmp_path):
    return ToolContext(workspace=tmp_path, config=None)


def test_save_and_list_roundtrip(tmp_path):
    ctx = _ctx(tmp_path)
    r = memory(ctx, "save", category="user", content="偏好深色主题")
    assert "已记住(用户偏好)" in r
    r = memory(ctx, "save", category="project", content="本项目禁止后端代码")
    assert "已记住(项目约定)" in r

    out = memory(ctx, "list")
    assert "共 2 条" in out
    assert "偏好深色主题" in out and "本项目禁止后端代码" in out
    # 文件人工可读、可编辑
    text = memory_path(tmp_path).read_text(encoding="utf-8")
    assert text.startswith("# 长期记忆")
    assert "user]" in text and "project]" in text


def test_save_deduplicate(tmp_path):
    ctx = _ctx(tmp_path)
    memory(ctx, "save", category="user", content="喜欢简洁回复")
    r = memory(ctx, "save", category="user", content="喜欢简洁回复")
    assert "已经存在" in r
    assert len(load_entries(tmp_path)) == 1


def test_category_alias_and_validation(tmp_path):
    ctx = _ctx(tmp_path)
    assert "已记住(用户偏好)" in memory(ctx, "save", category="偏好",
                                        content="x")
    assert "未知记忆类别" in memory(ctx, "save", category="xyz", content="x")
    assert "不能为空" in memory(ctx, "save", category="user", content="  ")


def test_search_ranking(tmp_path):
    ctx = _ctx(tmp_path)
    memory(ctx, "save", category="project", content="引路学长产品使用纯前端架构")
    memory(ctx, "save", category="user", content="用户准备参加蓝桥杯竞赛")
    memory(ctx, "save", category="feedback", content="回复要更简短")
    out = memory(ctx, "search", query="蓝桥杯")
    assert "命中 1 条" in out and "蓝桥杯" in out
    assert "没有与" in memory(ctx, "search", query="量子纠缠")


def test_forget(tmp_path):
    ctx = _ctx(tmp_path)
    memory(ctx, "save", category="project", content="部署走 Docker")
    memory(ctx, "save", category="project", content="文档放 docs 目录")
    out = memory(ctx, "forget", query="Docker")
    assert "已忘记 1 条" in out
    out = memory(ctx, "list")
    assert "Docker" not in out and "docs 目录" in out
    assert "没有匹配" in memory(ctx, "forget", query="Docker")


def test_entry_limit_drops_oldest(tmp_path):
    ctx = _ctx(tmp_path)
    from minicode import memory as mem_mod
    saved_limit = mem_mod.ENTRY_LIMIT
    try:
        mem_mod.ENTRY_LIMIT = 5
        for i in range(7):
            memory(ctx, "save", category="project", content=f"条目{i}")
        entries = load_entries(tmp_path)
        assert len(entries) == 5
        assert entries[0].content == "条目2"   # 最旧两条被淘汰
    finally:
        mem_mod.ENTRY_LIMIT = saved_limit


def test_prompt_section_injection(tmp_path):
    ctx = _ctx(tmp_path)
    assert prompt_section(tmp_path) == ""          # 无记忆时不注入
    memory(ctx, "save", category="project", content="使用 vitest 跑测试")
    text = prompt_section(tmp_path)
    assert "# 长期记忆(跨会话,自动加载)" in text
    assert "使用 vitest 跑测试" in text
    assert "action=save" in text


def test_memory_file_is_human_editable(tmp_path):
    ctx = _ctx(tmp_path)
    memory(ctx, "save", category="reference", content="校历在教务处官网")
    p = memory_path(tmp_path)
    # 用户手工追加一条 → 读取正常
    p.write_text(p.read_text(encoding="utf-8") +
                 "- [2026-01-01][reference] 手工添加的参考\n", encoding="utf-8")
    out = list_entries(tmp_path)
    assert "手工添加的参考" in out


# ------------------------------------------------------------------ 注入预算
# 钉的是"按条让位"这一整类,而不是某一句文案。以前是 text[:4000] 裸切,
# 坏在两面:半条记忆以完整句子的口气进系统提示词;超限必丢项目级(新的那批)。

def _both_levels(tmp_path, n_user, n_project, chars):
    """两级各写 n 条、每条约 chars 字,返回写入顺序(旧 → 新)。"""
    from minicode.memory import MemoryEntry, save_to
    user = [MemoryEntry(date=f"2026-01-{i + 1:02d}", category="user",
                         content=f"用户级{i}:" + "偏" * chars)
            for i in range(n_user)]
    proj = [MemoryEntry(date=f"2026-02-{i + 1:02d}", category="project",
                        content=f"项目级{i}:" + "约" * chars)
            for i in range(n_project)]
    gpath = tmp_path / "GLOBAL.md"
    save_to(gpath, user)
    save_to(memory_path(tmp_path), proj)
    return user, proj, gpath


def test_overlong_memory_never_splits_an_entry(tmp_path):
    user, proj, gpath = _both_levels(tmp_path, 30, 30, 200)
    text = prompt_section(tmp_path, global_path=gpath)
    whole = {line for e in user + proj for line in inject_line(e).splitlines()}
    shown = [ln for ln in text.splitlines() if ln.startswith("- [")]
    assert shown                                   # 确实注入了东西
    for ln in shown:
        assert ln in whole                          # 每一行都是一条完整记忆


def test_both_levels_survive_and_newest_is_visible(tmp_path):
    """旧坏法是整块切掉项目级;让位方向应与写侧 ENTRY_LIMIT 一致 —— 先丢最旧。"""
    user, proj, gpath = _both_levels(tmp_path, 30, 30, 200)
    text = prompt_section(tmp_path, global_path=gpath)
    assert "用户偏好" in text and "项目约定" in text
    assert "用户级29:" in text and "项目级29:" in text    # 最新的一定在
    assert "用户级0:" not in text and "项目级0:" not in text   # 最旧的先让位


def test_what_is_left_out_is_reported(tmp_path):
    """不许悄悄截了还写着"全部" —— 没注入的条数要说出口,并给一条取回的路。"""
    user, proj, gpath = _both_levels(tmp_path, 30, 30, 200)
    text = prompt_section(tmp_path, global_path=gpath)
    assert "条没列出" in text
    assert "action=list" in text and "action=search" in text
    shown = len([ln for ln in text.splitlines() if ln.startswith("- [")])
    assert f"本次注入最近 {shown} 条" in text        # 报的数要和真注入的对得上


def test_under_budget_injects_everything_silently(tmp_path):
    """没超限时一个字都不许多 —— 注入面前缀稳定是压缩那一层的命门。"""
    user, proj, gpath = _both_levels(tmp_path, 3, 2, 20)
    text = prompt_section(tmp_path, global_path=gpath)
    assert "没列出" not in text
    for e in user + proj:
        assert e.content in text


def test_two_giant_entries_are_not_dropped_whole(tmp_path):
    """单条就超预算时宁可略微超出,也不让一本记忆整本消失。"""
    user, proj, gpath = _both_levels(tmp_path, 1, 1, 3000)
    text = prompt_section(tmp_path, global_path=gpath)
    assert "用户级0:" in text and "项目级0:" in text
    assert "没列出" not in text                     # 一条都没让位,就不许报让位
