"""本地知识库(kb/*.json):通知与经验资料的关键词检索,回答时输出来源网址。

数据是异构的(通知类用 body/url,经验类用 content/source_url/tags),
加载时统一映射为 KbEntry。检索用中文 2-gram 打分:
标题命中×3 + 标签/分类×2 + 正文×1(与引路学长产品 PLAN §11 的权重一致)。
条目的 credibility(A=可靠来源 / D=演示或待核实)必须随结果输出,
模型回答时对非 A 级经验内容按 reply-tone 技能话术表述(亲测+来源,或如实"暂无实测记录")。
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from minicode.paths import app_path

SNIPPET_LIMIT = 160
SNIPPET_MAX = 1200
MAX_CONTENT_LIMIT = 1500
KB_RESULT_LIMIT = 5

# "没查到"这句话只有一处真相:`format_results` 用它拼回话,`is_no_hit` 用它判回话。
# 为什么要有 `is_no_hit` —— B0「反复搜空」要在**工具结果文本**上判断这次有没有空手,
# 而那串字面量原先只写在 format_results 里面;两边各写一份,改一次文案就静默失灵
# (表现为"连着搜空也不再提示",而且没有任何东西会红)。
NO_HIT_TEXT = "知识库中没有匹配的内容。"


def is_no_hit(result) -> bool:
    """这段工具结果是不是"知识库里没查到"。非字符串一律 False(宁可少标不乱标)。"""
    return isinstance(result, str) and NO_HIT_TEXT in result

# 向量档的相似度闸门:低于它就不算命中。这只是一个**保守闸门**,不是"经验阈值"
# —— 本仓没有拿真模型跑过阈值曲线,所以不吹某个数是最佳值;要调就在
# config.json 的 capabilities.embeddings.min_sim 里给,给了就用给的。
DEFAULT_MIN_SIM = 0.35
# 关键词一路先取多少候选送去参与融合(融合是名次级的,候选宽一点不心疼)
HYBRID_CANDIDATES = 12

# ------------------------------------------------------------------ 来源可信度
#
# credibility 管"内容真不真",围栏管"话听不听"(见 provenance.py),两轴正交。
# A 级的语义是"按栏目白名单抓取的学校官网",不是"任何爬回来的东西"。
# 抓取入库时只有下面这些域名能标 A,其余一律降 B-抓取 —— 否则一条投毒页面
# 入库后会被之后**每一次** search_knowledge 当权威内容召回(注入防御 M4)。

# 兼容打包后没有 scripts/ 目录的情况,兼作兵库:学校官网及其子系统
DEFAULT_TRUSTED_SCHOOL_HOSTS = (
    "www.gzasc.edu.cn",
    "gzasc.edu.cn",
    "jwc.gzasc.edu.cn",        # 教务处
    "tsg.gzasc.edu.cn",        # 图书馆
    "xsc.gzasc.edu.cn",        # 学生处
    "zsw.gzasc.edu.cn",        # 招生办
    "dw.gzasc.edu.cn",         # 党建网
    "tw.gzasc.edu.cn",         # 团委
    "cxcy.gzasc.edu.cn",       # 创新创业学院
)


def trusted_school_hosts() -> tuple[str, ...]:
    """学校可信域名白名单:常量 + scripts/school_topics.json 里的 host。

    读不到配置文件不报错——降级为纯常量白名单,安全侧偏保守。
    """
    hosts = {h.lower() for h in DEFAULT_TRUSTED_SCHOOL_HOSTS}
    try:
        import json
        from minicode.paths import resource_path
        for base in (Path("scripts") / "school_topics.json",
                     resource_path("scripts") / "school_topics.json"):
            try:
                if base.exists():
                    raw = json.loads(base.read_text(encoding="utf-8"))
                    for key in ("host", "hosts"):
                        val = raw.get(key) if isinstance(raw, dict) else None
                        if isinstance(val, str):
                            hosts.add(val.strip().lower())
                        elif isinstance(val, list):
                            hosts.update(str(x).strip().lower() for x in val if x)
            except (OSError, ValueError):
                continue
    except Exception:
        pass
    return tuple(sorted(h for h in hosts if h))


def _clean_host(host: str) -> str:
    """容忍传进来的是完整 URL:只取其中的域名部分。"""
    h = str(host or "").strip().lower().rstrip(".")
    if "://" in h:
        h = h.split("://", 1)[1]
    h = h.split("/", 1)[0].split("?", 1)[0]
    return h.split("@")[-1].split(":")[0]


def host_is_trusted(host: str) -> bool:
    """抓取来源域名是否在学校白名单内(子域也算)。"""
    h = _clean_host(host)
    if not h:
        return False
    return any(h == t or h.endswith("." + t) for t in trusted_school_hosts())


def crawl_credibility(host: str) -> str:
    """抓取入库时的可信度:白名单校官网标 A,其余降为 B-抓取(未核实)。"""
    return "A" if host_is_trusted(host) else CRAWLED_UNVERIFIED


CRAWLED_UNVERIFIED = "B-抓取(未核实)"


@dataclass
class KbEntry:
    source_file: str          # 来源文件名,如 knowledge.json
    id: str
    title: str
    content: str
    url: str                  # 来源网址(可能为空)
    source: str               # 来源单位/渠道
    tags: list[str] = field(default_factory=list)
    credibility: str = ""     # A=可靠;D-*=演示或待核实
    publish_at: str = ""
    deadline: str = ""

    @property
    def credible(self) -> bool:
        return self.credibility.strip().upper().startswith("A")


def kb_dirs(workspace: Path | None = None) -> list[Path]:
    """知识库目录:打包内置资源 + exe 旁 + 工作区(去重;条目另有 (文件名,id) 去重)。"""
    import os
    from minicode.paths import resource_path
    dirs = [app_path("kb"), resource_path("kb")]
    if workspace:
        dirs.append(Path(workspace) / "kb")
    seen, out = set(), []
    for d in dirs:
        key = os.path.normcase(str(Path(d).resolve()))
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def _first(item: dict, *keys: str) -> str:
    for k in keys:
        v = item.get(k)
        if v:
            return str(v)
    return ""


def load_kb(dirs: list[Path]) -> list[KbEntry]:
    entries: list[KbEntry] = []
    seen: set[tuple[str, str]] = set()   # (文件名, 条目id) 去重:开发态应用目录与工作区可能同盘
    for d in dirs:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, list):
                continue
            for i, item in enumerate(data, start=1):
                if not isinstance(item, dict):
                    continue
                title = _first(item, "title")
                content = _first(item, "content", "body")
                if not title and not content:
                    continue
                key = (f.name.lower(), str(item.get("id") or i))
                if key in seen:
                    continue
                seen.add(key)
                tags = item.get("tags") if isinstance(item.get("tags"), list) else []
                tag_list = [str(t) for t in tags if t]
                if item.get("category"):
                    tag_list.insert(0, str(item["category"]))
                entries.append(KbEntry(
                    source_file=f.name,
                    id=str(item.get("id") or f"{f.stem}_{i}"),
                    title=title,
                    content=content,
                    url=_first(item, "url", "source_url"),
                    source=_first(item, "source", "source_channel"),
                    tags=tag_list,
                    credibility=str(item.get("credibility") or ""),
                    publish_at=_first(item, "publish_at", "publish_time"),
                    deadline=_first(item, "deadline"),
                ))
    return entries


def _tokens(text: str) -> set[str]:
    """中文按 2-gram 切分,英文/数字按词,小写化。"""
    text = (text or "").lower()
    tokens: set[str] = set()
    for word in re.findall(r"[a-z0-9]+", text):
        tokens.add(word)
    for seg in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(seg) == 1:
            tokens.add(seg)
        else:
            tokens.update(seg[i:i + 2] for i in range(len(seg) - 1))
    return tokens


def score_entry(entry: KbEntry, query_tokens: set[str]) -> int:
    if not query_tokens:
        return 0
    title = _tokens(entry.title)
    tags = _tokens(" ".join(entry.tags))
    content = _tokens(entry.content)
    score = 0
    score += 3 * len(query_tokens & title)
    score += 2 * len(query_tokens & tags)
    score += 1 * len(query_tokens & content)
    return score


@dataclass
class SearchHit:
    """一条命中:条目 + 分数 + 命中依据(哪个词落在哪个字段)+ 这一条是靠哪档进来的。"""
    entry: KbEntry
    score: float
    reason: str
    via: str


def _min_sim(cfg) -> float:
    caps = getattr(cfg, "capabilities", None) or {}
    spec = caps.get("embeddings") if isinstance(caps, dict) else None
    if isinstance(spec, dict) and spec.get("min_sim") is not None:
        try:
            return float(spec["min_sim"])
        except (TypeError, ValueError):
            return DEFAULT_MIN_SIM
    return DEFAULT_MIN_SIM


def search_hybrid(entries: list[KbEntry], query: str, *, top_k: int = 3,
                  category: str | None = None, source: str | None = None,
                  since: str | None = None, until: str | None = None,
                  semantic: bool | None = None, build_index: bool = False,
                  cfg=None) -> tuple[list[SearchHit], dict]:
    """关键词(BM25F)+ 可选向量档 + 元数据过滤,RRF 名次融合。

    ``semantic``:None=auto(只有向量索引**已经建好**时才用,不会顺手发几百条请求)、
    False=只用关键词、True=要用语义档(缺索引且 build_index=True 才补建)。
    ``meta`` 里的每个字段都是真跑出来的,直接进给用户看的"检索口径"一行。
    """
    from minicode import embed, retrieval

    query = (query or "").strip()
    semantic = _tri_state(semantic)
    pool, dropped = retrieval.filter_meta(
        entries, category=category, source=source, since=since, until=until,
        date_of=lambda e: e.publish_at, tags_of=lambda e: e.tags,
        source_of=lambda e: " ".join([e.source, e.url, e.source_file]))
    meta = {"pool": len(pool), "dropped": dropped, "keyword_hits": 0,
            "modes": ["关键词 BM25F"], "vector": {"ran": False, "why": "未启用",
                                                  "backend": "none", "cached": 0,
                                                  "missing": 0, "embedded": 0,
                                                  "elapsed_ms": 0},
            "fused": False, "elapsed_ms": 0}
    if not query or not pool:
        return [], meta

    started = time.perf_counter()
    index = retrieval.KeywordIndex([
        retrieval.fields_of(e.title, e.tags, e.content) for e in pool])
    kw_hits = index.search(query, top_k=max(top_k * 4, HYBRID_CANDIDATES))
    meta["keyword_hits"] = len(kw_hits)
    reasons = {h.index: retrieval.reason_line(h) for h in kw_hits}
    rankings = [[h.index for h in kw_hits]]
    weights = [1.0]
    via = {i: "关键词" for i in rankings[0]}

    vec_report = None
    if semantic is not False and pool:
        vec_report = embed.ensure_vectors(pool, cfg, build=(build_index or semantic is True))
        cached_ok = vec_report["cached"] or vec_report["embedded"]
        meta["vector"].update(backend=vec_report["backend"], cached=vec_report["cached"],
                              missing=vec_report["missing"], embedded=vec_report["embedded"])
        if not vec_report["ready"] or not cached_ok:
            meta["vector"]["why"] = vec_report["why"] or (
                f"索引未建({vec_report['missing']} 条没有向量),本次只走关键词;"
                "要建索引就明确说一声(build_index=True)")
        else:
            qv = None
            try:
                qv = embed.embed_texts([query], cfg)[0]
            except embed.EmbedUnavailable as e:
                meta["vector"]["why"] = f"查询向量取不到:{e}"
            if qv:
                ranked = retrieval.rank_by_vector(qv, vec_report["vectors"],
                                                  min_sim=_min_sim(cfg))
                if ranked:
                    meta["vector"].update(ran=True, why="",
                                          elapsed_ms=vec_report["elapsed_ms"],
                                          sims=dict(ranked[:top_k * 4]))
                    meta["modes"].append("向量(%s)" % vec_report["backend"])
                    rankings.append([i for i, _s in ranked])
                    weights.append(1.0)
                    for i, _s in ranked:
                        via[i] = "关键词+向量" if i in via else "仅向量"
                else:
                    meta["vector"]["why"] = ("向量档跑了但没有任何条目过相似度闸门"
                                             f"({_min_sim(cfg):.2f}),本次只走关键词")
    if len(rankings) > 1:
        meta["fused"] = True
        fused = retrieval.rrf(rankings, weights)
        order = retrieval.order_by_fused(fused)
    else:
        fused = {h.index: h.score for h in kw_hits}
        order = [h.index for h in kw_hits]
    limit = max(1, min(int(top_k), KB_RESULT_LIMIT))
    hits = [SearchHit(entry=pool[i], score=round(fused.get(i, 0.0), 6),
                      reason=reasons.get(i, ""), via=via.get(i, "关键词"))
            for i in order[:limit]]
    meta["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
    return hits, meta


def search(entries: list[KbEntry], query: str,
           top_k: int = 3, category: str | None = None) -> list[KbEntry]:
    """旧接口:只走关键词档(BM25F 排序),返回裸条目。

    留着是因为脚本与测试都在用它;要拿命中依据与检索口径请用 search_hybrid。
    """
    hits, _meta = search_hybrid(entries, query, top_k=top_k, category=category,
                                semantic=False)
    return [h.entry for h in hits]


def _mode_line(meta: dict | None) -> str:
    """把本次检索真跑了什么拼成一行,放在结果最前面。

    这一行的每个数字都来自 search_hybrid 的 meta(候选数、排除数、向量档状态、
    实测耗时),没有任何"看起来更聪明"的措辞:向量档没跑就写没跑,以及为什么没跑。
    """
    if not meta:
        return ""
    vec = meta.get("vector") or {}
    if vec.get("ran"):
        vpart = f"向量档已跑(后端 {vec.get('backend')},缓存 {vec.get('cached')} 条"
        if vec.get("embedded"):
            vpart += f",本次新算 {vec.get('embedded')} 条"
        vpart += f",{vec.get('elapsed_ms')}ms)"
    else:
        vpart = f"向量档未跑:{vec.get('why') or '未启用'}"
    parts = ["+".join(meta.get("modes") or ["关键词"]), vpart,
             f"候选 {meta.get('keyword_hits', 0)} 条", f"耗时 {meta.get('elapsed_ms', 0)}ms"]
    dropped = meta.get("dropped") or {}
    if dropped:
        parts.append("元数据排除 " + "、".join(f"{k} {v} 条" for k, v in dropped.items()))
    return "检索口径:" + "｜".join(parts) + "\n"


def format_results(results: list, snippet_chars: int = SNIPPET_LIMIT,
                   meta: dict | None = None) -> str:
    if not results:
        return (_mode_line(meta) + NO_HIT_TEXT) if meta else NO_HIT_TEXT
    blocks = []
    for i, item in enumerate(results, start=1):
        e = item.entry if isinstance(item, SearchHit) else item
        cred = e.credibility or "未标注"
        # 抓取降级条目不是"学长学姐经验",不能套 reply-tone 的"亲测有效"话术(那会把
        # 一个未核实的网页说成实地经过的推荐)。
        if e.credible:
            mark = ""
        elif "抓取" in cred:
            mark = "⚠ 此条是网页抓取内容且来源不在学校白名单,引用时必须说明「未核实,以链接原文为准」"
        else:
            mark = "⚠ 此条为演示/待核实数据,引用时必须向用户注明"
        lines = [f"[{i}] {e.title}  (可信度: {cred}){(' ' + mark) if mark else ''}"]
        meta_bits = []
        if e.tags:
            meta_bits.append("分类: " + "/".join(e.tags[:4]))
        if e.publish_at:
            meta_bits.append("发布: " + e.publish_at)
        if e.deadline:
            meta_bits.append("截止: " + e.deadline)
        if meta_bits:
            lines.append("    " + " · ".join(meta_bits))
        if isinstance(item, SearchHit):
            why = item.reason or ("" if item.via == "关键词" else "靠语义档进来的,关键词没对上原词")
            lines.append(f"    为什么命中: {why}({item.via})" if why else
                         f"    为什么命中: 元数据过滤后进入候选({item.via})")
        if e.content:
            snippet = e.content[:snippet_chars] + ("…" if len(e.content) > snippet_chars else "")
            lines.append(f"    摘要: {snippet}")
        if e.url:
            lines.append(f"    来源网址: {e.url}")
        elif e.source:
            lines.append(f"    来源渠道: {e.source}(无网址)")
        blocks.append("\n".join(lines))
    header = _mode_line(meta) + (f"共命中 {len(results)} 条(按相关度排序)。"
              "回答引用这些内容时,请给出条目标题与来源网址;"
              "可信度非 A 的条目按 reply-tone 技能话术表述:能指认实测者的说"
              "「学长学姐亲测有效、推荐的方法」并附条目标题与来源;"
              "指认不出的如实写「该方法出自条目《标题》,暂无更多实测记录,供参考」。\n")
    return header + "\n\n".join(blocks)


def _int_arg(value, default: int, low: int, high: int) -> int:
    """工具参数容错:模型可能传字符串或超范围数值。"""
    try:
        num = int(value)
    except (TypeError, ValueError):
        num = default
    return max(low, min(num, high))


def _tri_state(value):
    """把工具入参收成 True / False / None(自动)。

    模型经常传字符串:"false"、"否"、"" —— 这些都得算"不要",不能因为
    `if semantic:` 遇到非空字符串为真就默认去跑向量档。传得含糊时一律退回
    最保守的 None(自动:有现成索引才用)。
    """
    if value is None or isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "y", "是", "开", "true."):
        return True
    if text in ("0", "false", "no", "n", "否", "关", "off"):
        return False
    return None


def search_knowledge(ctx, query: str, top_k: int = 3,
                     category: str | None = None,
                     snippet_chars: int = SNIPPET_LIMIT,
                     source: str | None = None,
                     since: str | None = None,
                     semantic: bool | None = None,
                     build_index: bool = False) -> str:
    """工具处理器:检索本地知识库(关键词 BM25F + 元数据过滤,可选向量档融合)。

    snippet_chars 是每条返回的正文长度(默认 160 字的速览摘要)。用户问细节
    (评定条件、办理流程、时间安排、分数线等)时调大,一次就能拿到原文要点,
    不必再去爬官网等结果。

    source / since 是元数据过滤(按来源渠道或网址、按发布日期下限 YYYY-MM-DD);
    被过滤掉多少条会写在返回的「检索口径」里,不静默减少结果。
    semantic 默认 None=自动:只有向量索引**已经建好**才参与融合,缺索引不会
    顺手去发几百条 embedding 请求(那既花时间又花使用者的额度)。真要建索引
    就明确传 build_index=True,返回里会如实说明这次新算了多少条。
    """
    query = (query or "").strip()
    if not query:
        return "错误: 查询词不能为空。"
    entries = load_kb(kb_dirs(ctx.workspace))
    if not entries:
        return "错误: 知识库为空(kb/ 目录下没有可用的 JSON 数据)。"
    snippet_chars = _int_arg(snippet_chars, SNIPPET_LIMIT, SNIPPET_LIMIT, SNIPPET_MAX)
    hits, meta = search_hybrid(entries, query, top_k=top_k, category=category,
                               source=source, since=since, semantic=semantic,
                               build_index=bool(build_index),
                               cfg=getattr(ctx, "config", None))
    if not hits:
        # 未命中登记(知识库成长闭环):库里明明有数据却接不住这个问法,
        # 这就是要补的语料缺口。登记失败静默,绝不影响回答。
        from minicode import growth
        growth.log_kb_gap(query)
    out = format_results(hits, snippet_chars=snippet_chars, meta=meta)
    # 每条命中多了「为什么命中」一行,上限跟着放宽,否则口径行会把最后一条挤掉
    return out[:max(MAX_CONTENT_LIMIT, snippet_chars * len(hits) + 800 + 80 * max(1, len(hits)))]


def kb_stats(dirs: list[Path]) -> list[dict]:
    """每个知识库文件的条目数(给系统提示词与 GUI 用)。"""
    stats = []
    for d in dirs:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, list):
                stats.append({"file": f.name, "count": len(data)})
    return stats


def kb_prompt_section(dirs: list[Path]) -> str:
    stats = kb_stats(dirs)
    if not stats:
        return ""
    detail = ", ".join(f"{s['file']}({s['count']} 条)" for s in stats)
    total = sum(s["count"] for s in stats)
    return (
        f"\n# 本地知识库\n"
        f"已装载本地知识库(共 {total} 条):{detail}。\n"
        f"用户问到校园通知、奖学金/助学金、竞赛、证书、考研、就业、心理调适等话题时,"
        f"先调用 search_knowledge 工具检索,基于检索结果回答;\n"
        f"引用时必须给出条目标题与来源网址(url),让用户可以核实原文;\n"
        f"问申请条件、办理流程、时间安排等细节时,把 snippet_chars 调大(600~1200)"
        f"一次取回原文要点,不要为了细节再去爬官网让用户等;\n"
        f"官网抓取的条目带 publish_at(发布日期),引用时一并说明是哪一年的通知;\n"
        f"**旧年份的通知(放假安排/考试安排/评选办法等)绝不是今年的安排** —— 把它"
        f"当「今年的」说出去就是把人带沟里;引用前先核年份,不是今年的就明说"
        f"「这是 XX 年的旧通知」,并建议用 campus_notices 现拉官网看有没有新的;\n"
        f"问学校自己的事(最近活动、宿舍与生活服务、社团、比赛竞赛、军训、学生故事、\n"
        f"办事入口等)时,用 category 参数把检索限定到对应栏目(如 category=\"校园活动与文体\"\n"
        f"\"宿舍与生活服务\" \"学科竞赛\" \"社团与学生组织\" \"军训与国防教育\"\n"
        f"\"学生故事与风采\" \"校园新闻动态\" \"视频与图文校园\" \"服务大厅与办事入口\"),\n"
        f"命中率比直接拿口语问句搜更高;条目正文里带 `观看地址:`/`查看地址:` 一行的\n"
        f"是官网视频页或图文/二维码指引页,回答时把该链接单独一行列出,方便用户点开观看;\n"
        f"可信度不是 A 的条目按 reply-tone 技能话术表述(能指认实测者说「学长学姐亲测有效、"
        f"推荐的方法」并附来源,指认不出如实写「暂无更多实测记录,供参考」);来源为「哔哩哔哩」的视频"
        f"条目(带 观看地址: 一行)是 UP 主亲测的经验类视频推荐,回答时把视频链接单独一行列出"
        f"方便用户点开核实,并冠以「UP 主亲测有效、推荐」的说法;来源为「微信公众号」的"
        f"文章条目同理,把原文链接单独一行列出(正文已全文入库,链接供查看原文与核实)。\n"
    )
