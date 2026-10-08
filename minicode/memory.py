"""长期记忆:跨会话记住用户偏好、反馈纠正、项目约定与参考资料。

两级作用域:
- **项目级**(默认):工作区 `memory/MEMORY.md`,只在本项目生效;
- **用户级**:`~/.yinluxs/memory/MEMORY.md`(跟凭据同目录),跨项目通用,
  适合"用户偏好""沟通习惯"这类跟项目无关的事实。

存储格式一条一行,人工可直接编辑、可进版本库::

    - [2026-09-13][user] 喜欢简短结论,先给答案再给理由

四类:用户偏好(user)/反馈纠正(feedback)/项目约定(project)/参考资料(reference)。

**Dream 整理**(对齐星瑶 Y-Code 的 /dream 思路,按本项目体量收敛):
1. 先做确定性的去重归并 —— 同类别内按中文 2-gram 相似度聚类,相似条目只留
   信息最全的一条,日期取较新;
2. 有 LLM 时再让模型做一次归纳:合并同义、删掉被后续推翻的、每条压成一句话;
   模型输出一律当"建议"处理,解析失败/返回为空就退回第 1 步的结果;
3. 覆盖前先把旧文件备份到同目录 `history/`,整理永远不丢原始记录。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from minicode import config as config_mod
from minicode.kb import _tokens  # 复用中文 2-gram 分词

MEMORY_REL = Path("memory") / "MEMORY.md"
GLOBAL_MEMORY_REL = Path("memory") / "MEMORY.md"
CATEGORIES = {"user": "用户偏好", "feedback": "反馈纠正",
              "project": "项目约定", "reference": "参考资料"}
CATEGORY_ALIASES = {**CATEGORIES, **{v: k for k, v in CATEGORIES.items()},
                    "偏好": "user", "反馈": "feedback", "约定": "project",
                    "参考": "reference"}
MEMORY_CHAR_LIMIT = 4000   # 注入系统提示词的字数预算(两级合计,按条让位见 _fit_levels)
ENTRY_LIMIT = 200          # 单级记忆条数上限,超出淘汰最旧
CONTENT_LIMIT = 500        # 单条内容长度上限
DREAM_CONTENT_LIMIT = 120  # Dream 整理后单条的目标长度上限
MERGE_SIMILARITY = 0.75    # 同类别两条的 2-gram Jaccard 达到该值即视为同一条
CONTAINMENT_SIMILARITY = 0.85  # 或较短一条被覆盖比例达到该值(补充说明式重复)
MIN_TOKENS_FOR_CONTAINMENT = 3  # 覆盖判据的 token 下限(中英混排短句 token 很少)
SCOPES = ("project", "user")
DREAM_HISTORY_DIR = "history"
LINE_RE = re.compile(r"^-\s*\[(\d{4}-\d{2}-\d{2})\]\[(.+?)\]\s*(.+)$")


@dataclass
class MemoryEntry:
    date: str
    category: str   # 存储 key:user / feedback / project / reference
    content: str

    def line(self) -> str:
        return f"- [{self.date}][{self.category}] {self.content}"


def memory_path(workspace: Path) -> Path:
    """项目级记忆文件。"""
    return Path(workspace) / MEMORY_REL


def global_memory_path() -> Path:
    """用户级(跨项目)记忆文件:跟凭据同目录,便于随用户漫游。

    这里刻意走 `config_mod.CREDENTIALS_PATH` 而不是 import 时绑定的值,
    这样测试里 monkeypatch 它就能把文件指到临时目录。
    """
    return Path(config_mod.CREDENTIALS_PATH).parent / GLOBAL_MEMORY_REL


def path_for(workspace: Path | None, scope: str = "project") -> Path:
    scope = norm_scope(scope) or "project"
    if scope == "user":
        return global_memory_path()
    return memory_path(workspace if workspace is not None else Path.cwd())


def norm_scope(raw: str | None) -> str | None:
    raw = (raw or "").strip().lower()
    if raw in ("", "project", "workspace", "项目", "本项目"):
        return "project"
    if raw in ("user", "global", "用户", "全局", "跨项目"):
        return "user"
    return None


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _norm_category(raw: str) -> str | None:
    raw = (raw or "").strip().lower()
    if raw in CATEGORIES:
        return raw                       # 先按存储 key 匹配
    return CATEGORY_ALIASES.get(raw)     # 再按中文名/别名匹配


# --------------------------------------------------------------------- 读写

def load_from(path: Path) -> list[MemoryEntry]:
    """读取指定记忆文件(不存在返回空表)。"""
    p = Path(path)
    if not p.exists():
        return []
    entries = []
    for line in p.read_text(encoding="utf-8").splitlines():
        m = LINE_RE.match(line.strip())
        if m:
            entries.append(MemoryEntry(date=m.group(1), category=m.group(2),
                                       content=m.group(3)))
    return entries


def save_to(path: Path, entries: list[MemoryEntry]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# 长期记忆",
             "<!-- 一条一行: - [日期][类别] 内容;类别: 用户偏好/反馈纠正/项目约定/参考资料 -->",
             ""]
    lines += [e.line() for e in entries[-ENTRY_LIMIT:]]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_entries(workspace: Path) -> list[MemoryEntry]:
    """项目级记忆(保持旧签名,大量既有调用点依赖它)。"""
    return load_from(memory_path(workspace))


def _write_entries(workspace: Path, entries: list[MemoryEntry]) -> None:
    save_to(memory_path(workspace), entries)


def _backup(path: Path) -> Path | None:
    """覆盖前把旧文件备份到同目录 history/ 下(整理永不丢原始记录)。"""
    p = Path(path)
    if not p.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = p.parent / DREAM_HISTORY_DIR / f"{p.stem}-{stamp}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    return target


# --------------------------------------------------------------------- 增删查

def save_entry(workspace: Path, category: str, content: str,
               scope: str = "project") -> str:
    cat = _norm_category(category)
    if cat is None:
        return (f"错误: 未知记忆类别 '{category}'。"
                f"可用: user(用户偏好)/feedback(反馈纠正)/"
                f"project(项目约定)/reference(参考资料)。")
    sc = norm_scope(scope)
    if sc is None:
        return f"错误: 未知作用域 '{scope}'。可用: project(本项目)/user(跨项目)。"
    content = (content or "").strip()[:CONTENT_LIMIT]
    if not content:
        return "错误: 记忆内容不能为空。"

    path = path_for(workspace, sc)
    entries = load_from(path)
    if any(e.category == cat and e.content == content for e in entries):
        return "这条记忆已经存在,无需重复保存。"
    for e in entries:                     # 相似度高的当作同一条,更新而不是堆积
        if e.category == cat and is_same_memory(e.content, content):
            e.content = content if len(content) >= len(e.content) else e.content
            e.date = _today()
            save_to(path, entries)
            return f"已更新已有记忆({CATEGORIES[cat]}): {e.content}"

    entries.append(MemoryEntry(date=_today(), category=cat, content=content))
    save_to(path, entries)
    label = CATEGORIES[cat]
    tip = ""
    if len(entries) >= ENTRY_LIMIT * 0.6:
        tip = "(记忆偏多,可用 action=dream 整理去重)"
    mark = "[用户级]" if sc == "user" else ""
    return f"已记住{mark}({label}): {content}{tip}"


def list_entries(workspace: Path, category: str = "", scope: str = "project") -> str:
    sc = norm_scope(scope)
    if sc is None:
        return f"错误: 未知作用域 '{scope}'。可用: project / user。"
    entries = load_from(path_for(workspace, sc))
    cat = _norm_category(category) if category.strip() else None
    if category.strip() and cat is None:
        return f"错误: 未知记忆类别 '{category}'。"
    if cat:
        entries = [e for e in entries if e.category == cat]
    mark = "[用户级]" if sc == "user" else ""
    if not entries:
        return (f"{mark}还没有任何记忆。用户提到偏好、约定或纠正时,"
                f"用 action=save 记下来。")
    lines = [f"{mark}共 {len(entries)} 条:"]
    for e in entries:
        lines.append(f"- [{e.date}][{CATEGORIES.get(e.category, e.category)}] {e.content}")
    return "\n".join(lines)


def search_entries(workspace: Path, query: str, scope: str = "project") -> str:
    query = (query or "").strip()
    if not query:
        return "错误: 查询词不能为空。"
    sc = norm_scope(scope)
    if sc is None:
        return f"错误: 未知作用域 '{scope}'。可用: project / user。"
    entries = load_from(path_for(workspace, sc))
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
    head = "[用户级]" if sc == "user" else ""
    lines = [f"{head}命中 {len(scored)} 条:"]
    for s, e in scored[:5]:
        lines.append(f"- [{e.date}][{CATEGORIES.get(e.category, e.category)}] {e.content}")
    return "\n".join(lines)


def forget_entries(workspace: Path, query: str, scope: str = "project") -> str:
    query = (query or "").strip()
    if not query:
        return "错误: 请说明要忘记的内容(支持部分匹配)。"
    sc = norm_scope(scope)
    if sc is None:
        return f"错误: 未知作用域 '{scope}'。可用: project / user。"
    path = path_for(workspace, sc)
    entries = load_from(path)
    q = query.lower()
    kept = [e for e in entries if q not in e.content.lower()]
    removed = len(entries) - len(kept)
    if not removed:
        return f"记忆中没有匹配「{query}」的条目。"
    save_to(path, kept)
    mark = "(用户级)" if sc == "user" else ""
    return f"已忘记{mark} {removed} 条与「{query}」相关的记忆。"


# --------------------------------------------------------------------- 整理

def _norm_for_compare(text: str) -> str:
    """比较用的归一化:去掉空白/标点,英文小写(中文原样)。"""
    return re.sub(r"[\s,.。，、;；:：!！?？\"'“”‘’()()\[\]【】<>《》/\-—_*#]+", "",
                  (text or "").lower())


def similarity(a: str, b: str) -> float:
    """中文 2-gram Jaccard 相似度(0~1)。"""
    ta, tb = _tokens(_norm_for_compare(a)), _tokens(_norm_for_compare(b))
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


def containment(a: str, b: str) -> float:
    """较短一条有多大比例被另一条覆盖(处理"新记忆是旧记忆的补充说明")。"""
    ta, tb = _tokens(_norm_for_compare(a)), _tokens(_norm_for_compare(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def is_same_memory(a: str, b: str, threshold: float = MERGE_SIMILARITY) -> bool:
    """判定两条记忆是否在说同一件事:整体相似,或一条基本覆盖另一条。

    中英混排的短句 token 少(见 kb._tokens:中文取 2-gram、英文取词),
    "部署走 Docker" 只有 3 个 token,所以覆盖判据的下限设到 3 —— 配合
    0.85 的覆盖率要求,不会把"部署走 Docker"和"文档放 docs"这类误判为同一条。
    """
    if similarity(a, b) >= threshold:
        return True
    shorter = min(len(_tokens(_norm_for_compare(a))), len(_tokens(_norm_for_compare(b))))
    return shorter >= MIN_TOKENS_FOR_CONTAINMENT and containment(a, b) >= CONTAINMENT_SIMILARITY


def dedupe_entries(entries: list[MemoryEntry],
                   threshold: float = MERGE_SIMILARITY) -> tuple[list[MemoryEntry], list[str]]:
    """确定性的去重归并:同类别内相似条目合并,完全重复直接丢。

    保留下来的:信息最全(最长)的正文 + 较新的日期。
    返回 (新条目表, 变更说明)。
    """
    kept: list[MemoryEntry] = []
    notes: list[str] = []
    seen_exact: set[tuple[str, str]] = set()

    for e in entries:
        key = (e.category, _norm_for_compare(e.content))
        if key in seen_exact:
            notes.append(f"删除重复条目: {e.content[:40]}")
            continue
        seen_exact.add(key)

        merged = False
        for k in kept:
            if k.category != e.category:
                continue
            if not is_same_memory(k.content, e.content, threshold):
                continue
            better, worse = (e, k) if len(e.content) > len(k.content) else (k, e)
            k.content = better.content[:CONTENT_LIMIT]
            k.date = max(k.date, e.date)
            notes.append(f"合并同类({len(worse.content)}字并入{len(better.content)}字): "
                         f"丢掉「{worse.content[:24]}」")
            merged = True
            break
        if not merged:
            kept.append(MemoryEntry(e.date, e.category, e.content[:CONTENT_LIMIT]))
    return kept, notes


DREAM_SYSTEM = (
    "你在整理一份 AI 助手的长期记忆文件。只做整理,不要新增事实、不要臆测。"
)

DREAM_PROMPT = """下面是现有的长期记忆条目(JSON 数组,已按作用域分组)。

