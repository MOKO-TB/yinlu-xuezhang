"""长期记忆:跨会话记住用户偏好、反馈纠正、项目约定与参考资料。

存储在工作区 memory/MEMORY.md,一条一行(日期+类别+内容),人工可直接编辑、
可进版本库;系统提示词自动注入全部记忆(截断),模型通过 memory 工具
save/list/search/forget 增删查。四类:用户偏好(user)/反馈纠正(feedback)/
项目约定(project)/参考资料(reference)。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from minicode.kb import _tokens  # 复用中文 2-gram 分词

MEMORY_REL = Path("memory") / "MEMORY.md"
CATEGORIES = {"user": "用户偏好", "feedback": "反馈纠正",
              "project": "项目约定", "reference": "参考资料"}
CATEGORY_ALIASES = {**CATEGORIES, **{v: k for k, v in CATEGORIES.items()},
                    "偏好": "user", "反馈": "feedback", "约定": "project",
                    "参考": "reference"}
MEMORY_CHAR_LIMIT = 4000   # 注入系统提示词的截断长度
ENTRY_LIMIT = 200          # 记忆条数上限,超出淘汰最旧
CONTENT_LIMIT = 500        # 单条内容长度上限
LINE_RE = re.compile(r"^-\s*\[(\d{4}-\d{2}-\d{2})\]\[(.+?)\]\s*(.+)$")


@dataclass
class MemoryEntry:
    date: str
    category: str   # 存储 key:user / feedback / project / reference
    content: str


def memory_path(workspace: Path) -> Path:
    return Path(workspace) / MEMORY_REL


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _norm_category(raw: str) -> str | None:
    raw = (raw or "").strip().lower()
    if raw in CATEGORIES:
        return raw                       # 先按存储 key 匹配
    return CATEGORY_ALIASES.get(raw)     # 再按中文名/别名匹配


def load_entries(workspace: Path) -> list[MemoryEntry]:
    p = memory_path(workspace)
    if not p.exists():
        return []
    entries = []
    for line in p.read_text(encoding="utf-8").splitlines():
        m = LINE_RE.match(line.strip())
        if m:
            entries.append(MemoryEntry(date=m.group(1), category=m.group(2),
                                       content=m.group(3)))
    return entries


def save_entry(workspace: Path, category: str, content: str) -> str:
    cat = _norm_category(category)
    if cat is None:
        return (f"错误: 未知记忆类别 '{category}'。"
                f"可用: user(用户偏好)/feedback(反馈纠正)/"
                f"project(项目约定)/reference(参考资料)。")
    content = (content or "").strip()[:CONTENT_LIMIT]
    if not content:
        return "错误: 记忆内容不能为空。"

    entries = load_entries(workspace)
    if any(e.category == cat and e.content == content for e in entries):
        return "这条记忆已经存在,无需重复保存。"

    entries.append(MemoryEntry(date=_today(), category=cat, content=content))
    _write_entries(workspace, entries)
    label = CATEGORIES[cat]
    return f"已记住({label}): {content}"


def list_entries(workspace: Path, category: str = "") -> str:
    entries = load_entries(workspace)
    cat = _norm_category(category) if category.strip() else None
    if category.strip() and cat is None:
        return f"错误: 未知记忆类别 '{category}'。"
    if cat:
        entries = [e for e in entries if e.category == cat]
    if not entries:
        return "还没有任何记忆。用户提到偏好、约定或纠正时,用 action=save 记下来。"
    lines = [f"共 {len(entries)} 条:"]
    for e in entries:
        lines.append(f"- [{e.date}][{CATEGORIES.get(e.category, e.category)}] {e.content}")
    return "\n".join(lines)


def search_entries(workspace: Path, query: str) -> str:
    query = (query or "").strip()
    if not query:
        return "错误: 查询词不能为空。"
    entries = load_entries(workspace)
    if not entries:
        return "还没有任何记忆。"
    q = _tokens(query)
    scored = []
    for e in entries:
        s = len(q & _tokens(f"{e.content} {e.category}"))
        if s:
            scored.append((s, e))
    scored.sort(key=lambda pair: -pair[0])
    if not scored:
        return f"记忆中没有与「{query}」相关的内容。"
    lines = [f"命中 {len(scored)} 条:"]
    for s, e in scored[:5]:
        lines.append(f"- [{e.date}][{CATEGORIES.get(e.category, e.category)}] {e.content}")
    return "\n".join(lines)


def forget_entries(workspace: Path, query: str) -> str:
    query = (query or "").strip()
    if not query:
        return "错误: 请说明要忘记的内容(支持部分匹配)。"
    entries = load_entries(workspace)
    q = query.lower()
    kept = [e for e in entries if q not in e.content.lower()]
    removed = len(entries) - len(kept)
    if not removed:
        return f"记忆中没有匹配「{query}」的条目。"
    _write_entries(workspace, kept)
    return f"已忘记 {removed} 条与「{query}」相关的记忆。"


def _write_entries(workspace: Path, entries: list[MemoryEntry]) -> None:
    p = memory_path(workspace)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# 长期记忆",
             "<!-- 一条一行: - [日期][类别] 内容;类别: 用户偏好/反馈纠正/项目约定/参考资料 -->",
             ""]
    for e in entries[-ENTRY_LIMIT:]:
        lines.append(f"- [{e.date}][{e.category}] {e.content}")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def prompt_section(workspace: Path) -> str:
    """注入系统提示词的记忆片段(超出上限时保留较新记忆)。"""
    entries = load_entries(workspace)
    if not entries:
        return ""
    lines = ["", "# 长期记忆(跨会话,自动加载)"]
    body = [f"- [{e.date}][{CATEGORIES.get(e.category, e.category)}] {e.content}"
            for e in entries]
    text = "\n".join(body)
    if len(text) > MEMORY_CHAR_LIMIT:
        text = text[-MEMORY_CHAR_LIMIT:]
        lines.append("(仅保留较新的记忆)")
    lines += text.splitlines()
    lines.append("当用户提到个人偏好、项目约定,或纠正了你的做法时,用 action=save 记住它。")
    return "\n".join(lines) + "\n"


# 兼容别名(app_setup 的接线使用此名称)
memory_prompt_section = prompt_section


def memory(ctx, action: str, category: str = "", content: str = "",
           query: str = "") -> str:
    """memory 工具处理器:save / list / search / forget。"""
    action = (action or "").strip().lower()
    workspace = ctx.workspace
    if action == "save":
        return save_entry(workspace, category or "project", content)
    if action == "list":
        return list_entries(workspace, category)
    if action == "search":
        return search_entries(workspace, query)
    if action == "forget":
        return forget_entries(workspace, query)
    return f"错误: 未知 action '{action}'。可用: save / list / search / forget。"
