"""配置加载与校验:多供应商 API Key(providers)+ 运行参数。

兼容两代格式:
- v0.2 扁平格式 {base_url, api_key, model, ...} → 合成单个 default 供应商
- v0.3 数组格式 {"providers": [{id,name,base_url,api_key,model}], "active_provider": ...}
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from minicode.paths import CONFIG_PATH, EXAMPLE_CONFIG_PATH

API_KEY_PLACEHOLDERS = {"", "YOUR_API_KEY", "在这里填入你的 API Key"}

DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_MODEL = "glm-4.6"


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
    permission_mode: str = "confirm"    # confirm: 变更类工具逐次确认; accept_all: 自动放行
    shell: str = "auto"                 # auto | bash | cmd | powershell
    max_output_chars: int = 20000       # 工具输出回填给模型前的截断长度
    request_timeout_seconds: int = 300  # 单次 LLM 请求超时
    context_window: int = 131072        # 模型上下文窗口(token),用于压缩触发判断
    compact_at_ratio: float = 0.7       # 估算 token 达到窗口的该比例时触发压缩

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
    cfg = Config(
        providers=providers,
        active_provider_id=active,
        permission_mode=raw.get("permission_mode", "confirm"),
        shell=raw.get("shell", "auto"),
        max_output_chars=int(raw.get("max_output_chars", 20000)),
        request_timeout_seconds=int(raw.get("request_timeout_seconds", 300)),
        context_window=int(raw.get("context_window", 131072)),
        compact_at_ratio=float(raw.get("compact_at_ratio", 0.7)),
    )
    if providers and not cfg.provider:      # active 指向不存在的供应商
        cfg.active_provider_id = providers[0].id
    return cfg


def load_config(path: Path = CONFIG_PATH) -> Config:
    """CLI 用:配置缺失/为空直接提示退出。"""
    if not path.exists():
        if EXAMPLE_CONFIG_PATH.exists():
            shutil.copy(EXAMPLE_CONFIG_PATH, path)
            print(f"已从模板生成 {path}")
            print("请编辑该文件填入 api_key(以及 base_url / model)后重新启动。")
        else:
            print(f"缺少配置文件: {path}")
        sys.exit(1)

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.exit(f"config.json 不是有效 JSON: {e}")
    cfg = _config_from_raw(raw if isinstance(raw, dict) else {})
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
        cfg.active_provider_id = "zhipu"
    save_config(path, cfg)   # 旧格式迁移为新格式,GUI 增删改查都基于新格式
    return cfg


def save_config(path: Path, cfg: Config) -> None:
    data = {
        "providers": [p.to_dict() for p in cfg.providers],
        "active_provider": cfg.provider.id if cfg.provider else "",
        "permission_mode": cfg.permission_mode,
        "shell": cfg.shell,
        "max_output_chars": cfg.max_output_chars,
        "request_timeout_seconds": cfg.request_timeout_seconds,
        "context_window": cfg.context_window,
        "compact_at_ratio": cfg.compact_at_ratio,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
