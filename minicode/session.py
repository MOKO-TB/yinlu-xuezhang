"""会话消息管理:内存为主,JSONL 留档 + 恢复。"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from minicode.llm import AssistantTurn


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
        self._replace_messages(loaded, log_marker="resumed")
        self._log_path = path  # 后续对话继续写入该文件
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


def search_logs(log_dir: Path, query: str, limit: int = 50) -> list[dict]:
    """在所有会话留档里搜关键词,返回 [{"path", "hits", "snippet"}]。

    大小写不敏感的子串匹配 —— 留档以中文为主,上分词器纯属负担,而且会让
    "搜不到"变得无法解释。每条命中给一段上下文片段:界面上要能看见
    "为什么这条算命中",否则用户只能挨个点开试。
    """
    query = (query or "").strip()
    if not query or not log_dir.exists():
        return []
    needle = query.lower()
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
                start = max(0, at - 24)
                snippet = ("…" if start else "") + \
                    content[start:at + len(query) + 56].replace("\n", " ")
        if hits:
            out.append({"path": path, "hits": hits, "snippet": snippet.strip()})
    out.sort(key=lambda h: h["hits"], reverse=True)
    return out


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
