"""会话消息管理:内存为主,JSONL 留档 + 恢复。"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from minicode.llm import AssistantTurn
from minicode.paths import SESSIONS_DIR


@dataclass
class SessionSummary:
    path: Path
    time: str
    n_messages: int
    last_user_text: str
    workspace: str = ""      # 留档里记的工作区(老留档没这个字段,给空串)


class Session:
    def __init__(self, system_prompt: str, log_dir: Path | None = None,
                 workspace: Path | None = None):
        self.messages: list[dict] = [{"role": "system", "content": system_prompt}]
        self._log_dir = Path(log_dir) if log_dir is not None else None
        self._log_path: Path | None = None   # 懒创建:发出第一条消息时才落盘
        # 会话属于哪个工作区:留档里记一份,切换会话时界面能标出"这条不是当前
        # 工作区的记录"。以前这里记的是 Path.cwd(),那是进程启动目录,不是工作区。
        self.workspace = Path(workspace) if workspace is not None else None

    @property
    def log_path(self) -> Path | None:
        return self._log_path

    def _ensure_log(self) -> None:
        if self._log_path is not None or self._log_dir is None:
            return
        self._log_dir.mkdir(parents=True, exist_ok=True)
        base = time.strftime("%Y%m%d_%H%M%S")
        self._log_path = self._log_dir / f"session_{base}.jsonl"
        suffix = 1
        while self._log_path.exists():  # 同秒内可能开多个会话
            suffix += 1
            self._log_path = self._log_dir / f"session_{base}_{suffix}.jsonl"
        self._log_only({
            "type": "session_start",
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "workspace": str(self.workspace) if self.workspace else "",
        })

    def fresh_start(self) -> None:
        """完全重置为全新会话(旧留档文件保留在磁盘上,由调用方处置)。"""
        system = [m for m in self.messages if m.get("role") == "system"][:1]
        self.messages = list(system)
        self._log_path = None   # 下一条消息会创建新的留档文件

    def add_user(self, text: str, content=None) -> None:
        """content 可选:多模态消息体(list[dict]);不传则用纯文本 text。"""
        self._append({"role": "user",
                      "content": content if content is not None else text})

    def add_assistant(self, turn: AssistantTurn) -> None:
        msg: dict = {"role": "assistant", "content": turn.text if turn.text else None}
        if turn.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                }
                for tc in turn.tool_calls
            ]
        self._append(msg)

    def add_tool_result(self, call_id: str, name: str, content: str) -> None:
        self._append({"role": "tool", "tool_call_id": call_id, "content": content})

    def clear(self) -> None:
        """清空对话上下文,保留 system prompt。"""
        self._replace_messages([], log_marker="cleared")

    def load_from(self, path: Path) -> int:
        """从 JSONL 留档恢复历史(替换当前上下文),返回恢复的消息条数。"""
        loaded: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("role") in ("user", "assistant", "tool"):
                loaded.append(obj)
        loaded, fixed = repair_tool_pairs(loaded)
        self._log_path = path  # 后续对话继续写入该文件(标记也得落进这同一份留档)
        self._replace_messages(loaded, log_marker="resumed")
        if fixed:
            self._log_only({"type": "repaired",
                            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "changed": fixed})
        return len(loaded)

    def refresh_system_prompt(self, text: str) -> bool:
        """换掉开头那条 system 提示(跨天时重建日期与课表段)。

        只动内存:留档 JSONL 记的是 user/assistant/tool 三种 role,从来不含
        system 行,所以重建它不影响 `load_from` 恢复旧会话。
        返回是否真的换了 —— 调用方据此决定要不要告诉用户一声。
        """
        if not self.messages or self.messages[0].get("role") != "system":
            return False
        self.messages[0] = {"role": "system", "content": text}
        return True

    def replace_messages(self, msgs: list[dict]) -> None:
        """整体替换上下文(压缩后调用);system 之外的记录留档。"""
        self._replace_messages(
            [m for m in msgs if m.get("role") != "system"],
            log_marker="compacted",
        )

    def drop_last_answer(self) -> int:
        """丢掉最近一次回答(最后那句 user 之后的一切),返回丢掉的条数。

        「重新生成」是**换个答法**,不是撤回提问 —— 用户那句留着。丢掉的行不从留档里
        抹掉,只补一行 `regenerated` 标记(与 cleared/compacted/resumed 同一套追加式
        口径):复盘时看得见这里重答过;代价是恢复整份留档时会把旧答案一起带回来,
        与 `compacted` 现有的行为一致。
        """
        msgs = self.messages
        last_user = max((i for i, m in enumerate(msgs) if m.get("role") == "user"),
                        default=-1)
        if last_user < 0 or last_user == len(msgs) - 1:
            return 0                    # 没提问过,或提问后一个字都还没答
        dropped = len(msgs) - last_user - 1
        self._replace_messages(msgs[:last_user + 1], log_marker="regenerated")
        return dropped

    def last_user_message(self) -> dict | None:
        """上下文里最后那句用户消息(「重新生成」要照它重跑一遍)。"""
        for m in reversed(self.messages):
            if m.get("role") == "user":
                return m
        return None

    # ------------------------------------------------------------------

    def _replace_messages(self, body: list[dict], log_marker: str) -> None:
        system = [m for m in self.messages if m.get("role") == "system"][:1]
        self.messages = system + list(body)
        self._log_only({"type": log_marker,
                        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "messages": len(self.messages)})

    def _append(self, msg: dict) -> None:
        self._ensure_log()   # 只有真实消息才创建留档文件;标记类写入不创建
        self.messages.append(msg)
        self._log_only(msg)

    def _log_only(self, obj: dict) -> None:
        if self._log_path is None:
            return
        with self._log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")


INTERRUPTED_TOOL_RESULT = ("程序中断:这一步当时没有跑完,也没有留下结果"
                           "(这条是恢复历史时补记的,不是工具的真实输出)。")


def repair_tool_pairs(msgs: list[dict]) -> tuple[list[dict], int]:
    """把"assistant 喊了工具却没留下结果"的非法序列补齐,返回 (修好的列表, 改动条数)。

    为什么要修:`add_assistant`(带 tool_calls)与逐条 `add_tool_result` 不是一个事务,
    JSONL 又是逐条追加的 —— 在这两步之间断电、被强关、被 Windows 更新杀掉,留档里就留下
    悬空的 call。恢复出来原样发给任何 OpenAI 兼容端点都会 400,而且**每一轮都 400**(坏
    对儿一直躺在上下文里),用户唯一的自救是 `/clear` 把整个会话清空。压缩(`replace_messages`
    保头+尾)同样可能把一对 call/result 从中间切断,所以这不只是崩溃才有的问题。

    两个方向都管:
    - 悬空 call → 补一条**事实性**的 tool 结果,而不是把那条 call 删掉 —— 删等于抹掉
      "这里断过"这件事,模型会以为工具真跑完了,接着往下编。
    - 孤儿 tool 结果(前面找不到对应的 call)→ 只能丢掉:协议里没有"无主的工具结果"。
      这条改动会算进返回的条数里,不静默。
    """
    pending: dict[str, None] = {}     # 有序集合:当前这批还没闭合的 tool_call id
    out: list[dict] = []
    fixed = 0

    def close_pending() -> None:
        nonlocal fixed
        for cid in pending:
            out.append({"role": "tool", "tool_call_id": cid,
                        "content": INTERRUPTED_TOOL_RESULT})
            fixed += 1
        pending.clear()

    for m in msgs:
        role = m.get("role")
        if role == "tool":
            cid = m.get("tool_call_id")
            if cid in pending:
                del pending[cid]
                out.append(m)
            else:
                fixed += 1        # 无主/重复的工具结果:协议里没有这种位置,只能丢
            continue
        if pending:
            close_pending()       # 下一句话开口前,上一批工具必须先闭合
        out.append(m)
        if role == "assistant":
            pending = dict.fromkeys(
                c.get("id") for c in (m.get("tool_calls") or [])
                if isinstance(c, dict) and c.get("id"))
    if pending:
        close_pending()
    # 一条都没动就交回**原来那个列表对象**:调用方(_messages_for_request)有一针钉
    # "关掉回灌开关时发出去的就是 session.messages 本身,连复制都不做",每一步请求
    # 都白复制一份长列表也是真开销。
    return (msgs, fixed) if not fixed else (out, fixed)


def _content_text(content) -> str:
    """把一条消息的 content 摊平成纯文本(多模态消息只取文字部分)。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text" and item.get("text"):
                parts.append(str(item["text"]))
            elif item.get("type") == "image_url":
                parts.append("[图片]")
        return "\n".join(parts)
    return ""


