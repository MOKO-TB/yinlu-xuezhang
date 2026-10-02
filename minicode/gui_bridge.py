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
from minicode import asking, model_probe
from minicode import workspace as ws
from minicode.activate import take_pending_panel as _take_pending_panel
from minicode.app_setup import (build_runner_agent, instruction_layers,
                                instructions_line, mcp_log)
from minicode.attachments import (
    UPLOAD_DIR, Attachment, build_content, data_url, display_text, is_image,
    mime_for, parse_attachment_note, preview_data_url, save_attachments,
    save_dropped_bytes,
)
from minicode.campus import frontend_payload as campus_map_data
from minicode.compaction import estimate_tokens
from minicode.config import (BUILTIN_PROVIDER_ID, CONFIG_PATH,
                             API_KEY_PLACEHOLDERS, save_config)
from minicode.gui_ui import GuiUI
from minicode.kb import kb_dirs, kb_stats as kb_stats_fn
from minicode.mcp import connect_servers, parse_servers, summary_rows
from minicode.memory import (
    dream, dream_all, global_memory_path, load_from as mem_load_file,
    memory_path as mem_path, save_entry,
)
from minicode.llm import LLMClient
from minicode.library import catalog_health, health_notice
from minicode.model_catalog import session_metrics
from minicode.paths import (APP_DIR, SESSIONS_DIR, diagnostics as paths_diagnostics,
                            resource_path)
from minicode.permission import MODE_LABELS, PERMISSION_MODES
from minicode.sandbox import SandboxState, classify_path, policy_from_raw, policy_to_raw
from minicode.session import (derive_title,
                              export_filename as session_export_name,
                              list_sessions,
                              search_logs as search_session_logs,
                              to_markdown as session_to_markdown,
                              user_message_at)
from minicode.skills import load_skills, skill_dirs
from minicode.suggestions import build_suggestions, collect_user_texts, continue_chips
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

    @property
    def workspace(self) -> str:
        """当前工作区路径。用属性而不是构造时的快照 —— 工作区可以切换。"""
        return str(self._b.workspace)

    def get_info(self) -> dict:
        return self._b.get_info()

    def send_task(self, text: str, attachments: str = "[]") -> dict:
        return self._b.send_task(text, attachments)

    def regenerate(self) -> dict:
        """重新生成:撤掉上一段回答,照原样重跑那句提问(带上当时的附件)。"""
        return self._b.regenerate()

    def pick_attachments(self) -> dict:
        return self._b.pick_attachments()

    def attachment_full(self, path: str) -> dict:
        """灯箱打开时按需取原图(v0.9.97)。纯数据方法:回 data URL,不回本地路径。"""
        return self._b.attachment_full(path)

    def workspace_search_files(self, query: str = "", limit: int = 20) -> dict:
        """输入框 @ 引用:工作区内按名字搜文件(只读,相对路径)。"""
        return self._b.workspace_search_files(query, limit)

    def workspace_files_list(self, folder: str = "uploads") -> dict:
        """工作区产物目录的清单(uploads/images/charts,名字/大小/日期 + 总占用)。"""
        return self._b.workspace_files_list(folder)

    def workspace_files_delete(self, names_json: str = "[]",
                               folder: str = "uploads") -> dict:
        """删掉选中的产物文件;只认那三个目录里的真文件名。"""
        return self._b.workspace_files_delete(names_json, folder)

    def set_sandbox(self, enabled: bool) -> dict:
        return self._b.set_sandbox(enabled)

    def consolidate_memory(self, scope: str = "all") -> dict:
        return self._b.consolidate_memory(scope)

    def remember(self, text: str, scope: str = "project") -> dict:
        """记一条长期记忆(只转发:存哪、怎么存都在桥里)。"""
        return self._b.remember(text, scope)

    def edit_resend(self, ordinal: int, new_text: str, expect: str = "",
                    revert_files: bool = False) -> dict:
        """编辑重发;`revert_files=True` 才一起退回那一问之后改过的文件(前端点过头)。"""
        return self._b.edit_resend(ordinal, new_text, expect, bool(revert_files))

    def fork_session(self, ordinal: int) -> dict:
        return self._b.fork_session(ordinal)

    def get_suggestions(self) -> dict:
        return self._b.get_suggestions()

    def share_image(self, text: str = "") -> dict:
        """把某段回答排成一张能发群的卡片图(实现在 Bridge,工作区在那边)。"""
        return self._b.share_image(text)

    def rate_message(self, rating: int, question: str = "", answer: str = "") -> dict:
        """消息级 👍/👎 反馈:落到应用目录的反馈日志。"""
        return self._b.rate_message(rating, question, answer)

    def get_countdowns(self) -> dict:
        """首页倒计时 chips:校历里最近的安排。"""
        return self._b.get_countdowns()

    def get_weather_card(self) -> dict:
        """首页天气 chip:只读缓存,当场返回,绝不等网络。"""
        return self._b.get_weather_card()

    def get_schedule_view(self) -> dict:
        """课表面板:今日/明日/本周网格(只读本机课表)。"""
        return self._b.get_schedule_view()

    def schedule_save_times(self, rows_json: str = "[]") -> dict:
        """课表面板「填作息表」:存本人按教务处抄来的每节起止(空数组=清掉)。"""
        return self._b.schedule_save_times(rows_json)

    def schedule_import_open(self) -> dict:
        """教务导入第 1 步:打开看得见的登录页(登录由人做)。"""
        return self._b.schedule_import_open()

    def schedule_import_read(self) -> dict:
        """教务导入第 2 步:读学期课表并解析(不写文件)。"""
        return self._b.schedule_import_read()

    def schedule_import_model(self) -> dict:
        """教务导入第 2 步的退路:页面文字交给模型整理。"""
        return self._b.schedule_import_model()

    def schedule_import_confirm(self, term_start: str = "") -> dict:
        """教务导入第 3 步:用户点头后才落盘(要填第 1 周周一)。"""
        return self._b.schedule_import_confirm(term_start)

    def schedule_import_clear(self) -> dict:
        """教务导入收尾:关窗口并清掉那份独立 profile 的登录态。"""
        return self._b.schedule_import_clear()

    # 2026-10-01 删掉 `schedule_import_status` / `scores_import_status` 这一对门面方法:
    # 前端一次都没调过(全文件 0 命中),而 `*_import_open` 每次开面板都把 `_jw_result` /
    # `_scores_result` 清空 —— 也就是说它们**结构上**永远只能回 pending=False。
    # 留着的表现是"docstring 承诺了面板开关会问一次,而没人问",这不是缺根线,是假承诺。
    # 想真做(读到没确认的那份跨面板活着 + 开面板提示「还等你确认 / 丢弃」)先看 §6 #45。

    def scores_import_open(self) -> dict:
        """成绩导入第 1 步:打开看得见的登录页(登录由人做)。"""
        return self._b.scores_import_open()

    def scores_import_read(self) -> dict:
        """成绩导入第 2 步:读成绩表并解析(不写文件)。"""
        return self._b.scores_import_read()

    def scores_import_model(self) -> dict:
        """成绩导入第 2 步的退路:页面文字交给模型整理。"""
        return self._b.scores_import_model()

    def scores_import_confirm(self) -> dict:
        """成绩导入第 3 步:用户点头后才落盘 scores.json。"""
        return self._b.scores_import_confirm()

    def scores_import_clear(self) -> dict:
        """成绩导入收尾:关窗口并清掉那份独立 profile 的登录态。"""
        return self._b.scores_import_clear()

    def schedule_undo(self) -> dict:
        """课表面板「换回上一版」(名字避开输入框那个已有的 undo)。"""
        return self._b.schedule_undo()

    def add_dropped_attachments(self, files_json: str = "[]") -> dict:
        """把拖进对话框的文件收进 uploads/(与 📎 选文件同一条落盘路径)。"""
        return self._b.add_dropped_attachments(files_json)

    def read_clipboard(self) -> dict:
        """右键「粘贴」:读本机剪贴板(文本/图片),免掉 WebView2 那条授权提示。"""
        return self._b.read_clipboard()

    def set_chrome(self, dark: bool) -> dict:
        """开机与每次切主题报一次:系统标题栏跟着界面染色、写工作区名(p2)。"""
        return self._b.set_chrome(dark)

    def set_notice_watch(self, enabled: bool) -> dict:
        """主动推荐开关:官网新通知看门狗。"""
        return self._b.set_notice_watch(enabled)

    def get_onboarding(self) -> dict:
        """首次运行向导:该不该弹 + 三步内容 + 数据目录。"""
        return self._b.get_onboarding()

    def dismiss_onboarding(self, key: str = "first_run") -> dict:
        """首次运行向导勾了「不再显示」。"""
        return self._b.dismiss_onboarding(key)

    def stop_task(self) -> dict:
        return self._b.stop_task()

    def confirm_reply(self, choice: str) -> dict:
        return self._b.confirm_reply(choice)

    def ask_reply(self, payload) -> dict:
        """提问卡的回答(「发送回复」/「按推荐继续」)。只转发,判定与记账都在桥里。"""
        return self._b.ask_reply(payload)

    def undo(self) -> dict:
        return self._b.undo()

    def undo_step(self, seq=None) -> dict:
        """审查视图「回退这一处」:只退清单上那一条,比它更新的改动原样留着。"""
        return self._b.undo_step(seq)

    def undo_diff(self, seq=None) -> dict:
        """审查视图「看改了什么」:点开某一处才要的那一份 diff(后端算,前端不拼文案)。"""
        return self._b.undo_diff(seq)

    def undo_records(self) -> dict:
        """「本轮改动」整份清单:编辑重发前要按回合号数一下"这一问之后改过几处"。

        只读;绝对路径不在载荷里(后端给的就是相对工作区那份)。
        """
        return self._b.undo_records()

    def undo_all(self) -> dict:
        """审查视图「全部回退」:一路退回本轮开始前(命令改的不在这份账里)。"""
        return self._b.undo_all()

    def new_task(self) -> dict:
        return self._b.new_task()

    def compact_now(self) -> dict:
        return self._b.compact_now()

    def set_permission_mode(self, mode: str) -> dict:
        return self._b.set_permission_mode(mode)

    def refresh_sessions(self) -> dict:
        return self._b.refresh_sessions()

    def resume_session(self, index) -> dict:
        return self._b.resume_session(index)

    def delete_session(self, index) -> dict:
        return self._b.delete_session(index)

    def get_asset(self, rel_path: str) -> dict:
        return self._b.get_asset(rel_path)

    def open_url(self, url: str) -> dict:
        return self._b.open_url(url)

    def get_activity(self) -> dict:
        return self._b.get_activity()

    def get_board(self) -> dict:
        """跨会话看板:几件事在跑、哪几件等你的决定、最近做完了什么(只读)。"""
        return self._b.get_board()

    def get_monitor_pref(self) -> dict:
        return self._b.get_monitor_pref()

    def set_monitor_pref(self, is_open=None, folded_json: str = "") -> dict:
        return self._b.set_monitor_pref(is_open, folded_json)

    def reveal_file(self, path: str) -> dict:
        return self._b.reveal_file(path)

    def get_providers(self) -> dict:
        return self._b.get_providers()

    def save_providers(self, providers_json: str, active_id: str) -> dict:
        return self._b.save_providers(providers_json, active_id)

    def set_provider(self, provider_id: str) -> dict:
        return self._b.set_provider(provider_id)

    def set_session_model(self, provider_id: str, model: str = "") -> dict:
        """任务面板:把当前会话切到「供应商 · 模型」(不影响全局默认)。"""
        return self._b.set_session_model(provider_id, model)

    def test_model(self, provider_id: str, model: str = "", base_url: str = "",
                   api_key: str = "") -> dict:
        """模型管理「测试」:真发一条请求,回报连通/窗口/是否支持看图。"""
        return self._b.test_model(provider_id, model, base_url, api_key)

    def fetch_models(self, provider_id: str, base_url: str = "",
                     api_key: str = "") -> dict:
        """模型管理「从服务获取」:拉服务端的模型列表(GET /models)。"""
        return self._b.fetch_models(provider_id, base_url, api_key)

    def export_session(self) -> dict:
        """把当前会话导出成 Markdown 文件。"""
        return self._b.export_session()

    def mydata_inventory(self) -> dict:
        """「我的数据」面板:数据在哪 + 每类有哪些文件(只读)。"""
        return self._b.mydata_inventory()

    def mydata_export(self, include_sessions: bool = True) -> dict:
        """导出迁移包(先弹"另存为");密钥与教务登录态压根不在候选里。"""
        return self._b.mydata_export(include_sessions)

    def mydata_import_pick(self) -> dict:
        """导入第 1 步:选包 + 给逐条预览,**不写盘**。"""
        return self._b.mydata_import_pick()

    def mydata_import_apply(self, nonce: str = "") -> dict:
        """导入第 2 步:用户点头后按预览落盘(替换前先备份)。"""
        return self._b.mydata_import_apply(nonce)

    def mydata_forget_import(self, nonce: str = "") -> dict:
        """预览看了说"算了":丢掉这次预览,不留"稍后还能写"的口子。"""
        return self._b.mydata_forget_import(nonce)

    def mydata_open_dir(self) -> dict:
        """在资源管理器里打开数据目录。"""
        return self._b.mydata_open_dir()

    def mood_review(self, days: int = 7) -> dict:
        """情绪面板:最近一周自己记了什么(现读现给,桥里不留副本)。"""
        return self._b.mood_review(days)

    def mood_export(self) -> dict:
        """情绪记录另存为文本文件(先弹"另存为")。"""
        return self._b.mood_export()

    def mood_clear(self) -> dict:
        """清空本机情绪记录(前端已两次确认)。"""
        return self._b.mood_clear()

    def mood_log(self, score=None, note: str = "") -> dict:
        """情绪面板快捷记一句(一天一条,同一天覆盖)。"""
        return self._b.mood_log(score, note)

    def mood_help(self) -> dict:
        """「想找人聊聊」:学校心理中心的已核实去处(只给人看的那段)。"""
        return self._b.mood_help()

    def search_sessions(self, query: str = "") -> dict:
        """侧栏搜索:在所有历史会话留档里找关键词。"""
        return self._b.search_sessions(query)

    def get_reasoning(self) -> dict:
        """思考档位面板:当前模型能力 + 档位列表 + 当前选择。"""
        return self._b.get_reasoning()

    def set_reasoning_choice(self, choice: str) -> dict:
        """切换当前会话的思考档位(默认/关闭/低/高)。"""
        return self._b.set_reasoning_choice(choice)

    def set_vision(self, on: bool) -> dict:
        """强制开关"给模型发图":能力表没登记的自定义模型用得上。"""
        return self._b.set_vision(on)

    def close_choice(self, action: str, remember: bool = False) -> dict:
        """关窗确认框:action = hide(最小化到托盘) / exit(退出应用)。"""
        return self._b.close_choice(action, remember)

    def get_recent_projects(self) -> dict:
        """侧栏「项目」面板:最近工作区 + 当前(给任务栏下面那块工作区面板)。"""
        return self._b.get_workspaces()

    def get_session_metrics(self) -> dict:
        """任务面板:当前会话模型/能力/消耗/上下文占用/可切列表。"""
        return self._b.get_session_metrics()

    def get_token_usage(self, days: int = 7) -> dict:
        """每日 token 用量统计(弹窗圆环图数据源)。"""
        return self._b.get_token_usage(days)

    def get_skills(self) -> dict:
        """技能面板:内置/应用/工作区三个渠道里的技能清单。"""
        return self._b.get_skills()

    def get_skill_body(self, name: str) -> dict:
        """技能详情:点开一张技能卡片时取那份 SKILL.md 全文。

        这一条转发是 v0.9.39 补的洞:`Bridge.get_skill_body` 早就在,门面漏了
        一行,于是桌面版点技能卡片只会得到一句「读取技能失败:TypeError …」。
        `tests/test_clipboard.py::test_frontend_only_calls_api_that_exist` 现在
        逐名对账,这类"桥有、门面没接"的静默坏掉不指望人眼发现。
        """
        return self._b.get_skill_body(name)

    def get_skill_channels(self) -> dict:
        """技能面板「渠道」tab:技能目录的存在状态与各自技能数。"""
        return self._b.get_skill_channels()

    def get_slash_commands(self) -> dict:
        """对话型 slash 命令表(/ 面板混排用)。"""
        return self._b.get_slash_commands()

    def export_ics(self) -> dict:
        """课表面板「导出到日历」。"""
        return self._b.export_ics()

    def add_skill(self, name: str, description: str = "", body: str = "",
                  channel: str = "app") -> dict:
        """技能面板「添加技能」:写一个 skills/<name>/SKILL.md。"""
        return self._b.add_skill(name, description, body, channel)

    def get_kb_candidates(self, status: str = "") -> dict:
        """技能面板「候选」tab:知识库候选清单(待审核在前)。"""
        return self._b.get_kb_candidates(status)

    def review_kb_candidate(self, index, action: str) -> dict:
        """候选审核:并入正式知识库(merge)或拒绝(reject)。"""
        return self._b.review_kb_candidate(index, action)

    def get_plan(self) -> dict:
        """计划面板:plans/plan.md 的每步状态(可执行/被卡住)+ 一键派发文案。"""
        return self._b.get_plan()

    def get_workspaces(self) -> dict:
        """工作区弹窗:当前 / 默认 / 最近。"""
        return self._b.get_workspaces()

    def pick_workspace(self) -> dict:
        """弹系统文件夹选择框(只选不改)。"""
        return self._b.pick_workspace()

    def switch_workspace(self, path: str) -> dict:
        return self._b.switch_workspace(path)

    def reset_workspace(self) -> dict:
        """回到默认工作区(程序目录)。"""
        return self._b.reset_workspace()

    def forget_workspace(self, path: str) -> dict:
        """把某个工作区移出最近列表(不切走、不删文件夹)。"""
        return self._b.forget_workspace(path)

    def delete_project(self, path: str) -> dict:
        """项目面板「删除」:移出列表;删当前项目则回默认工作区。"""
        return self._b.delete_project(path)

    def rename_workspace(self, path: str, name: str) -> dict:
        """给项目起备注名(只改 workspaces.json 里的显示名,不碰磁盘)。"""
        return self._b.rename_workspace(path, name)

    def list_project_files(self, path: str, subdir: str = "") -> dict:
        """项目面板「查看文件」:列某个工作区(或其子目录)的直接子项。

        只读、有上限;敏感目录(sandbox 同一套清单)与越界路径一律拒绝 ——
        面板是纯展示入口,不给它变成任意目录遍历器。
        """
        return self._b.list_project_files(path, subdir)


