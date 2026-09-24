"""混合检索的纯计算层:BM25F + 元数据过滤 + RRF 融合 + 余弦。

这一层刻意不碰磁盘、不碰网络、不 import 任何第三方包:打包配置
(packaging/gcode.spec 的 excludes)把 numpy 排除在包外以控制体积,而本地
知识库只有几百条,暴力余弦在纯 Python 下也就是几十万次乘加,几十毫秒量级。

三档检索的分工写死在这里,省得上层含混:
- 关键词档 = 2-gram 召回 + BM25F 排序(本模块,**永远可用**);
- 向量档 = minicode/embed.py 提供后端,取不到后端就没有这一档;
- 融合 = RRF(只按名次融合,所以两档的量纲不同也不会互相压垮)。

上层输出必须如实写明本次跑了几档。把 BM25 说成"语义检索"是本项目不允许的
那类装饰:界面/回答里每一句能力描述都要能追到真的执行分支。
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Iterable, Sequence

# Okapi BM25 的饱和与长度归一参数:文献常用区间 k1∈[1.2,2.0]、b=0.75,取保守值。
K1 = 1.4
B = 0.75
# RRF 平滑常数(Cormack 等提出的排名融合式里的 k),取原论文常用的 60。
RRF_K = 60

# 字段权重沿用 kb.py 既有口径(标题 3 / 标签 2 / 正文 1),只是从"命中集合求交"
# 升级为"带词频与文档长度归一的 BM25",这样"重复提到"和"只提一次"才分得开。
FIELD_WEIGHTS = {"title": 3.0, "tags": 2.0, "body": 1.0}

_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_LATIN_RUN = re.compile(r"[a-z0-9]+")
_ISO_HEAD = re.compile(r"(\d{4})\D?(\d{2})\D?(\d{2})")


def tokenize(text: str) -> Counter:
    """中文按 2-gram、英文数字按词,返回**词频**(BM25 要 tf,不能像旧实现那样去重)。"""
    text = (text or "").lower()
    counts: Counter = Counter()
    for word in _LATIN_RUN.findall(text):
        counts[word] += 1
    for seg in _CJK_RUN.findall(text):
        if len(seg) == 1:
            counts[seg] += 1
        else:
            for i in range(len(seg) - 1):
                counts[seg[i:i + 2]] += 1
    return counts


def iso_date(value: str) -> str:
    """从五花八门的入库时间里取 YYYY-MM-DD;取不到返回空串(调用方要如实标注,别当 0 日)。"""
    m = _ISO_HEAD.search(str(value or ""))
    return "-".join(m.groups()) if m else ""


class Hit:
    """一条命中:分数 + 名次 + **命中依据**(哪个词在哪个字段、贡献了多少)。"""

    __slots__ = ("index", "score", "reasons")

    def __init__(self, index: int, score: float, reasons: list[tuple[str, str, float]]):
        self.index = index
        self.score = score
        self.reasons = reasons      # [(词, 字段, 该词该字段的贡献)]


class KeywordIndex:
    """对一批条目建 BM25F 索引。建索引是纯内存计算,几百条毫秒级,所以每次调用现建。"""

    def __init__(self, field_maps: Sequence[dict[str, Counter]]):
        self.docs = list(field_maps)
        self.n = len(self.docs)
        # 每个字段各自的平均长度(BM25 的长度归一按字段算,标题 3 个字和正文 300 字
        # 混在一起归一会把标题的高分压没)
        self.avgdl: dict[str, float] = {}
        for fname in FIELD_WEIGHTS:
            lens = [sum(d.get(fname, Counter()).values()) for d in self.docs]
            self.avgdl[fname] = (sum(lens) / len(lens)) if lens else 0.0
        df: Counter = Counter()
        for d in self.docs:
            present: set[str] = set()
            for fname in FIELD_WEIGHTS:
                present.update(d.get(fname, Counter()))
            for term in present:
                df[term] += 1
        self.df = df

    def __len__(self) -> int:
        return self.n

    def idf(self, term: str) -> float:
        df = self.df.get(term, 0)
        return math.log(1.0 + (self.n - df + 0.5) / (df + 0.5))

    def search(self, query: str, top_k: int = 50) -> list[Hit]:
        """返回按 BM25F 分数降序的命中(分数为 0 的词不会进命中,所以空串查询得空列表)。"""
        terms = tokenize(query)
        if not terms or not self.docs:
            return []
        out: list[Hit] = []
        for i, doc in enumerate(self.docs):
            score = 0.0
            reasons: list[tuple[str, str, float]] = []
            for term in terms:
                idf = self.idf(term)
                if idf <= 0.0:
                    continue
                for fname, weight in FIELD_WEIGHTS.items():
                    tf = doc.get(fname, Counter()).get(term, 0)
                    if not tf:
                        continue
                    dl = sum(doc.get(fname, Counter()).values())
                    avg = self.avgdl.get(fname, 0.0) or 1.0
                    denom = tf + K1 * (1.0 - B + B * dl / avg)
                    contrib = weight * idf * (tf * (K1 + 1.0)) / denom
                    score += contrib
                    reasons.append((term, fname, contrib))
            if score > 0.0:
                reasons.sort(key=lambda r: -r[2])
                out.append(Hit(i, score, reasons))
        out.sort(key=lambda h: (-h.score, h.index))
        return out[:max(1, int(top_k))]


def fields_of(title: str = "", tags: Iterable[str] = (), body: str = "") -> dict[str, Counter]:
    return {"title": tokenize(title),
            "tags": tokenize(" ".join(str(t) for t in tags if t)),
            "body": tokenize(body)}


def rrf(rankings: Sequence[Sequence[int]], weights: Sequence[float] | None = None,
        k: int = RRF_K) -> dict[int, float]:
    """Reciprocal Rank Fusion:score(d)=Σ wᵢ/(k+rankᵢ)。

    传进来的每一路都是**已排好序的下标列表**;融合只用名次,所以关键词分和余弦
    相似度量纲不同也不影响结果 —— 这也是我们不需要给两档调归一化系数的原因。
    """
    out: dict[int, float] = {}
    ws = list(weights) if weights else [1.0] * len(rankings)
    for rank_list, w in zip(rankings, ws):
        for rank, item in enumerate(rank_list, start=1):
            out[item] = out.get(item, 0.0) + w / (k + rank)
    return out


def order_by_fused(scores: dict[int, float]) -> list[int]:
    """融合分数降序;同分时按下标升序,保证结果稳定可测(不让排序随 dict 顺序抖)。"""
    return [i for i, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / math.sqrt(na * nb)


def rank_by_vector(query_vec: Sequence[float], vecs: Sequence[Sequence[float]],
                   min_sim: float = 0.0) -> list[tuple[int, float]]:
    """暴力余弦,返回 (下标, 相似度) 降序;相似度低于 min_sim 的一律丢掉。

    min_sim 这一道闸门是**语义上的诚实**:开了向量也不能让"随便什么条目"都算命中,
    否则未命中的问句会被塞进一堆不相干结果,比不返回更难查。
    """
    if not query_vec:
        return []
    scored = [(i, cosine(query_vec, v)) for i, v in enumerate(vecs) if v]
    scored = [(i, s) for i, s in scored if s >= min_sim > 0.0] if min_sim > 0.0 else scored
    scored.sort(key=lambda pair: (-pair[1], pair[0]))
    return scored


def filter_meta(items: Sequence, *,
                category: str | None = None,
                source: str | None = None,
                since: str | None = None,
                until: str | None = None,
                date_of=lambda e: getattr(e, "publish_at", ""),
                tags_of=lambda e: getattr(e, "tags", []),
                source_of=lambda e: getattr(e, "source", "")) -> tuple[list, dict]:
    """元数据过滤,返回 (留下的, 各理由排除数)。

    排除数一定要交回上层写进结果里:"因为分类不符少了 40 条"和"库里就 3 条"
    是两件完全不同的事,前者不说用户会以为学校没发过通知。
    """
    kept: list = []
    dropped = {"分类不符": 0, "来源不符": 0, "时间不符": 0, "无日期": 0}
    want_cat = (category or "").strip().lower()
    want_src = (source or "").strip().lower()
    since_d, until_d = iso_date(since or ""), iso_date(until or "")
    for e in items:
        if want_cat:
            blob = " ".join(str(t) for t in (tags_of(e) or [])).lower()
            if want_cat not in blob:
                dropped["分类不符"] += 1
                continue
        if want_src and want_src not in str(source_of(e) or "").lower():
            dropped["来源不符"] += 1
            continue
        if since_d or until_d:
            d = iso_date(date_of(e))
            if not d:
                dropped["无日期"] += 1
                continue
            if since_d and d < since_d:
                dropped["时间不符"] += 1
                continue
            if until_d and d > until_d:
                dropped["时间不符"] += 1
                continue
        kept.append(e)
    return kept, {k: v for k, v in dropped.items() if v}


_FIELD_CN = {"title": "标题", "tags": "标签", "body": "正文"}


def reason_line(hit: Hit, max_terms: int = 3) -> str:
    """把命中依据写成一句人话:BM25 最高的几个词分别落在哪个字段。"""
    seen: list[str] = []
    used: set[str] = set()
    for term, fname, _contrib in hit.reasons:
        if term in used:
            continue
        used.add(term)
        fields = sorted({f for t, f, _ in hit.reasons if t == term},
                        key=lambda f: -FIELD_WEIGHTS.get(f, 0))
        seen.append(f"{term}←{'/'.join(_FIELD_CN.get(f, f) for f in fields)}")
        if len(seen) >= max_terms:
            break
    return "、".join(seen)