请整理:
1. 合并重复或高度相似的条目(保留信息更全的那种说法);
2. 删掉已过期、或已被后续条目推翻的内容(同一件事以更新的为准);
3. 每条压成一句话,不超过 {limit} 字,保留关键细节(路径、命令、偏好取值);
4. 类别只能是 user / feedback / project / reference,不得新增;
5. 日期沿用原条目日期,合并时取较新的那个。

只输出 JSON,不要解释、不要代码块:
{{"entries": [{{"date": "YYYY-MM-DD", "category": "project", "content": "..."}}]}}

现有条目:
{payload}"""


def _parse_dream_output(raw: str) -> list[MemoryEntry]:
    """从模型输出里抠出 entries;格式不对就抛 ValueError 交给上层兜底。"""
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型输出里没有 JSON 对象")
    data = json.loads(text[start:end + 1])
    items = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("JSON 里缺少 entries 数组")
    out: list[MemoryEntry] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        cat = _norm_category(str(item.get("category") or ""))
        content = re.sub(r"\s+", " ", str(item.get("content") or "")).strip()
        if cat is None or not content:
            continue
        date = str(item.get("date") or "").strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            date = _today()
        out.append(MemoryEntry(date=date, category=cat,
                               content=content[:CONTENT_LIMIT]))
    return out


def dream(workspace: Path | None, scope: str = "project",
          llm=None) -> str:
    """整理指定作用域的记忆:先确定性去重,再(可选)让模型归纳,最后写回。

    任何异常都不改变原文件(除了确定性去重这一步的结果);覆盖前自动备份。
    """
    sc = norm_scope(scope)
    if sc is None:
        return f"错误: 未知作用域 '{scope}'。可用: project / user。"
    path = path_for(workspace, sc)
    before = load_from(path)
    where = "用户级(跨项目)" if sc == "user" else "本项目"
    if not before:
        return f"[{where}]还没有记忆,无需整理。"

    merged, notes = dedupe_entries(before)
    source = "确定性去重"

    if llm is not None and hasattr(llm, "complete"):
        payload = json.dumps(
            [{"date": e.date, "category": e.category, "content": e.content} for e in merged],
            ensure_ascii=False, indent=1)
        try:
            raw = llm.complete(
                DREAM_SYSTEM,
                DREAM_PROMPT.format(limit=DREAM_CONTENT_LIMIT, payload=payload))
            polished = _parse_dream_output(raw)
            if not polished:
                notes.append("模型返回空结果,已忽略(原文保留)")
        except Exception as e:
            notes.append(f"模型整理未生效({type(e).__name__}),仅做了去重")
            polished = []
        if polished:
            merged, extra = dedupe_entries(polished)
            notes += [f"模型整理: {n}" for n in extra]
            source = "模型归纳 + 去重"

    if not merged:                      # 宁可保留原样,也不能把记忆清空
        return f"[{where}]整理中止:结果为空,已保留原有 {len(before)} 条。"

    backup = _backup(path)
    save_to(path, merged)
    lines = [f"[{where}]整理完成({source}): {len(before)} 条 → {len(merged)} 条"]
    if backup is not None:
        lines.append(f"原文件已备份: {backup.name}")
    if notes:
        lines.append("变更明细:")
        lines += [f"  · {n}" for n in notes[:12]]
        if len(notes) > 12:
            lines.append(f"  · …共 {len(notes)} 处")
    return "\n".join(lines)


def dream_all(workspace: Path | None, llm=None) -> str:
    """整理两级记忆(项目 + 用户级),分别汇报。"""
    return "\n\n".join([dream(workspace, "project", llm),
                        dream(workspace, "user", llm)])


# --------------------------------------------------------------------- 提示词

INJECT_HEADER = "# 长期记忆(跨会话,自动加载)"
_SAVE_HINT = ("当用户提到个人偏好、项目约定,或纠正了你的做法时,用 action=save 记住它"
              "(跟人有关的偏好用 scope=user,只跟本项目有关的用 scope=project);"
              "记忆条目多且重复时,用 action=dream 整理去重。")
# 让位预算先扣掉固定开销:大标题 + 那句 action=save + 两个小标题和"另有 M 条"那行。
# 这是"每轮少付一点"的自律,不是协议硬线 —— 估多一点只少注入一条。
_INJECT_OVERHEAD = len(INJECT_HEADER) + len(_SAVE_HINT) + 160


def inject_line(entry: MemoryEntry) -> str:
    """一条记忆在系统提示词里的那一行(类别显示成中文,与落盘格式 `line()` 不同)。"""
    return (f"- [{entry.date}]"
            f"[{CATEGORIES.get(entry.category, entry.category)}] {entry.content}")


def _section(title: str, entries: list[MemoryEntry]) -> list[str]:
    if not entries:
        return []
    return [f"## {title}"] + [inject_line(e) for e in entries]


def _fit_levels(user: list[MemoryEntry], project: list[MemoryEntry]
                ) -> tuple[list[MemoryEntry], list[MemoryEntry], int]:
    """两级共享一份预算,装不下时各自从**最旧**一条开始让位(轮流丢)。

    为什么不是切字符串:以前是 `text[:MEMORY_CHAR_LIMIT]` 裸切,坏在两面 ——
    ① 半条记忆以完整句子的口气进系统提示词,模型会把说了一半的话当成说全了;
    ② 用户级在前、项目级在后,于是一超就整块丢项目级 —— 那通常是记得最不久的那批。
    让位方向与写侧 `ENTRY_LIMIT` 淘汰最旧是同一条口径。两级各留到最后一条就不再动:
    宁可略微超预算,也不让一本记忆整本消失。
    """
    budget = MEMORY_CHAR_LIMIT - _INJECT_OVERHEAD
    u, p = list(user), list(project)

    def cost() -> int:
        return sum(len(inject_line(e)) + 1 for e in u + p)

    dropped = 0
    while cost() > budget and (len(u) > 1 or len(p) > 1):
        if len(u) > 1:
            u.pop(0)
            dropped += 1
        if cost() <= budget:
            break
        if len(p) > 1:
            p.pop(0)
            dropped += 1
    return u, p, dropped


def prompt_section(workspace: Path | None, global_path: Path | None = None) -> str:
    """注入系统提示词:用户级记忆在前(跨项目),项目级在后;装不下按条让位。"""
    project = load_entries(workspace) if workspace is not None else []
    gpath = Path(global_path) if global_path is not None else global_memory_path()
    user = load_from(gpath)
    if not project and not user:
        return ""
    kept_u, kept_p, dropped = _fit_levels(user, project)
    lines = ["", INJECT_HEADER]
    lines += _section("用户级(跨项目通用)", kept_u)
    lines += _section("本项目", kept_p)
    if dropped:
        lines.append(f"(本次注入最近 {len(kept_u) + len(kept_p)} 条,"
                     f"另有 {dropped} 条没列出 —— 翻旧账用 action=list,"
                     "按词找用 action=search;条目多且重复时 action=dream 去重)")
    lines.append(_SAVE_HINT)
    return "\n".join(lines) + "\n"


# 兼容别名(app_setup 的接线使用此名称)
memory_prompt_section = prompt_section


def memory(ctx, action: str, category: str = "", content: str = "",
           query: str = "", scope: str = "project") -> str:
    """memory 工具处理器:save / list / search / forget / dream。"""
    action = (action or "").strip().lower()
    workspace = ctx.workspace
    llm = getattr(ctx, "llm", None)
    if action == "save":
        return save_entry(workspace, category or "project", content, scope)
    if action == "list":
        return list_entries(workspace, category, scope)
    if action == "search":
        return search_entries(workspace, query, scope)
    if action == "forget":
        return forget_entries(workspace, query, scope)
    if action == "dream":
        if (scope or "").strip().lower() in ("all", "both", "全部", "所有", "两级"):
            return dream_all(workspace, llm)
        return dream(workspace, scope, llm)
    return (f"错误: 未知 action '{action}'。"
            f"可用: save / list / search / forget / dream。")
