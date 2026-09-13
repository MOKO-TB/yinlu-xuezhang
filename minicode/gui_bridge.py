"""GUI 桥接层:pywebview js_api ↔ mini-code 核心引擎。

前端通过 js_api 调用这里的公开方法;引擎事件经 GuiUI.push 反向推给前端。
确认弹窗用 threading.Event 跨线程握手:Agent 线程在 confirm_tool 里等待,
用户点击后前端调 confirm_reply 唤醒。
"""
from __future__ import annotations

import difflib
import json
import threading
from pathlib import Path

from minicode import APP_NAME, __version__
from minicode.app_setup import create_agent
from minicode.attachments import (
    Attachment, build_content, display_text, is_image, mime_for,
    preview_data_url, save_attachments,
)
from minicode.compaction import estimate_tokens
from minicode.config import CONFIG_PATH, API_KEY_PLACEHOLDERS, save_config
from minicode.gui_ui import GuiUI
from minicode.kb import kb_dirs, kb_stats as kb_stats_fn
from minicode.memory import load_entries as mem_load, memory_path as mem_path
from minicode.llm import LLMClient
from minicode.paths import SESSIONS_DIR
from minicode.sandbox import classify_path, policy_to_raw
from minicode.session import Session, list_sessions
from minicode.skills import load_skills, skill_dirs
from minicode.suggestions import build_suggestions, collect_user_texts
from minicode.tools.base import Tool

PREVIEW_LINES = 15
DIFF_MAX_LINES = 40


class PublicApi:
    """js_api 门面:只暴露方法,不暴露 Bridge 的成员对象图。

    直接把 Bridge 交给 pywebview 时,页面/自动化客户端(UIA 属性枚举)可以
    沿 `agent.ui.window.native...` 链式解析到 WinForms 窗体对象,造成
    递归爆栈并堵塞桥线程。门面上只有纯数据方法,链走不到任何内部对象。
    """

    def __init__(self, bridge: "Bridge"):
        self._b = bridge

    def get_info(self) -> dict:
        return self._b.get_info()

    def send_task(self, text: str, attachments: str = "[]") -> dict:
        return self._b.send_task(text, attachments)

    def pick_attachments(self) -> dict:
        return self._b.pick_attachments()

    def set_sandbox(self, enabled: bool) -> dict:
        return self._b.set_sandbox(enabled)

    def edit_resend(self, ordinal: int, new_text: str, expect: str = "") -> dict:
        return self._b.edit_resend(ordinal, new_text, expect)

    def get_suggestions(self) -> dict:
        return self._b.get_suggestions()

    def stop_task(self) -> dict:
        return self._b.stop_task()

    def confirm_reply(self, choice: str) -> dict:
        return self._b.confirm_reply(choice)

    def undo(self) -> dict:
        return self._b.undo()

    def new_task(self) -> dict:
        return self._b.new_task()

    def compact_now(self) -> dict:
        return self._b.compact_now()

    def set_permission_mode(self, mode: str) -> dict:
        return self._b.set_permission_mode(mode)

    def refresh_sessions(self) -> dict:
        return self._b.refresh_sessions()

    def resume_session(self, index: int) -> dict:
        return self._b.resume_session(index)

    def delete_session(self, index: int) -> dict:
        return self._b.delete_session(index)

    def open_url(self, url: str) -> dict:
        return self._b.open_url(url)

    def get_providers(self) -> dict:
        return self._b.get_providers()

    def save_providers(self, providers_json: str, active_id: str) -> dict:
        return self._b.save_providers(providers_json, active_id)

    def set_provider(self, provider_id: str) -> dict:
        return self._b.set_provider(provider_id)