def _instructions_info(workspace) -> dict:
    """侧栏那一行「指令层」的数据:读到几层、各几行、有没有被截。

    读不动就当没这一层(空 line → 前端整行不出现),不编一句"未配置指令"占版面。
    """
    try:
        layers = instruction_layers(workspace)
        return {"ok": True, "line": instructions_line(workspace), "layers": layers}
    except Exception as exc:          # 这一行读不出来不该把整个 get_info 带崩
        return {"ok": False, "line": "", "layers": [],
                "msg": f"读取指令层失败:{type(exc).__name__}: {exc}"}


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except Exception:
        return False


def restore_user_atts(workspace, content) -> list[dict]:
    """从留档的用户消息复原附件标签(v0.9.69,§6 #5 挂账的"历史会话附件可视化")。

    发送时前端在本地摆标签,而留档里只有 [附件] 清单 —— 恢复时把清单读回来、
    对工作区核一遍:文件还在的图片给缩略图与真实大小;不在了的只剩文件名、
    当时记下的大小和一个 missing 标记,绝不补一张编出来的缩略图。
    路径必须仍落在工作区内才读文件(留档可能来自别人导进来的会话)。
    """
    if isinstance(content, list):
        text = "".join(str(p.get("text") or "") for p in content
                       if isinstance(p, dict) and p.get("type") == "text")
    else:
        text = str(content or "")
    out: list[dict] = []
    for entry in parse_attachment_note(text):
        rel = entry["path"]
        p = Path(rel)
        item: dict = {"name": p.name, "path": rel,
                      "kind": "image" if is_image(p) else "file",
                      "size_text": entry["size_text"]}
        kind, real = classify_path(Path(workspace), rel)
        if kind == "inside" and real.is_file():
            try:
                item["size"] = real.stat().st_size
            except OSError:
                pass
            if item["kind"] == "image":
                url = preview_data_url(real)
                if url:
                    item["preview"] = url
        else:
            item["missing"] = True
        out.append(item)
    return out


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
                 llm=None, mcp_tools=None, sandbox=None,
                 workspace_file: Path | None = None, app_dir: Path | None = None):
        self.config = config
        # 模型能力探测(窗口多大 / 能不能看图):有缓存就立即生效,没有就在
        # 后台问一次模型服务,结论经 model_catalog 影响面板标签与压缩阈值。
        model_probe.ensure_async(config.base_url, config.api_key, config.model)
        self.workspace = workspace
        self.ui = ui
        self.sessions_dir = sessions_dir
        self.config_path = config_path
        # 工作区记忆(workspaces.json)与「默认工作区」的基准目录
        self.app_dir = Path(app_dir) if app_dir is not None else APP_DIR
        self.workspace_file = (Path(workspace_file) if workspace_file is not None
                               else ws.state_path(self.app_dir))
        self._shared_llm = llm
        # 模型看门狗:一次只跑一条(探的是同一个服务端,多起只是多敲 GET)。
        self._liveness_lock = threading.Lock()
        self._liveness_running = False
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
        # 提问卡(v0.9.96):与确认框**各一条锁各一个事件**,不共用 ——
        # 共用的话"用户点了提问的选项"会被当成对上一个确认的答复,那是串了答案。
        self._ask_lock = threading.Lock()
        self._ask_event = threading.Event()
        self._ask_answer: dict = {}
        self._ask_questions: list = []      # 当前这张卡问了什么(留痕那一行要用)
        # 「此刻有没有一张卡等人答」(跨会话看板 B1 用,见 _wait_event)
        self._ask_open = False
        self._confirm_open = False
        self._closing = False             # 关窗中:等前端回答的线程据此收口
        # 标题栏(p2):前端报过一次主题之后就记着,切工作区时只改字不重染
        self._chrome_dark: bool | None = None
        ui.confirm_handler = self.confirm_tool
        ui.ask_handler = self.ask_user
        # 停止键与关窗都要能叫醒"正在等用户点"的那两条线程(见 _wait_event)。
        ui.abandon_pending = self.abandon_pending
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
        ui.on_llm_failure = lambda _msg: self._watch_model_recovery(ui)
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

    def _watch_model_recovery(self, ui) -> None:
        """模型调不通之后起一条看门狗,服务活了就告诉用户一声(发现三说的"死胡同")。

        以前失败只有 `ui.llm_error` 那一句,之后没有任何恢复信号 —— 内网网关抖动几十秒
        就回来的日常,用户只能自己反复点发送试。探活只问 `GET /models`:不发对话请求、
        不动 Key 的额度。恢复通知走两条现成的路(聊天里一行 info + 一条桌面通知),
        **刻意不新增提醒类别**:那要连通知开关、按天账本、托盘菜单一起改,不是一条
        "告诉你服务回来了"该付的重量。
        """
        with self._liveness_lock:
            if self._liveness_running:
                return
            self._liveness_running = True

        def probe() -> bool:
            status, _names = model_probe.list_models(
                self.config.base_url, self.config.api_key, timeout=10.0)
            # 404 也算"活着":这家没实现 /models,但网络与服务都是通的
            return status in (200, 404)

        def worker() -> None:
            from minicode import proactive, tray
            try:
                got = model_probe.watch_liveness(probe)
                if got["recovered"]:
                    text = (f"模型服务又能连上了(第 {got['tried']} 次探活问通的),"
                            "刚才没答上来的那句可以重发了")
                    ui.push("info", text=text)
                    sent = tray.notify("模型恢复了", text, kind="recovery")
                    proactive.wd_log(
                        f"liveness: recovered at try {got['tried']}, "
                        f"toast={'ok' if sent.get('ok') else sent.get('reason', 'no')}")
                else:
                    # 没恢复也要留一行:否则用户说"到底通没通过"时只能靠猜
                    proactive.wd_log(
                        f"liveness: still down after {got['tried']} tries,不再盯")
            except Exception as e:      # 看门狗出错绝不影响正常使用
                from minicode import proactive
                proactive.wd_log(f"liveness: 探活自身出错 {type(e).__name__}: {e}")
            finally:
                with self._liveness_lock:
                    self._liveness_running = False

        threading.Thread(target=worker, daemon=True).start()

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
        # 先立旗再叫醒:正在等确认框/提问卡回答的那两条线程要的是"不会再有人答了"
        # 这个事实,晚一步设 `_closing` 它们就会在 `request_stop` 之后又循环回去。
        self._closing = True
        self.abandon_pending("窗口正在关闭")
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
        provider = self.config.provider
        # 内置校园服务的内网地址不下发给界面:用户只看得到模型名。
        builtin_active = bool(provider and provider.id == BUILTIN_PROVIDER_ID)
        return {
            "app_name": APP_NAME,
            "version": __version__,
            "model": self.config.model,
            "base_url": "" if builtin_active else self.config.base_url,
            "workspace": str(self.workspace),
            "workspace_name": ws.display_name(self.workspace, self.app_dir),
            "workspace_is_default": ws.is_default(self.workspace, self.app_dir),
            "permission_mode": self.config.permission_mode,
            "permission_modes": [
                {"value": m, "label": MODE_LABELS[m]}
                for m in PERMISSION_MODES
            ],
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
            # 通知点击直达(v0.9.72):--activate 拉起且没交接出去时,自己
            # 启动的这个实例在首屏把落点面板打开(一次性取走,取完即清)。
            "activate_panel": _take_pending_panel(),
            "sandbox": self._sandbox_info(),
            "vision": self._vision_on(fallback_model=self.config.model),
            "vision_forced": bool(self.config.vision),
            "paths": paths_diagnostics(),
            # L3(v0.9.101 ④):这一轮实际读到了哪几层指令文件。只报份数与行数,
            # 不贴内容、也不报"命中了哪条" —— 后者是把模型的自述当因果证据。
            "instructions": _instructions_info(self.workspace),
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
        """侧栏 MCP 健康行 + get_info 的数据(P3,v0.9.99 起是转发)。

        形状与判定在 `mcp.summary_rows` 一处 —— 终端 /health 与这里消费
        同一份,不再各抄一份配置合并逻辑。
        """
        mcp = self._mcp or getattr(self.agent, "mcp", None)
        return summary_rows(self.config, mcp)

    # ------------------------------------- 对话里自助管理 MCP(v0.9.61)

    def _mcp_admin(self, action: str, name: str = "", raw: dict | None = None) -> str:
        """mcp 工具的落盘与热重载(装进 mcp.set_admin_hook 的那半)。

        config.json 是唯一事实:这里改完 save_config 立刻落盘,再整组重连
        (旧进程关掉、启用的重连、工具清单原地去旧换新 —— 现有会话的注册表
        也当场换,新清单下一轮就能用,不用重启学长)。
        """
        from minicode import mcp as mcp_mod
        from minicode.config import save_config
        action = (action or "").strip().lower()
        name = str(name or "").strip()
        cfg = self.config
        servers = [dict(s) for s in (cfg.mcp_servers or []) if isinstance(s, dict)]

        def _sid_of(s: dict) -> str:
            return str((s.get("id") or s.get("name") or "")).strip()

        def _persist_and_reload(extra: str) -> str:
            cfg.mcp_servers = servers
            save_config(self.config_path, cfg)
            old = self._mcp
            try:
                if old is not None:
                    old.close_all()          # 旧子进程不留孤儿
            except Exception:
                pass
            new = mcp_mod.connect_servers(
                mcp_mod.parse_servers(cfg.mcp_servers), log=mcp_log(self.ui))
            self._mcp = new
            self._mcp_tools[:] = new.build_tools()   # 原列表换内容:所有会话注册表同步
            for runner in self._runners.values():
                runner.agent.mcp = new
                reg = runner.agent.registry
                reg[:] = ([t for t in reg if not t.name.startswith("mcp_")]
                          + self._mcp_tools)
                runner.agent.refresh_registry()   # 只换列表 = 新工具能看见却调不动
            n = len(self._mcp_tools)
            # 侧栏 MCP 健康行跟着刷新:配置与活状态只有这里有(同一份 _mcp_summary)
            self.ui.push("mcp_summary", session=self._current.key,
                         items=self._mcp_summary())
            return (extra + f"(已热重载:当前 {len(servers)} 个服务器 / {n} 个工具,"
                    "现有会话下一轮即可用,不用重启)")

        if action == "list":
            if not servers:
                return ("还没有配置任何 MCP 服务器。用 action=add 加一个:"
                        "本地进程给 name+command(+args),远程给 name+transport"
                        "(http/sse)+url;需要鉴权的 headers 里写「Authorization: "
                        "${MY_TOKEN}」—— 密钥走环境变量,不落 config.json。")
            lines = [f"- {s.get('id') or s.get('name')}"
                     f"({'启用' if s.get('enabled', True) else '停用'},"
                     f"{s.get('transport') or 'stdio'})" for s in servers]
            tools = ", ".join(x.name for x in self._mcp_tools) or "(无)"
            return ("已配置的 MCP 服务器:\n" + "\n".join(lines)
                    + "\n当前挂着的工具:" + tools)
        if action == "add":
            if not isinstance(raw, dict) or not raw:
                return ("错误: action=add 需要给 raw(服务器配置):name 必填,"
                        "stdio 给 command(+args),http/sse 给 url(+headers)。")
            sid = str(raw.get("id") or raw.get("name") or "").strip()
            if not sid:
                return "错误: 配置里要有 name 字段作为服务器标识。"
            if any(_sid_of(s) == sid for s in servers):
                return (f"错误: 已有叫「{sid}」的服务器。要改配置先 remove 再 add,"
                        "或直接编辑 config.json。")
            probe = mcp_mod.probe_server(raw, log=mcp_log(self.ui))
            if not probe["ok"]:
                return (f"错误: 探测失败,没有写入配置 —— {probe['error']}。"
                        "服务器可能只是暂时不在线:修好命令/地址后再加一次。")
            servers.append(dict(raw))
            # 探测时顺便把「对端声明了什么、学长只接了 tools」讲清楚:加服务器这一刻
            # 正是用户最容易以为"接上就等于全能用"的时候。
            caps = str(probe.get("caps") or "")
            return _persist_and_reload(
                f"已添加「{sid}」({probe['server'] or '服务器'}),"
                f"探到 {probe['n_tools']} 个工具。" + (f"{caps}。" if caps else ""))
        if action in ("remove", "enable", "disable"):
            if not name:
                return f"错误: action={action} 需要 name。"
            hit = next((s for s in servers if _sid_of(s) == name), None)
            if hit is None:
                return (f"错误: 没有叫「{name}」的服务器。"
                        f"现有的:{', '.join(_sid_of(s) for s in servers) or '(无)'}")
            if action == "remove":
                servers.remove(hit)
                return _persist_and_reload(f"已移除「{name}」。")
            hit["enabled"] = (action == "enable")
            if hit["enabled"]:
                probe = mcp_mod.probe_server(hit, log=mcp_log(self.ui))
                if not probe["ok"]:
                    return (f"错误: 启用前探测失败,配置没有改动 —— {probe['error']}。")
            return _persist_and_reload(
                f"已{'启用' if action == 'enable' else '停用'}「{name}」。")
        return "错误: 未知 action。可用: list / add / remove / enable / disable。"

    def get_providers(self) -> dict:
        """模型管理弹窗的数据源:每个供应商 + 它底下的模型清单。

        内置校园供应商的 key 与服务地址(内网 IP)都永不回传 —— 对用户只呈现为
        一个只读的「内置」条目;其他供应商本机单用户,key 原样返回便于修改。

        每个模型条目附带能力结论(窗口/是否支持看图,来自 model_catalog),
        界面上就是"328K · 纯文本"这类徽标;预设模板一并回传,省一次往返。
        """
        from minicode.config import PROVIDER_PRESETS
        from minicode.model_catalog import lookup
        out = []
        for p in self.config.providers:
            if p.id == BUILTIN_PROVIDER_ID:
                item = {**p.to_dict(), "api_key": "", "base_url": "",
                        "builtin": True, "enabled": True}
            else:
                item = p.to_dict()
            item["has_key"] = p.has_key
            item["models"] = [
                {**entry, **lookup(entry["name"]), "name": entry["name"],
                 "enabled": bool(entry.get("enabled", True))}
                for entry in p.model_entries()
            ]
            out.append(item)
        return {
            "providers": out,
            "active_provider": self.config.provider.id if self.config.provider else "",
            "active_model": self.config.model,
            "presets": PROVIDER_PRESETS,
        }

    def get_token_usage(self, days: int = 7) -> dict:
        """每日/按模型聚合的 token 用量统计(前端弹窗圆环图)。"""
        from minicode import usage
        return usage.statistics(int(days))

    # ------------------------------------------------------------ 技能面板

    def get_skills(self) -> dict:
        """技能页数据:当前生效的技能清单,带渠道归属与 frontmatter 简介。

        load_skills 是先到先得(应用目录压过工作区同名),所以渠道归属按
        skill_dirs 的顺序反推;正文不回传(点开详情时按路径读,保持轻量)。
        """
        from minicode.skills import describe_channels, merge_usage
        dirs = skill_dirs(self.workspace)
        labels = {c["key"]: c["label"] for c in describe_channels(self.workspace)}
        skills: list[dict] = []
        for s in load_skills(dirs):
            channel = ""
            for key, d in zip(("app", "builtin", "workspace"), dirs):
                try:
                    s.path.resolve().relative_to(Path(d).resolve())
                    channel = labels.get(key, key)
                    break
                except (ValueError, OSError):
                    continue
            skills.append({"name": s.name, "description": s.description,
                           "channel": channel, "path": str(s.path),
                           "is_builtin": channel == labels.get("builtin", "内置技能")})
        return {"ok": True, "skills": merge_usage(skills)}

    def get_skill_body(self, name: str) -> dict:
        """技能详情:读单个技能的 SKILL.md 全文(前端点开时才取,默认截断)。"""
        from minicode.skills import SKILL_BODY_LIMIT, find_skill
        skill = find_skill(load_skills(skill_dirs(self.workspace)), str(name or ""))
        if skill is None:
            return {"ok": False, "msg": f"未找到技能:{name}"}
        try:
            body = skill.path.read_text(encoding="utf-8")
        except OSError as exc:
            return {"ok": False, "msg": f"读取技能失败:{type(exc).__name__}: {exc}"}
        if len(body) > SKILL_BODY_LIMIT:
            body = body[:SKILL_BODY_LIMIT] + "\n…(内容过长,已截断)"
        return {"ok": True, "name": skill.name, "body": body}

    def get_skill_channels(self) -> dict:
        """渠道页「渠道」tab 的数据源(目录路径/存在状态/技能数)。"""
        from minicode.skills import describe_channels
        return {"ok": True,
                "channels": describe_channels(self.workspace, app_dir=self.app_dir)}

    def get_slash_commands(self) -> dict:
        """对话型 slash 命令表(唯一真相在 minicode.slash_commands,改字不动前端)。

        工作区自定义命令(F2,v0.9.100)并进同一张表:同名覆盖内置
        (slash_commands.expand 的顺序),前端零改动 —— 菜单里自动多出条目。
        """
        from minicode.slash_commands import PROMPT_COMMANDS, workspace_commands
        custom = workspace_commands(self.workspace)
        custom_cmds = {c["cmd"] for c in custom}
        rows = [{"cmd": c["cmd"], "label": c["label"], "desc": c["desc"],
                 "prompt": c["prompt"], "has_arg": "$text" in c["prompt"]}
                for c in PROMPT_COMMANDS if c["cmd"] not in custom_cmds]
        rows += [{"cmd": c["cmd"], "label": c["label"], "desc": c["desc"],
                  "prompt": c["prompt"], "has_arg": "$ARGS" in c["prompt"]}
                 for c in custom]
        return {"ok": True, "commands": rows}

    def export_ics(self) -> dict:
        """课表面板「导出到日历」:生成工作区里的 .ics(课表+待办+校历)。"""
        from minicode.ics_export import export
        return export(self.workspace)

    def add_skill(self, name: str, description: str = "", body: str = "",
                  channel: str = "app") -> dict:
        """把用户在面板上填的技能落盘成 SKILL.md;成功后广播刷新。"""
        from minicode.skills import create_skill
        result = create_skill(self.workspace, name, description, body, channel,
                              app_dir=self.app_dir)
        if result.get("ok"):
            self.ui.push("skills_changed", session=self._current.key)
            self.ui.push("info", text=result.get("msg") or "技能已添加")
        return result

    # ------------------------------------------------------------ 知识库候选审核

    def get_kb_candidates(self, status: str = "") -> dict:
        """技能面板「候选」tab:kb_suggest 落的知识候选清单(待审核在前)。

        只读展示;真正的并入/拒绝走 review_kb_candidate。status 可传
        pending/merged/rejected 过滤,默认全部。
        """
        from minicode import growth
        try:
            items = growth.list_candidates(str(status or ""))
        except Exception as exc:      # 文件坏了也不该拖垮面板
            return {"ok": False, "msg": f"读取候选失败:{type(exc).__name__}: {exc}"}
        pending = sum(1 for it in items
                      if str(it.get("status") or "pending") == "pending")
        return {"ok": True, "items": items, "pending": pending}

    def review_kb_candidate(self, index, action: str) -> dict:
        """候选审核:action=merge 并入正式知识库 / reject 拒绝。

        merge 会把候选写进 app_path('kb')/reviewed.json(人工核实过,credibility
        标 A-已审核),下次检索即可命中;绝不改爬虫生成的 kb/*.json。
        """
        from minicode import growth
        try:
            ok, msg = growth.review_candidate(index, action)
        except Exception as exc:
            return {"ok": False, "msg": f"审核失败:{type(exc).__name__}: {exc}"}
        if ok:
            self.ui.push("info", text=msg)
        return {"ok": ok, "msg": msg}

    # ------------------------------------------------------------ 计划面板

    def get_plan(self) -> dict:
        """计划面板的数据:plans/plan.md 拍平成每步带「可执行/被卡住」标记的状态。

        只读。步骤状态归 plan 工具(文件即状态),面板不去改文件 ——
        两边都能改就会互相盖,而且模型看不到界面偷偷改过的那一下。
        「一键派发」要的这句话也在后端拼(dispatch_text),前端只负责填回输入框。
        """
        from minicode.planning import dispatch_text, plan_state
        try:
            state = plan_state(self.workspace)
        except Exception as exc:      # 计划文件坏了/读不动都不该让面板打不开
            return {"ok": False, "has_plan": False, "goal": "", "steps": [],
                    "total": 0, "done": 0, "ready": [], "blocked": [], "failed": [],
                    "running": [],
                    "dispatch_text": "",
                    "msg": f"读取计划失败:{type(exc).__name__}: {exc}"}
        return {"ok": True, "dispatch_text": dispatch_text(state), **state}

    # ------------------------------------------------------------ 任务监控栏

    def get_activity(self) -> dict:
        """右侧「任务监控」栏的数据:这一轮真调过哪些工具、按先后排成时间线、
        此刻在跑哪一步、产出还在不在磁盘上。

        为什么在前端数不行:它手里只有**渲染过的事件流**,刷新一次、切一次会话就没了,
        而且工具卡片上显示的是省略过的文本。真数据在 `session.messages` 里(完整参数、
        完整回话),所以归类这件事整个放在 `minicode.activity.session_activity`
        (纯函数,好测)。这里只负责把当前会话递过去。
        时刻是唯一例外:消息里没有 Timestamp,只有 `GuiUI` 那张实时表记过真实开始
        与结束 —— 所以递的是**当前会话那个 UI** 的表(每会话一份,后台会话的进度
        不会串进你正在看的这一栏)。
        聚合逻辑再怎么坏都不该让界面打不开,所以异常兜成 ok:False 带一句原因。
        """
        from minicode.activity import session_activity
        ui = getattr(self._current, "ui", None)
        live = getattr(ui, "live_rows", None)
        now = getattr(ui, "live_now", None)
        try:
            data = session_activity(
                self.session.messages, workspace=self.workspace,
                live=live() if callable(live) else None,
                now=now() if callable(now) else None,
                busy=bool(getattr(self._current, "busy", False)))
            # 「本轮改动」跟着这一份载荷走:它和产出行是同一份事实的两个视图,
            # 同一次刷新到位,就不会出现"产出行说写了 3 个文件、审查清单还空着"。
            data["changes"] = self.undo_records()
            return data
        except Exception as exc:
            return {"ok": False, "groups": [], "timeline": {}, "now": None,
                    "summary": {}, "empty": True, "changes": self.undo_records(),
                    "msg": f"读取会话活动失败:{type(exc).__name__}: {exc}"}

    # ------------------------------------------------------------ 跨会话看板(B1)

    def get_board(self) -> dict:
        """「我手上到底有几件事在跑?」——四处现成结论拼的一屏,只读、不落盘。

        四处真源:在跑的会话来自 `running_sessions()`(桥内存,跨进程看不见,所以载荷
        里带着那句边界)、后台命令来自 `shell.bg_facts()`、计划状态来自
        `planning.plan_state`、定时任务来自 `jobs` 那本账。这里刻意**不重算任何东西**,
        也不新开一份"任务状态"文件(那要回答"会话被杀后谁清",还要交四处登记税)。
        """
        from minicode import board
        return board.aggregate({"会话": self._board_sessions,
                                "后台命令": self._board_background,
                                "计划": self._board_plan,
                                "定时任务": self._board_jobs})

    def _ws_label(self, path) -> str:
        """B2(v0.9.102):这一行活儿发生在哪个工作区(显示名口径与侧栏同源)。"""
        from minicode import workspace as ws
        if not path:
            return "未记录"
        return ws.display_name(path, self.app_dir) or "当前工作区"

    def _board_sessions(self) -> list[dict]:
        from minicode import board
        running = {str(r.log_path) for r in self.running_sessions() if r.log_path}
        rows = []
        for i, s in enumerate(list_sessions(self.sessions_dir,
                                            limit=board.SESSION_SCAN), start=1):
            if str(s.path) not in running:
                continue          # 拿不到确证就不列 —— 宁可少列,也不谎报"在跑"
            r = board.row(
                "running", derive_title(s.first_user_text) or s.path.name,
                f"{s.n_messages} 条 · {s.time} · 侧栏第 {i} 个",
                action={"type": "session", "index": i}, source="会话")
            r["ws"] = self._ws_label(s.workspace)
            rows.append(r)
        if self._ask_open or self._confirm_open:
            rows.append(board.row(
                "waiting", "学长在等你答",
                "提问卡或确认框开着还没回(没答按没同意算,这一条不会自己往下走)",
                action={"type": "card"}, source="会话"))
        return rows

    def _board_background(self) -> list[dict]:
        from minicode import board
        from minicode.tools import shell
        rows = []
        for j in shell.bg_facts():
            if j["running"]:
                rows.append(board.row(
                    "running", f"后台命令 {j['id']}:{j['command']}",
                    f"已跑 {j['elapsed']} 秒 · 输出 {j['chars']} 字",
                    action={"type": "ask", "text": f"看一下后台命令 {j['id']} 跑到哪了"},
                    source="后台命令"))
            else:
                rows.append(board.row(
                    "done", f"后台命令 {j['id']}:{j['command']}",
                    f"已结束,退出码 {j['exit_code']} · 输出 {j['chars']} 字",
                    action={"type": "ask", "text": f"看一下后台命令 {j['id']} 的输出"},
                    source="后台命令"))
        for r in rows:
            r["ws"] = self._ws_label(self.workspace)
        return rows

    def _board_plan(self) -> list[dict]:
        from minicode import board
        from minicode.planning import plan_state
        st = plan_state(self.workspace)
        if not st.get("has_plan"):
            return []
        by_n = {s["n"]: s for s in st["steps"]}
        rows = []
        for n in st.get("running") or []:
            s = by_n.get(n) or {}
            rows.append(board.row("running", f"计划第 {n} 步:{s.get('title') or ''}",
                                  "已标进行中(做完要标 done)" + (
                                      f" · 验收 {s['verify']}" if s.get("verify") else ""),
                                  action={"type": "panel", "panel": "plan"},
                                  source="计划"))
        for n in st.get("blocked") or []:
            s = by_n.get(n) or {}
            rows.append(board.row("waiting", f"第 {n} 步被上游卡住:{s.get('title') or ''}",
                                  "要人工决定:重做上游还是改计划",
                                  action={"type": "panel", "panel": "plan"},
                                  source="计划"))
        for n in st.get("verify") or []:
            s = by_n.get(n) or {}
            rows.append(board.row("waiting", f"第 {n} 步的验收没跑:{s.get('title') or ''}",
                                  f"判据 {s.get('verify')}",
                                  action={"type": "panel", "panel": "plan"},
                                  source="计划"))
        if st["total"] and st["done"] == st["total"]:
            rows.append(board.row("done", f"计划「{st.get('goal') or '未命名'}」全部做完",
                                  f"{st['done']}/{st['total']} 步",
                                  action={"type": "panel", "panel": "plan"},
                                  source="计划"))
        for r in rows:
            r["ws"] = self._ws_label(self.workspace)
        return rows

    def _board_jobs(self) -> list[dict]:
        from minicode import board, jobs, proactive
        book = jobs.load_state()
        rows = []
        for spec in proactive.JOB_SPECS:
            entry = book.get(spec.id) if isinstance(book.get(spec.id), dict) else {}
            last = str(entry.get("last_run") or "")
            if not last:
                continue          # 从没跑过的不占一行:这里没有"结论"可报
            result = str(entry.get("result") or "")
            rows.append(board.row(
                "done", f"定时任务「{spec.label or spec.id}」",
                f"上次 {last[:16]} · 结论 {result or '(没记结论)'}"
                f" · 共 {jobs.runs_of(spec.id, book)} 次",
                action={"type": "ask",
                        "text": f"说一下「{spec.label or spec.id}」上次跑出了什么"},
                source="定时任务"))
        return rows

    # ------------------------------------------------------------ 任务监控栏偏好


    def get_monitor_pref(self) -> dict:
        """这一栏开没开、哪几段被折叠过(纯本机界面偏好,不含任何内容数据)。"""
        from minicode import monitor
        return {"ok": True, **monitor.read_pref()}

    def set_monitor_pref(self, is_open=None, folded_json: str = "") -> dict:
        """记住用户对这一栏的开关/折叠。`is_open` 传 None = 这次只改折叠状态。

        `folded_json` 是增量(只带点过的那一段),否则用户折了「计划」再折「产出」
        会把「计划」又弹开 —— 那是"记不住"级别的假。
        """
        from minicode import monitor
        folded = None
        if folded_json:
            try:
                folded = json.loads(folded_json)
            except (TypeError, ValueError):
                folded = None
        try:
            val = None if is_open is None else (
                str(is_open).strip().lower() in ("true", "1", "yes")
                if isinstance(is_open, str) else bool(is_open))
        except (TypeError, ValueError):
            val = None
        return {"ok": True, **monitor.write_pref(open_=val, folded=folded)}

    # ------------------------------------------------------------ 工作区

    def get_workspaces(self) -> dict:
        """工作区弹窗的数据:当前 / 默认 / 最近(已不存在的目录自动隐去)。"""
        state = ws.load_state(self.workspace_file)
        return {
            "ok": True,
            "current": ws.entry(self.workspace, self.app_dir),
            "default": ws.entry(ws.default_workspace(self.app_dir), self.app_dir),
            "recent": ws.recent_entries(state, app_dir=self.app_dir),
            "busy": bool(self.running_sessions()),
        }

    def pick_workspace(self) -> dict:
        """弹系统文件夹选择框:**只选不改**(切不切由用户下一步决定)。"""
        window = getattr(self.ui, "window", None)
        if window is None:
            return {"ok": False, "msg": "窗口未就绪,无法打开文件夹选择框"}
        try:
            import webview
            picked = window.create_file_dialog(webview.FOLDER_DIALOG)
        except Exception as e:
            return {"ok": False,
                    "msg": f"打开文件夹选择框失败: {type(e).__name__}: {e}"}
        if not picked:
            return {"ok": False, "msg": "已取消"}
        chosen = picked if isinstance(picked, str) else picked[0]
        return {"ok": True, "path": str(chosen)}

    def forget_workspace(self, path: str) -> dict:
        """把某个工作区移出最近列表(不切走、更不删磁盘上的文件夹)。"""
        ws.forget(path, state_file=self.workspace_file, app_dir=self.app_dir)
        return {"ok": True, "recent": self.get_workspaces()["recent"]}

    def delete_project(self, path: str) -> dict:
        """把项目从列表里删掉(仅移出记录,磁盘文件夹不动)。

        只允许删「列表里存在的项目」—— 不在列表里的路径直接报错,
        避免把任意路径当成项目操作。当前项目也能删:删完回默认工作区。
        """
        target, _err = ws.resolve_target(path, app_dir=self.app_dir)
        if target is None:
            return {"ok": False, "msg": _err}
        state = ws.load_state(self.workspace_file, app_dir=self.app_dir)
        listed = any(ws.key(i.get("path") or "") == ws.key(target)
                     for i in state.get("recent") or [])
        if not listed:
            return {"ok": False, "msg": f"这个项目不在列表里:{target.name}"}
        is_current = ws.key(target) == ws.key(self.workspace)
        ws.forget(target, state_file=self.workspace_file, app_dir=self.app_dir)
        if is_current:
            result = self._apply_workspace(
                str(ws.default_workspace(self.app_dir)))
            if not result.get("ok"):
                return result
            self.ui.push("info", text=f"已移除项目:{target.name}(文件夹未删除)")
        else:
            self.ui.push("info", text=f"已移除项目:{target.name}(文件夹未删除)")
        return {"ok": True, "recent": self.get_workspaces()["recent"]}

    def rename_workspace(self, path: str, name: str) -> dict:
        """给项目起备注名(只改 workspaces.json,不碰磁盘上的文件夹)。"""
        target, _err = ws.resolve_target(path, app_dir=self.app_dir)
        if target is None:
            return {"ok": False, "msg": _err}
        final = ws.rename(target, name, state_file=self.workspace_file,
                          app_dir=self.app_dir)
        self.ui.push("workspace", session=self.session.key,
                     **ws.entry(self.workspace, self.app_dir))
        self.ui.push("info", text=(f"已改名:{final}"
                                   if final else f"已恢复默认名:{target.name}"))
        return {"ok": True, "name": final,
                "recent": self.get_workspaces()["recent"]}

    def list_project_files(self, path: str, subdir: str = "") -> dict:
        """项目面板「查看文件」:列某个工作区(或其子目录)的直接子项。

        只读、有上限;敏感目录与越界路径一律拒绝(ws.list_files 兑底)。
        """
        return ws.list_files(path, subdir, home=Path.home())

    def switch_workspace(self, path: str) -> dict:
        return self._apply_workspace(path)

    def reset_workspace(self) -> dict:
        """回到默认工作区(程序目录)。"""
        return self._apply_workspace(str(ws.default_workspace(self.app_dir)))

    def _apply_workspace(self, raw: str) -> dict:
        """切工作区的唯一入口:校验 → 停旧会话 → 换围栏 → 开新会话。

        旧会话运行器一律丢掉:它们的 ToolContext/Session 是按旧工作区装的
        (读写围栏、提示词里的工作区路径都是旧的),留着就是两个工作区混用。
        有任务在跑时直接拒绝,不偷偷停掉用户的工作。
        """
        target, err = ws.resolve_target(raw, app_dir=self.app_dir)
        if target is None:
            return {"ok": False, "msg": err}
        if ws.key(target) == ws.key(self.workspace):
            return {"ok": True, "path": str(target),
                    "msg": f"已经在用这个工作区:{target}"}
        running = self.running_sessions()
        if running:
            return {"ok": False,
                    "msg": f"还有 {len(running)} 个任务在跑:等它们结束或先停止,再切工作区"}
        self._close_runners()
        self.workspace = target
        if self._sandbox is not None:
            self._sandbox.retarget(target)
        ws.remember(target, state_file=self.workspace_file, app_dir=self.app_dir)
        runner = self.open_session()          # 新工作区从空会话开始
        # 先 cleared 再 workspace:前端用 cleared 认下新的 session 键,
        # 否则带旧 session 键的事件会被“后台会话不渲染”那道阑筛掉
        self.ui.push("cleared", session=runner.key)
        self.ui.push("workspace", session=runner.key,
                     **ws.entry(target, self.app_dir))
        self._push_sessions()
        self.ui.push("info", session=runner.key, text=f"工作区已切换:{target}")
        # 标题栏那行字跟着换(p2):前端不重画主题,这里自己补一次
        self._apply_chrome()
        return {"ok": True, "session": runner.key, "path": str(target)}

    def _close_runners(self) -> None:
        """丢掉所有会话运行器(切工作区时用),并请求它们手上的回合停下。"""
        for r in list(self._runners.values()):
            try:
                r.ui.request_stop()
            except Exception:
                pass
        self._runners.clear()
        self._current = None

    def save_providers(self, providers_json: str, active_id: str) -> dict:
        """整体保存供应商列表(前端编辑后提交),并切换到 active_id。

        v0.9.13 起每个供应商带自己的**模型清单**(`models`)与启停开关
        (`enabled`)。一个模型都没启用的供应商会被自动停用 —— 与其让它留在
        可切列表里、选中后把空模型名发给服务端,不如明确标成"停用"并说一声。
        """
        try:
            items = json.loads(providers_json)
        except json.JSONDecodeError as e:
            return {"ok": False, "msg": f"数据不是有效 JSON: {e}"}
        if not isinstance(items, list):
            return {"ok": False, "msg": "数据不是有效列表"}

        from minicode.config import (BUILTIN_PROVIDER, BUILTIN_PROVIDER_ID,
                                     DEFAULT_MODEL, Provider, _builtin_api_key,
                                     _dedupe_models, _models_from_raw, _slug)
        # 内置校园供应商不随前端提交走:v0.9.03 的提交里混进了它,导致任何保存
        # 都被「内置不可编辑」挡死(自定义模型存不进去)。这里一律跳过,由下方
        # 统一按代码补回;提交列表为空 = 只用内置,也允许。
        providers, seen = [], set()
        auto_disabled: list[str] = []
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
            entries = _dedupe_models(_models_from_raw(item.get("models")))
            providers.append(Provider(
                id=pid, name=str(item.get("name") or pid).strip() or pid,
                base_url=base.rstrip("/"),
                api_key=str(item.get("api_key") or ""),
                model=str(item.get("model") or DEFAULT_MODEL),
            ))
            # 模型清单与启停开关:v0.9.13 的界面提交里带 models;老前端(或手改
            # 配置)只给单个 model 字段时,兜底成"清单里的一条",行为与改版前一致。
            provider = providers[-1]
            if not entries and provider.model.strip():
                # 老前端(或手改配置)只给单个 model 字段:补成清单里的一条,
                # 让磁盘上的配置始终自洽(有 models 就一定能看出"启用了哪些")
                entries = [{"name": provider.model.strip(), "enabled": True}]
            provider.models = entries
            provider.enabled = item.get("enabled", True) is not False
            provider.sync_model()
            if not provider.enabled_models() and provider.enabled:
                provider.enabled = False          # 没勾任何模型:停用而不是留个坑
                auto_disabled.append(provider.name)
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

        # 当前激活的那个供应商正好被停用/没有启用模型:回落内置,并把原因说出来
        # (静默回落最糟:用户会以为"我刚加好的模型怎么没生效")
        fell_back = ""
        chosen = next((p for p in providers if p.id == active), None)
        if chosen is not None and (not chosen.enabled
                                   or not chosen.enabled_models()):
            fell_back = chosen.name
            self.config.active_provider_id = BUILTIN_PROVIDER_ID

        self._persist_config()
        self._apply_active_provider()
        # 实时同步(v0.9.11):任务面板的可切列表/能力标签直接吃这份配置,
        # 不推的话用户在模型管理里加完模型,面板要等切会话才看得到(p1/p2)。
        for r in self._runners.values():
            self.ui.push("session_metrics", session=r.key, **session_metrics(
                r.agent, r.session, self.config, r.llm_override))
        notes = []
        if auto_disabled:
            notes.append("没勾选任何模型的供应商已自动停用:" + "、".join(auto_disabled))
        if fell_back:
            notes.append(f"「{fell_back}」不可用,已切回内置校园模型")
        tail = ("(" + ";".join(notes) + ")") if notes else ""
        self.ui.push("info", text=f"已保存 {len(providers)} 个供应商,当前: "
                                  f"{self.config.provider.name} · {self.config.model}{tail}")
        return {"ok": True, "auto_disabled": auto_disabled, "fell_back": fell_back}

    def set_provider(self, provider_id: str) -> dict:
        target = next((p for p in self.config.providers if p.id == provider_id), None)
        if target is None:
            return {"ok": False, "msg": f"供应商不存在: {provider_id}"}
        if target.id != BUILTIN_PROVIDER_ID:
            if not target.enabled:
                return {"ok": False, "msg": f"「{target.name}」已停用,先在模型管理里启用"}
            if not target.enabled_models():
                return {"ok": False, "msg": f"「{target.name}」没有启用的模型"}
        self.config.set_active_provider(provider_id)
        self._persist_config()
        self._apply_active_provider()
        # 跟 save_providers 同理:面板立刻跟上新的全局模型(p1/p2 实时同步)
        for r in self._runners.values():
            if r.llm_override is None:      # 会话单独切过模型的不受全局影响
                self.ui.push("session_metrics", session=r.key, **session_metrics(
                    r.agent, r.session, self.config, None))
        self.ui.push("info", text=f"已切换到 {self.config.provider.name} · {self.config.model}")
        return {"ok": True}

    def set_session_model(self, provider_id: str, model: str = "") -> dict:
        """把当前会话的模型切到指定供应商(可指定它下面的某个模型)。

        给这个会话的 Agent 重建一个 LLMClient;后续回合立即用新模型。
        仅当"同一个供应商 + 同一个模型"时才清掉会话级覆盖 = 重新跟随全局
        (p1/p2 同步语义)。

        `model` 留空 = 用该供应商的第一个启用模型(兼容老前端只传供应商 id)。
        """
        runner = self._current
        if runner is None:
            return {"ok": False, "msg": "没有活动会话"}
        if runner.busy:
            return {"ok": False, "msg": "任务进行中,等这轮跑完再切模型"}
        target = next((p for p in self.config.providers if p.id == provider_id), None)
        if target is None:
            return {"ok": False, "msg": f"供应商不存在: {provider_id}"}
        if target.id != BUILTIN_PROVIDER_ID and not target.enabled:
            return {"ok": False, "msg": f"「{target.name}」已停用,先在模型管理里启用"}
        usable = target.enabled_models()
        picked_name = str(model or "").strip()
        if picked_name and picked_name not in usable:
            return {"ok": False, "msg": f"「{target.name}」下没有启用中的模型:{picked_name}"}
        picked_name = picked_name or (usable[0] if usable else target.model)
        if not picked_name:
            return {"ok": False, "msg": f"「{target.name}」没有可用的模型"}
        if not target.api_key.strip() or target.api_key.strip() in API_KEY_PLACEHOLDERS:
            return {"ok": False, "msg": f"「{target.name}」还没配 API Key,先在模型管理里配好"}
        follows_global = (target.id == self.config.active_provider_id
                          and picked_name == self.config.model)
        runner.llm_override = None if follows_global else \
            {"provider_id": target.id, "model": picked_name}
        # 具体到"哪个模型"的构造规则在 `LLMClient.scoped` 一处(子代理按档位分模型
        # 走同一条),这里不再手抄一遍 providers 的替换。
        runner.agent.llm = LLMClient.scoped(self.config, provider_id=target.id,
                                            model=picked_name)
        # 模型换了,思考能力表也换了:档位归回 default,并把新能力推给界面
        runner.agent.llm.reasoning_choice = "default"
        self._push_reasoning(runner)
        note = "(重新跟随全局默认模型)" if follows_global \
            else "(不影响其他会话与默认模型)"
        self.ui.push("info", text=f"本会话已切换到 {target.name} · {picked_name}{note}")
        self.ui.push("session_metrics", **session_metrics(
            runner.agent, runner.session, self.config, runner.llm_override))
        return {"ok": True}

    # ------------------------------------------------------------ 模型测试 / 拉取

    def _provider_credentials(self, provider_id: str, base_url: str = "",
                              api_key: str = "") -> tuple[str, str]:
        """取"要拿去请求的那个地址与密钥"。

        测试与拉取要用**界面上正在编辑的那份**:用户在表单里刚敲进地址和 Key、
        还没保存就点「测试」是最常见的用法 —— 只读磁盘配置的话,他会撞上
        "还没填接口地址"(而地址明明就在眼前)。所以:传了就用传的,没传才回落
        到已保存的配置。

        内置校园供应商是例外:它的地址与 Key 一律以代码为准,**前端传什么都不认**
        —— 否则"给界面传个自定义地址"就能把内置请求劫持到别处。
        """
        if provider_id == BUILTIN_PROVIDER_ID:
            saved = next((p for p in self.config.providers
                          if p.id == BUILTIN_PROVIDER_ID), None)
            if saved is not None:
                return saved.base_url, saved.api_key
            from minicode.config import BUILTIN_PROVIDER, _builtin_api_key
            return BUILTIN_PROVIDER.base_url, _builtin_api_key()
        saved = next((p for p in self.config.providers if p.id == provider_id), None)
        return (str(base_url or "").strip() or (saved.base_url if saved else ""),
                str(api_key or "").strip() or (saved.api_key if saved else ""))

    def test_model(self, provider_id: str, model: str = "", base_url: str = "",
                   api_key: str = "") -> dict:
        """「测试」按钮:真发一条最小请求,把连通/鉴权/窗口/视觉一次问清。

        结论会记进能力表(缓存 + 任务面板徽标):用户点过一次测试,面板上就该
        显示真实窗口,而不是继续写着"未知"。
        """
        name = str(model or "").strip()
        if not name:
            return {"ok": False, "msg": "还没填模型名"}
        base, key = self._provider_credentials(provider_id, base_url, api_key)
        try:
            result = model_probe.test_model(base, key, name)
        except Exception as exc:      # 测试按钮绝不能把窗口带崩
            return {"ok": False, "msg": f"测试失败:{type(exc).__name__}: {exc}"}
        if result.get("ok"):
            model_probe.remember(name, {
                "context_window": result.get("context_window"),
                "supports_vision": result.get("supports_vision"),
                "endpoint": model_probe.endpoint_fingerprint(base),
                "checked_at": time.time(),
            })
            for r in self._runners.values():      # 面板徽标吃新结论
                self.ui.push("session_metrics", session=r.key, **session_metrics(
                    r.agent, r.session, self.config, r.llm_override))
        return {**result, "model": name}

    def fetch_models(self, provider_id: str, base_url: str = "",
                     api_key: str = "") -> dict:
        """「从服务获取」:拉服务端的模型列表(GET /models)。

        比预设名单准得多 —— 各家模型名几个月换一茬,只有服务端自己知道现在有什么。
        """
        base, key = self._provider_credentials(provider_id, base_url, api_key)
        if not base:
            return {"ok": False, "msg": "还没填接口地址"}
        try:
            status, names = model_probe.list_models(base, key)
        except Exception as exc:
            return {"ok": False, "msg": f"读取失败:{type(exc).__name__}: {exc}"}
        if not names:
            if status == 200:
                return {"ok": False,
                        "msg": "服务端没返回模型列表(这个服务可能不实现 /models,手填模型名即可)"}
            return {"ok": False, "msg": model_probe.explain_status(status, "")}
        return {"ok": True, "models": names}

    # ------------------------------------------------------------ 思考档位

    def _active_model_name(self, runner: SessionRunner | None = None) -> str:
        """当前会话实际在用的模型名(会话级覆盖优先)。"""
        r = runner or self._current
        if r is not None and r.llm_override:
            return str(r.llm_override.get("model") or self.config.model)
        return self.config.model

    def _session_llm(self, runner: SessionRunner | None = None):
        """当前会话真正发请求的 LLMClient。"""
        r = runner or self._current
        return r.agent.llm if r is not None else None

    def _push_reasoning(self, runner: SessionRunner | None = None) -> None:
        """把思考能力快照推给前端(按钮显隐/档位列表/当前选择)。"""
        from minicode.reasoning import reasoning_snapshot
        r = runner or self._current
        if r is None:
            return
        llm = self._session_llm(r)
        snap = reasoning_snapshot(
            self._active_model_name(r),
            getattr(llm, "reasoning_choice", "default") if llm else "default")
        self.ui.push("reasoning", session=r.key, **snap)

    def get_reasoning(self) -> dict:
        """思考档位面板一屏数据:能力 + 当前选择(初始化/切模型后拉取)。"""
        from minicode.reasoning import reasoning_snapshot
        llm = self._session_llm()
        snap = reasoning_snapshot(
            self._active_model_name(),
            getattr(llm, "reasoning_choice", "default") if llm else "default")
        snap["ok"] = True
        return snap

    def set_reasoning_choice(self, choice: str) -> dict:
        """切换当前会话的思考档位,下一个请求立即生效(不影响其他会话)。

        与切模型一样走"重建会话 LLM 客户端"的路:LLMClient 是每会话一份的,
        重建只动 self._current.agent.llm,后台会话的思考档位不受影响。
        """
        from minicode.reasoning import get_capability, normalize_choice
        runner = self._current
        if runner is None:
            return {"ok": False, "msg": "没有活动会话"}
        if runner.busy:
            return {"ok": False, "msg": "任务进行中,等这轮跑完再切思考档位"}
        model = self._active_model_name(runner)
        cap = get_capability(model)
        if cap.kind == "hidden":
            return {"ok": False, "msg": f"模型 {model} 暂不支持思考档位设置"}
        if cap.kind == "fixed":
            return {"ok": False, "msg": f"模型 {model} 固定开启思考,不可调节"}
        choice = normalize_choice(model, choice)
        keep = getattr(runner.agent.llm, "reasoning_choice", "default")
        # 换回哪个供应商的凭据由 `LLMClient.scoped` 一处管(会话切模型同一套路)
        pid = (runner.llm_override["provider_id"] if runner.llm_override
               else (self.config.provider.id if self.config.provider else ""))
        runner.agent.llm = LLMClient.scoped(self.config, provider_id=pid)
        runner.agent.llm.reasoning_choice = choice
        if choice != keep:
            labels = {"default": "默认", "off": "关闭", "low": "低",
                      "high": "高", "max": "最高"}
            self.ui.push("info", text=f"思考档位:{labels.get(choice, choice)}"
                         f"({model},下一个请求生效)")
        self._push_reasoning(runner)
        return {"ok": True, "choice": choice}

    def get_session_metrics(self) -> dict:
        """任务面板一屏数据:模型/能力/消耗/上下文占用/可切列表。"""
        runner = self._current
        if runner is None:
            return {"ok": False, "msg": "没有活动会话"}
        m = session_metrics(runner.agent, runner.session, self.config,
                            runner.llm_override)
        m["ok"] = True
        return m

    def _persist_config(self) -> bool:
        """把配置写回 config.json;写不进去时说清原因,绝不静默丢改动。

        安装版被装进只读目录(以管理员身份安装 → Program Files)时,这里
        原本会抛 OSError:前端的 promise 直接 reject,用户只看到"操作失败",
        下次启动配好的 Key 又没了 —— 典型的"保存不了但也不知道为什么"。
        """
        try:
            save_config(self.config_path, self.config)
            return True
        except OSError as e:
            self.ui.push("info", text=(
                f"⚠ 配置没能写入 {self.config_path}(目录不可写?{type(e).__name__})。"
                "本次改动只在当前窗口有效,重启会丢失。"
                "建议改用绿色便携版(整个目录放 D 盘),或重装到用户目录。"))
            return False

    def _vision_on(self, runner: SessionRunner | None = None,
                   fallback_model: str = "") -> bool:
        """当前会话能不能真把图片编码进消息(base64 data URL)。

        v0.9.21 之前只看 `config.vision` 那个总开关 —— 而它**没有任何设置入口**,
        只有手改 config.json 才打得开,所以"选了视觉模型仍说不能看图"。现在按
        会话实际在用的模型查能力表(探测结果优先,静态目录兜底),配置里的开关
        退化成"人工强制打开"。
        """
        model = self._active_model_name(runner) or fallback_model
        try:
            from minicode.model_catalog import lookup
            capable = bool(lookup(model)["supports_vision"])
        except Exception:
            capable = False
        return capable or bool(self.config.vision)

    def set_vision(self, on: bool) -> dict:
        """强制开关:能力表没登记的自定义模型,用户知道它能看图时手动打开。

        只影响"要不要把图片压成 base64 塞进消息",不改模型本身;关掉后
        退回"只给文件路径"的老行为。
        """
        self.config.vision = bool(on)
        self._persist_config()
        for r in self._runners.values():
            self.ui.push("session_metrics", session=r.key, **session_metrics(
                r.agent, r.session, self.config, r.llm_override))
        self.ui.push("info", text="已" + ("开启" if on else "关闭")
                     + "强制发图:图片" + ("会编码进消息给模型看"
                                          if on else "按模型能力判定是否编码进消息"))
        return {"ok": True, "vision": self._vision_on(),
                "vision_forced": bool(self.config.vision)}

    def close_choice(self, action: str, remember: bool = False) -> dict:
        """关窗确认框的答复(见 minicode.tray)。"""
        from minicode import tray
        return tray.close_choice(action, bool(remember))

    def get_onboarding(self) -> dict:
        """首次运行向导该不该弹 + 三步内容 + 数据目录实况(见 minicode.onboarding)。"""
        from minicode import onboarding
        try:
            return onboarding.payload(key_configured=self.config.api_key_configured,
                                      paths=paths_diagnostics())
        except Exception as exc:                 # 向导是锦上添花,不能拖住启动
            return {"ok": False, "show": False, "steps": [],
                    "msg": f"读首次运行引导出错:{type(exc).__name__}"}

    def dismiss_onboarding(self, key: str = "first_run") -> dict:
        """「不再显示」:落盘偏好,写不进去要如实说(只读目录是踩过的坑)。"""
        from minicode import onboarding
        return onboarding.dismiss(str(key or "first_run"))

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
        content = (build_content(text, atts, self._vision_on(runner))
                   if atts else None)
        runner.thread = threading.Thread(target=self._run_task,
                                         args=(runner, text, content), daemon=True)
        runner.thread.start()
        return {"ok": True, "session": runner.key}

    def regenerate(self) -> dict:
        """「重新生成」:撤掉上一段回答,照**原样**重跑那句提问。

        与「编辑重发」的区别是这里没有新文本,所以走 `add_user=False`:那条提问原地不动,
        既不重复追加(模型会把同一句看到两遍,答出来的是"下一题"),也不掉当时的附件
        —— 拍错题那类提问重答一次,图还得在。
        点 👎 已经记进反馈日志的那条**不清除**:那是"这答得不好"的信号,差评回灌要用。
        """
        runner = self._current
        if runner.busy:
            return {"ok": False, "msg": "当前会话有任务正在进行中"}
        last = runner.session.last_user_message()
        if last is None:
            return {"ok": False, "msg": "还没有提问,没有可重答的回答"}
        if not runner.session.drop_last_answer():
            return {"ok": False, "msg": "最后一条就是提问本身,还没有答过的内容"}
        content = last.get("content")
        text = display_text(content)
        runner.thread = threading.Thread(
            target=self._run_task, args=(runner, text, content),
            kwargs={"add_user": False}, daemon=True)
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

    # ------------------------------------------------------------ 工作区产物目录
    #
    # 三类"只进不出"的东西:`uploads/`(拖进来/粘贴的附件原件)、`images/`(分享卡片与
    # 文生图)、`charts/`(成绩图/时长图)。它们都在**工作区**里,因此不在「我的数据」面板
    # 范围内(mydata 把工作区的东西排除在外) —— 于是没任何地方看得见大小,更没地方删。
    # 《使用说明》第 25 条那句"这两个目录里的东西要手动删"以前是句实话,现在有入口了。

    WORKSPACE_DIRS = ("uploads", "images", "charts")
    UPLOAD_LIST_CAP = 300      # 一次最多列/删多少个:堆到几千个时界面不该跟着卡

    def _workspace_dir(self, folder) -> Path:
        """把界面传来的目录名限死在这三个产物目录里,别的目录一律不给列、不给删。"""
        name = str(folder or "").strip()
        if name not in self.WORKSPACE_DIRS:
            raise ValueError(f"只认这几个工作区目录:{'/'.join(self.WORKSPACE_DIRS)}")
        return self.workspace / name

    def workspace_search_files(self, query: str = "", limit: int = 20) -> dict:
        """工作区内按名字搜文件(相对路径)—— 输入框 @ 引用补全的数据源。

        只读:只列名字,不碰内容;跳过依赖/产物目录(与 repo_outline 同一份名单)。
        """
        try:
            from minicode.tools import fs
            from minicode.tools.base import ToolContext
            hits = fs.search_files(ToolContext(workspace=self.workspace,
                                               config=self.config),
                                   query, limit)
        except Exception as e:
            return {"ok": False, "msg": f"{type(e).__name__}: {e}", "items": []}
        return {"ok": True, "items": hits}

    def workspace_files_list(self, folder: str = UPLOAD_DIR) -> dict:
        """工作区某个产物目录的清单:名字、多大、哪天进来,外加总共占多少。

        为什么要有这一条:附件有三个入口往里拷(📎 选文件、拖进来、剪贴板),分享卡片与
        图表也各有一个,而**没有任何地方看得见它们** —— 天天拖 20MB 课件的人,工作区在
        无声膨胀。这里给一个看得见也删得掉的地方。
        """
        try:
            base = self._workspace_dir(folder)
        except ValueError as e:      # 桥的边界不往外扔异常:两个方法同一套回法
            return {"ok": False, "msg": str(e), "items": [], "count": 0,
                    "total_bytes": 0, "shown": 0}
        if not base.is_dir():
            return {"ok": True, "items": [], "count": 0, "total_bytes": 0,
                    "shown": 0, "dir": str(folder)}
        files = [p for p in base.iterdir() if p.is_file()]
        total = sum(p.stat().st_size for p in files)
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return {"ok": True, "count": len(files), "total_bytes": total,
                "shown": min(len(files), self.UPLOAD_LIST_CAP), "dir": str(folder),
                "items": [{"name": p.name, "bytes": p.stat().st_size,
                           "mtime": time.strftime(
                               "%Y-%m-%d %H:%M",
                               time.localtime(p.stat().st_mtime))}
                          for p in files[:self.UPLOAD_LIST_CAP]]}

    def workspace_files_delete(self, names_json: str = "[]",
                               folder: str = UPLOAD_DIR) -> dict:
        """删掉选中的产物文件。

        **只接受文件名**:带路径分隔符、绝对路径、`..`、解析后不在该目录里的、不是真
        文件的,一律拒并且原样报回去 —— 前端来的字符串不可信,而这是"删除"。目录本身
        不删(要清的是文件);删掉的字节数如实报,不猜。
        """
        try:
            names = json.loads(names_json) if isinstance(names_json, str) else names_json
        except json.JSONDecodeError:
            return {"ok": False, "msg": "要删的文件名不是有效 JSON"}
        if not isinstance(names, list) or not names:
            return {"ok": False, "msg": "没有选中要删的文件"}
        try:
            base = self._workspace_dir(folder)
        except ValueError as e:
            return {"ok": False, "msg": str(e)}
        base_dir = base.resolve()
        deleted: list[str] = []
        refused: list[str] = []
        freed = 0
        for raw in names[:self.UPLOAD_LIST_CAP]:
            name = str(raw if raw is not None else "").strip()
            if (not name or Path(name).is_absolute() or Path(name).name != name
                    or name.startswith(".")):
                refused.append(name[:60] or "(空)")
                continue
            target = base / name
            try:
                real = target.resolve()
            except OSError:
                refused.append(name)
                continue
            if real.parent != base_dir or not real.is_file():
                refused.append(name)       # 逃出该目录的、软链到别处的、目录,都算拒
                continue
            try:
                size = real.stat().st_size
                real.unlink()
            except OSError:
                refused.append(name)
                continue
            deleted.append(name)
            freed += size
        return {"ok": bool(deleted), "deleted": deleted, "refused": refused,
                "freed_bytes": freed, "dir": str(folder),
                "msg": (f"删掉 {len(deleted)} 个,腾出 {freed} 字节"
                        + (f";{len(refused)} 个没删(不在 {folder}/ 里或不是文件)"
                           if refused else ""))}

    def _attachment_item(self, att: Attachment) -> dict:
        item = att.to_dict()
        if att.kind == "image":
            item["preview"] = preview_data_url(self.workspace / att.path)
        return item

    # 原图查看的字节上限:base64 要过一遍桥(再好的机器,几十 MB 的 JSON 也会卡
    # 界面一下)。手机照片/截图都在几 MB 量级,25 MB 基本罩得住真实使用;
    # 超了就说人话拒掉,前端留在预览帧,绝不静默给一张压过的冒充原图。
    ATTACHMENT_FULL_MAX_BYTES = 25 * 1024 * 1024

    def attachment_full(self, path: str) -> dict:
        """灯箱打开时按需取**原图**的 data URL(v0.9.97)。

        消息清单里附件只带 320px 的预览帧(attachments.preview_data_url),
        放大就糊;但原图不该跟着每一条消息反复过桥 —— 所以走按需:前端点开
        灯箱才来要这一次。返回的是**图片内容**(data URL),不是本地路径 ——
        `Attachment.to_dict` 的口径是 local 绝对路径不出桥,这里不破例。
        只收工作区内真实存在的图片文件,与 _parse_attachments 同一条边界。
        """
        rel = str(path or "").strip()
        kind, p = classify_path(self.workspace, rel)
        if kind != "inside" or not p.is_file():
            return {"ok": False, "msg": "文件不在工作区,原图看不了"}
        if not is_image(p):
            return {"ok": False, "msg": "不是图片文件"}
        size = p.stat().st_size
        if size > self.ATTACHMENT_FULL_MAX_BYTES:
            return {"ok": False,
                    "msg": f"原图 {size / 1048576:.0f} MB,超过查看上限 "
                           f"{self.ATTACHMENT_FULL_MAX_BYTES // 1048576} MB"}
        try:
            data = p.read_bytes()
        except OSError as e:
            return {"ok": False, "msg": f"读不出来:{e}"}
        return {"ok": True, "data": data_url(data, mime_for(p))}

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
        self._persist_config()
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
            "strip_env_secrets": state.policy.strip_env_secrets,
            "description": state.describe(),
        }

    # ------------------------------------------------------------ 长期记忆

    def remember(self, text: str, scope: str = "project") -> dict:
        """界面里「记一条长期记忆」—— 走的是 CLI `/remember` 同一个 `save_entry`。

        为什么要给界面开这个口:以前只有命令行能主动记一条(门面这边只有 `consolidate_memory`
        整理),同学想说"记住我讨厌表格"就只能打字让模型自己决定要不要存。
        两条边界:①**空内容不存**(存进去是一条没用的行,还会占掉去重的位置);
        ②归属只有 project/user 两种,写错就报错,不猜成"两级都存"。
        """
        got = str(text or "").strip()
        if not got:
            return {"ok": False, "msg": "没内容可记 —— 写一句再存。"}
        sc = (scope or "project").strip().lower()
        if sc not in ("project", "user"):
            return {"ok": False,
                    "msg": f"未知归属「{sc}」,可选:project(本项目)/ user(用户级)"}
        msg = save_entry(self.workspace, sc, got, sc)
        failed = msg.startswith(("错误", "工具执行出错"))
        if not failed:
            self.ui.push("info", text=msg)
        return {"ok": not failed, "msg": msg, **self._memory_counts()}

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

    def edit_resend(self, ordinal: int, new_text: str, expect: str = "",
                    revert_files: bool = False) -> dict:
        """编辑重发:截断到第 ordinal 条用户消息(含),校验原文一致后以新文本重发。

        expect 传前端记录的原消息文本,防止上下文压缩/变更后序号错位截断到别的消息。

        `revert_files`(R3,v0.9.103)= 一起把「第 N 问之后学长改过的文件」退回。
        默认 False:退文件是动手面,只有前端已经用 appConfirm 点过头才传 True。
        退的动作在**截断之前**做 —— 截断会把回合坐标改掉,先截后方就说不清退的是哪几问。
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
        reverted = []
        if revert_files:
            # 先退文件再截对话:截断会改掉"第几问"这个坐标,反过来做就说不清退的是哪几问。
            back = self.agent.rollback_to_ordinal(int(ordinal))
            reverted = back.get("reverted") or []
            self.ui.push("info", text=back.get("msg") or "")
            if not back.get("ok") and back.get("stale"):
                # 号对不上时**不截断也不重发**:那是同一枚硬币的两面,退不了就别假装退过了
                return {"ok": False, "msg": back.get("msg") or "工作区没能退回,编辑重发先不做了"}
        self.session.replace_messages(self.session.messages[:idx])
        out = self.send_task(new_text)
        if isinstance(out, dict) and reverted:
            out["reverted"] = reverted
        return out

    def fork_session(self, ordinal: int) -> dict:
        """会话分支(C4,v0.9.102):保留到第 ordinal 问(含),从这里开一份新会话。

        原会话**原样保留** —— 分叉不是截断,是"从这一刻换个思路再走一遍"。
        行级复制留档(原消息的 ts 原样),再按 resume 同一条路建运行器接上。
        """
        if self._busy:
            return {"ok": False, "msg": "有任务正在进行中,分叉要等它停"}
        src = self.session.log_path
        if src is None:
            return {"ok": False, "msg": "当前会话还没有留档(先说一句话)"}
        idx, _content = self._find_user_message(int(ordinal))
        if idx < 0:
            return {"ok": False, "msg": f"第 {ordinal} 问已不在当前上下文(可能已被压缩)"}
        from minicode.session import fork_log_file
        got = fork_log_file(src, idx + 1, self.sessions_dir, self.workspace)
        if got is None:
            return {"ok": False, "msg": "分叉失败:源留档读不到"}
        new_path, kept = got
        runner = self.open_session(load_path=new_path)
        key = runner.key
        self.ui.push("cleared", session=key)
        self.ui.push("resumed", session=key, messages=self._history_for_frontend())
        self.ui.push("info", session=key,
                     text=f"已分叉出新会话:保留到第 {kept} 问(原会话原样保留在侧栏)。"
                          f"从现在起两边各走各的。")
        self._push_sessions()
        return {"ok": True, "session": key, "file": new_path.name}

    def _find_user_message(self, ordinal: int) -> tuple[int, str]:
        """第 ordinal 条用户消息的 (下标, 内容)。数法在 `session.user_message_at`
        里只有一份 —— 编辑重发、分叉、CLI 的 /rewind 共用它,不各数一遍。"""
        idx, msg = user_message_at(self.session.messages, ordinal)
        return idx, (display_text(msg.get("content")) if msg else "")

    def get_suggestions(self) -> dict:
        """首页两行 chips:「试试这些」按近期聊天打分,「接着做」读计划与待办的真状态。

        第二行是 v0.9.42 补的:数据源里本来躺着任务状态(`plans/plan.md` 的调度视图、
        今天到期的待办),而首页只会问"你想了解什么"。打开应用第一眼看到的应该是
        "有什么接着做"。两样都没有时那一行整行不出 —— 读不到状态也绝不编一条出来。
        """
        from minicode import planning, reminders
        texts = collect_user_texts(self.sessions_dir, self.session.messages)
        out = {"suggestions": build_suggestions(texts), "continue": []}
        try:
            out["continue"] = continue_chips(
                planning.plan_state(self.workspace), reminders.due_soon())
        except Exception:
            # 计划文件或待办文件坏了,首页照样起得来,只是少一行真状态
            pass
        return out

    def share_image(self, text: str = "") -> dict:
        """把某段回答排成一张能发群的卡片图,落到工作区 `images/`。

        前端只给文字:目录与文件名一律后端定、同名不覆盖 —— 路径不能由界面传进来。
        失败回 `ok=False` 带真原因(没有 Pillow / 中文字体读不动 / 文字为空 /
        超上限 / 写盘报错),那句话要原样显示给用户,不能缩成"生成失败"三个字。
        """
        from minicode import images
        try:
            got = images.save_share_card(self.workspace, text)
        except Exception as e:      # 出图的意外不该掀翻桥线程
            return {"ok": False, "msg": f"生成卡片时出了 {type(e).__name__}，没有写任何文件"}
        if got.get("ok"):
            bits = [f"{got.get('bytes', 0):,} 字节"]
            if got.get("truncated"):
                bits.append("内容较长，卡片只排了一部分")
            srcs = got.get("sources") or []
            bits.append(f"来源 {len(srcs)} 条" if srcs else "这段回答里没有来源链接")
            got["msg"] = f"已生成分享图：{got.get('path')}（" + "；".join(bits) + "）"
        return got

    def rate_message(self, rating: int, question: str = "", answer: str = "") -> dict:
        """消息级 👍/👎 反馈:追加到应用目录 message-feedback.jsonl。

        记录失败不影响使用(返回 ok=False 由前端静默处理);同一消息重复评分
        由前端置灰防住,后端不再去重。session 记录的是留档文件名(还没落盘的
        新会话用运行器键兜底),便于回溯当时是在哪个会话里答错的。
        """
        from minicode import growth
        log = ""
        try:
            log = self.session.log_path.name or ""
        except Exception:
            pass
        if not log and self._current is not None:
            log = self._current.key
        # 把这次回答引用过的知识库条目绑上(v0.9.80):取"最后一条用户消息
        # 之后"的检索命中窗口(检索发生在回答之前,ts 是消息级真实时刻)。
        # 取不到(老留档没 ts / 当轮没检索)就不绑 —— 宁可不记,不硬凑归因。
        kb_ids: list[str] = []
        try:
            last_user_ts = next(
                (str(m.get("ts") or "") for m in reversed(self.session.messages)
                 if isinstance(m, dict) and m.get("role") == "user"), "")
            kb_ids = growth.last_hits_since(last_user_ts)
        except Exception:
            kb_ids = []
        if not growth.log_feedback(int(rating), question=question,
                                   answer=answer, session=log, kb_ids=kb_ids):
            return {"ok": False, "msg": "反馈没有记录成功(不影响使用)"}
        return {"ok": True, "kb_ids": kb_ids}

    def get_countdowns(self) -> dict:
        """首页倒计时 chips:校历里最近的几条安排(没有就不显示)。"""
        from minicode import calendar as campus_calendar
        try:
            items = campus_calendar.calendar_payload(limit=3)
        except Exception:             # 校历文件坏了也不能拖垮首页
            items = []
        return {"ok": True, "countdowns": items}

    def get_weather_card(self) -> dict:
        """首页天气 chip(肇庆·鼎湖,缓存 30 分钟;断网时 card=None 让前端隐掉)。"""
        from minicode import weather
        try:
            return {"ok": True, "card": weather.weather_card()}
        except Exception:
            return {"ok": False}

    def get_schedule_view(self) -> dict:
        """课表面板数据:今日/明日/本周网格(本机 schedule.json;没登记时 registered=False)。"""
        from minicode import schedule
        try:
            return {"ok": True, "schedule": schedule.view()}
        except Exception:
            return {"ok": False}

    def schedule_save_times(self, rows_json: str = "[]") -> dict:
        """课表面板「填作息表」:把每节起止存进本机 `period-times.json`。

        为什么值得给面板一个表单:教务处那张作息表同学手上有,但为了"以后问空档
        能带钟点"专门开一轮对话贴一遍,多数人不会做。填一次,面板和
        `schedule(action=free)` 同时升级成钟点口径;**传空数组就是清掉**,
        空档退回只说第几节。绝不替他生成"常见作息"。
        """
        import json
        from minicode import schedule
        try:
            rows = json.loads(rows_json or "[]")
        except (TypeError, ValueError):
            return {"ok": False, "msg": "作息表没读明白,本机文件保持原样。",
                    "schedule": None}
        if not isinstance(rows, list):
            return {"ok": False, "msg": "作息表格式不对(要每节一条起止),没写文件。",
                    "schedule": None}
        ok, msg = schedule.save_period_times(rows)
        try:
            view = schedule.view()
        except Exception:
            view = None
        return {"ok": ok, "msg": msg, "schedule": view}

    # ------------------------------------------- 教务课表导入(v0.9.23 三步向导)

    def _jw_flow(self):
        """整程共用一个 ImportFlow 实例(它自己不持有连接,只按 URL 认领页面)。"""
        from minicode import schedule_import
        flow = getattr(self, "_jw_import_flow", None)
        if flow is None:
            flow = schedule_import.ImportFlow()
            self._jw_import_flow = flow
        return flow

    def schedule_import_open(self) -> dict:
        """第 1 步:把教务登录页开在**看得见**的窗口里。登录是人的事,学长不碰。"""
        self._jw_result = None
        try:
            return self._jw_flow().open_login()
        except Exception as exc:                      # 桥不能让前端等一个不回来的 promise
            return {"ok": False, "stage": "failed",
                    "msg": f"学长这边出错了:{type(exc).__name__}: {exc}"}

    def schedule_import_read(self) -> dict:
        """第 2 步:读那张教务页的学期课表并确定性解析。**不落盘**。"""
        self._jw_result = None
        try:
            r = self._jw_flow().read()
        except Exception as exc:
            return {"ok": False, "stage": "failed",
                    "msg": f"读课表时出错:{type(exc).__name__}: {exc}"}
        if r.get("ok"):
            self._jw_result = [dict(c) for c in r.get("classes") or []]
        return r

    def schedule_import_model(self) -> dict:
        """第 2 步的退路:页面文字交给当前模型整理(会真的发出这段文字)。"""
        self._jw_result = None
        llm = self._session_llm() or self._shared_llm
        if llm is None:
            return {"ok": False, "stage": "failed",
                    "msg": "还没有可用的模型配置:先在设置里填好模型和密钥再试。"}
        try:
            r = self._jw_flow().read_with_model(llm.complete)
        except Exception as exc:
            return {"ok": False, "stage": "failed",
                    "msg": f"模型整理课表时出错:{type(exc).__name__}: {exc}"}
        if r.get("ok"):
            self._jw_result = [dict(c) for c in r.get("classes") or []]
        return r

    def schedule_import_confirm(self, term_start: str = "") -> dict:
        """第 3 步:用户点头之后才真的写 schedule.json。

        term_start(第 1 周周一)教务页上**没有**,只能问人 —— 所以确认页必须有这一栏。
        """
        from minicode import schedule
        classes = getattr(self, "_jw_result", None) or []
        if not classes:
            return {"ok": False,
                    "msg": "手上还没有读到课表:先点「读取课表」,读到了再确认导入。"}
        ok, msg = schedule.save(str(term_start or "").strip(), classes)
        if ok:
            self._jw_result = None
        try:
            view = schedule.view()
        except Exception:
            view = None
        return {"ok": ok, "msg": msg, "schedule": view}

    def schedule_import_clear(self) -> dict:
        """收尾:关掉导入用的浏览器窗口并清掉它的登录状态(独立 profile)。"""
        from minicode import schedule_import
        self._jw_result = None
        try:
            return schedule_import.clear_login_state()
        except Exception as exc:
            return {"ok": False, "msg": f"清理登录态失败:{type(exc).__name__}: {exc}"}

    # ------------------------------------------- 教务成绩导入(v0.9.59 H 件)
    # 三步与课表导入同构,差别只在:读的是成绩表(表头认列)、确认不需要补填
    # 学期起点、落盘的是 scores.json。同一个浏览器窗口可以两用 ——
    # 登录态在同一个 profile 里,登录一次课表和成绩都能读。

    def _scores_flow(self):
        """整程共用一个 ScoresFlow(同 _jw_flow 的道理:标签页属于开它的那条线程)。"""
        from minicode import scores_import
        flow = getattr(self, "_scores_import_flow", None)
        if flow is None:
            flow = scores_import.ScoresFlow()
            self._scores_import_flow = flow
        return flow

    def scores_import_open(self) -> dict:
        """成绩导入第 1 步:打开看得见的登录页(登录由人做,学长不碰)。"""
        self._scores_result = None
        try:
            return self._scores_flow().open_login()
        except Exception as exc:
            return {"ok": False, "stage": "failed",
                    "msg": f"学长这边出错了:{type(exc).__name__}: {exc}"}

    def scores_import_read(self) -> dict:
        """成绩导入第 2 步:读当前那张教务页的成绩表(表头认列)。**不落盘**。"""
        self._scores_result = None
        try:
            r = self._scores_flow().read()
        except Exception as exc:
            return {"ok": False, "stage": "failed",
                    "msg": f"读成绩时出错:{type(exc).__name__}: {exc}"}
        if r.get("ok"):
            self._scores_result = [dict(x) for x in r.get("rows") or []]
        return r

    def scores_import_model(self) -> dict:
        """成绩导入第 2 步的退路:页面文字交给当前模型整理(会真的发出)。"""
        self._scores_result = None
        llm = self._session_llm() or self._shared_llm
        if llm is None:
            return {"ok": False, "stage": "failed",
                    "msg": "还没有可用的模型配置:先在设置里填好模型和密钥再试。"}
        try:
            r = self._scores_flow().read_with_model(llm.complete)
        except Exception as exc:
            return {"ok": False, "stage": "failed",
                    "msg": f"模型整理成绩时出错:{type(exc).__name__}: {exc}"}
        if r.get("ok"):
            self._scores_result = [dict(x) for x in r.get("rows") or []]
        return r

    def scores_import_confirm(self) -> dict:
        """成绩导入第 3 步:用户点头之后才真的写 scores.json(整表覆盖)。"""
        from minicode import scores as _scores
        rows = getattr(self, "_scores_result", None) or []
        if not rows:
            return {"ok": False,
                    "msg": "手上还没有读到成绩:先点「读取成绩」,读到了再确认导入。"}
        ok, msg = _scores.save(rows)
        if ok:
            self._scores_result = None
        try:
            view = _scores.view()
        except Exception:
            view = None
        return {"ok": ok, "msg": msg, "scores": view}

    def scores_import_clear(self) -> dict:
        """收尾:关掉导入用的浏览器窗口并清掉那份独立 profile 的登录态。"""
        from minicode import schedule_import
        self._scores_result = None
        try:
            return schedule_import.clear_login_state()
        except Exception as exc:
            return {"ok": False, "msg": f"清理登录态失败:{type(exc).__name__}: {exc}"}

    def schedule_undo(self) -> dict:
        """课表换回上一版(save/clear 时留的那份备份)。

        为什么给按钮而不是只让模型做:导入向导第 ③ 步写着"覆盖",
        同学点完才发现读错了周次 —— 这时候他要的是**一键回去**,
        不是再去输入框里打一句话。
        """
        from minicode import schedule
        ok, msg = schedule.undo()
        try:
            view = schedule.view()
        except Exception:
            view = None
        return {"ok": ok, "msg": msg, "schedule": view}

    # ------------------------------------------------------------ 标题栏(p2)

    def _chrome_window(self):
        return getattr(self.ui, "window", None)

    def _apply_chrome(self, dark: bool | None = None) -> dict:
        """把标题栏染成与界面同一套色,并把字换成当前工作区名。

        标题用的就是顶栏那颗 📁 胶囊同一份口径(`get_info()["workspace_name"]`
        走的就是 `ws.display_name`),两边写两个名字迟早会对不上;
        主题没报过来之前只改字、不猜颜色。
        """
        from minicode import window_chrome
        if dark is not None:
            self._chrome_dark = bool(dark)
        return window_chrome.apply(
            self._chrome_window(), dark=dark,
            title=ws.display_name(self.workspace, self.app_dir))

    def set_chrome(self, dark: bool) -> dict:
        """前端切主题(含开机那一次)时报给本机,让系统标题栏跟着变。"""
        return self._apply_chrome(bool(dark))

    def read_clipboard(self) -> dict:
        """输入框右键菜单「粘贴」:本机直接读剪贴板,不弹浏览器那条授权横条。

        返回 {ok, source, text, files, msg};files 已是 [{name, b64}],前端可以
        原样交给下面那个 add_dropped_attachments,不用再经 FileReader 绕一圈。
        """
        from minicode import clipboard
        return clipboard.read_clipboard()

    def add_dropped_attachments(self, files_json: str = "[]") -> dict:
        """把拖进对话框的文件落进工作区 uploads/(与点 📎 选文件同一条路径)。

        前端用 FileReader 读到字节后传 [{name, b64}, ...]:WebView2 里 JS 拿不到
        本地绝对路径,走字节最稳。复制/上限/图片判定全部复用 save_dropped_bytes。
        """
        import base64
        try:
            raw = json.loads(files_json) if isinstance(files_json, str) else files_json
        except (json.JSONDecodeError, TypeError):
            return {"ok": False, "msg": "拖拽附件参数不合法"}
        items = [x for x in (raw or []) if isinstance(x, dict)]
        if not items:
            return {"ok": True, "items": [], "errors": []}
        out, errors = [], []
        for it in items:
            name = str(it.get("name") or "").strip()
            try:
                data = base64.b64decode(str(it.get("b64") or ""), validate=True)
                att = save_dropped_bytes(self.workspace, name, data)
            except (ValueError, OSError) as e:
                errors.append(f"{name or '文件'}: {e}")
                continue
            out.append(self._attachment_item(att))
        return {"ok": True, "items": out, "errors": errors}

    def set_notice_watch(self, enabled: bool) -> dict:
        """主动推荐:开/关官网新通知看门狗(落在 notice-watch.json)。"""
        from minicode import proactive
        state = proactive.load_state()
        state["enabled"] = bool(enabled)
        proactive.save_state(state)
        self.ui.push("info", text="已开启官网新通知提醒" if enabled
                     else "已关闭官网新通知提醒(随时可在 config 同目录的 "
                          "notice-watch.json 里重新打开)")
        return {"ok": True, "enabled": state["enabled"]}

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

    def undo_records(self) -> dict:
        """「本轮改动」清单 —— 这一屏读的就是 agent 那本撤销栈,不开第三本账。

        栈被 /clear、换会话、压缩历史清空时它自然为空,前端那句"没有可退的改动"
        于是是真话,而不是界面自己缓存了一份看起来还能退的旧清单。
        """
        try:
            return self.agent.undo_records()
        except Exception as exc:      # 清单读坏了不该让整栏打不开
            return {"ok": False, "items": [], "total": 0, "more": 0, "note": "",
                    "hint": f"读取本轮改动失败:{type(exc).__name__}: {exc}"}

    def undo_step(self, seq=None) -> dict:
        """只回退清单上的那一处(其余原样留着)。前端已弹过确认框,这里动手。"""
        msg = self.agent.undo(seq)
        self.ui.push("info", text=msg)
        return {"ok": msg.startswith("已撤销"), "msg": msg}

    def undo_diff(self, seq=None) -> dict:
        """某一处的 diff(R2):前像对磁盘现值,**算都在后端算**。

        刻意不在 `get_activity` 里顺带算 —— 那等于每次刷新都把所有改过的文件重读一遍;
        这一处是点开才要的那一份,按需取。
        """
        try:
            return self.agent.undo_diff(seq)
        except Exception as exc:
            return {"ok": False, "msg": f"读不出这一处的 diff:{type(exc).__name__}: {exc}"}

    def undo_all(self) -> dict:
        """一路退回本轮开始前。命令(bash/py_script)改的不在账里,退不到 —— 这话在后端那句回话里。"""
        msg = self.agent.undo_all()
        self.ui.push("info", text=msg)
        return {"ok": msg.startswith("已全部回退"), "msg": msg}

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
        """立即压缩上下文(上下文窗口面板的「压缩」按钮)。

        压缩是真的:agent 会把早期对话交给模型摘编(消耗一次 LLM 调用),
        所以任务进行中直接拒绝 —— 摘编读的就是那些消息,边跑边压会撕裂回合。
        压缩完立即回推一次 session_metrics,面板上的占用数字马上掉下来。

        返回里带上压前/压后两个占用数,以及**这次到底压掉没有**:按钮按下却不回话,
        用户读成"它坏了",而真实情况多半是"没有可压缩的早期对话"(原来这条一律
        返回 ok,压没压只有 agent 自己知道)。
        """
        if self._busy:
            return {"ok": False, "msg": "任务进行中,等这轮跑完再压缩上下文"}
        before = int(self.agent.context_tokens())
        done = bool(self.agent.compact_now())
        after = int(self.agent.context_tokens())
        runner = self._current
        if runner is not None:
            self.ui.push("session_metrics", session=runner.key, **session_metrics(
                runner.agent, runner.session, self.config, runner.llm_override))
        if not done:
            return {"ok": False, "before": before, "after": after,
                    "msg": f"没压掉东西:早期对话还不够多(或这次摘编没做成),"
                           f"占用仍是约 {after} tokens"}
        return {"ok": True, "before": before, "after": after,
                "saved": max(0, before - after),
                "msg": f"已压缩:占用约 {before} → {after} tokens"}

    def set_permission_mode(self, mode: str) -> dict:
        """切换权限档位(confirm/accept_edits/accept_all/full),写回 config.json。

        档位语义见 minicode/permission.py 模块头;未知档位拒绝,不静默归档。
        """
        from minicode.permission import MODE_LABELS, PERMISSION_MODES
        if mode in PERMISSION_MODES:
            self.config.permission_mode = mode
            self._persist_config()
            self.ui.push("info",
                         text=f"权限模式已切换为「{MODE_LABELS[mode]}」({mode})")
            return {"ok": True, "permission_mode": mode}
        return {"ok": False, "msg": "未知权限模式"}

    def refresh_sessions(self) -> dict:
        self._push_sessions()
        return {"ok": True}

    def delete_session(self, index) -> dict:
        """删除一条历史会话留档;若该会话正在后台跑,先停掉它。

        index 同 resume_session:留档文件名(首选)或序号(兼容)。
        """
        chosen, _summaries = self._resolve_session_summary(index)
        if chosen is None:
            return {"ok": False, "msg": "会话不存在"}
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

    def reveal_file(self, path: str) -> dict:
        """在系统文件管理器里**定位**一个文件(活动面板「打开所在文件夹」)。

        只做定位,不双击打开文件 —— 该用什么软件打开是用户的事,替他猜只会弹出
        一个他没打算开的窗口。路径过一遍沙箱分级:敏感目录(BLOCKED)与工作区内的
        凭据文件(SECRET)都不给定位,免得一个"顺手看看"的按钮把人带到 key 面前。
        """
        import subprocess
        import sys
        from minicode.sandbox import BLOCKED, SECRET, classify_path
        raw = (path or "").strip()
        if not raw:
            return {"ok": False, "msg": "没说要打开哪个文件"}
        if sys.platform != "win32":
            return {"ok": False, "msg": "定位文件目前只在 Windows 版可用"}
        grade, p = classify_path(self.workspace, raw, home=Path.home())
        if grade == BLOCKED:
            return {"ok": False, "msg": "这个位置不在可打开的范围内"}
        if grade == SECRET:
            return {"ok": False, "msg": "凭据类文件不在文件管理器里替你定位"}
        try:
            if not p.exists():
                return {"ok": False, "msg": f"这个文件已经不在了:{p.name}"}
        except OSError as exc:
            return {"ok": False, "msg": f"读不到这个路径:{exc}"}
        # /select, 对文件和目录都适用(目录会被它在上一级里选中),不必分两支
        subprocess.Popen(["explorer", "/select,", str(p)])
        return {"ok": True, "msg": f"已在文件管理器里定位:{p.name}"}

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

    def _resolve_session_summary(self, target):
        """按**留档文件名**(str,首选)或序号(int,兼容)定位一条会话。

        返回 (SessionSummary | None, summaries)。前端点侧栏时回传文件名而非
        序号 —— 渲染到点击之间若有新会话落盘,序号会错位到另一条(路线图 #11),
        文件名是稳定的。只拿输入比对 basename,绝不据它拼路径(防目录穿越)。
        """
        summaries = list_sessions(self.sessions_dir)
        if isinstance(target, str):
            name = target.strip().replace("\\", "/").rsplit("/", 1)[-1]
            if not name:
                return None, summaries
            for s in summaries:
                if s.path.name == name:
                    return s, summaries
            return None, summaries
        try:
            idx = int(target)
        except (TypeError, ValueError):
            return None, summaries
        if 1 <= idx <= len(summaries):
            return summaries[idx - 1], summaries
        return None, summaries

    def resume_session(self, index) -> dict:
        """切换到某个会话:它正在后台跑就直接接上,否则从留档恢复。

        以前这里会先打断当前任务,现在切换就是纯粹"换视角"。
        index 可以是**留档文件名**(前端首选,稳定)或序号(int,兼容旧调用):
        渲染到点击之间若有新会话落盘,序号会错位到另一条,文件名不会。
        """
        chosen, _summaries = self._resolve_session_summary(index)
        if chosen is None:
            return {"ok": False, "msg": "会话不存在"}
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

    # ------------------------------------------------------------ 会话导出 / 搜索

    def export_session(self) -> dict:
        """把当前会话导出成 Markdown(先弹"另存为")。

        只导出用户与助手的正文:工具往返不进文件,那是过程噪音,发给同学时
        只会把重点淹没。没有窗口(网页版/测试)时落到工作区 exports/。
        """
        runner = self._current
        if runner is None:
            return {"ok": False, "msg": "没有活动会话"}
        if len(runner.session.messages) <= 1:
            return {"ok": False, "msg": "这个会话还没有对话内容可导出"}
        body = session_to_markdown(runner.session)
        name = session_export_name(runner.session)

        window = getattr(self.ui, "window", None)
        if window is not None:
            target = self._ask_save_path(name)
            if target is None:
                return {"ok": False, "msg": "已取消导出"}
        else:
            out_dir = Path(self.workspace) / "exports"
            try:
                out_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                return {"ok": False, "msg": f"创建导出目录失败:{exc}"}
            target = out_dir / name
        try:
            Path(target).write_text(body, encoding="utf-8")
        except OSError as exc:
            return {"ok": False, "msg": f"写入失败:{exc}"}
        self.ui.push("info", text=f"会话已导出:{target}")
        return {"ok": True, "path": str(target), "name": Path(target).name}

    def _ask_save_path(self, default_name: str, file_types: tuple =
                       ("Markdown 文件 (*.md)", "所有文件 (*.*)")):
        """弹系统"另存为"框;窗口没就绪、或用户取消,都返回 None。"""
        window = getattr(self.ui, "window", None)
        if window is None:
            return None
        try:
            import webview
            picked = window.create_file_dialog(
                webview.SAVE_DIALOG, save_filename=default_name,
                file_types=file_types)
        except Exception:
            return None
        if isinstance(picked, (list, tuple)):
            picked = picked[0] if picked else None
        return Path(picked) if picked else None

    def _ask_open_path(self, file_types: tuple =
                       ("迁移包 (*.zip)", "所有文件 (*.*)")) -> Path | None:
        """弹系统单选文件框;取消或窗口没就绪返回 None(不报错、不写任何东西)。"""
        window = getattr(self.ui, "window", None)
        if window is None:
            return None
        try:
            import webview
            picked = window.create_file_dialog(webview.OPEN_DIALOG, file_types=file_types)
        except Exception:
            return None
        if isinstance(picked, (list, tuple)):
            picked = picked[0] if picked else None
        return Path(picked) if picked else None

    # ------------------------------------------------------------ 我的数据 / 迁移包
    #
    # 这一节只做三件事:把面板要的数据交给前端、把"存到哪/读哪个"交给**系统对话框**、
    # 把真正的打包/落盘交给 mydata 模块。路径**绝不从前端字符串拿**(那是 zip-slip 的
    # 入口),前端只能拿到 nonce。

    def mydata_inventory(self) -> dict:
        """「我的数据」面板 payload:数据目录在哪 + 每类有哪些文件。"""
        from minicode import mydata
        return mydata.inventory(workspace=str(self.workspace))

    def mydata_export(self, include_sessions=True) -> dict:
        """导出迁移包:先弹"另存为",再交给 mydata 打包。

        `include_sessions` 前端可能传成字符串("false"),按字符串判一次,
        别把用户"不要带聊天记录"的勾选当成真。
        """
        from minicode import mydata
        if isinstance(include_sessions, str):
            include_sessions = include_sessions.strip().lower() not in (
                "false", "0", "off", "no", "")
        window = getattr(self.ui, "window", None)
        if window is None:
            return {"ok": False, "msg": "窗口未就绪,无法另存迁移包"}
        import time as _time
        target = self._ask_save_path(
            f"引路学长迁移包-{_time.strftime('%Y%m%d-%H%M')}.zip",
            file_types=("迁移包 (*.zip)", "所有文件 (*.*)"))
        if target is None:
            return {"ok": False, "msg": "已取消导出"}
        result = mydata.export_package(target, include_sessions=bool(include_sessions))
        if result.get("ok"):
            self.ui.push("info", text=f"迁移包已导出:{result['path']}"
                                      f"({result['files']} 个文件,{result['size']})")
        return result

    def mydata_import_pick(self) -> dict:
        """导入第 1 步:选包 + 逐条预览(写不写、保不保留本机,这一步就已定死)。"""
        from minicode import mydata
        window = getattr(self.ui, "window", None)
        if window is None:
            return {"ok": False, "msg": "窗口未就绪,无法选择迁移包"}
        source = self._ask_open_path()
        if source is None:
            return {"ok": False, "msg": "已取消导入"}
        return mydata.import_pick(source)

    def mydata_import_apply(self, nonce: str = "") -> dict:
        """导入第 2 步:按预览确认落盘;替换过的原件先进 migrate-backup。"""
        from minicode import mydata
        result = mydata.import_apply(str(nonce or ""))
        if result.get("ok"):
            tail = ";重启学长后生效" if result.get("restart_needed") else ""
            self.ui.push("info", text=(
                f"迁移包已导入:新增 {result['written']} 项、更新 {result['replaced']} 项、"
                f"本机更新保留 {result['kept_newer']} 项、跳过 {result['blocked']} 项"
                + tail))
        return result

    def mydata_forget_import(self, nonce: str = "") -> dict:
        """用户看了预览说"算了":把这次预览丢掉。"""
        from minicode import mydata
        mydata.forget_preview(str(nonce or ""))
        return {"ok": True}

    def mydata_open_dir(self) -> dict:
        """在资源管理器里打开数据目录(只开目录,不开任何数据文件)。"""
        import os
        from minicode import paths
        target = paths.app_path()
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return {"ok": False, "msg": f"打不开数据目录:{e}"}
        try:
            os.startfile(str(target))       # type: ignore[attr-defined]
        except AttributeError:
            return {"ok": False, "msg": "这个平台没有资源管理器可打开,路径见面板顶部"}
        except OSError as e:
            return {"ok": False, "msg": f"打不开数据目录:{type(e).__name__}"}
        return {"ok": True, "path": str(target)}

    # ------------------------------------------------------------ 情绪日记(v0.9.32)
    #
    # 面板要什么就给什么,一律**现读现给**:桥里不缓存任何一条情绪记录,
    # 关掉面板这台进程里就没有第二份副本。删除只有面板上那一下点击能触发。

    def mood_review(self, days: int = 7) -> dict:
        """情绪面板 payload:最近一周的回顾文本 + 条数(只回读,不写)。"""
        from minicode import mood as _mood
        try:
            n = max(1, int(days or 7))
        except (TypeError, ValueError):
            n = 7
        r = _mood.weekly_review(days=n)
        return {"ok": bool(r.get("ok")), "days": n,
                "count": int(r.get("count") or 0), "text": str(r.get("text") or ""),
                "labels": {str(k): v for k, v in _mood.SCORE_LABELS.items()}}

    def mood_export(self) -> dict:
        """把全部情绪记录另存成文本文件(先弹"另存为",取消就什么都不做)。"""
        from minicode import mood as _mood
        body = _mood.export_text()
        if not body:
            return {"ok": False, "msg": "还没记过情绪,没有可导出的内容"}
        name = f"我的情绪记录-{time.strftime('%Y%m%d-%H%M')}.md"
        window = getattr(self.ui, "window", None)
        if window is not None:
            target = self._ask_save_path(name,
                                         file_types=("Markdown 文件 (*.md)",
                                                     "所有文件 (*.*)"))
            if target is None:
                return {"ok": False, "msg": "已取消导出"}
        else:
            out_dir = Path(self.workspace) / "exports"
            try:
                out_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                return {"ok": False, "msg": f"创建导出目录失败:{exc}"}
            target = out_dir / name
        try:
            Path(target).write_text(body, encoding="utf-8")
        except OSError as exc:
            return {"ok": False, "msg": f"写入失败:{exc}"}
        self.ui.push("info", text=f"情绪记录已导出:{target}")
        return {"ok": True, "path": str(target), "name": Path(target).name,
                "count": len(_mood.load_entries())}

    def mood_clear(self) -> dict:
        """清空本机情绪记录(前端已两次确认;这一步没有备份,提示里说清楚)。"""
        from minicode import mood as _mood
        r = _mood.clear_all()
        if r.get("ok"):
            self.ui.push("info", text="情绪记录已清空。")
        return r

    def mood_log(self, score=None, note: str = "") -> dict:
        """面板上的快捷记录:一天一条,同一天再记就是覆盖(mood.log 里会说清)。

        msg 只含**给人看**的那段求助内容;转述规矩(HELP_CAVEATS)留给工具返回给模型。
        """
        from minicode import mood as _mood
        r = _mood.log(score=score, note=str(note or ""))
        return {"ok": bool(r.get("ok")), "msg": str(r.get("msg") or ""),
                "crisis": bool(r.get("crisis"))}

    def mood_help(self) -> dict:
        """「想找人聊聊」这段摆给人看:只给事实,不给模型看的转述规矩。"""
        from minicode import mood as _mood
        return {"ok": True, "text": _mood.HELP_FACTS}

    def search_sessions(self, query: str = "") -> dict:
        """侧栏搜索:在所有历史会话留档里找关键词。

        返回的 `index` 与侧栏列表同一套序号(都来自 `list_sessions` 的先后顺序),
        前端拿到就能直接调 `resume_session` 切过去,不必再引入路径维度
        (路径也不该下发给网页版访客)。
        """
        q = (query or "").strip()
        if not q:
            return {"ok": True, "query": "", "total": 0, "results": []}
        hits = search_session_logs(self.sessions_dir, q)
        order = {s.path: i for i, s in
                 enumerate(list_sessions(self.sessions_dir, limit=50), start=1)}
        results = []
        for hit in hits:
            idx = order.get(hit["path"])
            if not idx:
                continue        # 超出侧栏条数上限的老会话:搜得到,但列表里没有它
            results.append({"index": idx, "hits": hit["hits"],
                            "snippet": hit["snippet"], "file": hit["path"].name})
        return {"ok": True, "query": q, "total": len(results), "results": results}

    # ------------------------------------------------------------ 内部

    def _run_task(self, runner: SessionRunner, text: str, content=None,
                  *, add_user: bool = True) -> None:
        """在后台线程里跑一个回合(每个会话一个线程,互不干扰)。"""
        runner.busy = True
        runner.ui.push("busy", value=True)
        self._push_sessions()          # 立刻让侧栏标上"运行中"的转圈
        try:
            runner.agent.run_turn(text, content, add_user=add_user)
        except Exception as e:  # 引擎之外的意外错误也要让前端知道
            runner.ui.push("error", text=f"内部错误: {type(e).__name__}: {e}")
        finally:
            runner.busy = False
            # 中断/停止会让 `tool_done` 根本不被调用:实时表里那一步还开着,
            # 监控栏就会永远转圈。回合结束了,统一收成"已中断"。
            abandon = getattr(runner.ui, "live_abandon", None)
            if callable(abandon):
                abandon()
            runner.ui.push("busy", value=False)
            self._push_sessions()

    def _push_sessions(self) -> None:
        running_paths = {str(r.log_path) for r in self.running_sessions() if r.log_path}
        cur_path = self.session.log_path
        cur_key = ws.key(self.workspace)
        items = []
        for i, s in enumerate(list_sessions(self.sessions_dir, limit=50), start=1):
            # 每条会话都带工作区名:前端按它分组(当前工作区的组不额外标名)。
            # 老留档没有 workspace 字段 → 归进"(未记录)"组,不与当前混在一起。
            if s.workspace:
                in_current = ws.key(s.workspace) == cur_key
                ws_name = "" if in_current else ws.display_name(s.workspace, self.app_dir)
            else:
                in_current = False
                ws_name = "未记录"
            items.append({
                "index": i, "file": s.path.name, "time": s.time,
                "n_messages": s.n_messages,
                "last_user_text": s.last_user_text,
                # v0.9.98:自动标题(第一条提问派生,不调模型);前端优先显示它,
                # 老留档没有 first_user_text 时是空串,前端退回原样
                "title": derive_title(s.first_user_text),
                "current": s.path == cur_path,
                "workspace": ws_name,
                "ws_current": in_current,
                "running": str(s.path) in running_paths})
        self.ui.push("sessions", items=items)

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
                item = {"role": role, "text": display_text(content)}
                if role == "user":
                    atts = restore_user_atts(self.workspace, content)
                    if atts:
                        item["atts"] = atts
                out.append(item)
        return out

    # 确认握手:Agent 线程阻塞在此,前端点按钮后返回
    def confirm_tool(self, tool: Tool, args: dict, reason: str = "",
                     note: str = "", label: str = "") -> str:
        from minicode.explain import permission_why
        why = permission_why(tool.name, args, reason)
        risks = list(why.get("risks") or [])
        if note:
            risks.insert(0, note)      # 升级警示排在第一行,确认框里红字强调
        with self._confirm_lock:             # 一次只处理一个确认请求
            self._confirm_event.clear()
            self._confirm_choice = "no"
            self.ui.push("confirm_request", tool=tool.name,
                         detail=self._permission_detail_text(tool, args),
                         why=why.get("why", ""), digest=why.get("digest", ""),
                         risks=risks, alert=bool(note),
                         label=str(label or ""))
            if not self._wait_event(self._confirm_event,
                                    open_flag="_confirm_open"):
                # 没人回答:按"拒绝"收口。这方向是安全的 —— 不执行任何东西,
                # 也不会把"没点"说成"同意了"。
                self.ui.push("info", text="这次没等到你的确认,已按**不执行**处理。")
            return self._confirm_choice

    def _wait_event(self, event: threading.Event, slice_s: float = 1.0,
                    open_flag: str = "") -> bool:
        """等一个"用户点了什么"的事件,每秒醒一次看还有没有人会来回答它。

        为什么不能 `event.wait()` 一把梭:确认框与提问卡都是**后台线程阻塞等前端**,
        而叫醒它的只有 `confirm_reply` / `reply_ask` 两处。用户按停止、直接关窗、
        或者界面脚本半路死了,那次等待就永远没人回答 —— 线程带着那把锁一起挂住,
        之后所有确认框都弹不出来(症状是"学长再也不问我了,也什么都不做")。
        每秒探一次"还要不要等",等不到就按**未回答**收口:既不凭空造一个超时把慢的人
        挡在门外,也绝不无期限地占着一条线程。

        `open_flag` 是跨会话看板(B1)要的那个事实:"此刻有没有一张卡等人答"。
        它只在等待期间为真,`finally` 里必清 —— 看板把"等你"报成"在跑"会让用户
        以为还有活在动,而真相是它停在你手上。
        """
        if open_flag:
            setattr(self, open_flag, True)
        try:
            while not event.wait(slice_s):
                if self._closing or self.ui.stop_requested:
                    return False
            return True
        finally:
            if open_flag:
                setattr(self, open_flag, False)

    # 提问握手(v0.9.96):Agent 线程阻塞在此,前端点完按钮后带答案返回
    def ask_user(self, questions: list[dict], note: str = "",
                 label: str = "") -> dict:
        with self._ask_lock:                # 一次只问一张卡(与确认框同一条规矩)
            self._ask_event.clear()
            self._ask_answer = {"mode": asking.MODE_UNANSWERED,
                                "reason": "没等到回答"}
            self._ask_questions = questions
            self.ui.push("ask_request", questions=questions,
                         note=str(note or ""), label=str(label or ""))
            if not self._wait_event(self._ask_event,
                                    open_flag="_ask_open"):
                reason = ("窗口正在关闭" if self._closing else "你按了停止")
                self._push_ask_trace("没等到回答,这一题按未回答处理")
                return {"mode": asking.MODE_UNANSWERED, "reason": reason}
            return self._ask_answer

    def _push_ask_trace(self, text: str) -> None:
        """答完在聊天区留一行痕(v0.9.96 拍的 D2:卡片关掉不是消失)。

        这句话由**后端**组:前端自己拼一份同样的话,以后就是两处真相 ——
        而这两处都会显示给用户看,分家时没人看得出来。
        """
        try:
            self.ui.push("ask_trace", text=str(text or ""))
        except Exception:
            pass        # 留痕是展示层,绝不能把回答通道本身弄坏

    def ask_reply(self, payload) -> dict:
        """前端「发送回复」/「按推荐继续」的落点。payload 是 JSON 字符串或对象。"""
        try:
            data = json.loads(payload) if isinstance(payload, str) else (payload or {})
        except (ValueError, TypeError):
            return {"ok": False, "msg": "回答的格式不对,没记下来"}
        if not isinstance(data, dict):
            return {"ok": False, "msg": "回答的格式不对,没记下来"}
        mode = str(data.get("mode") or "")
        if mode not in (asking.MODE_ANSWER, asking.MODE_RECOMMEND):
            return {"ok": False, "msg": f"未知的回答方式「{mode}」"}
        answers = data.get("answers")
        if mode == asking.MODE_ANSWER and not isinstance(answers, list):
            return {"ok": False, "msg": "answer 方式必须带 answers 数组"}
        self._ask_answer = {"mode": mode, "answers": answers or []}
        self._ask_event.set()
        # 卡片关掉之后聊天区要留下一行"你当时怎么答的"(D2 拍板:可回看)。
        # 这句话由后端组,前端只负责摆 —— 两处各拼一份就是两份真相,而两份都摆在用户眼前。
        self._push_ask_trace(self._ask_trace_text(mode, answers or []))
        return {"ok": True}

    def _ask_trace_text(self, mode: str, answers: list) -> str:
        qs = self._ask_questions or []
        if mode == asking.MODE_RECOMMEND:
            return ("学长问的 " + str(len(qs)) + " 题:你点了「按推荐继续」"
                    "→ 逐题按他列在第一项的推荐做(没逐题确认)。")
        by_id: dict[int, dict] = {}
        for a in answers:
            if isinstance(a, dict):
                try:
                    by_id[int(a.get("id"))] = a
                except (TypeError, ValueError):
                    continue
        parts = []
        for q in qs:
            item = by_id.get(int(q.get("id", -1)), {})
            choice = item.get("choice")
            got = [str(c) for c in (choice if isinstance(choice, list)
                                    else [choice] if choice else [])]
            free = str(item.get("free") or "").strip()
            head = "「" + str(q.get("q") or "")[:24] + "」→ " + ("、".join(got) or "(没选)")
            parts.append(head + ("(自己补了一句:" + free[:40] + ")" if free else ""))
        return ("学长问的 " + str(len(qs)) + " 题,你答了:" + ";".join(parts)
                if parts else "学长的提问已回答。")

    def abandon_pending(self, reason: str = "") -> None:
        """停止键 / 关窗时叫醒正在等待的线程(确认框与提问卡各一次)。

        由 `GuiUI.request_stop` 与 `Bridge.close` 调:那两处是"用户已经不打算再答了"
        的确凿时刻。不叫醒的话挂住的不是这一次,而是**下一切** —— 锁在后面那条
        线程手里,后来的确认框根本弹不出来。
        """
        self._confirm_choice = "no"
        self._confirm_event.set()
        self._ask_answer = {"mode": asking.MODE_UNANSWERED,
                            "reason": str(reason or "没等到回答")}
        self._ask_event.set()

    def _permission_detail_text(self, tool: Tool, args: dict) -> str:
        if tool.name == "bash":
            return f"$ {args.get('command', '')}"

        if tool.name == "write_file":
            path = str(args.get("path", "?"))
            content = str(args.get("content", ""))
            target = self.workspace / path
            if target.exists():
                # 覆盖已有文件:给真 diff(改了哪几行一眼看清),不再只给新内容头部
                current = target.read_text(encoding="utf-8",
                                           errors="replace").splitlines()
                after = content.splitlines()
                diff = list(difflib.unified_diff(
                    current, after, lineterm="", n=2))[2:]
                text = f"覆盖已有文件: {path}\n\n" + "\n".join(diff[:DIFF_MAX_LINES])
                if len(diff) > DIFF_MAX_LINES:
                    text += f"\n…(diff 共 {len(diff)} 行)"
                return text
            lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
            head = f"新建文件: {path}({lines} 行)\n\n"
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
        if name == "browser_click":
            return f"在当前页面里点击:{args.get('target', '')}"
        if name == "browser_fill":
            keys = list((args.get("fields") or {}).keys())
            text = "在当前页面的表单里填:" + "、".join(str(k) for k in keys[:8])
            if args.get("submit"):
                text += ";填完自动点提交"
            return text
        if name == "browser_screenshot":
            return (f"把当前页面截图存到:{args.get('path') or 'shots/ 下(自动命名)'}"
                    + ("" if args.get("full_page", True) else "(只截可视区)"))
        if name == "web_search":
            return f"联网搜索:{args.get('query', '')}"
        if name == "kb_suggest":
            return ("把这条信息存成知识库候选(等维护者审核,不进正式知识库):"
                    + str(args.get('question', ''))[:120])
        if name == "py_script":
            return f"用内置 Python 运行时执行脚本:{args.get('path', '')}"
        if name == "reminder":
            action = str(args.get("action", "list"))
            if action == "add":
                due = str(args.get("due", "") or "未定日期")
                return f"记一条提醒:{args.get('title', '')}(截止 {due})"
            return f"查看/更新提醒清单(action={action})"

        return json.dumps(args, ensure_ascii=False)[:500]


def create_app(config, workspace: Path, llm=None, ui: GuiUI | None = None,
               sessions_dir: Path | None = None, config_path: Path | None = None,
               workspace_file: Path | None = None, app_dir: Path | None = None):
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
                    llm=llm, mcp_tools=mcp.build_tools(), sandbox=sandbox,
                    workspace_file=workspace_file, app_dir=app_dir)
    bridge._mcp = mcp
    for runner in bridge._runners.values():
        runner.agent.mcp = mcp          # 退出时 bridge.close() 统一关停
    # v0.9.61:mcp 工具的落盘与热重载。钩子挂在**模块**上(局部变量 mcp 是
    # McpManager 实例,不是模块 —— 装错对象就是 AttributeError)。
    from minicode import mcp as mcp_module
    mcp_module.set_admin_hook(bridge._mcp_admin)
    return bridge, ui, bridge.agent, bridge.session
