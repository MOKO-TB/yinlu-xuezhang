"""本地知识库(kb/*.json):通知与经验资料的关键词检索,回答时输出来源网址。

数据是异构的(通知类用 body/url,经验类用 content/source_url/tags),
加载时统一映射为 KbEntry。检索用中文 2-gram 打分:
标题命中×3 + 标签/分类×2 + 正文×1(与引路学长产品 PLAN §11 的权重一致)。
条目的 credibility(A=可靠来源 / D=演示或待核实)必须随结果输出,
模型回答时对 D 级内容要注明"未经核实"。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from minicode.paths import app_path

SNIPPET_LIMIT = 160
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


def format_results(results: list[KbEntry]) -> str:
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
            snippet = e.content[:SNIPPET_LIMIT] + ("…" if len(e.content) > SNIPPET_LIMIT else "")
            lines.append(f"    摘要: {snippet}")
        if e.url:
            lines.append(f"    来源网址: {e.url}")
        elif e.source:
            lines.append(f"    来源渠道: {e.source}(无网址)")
        blocks.append("\n".join(lines))
    header = (f"共命中 {len(results)} 条(按相关度排序)。"
              "回答引用这些内容时,请给出条目标题与来源网址;"
              "可信度非 A 的条目必须注明未经核实。\n")
    return header + "\n\n".join(blocks)


def search_knowledge(ctx, query: str, top_k: int = 3,
                     category: str | None = None) -> str:
    """工具处理器:检索本地知识库。"""
    query = (query or "").strip()
    if not query:
        return "错误: 查询词不能为空。"
    entries = load_kb(kb_dirs(ctx.workspace))
    if not entries:
        return "错误: 知识库为空(kb/ 目录下没有可用的 JSON 数据)。"
    results = search(entries, query, top_k=top_k, category=category)
    out = format_results(results)
    return out[:MAX_CONTENT_LIMIT]


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
        f"可信度不是 A 的条目(演示/待核实数据)必须明确告知用户该条未经核实。\n"
    )