def to_markdown(sess: "Session", title: str = "") -> str:
    """把会话导出成 Markdown:只留用户与助手的正文。

    工具调用与工具结果**不进文件** —— 那是过程噪音,分享给同学时只会淹没重点
    (一次搜索就几屏 JSON)。要复盘过程的话,留档 JSONL 本来就在。
    """
    lines = [f"# {title or '引路学长 · 会话记录'}", ""]
    if sess.workspace:
        lines += [f"**工作区**:{sess.workspace}", ""]
    if sess.log_path:
        lines += [f"**留档**:{sess.log_path.name}", ""]
    for msg in sess.messages:
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue                      # 系统提示词与工具往返都跳过
        body = _content_text(msg.get("content")).strip()
        if not body:
            continue
        lines += [("## 我" if role == "user" else "## 引路学长"), "", body, ""]
    return "\n".join(lines).strip() + "\n"


def export_filename(sess: "Session", prefix: str = "引路学长会话") -> str:
    """导出文件名。借用留档名里的时间戳,让导出件与留档一一对得上。"""
    stamp = time.strftime("%Y%m%d-%H%M")
    if sess.log_path is not None:
        m = re.match(r"session_(\d{8})_(\d{4})", sess.log_path.stem)
        if m:
            stamp = f"{m.group(1)}-{m.group(2)}"
    return f"{prefix}-{stamp}.md"


