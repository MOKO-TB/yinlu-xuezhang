import json

from minicode import kb as kbmod
from minicode.kb import (format_results, kb_prompt_section, kb_stats,
                         load_kb, search)


def _make_kb(tmp_path):
    d = tmp_path / "kb"
    d.mkdir()
    (d / "notices.json").write_text(json.dumps([
        {"id": "N001", "title": "国家励志奖学金申请通知", "category": "奖助学金",
         "publish_at": "2026-09-08 09:30", "deadline": "2026-09-14 17:00",
         "body": "奖励标准5000元/人,请于9月14日前提交材料。",
         "source": "学生工作办公室",
         "url": "https://example.edu.cn/n001"},
        {"id": "N002", "title": "蓝桥杯校内选拔赛报名", "category": "学科竞赛",
         "publish_at": "2026-09-07 14:20", "deadline": "2026-09-15 12:00",
         "body": "校选赛成绩优异者统一参加省赛,报名费学院承担。",
         "source": "竞赛工作组"},
    ], ensure_ascii=False), encoding="utf-8")
    (d / "knowledge.json").write_text(json.dumps([
        {"id": "A001", "category": "考研", "title": "本校195人考研上岸",
         "content": "含华南理工大学等985高校,民办考研不是小概率事件。",
         "tags": ["考研", "升学数据"], "credibility": "A",
         "source_url": "https://example.edu.cn/a001"},
        {"id": "K001", "category": "学业规划", "title": "大一怎么规划",
         "content": "先过四级,加一两个社团,搞清楚专业学什么。",
         "tags": ["规划"], "credibility": "D-待核实"},
    ], ensure_ascii=False), encoding="utf-8")
    return d


def test_load_kb_maps_heterogeneous_fields(tmp_path):
    _make_kb(tmp_path)
    entries = load_kb([tmp_path / "kb"])
    assert len(entries) == 4
    n1 = next(e for e in entries if e.id == "N001")
    assert n1.url == "https://example.edu.cn/n001"
    assert "5000元" in n1.content and n1.deadline == "2026-09-14 17:00"
    a1 = next(e for e in entries if e.id == "A001")
    assert a1.credible and "考研" in a1.tags
    k1 = next(e for e in entries if e.id == "K001")
    assert not k1.credible


def test_search_ranks_title_hits_first(tmp_path):
    entries = load_kb([_make_kb(tmp_path)])
    results = search(entries, "励志奖学金申请")
    assert results and results[0].id == "N001"


def test_search_category_filter(tmp_path):
    entries = load_kb([_make_kb(tmp_path)])
    results = search(entries, "报名", category="学科竞赛")
    assert results
    assert all("竞赛" in " ".join(e.tags) for e in results)


def test_search_no_match(tmp_path):
    entries = load_kb([_make_kb(tmp_path)])
    assert search(entries, "外星人侵略地球") == []


def test_format_results_includes_url_and_credibility(tmp_path):
    entries = load_kb([_make_kb(tmp_path)])
    results = search(entries, "励志奖学金")
    text = format_results(results)
    assert "来源网址: https://example.edu.cn/n001" in text
    assert "可信度" in text


def test_format_results_uses_reply_tone_on_non_a(tmp_path):
    entries = load_kb([_make_kb(tmp_path)])
    text = format_results([next(e for e in entries if e.id == "K001")])
    # 非 A 条目改用 reply-tone 话术指引(亲测+来源 / 暂无实测记录),不再用"未经核实"免责句
    assert "reply-tone" in text
    assert "亲测有效" in text
    assert "未经核实" not in text


def test_kb_stats(tmp_path):
    stats = kb_stats([_make_kb(tmp_path)])
    counts = {s["file"]: s["count"] for s in stats}
    assert counts == {"notices.json": 2, "knowledge.json": 2}


def test_prompt_section_mentions_urls(tmp_path):
    text = kb_prompt_section([_make_kb(tmp_path)])
    assert "search_knowledge" in text and "来源网址" in text and "4 条" in text
    assert kb_prompt_section([tmp_path / "nope"]) == ""


def test_prompt_section_teaches_category_filter_and_media_links(tmp_path):
    """口语提问(“学校有什么比赛”)命中率低,得告诉模型用 category 锁官网栏目;
    视频页/图文指引页的链接靠正文里的 观看地址/查看地址 行区分。"""
    text = kb_prompt_section([_make_kb(tmp_path)])
    assert "category" in text
    assert "宿舍与生活服务" in text and "学科竞赛" in text
    assert "观看地址" in text and "查看地址" in text


def test_tool_handler_end_to_end(ctx, tmp_path, monkeypatch):
    _make_kb(tmp_path)
    monkeypatch.setattr(kbmod, "kb_dirs", lambda ws: [tmp_path / "kb"])
    out = kbmod.search_knowledge(ctx, "蓝桥杯报名")
    assert "蓝桥杯" in out and "来源" in out
    assert "知识库为空" not in out


