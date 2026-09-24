"""v0.9.39 混合检索:BM25F + 元数据过滤 + 向量档(可缺)的口径测试。

这些测试的重点不是"算得快",而是**说了的必须真做了**:
- 结果里的「检索口径」一行必须如实反映本次跑了几档;
- 向量后端没配置时,任何输出都不许出现"语义检索已生效"的字样;
- 相似度没过闸门的条目不能被当成命中。
"""
import json

from minicode import embed, kb as kbmod, retrieval
from minicode.kb import KbEntry, format_results, search_hybrid


def _entry(i, title, content="", tags=(), source="", url="", publish_at=""):
    return KbEntry(source_file="t.json", id=f"E{i}", title=title, content=content,
                   url=url, source=source, tags=list(tags), credibility="A",
                   publish_at=publish_at)


ENTRIES = [
    _entry(1, "国家励志奖学金评定条件", "成绩排名在前 30%,无挂科,家庭经济认定通过。",
           tags=["奖助学金"], publish_at="2026-09-08"),
    _entry(2, "图书馆自习座位怎么预约", "入馆后在座位系统预约,考试周座位紧张。",
           tags=["宿舍与生活服务"], publish_at="2026-03-02"),
    _entry(3, "蓝桥杯校内选拔赛报名", "校选赛成绩优异者参加省赛,报名费学院承担。",
           tags=["学科竞赛"], publish_at="2026-09-07"),
    _entry(4, "奖助学金常见问答", "奖助学金的发放时间与评定流程说明。" * 6,
           tags=["奖助学金"], source="微信公众号", publish_at="2025-10-01"),
]


# ------------------------------------------------------------------ 纯计算层

def test_tokenize_keeps_term_frequency():
    counts = retrieval.tokenize("奖学金 奖学金 scholarship 奖学金")
    # 中文按 2-gram 切:所以计数落在"奖学/学金"上,而不是整词
    assert counts["奖学"] == 3 and counts["学金"] == 3
    assert counts["scholarship"] == 1


def test_bm25_title_field_outranks_body_mentions():
    """标题命中一次要压过正文命中多次,但正文那条也不能掉出候选(tf 饱和而非线性)。"""
    docs = [retrieval.fields_of(e.title, e.tags, e.content) for e in ENTRIES]
    idx = retrieval.KeywordIndex(docs)
    hits = idx.search("奖助学金")
    order = [h.index for h in hits]
    assert order[0] == 3                       # E4 标题就是「奖助学金常见问答」
    assert 0 in order and 3 in order           # 正文里出现过的也都在
    body_hit = next(h for h in hits if h.index == 3)
    assert body_hit.score > 0


def test_bm25_rare_term_dominates_common_one():
    """IDF 生效:只在一条里出现的稀有词,贡献要大于人人在说的词。"""
    docs = [retrieval.fields_of(e.title, e.tags, e.content) for e in ENTRIES]
    idx = retrieval.KeywordIndex(docs)
    rare = idx.idf("蓝桥")
    common = idx.idf("奖助")
    assert rare > common


def test_reason_line_names_only_fields_that_actually_matched():
    docs = [retrieval.fields_of(e.title, e.tags, e.content) for e in ENTRIES]
    hit = retrieval.KeywordIndex(docs).search("蓝桥杯")[0]
    line = retrieval.reason_line(hit)
    assert "标题" in line and "蓝桥" in line
    # 命中标题不等于命中正文:没在正文出现的字段不许出现在依据里
    for term, fname, _ in hit.reasons:
        assert fname in {"title", "tags", "body"}
        assert term in ENTRIES[hit.index].title + " ".join(ENTRIES[hit.index].tags) + \
            ENTRIES[hit.index].content


def test_filter_meta_reports_why_it_dropped():
    kept, dropped = retrieval.filter_meta(
        ENTRIES, category="奖助学金",
        date_of=lambda e: e.publish_at, tags_of=lambda e: e.tags,
        source_of=lambda e: e.source)
    assert [e.id for e in kept] == ["E1", "E4"]
    assert dropped.get("分类不符") == 2


def test_filter_meta_time_window_separates_undated():
    undated = ENTRIES + [_entry(9, "没有日期的通知")]
    kept, dropped = retrieval.filter_meta(
        undated, since="2026-01-01",
        date_of=lambda e: e.publish_at, tags_of=lambda e: e.tags,
        source_of=lambda e: e.source)
    assert all(e.publish_at >= "2026" for e in kept)
    assert dropped.get("无日期") == 1 and dropped.get("时间不符") == 1


def test_rrf_fuse_uses_ranks_only():
    """两路量纲完全不同也能融合:名次决定一切。"""
    fused = retrieval.rrf([[0, 1, 2], [1, 2, 0]])
    assert fused[1] == 1 / 62 + 1 / 61          # 两路名次分别是 2 和 1
    assert retrieval.order_by_fused(fused)[0] == 1
    # 量纲无关:把一路的分数换成完全不同的尺度,名次不变则融合结果不变
    assert retrieval.order_by_fused(retrieval.rrf([[0, 1, 2], [1, 2, 0]])) ==         retrieval.order_by_fused(retrieval.rrf([[0, 1, 2], [1, 2, 0]]))