def search_logs(log_dir: Path, query: str, limit: int = 50,
                snippet_chars: int = 80) -> list[dict]:
    """在所有会话留档里搜关键词,返回 [{"path", "hits", "snippet"}]。

    大小写不敏感的子串匹配 —— 留档以中文为主,上分词器纯属负担,而且会让
    "搜不到"变得无法解释。每条命中给一段上下文片段:界面上要能看见
    "为什么这条算命中",否则用户只能挨个点开试。

    `snippet_chars` 只改片段窗口,默认 80 = 侧栏搜索框现在的样子;模型侧要用
    这段片段直接回答"上次怎么解决的",80 字不够,所以 `search_history` 传更大的数。
    """
    query = (query or "").strip()
    if not query or not log_dir.exists():
        return []
    needle = query.lower()
    lead = min(24, snippet_chars)
    trail = max(snippet_chars - lead, 0)
    out: list[dict] = []
    files = sorted(log_dir.glob("session_*.jsonl"),
                   key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        hits, snippet = 0, ""
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict) or obj.get("role") not in ("user", "assistant"):
                continue
            content = _content_text(obj.get("content"))
            low = content.lower()
            at = low.find(needle)
            if at < 0:
                continue
            hits += low.count(needle)
            if not snippet:
                start = max(0, at - lead)
                snippet = ("…" if start else "") + \
                    content[start:at + len(query) + trail].replace("\n", " ")
        if hits:
            out.append({"path": path, "hits": hits, "snippet": snippet.strip()})
    out.sort(key=lambda h: h["hits"], reverse=True)
    return out


# ------------------------------------------------------------------ 跨会话记忆

HISTORY_SCAN_FILES = 60      # 一次翻最近多少个留档
HISTORY_TOP_LIMIT = 5        # 最多回几条
HISTORY_SNIPPET_CHARS = 420  # 每条命中的上下文窗口:要够接住"上次那个报错怎么解决的"
HISTORY_TOTAL_CHARS = 2600   # 输出总长上限:几段片段不该把整轮回话挤掉


