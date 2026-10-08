"""思考档位:按模型名维护「能不能关 / 有哪几档 / 怎么写进请求参数」。

参照星瑶 Y-Code 的 core/reasoning_capabilities.py(公开仓库
gitee.com/feng-chenhao/xingyao-y-code)的形态,但只保留本项目
model_catalog.py 里登记过的模型与本项目实际会发请求的编码策略:

- ``default``  不动任何参数 —— 服务端用自己的默认思考行为;
- ``off``      显式关掉思考(能关的模型才提供这一档);
- ``low``/``high``  思考强度,写进 reasoning_effort / thinking 对象。

为什么要"真正的思考": DeepSeek 官方约定是
``extra_body.thinking = {"type": "enabled"|"disabled"}`` 控制开关,
``extra_body.reasoning_effort = low|high|max`` 控制强度(缺省开启、
effort 默认档随服务)。因此这里对 deepseek 家族统一用
``thinking_object_effort`` 策略;没登记的模型退回 hidden —— **界面上按钮照旧
显示但置灰**,并说明"跟随服务端默认",同时绝不往请求里塞思考参数,以免兼容
端点直接报 400(v0.9.21 之前是直接藏掉按钮,用户误以为安装包缺功能)。

未知模型不猜:能力表没有的一律 hidden。新增模型时在 REASONING_CAPABILITIES
里登记,或给 model_catalog 的同族加前缀规则。
"""
from __future__ import annotations

LABELS = {
    "default": "默认",
    "off": "关闭",
    "low": "低",
    "high": "高",
    "max": "最高",
}


class ReasoningCapability:
    """一个模型的思考能力:kind(展示形态)+ options(可选档位)+ 策略。

    `param` 只在策略是 `template` 时有值:用户在模型配置里写的那份映射,
    里面 `reasoningLevel` 是要被替换成实际档位的占位符。
    """

    __slots__ = ("kind", "options", "strategy", "param")

    def __init__(self, kind: str, options: tuple[str, ...], strategy: str,
                 param: str = ""):
        self.kind = kind            # effort(档位选择) / hidden(不显示)
        self.options = options      # ("default", "off", ...) 档位值
        self.strategy = strategy    # 请求参数编码策略(见 apply_reasoning_choice)
        self.param = param          # 模板串(template 策略下用)


_HIDDEN = ReasoningCapability("hidden", (), "")


def declared_capability(cfg) -> "ReasoningCapability | None":
    """用户在模型配置里自己登记的档位清单 → 一份能力。没登记就返回 None。

    这条是 §6 47 那两难里的"便宜那条":配置文件声明,不花额度、不猜端点。
    内置表仍然兜底 —— 没声明过的模型行为与今天逐字节一致。
    """
    levels = (cfg or {}).get("reasoning_levels")
    if not isinstance(levels, (list, tuple)) or not levels:
        return None
    opts = tuple(["default"] + [str(v).strip() for v in levels if str(v).strip()])
    return ReasoningCapability("effort", opts, "template",
                               str((cfg or {}).get("reasoning_param") or ""))


def get_capability(model_name: str, cfg=None) -> ReasoningCapability:
    """查能力:**用户在这个模型上声明的档位优先**,再精确登记,再前缀兜底,
    其余一律 hidden(不猜)。"""
    declared = declared_capability(cfg)
    if declared is not None:
        return declared
    key = normalize_model(model_name)
    cap = REASONING_CAPABILITIES.get(key)
    if cap is not None:
        return cap
    if key.startswith("deepseek"):
        return _DEEPSEEK
    return _HIDDEN

# model 名(小写精确匹配) -> 能力。来源与 model_catalog.MODEL_CATALOG 同步:
# 引路学长目前分发的模型以 deepseek-v4 家族为主(校园内置 deepseek-v4-flash,
# 用户可自配 deepseek-v4-pro 等)。deepseek 官方约定见模块 docstring。
_DEEPSEEK = ReasoningCapability(
    "effort", ("default", "off", "low", "high", "max"), "thinking_object_effort")

REASONING_CAPABILITIES: dict[str, ReasoningCapability] = {
    # deepseek 官方思考模式:thinking.type 开关 + reasoning_effort 强度
    "deepseek-flash":            _DEEPSEEK,
    "deepseek-v4-flash":         _DEEPSEEK,
    "deepseek-v4-flash-0731":    _DEEPSEEK,
    "deepseek-v4-pro":           ReasoningCapability(
        "effort", ("default", "off", "high", "max"), "thinking_object_effort"),
    "deepseek-v4-pro-0813":      _DEEPSEEK,
    "deepseek-chat":             ReasoningCapability("hidden", (), ""),
    "deepseek-reasoner":         ReasoningCapability(
        "fixed", (), "always_on"),   # 官方推理模型:固定思考,不可关
}


