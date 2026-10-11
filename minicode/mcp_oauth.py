"""MCP 的 OAuth 动态授权(C,v0.9.156)。

**它补的是哪一格**:MCP 服务器今天已经能带 `headers`,而值支持 `${ENV}` 展开 ——
所以"静态密钥"那条路是通的。缺的是**动态取令牌**:令牌有有效期、要按 scope 申请、
过期要换新,而让用户自己去申请一个长期令牌、手动填进配置、过期了再手动换,
恰恰是这条路最容易出错的地方。

**这一版只做 `client_credentials`**(机器对机器,不需要人参与):配置里写一段 `oauth`,
连接之前自动换一个 `access_token` 注进 `Authorization`;按 `expires_in` 提前作废,
过期自动再换;服务器回 **401** 时重取一次再试一次(与 `llm` 那边"400 摘字段重发"
同一个形状:**一次自愈,不无限重试**)。

**`authorization_code` 明确不做,而且是拒绝而不是忽略**(见 `RESERVED_TYPES`):
那条流要在你机器上**开一个回调端口**、**拉起浏览器**、把重定向接回来、还要管
PKCE 与 refresh token —— 那是另外几笔账(开端口与拉起浏览器各自都是一个决定)。
而"配了没生效、只看见一个看不懂的 401"比"明说不支持"坏得多。

**三条写死的口径**:
①令牌**不进日志、不进任何回话** —— 它只活在 header 与这个模块的内存缓存里;
②`token_url` 只认 http(s)(与 `mcp._usable_url` 同一道围栏);
③**拿不到令牌不许静默退化** —— 那台服务器就当连不上,并说清是哪一步失败,
而不是"没带头去请求、然后报一个看不懂的 401"。
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

# 认得的流。`authorization_code` 在**拒绝**名单里而不是被忽略:
# 写进配置里要得到一句"不支持,为什么",而不是静静地什么都不发生。
SUPPORTED_TYPES = ("client_credentials",)
RESERVED_TYPES = ("authorization_code", "authorization-code", "implicit",
                  "device_code", "device-code")

# 提前多久把令牌作废。取 60 秒:网络慢的时候那一次请求仍在有效期内,
# 而 60 秒又远小于任何真实令牌的有效期。
EXPIRY_SKEW = 60
DEFAULT_TIMEOUT = 20.0
# 令牌响应与令牌本身的长度上限(兜底):服务器回一个几 MB 的字符串时,
# 不许把它塞进 header 再发出去。
TOKEN_MAX = 8192


class OAuthError(Exception):
    """取令牌失败。**带上原因**,调用方如实转述(不许只说"鉴权失败")。"""


def _expand(value) -> str:
    """展开 `${ENV}`。

    ⚠ **没设的变量会原样留下**(`os.path.expandvars` 的行为:它只替换认得的名字),
    所以"展开之后是空的"这个判据**永远不触发** —— 剩下的那个字面量 `${VAR}` 会被当成
    凭据发出去。调用方必须再判一次"还剩没剩 `${`"(见 `parse_oauth`)。
    (顺带一条同源的既有实情:`mcp._headers` 里那些静态 `${ENV}` 头也是这个行为 ——
    没设时那一条头就带着字面量发出去了,回来的是一句看不懂的 401。**这一版没动它**
    —— 改了会让"故意写一个字面量"的既有配置变样,而正确的那条路现在是 `oauth`。)
    """
    return os.path.expandvars(str(value or "").strip())


def _unexpanded(text: str) -> bool:
    """展开之后还留着 `${…}` = 那个环境变量没设。"""
    return "${" in text


def parse_oauth(raw) -> dict:
    """把配置里那段 `oauth` 规范化;不合法就回一个带 `problem` 的字典。

    为什么不抛异常:`parse_servers` 的约定是"非法项跳过,配置永远不该让应用起不来",
    而这里要比它**多说一句**(那一台服务器带着一段我不认或没实现的 oauth)—— 所以
    回一个 `problem`,由调用方在连接时如实报出来。
    """
    out: dict = {}
    if not isinstance(raw, dict):
        return out
    kind = str(raw.get("type") or "").strip().lower()
    if kind in RESERVED_TYPES:
        out["problem"] = (
            f"oauth.type「{kind}」本项目**没有实现**:它要在你机器上开一个回调端口、"
            "拉起浏览器把重定向接回来(还要管 PKCE 与 refresh token)。"
            "两条替代路:①改成 `client_credentials`;②自己换好令牌,用 `headers` 里的"
            "`${ENV}` 静态带上。**它没有被静默忽略** —— 说出来就是为了让你知道该换哪条。")
        return out
    if kind not in SUPPORTED_TYPES:
        out["problem"] = (f"oauth.type「{kind or '(空)'}」不认得 —— 现在只支持 "
                          + "、".join(SUPPORTED_TYPES) + "。")
        return out
    out["type"] = kind
    token_url = str(raw.get("token_url") or raw.get("tokenUrl") or "").strip()
    try:
        parts = urllib.parse.urlsplit(token_url)
    except ValueError:
        parts = None
    if parts is None or parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        out["problem"] = f"oauth.token_url「{token_url or '(空)'}」不是 http(s) 的绝对地址。"
        return out
    out["token_url"] = token_url
    out["client_id"] = _expand(raw.get("client_id") or raw.get("clientId"))
    out["client_secret"] = _expand(raw.get("client_secret") or raw.get("clientSecret"))
    out["scope"] = str(raw.get("scope") or "").strip()
    out["audience"] = str(raw.get("audience") or "").strip()
    if (not out["client_id"] or not out["client_secret"]
            or _unexpanded(out["client_id"]) or _unexpanded(out["client_secret"])):
        # **空值 / 没展开一定要在这里说出来**:没设的 `${ENV}` 会**原样留下**,于是会
        # 拿着字面量 `${MCP_CLIENT_ID}` 去换令牌,回来的是一句服务端的 `invalid_client`
        # —— 那句与"你没设那个环境变量"差着十万八千里,而后者才是真相。
        out["problem"] = ("oauth.client_id / client_secret 是空的、或者还有没展开的 "
                          "`${…}` —— 配置里那个 `${ENV}` 环境变量设了吗?")
        return out
    return out


def _fetch(cfg: dict, timeout: float = DEFAULT_TIMEOUT) -> tuple[str, float]:
    """换一个令牌,回 `(令牌, 有效到什么时候)`(单调钟读数)。

    `expires_in` 缺了或不是正数时**按 300 秒算**:当成永久的话,一个不回
    `expires_in` 的服务器会让这一次令牌用到进程退出,而它可能五分钟就废了 ——
    表现是"跑着跑着开始 401",而且只在别人机器上复现。
    """
    data = {"grant_type": "client_credentials",
            "client_id": cfg.get("client_id", ""),
            "client_secret": cfg.get("client_secret", "")}
    if cfg.get("scope"):
        data["scope"] = cfg["scope"]
    if cfg.get("audience"):
        data["audience"] = cfg["audience"]
    req = urllib.request.Request(
        cfg["token_url"], data=urllib.parse.urlencode(data).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = r.read(TOKEN_MAX + 1)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read(400).decode("utf-8", "replace").strip()
        except Exception:
            pass
        raise OAuthError(f"取令牌被拒:HTTP {e.code}"
                         + (f" —— {detail}" if detail else "")) from e
    except Exception as e:                      # 连不上/超时/SSL:都是"没拿到"
        raise OAuthError(f"取令牌失败:{type(e).__name__}: {e}") from e
    if len(payload) > TOKEN_MAX:
        raise OAuthError(f"令牌响应超过 {TOKEN_MAX} 字节,拒绝使用")
    try:
        obj = json.loads(payload.decode("utf-8"))
    except Exception as e:
        raise OAuthError(f"令牌响应不是 JSON({type(e).__name__})") from e
    token = str((obj or {}).get("access_token") or "").strip()
    if not token:
        raise OAuthError("令牌响应里没有 access_token")
    try:
        ttl = float((obj or {}).get("expires_in") or 0)
    except (TypeError, ValueError):
        ttl = 0.0
    if ttl <= 0:
        ttl = 300.0
    return token, time.monotonic() + max(1.0, ttl - EXPIRY_SKEW)


_LOCK = threading.Lock()
# sid → (令牌, 有效到什么时候)。**进程内**缓存:令牌是短命的,落盘反而多一个
# 要清、要保密的文件(个人数据那四处登记税也不该为它交)。
_CACHE: dict[str, tuple[str, float]] = {}


def cached(sid: str) -> str:
    """缓存里那个还没过期的令牌;没有就回空串。"""
    with _LOCK:
        item = _CACHE.get(sid)
    if item and item[1] > time.monotonic():
        return item[0]
    return ""


def forget(sid: str) -> None:
    """把这个服务器的令牌丢掉(401 之后重取前要做的第一件事)。"""
    with _LOCK:
        _CACHE.pop(sid, None)


def bearer(cfg: dict, sid: str, *, force: bool = False) -> str:
    """拿到一个可用令牌(带缓存);**拿不到就抛 `OAuthError`**。

    抛而不是回空串:回空串的话调用方会"没带头去请求",而服务器那边回的是一个
    看不懂的 401 —— 那正是这一条存在的理由(口径③)。
    """
    if not force:
        got = cached(sid)
        if got:
            return got
    token, until = _fetch(cfg)
    with _LOCK:
        _CACHE[sid] = (token, until)
    return token


def auth_header(cfg: dict, sid: str, *, force: bool = False) -> str:
    """`Authorization` 那个头的值;没配 oauth(或有 problem)就回空串。"""
    if not cfg or cfg.get("problem"):
        return ""
    return "Bearer " + bearer(cfg, sid, force=force)


def reset_cache() -> None:
    """清空进程内缓存(测试与"改了配置重连"用)。"""
    with _LOCK:
        _CACHE.clear()