def test_tool_handler_empty_kb(ctx, tmp_path, monkeypatch):
    monkeypatch.setattr(kbmod, "kb_dirs", lambda ws: [tmp_path / "empty"])
    assert "知识库为空" in kbmod.search_knowledge(ctx, "奖学金")


def test_snippet_chars_returns_more_detail(ctx, tmp_path, monkeypatch):
    """问细节时可调大 snippet_chars,一次拿到更多原文,不必再去爬官网。"""
    d = tmp_path / "kb"
    d.mkdir()
    body = "评审条件:" + "家庭经济困难且成绩优良。" * 60
    (d / "crawl-x.json").write_text(json.dumps(
        [{"id": "C1", "title": "国家励志奖学金评审通知", "content": body,
          "url": "https://x.edu.cn/n1", "credibility": "A", "tags": ["奖助学金"]}],
        ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(kbmod, "kb_dirs", lambda ws: [d])
    short = kbmod.search_knowledge(ctx, "国家励志奖学金")
    long = kbmod.search_knowledge(ctx, "国家励志奖学金", snippet_chars=800)
    assert len(long) > len(short)
    assert long.count("家庭经济困难") > short.count("家庭经济困难")
    assert "https://x.edu.cn/n1" in long          # 长摘要同样带来源网址


def test_hit_logs_entry_ids_for_appraisal(ctx, tmp_path, monkeypatch):
    """命中后把条目 id 记进 kb-hits.jsonl(v0.9.80):评价绑条目的唯一来源。"""
    _make_kb(tmp_path)
    monkeypatch.setattr(kbmod, "kb_dirs", lambda ws: [tmp_path / "kb"])
    seen = []
    from minicode import growth
    monkeypatch.setattr(growth, "log_kb_hits",
                        lambda q, ids: seen.append((q, list(ids))) or True)
    kbmod.search_knowledge(ctx, "蓝桥杯报名")
    assert seen and seen[0][0] == "蓝桥杯报名"
    assert all(i for i in seen[0][1]), "id 不允许是空串"
    seen.clear()
    kbmod.search_knowledge(ctx, "这个词库里绝对没有XYZ")
    assert seen == []                      # 未命中走缺口账,不记命中账


def test_no_hit_carries_fallback_hint_and_stays_recognizable():
    """未命中即给联网入库路径(v0.9.92,第三梯队 ⑦):路径说死在工具回话里,
    不靠模型自觉;is_no_hit 的子串判据必须仍然成立(B0 计数靠它)。"""
    out = kbmod.format_results([])
    assert kbmod.NO_HIT_TEXT in out and kbmod.FALLBACK_HINT in out
    assert kbmod.is_no_hit(out) is True
    assert "web_search" in out and "kb_suggest" in out
    assert "征得用户同意" in out                # 入库闸的口径随行
    hit = kbmod.format_results([kbmod.KbEntry(
        id="x", source_file="t.json", title="t", content="c",
        url="", source="")])
    assert kbmod.FALLBACK_HINT not in hit       # 命中不给联网指引


def test_search_knowledge_leads_with_the_corpus_cutoff(tmp_path, monkeypatch):
    """检索回话的第一行是「本地语料截至 …」(§6 #45 的第二半)。

    为什么摆在「检索口径**之前**」:模型正要用这批条目回答"最近/今年"的时候,
    拦得住它的那个数必须第一个被读到。数字只有一个来源(`notices.corpus_age`);
    没有归档就整行不出现 —— 宁可不提,也不写一句"这台机器从没抓过"。
    """
    import json as _json
    import shutil
    import time as _t

    from minicode import notices, paths

    _make_kb(tmp_path)
    monkeypatch.setattr(paths, "APP_DIR", tmp_path / "app")   # 命中登记别落进真数据目录
    monkeypatch.setattr(kbmod, "kb_dirs", lambda workspace: [tmp_path / "kb"])

    class _Ctx:
        workspace = tmp_path

    when = _t.strftime(notices.CRAWL_STAMP_FMT, _t.localtime(_t.time() - 2 * 86400))
    d = tmp_path / "crawled" / "www.gzasc.edu.cn"
    d.mkdir(parents=True)
    (d / "index.json").write_text(_json.dumps(
        {"site": "http://www.gzasc.edu.cn/", "crawled_at": when}, ensure_ascii=False),
        encoding="utf-8")

    out = kbmod.search_knowledge(_Ctx(), "奖学金")
    assert out.splitlines()[0] == (f"本地语料截至 {when}(2 天前,来自 www.gzasc.edu.cn,"
                                   "共 1 个站点有归档)—— 比这个时刻更新的事,本地库里没有。")
    assert "检索口径" in out and "国家励志奖学金" in out

    shutil.rmtree(tmp_path / "crawled")
    out2 = kbmod.search_knowledge(_Ctx(), "奖学金")
    assert "本地语料截至" not in out2 and "国家励志奖学金" in out2
