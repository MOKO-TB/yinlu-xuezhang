"""GUI 桥接层:pywebview js_api ↔ mini-code 核心引擎。

前端通过 js_api 调用这里的公开方法;引擎事件经 GuiUI.push 反向推给前端。
确认弹窗用 threading.Event 跨线程握手:Agent 线程在 confirm_tool 里等待,
用户点击后前端调 confirm_reply 唤醒。
"""
from __future__ import annotations

import difflib
import json
import threading
import time
from dataclasses import replace
from pathlib import Path

from minicode import APP_NAME, __version__
from minicode.app_setup import build_runner_agent, mcp_log
from minicode.attachments import (
    Attachment, build_content, display_text, is_image, mime_for,
    preview_data_url, save_attachments,
)
from minicode.campus import frontend_payload as campus_map_data
from minicode.compaction import estimate_tokens
from minicode.config import CONFIG_PATH, API_KEY_PLACEHOLDERS, save_config
from minicode.gui_ui import GuiUI
from minicode.kb import kb_dirs, kb_stats as kb_stats_fn
from minicode.mcp import connect_servers, parse_servers
from minicode.memory import (
    dream, dream_all, global_memory_path, load_from as mem_load_file,
    memory_path as mem_path,
)
from minicode.llm import LLMClient
from minicode.library import catalog_health, health_notice
from minicode.model_catalog import session_metrics
from minicode.paths import SESSIONS_DIR, resource_path
from minicode.sandbox import SandboxState, classify_path, policy_from_raw, policy_to_raw
from minicode.session import Session, list_sessions
from minicode.skills import load_skills, skill_dirs
from minicode.suggestions import build_suggestions, collect_user_texts
from minicode.tools.base import Tool

PREVIEW_LINES = 15
DIFF_MAX_LINES = 40
PREVIEW_CHARS = 1200      # 浏览器 JS 之类"长参数"在确认框里的截断长度


class PublicApi:
    """js_api 门面:只暴露方法,不暴露 Bridge 的成员对象图。

    直接把 Bridge 交给 pywebview 时,页面/自动化客户端(UIA 属性枚举)可以
    沿 `agent.ui.window.native...` 链式解析到 WinForms 窗体对象,造成
    递归爆栈并堵塞桥线程。门面上只有纯数据方法,链走不到任何内部对象。
    """

    def __init__(self, bridge: "Bridge"):
        self._b = bridge
        self.workspace = bridge.workspace

    def get_info(self) -> dict:
        return self._b.get_info()

    def send_task(self, text: str, attachments: str = "[]") -> dict:
        return self._b.send_task(text, attachments)

    def pick_attachments(self) -> dict:
        return self._b.pick_attachments()

    def set_sandbox(self, enabled: bool) -> dict:
        return self._b.set_sandbox(enabled)

    def consolidate_memory(self, scope: str = "all") -> dict:
        return self._b.consolidate_memory(scope)

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

    def get_asset(self, rel_path: str) -> dict:
        return self._b.get_asset(rel_path)

    def open_url(self, url: str) -> dict:
        return self._b.open_url(url)

    def get_providers(self) -> dict:
        return self._b.get_providers()

    def save_providers(self, providers_json: str, active_id: str) -> dict:
        return self._b.save_providers(providers_json, active_id)

    def set_provider(self, provider_id: str) -> dict:
        return self._b.set_provider(provider_id)

    def set_session_model(self, provider_id: str) -> dict:
        """任务面板:把当前会话的模型切到指定供应商(不影响全局默认)。"""
        return self._b.set_session_model(provider_id)

    def get_session_metrics(self) -> dict:
        """任务面板:当前会话模型/能力/消耗/上下文占用/可切列表。"""
        return self._b.get_session_metrics()

    def get_token_usage(self, days: int = 7) -> dict:
        """每日 token 用量统计(弹窗圆环图数据源)。"""
        return self._b.get_token_usage(days)


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except Exception:
        return False


