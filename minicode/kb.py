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
from dataclasses import dataclass, field
from pathlib import Path

from minicode.paths import app_path

SNIPPET_LIMIT = 160
SNIPPET_MAX = 1200
MAX_CONTENT_LIMIT = 1500
KB_RESULT_LIMIT = 5


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


def search(entries: list[KbEntry], query: str,
           top_k: int = 3, category: str | None = None) -> list[KbEntry]:
    query_tokens = _tokens(query)
    pool = [e for e in entries
            if not category or category.lower() in " ".join(e.tags).lower()]
    scored = [(score_entry(e, query_tokens), e) for e in pool]
    scored = [(s, e) for s, e in scored if s > 0]
    scored.sort(key=lambda pair: (-pair[0], not pair[1].credible))
    return [e for _, e in scored[:max(1, min(int(top_k), KB_RESULT_LIMIT))]]


def format_results(results: list[KbEntry],
                   snippet_chars: int = SNIPPET_LIMIT) -> str:
    if not results:
        return "知识库中没有匹配的内容。"
    blocks = []
    for i, e in enumerate(results, start=1):
        cred = e.credibility or "未标注"
        mark = "" if e.credible else "⚠ 此条为演示/待核实数据,引用时必须向用户注明"
        lines = [f"[{i}] {e.title}  (可信度: {cred}){(' ' + mark) if mark else ''}"]
        meta = []
        if e.tags:
            meta.append("分类: " + "/".join(e.tags[:4]))
        if e.publish_at:
            meta.append("发布: " + e.publish_at)
        if e.deadline:
            meta.append("截止: " + e.deadline)
        if meta:
            lines.append("    " + " · ".join(meta))
        if e.content:
            snippet = e.content[:snippet_chars] + ("…" if len(e.content) > snippet_chars else "")
            lines.append(f"    摘要: {snippet}")
        if e.url:
            lines.append(f"    来源网址: {e.url}")
        elif e.source:
            lines.append(f"    来源渠道: {e.source}(无网址)")
        blocks.append("\n".join(lines))
    header = (f"共命中 {len(results)} 条(按相关度排序)。"
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


def search_knowledge(ctx, query: str, top_k: int = 3,
                     category: str | None = None,
                     snippet_chars: int = SNIPPET_LIMIT) -> str:
    """工具处理器:检索本地知识库。

    snippet_chars 是每条返回的正文长度(默认 160 字的速览摘要)。用户问细节
    (评定条件、办理流程、时间安排、分数线等)时调大,一次就能拿到原文要点,
    不必再去爬官网等结果。
    """
    query = (query or "").strip()
    if not query:
        return "错误: 查询词不能为空。"
    entries = load_kb(kb_dirs(ctx.workspace))
    if not entries:
        return "错误: 知识库为空(kb/ 目录下没有可用的 JSON 数据)。"
    snippet_chars = _int_arg(snippet_chars, SNIPPET_LIMIT, SNIPPET_LIMIT, SNIPPET_MAX)
    results = search(entries, query, top_k=top_k, category=category)
    if not results:
        # 未命中登记(知识库成长闭环):库里明明有数据却接不住这个问法,
        # 这就是要补的语料缺口。登记失败静默,绝不影响回答。
        from minicode import growth
        growth.log_kb_gap(query)
    out = format_results(results, snippet_chars=snippet_chars)
    return out[:max(MAX_CONTENT_LIMIT, snippet_chars * len(results) + 800)]


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
