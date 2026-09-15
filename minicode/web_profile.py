"""网页版(对外只读问答)的收敛层:工具白名单 + 裁剪版系统提示词 + 对外 API 门面。

设计要点见 `D:\\game\\agent网页版-实施方案.md`:

1. **白名单显式列举,绝不用 `Tool.read_only` 过滤。** 那个标志在本项目里的语义是
   "执行前不需要用户确认",不是"读不到敏感数据" —— `read_file` / `glob` / `grep`
   都是 `read_only=True`,而工作区根目录下就有 `config.local.json`(内置校园 Key)。
   按标志位过滤等于把 Key 送出去。
2. **系统提示词不带长期记忆**,也不带编码 agent 那套 read_file/edit_file/bash 工作准则
   —— 提示词里写着"你通过工具读写文件",对着一份没有这些工具的白名单只会让模型胡言乱语。
3. **对外门面砍掉所有者专属方法**:供应商与 Key、沙箱开关、权限模式、附件、记忆整理;
   而 `get_asset` 只放行图片扩展名 —— 它本身是一个覆盖整个工作区的文件读取原语。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from minicode.agent import Agent
from minicode.gui_bridge import Bridge
from minicode.kb import kb_dirs, kb_prompt_section
from minicode.library import library_prompt_section
from minicode.library_guide import library_guide_prompt_section
from minicode.session import Session
from minicode.skills import load_skills, skill_dirs, skills_prompt_section

# 同学可以用到的工具。改这里之前先读实施方案 §3。
PUBLIC_TOOLS: frozenset[str] = frozenset({
    "search_knowledge",         # 校园语料(带来源与可信度)
    "library_search",           # 图书馆 133 万条离线馆藏
    "library_guide",            # 入馆教学 / 中图法→楼层
    "use_skill",                # 读技能指导(只读)
    "map_find_place",
    "map_show_list",
    "map_highlight_building",
    "map_drop_pin",
    "map_clear",
})

# 明确排除(留在这里是为了让人一眼看到"为什么没有它"),测试会断言它们不在白名单里。
EXCLUDED_TOOLS: dict[str, str] = {
    "read_file": "能读工作区任意文件,包括内含校园 Key 的 config.local.json",
    "glob": "同 read_file,可枚举工作区文件",
    "grep": "同 read_file,可在工作区全文搜索",
    "write_file": "写文件",
    "edit_file": "改文件",
    "bash": "执行命令",
    "plan": "写工作区文件",
    "memory": "读写所有者自己的长期记忆",
    "task": "子代理会携带 implement 类写工具,绕过白名单",
    "web_crawl": "SSRF:可让服务端请求校园内网任意地址;且会写 crawled//kb/",
}

# 只允许通过 get_asset 读回的扩展名(该接口的原意是显示技能正文里的配图)
ASSET_EXTENSIONS: frozenset[str] = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg",
})

PUBLIC_PROMPT = """你是「广州应用科技学院 · 校园助手」,由学长学姐整理的经验库驱动,面向在校同学答疑。

# 服务边界(必须遵守)
- 你只能使用校园知识库、图书馆馆藏、校园地图这三类数据回答;手上有哪些工具见下方清单。
- 你没有读写文件、执行命令、访问网络的能力。**不要声称自己能做这些事**,也不要建议同学
  让你"去看某个文件";遇到这类请求,直说超出服务范围,并指出同学可以自己怎么办。
- 涉及学校政策、通知、时间、金额、办事流程时,一律以知识库条目为准并给出**来源链接**;
  条目里没有的就不说,宁可可查不到也不要编。
- 引用学长学姐的经验条目(可信度不是 A)时,说明"学长学姐亲测有效、推荐的方法"并附条目标题。

# 校园背景
- 学校简称"广应科",默认**肇庆校区**(广东省肇庆市鼎湖区莲花镇丰乐路20号)。
  同学没特别说明校区时,一律按肇庆校区回答。
- 常见问题范围:校园活动、考试与四六级、考研与专升本、奖助学金、入党、社团、学科竞赛、
  选课、宿舍与生活服务、校园卡、图书馆、办事流程。

# 校园地图(肇庆校区)
- 手上有一份校区手绘地图的标注索引(36 处建筑 + 203 条学生俗称),回答时可以同时把位置画在
  同学界面上(工具名 map_*):问"XX 在哪 / 怎么走"→ `map_find_place`;问"学校有哪些食堂/楼"
  → `map_show_list`;要一次标出多处 → `map_highlight_building`;标一个具体点 → `map_drop_pin`。
- **这份数据是位置图,没有路网**:只能回答"在哪、旁边是什么、属于哪个片区",
  **绝不能编路线、方向顺序或步行时间**。问"怎么走"就给方位与邻近建筑,并说明
  "手上是位置图,具体路线以现场指引为准"。