class Bridge:
    """暴露给前端 JS 的 API(window.pywebview.api.<method>)。"""

    def __init__(self, config, workspace: Path, ui: GuiUI, agent, session: Session,
                 sessions_dir: Path = SESSIONS_DIR, config_path: Path = CONFIG_PATH):
        self.config = config
        self.workspace = workspace
        self.ui = ui
        self.agent = agent
        self.session = session
        self.sessions_dir = sessions_dir
        self.config_path = config_path
        self._busy = False
        self._confirm_event = threading.Event()
        self._confirm_choice = "no"
        ui.confirm_handler = self.confirm_tool

    def public_api(self) -> "PublicApi":
        """传给 pywebview js_api 的门面对象。"""
        return PublicApi(self)

    # ------------------------------------------------------------ JS API

    def get_info(self) -> dict:
        skills = load_skills(skill_dirs(self.workspace))
        kb_stats = kb_stats_fn(kb_dirs(self.workspace))
        return {
            "app_name": APP_NAME,
            "version": __version__,
            "model": self.config.model,
            "base_url": self.config.base_url,
            "workspace": str(self.workspace),
            "permission_mode": self.config.permission_mode,
            "api_key_configured": self.config.api_key_configured,
            "messages": len(self.session.messages),
            "tokens": estimate_tokens(self.session.messages),
            "providers": [
                {"id": p.id, "name": p.name, "model": p.model,
                 "has_key": p.api_key.strip() not in API_KEY_PLACEHOLDERS}
                for p in self.config.providers
            ],
            "active_provider": self.config.provider.id if self.config.provider else "",
            "skills": [{"name": s.name, "description": s.description} for s in skills],
            "kb_count": sum(s["count"] for s in kb_stats),
            "memory_count": len(mem_load(mem_path(self.workspace))),
            "mcp_servers": self._mcp_summary(),
            "sandbox": self._sandbox_info(),
            "vision": bool(self.config.vision),
        }

    def _mcp_summary(self) -> list[dict]:
        mcp = getattr(self.agent, "mcp", None)
        return mcp.tools_summary() if mcp is not None else []

    def get_providers(self) -> dict:
        """API Key 管理弹窗用:返回完整配置(本机单用户应用,key 不脱敏)。"""
        return {
            "providers": [p.to_dict() for p in self.config.providers],
            "active_provider": self.config.provider.id if self.config.provider else "",
        }

    def save_providers(self, providers_json: str, active_id: str) -> dict:
        """整体保存供应商列表(前端编辑后提交),并切换到 active_id。"""
        try:
            items = json.loads(providers_json)
        except json.JSONDecodeError as e:
            return {"ok": False, "msg": f"数据不是有效 JSON: {e}"}
        if not isinstance(items, list) or not items:
            return {"ok": False, "msg": "至少需要一个供应商"}

        from minicode.config import DEFAULT_MODEL, Provider, _slug
        providers, seen = [], set()
        for i, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                return {"ok": False, "msg": f"第 {i} 项格式错误"}
            base = str(item.get("base_url") or "").strip()
            if not base:
                return {"ok": False, "msg": f"第 {i} 个供应商缺少 base_url"}
            pid = _slug(str(item.get("id") or ""), f"provider_{i}")
            if pid in seen:
                return {"ok": False, "msg": f"供应商 id 重复: {pid}"}
            seen.add(pid)
            providers.append(Provider(
                id=pid, name=str(item.get("name") or pid),
                base_url=base.rstrip("/"),
                api_key=str(item.get("api_key") or ""),
                model=str(item.get("model") or DEFAULT_MODEL),
            ))
        active = active_id if any(p.id == active_id for p in providers) else providers[0].id

        self.config.providers = providers
        self.config.active_provider_id = active
        save_config(self.config_path, self.config)
        self._apply_active_provider()
        self.ui.push("info", text=f"已保存 {len(providers)} 个供应商,当前: {self.config.provider.name}")
        return {"ok": True}

    def set_provider(self, provider_id: str) -> dict:
        if not self.config.set_active_provider(provider_id):
            return {"ok": False, "msg": f"供应商不存在: {provider_id}"}
        save_config(self.config_path, self.config)
        self._apply_active_provider()
        self.ui.push("info", text=f"已切换到 {self.config.provider.name} · {self.config.model}")
        return {"ok": True}

    def _apply_active_provider(self) -> None:
        """供应商/密钥变更后重建 LLM 客户端并提示当前模型。"""
        self.agent.llm = LLMClient(self.config)
        self.ui.push("info", text=f"当前模型: {self.config.model} @ {self.config.base_url}")

    def send_task(self, text: str, attachments: str = "[]") -> dict:
        text = (text or "").strip()
        atts = self._parse_attachments(attachments)
        if not text and not atts:
            return {"ok": False, "msg": "任务为空"}
        if text.startswith("/"):
            return {"ok": False, "msg": "GUI 里不需要斜杠命令,直接描述任务"}
        if self._busy:
            return {"ok": False, "msg": "有任务正在进行中"}
        content = build_content(text, atts, bool(self.config.vision)) if atts else None
        threading.Thread(target=self._run_task, args=(text, content), daemon=True).start()
        return {"ok": True}

    # ------------------------------------------------------------ 附件

    def pick_attachments(self) -> dict:
        """弹系统文件选择框,把选中的文件复制进工作区 uploads/。"""
        window = getattr(self.ui, "window", None)
        if window is None:
            return {"ok": False, "msg": "窗口未就绪,无法打开文件选择框"}
        try:
            import webview
            picked = window.create_file_dialog(
                webview.OPEN_DIALOG, allow_multiple=True,
                file_types=("所有文件 (*.*)",
                            "图片 (*.png;*.jpg;*.jpeg;*.gif;*.webp;*.bmp)"))
        except Exception as e:
            return {"ok": False, "msg": f"打开文件选择框失败: {type(e).__name__}: {e}"}
        if not picked:
            return {"ok": True, "items": []}
        atts, errors = save_attachments(self.workspace, [str(p) for p in picked])
        return {"ok": True, "items": [self._attachment_item(a) for a in atts],
                "errors": errors}

    def _attachment_item(self, att: Attachment) -> dict:
        item = att.to_dict()
        if att.kind == "image":
            item["preview"] = preview_data_url(self.workspace / att.path)
        return item

    def _parse_attachments(self, raw) -> list[Attachment]:
        """解析前端传来的附件清单;只接受工作区内真实存在的文件(防越界读取)。"""
        try:
            items = json.loads(raw) if isinstance(raw, str) else (raw or [])
        except json.JSONDecodeError:
            return []
        if not isinstance(items, list):
            return []
        out: list[Attachment] = []
        for item in items:
            rel = item.get("path") if isinstance(item, dict) else item
            rel = str(rel or "").strip()
            if not rel:
                continue
            kind, p = classify_path(self.workspace, rel)
            if kind != "inside" or not p.is_file():
                continue
            out.append(Attachment(
                name=p.name,
                path=p.relative_to(self.workspace).as_posix(),
                size=p.stat().st_size,
                kind="image" if is_image(p) else "file",
                mime=mime_for(p),
                local=str(p),
            ))
        return out

    # ------------------------------------------------------------ 沙箱

    def set_sandbox(self, enabled: bool) -> dict:
        """开关沙箱(运行期立即生效,并写回 config.json)。"""
        state = getattr(self.agent.ctx, "sandbox", None)
        if state is None:
            return {"ok": False, "msg": "沙箱未启用"}
        state.policy.enabled = bool(enabled)
        self.config.sandbox = policy_to_raw(state.policy)
        save_config(self.config_path, self.config)
        self.ui.push("info", text=f"沙箱已{'开启' if enabled else '关闭'} · {state.describe()}")
        return {"ok": True, "sandbox": self._sandbox_info()}

    def _sandbox_info(self) -> dict:
        state = getattr(self.agent.ctx, "sandbox", None)
        if state is None:
            return {"available": False, "enabled": False, "description": "未启用"}
        return {
            "available": True,
            "enabled": state.policy.enabled,
            "allow_outside_workspace": state.policy.allow_outside_workspace,
            "allow_network": state.policy.allow_network,
            "timeout_seconds": state.policy.timeout_seconds,
            "description": state.describe(),
        }

    def edit_resend(self, ordinal: int, new_text: str, expect: str = "") -> dict:
        """编辑重发:截断到第 ordinal 条用户消息(含),校验原文一致后以新文本重发。

        expect 传前端记录的原消息文本,防止上下文压缩/变更后序号错位截断到别的消息。
        """
        if self._busy:
            return {"ok": False, "msg": "有任务正在进行中"}
        new_text = (new_text or "").strip()
        if not new_text or new_text.startswith("/"):
            return {"ok": False, "msg": "任务为空或非法"}
        idx, content = self._find_user_message(int(ordinal))
        if idx < 0:
            return {"ok": False, "msg": "该消息已不在当前上下文(可能已被压缩),无法编辑重发"}
        if expect and content != expect:
            return {"ok": False, "msg": "消息内容已变化,请刷新后重试"}
        self.session.replace_messages(self.session.messages[:idx])
        return self.send_task(new_text)

    def _find_user_message(self, ordinal: int) -> tuple[int, str]:
        """返回第 ordinal(1 起)条用户消息的 (原始下标, 内容);找不到返回 (-1, "")。"""
        if ordinal < 1:
            return -1, ""
        count = 0
        for i, m in enumerate(self.session.messages):
            if m.get("role") == "user":
                count += 1
                if count == ordinal:
                    return i, display_text(m.get("content"))
        return -1, ""

    def get_suggestions(self) -> dict:
        """首页建议 chips:根据近期聊天记录动态生成。"""
        texts = collect_user_texts(self.sessions_dir, self.session.messages)
        return {"suggestions": build_suggestions(texts)}

    def stop_task(self) -> dict:
        self.ui.request_stop()
        return {"ok": True}

    def confirm_reply(self, choice: str) -> dict:
        if choice in ("yes", "no", "always"):
            self._confirm_choice = choice
            self._confirm_event.set()
        return {"ok": True}

    def undo(self) -> dict:
        msg = self.agent.undo()
        self.ui.push("info", text=msg)
        return {"ok": True, "msg": msg}

    def new_task(self) -> dict:
        self.session.clear()
        self.ui.push("cleared")
        self._push_sessions()
        return {"ok": True}

    def compact_now(self) -> dict:
        self.agent.compact_now()
        return {"ok": True}

    def set_permission_mode(self, mode: str) -> dict:
        if mode in ("confirm", "accept_all"):
            self.config.permission_mode = mode
            self.ui.push("info", text=f"权限模式已切换为 {mode}")
            return {"ok": True}
        return {"ok": False, "msg": "未知权限模式"}

    def refresh_sessions(self) -> dict:
        self._push_sessions()
        return {"ok": True}

    def delete_session(self, index: int) -> dict:
        """删除一条历史会话留档;删的是当前会话时,重置为全新会话。"""
        if self._busy:
            return {"ok": False, "msg": "有任务正在进行中"}
        summaries = list_sessions(self.sessions_dir)
        if not 1 <= int(index) <= len(summaries):
            return {"ok": False, "msg": "会话不存在"}
        chosen = summaries[int(index) - 1]
        is_current = chosen.path == self.session.log_path
        try:
            chosen.path.unlink()
        except OSError as e:
            return {"ok": False, "msg": f"删除失败: {e}"}
        if is_current:
            self.session.fresh_start()
            self.ui.push("cleared")
        self._push_sessions()
        self.ui.push("info", text="已删除该会话记录")
        return {"ok": True}

    def open_url(self, url: str) -> dict:
        """用系统默认浏览器打开链接(聊天里的网址不在应用内导航)。"""
        url = (url or "").strip()
        if not url.startswith(("http://", "https://")):
            return {"ok": False, "msg": "仅支持 http/https 链接"}
        import webbrowser
        webbrowser.open(url)
        return {"ok": True}

    def resume_session(self, index: int) -> dict:
        if self._busy:
            return {"ok": False, "msg": "有任务正在进行中"}
        summaries = list_sessions(self.sessions_dir)
        if not 1 <= int(index) <= len(summaries):
            return {"ok": False, "msg": "会话不存在"}
        chosen = summaries[int(index) - 1]
        n = self.session.load_from(chosen.path)
        self.ui.push("cleared")
        self.ui.push("resumed", messages=self._history_for_frontend())
        self.ui.push("info", text=f"已恢复 {n} 条消息(来自 {chosen.path.name})")
        self._push_sessions()
        return {"ok": True}

    # ------------------------------------------------------------ 内部

    def _run_task(self, text: str, content=None) -> None:
        self._busy = True
        self.ui.push("busy", value=True)
        try:
            self.agent.run_turn(text, content)
        except Exception as e:  # 引擎之外的意外错误也要让前端知道
            self.ui.push("error", text=f"内部错误: {type(e).__name__}: {e}")
        finally:
            self._busy = False
            self.ui.push("busy", value=False)
            self._push_sessions()

    def _push_sessions(self) -> None:
        items = [
            {"index": i, "time": s.time, "n_messages": s.n_messages,
             "last_user_text": s.last_user_text,
             "current": s.path == self.session.log_path}
            for i, s in enumerate(list_sessions(self.sessions_dir), start=1)
        ]
        self.ui.push("sessions", items=items[:12])

    def _history_for_frontend(self) -> list[dict]:
        out = []
        for m in self.session.messages:
            role = m.get("role")
            if role == "system":
                continue
            content = m.get("content")
            if role == "tool":
                out.append({"role": "tool", "text": str(content or "")[:200]})
            elif role == "assistant" and m.get("tool_calls"):
                names = ", ".join(t["function"]["name"] for t in m["tool_calls"])
                out.append({"role": "assistant", "text": str(content or ""),
                            "tools": names})
            else:
                out.append({"role": role, "text": display_text(content)})
        return out

    # 确认握手:Agent 线程阻塞在此,前端点按钮后返回
    def confirm_tool(self, tool: Tool, args: dict) -> str:
        self._confirm_event.clear()
        self._confirm_choice = "no"
        self.ui.push("confirm_request", tool=tool.name,
                     detail=self._permission_detail_text(tool, args))
        self._confirm_event.wait()
        return self._confirm_choice

    def _permission_detail_text(self, tool: Tool, args: dict) -> str:
        if tool.name == "bash":
            return f"$ {args.get('command', '')}"

        if tool.name == "write_file":
            path = str(args.get("path", "?"))
            content = str(args.get("content", ""))
            target = self.workspace / path
            lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
            action = "覆盖已有文件" if target.exists() else "新建文件"
            head = f"{action}: {path}({lines} 行)\n\n"
            body = "\n".join(content.splitlines()[:PREVIEW_LINES])
            if content.count("\n") >= PREVIEW_LINES:
                body += "\n…"
            return head + body

        if tool.name == "edit_file":
            path = str(args.get("path", "?"))
            old = str(args.get("old_string", ""))
            new = str(args.get("new_string", ""))
            p = self.workspace / path
            current = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
            after = current.replace(old, new, 1)
            diff = list(difflib.unified_diff(
                current.splitlines(), after.splitlines(), lineterm="", n=2))[2:]
            text = f"{path}\n\n" + "\n".join(diff[:DIFF_MAX_LINES])
            if len(diff) > DIFF_MAX_LINES:
                text += f"\n…(diff 共 {len(diff)} 行)"
            return text

        return json.dumps(args, ensure_ascii=False)[:500]


def create_app(config, workspace: Path, llm=None, ui: GuiUI | None = None,
               sessions_dir: Path | None = None, config_path: Path | None = None):
    """组装 GUI 形态的完整应用;llm/sessions_dir/config_path 仅供测试注入。"""
    ui = ui or GuiUI()
    agent, session = create_agent(config, workspace, ui, llm=llm,
                                  sessions_dir=sessions_dir)
    bridge = Bridge(config, workspace, ui, agent, session,
                    sessions_dir=sessions_dir or SESSIONS_DIR,
                    config_path=config_path or CONFIG_PATH)
    return bridge, ui, agent, session