class SessionGuiUI(GuiUI):
    """把某个会话的事件打上 session 标记,再转发给真正的窗口 UI。

    每个会话一个实例:停止标志、确认通道都属于它自己,
    这样一个会话在后台跑时,不会和当前会话的状态串味。
    """

    def __init__(self, base: GuiUI, session_key: str):
        super().__init__()
        self._base = base
        self.session_key = session_key

    def attach(self, window) -> None:      # 适配器不直接持有窗口
        pass

    def push(self, type_: str, **payload) -> None:
        payload["session"] = self.session_key
        self._base.push(type_, **payload)


class SessionRunner:
    """一个会话的运行时:独立 Agent + Session + 事件适配器。"""

    def __init__(self, key: str, agent, session, ui: SessionGuiUI):
        self.key = key
        self.agent = agent
        self.session = session
        self.ui = ui
        self.busy = False
        self.thread: threading.Thread | None = None
        # 本会话单独切过的模型:None = 跟随全局;非 None = {"provider_id", "model"}
        # 切换只影响这个会话的 Agent(LLMClient 逐会话重建),不写回全局配置。
        self.llm_override: dict | None = None

    @property
    def log_path(self):
        return self.session.log_path


class Bridge:
    """暴露给前端 JS 的 API(window.pywebview.api.<method>)。

支持多个会话同时运行:每个会话一个 SessionRunner,事件带 session 键,
新建任务不再打断正在跑的任务(它转后台继续)。"""

    def __init__(self, config, workspace: Path, ui: GuiUI,
                 sessions_dir: Path = SESSIONS_DIR, config_path: Path = CONFIG_PATH,
                 llm=None, mcp_tools=None, sandbox=None):
        self.config = config
        self.workspace = workspace
        self.ui = ui
        self.sessions_dir = sessions_dir
        self.config_path = config_path
        self._shared_llm = llm
        self._mcp_tools = list(mcp_tools or [])
        self._sandbox = sandbox
        self._mcp = None                     # create_app 注入,退出时统一关停
        self._runners: dict[str, SessionRunner] = {}
        self._seq = 0
        self._current: SessionRunner | None = None
        self._confirm_event = threading.Event()
        self._confirm_choice = "no"
        # 确认握手是一次一条的(前端只有一个弹窗):并行子代理同时要确认时
        # 靠这把锁排队,否则两个线程会互抢 event/choice,确认框对不上是谁在等。
        self._confirm_lock = threading.Lock()
        ui.confirm_handler = self.confirm_tool
        self.open_session()                  # 第一个会话

    # ------------------------------------------------------------ 会话运行器

    def open_session(self, load_path: Path | None = None) -> SessionRunner:
        """新建一个会话运行器(可顺带从留档恢复),并切为当前会话。

        LLM/MCP/沙箱都是共享的,只有 Agent/Session/UI 适配器是新的 ——
        所以多开一个会话很便宜,也不会重复拉起 MCP 子进程。
        """
        self._seq += 1
        ui = SessionGuiUI(self.ui, f"s{self._seq}")
        ui.confirm_handler = self.confirm_tool
        agent, session = build_runner_agent(
            self.config, self.workspace, ui, self._shared_llm,
            sessions_dir=self.sessions_dir, mcp_tools=self._mcp_tools,
            sandbox=self._sandbox)
        agent.mcp = self._mcp
        if load_path is not None:
            session.load_from(Path(load_path))
        runner = SessionRunner(ui.session_key, agent, session, ui)
        self._runners[runner.key] = runner
        self._current = runner
        return runner

    def _runner_for_log(self, path: Path | None) -> SessionRunner | None:
        if path is None:
            return None
        for r in self._runners.values():
            if r.log_path == Path(path):
                return r
        return None

    def switch_to(self, runner: SessionRunner) -> None:
        self._current = runner

    @property
    def agent(self):
        return self._current.agent

    @property
    def session(self):
        return self._current.session

    @property
    def _busy(self) -> bool:
        return bool(self._current and self._current.busy)

    def running_sessions(self) -> list[SessionRunner]:
        return [r for r in self._runners.values() if r.busy]

    def close(self) -> None:
        """关停所有会话的 MCP 子进程(退出时调用)。"""
        for r in list(self._runners.values()):
            try:
                r.ui.request_stop()
            except Exception:
                pass
        if self._mcp is not None:
            try:
                self._mcp.close_all()
            except Exception:
                pass
            self._mcp = None

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
            "memory_count": len(mem_load_file(mem_path(self.workspace))),
            "memory_user_count": len(mem_load_file(global_memory_path())),
            "mcp_servers": self._mcp_summary(),
            "sandbox": self._sandbox_info(),
            "vision": bool(self.config.vision),
            "campus_map": campus_map_data(),
            # 馆藏库自检:共享给同学后「图书馆搜书用不了」的头号原因就是库没装全
            # (1.3 GB 的大文件)。启动就把结论一并下发给界面,不用等搜书失败。
            "library_health": self._library_health(),
            # 任务面板:当前会话模型/能力/消耗/上下文占用/可切列表(见 model_catalog)
            "session_metrics": session_metrics(
                self.agent, self.session, self.config,
                self._current.llm_override if self._current else None),
        }

    def _library_health(self) -> dict:
        try:
            health = catalog_health(self.workspace)
            notice = health_notice(health)
        except Exception as exc:      # 自检本身绝不能把窗口拖垮
            return {"status": "unknown", "notice": "",
                    "detail": f"{type(exc).__name__}: {exc}"}
        return {
            "status": health.get("status"),
            "total": health.get("total"),
            "notice": notice or "",
        }

    def _mcp_summary(self) -> list[dict]:
        mcp = getattr(self.agent, "mcp", None)
        return mcp.tools_summary() if mcp is not None else []

    def get_providers(self) -> dict:
        """「添加模型」弹窗用。内置校园供应商的 key 永不回传(用户不可见,
        也不可编辑);其他供应商本机单用户,key 原样返回便于修改。"""
        return {
            "providers": [
                p.to_dict() if p.id != "gzasc"
                else {**p.to_dict(), "api_key": "", "builtin": True}
                for p in self.config.providers
            ],
            "active_provider": self.config.provider.id if self.config.provider else "",
        }

    def get_token_usage(self, days: int = 7) -> dict:
        """每日/按模型聚合的 token 用量统计(前端弹窗圆环图)。"""
        from minicode import usage
        return usage.statistics(int(days))

    def save_providers(self, providers_json: str, active_id: str) -> dict:
        """整体保存供应商列表(前端编辑后提交),并切换到 active_id。"""
        try:
            items = json.loads(providers_json)
        except json.JSONDecodeError as e:
            return {"ok": False, "msg": f"数据不是有效 JSON: {e}"}
        if not isinstance(items, list):
            return {"ok": False, "msg": "数据不是有效列表"}

        from minicode.config import (BUILTIN_PROVIDER, BUILTIN_PROVIDER_ID,
                                     DEFAULT_MODEL, Provider, _builtin_api_key,
                                     _slug)
        # 内置校园供应商不随前端提交走:v0.9.03 的提交里混进了它,导致任何保存
        # 都被「内置不可编辑」挡死(自定义模型存不进去)。这里一律跳过,由下方
        # 统一按代码补回;提交列表为空 = 只用内置,也允许。
        providers, seen = [], set()
        for i, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                return {"ok": False, "msg": f"第 {i} 项格式错误"}
            pid = _slug(str(item.get("id") or ""), f"provider_{i}")
            if pid == BUILTIN_PROVIDER_ID:
                continue      # 内置供应商始终以代码为准,由下方补回
            base = str(item.get("base_url") or "").strip()
            if not base:
                return {"ok": False, "msg": f"第 {i} 个供应商缺少 base_url"}
            if pid in seen:
                return {"ok": False, "msg": f"供应商 id 重复: {pid}"}
            seen.add(pid)
            providers.append(Provider(
                id=pid, name=str(item.get("name") or pid),
                base_url=base.rstrip("/"),
                api_key=str(item.get("api_key") or ""),
                model=str(item.get("model") or DEFAULT_MODEL),
            ))
        ids = {p.id for p in providers}
        if active_id == BUILTIN_PROVIDER_ID or not providers:
            active = BUILTIN_PROVIDER_ID      # 内置/空列表:激活内置,绝不悄悄换
        elif active_id in ids:
            active = active_id
        else:
            active = providers[0].id

        # 内置供应商始终在第一位(界面与配置的展示顺序一致)
        builtin = replace(BUILTIN_PROVIDER)
        builtin.api_key = _builtin_api_key()
        self.config.providers = [builtin] + providers
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

    def set_session_model(self, provider_id: str) -> dict:
        """把当前会话的模型切到指定供应商(不写回全局配置)。

        给这个会话的 Agent 重建一个 LLMClient;后续回合立即用新模型。
        """
        runner = self._current
        if runner is None:
            return {"ok": False, "msg": "没有活动会话"}
        if runner.busy:
            return {"ok": False, "msg": "任务进行中,等这轮跑完再切模型"}
        target = next((p for p in self.config.providers if p.id == provider_id), None)
        if target is None:
            return {"ok": False, "msg": f"供应商不存在: {provider_id}"}
        if not target.api_key.strip() or target.api_key.strip() in API_KEY_PLACEHOLDERS:
            return {"ok": False, "msg": f"「{target.name}」还没配 API Key,先在模型管理里配好"}
        runner.llm_override = {"provider_id": target.id, "model": target.model}
        runner.agent.llm = LLMClient(replace(self.config, active_provider_id=target.id))
        self.ui.push("info", text=f"本会话已切换到 {target.name} · {target.model}(不影响其他会话与默认模型)")
        self.ui.push("session_metrics", **session_metrics(
            runner.agent, runner.session, self.config, runner.llm_override))
        return {"ok": True}

    def get_session_metrics(self) -> dict:
        """任务面板一屏数据:模型/能力/消耗/上下文占用/可切列表。"""
        runner = self._current
        if runner is None:
            return {"ok": False, "msg": "没有活动会话"}
        m = session_metrics(runner.agent, runner.session, self.config,
                            runner.llm_override)
        m["ok"] = True
        return m

    def _apply_active_provider(self) -> None:
        """供应商/密钥变更后重建共享 LLM 客户端并同步给当前会话。"""
        self._shared_llm = LLMClient(self.config)
        self.agent.llm = self._shared_llm
        self.ui.push("info", text=f"当前模型: {self.config.model} @ {self.config.base_url}")

    def send_task(self, text: str, attachments: str = "[]") -> dict:
        text = (text or "").strip()
        atts = self._parse_attachments(attachments)
        if not text and not atts:
            return {"ok": False, "msg": "任务为空"}
        if text.startswith("/"):
            return {"ok": False, "msg": "GUI 里不需要斜杠命令,直接描述任务"}
        runner = self._current
        if runner.busy:
            return {"ok": False, "msg": "当前会话有任务正在进行中"}
        content = build_content(text, atts, bool(self.config.vision)) if atts else None
        runner.thread = threading.Thread(target=self._run_task,
                                         args=(runner, text, content), daemon=True)
        runner.thread.start()
        return {"ok": True, "session": runner.key}

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

    # ------------------------------------------------------------ 长期记忆

    def consolidate_memory(self, scope: str = "all") -> dict:
        """整理长期记忆(去重/归并);scope: project / user / all。"""
        llm = getattr(self.agent.ctx, "llm", None)
        sc = (scope or "all").strip().lower()
        if sc in ("all", "both", "全部", "两级"):
            msg = dream_all(self.workspace, llm)
        else:
            msg = dream(self.workspace, sc, llm)
        self.ui.push("info", text=msg)
        return {"ok": True, "msg": msg, **self._memory_counts()}

    def _memory_counts(self) -> dict:
        """两级记忆条数。

        注意用 load_from(文件路径):load_entries 收的是「工作区」,
        再传文件路径进去会二次拼接 memory/MEMORY.md,永远读到空表。
        """
        return {
            "project": len(mem_load_file(mem_path(self.workspace))),
            "user": len(mem_load_file(global_memory_path())),
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
        """只停当前会话的任务;别的会话在后台继续跑。"""
        self._current.ui.request_stop()
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
        """新建任务:直接开一个新会话,原会话(如果正在跑)转后台继续。

        每个会话有自己的 Agent/Session,所以不需要停下谁 —— 这正是
        "后台也能进行任务"的实现方式。
        """
        runner = self.open_session()
        self.ui.push("cleared", session=runner.key)
        self._push_sessions()
        if self.running_sessions():
            others = len([r for r in self.running_sessions() if r.key != runner.key])
            if others:
                self.ui.push("info", session=runner.key,
                             text=f"{others} 个任务在后台继续运行(点左侧会话可切回去看)")
        return {"ok": True, "session": runner.key}

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
        """删除一条历史会话留档;若该会话正在后台跑,先停掉它。"""
        summaries = list_sessions(self.sessions_dir)
        if not 1 <= int(index) <= len(summaries):
            return {"ok": False, "msg": "会话不存在"}
        chosen = summaries[int(index) - 1]
        live = self._runner_for_log(chosen.path)
        if live is not None:
            live.ui.request_stop()
            deadline = time.monotonic() + 8.0
            while time.monotonic() < deadline and live.busy:
                time.sleep(0.05)
            self._runners.pop(live.key, None)
        try:
            chosen.path.unlink()
        except OSError as e:
            return {"ok": False, "msg": f"删除失败: {e}"}
        if live is not None and (self._current is live or self._current is None):
            fresh = self.open_session()          # 删的是当前会话:换一个空的
            self.ui.push("cleared", session=fresh.key)
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

    def get_asset(self, rel_path: str) -> dict:
        """把工作区 skills/ 下的图片等静态资产转成 data URL,供聊天里的图片显示
        (技能正文引用的是相对技能目录的路径,前端直接 src 解析不到)。"""
        import base64 as b64mod
        import mimetypes
        rel = (rel_path or "").replace("\\", "/").strip().lstrip("/")
        if not rel or ".." in rel.split("/"):
            return {"ok": False, "msg": "非法路径"}
        # 搜索范围:工作区根、工作区各技能目录、打包内置资源(_MEIPASS/skills)
        rskills = resource_path("skills")
        candidates = [self.workspace / rel, rskills / rel]
        skills_root = self.workspace / "skills"
        if skills_root.is_dir():
            for skill_dir in sorted(skills_root.iterdir()):
                if skill_dir.is_dir():
                    candidates.append(skill_dir / rel)
        allowed_roots = [self.workspace.resolve(), Path(rskills).resolve()]
        for cand in candidates:
            try:
                resolved = cand.resolve()
            except Exception:
                continue
            if not any(_within(resolved, root) for root in allowed_roots):
                continue
            if resolved.is_file() and resolved.stat().st_size <= 8 * 1024 * 1024:
                mime = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
                data_url = ("data:" + mime + ";base64,"
                            + b64mod.b64encode(resolved.read_bytes()).decode())
                return {"ok": True, "data_url": data_url}
        return {"ok": False, "msg": "未找到该图片文件"}

    def resume_session(self, index: int) -> dict:
        """切换到某个会话:它正在后台跑就直接接上,否则从留档恢复。

        以前这里会先打断当前任务,现在切换就是纯粹"换视角"。
        """
        summaries = list_sessions(self.sessions_dir)
        if not 1 <= int(index) <= len(summaries):
            return {"ok": False, "msg": "会话不存在"}
        chosen = summaries[int(index) - 1]
        live = self._runner_for_log(chosen.path)
        if live is not None:
            self.switch_to(live)                 # 后台任务:接上它当前的内存上下文
            key, n = live.key, max(0, len(live.session.messages) - 1)
        else:
            runner = self.open_session(load_path=chosen.path)
            key, n = runner.key, max(0, len(runner.session.messages) - 1)
        self.ui.push("cleared", session=key)
        self.ui.push("resumed", session=key, messages=self._history_for_frontend())
        self.ui.push("info", session=key,
                     text=f"已恢复 {n} 条消息(来自 {chosen.path.name})")
        self._push_sessions()
        return {"ok": True, "session": key}

    # ------------------------------------------------------------ 内部

    def _run_task(self, runner: SessionRunner, text: str, content=None) -> None:
        """在后台线程里跑一个回合(每个会话一个线程,互不干扰)。"""
        runner.busy = True
        runner.ui.push("busy", value=True)
        self._push_sessions()          # 立刻让侧栏标上"运行中"的转圈
        try:
            runner.agent.run_turn(text, content)
        except Exception as e:  # 引擎之外的意外错误也要让前端知道
            runner.ui.push("error", text=f"内部错误: {type(e).__name__}: {e}")
        finally:
            runner.busy = False
            runner.ui.push("busy", value=False)
            self._push_sessions()

    def _push_sessions(self) -> None:
        running_paths = {str(r.log_path) for r in self.running_sessions() if r.log_path}
        cur_path = self.session.log_path
        items = [
            {"index": i, "time": s.time, "n_messages": s.n_messages,
             "last_user_text": s.last_user_text,
             "current": s.path == cur_path,
             "running": str(s.path) in running_paths}
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
    def confirm_tool(self, tool: Tool, args: dict, reason: str = "") -> str:
        from minicode.explain import permission_why
        why = permission_why(tool.name, args, reason)
        with self._confirm_lock:             # 一次只处理一个确认请求
            self._confirm_event.clear()
            self._confirm_choice = "no"
            self.ui.push("confirm_request", tool=tool.name,
                         detail=self._permission_detail_text(tool, args),
                         why=why.get("why", ""), digest=why.get("digest", ""),
                         risks=why.get("risks", []))
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

        # 浏览器工具:让用户一眼看清"要开哪个网址 / 要执行什么 JS / 图存到哪"
        name = tool.name.rsplit("·", 1)[-1]        # 子代理工具带类型前缀
        if name == "browser_open":
            return f"打开网页:{args.get('url', '')}"
        if name == "browser_eval":
            js = str(args.get("js", ""))
            return "在当前页面里执行这段 JS:\n" + (
                js if len(js) <= PREVIEW_CHARS else js[:PREVIEW_CHARS] + "…")
        if name == "browser_screenshot":
            return (f"把当前页面截图存到:{args.get('path') or 'shots/ 下(自动命名)'}"
                    + ("" if args.get("full_page", True) else "(只截可视区)"))

        return json.dumps(args, ensure_ascii=False)[:500]


def create_app(config, workspace: Path, llm=None, ui: GuiUI | None = None,
               sessions_dir: Path | None = None, config_path: Path | None = None):
    """组装 GUI 形态的完整应用;llm/sessions_dir/config_path 仅供测试注入。

    MCP 客户端、LLM、沙箱在这里建**一次**,由所有会话共享 ——
    新建会话只多一个 Agent/Session,不会重复拉起 MCP 子进程。
    """
    ui = ui or GuiUI()
    llm = llm or LLMClient(config)
    mcp = connect_servers(parse_servers(config.mcp_servers), log=mcp_log(ui))
    sandbox = SandboxState(policy_from_raw(config.sandbox), workspace)
    bridge = Bridge(config, workspace, ui,
                    sessions_dir=sessions_dir or SESSIONS_DIR,
                    config_path=config_path or CONFIG_PATH,
                    llm=llm, mcp_tools=mcp.build_tools(), sandbox=sandbox)
    bridge._mcp = mcp
    for runner in bridge._runners.values():
        runner.agent.mcp = mcp          # 退出时 bridge.close() 统一关停
    return bridge, ui, bridge.agent, bridge.session