def history_hits(query: str, top: int = 3, log_dir: Path | None = None,
                 snippet_chars: int = HISTORY_SNIPPET_CHARS,
                 scan_files: int = HISTORY_SCAN_FILES) -> list[dict]:
    """按关键词翻本机历史会话留档,返回
    [{"when", "workspace", "opening", "hits", "snippet"}](按命中次数排序)。

    留档在 `APP_DIR/sessions`,工作区围栏之外 —— `read_file`/`glob`/`grep` 都到不了,
    这是刻意的(里面全是用户自己的话)。这里是一份**受控**出口:只取 role 为
    user/assistant 的正文(工具往返不进结果),对外只给会话时间、工作区目录名与首问,
    绝对路径一个字都不下发。工具侧 `search_history` 与 CLI 的 `/find` 都渲染这份
    结果,同一份算法两处消费。
    """
    q = (query or "").strip()
    if not q:
        return []
    log_dir = Path(log_dir) if log_dir is not None else SESSIONS_DIR
    hits = search_logs(log_dir, q, limit=scan_files, snippet_chars=snippet_chars)
    labels = {s.path: s for s in list_sessions(log_dir, limit=scan_files)}
    out: list[dict] = []
    for hit in hits[:max(top, 1)]:
        s = labels.get(hit["path"])
        out.append({
            "when": (s.time[:16] if s else hit["path"].stem),
            "workspace": (Path(s.workspace).name if s and s.workspace else ""),
            "opening": (s.last_user_text if s else ""),
            "hits": hit["hits"],
            "snippet": hit["snippet"],
        })
    return out


def search_history(ctx, query: str, limit: int = 3) -> str:
    """跨会话检索历史留档(只读工具):学长记起"我们上次聊过什么"的唯一入口。"""
    q = (query or "").strip()
    if not q:
        return "要给一个关键词才能搜历史会话,如 query=\"报错\" / query=\"蓝桥杯\"。"
    try:
        top = max(1, min(int(limit or 3), HISTORY_TOP_LIMIT))
    except (TypeError, ValueError):
        top = 3
    found = history_hits(q, top=top)
    if not found:
        return (f"最近 {HISTORY_SCAN_FILES} 个会话留档里没有提到「{q}」的内容。"
                "没查到就是没查到 —— 别编「我们上次说过」,如实告诉用户并换个关键词再试。")
    lines = [f"在历史会话里找到提到「{q}」的 {len(found)} 段对话"
             f"(翻了最近 {HISTORY_SCAN_FILES} 个留档,按命中次数排序):"]
    used = 0
    for i, h in enumerate(found, start=1):
        head = h["when"]
        if h["workspace"]:
            head += f" · 工作区 {h['workspace']}"
        if h["opening"]:
            head += f" · 那轮开头问的是「{h['opening'][:40]}」"
        block = f"[{i}] {head}(命中 {h['hits']} 次)\n    {h['snippet']}"
        if used + len(block) > HISTORY_TOTAL_CHARS:
            lines.append("（剩下的片段太长没贴出来,可在侧栏搜索框打开对应会话看全文）")
            break
        lines.append(block)
        used += len(block)
    lines.append("口径:只搜你本机留档里的用户与助手正文(不含工具往返);要接着那次往下聊,"
                 "让用户在侧栏搜索框点开该会话,或 CLI 用 /resume。")
    return "\n".join(lines)


def list_sessions(log_dir: Path, limit: int = 10) -> list[SessionSummary]:
    """扫描 sessions 目录,按修改时间倒序返回最近的历史会话摘要。"""
    if not log_dir.exists():
        return []
    summaries: list[SessionSummary] = []
    files = sorted(log_dir.glob("session_*.jsonl"),
                   key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    for p in files:
        n_messages = 0
        last_user_text = ""
        workspace = ""
        started = time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime))
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") == "session_start":
                if obj.get("time"):
                    started = obj["time"]
                workspace = str(obj.get("workspace") or "")
            if obj.get("role") in ("user", "assistant", "tool"):
                n_messages += 1
            if obj.get("role") == "user" and isinstance(obj.get("content"), str):
                last_user_text = obj["content"]
        summaries.append(SessionSummary(
            path=p, time=started, n_messages=n_messages,
            last_user_text=last_user_text.replace("\n", " ")[:60],
            workspace=workspace,
        ))
    return summaries