- **真歧义必须让同学确认**:"A/B/C/D/E 栋"在地图上是两栋楼(学思苑X 与 集贤苑X座),
  相距很远。工具返回 ambiguous 时把候选都摆出来问清是哪一栋,**不要自己挑一个**。
- 学生俗称直接传参即可(E栋、s5、旧图=图书馆、综合馆=体育综合馆、正校门/西门),不用先转官方名。
- 讲新地点前,如果地图上还留着上一次的框,先 `map_clear` 清一下。
- 索引里没有的地点,如实说"地图上没有这个标注",不要编位置。

# 图书馆
- 问"有没有某本书 / 在哪个楼层 / 索书号"→ `library_search`(离线全量馆藏,毫秒级);
  问"图书馆怎么用 / 借还流程 / 有什么空间"→ `library_guide`。一次结果通常就够答,别反复重查。

# 回复风格
- 用中文,直接给结论,再补必要背景;面向同学,不要长篇大论。
- 回复话术遵守 reply-tone 技能(需要细节时用 use_skill("reply-tone") 读取),
  称谓用"学弟/学妹"。

# 环境
- 今天日期:{date}
"""


def build_public_system_prompt(workspace: Path) -> str:
    """对外模式专用提示词。

    刻意**不**复用 `app_setup.build_system_prompt()`:那一份是给编码 agent 的
    (含文件/命令工作准则),而且会注入 `memory/MEMORY.md` —— 那是所有者自己的笔记。
    """
    skills = load_skills(skill_dirs(workspace))
    return (PUBLIC_PROMPT.replace("{date}", time.strftime("%Y-%m-%d"))
            + skills_prompt_section(skills)
            + kb_prompt_section(kb_dirs(workspace))
            + library_prompt_section(workspace)
            + library_guide_prompt_section(workspace))


def apply_public_profile(agent: Agent, session: Session, workspace: Path) -> list[str]:
    """把一个刚建好的会话运行器收敛成"对外只读":过滤工具 + 换系统提示词。

    返回保留下来的工具名(便于日志与测试断言)。
    """
    kept = [t for t in agent.registry if t.name in PUBLIC_TOOLS]
    agent.registry = kept
    agent._tool_map = {t.name: t for t in kept}      # noqa: SLF001 —— 同包内部约定

    prompt = build_public_system_prompt(workspace)
    for i, msg in enumerate(session.messages):
        if msg.get("role") == "system":
            session.messages[i] = {"role": "system", "content": prompt}
            break
    return [t.name for t in kept]


class WebBridge(Bridge):
    """对外模式的门面 Bridge。

    每个访客**一个实例**(自带私有 sessions 目录),因此访客之间不共享任何内存状态:
    看不到彼此的对话,也不会因为某个人的 `_confirm_event` / `_busy` 影响别人。
    代价只是一份 LLMClient(很轻),MCP 在对外模式直接不接。
    """

    def open_session(self, load_path: Path | None = None):
        runner = super().open_session(load_path)
        apply_public_profile(runner.agent, runner.session, self.workspace)
        # 第二道保险:确认通道一律拒绝。SessionGuiUI 默认会转给 Bridge.confirm_tool,
        # 那是个单例 Event,没人点击就永久挂住线程。
        runner.ui.confirm_handler = lambda *a, **k: "no"
        return runner


def _refuse(msg: str) -> dict:
    return {"ok": False, "msg": msg}


class PublicFacade:
    """对外可调用的方法集合 —— 白名单语义:没列出的名字一律拒绝。

    与 `gui_bridge.PublicApi` 的区别:那个是"暴露给本机窗口",这个是"暴露给校园网"。
    """

    def __init__(self, bridge: WebBridge):
        self._b = bridge
        self._handlers = {
            "hello": self._hello,
            "get_info": self._get_info,
            "get_suggestions": self._get_suggestions,
            "send_task": self._send_task,
            "stop_task": self._stop_task,
            "new_task": self._new_task,
            "compact_now": self._compact_now,
            "edit_resend": self._edit_resend,
            "confirm_reply": self._confirm_reply,
            "refresh_sessions": self._refresh_sessions,
            "resume_session": self._resume_session,
            "delete_session": self._delete_session,
            "get_asset": self._get_asset,
            "undo": self._undo,
            # 以下方法前端会调,但对外模式不开放:返回可读的拒绝原因而不是静默失败
            "pick_attachments": lambda **k: _refuse("网页版暂不支持上传附件"),
            "set_sandbox": lambda **k: _refuse("网页版不开放沙箱设置"),
            "set_permission_mode": lambda **k: _refuse("网页版不开放权限模式设置"),
            "consolidate_memory": lambda **k: _refuse("网页版不开放记忆整理"),
            "get_providers": lambda **k: _refuse("网页版不提供密钥管理"),
            "save_providers": lambda **k: _refuse("网页版不提供密钥管理"),
            "set_provider": lambda **k: _refuse("网页版不提供密钥管理"),
            "get_token_usage": lambda **k: _refuse("网页版不开放用量统计"),
        }

    # ------------------------------------------------------------ 分发

    def allowed_methods(self) -> list[str]:
        return sorted(self._handlers)

    def call(self, name: str, args: dict | None = None) -> dict:
        handler = self._handlers.get(name)
        if handler is None:
            return _refuse(f"网页版不开放该功能:{name}")
        args = args or {}
        if not isinstance(args, dict):
            return _refuse("参数必须是 JSON 对象")
        try:
            return handler(**args)
        except TypeError as e:
            return _refuse(f"参数不匹配:{e}")
        except Exception as e:      # 单个方法出错不该掀翻整个请求
            return _refuse(f"服务内部错误:{type(e).__name__}: {e}")

    # ------------------------------------------------------------ 读

    def _hello(self) -> dict:
        return {"ok": True, "session_key": self._current_key()}

    def _get_info(self) -> dict:
        """给前端初始化用。所有者专属信息一律抹掉。"""
        info = dict(self._b.get_info())
        info["workspace"] = ""                       # 不暴露服务器路径
        info["base_url"] = ""                        # 不暴露内网模型端点
        info["session_key"] = self._current_key()
        info["api_key_configured"] = True            # 服务端已配好,前端不必提示
        info["vision"] = False                       # 对外不开放图片理解
        info["memory_count"] = 0                     # 不暴露所有者记忆条数
        info["memory_user_count"] = 0
        info["mcp_servers"] = []
        info["permission_mode"] = "confirm"
        active = info.get("active_provider")
        # 只留当前供应商的展示信息(前端顶部模型标识用),不带 base_url / has_key
        info["providers"] = [
            {k: p.get(k) for k in ("id", "name", "model")}
            for p in info.get("providers", []) if p.get("id") == active
        ]
        return info

    def _current_key(self) -> str:
        runner = getattr(self._b, "_current", None)
        return getattr(runner, "key", "") or ""

    def _clear_stop_flag(self) -> None:
        """清掉上一轮遗留的停止标志。

        `GuiUI.request_stop()` 设的是**跨回合**的标志(桌面版里只有人手点"停止"才会设),
        而网页版里 SSE 断开是家常便饭(刷新、切标签、EventSource 自动重连)。
        不在这里清掉的话,刷新页面之后的下一次提问会在第一个输出增量处被立刻打断。
        """
        runner = getattr(self._b, "_current", None)
        ui = getattr(runner, "ui", None)
        if ui is not None:
            ui.stop_requested = False

    def _get_suggestions(self) -> dict:
        return self._b.get_suggestions()

    def _get_asset(self, rel_path: str = "") -> dict:
        """只放行图片:这个接口本来只用于显示技能正文里的配图。"""
        ext = os.path.splitext(str(rel_path))[1].lower()
        if ext not in ASSET_EXTENSIONS:
            return _refuse("网页版仅支持读取图片资源")
        return self._b.get_asset(rel_path)

    # ------------------------------------------------------------ 写对话

    def _send_task(self, text: str = "", attachments: str = "[]") -> dict:
        # 附件在对外模式已关闭,这里固定传空清单
        self._clear_stop_flag()
        return self._b.send_task(text, "[]")

    def _stop_task(self) -> dict:
        return self._b.stop_task()

    def _new_task(self) -> dict:
        return self._b.new_task()

    def _compact_now(self) -> dict:
        return self._b.compact_now()

    def _edit_resend(self, ordinal: int = 0, new_text: str = "", expect: str = "") -> dict:
        self._clear_stop_flag()
        return self._b.edit_resend(ordinal, new_text, expect)

    def _confirm_reply(self, choice: str = "no") -> dict:
        # 对外模式没有确认流程,前端也不该弹;吞掉即可
        return {"ok": True}

    def _undo(self) -> dict:
        return {"ok": True, "msg": "网页版没有需要撤销的文件改动"}

    # ------------------------------------------------------------ 会话(仅本访客自己的)

    def _refresh_sessions(self) -> dict:
        self._b._push_sessions()                     # noqa: SLF001
        return {"ok": True}

    def _resume_session(self, index: int = 0) -> dict:
        return self._b.resume_session(index)

    def _delete_session(self, index: int = 0) -> dict:
        return self._b.delete_session(index)