def _leaf(name: str) -> str:
    """取网关路径之后的裸模型名(与星瑶 normalize 的口径一致)。"""
    normalized = str(name or "").strip().lower()
    scheme, sep, suffix = normalized.partition("://")
    return (suffix if sep else normalized).split("/")[-1]


def normalize_model(name: str) -> str:
    return _leaf(name)


def reasoning_snapshot(model_name: str, choice: str = "default", cfg=None) -> dict:
    """给界面的能力快照:kind 三态 + options(中文标签)+ hint(为什么不能调)。

    三态语义(v0.9.22 改了第一态):
    - ``hidden``  这台服务上这个模型没登记过思考能力。**按钮照样显示但置灰**,
      鼠标上去说明"跟随服务端默认"。以前是直接藏起来,结果用户以为装好的
      版本少了功能(p1:安装版没有思考按钮),其实是免安装版正好用了
      deepseek 家族。按钮在,解释就在。
    - ``fixed``   模型固定思考,不可关(如 deepseek-reasoner)。
    - ``effort``  有档位可选。
    """
    cap = get_capability(model_name, cfg)
    fixed = cap.kind == "fixed"
    unknown = cap.kind == "hidden"
    choice = str(choice or "default")
    if fixed:
        choice = "on"          # 固定思考的模型给个"开启"展示位
    return {
        "kind": cap.kind,
        "visible": True,
        "disabled": fixed or unknown,
        "hint": (f"模型 {model_name or '未选择'} 未登记思考档位能力,"
                 "将跟随服务端默认行为") if unknown else
                ("该模型固定开启思考,不可调节" if fixed else ""),
        "choice": choice if choice in cap.options or fixed or choice == "default"
                  else "default",
        "options": [{"value": v, "label": LABELS.get(v, v)} for v in cap.options],
    }


def normalize_choice(model_name: str, choice: str, cfg=None) -> str:
    """非法/过期档位一律归回 default,绝不让界面积累的旧值打进请求。"""
    cap = get_capability(model_name, cfg)
    choice = str(choice or "").strip() or "default"
    if cap.kind == "fixed":
        return "default"       # 固定思考:不留 choice,由 always_on 策略处理
    return choice if choice in cap.options else "default"


def _render_template(param: str, choice: str) -> dict:
    """把用户写的那句映射渲成要发出去的键值。

    写法是 ZCode 那种模板串:`{ "reasoning_effort": reasoningLevel }` ——
    值位上放裸标识符 `reasoningLevel`,这里换成带引号的档位再按 JSON 解。
    解不出来就返回空 dict(**不发半截猜测**):写错模板的后果应该是"这一档
    没生效",而不是给网关送去一个畸形请求体。
    """
    import json
    body = str(param or "").replace("reasoningLevel", '"' + str(choice) + '"')
    try:
        parsed = json.loads(body)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def apply_reasoning_choice(kwargs: dict, model_name: str, choice: str,
                           cfg=None) -> dict:
    """把思考档位编码进 chat.completions 请求参数(原地修改并返回 kwargs)。

    - default → 不动任何参数(服务端默认行为,字节层面与旧请求完全一致);
    - off     → thinking.type=disabled,并移除可能残留的 reasoning_effort;
    - low/high/max → thinking.type=enabled + reasoning_effort=<档>;
    - 模型不在能力表(hidden) → 什么都不注入(未适配就别乱发)。

    strategy 说明:
    - thinking_object_effort: DeepSeek 官方约定(extra_body 里 thinking 对象);
    - always_on: 固定思考的模型(deepseek-reasoner),无需参数,直接返回。
    """
    cap = get_capability(model_name, cfg)
    choice = normalize_choice(model_name, choice, cfg)
    if cap.kind == "hidden" or choice == "default":
        return kwargs
    strategy = cap.strategy
    if strategy == "always_on":
        return kwargs
    if strategy == "template":
        # 用户自己声明的档位与映射:渲出来的键一律走 extra_body —— openai SDK
        # 会把它并进请求体,于是"顶层参数"与"私有参数"两种网关都能接得住,
        # 而不会因为 SDK 不认某个陌生顶层关键字直接抛。
        rendered = _render_template(cap.param, choice)
        if rendered:
            extra = kwargs.setdefault("extra_body", {}) or kwargs["extra_body"]
            extra.update(rendered)
            kwargs["extra_body"] = extra
        return kwargs
    if strategy == "thinking_object_effort":
        extra = kwargs.setdefault("extra_body", {}) or kwargs["extra_body"]
        if choice == "off":
            extra["thinking"] = {"type": "disabled"}
            extra.pop("reasoning_effort", None)
        else:
            extra["thinking"] = {"type": "enabled"}
            extra["reasoning_effort"] = choice
    return kwargs
