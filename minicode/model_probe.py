"""运行时探测模型能力:上下文窗口有多大 + 是否支持看图。

为什么要探测:模型服务(尤其校内自建网关)不会把「窗口多大」「能不能看图」
写进 `/v1/models` —— 实测校内服务只回一个 id。静态目录
(`model_catalog.MODEL_CATALOG`)只能人工维护,学校换一次模型就得改一次代码,
而且很容易填错(比如把 320K 窗口写成 1M,压缩就不会及时触发)。这两个问题
其实都能直接问服务本身,且都有确定的、可解析的答复形态:

- **上下文窗口**:发一条 `max_tokens` 明显超限的请求。服务端在参数校验阶段
  就会拒绝,并把真实上限写进错误文本,形如
      max_tokens=X cannot be greater than max_model_len=max_total_tokens=N
      This model's maximum context length is N tokens
  N 即窗口。请求被拒 = 没有真的生成 token,探测本身几乎不花钱。
- **视觉**:发一张 1x1 图片。不支持多模态的服务端会明确回
      xxx is not a multimodal model
  能正常返回,就是支持。

结果落 `model_caps.json`(跟随 exe / 项目根),并把结论发布给
`model_catalog.set_detected()` —— 任务面板的能力标签、窗口上限,以及压缩
触发阈值都会自动跟上。探测跑在后台线程里,任何失败都只是「这次没测出来」,
绝不阻塞或打断正常对话。
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
import time
from pathlib import Path

from minicode.paths import app_path

CACHE_NAME = "model_caps.json"
CACHE_TTL_SECONDS = 7 * 24 * 3600      # 能力很少变:一周问一次足够

# 探测用的 1x1 PNG(64 字节),只用来问「能不能收图」,不关心内容
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/"
    "q842iQAAAABJRU5ErkJggg==")

# 窗口探测的候选 max_tokens:从小往大试。第一个超过窗口的值就会把真实上限
# 带回来。故意不从"一个巨大的值"起步 —— 部分网关会先按 max_tokens 预扣费,
# 值太大就直接报「额度不足」,反而问不出窗口。
_WINDOW_LADDER = (1_048_576, 4_194_304, 16_777_216)

_WINDOW_PATTERNS = (
    re.compile(r"max_model_len\s*=\s*max_total_tokens\s*=\s*(\d+)"),
    re.compile(r"maximum context length is\s*(\d+)\s*tokens", re.I),
    re.compile(r"max_model_len\D{0,12}?(\d{4,})", re.I),
    re.compile(r"(?:context length|上下文长度)\D{0,12}?(\d{4,})", re.I),
)

# 服务端拒绝收图时的典型措辞(命中即判定"不支持视觉")
_NO_VISION_HINTS = (
    "not a multimodal", "multimodal", "does not support image",
    "不支持图片", "不是多模态", "no vision",
)

# 鉴权/额度之类的错误:说明这次问不出答案,不必再换值重试
_ABORT_HINTS = ("insufficient_user_quota", "预扣费", "unauthorized", "invalid api key",
                "invalid_api_key", "authentication")


# --------------------------------------------------------------- HTTP

def _request(method: str, base_url: str, path: str, payload: dict | None,
             api_key: str, timeout: float) -> tuple[int, str]:
    """发一次请求,返回 (status, text)。任何网络异常都收成 (0, 描述)。

    内网地址必须直连:系统代理(Clash 等)会拦成 502 —— 与 llm.py 同款处理。
    """
    url = base_url.rstrip("/") + path
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    try:
        from minicode.llm import _is_direct_host
        from urllib.parse import urlparse
        trust_env = not _is_direct_host(urlparse(url).hostname or "")
    except Exception:
        trust_env = True
    try:
        try:
            import httpx2 as httpx
        except ImportError:
            import httpx
        with httpx.Client(trust_env=trust_env,
                          timeout=httpx.Timeout(timeout, connect=min(timeout, 10.0))) as c:
            r = c.request(method, url, content=body, headers=headers)
            return r.status_code, r.text
    except Exception as e:          # 网络不通/超时/没装 httpx:都不该影响调用方
        return 0, f"{type(e).__name__}: {e}"


def _post(base_url: str, path: str, payload: dict, api_key: str,
          timeout: float) -> tuple[int, str]:
    """POST 一次,返回 (status, text)。"""
    return _request("POST", base_url, path, payload, api_key, timeout)


def list_models(base_url: str, api_key: str, timeout: float = 15.0) -> tuple[int, list[str]]:
    """问服务端"你有哪些模型"(GET /models)。

    返回 (status, 模型名列表)。**故意不猜**:只有真拿到 `data[].id` 才回列表,
    鉴权失败/超时/格式不对一律回空列表,由调用方把 status 讲给用户听。
    """
    if not base_url:
        return 0, []
    status, text = _request("GET", base_url, "/models", None, api_key, timeout)
    if status != 200:
        return status, []
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return status, []
    items = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return status, []
    names: list[str] = []
    for item in items:
        if isinstance(item, dict):
            name = str(item.get("id") or item.get("name") or "").strip()
        else:
            name = str(item or "").strip()
        if name and name not in names:
            names.append(name)
    return status, names


# --------------------------------------------------------------- 解析

LIVENESS_INTERVAL_SECONDS = 60.0
LIVENESS_ATTEMPTS = 10        # 最长盯约 10 分钟:校园网/内网网关抖动通常几十秒就回来


def watch_liveness(probe, *, interval: float = LIVENESS_INTERVAL_SECONDS,
                   attempts: int = LIVENESS_ATTEMPTS, sleep=time.sleep) -> dict:
    """模型调不通之后,盯一盯它活没活。返回 {"recovered": bool, "tried": int}。

    为什么要有这条:`llm.py` 重试两次就抛错,界面上只剩一句「模型调用失败」。服务
    恢复了没人说,用户以为得自己重启程序;这也是"死胡同"那半条。探活只问 `GET /models`
    —— 不发对话请求,不动用户的 Key 额度。`probe`/`sleep` 都可注入,测试不真等 60 秒。
    """
    for i in range(1, max(1, int(attempts)) + 1):
        try:
            alive = bool(probe())
        except Exception:
            alive = False          # 探测自己出错 = 还没活,不该把看门狗弄死
        if alive:
            return {"recovered": True, "tried": i}
        if i < attempts:
            sleep(interval)
    return {"recovered": False, "tried": max(1, int(attempts))}


def _parse_window(text: str) -> int | None:
    """从服务端错误文本里抠出上下文窗口。抠不到返回 None(不猜数字)。"""
    for pat in _WINDOW_PATTERNS:
        m = pat.search(text or "")
        if not m:
            continue
        try:
            value = int(m.group(1))
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def _looks_like_abort(text: str) -> bool:
    low = (text or "").lower()
    return any(h in low for h in _ABORT_HINTS)


# --------------------------------------------------------------- 探测

def probe_context_window(base_url: str, api_key: str, model: str,
                         timeout: float = 30.0) -> int | None:
    """问出模型的上下文窗口;问不出来返回 None(交给静态目录兜底)。"""
    if not base_url or not model:
        return None
    for max_tokens in _WINDOW_LADDER:
        status, text = _post(base_url, "/chat/completions",
                             {"model": model, "max_tokens": max_tokens,
                              "messages": [{"role": "user", "content": "hi"}]},
                             api_key, timeout)
        window = _parse_window(text)
        if window:
            return window
        if status == 200:
            continue        # 没超限 ⇒ 窗口比这一档还大,抬高一档再问
        return None         # 其他错误(额度/鉴权/网络):这次问不出来
    return None


def probe_vision(base_url: str, api_key: str, model: str,
                 timeout: float = 30.0) -> bool | None:
    """问出模型能否收图:True 能 / False 不能 / None 说不准。"""
    if not base_url or not model:
        return None
    data_url = "data:image/png;base64," + base64.b64encode(_TINY_PNG).decode("ascii")
    status, text = _post(base_url, "/chat/completions", {
        "model": model, "max_tokens": 8,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "what color is this image?"},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]}],
    }, api_key, timeout)
    if status == 200:
        return True
    low = (text or "").lower()
    if any(h in low for h in _NO_VISION_HINTS):
        return False
    return None             # 鉴权失败/超时之类:不妄下结论


def detect(base_url: str, api_key: str, model: str,
           timeout: float = 30.0) -> dict:
    """问一轮,返回一条可落盘的结论。"""
    return {
        "context_window": probe_context_window(base_url, api_key, model, timeout),
        "supports_vision": probe_vision(base_url, api_key, model, timeout),
        "endpoint": endpoint_fingerprint(base_url),
        "checked_at": time.time(),
    }


# 把服务端的 HTTP 状态翻成用户看得懂的一句话。前端「测试」按钮直接展示它。
def explain_status(status: int, text: str) -> str:
    if status == 0:
        return "连不上服务:请检查接口地址与网络(内网地址需要连着校园网)"
    if status in (401, 403):
        return f"API Key 无效或没有权限(HTTP {status})"
    if status == 404:
        return "接口路径不对(HTTP 404):确认地址末尾要不要带 /v1"
    if status == 429:
        return "被限流了(HTTP 429):稍后再试,或检查账户额度"
    if 400 <= status < 500:
        return f"服务端拒绝了这次请求(HTTP {status}):{_snippet(text)}"
    if status >= 500:
        return f"服务端报错(HTTP {status}):{_snippet(text)}"
    return f"服务端返回 HTTP {status}:{_snippet(text)}"


def _snippet(text: str, limit: int = 120) -> str:
    """从错误正文里抠一句能看的(优先取 JSON 的 message 字段)。"""
    raw = (text or "").strip()
    if not raw:
        return "(无内容)"
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict) and err.get("message"):
                raw = str(err["message"])
            elif isinstance(data.get("message"), str):
                raw = data["message"]
            elif isinstance(data.get("detail"), str):
                raw = data["detail"]
    except json.JSONDecodeError:
        pass
    return raw.replace("\n", " ")[:limit]


def test_model(base_url: str, api_key: str, model: str,
               timeout: float = 20.0) -> dict:
    """「测试」按钮:真发一条最小请求,顺便问出窗口与视觉能力。

    和 `detect()` 的区别是**先确认连通与鉴权**,再把结论讲成一句话 —— 只回
    "窗口 None" 的话,用户根本分不清是 key 错了、地址错了还是模型名写错了。

    返回 {"ok", "msg", "status", "context_window", "supports_vision"}。
    只探测、不落盘(要不要把结论记下来由调用方决定)。
    """
    if not base_url:
        return {"ok": False, "msg": "还没填接口地址", "status": 0,
                "context_window": None, "supports_vision": None}
    if not model:
        return {"ok": False, "msg": "还没填模型名", "status": 0,
                "context_window": None, "supports_vision": None}
    status, text = _post(base_url, "/chat/completions", {
        "model": model, "max_tokens": 8,
        "messages": [{"role": "user", "content": "hi"}],
    }, api_key, timeout)
    if status != 200:
        return {"ok": False, "msg": explain_status(status, text), "status": status,
                "context_window": None, "supports_vision": None}
    window = probe_context_window(base_url, api_key, model, timeout)
    vision = probe_vision(base_url, api_key, model, timeout)
    parts = ["已连通"]
    if window:
        parts.append(f"窗口 {window:,} tokens")
    if vision is not None:
        parts.append("支持看图" if vision else "纯文本")
    return {"ok": True, "msg": " · ".join(parts), "status": status,
            "context_window": window, "supports_vision": vision}


def remember(model: str, entry: dict) -> None:
    """把一次手动测试的结论记下来(缓存 + 模型能力表)。

    与 `ensure_async` 同款规则:什么都没问出来就不落盘,免得把一次失败
    当成长期结论。
    """
    if not isinstance(entry, dict):
        return
    if entry.get("context_window") is None and entry.get("supports_vision") is None:
        return
    cache = load_cache()
    cache[_key(model)] = entry
    save_cache(cache)
    publish(model, entry)


def endpoint_fingerprint(base_url: str) -> str:
    """服务地址的指纹。

    缓存要能识别"同名模型换了服务",但**不能把地址本身写进缓存文件** ——
    缓存就落在 exe 旁边,而校内服务地址是内网 IP,不该出现在用户能打开的
    文件里。哈希足以判断"还是不是同一个服务",又不泄露地址。
    """
    return hashlib.sha256((base_url or "").strip().encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------- 缓存

def _cache_path() -> Path:
    return app_path(CACHE_NAME)


def _key(model: str) -> str:
    return (model or "").strip().lower()


def load_cache() -> dict:
    try:
        raw = json.loads(_cache_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def save_cache(cache: dict) -> None:
    try:
        _cache_path().write_text(
            json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass            # 落盘失败只影响下次启动要重测一次,不值得报错


def cached(model: str, base_url: str = "", *,
           ttl: int = CACHE_TTL_SECONDS, now: float | None = None) -> dict | None:
    """取一条还没过期的探测结论;没有/过期/换了服务地址都返回 None。"""
    entry = load_cache().get(_key(model))
    if not isinstance(entry, dict):
        return None
    if base_url and entry.get("endpoint") and \
            entry["endpoint"] != endpoint_fingerprint(base_url):
        return None         # 同名模型换了服务:旧结论作废,重新问
    ts = entry.get("checked_at")
    if ttl and isinstance(ts, (int, float)) and (now or time.time()) - ts > ttl:
        return None
    return entry


# --------------------------------------------------------------- 发布 / 触发

def publish(model: str, entry: dict) -> None:
    """把结论交给 model_catalog:任务面板与压缩阈值都读它。"""
    from minicode import model_catalog
    if not isinstance(entry, dict):
        return
    model_catalog.set_detected(model,
                               context_window=entry.get("context_window"),
                               supports_vision=entry.get("supports_vision"))


_STARTED: set[tuple[str, str]] = set()
_LOCK = threading.Lock()


def reset() -> None:
    """清掉「本进程已发起过」的记录(测试用)。"""
    with _LOCK:
        _STARTED.clear()


def ensure_async(base_url: str, api_key: str, model: str, *,
                 timeout: float = 30.0, background: bool = True) -> dict | None:
    """有缓存就直接生效;没有(或过期)就起后台线程问一次。立即返回,不阻塞。

    同一个 (服务地址, 模型) 在一个进程里只问一次。
    """
    if not base_url or not model:
        return None
    entry = cached(model, base_url)
    if entry:
        publish(model, entry)
        return entry
    key = (base_url, model)
    with _LOCK:
        if key in _STARTED:
            return None
        _STARTED.add(key)

    def run() -> None:
        try:
            result = detect(base_url, api_key, model, timeout=timeout)
            if (result.get("context_window") is None
                    and result.get("supports_vision") is None):
                return          # 什么都没问到:不落盘,免得把一次失败当成结论
            cache = load_cache()
            cache[_key(model)] = result
            save_cache(cache)
            publish(model, result)
        except Exception:
            pass                # 后台线程绝不把异常抛给主流程

    if not background:
        run()
        return cached(model, base_url)
    threading.Thread(target=run, name="model-probe", daemon=True).start()
    return None