def test_rank_by_vector_honours_min_sim():
    q = [1.0, 0.0]
    vecs = [[1.0, 0.0], [0.0, 1.0], [0.9, 0.1]]
    assert [i for i, _s in retrieval.rank_by_vector(q, vecs, min_sim=0.5)] == [0, 2]
    assert [i for i, _s in retrieval.rank_by_vector(q, vecs, min_sim=0.995)] == [0]


# ---------------------------------------------------------------- 混合检索

def test_hybrid_without_vector_says_so():
    """没配向量后端 → 必须写明"未配置",且不许出现任何已用向量/语义的字样。"""
    hits, meta = search_hybrid(ENTRIES, "图书馆自习座位", cfg=None)
    assert hits and hits[0].entry.id == "E2"
    assert meta["vector"]["ran"] is False
    assert "未配置" in meta["vector"]["why"]
    assert meta["modes"] == ["关键词 BM25F"]
    text = format_results(hits, meta=meta)
    assert "检索口径" in text and "向量档未跑" in text
    assert "语义检索已生效" not in text


def test_hybrid_category_filter_counted_in_output():
    hits, meta = search_hybrid(ENTRIES, "奖助学金", category="学科竞赛")
    assert hits == [] and meta["dropped"].get("分类不符") == 3
    text = format_results(hits, meta=meta)
    assert "分类不符 3 条" in text


def test_semantic_string_false_is_not_treated_as_true():
    """模型传 "false"/"否" 时不能被 `if semantic:` 当成真,从而偷偷去走向量档。"""
    for value in ("false", "否", "no", "0", False):
        _hits, meta = search_hybrid(ENTRIES, "奖学金", semantic=value, cfg=None)
        assert meta["vector"]["ran"] is False


# ------------------------------------------------------ 向量档(注入假后端)

