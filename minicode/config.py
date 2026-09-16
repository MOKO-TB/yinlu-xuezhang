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
    active = cfg.provider
    if active is None or active.id == BUILTIN_PROVIDER_ID:
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
    id: str
    name: str
    base_url: str
    api_key: str
    model: str

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "base_url": self.base_url,
                "api_key": self.api_key, "model": self.model}


@dataclass
class Config:
    providers: list = field(default_factory=list)  # list[Provider]
    active_provider_id: str = ""
    # confirm:变更前确认(查询放行、变更询问) / accept_edits:自动编辑(普通变更放行、敏感变更询问)
    # accept_all:自动放行(兼容旧配置) / full:完全访问(沙箱内非灾难性操作全放)
    permission_mode: str = "confirm"
    shell: str = "auto"                 # auto | bash | cmd | powershell
    max_output_chars: int = 20000       # 工具输出回填给模型前的截断长度
    request_timeout_seconds: int = 300  # 单次 LLM 请求超时
    context_window: int = 131072        # 模型上下文窗口(token),用于压缩触发判断
    compact_at_ratio: float = 0.7       # 估算 token 达到窗口的该比例时触发压缩
    # MCP 服务器原始配置(list[dict],由 minicode.mcp.parse_servers 解析;
    # 这里存原文以免 config 反向依赖 mcp 造成循环导入)
    mcp_servers: list = field(default_factory=list)
    # 沙箱策略原文(list[dict]/dict,由 minicode.sandbox.policy_from_raw 解析)
    sandbox: dict = field(default_factory=dict)
    # 当前模型是否支持看图(多模态):开启后上传的图片会以 data URL 发给模型
    vision: bool = False

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
        ))
    if not out:
        base = str(raw.get("base_url") or "").strip()
        if base:
            out.append(Provider(id="default", name="默认",
                                base_url=base.rstrip("/"),
                                api_key=str(raw.get("api_key") or ""),
                                model=str(raw.get("model") or DEFAULT_MODEL)))
    return out


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
        request_timeout_seconds=int(raw.get("request_timeout_seconds", 300)),
        context_window=int(raw.get("context_window", 131072)),
        compact_at_ratio=float(raw.get("compact_at_ratio", 0.7)),
        mcp_servers=raw.get("mcp_servers") if isinstance(raw.get("mcp_servers"), list) else [],
        sandbox=raw.get("sandbox") if isinstance(raw.get("sandbox"), dict) else {},
        vision=bool(raw.get("vision", False)),
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

    data = {
        "providers": [_safe(p) for p in cfg.providers],
        "active_provider": cfg.provider.id if cfg.provider else "",
        "permission_mode": cfg.permission_mode,
        "shell": cfg.shell,
        "max_output_chars": cfg.max_output_chars,
        "request_timeout_seconds": cfg.request_timeout_seconds,
        "context_window": cfg.context_window,
        "compact_at_ratio": cfg.compact_at_ratio,
    }
    if cfg.mcp_servers:
        data["mcp_servers"] = cfg.mcp_servers
    if cfg.sandbox:
        data["sandbox"] = cfg.sandbox
    if cfg.vision:
        data["vision"] = True
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
