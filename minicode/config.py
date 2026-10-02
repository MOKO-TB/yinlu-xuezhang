"""配置加载与校验:多供应商 API Key(providers)+ 运行参数。

兼容两代格式:
- v0.2 扁平格式 {base_url, api_key, model, ...} → 合成单个 default 供应商
- v0.3 数组格式 {"providers": [{id,name,base_url,api_key,model}], "active_provider": ...}
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

from minicode.paths import (CONFIG_PATH, EXAMPLE_CONFIG_PATH, app_path,
                            resource_path)

API_KEY_PLACEHOLDERS = {"", "YOUR_API_KEY", "在这里填入你的 API Key"}

# 离线测试/调试用的端点覆盖(见 _apply_env_overrides);正常使用不要设置
ENV_BASE_URL = "YINLUXS_LLM_BASE_URL"
ENV_MODEL = "YINLUXS_LLM_MODEL"
ENV_API_KEY = "YINLUXS_LLM_API_KEY"

DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_MODEL = "glm-4.6"

# 「常用供应商」预设:一键把地址与几个常见模型名填进表单。
#
# 刻意保持克制 —— 这只是省打字,不是权威清单:各家模型名几个月就换一茬,
# 写成"唯一正确答案"只会过期误导。面板上的「从服务获取」会去问服务端要
# 真实列表(GET /models),那才是准的;这里的名字用户可改可删。
PROVIDER_PRESETS: list[dict] = [
    {"label": "DeepSeek", "name": "DeepSeek",
     "base_url": "https://api.deepseek.com/v1",
     "models": ["deepseek-chat", "deepseek-reasoner"]},
    {"label": "智谱 GLM", "name": "智谱 GLM",
     "base_url": "https://open.bigmodel.cn/api/paas/v4",
     "models": ["glm-4.6", "glm-4-flash"]},
    {"label": "通义千问(阿里云百炼)", "name": "通义千问",
     "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
     "models": ["qwen-plus", "qwen-max", "qwen-turbo"]},
    {"label": "Kimi(月之暗面)", "name": "Kimi",
     "base_url": "https://api.moonshot.cn/v1",
     "models": ["moonshot-v1-32k", "moonshot-v1-128k"]},
    {"label": "硅基流动 SiliconFlow", "name": "硅基流动",
     "base_url": "https://api.siliconflow.cn/v1",
     "models": ["deepseek-ai/DeepSeek-V3"]},
    {"label": "OpenAI", "name": "OpenAI",
     "base_url": "https://api.openai.com/v1",
     "models": ["gpt-4o-mini", "gpt-4o"]},
]

# API Key 不写进 config.json(它可能被上传 GitHub),单独存放在用户主目录
CREDENTIALS_PATH = Path.home() / ".yinluxs" / "credentials.json"

# 内置校园模型的 Key 同样不进仓库(仓库是公开的),改用本地注入:
# - 环境变量 YINLUXS_BUILTIN_API_KEY(测试/CI/打包机临时注入)
# - config.local.json 的 builtin_api_key 字段(开发态=项目根,打包态=exe 同目录)
# - 随 exe 打包进 _MEIPASS 的 config.local.json(见 packaging/*.spec)
# 三者都没有时留空:应用照常启动,只是内置模型不可用(界面会提示未配置)。
BUILTIN_API_KEY_ENV = "YINLUXS_BUILTIN_API_KEY"
LOCAL_SECRETS_NAME = "config.local.json"


def _builtin_api_key() -> str:
    """解析内置校园模型的 API Key;仓库里只留占位空值。"""
    env = os.environ.get(BUILTIN_API_KEY_ENV, "").strip()
    if env:
        return env
    for path in (app_path(LOCAL_SECRETS_NAME), resource_path(LOCAL_SECRETS_NAME)):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(raw, dict):
            key = str(raw.get("builtin_api_key") or "").strip()
            if key:
                return key
    return ""


def _ensure_builtin_provider(cfg: Config) -> None:
    """把内置供应商合并进配置:始终存在、以代码为准,并固定在列表第一位。

    激活规则(v0.9.03 起,由"只准用校园模型"改为"默认用它,可自主切换"):
    - 已配置的供应商**有可用 key**(凭据文件或环境变量注入)→ 尊重用户选择;
    - 否则(没配过、key 为空/占位符)→ 回落到内置校园模型,保证开箱即用。
    内置供应商的字段(base_url/model 等)仍以代码为准,防止被改坏。
    """
    if not any(p.id == BUILTIN_PROVIDER_ID for p in cfg.providers):
        # 插入副本而不是共享的模块级单例:避免任何写入(如测试的环境变量覆盖)
        # 污染内置模板,影响同进程里的后续配置。
        cfg.providers.insert(0, replace(BUILTIN_PROVIDER))
    else:                                    # 挪到第一位,保证"内置优先"的展示顺序
        idx = next(i for i, p in enumerate(cfg.providers)
                   if p.id == BUILTIN_PROVIDER_ID)
        cfg.providers.insert(0, cfg.providers.pop(idx))
    for p in cfg.providers:
        if p.id == BUILTIN_PROVIDER_ID:
            p.name = BUILTIN_PROVIDER.name
            p.base_url = BUILTIN_PROVIDER.base_url
            p.api_key = _builtin_api_key()   # 每次解析,本地换 Key 无需改配置
            p.model = BUILTIN_PROVIDER.model
            p.models = []            # 模型清单同样以代码为准(单条兜底)
            p.enabled = True
    active = cfg.provider
    if active is None or active.id == BUILTIN_PROVIDER_ID:
        cfg.active_provider_id = BUILTIN_PROVIDER_ID
    elif not active.enabled:
        # 供应商被停用:回落到内置校园模型(配置保留,重新启用后可再切)
        cfg.active_provider_id = BUILTIN_PROVIDER_ID
    elif not active.enabled_models():
        # 一个模型都没启用:同样回落,否则会把空模型名发给服务端
        cfg.active_provider_id = BUILTIN_PROVIDER_ID
    elif active.api_key.strip() in API_KEY_PLACEHOLDERS:
        # 选中了但没填 key:只能用回内置校园模型(配置保留,填 key 后可再切)
        cfg.active_provider_id = BUILTIN_PROVIDER_ID


def _apply_env_overrides(cfg: Config) -> None:
    """离线测试/调试:把当前模型的端点临时指到别处(如本地假 LLM 服务器)。

    只作用于本进程内存中的配置,不写回 config.json;普通用户无需设置。
    冻结版端到端测试(e2e_frozen*.py)靠它把打包后的 exe 指到假服务器。
    """
    p = cfg.provider
    if p is None:
        return
    base = os.environ.get(ENV_BASE_URL, "").strip()
    model = os.environ.get(ENV_MODEL, "").strip()
    key = os.environ.get(ENV_API_KEY, "").strip()
    if base:
        p.base_url = base.rstrip("/")
    if model:
        p.model = model
    if key:
        p.api_key = key


@dataclass
class Provider:
    """一个供应商(接口地址 + 密钥),以及它底下可用的模型清单。

    v0.9.13 起支持"一个供应商挂多个模型"(对齐星瑶的模型管理面板):
    `models` 是清单,每项 `{"name": 模型名, "enabled": 是否启用}`;
    `model` 保留为"当前用的那个"= 清单里第一个启用项,于是所有读 `p.model`
    的旧代码(base_url/api_key 那套属性、CLI /model、任务面板)零改动。

    老配置没有 `models` 字段:按"单条启用项"呈现,行为与改版前完全一致。
    """

    id: str
    name: str
    base_url: str
    api_key: str
    model: str = ""
    models: list = field(default_factory=list)   # [{"name": str, "enabled": bool}]
    enabled: bool = True                         # 供应商级启停(停用后不出现在可切列表)

    def model_entries(self) -> list[dict]:
        """规范化后的模型清单(老配置兜底成单条)。"""
        if self.models:
            return [dict(m) for m in self.models]
        return [{"name": self.model, "enabled": True}] if self.model.strip() else []

    def enabled_models(self) -> list[str]:
        return [str(e.get("name") or "").strip()
                for e in self.model_entries() if e.get("enabled", True)
                and str(e.get("name") or "").strip()]

    def sync_model(self) -> str:
        """把 `model` 同步成第一个启用模型;一个都没启用时保持原值。

        保持原值是有意的:模型名凭空消失会让用户在界面上"看不懂自己删了什么",
        而"没有启用模型"这件事由前端和保存校验各自提示。
        """
        names = self.enabled_models()
        if names:
            self.model = names[0]
        return self.model

    @property
    def has_key(self) -> bool:
        return bool(self.api_key.strip()) and \
            self.api_key.strip() not in API_KEY_PLACEHOLDERS

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "base_url": self.base_url,
                "api_key": self.api_key, "model": self.model,
                "models": self.model_entries(), "enabled": self.enabled}


@dataclass
class Config:
    providers: list = field(default_factory=list)  # list[Provider]
    active_provider_id: str = ""
    # confirm:变更前确认(查询放行、变更询问) / accept_edits:自动编辑(普通变更放行、敏感变更询问)
    # accept_all:自动放行(兼容旧配置) / full:完全访问(沙箱内非灾难性操作全放)
    permission_mode: str = "confirm"
    shell: str = "auto"                 # auto | bash | cmd | powershell
    max_output_chars: int = 20000       # 工具输出回填给模型前的截断长度
    # 单个回合里"请求模型 → 执行工具 → 回填"最多转几圈。撞上限后仍会让模型收一段尾
    # (那一次不带工具),只是不再允许它继续调工具。调大 = 一轮烧的 token 更多。
    max_steps: int = 40
    request_timeout_seconds: int = 300  # 单次 LLM 请求超时
    # 一轮对话的**总**时限(多次 LLM 往返还算在这一轮里):0 = 不限。
    # 到点后只在"上一步做完、下一步还没开始"的间隙停下,不强杀正在执行的那一步
    # ——bash/py_script 自己有单条命令超时,两层各管各的,别指望这里能打断一条卡住的命令。
    turn_time_limit_seconds: int = 1800
    # 本会话累计 token 消耗上限(0 = 不限,v0.9.78 成本闸门):只认服务端返回的
    # 真实用量(prompt+completion,不给 usage 的端点这道闸不起作用,文档写明)。
    # 到点与两条时限同一口径:两步之间收手,不强杀在跑的那一步。
    session_token_limit: int = 0
    # 会话留档保留个数(0=全部保留,默认):启动时按 mtime 删旧的。
    # 刻意只给这一个数 —— 年龄/体积的花样讲不清,一个数用户写得对。
    session_keep_last: int = 0
    # 单个子代理(task 工具派出去的那一档)最长等多久:0 = 不限。
    # 超时是"主代理不再等它",后台线程仍会跑完当前那一步(Python 没有安全的强杀),
    # 结果不再回填 —— 报告里会照这个口径写明是哪一路超时。
    subagent_time_limit_seconds: int = 600
    # 按档位分模型(v0.9.95):{"explore": "deepseek-v4-flash", "code": "..."}。
    # **只换模型名,不换供应商** —— 跨供应商要另一份 Key,那一层不在这里做
    # (会话级切供应商已有界面路径,这里要解决的是"侦察用便宜的、实现用硬的")。
    # 没配、或配的名字不在当前供应商的启用模型里 ⇒ 该档退回主模型,并在报告抬头
    # 里说清退回了、为什么 —— 静默换个模型跑,比不换更难查。
    subagent_models: dict = field(default_factory=dict)
    # 信号回灌(v0.9.39 B0/B1,见 minicode/feedback_recall.py):把已经记下来的东西
    # 回头用起来 —— 同一个词连着搜空就在结果里点一句、命中历史 👎 就给本轮一条
    # 系统侧先验。**关掉它,主循环的请求字节与结果文本都与开之前逐字一致**。
    feedback_recall: bool = True
    # 周期自动备份目录(v0.9.98,#17C 剩的"没有定时导出"):填了路径,周六上午
    # 那班任务才会把 pack=true 的本机数据(含聊天记录)打成迁移包放进去,留最近
    # 4 份;留空 = 不备份(任务到点只跳过并说明,绝不自选目录往用户盘上写)。
    backup_dir: str = ""
    context_window: int = 131072        # 模型上下文窗口(token),用于压缩触发判断
    compact_at_ratio: float = 0.7       # 估算 token 达到窗口的该比例时触发压缩
    # MCP 服务器原始配置(list[dict],由 minicode.mcp.parse_servers 解析;
    # 这里存原文以免 config 反向依赖 mcp 造成循环导入)
    mcp_servers: list = field(default_factory=list)
    # 沙箱策略原文(list[dict]/dict,由 minicode.sandbox.policy_from_raw 解析)
    sandbox: dict = field(default_factory=dict)
    # 当前模型是否支持看图(多模态):开启后上传的图片会以 data URL 发给模型
    vision: bool = False
    # 外接能力的原始配置(dict,由 minicode.embed / minicode.images 自己解析):
    # {"embeddings": {…}, "images": {…}}。与 sandbox / mcp 同一套路——config 只
    # 存原文,不反向依赖功能模块。**Key 只允许出现在用户自己的 config.json 里**,
    # 绝不进仓库、绝不进分享包(save_config 的白名单落盘会把 api_key 洗掉)。
    capabilities: dict = field(default_factory=dict)
    # 工具钩子(F3,v0.9.100;C2 v0.9.102 起 matcher):值为字符串 = 对所有工具跑;
    # 或 {"command": …, "tools": [工具名], "paths": ["通配符"]} 只对匹配的跑。
    # **只认 config.json 这一条路** —— 钩子是任意命令执行,工作区文件不能
    # 自证清白(permissions.json 同一条教训)。命令模板占位:{tool}=工具名、
    # {args_file}/{result_file}=参数/结果的临时 JSON 路径(经文件交付,
    # 不进命令行);pre 非零退出=拦截该步,post 失败只在结果尾如实附一行。
    hooks: dict = field(default_factory=dict)

    # ---- 活动供应商;base_url/api_key/model 属性保持旧代码零改动 ----
    @property
    def provider(self) -> Provider | None:
        for p in self.providers:
            if p.id == self.active_provider_id:
                return p
        return self.providers[0] if self.providers else None

    @property
    def base_url(self) -> str:
        return self.provider.base_url if self.provider else ""

    @property
    def api_key(self) -> str:
        return self.provider.api_key if self.provider else ""

    @property
    def model(self) -> str:
        return self.provider.model if self.provider else ""

    @property
    def api_key_configured(self) -> bool:
        return self.api_key.strip() not in API_KEY_PLACEHOLDERS

    def set_active_provider(self, provider_id: str) -> bool:
        if any(p.id == provider_id for p in self.providers):
            self.active_provider_id = provider_id
            return True
        return False

    def enabled_providers(self) -> list:
        """可用于切换的供应商:被停用的、一个模型都没启用的都不算。"""
        return [p for p in self.providers
                if p.id == BUILTIN_PROVIDER_ID or (p.enabled and p.enabled_models())]


# 内置供应商:学校部署的本地模型服务,该服务仅限校园网内使用。
# api_key 留空,运行时由 _builtin_api_key() 从本地注入(见上);打包时把
# config.local.json 一并打进 exe,用户侧仍是开箱即用。
BUILTIN_PROVIDER_ID = "gzasc"
BUILTIN_PROVIDER = Provider(
    id=BUILTIN_PROVIDER_ID,
    name="广州应用科技学院",
    base_url="http://10.223.24.16:8080/v1",
    api_key="",
    model="deepseek-v4-flash",
)



def _slug(text: str, fallback: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", text.strip()).strip("-").lower()
    return slug or fallback


def _providers_from_raw(raw: dict) -> list[Provider]:
    """v0.3 providers 数组;为空时回退 v0.2 扁平格式。"""
    out: list[Provider] = []
    seen: set[str] = set()
    for i, item in enumerate(raw.get("providers") or [], start=1):
        if not isinstance(item, dict):
            continue
        base = str(item.get("base_url") or "").strip()
        if not base:
            continue
        pid = _slug(str(item.get("id") or ""), f"provider_{i}")
        if pid in seen:
            pid = f"{pid}_{i}"
        seen.add(pid)
        out.append(Provider(
            id=pid,
            name=str(item.get("name") or pid),
            base_url=base.rstrip("/"),
            api_key=str(item.get("api_key") or ""),
            model=str(item.get("model") or DEFAULT_MODEL),
            models=_models_from_raw(item.get("models")),
            enabled=item.get("enabled", True) is not False,
        ))
    if not out:
        base = str(raw.get("base_url") or "").strip()
        if base:
            out.append(Provider(id="default", name="默认",
                                base_url=base.rstrip("/"),
                                api_key=str(raw.get("api_key") or ""),
                                model=str(raw.get("model") or DEFAULT_MODEL),
                                models=_models_from_raw(raw.get("models"))))
    return out


def _models_from_raw(raw) -> list[dict]:
    """解析供应商的模型清单(容错:字符串/字典混着来都能吃)。

    只做结构清洗,不在这里判重 —— 去重交给保存路径(save_providers),那里
    才有"告诉用户为什么要拒绝"的位置。
    """
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if isinstance(item, str):
            name, enabled = item, True
        elif isinstance(item, dict):
            name = str(item.get("name") or item.get("model") or "").strip()
            enabled = item.get("enabled", True) is not False
        else:
            continue
        name = name.strip()
        if name:
            out.append({"name": name, "enabled": enabled})
    return out


def _dedupe_models(entries: list[dict]) -> list[dict]:
    """按模型名去重(忽略大小写),保留先出现的那条。

    重名模型会让"启用了哪个"无法判断(切过去到底发哪个名字),所以在保存入口
    一律合并 —— 界面上的"＋ 添加模型"也可能被连点两次。
    """
    out: list[dict] = []
    seen: set[str] = set()
    for entry in entries:
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": name, "enabled": bool(entry.get("enabled", True))})
    return out


def _scrub_secrets(node, _keys=("api_key", "apikey", "token", "secret")):
    """递归洗掉外接能力配置里的密钥字段(只回显结构,不改内存里那份)。

    为什么必须洗:capabilities 是给"向量检索 / 图片生成"用的,使用者很容易顺手
    把 api_key 写进去。而项目里所有供应商密钥都走 `_credentials_save` 那份单独的
    凭据文件,config.json 是要显示在界面上、也可能被拷给别人看的。所以落盘时一律
    洗成空串,取 Key 只认 provider_id —— 外接能力**不新开一条密钥路径**。
    """
    if isinstance(node, dict):
        return {k: ("" if str(k).lower() in _keys else _scrub_secrets(v, _keys))
                for k, v in node.items()}
    if isinstance(node, list):
        return [_scrub_secrets(v, _keys) for v in node]
    return node


def _config_from_raw(raw: dict) -> Config:
    providers = _providers_from_raw(raw)
    active = str(raw.get("active_provider") or "")
    from minicode.permission import normalize_mode
    cfg = Config(
        providers=providers,
        active_provider_id=active,
        permission_mode=normalize_mode(raw.get("permission_mode", "confirm")),
        shell=raw.get("shell", "auto"),
        max_output_chars=int(raw.get("max_output_chars", 20000)),
        max_steps=max(1, int(raw.get("max_steps", 40) or 40)),
        request_timeout_seconds=int(raw.get("request_timeout_seconds", 300)),
        turn_time_limit_seconds=max(0, int(raw.get("turn_time_limit_seconds", 1800) or 0)),
        session_token_limit=max(0, int(raw.get("session_token_limit", 0) or 0)),
        session_keep_last=max(0, int(raw.get("session_keep_last", 0) or 0)),
        subagent_time_limit_seconds=max(0, int(
            raw.get("subagent_time_limit_seconds", 600) or 0)),
        # 按档位分模型的原始配置:一律转成"字符串→字符串",空值丢掉。
        # 写成列表/嵌套/数字都不崩(这份要拿去查表),但不认的条目就是不生效 ——
        # 具体能不能用交给 subagents.model_choice 去对着供应商的启用模型核,那才是真相。
        subagent_models={str(k): str(v).strip() for k, v in
                         (raw.get("subagent_models") if isinstance(
                             raw.get("subagent_models"), dict) else {}).items()
                         if str(v or "").strip()},
        feedback_recall=bool(raw.get("feedback_recall", True)),
        backup_dir=str(raw.get("backup_dir") or "").strip(),
        context_window=int(raw.get("context_window", 131072)),
        compact_at_ratio=float(raw.get("compact_at_ratio", 0.7)),
        mcp_servers=raw.get("mcp_servers") if isinstance(raw.get("mcp_servers"), list) else [],
        sandbox=raw.get("sandbox") if isinstance(raw.get("sandbox"), dict) else {},
        vision=bool(raw.get("vision", False)),
        capabilities=raw.get("capabilities") if isinstance(raw.get("capabilities"), dict) else {},
        hooks=raw.get("hooks") if isinstance(raw.get("hooks"), dict) else {},
    )
    # active 缺失/指向不存在的供应商 → 钉到第一个**用户配置**的供应商上。
    # 必须显式写回:Config.provider 在 id 为空时会隐式回退到 providers[0],
    # 而内置供应商随后会被插到第 0 位,不显式化的话旧配置(尤其 v0.2 扁平格式,
    # 根本没有 active_provider 字段)会被静默改成"用内置模型",用户填的 key 白配。
    if providers and not any(p.id == cfg.active_provider_id for p in providers):
        cfg.active_provider_id = providers[0].id
    return cfg


def load_config(path: Path = CONFIG_PATH) -> Config:
    """CLI 用:配置缺失/为空直接提示退出。"""
    if not path.exists():
        if EXAMPLE_CONFIG_PATH.exists():
            shutil.copy(EXAMPLE_CONFIG_PATH, path)
            print(f"已从模板生成 {path}")
            print("默认使用内置校园模型,直接重新启动即可;如需接入 MCP 服务器请编辑该文件。")
        else:
            print(f"缺少配置文件: {path}")
        sys.exit(1)

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.exit(f"config.json 不是有效 JSON: {e}")
    cfg = _config_from_raw(raw if isinstance(raw, dict) else {})
    _merge_credentials(cfg, path)
    _ensure_builtin_provider(cfg)   # 先补内置校园模型,providers 为空也能正常启动
    _apply_env_overrides(cfg)       # 仅测试/调试:环境变量临时改端点
    if not cfg.providers:
        sys.exit(f"配置中没有可用供应商(见 {path})。请在 providers 数组中"
                 f"至少填一项 base_url。")
    return cfg


def ensure_config(path: Path = CONFIG_PATH,
                  example_path: Path = EXAMPLE_CONFIG_PATH) -> Config:
    """GUI 用:缺失/残缺自动生成默认值并写回,绝不退出进程。"""
    if not path.exists() and example_path.exists():
        shutil.copy(example_path, path)
    raw: dict = {}
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raw = {}
    if not isinstance(raw, dict):
        raw = {}
    cfg = _config_from_raw(raw)
    if not cfg.providers:
        cfg.providers = [Provider(id="zhipu", name="智谱 GLM",
                                  base_url=DEFAULT_BASE_URL, api_key="",
                                  model=DEFAULT_MODEL)]
        cfg.active_provider_id = BUILTIN_PROVIDER_ID
    _merge_credentials(cfg, path)     # 迁移内嵌 key 并写回无密钥配置
    _ensure_builtin_provider(cfg)
    save_config(path, cfg)
    _apply_env_overrides(cfg)         # 仅测试/调试:环境变量临时改端点
    return cfg


def save_config(path: Path, cfg: Config) -> None:
    """写配置文件:api_key 一律不落 config.json(它可能被上传 GitHub),
    改存用户主目录 ~/.yinluxs/credentials.json。

    内置校园供应商连 base_url 也不落盘:内网地址是部署细节,不该出现在用户
    能打开的文件里。它的 base_url/api_key/model 每次加载都由
    _ensure_builtin_provider() 按代码补回,磁盘上那份纯属冗余。
    """
    def _safe(p: Provider) -> dict:
        d = {**p.to_dict(), "api_key": ""}      # 保持无密钥、可安全上传
        if p.id == BUILTIN_PROVIDER_ID:
            d["base_url"] = ""                   # 内网地址同样不落盘
        return d

    # 落盘前把 model 归位到"第一个启用模型",保证磁盘上的 model 与 models 自洽
    # (界面勾选/取消勾选后,只改 models 不改 model 的话,重开就会串味)
    for p in cfg.providers:
        p.sync_model()

    data = {
        "providers": [_safe(p) for p in cfg.providers],
        "active_provider": cfg.provider.id if cfg.provider else "",
        "permission_mode": cfg.permission_mode,
        "shell": cfg.shell,
        "max_output_chars": cfg.max_output_chars,
        "max_steps": cfg.max_steps,
        "request_timeout_seconds": cfg.request_timeout_seconds,
        # 两条时限跟着落盘:用户在设置里改过、下次启动还在(0=不限也照样存下来)
        "turn_time_limit_seconds": cfg.turn_time_limit_seconds,
        "session_token_limit": cfg.session_token_limit,
        "session_keep_last": cfg.session_keep_last,
        "subagent_time_limit_seconds": cfg.subagent_time_limit_seconds,
        # 空 dict 也照样存:用户写过就该在 config.json 里看得见(看不见就会被当成没生效)
        "subagent_models": cfg.subagent_models,
        "hooks": cfg.hooks,
        "feedback_recall": cfg.feedback_recall,
        "backup_dir": cfg.backup_dir,
        "context_window": cfg.context_window,
        "compact_at_ratio": cfg.compact_at_ratio,
    }
    if cfg.mcp_servers:
        data["mcp_servers"] = cfg.mcp_servers
    if cfg.sandbox:
        data["sandbox"] = cfg.sandbox
    if cfg.vision:
        data["vision"] = True
    if cfg.capabilities:
        # 外接能力**不新开一条密钥路径**:embeddings / images 只写
        # {"provider_id": "...", "model": "..."} 这类无密钥字段,运行时按
        # provider_id 去借 providers 里那份 Key(已有 credentials 机制管)。
        # 这里再兜一道:万一有人手写了 api_key,存盘时一律洗掉,
        # 免得 Key 从 credentials 文件漏进 config.json(便携版那是跟着包走的)。
        data["capabilities"] = _scrub_secrets(cfg.capabilities)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    _credentials_save({p.id: p.api_key for p in cfg.providers
                        if p.api_key.strip() and p.id != BUILTIN_PROVIDER_ID})


def _credentials_load(path: Path | None = None) -> dict[str, str]:
    path = path or CREDENTIALS_PATH   # 运行时取值,便于测试注入
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _credentials_save(keys: dict[str, str], path: Path | None = None) -> None:
    path = path or CREDENTIALS_PATH   # 运行时取值,便于测试注入
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(keys, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def _merge_credentials(cfg: Config, path: Path,
                       cred_path: Path | None = None) -> None:
    """凭据文件里的 key 覆盖 provider;config.json 内嵌的 key 迁移进凭据文件,
    并把 config.json 重写为无密钥版本。"""
    stored = _credentials_load(cred_path)
    rewrite = False
    for p in cfg.providers:
        if p.id == BUILTIN_PROVIDER_ID:
            continue                       # 内置 key 走本地注入,不进凭据文件
        file_key = p.api_key
        stored_key = stored.get(p.id, "")
        if file_key and file_key.strip() not in API_KEY_PLACEHOLDERS and file_key != stored_key:
            stored[p.id] = file_key           # 迁移:配置内嵌 key → 凭据文件
            stored_key = file_key
            rewrite = True
        p.api_key = stored_key
        if file_key != stored_key:
            rewrite = True
    if stored:
        _credentials_save(stored, cred_path)
    if rewrite and path.exists():
        save_config(path, cfg)                # 把内嵌 key 的旧配置重写为无密钥版