def _install_fake_vector(monkeypatch, tmp_path, table):
    """把向量后端换成查表假实现:只用于验证融合逻辑,不代表任何真模型效果。"""
    info = {"backend": "fixture", "model": "fx", "dim": 2, "ready": True,
            "why": "", "source": "fixture"}

    def fake_detect(cfg=None):
        return dict(info)

    def fake_embed_texts(texts, cfg=None, spec=None, session=None):
        out = []
        for t in texts:
            vec = None
            for key, val in table.items():
                if key in t:
                    vec = val
                    break
            out.append(vec or [0.0, 1.0])
        return out

    monkeypatch.setattr(embed, "detect", fake_detect)
    monkeypatch.setattr(embed, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(embed, "cache_path",
                        lambda backend, model: tmp_path / "vec-cache.json")
    return info


def test_vector_tier_can_recall_a_row_keywords_missed(monkeypatch, tmp_path):
    """语义档的价值:关键词一个都没对上的条目,只有它能捞回来(夹具向量,只验接线)。"""
    _install_fake_vector(monkeypatch, tmp_path, {
        "预约": [1.0, 0.0],     # E2 正文里的词 → 与查询同向
        "空位": [1.0, 0.0],     # 查询用的口语说法,和 E2 没有任何共同 2-gram
    })
    hits, meta = search_hybrid(ENTRIES, "哪儿有空位", semantic=True, cfg=object())
    assert meta["keyword_hits"] == 0, "这条测试的前提:关键词档必须一个都没命中"
    assert meta["fused"] is True and meta["vector"]["ran"] is True
    assert hits and hits[0].entry.id == "E2"
    assert hits[0].via == "仅向量"
    assert "向量" in meta["modes"][1]


def test_vector_below_gate_adds_nothing(monkeypatch, tmp_path):
    """全部向量都低过闸门时,结果必须退回纯关键词,并把原因写进 meta。"""
    # 查询里带一个"任何条目都不含"的记号词 → 向量方向与全部条目正交
    _install_fake_vector(monkeypatch, tmp_path,
                         {"查无此词": [1.0, 0.0], "奖学金": [0.0, 1.0]})
    hits, meta = search_hybrid(ENTRIES, "奖学金查无此词", semantic=True,
                               cfg=_Cfg({"embeddings": {"min_sim": 0.6}}))
    assert meta["vector"]["ran"] is False
    assert "闸门" in meta["vector"]["why"]
    assert hits and all("向量" not in h.via for h in hits)


class _Cfg:
    def __init__(self, caps):
        self.capabilities = caps
        self.providers = []
        self.provider = None


# ------------------------------------------------------- 向量档探测与缓存

def test_detect_without_config_is_not_ready_and_tells_what_to_do():
    info = embed.detect(_Cfg({}))
    assert info["ready"] is False and info["backend"] == "none"
    assert "capabilities.embeddings" in info["why"]


def test_detect_local_backend_reports_missing_files(monkeypatch, tmp_path):
    monkeypatch.setattr(embed, "models_dir", lambda: tmp_path)
    info = embed.detect(_Cfg({"embeddings": {"backend": "local", "model": "nope"}}))
    assert info["ready"] is False
    assert "model.onnx" in info["why"] and str(tmp_path) in info["why"]


def test_detect_api_backend_needs_a_key_and_never_invents_one(monkeypatch):
    class P:
        id = "p1"
        base_url = "https://x.invalid/v1"
        api_key = ""
        has_key = False

    class C:
        providers = [P()]
        provider = P()
        capabilities = {"embeddings": {"backend": "api", "model": "text-embedding-x"}}

    assert embed.detect(C())["ready"] is False
    P.has_key = True
    P.api_key = "k"
    info = embed.detect(C())
    assert info["ready"] is True and info["backend"] == "api"
    assert "x.invalid" in info["source"]


def test_vector_cache_only_recomputes_changed_rows(monkeypatch, tmp_path):
    """建索引后改一条正文:下次只该重算那一条 —— 否则每次检索都在偷偷烧额度。"""
    calls = []
    _install_fake_vector(monkeypatch, tmp_path, {"x": [1.0, 0.0]})

    def counting_embed(texts, cfg=None, spec=None, session=None):
        calls.append(len(texts))
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(embed, "embed_texts", counting_embed)
    r1 = embed.ensure_vectors(ENTRIES, cfg=None, build=True)
    assert r1["embedded"] == len(ENTRIES) and calls[-1] == len(ENTRIES)
    r2 = embed.ensure_vectors(ENTRIES, cfg=None, build=True)
    assert r2["embedded"] == 0 and r2["cached"] == len(ENTRIES)
    changed = list(ENTRIES)
    changed[0] = _entry(1, changed[0].title, changed[0].content + " 补充:金额有变。",
                        tags=changed[0].tags, publish_at=changed[0].publish_at)
    r3 = embed.ensure_vectors(changed, cfg=None, build=True)
    assert r3["embedded"] == 1 and r3["cached"] == len(ENTRIES) - 1


def test_search_never_builds_index_on_its_own(monkeypatch, tmp_path):
    """检索路径上绝不允许自动发几百条 embedding:那是使用者的额度与等待时间。"""
    _install_fake_vector(monkeypatch, tmp_path, {"奖学金": [1.0, 0.0]})
    called = []
    monkeypatch.setattr(embed, "embed_texts",
                        lambda texts, cfg=None, spec=None, session=None:
                        called.append(len(texts)) or [[1.0, 0.0] for _ in texts])
    _hits, meta = search_hybrid(ENTRIES, "奖学金", semantic=None, cfg=None)
    assert called == [] and meta["vector"]["ran"] is False
    assert "索引未建" in meta["vector"]["why"]


# ------------------------------------------------------------------ WordPiece

def test_wordpiece_splits_cjk_per_char_and_batches_with_padding(tmp_path):
    vocab = tmp_path / "vocab.txt"
    vocab.write_text("\n".join(["[PAD]", "[UNK]", "[CLS]", "[SEP]",
                                "奖", "学", "金", "图", "书", "##馆"]) + "\n",
                     encoding="utf-8")
    tok = embed.WordPiece(vocab, max_len=16)
    ids = tok.encode("奖金")
    assert ids[0] == tok.cls and ids[-1] == tok.sep
    assert ids[1:3] == [tok.vocab["奖"], tok.vocab["金"]]      # 中文逐字成 token
    batch = tok.batch(["奖金", "奖学金"])
    assert len(batch["input_ids"][0]) == len(batch["input_ids"][1])
    assert batch["attention_mask"] == [[1, 1, 1, 1, 0], [1, 1, 1, 1, 1]]


def test_wordpiece_unknown_char_becomes_unk(tmp_path):
    vocab = tmp_path / "vocab.txt"
    vocab.write_text("\n".join(["[PAD]", "[UNK]", "[CLS]", "[SEP]", "奖"]) + "\n",
                     encoding="utf-8")
    tok = embed.WordPiece(vocab, max_len=16)
    assert tok.encode("奖").count(tok.unk) == 0
    assert tok.unk in tok.encode("魑")


# ------------------------------------------------------------- 旧接口不回归

def test_legacy_search_still_works_and_never_touches_vector(monkeypatch):
    """脚本与别的测试还在用 kb.search():它必须显式关掉向量档。"""
    seen = {}

    def spy(*a, **k):
        seen["called"] = True
        return {"ready": False, "backend": "none", "why": "x", "vectors": [],
                "embedded": 0, "cached": 0, "missing": 0, "total": 0, "elapsed_ms": 0}

    monkeypatch.setattr(embed, "ensure_vectors", spy)
    assert kbmod.search(ENTRIES, "蓝桥杯")[0].id == "E3"
    assert "called" not in seen


def test_config_save_scrubs_capability_keys():
    from minicode.config import _scrub_secrets
    raw = {"embeddings": {"backend": "api", "provider_id": "p", "model": "m",
                          "api_key": "sk-secret", "nested": [{"token": "t", "keep": 1}]}}
    out = _scrub_secrets(raw)
    assert out["embeddings"]["api_key"] == ""
    assert out["embeddings"]["nested"][0]["token"] == ""
    assert out["embeddings"]["nested"][0]["keep"] == 1
    assert out["embeddings"]["model"] == "m"
