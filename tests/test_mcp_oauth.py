"""MCP 的 OAuth 动态授权(C,v0.9.156)。

这一族钉四件事:①**配置怎么被读**(不合法的那几种要拿到一句说得清的话,尤其
"`${ENV}` 没设" —— `expandvars` 对没设的变量**原样留下**,第一版的判据是"展开后是空",
于是它**永远不触发**);②**换令牌**这条路的每种失败都给得出原因;③**缓存与刷新**
(不该每一步请求都去换一次令牌);④**401 只自愈一次**。
"""
from __future__ import annotations

import io
import json
import urllib.error

import pytest

from minicode import mcp, mcp_oauth


@pytest.fixture(autouse=True)
def _clean_cache():
    mcp_oauth.reset_cache()
    yield
    mcp_oauth.reset_cache()


class _Resp:
    """够用的 urlopen 返回值:被测代码只用 `read()` 与 `with`。"""

    def __init__(self, payload, headers=None):
        self._payload = (payload if isinstance(payload, bytes)
                         else json.dumps(payload).encode("utf-8"))
        self.headers = dict(headers or {})

    def read(self, n=-1):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _stub(monkeypatch, handler):
    """把 `urlopen` 换掉;`handler(req)` 回一个 `_Resp` 或者抛。记下每一次请求。"""
    calls = []

    def fake(req, timeout=None):
        calls.append(req)
        return handler(req)

    monkeypatch.setattr(mcp_oauth.urllib.request, "urlopen", fake)
    return calls


GOOD = {"type": "client_credentials", "token_url": "https://auth.example/token",
        "client_id": "cid", "client_secret": "sec", "scope": "mcp.read"}


# ------------------------------------------------------------------ 配置怎么读
def test_no_oauth_block_is_an_empty_dict():
    assert mcp_oauth.parse_oauth(None) == {}
    assert mcp_oauth.parse_oauth({"type": "client_credentials"})["problem"]


def test_a_good_client_credentials_block_is_normalized():
    got = mcp_oauth.parse_oauth(GOOD)
    assert "problem" not in got
    assert got["type"] == "client_credentials" and got["scope"] == "mcp.read"


@pytest.mark.parametrize("kind", ["authorization_code", "authorization-code",
                                  "implicit", "device_code"])
def test_the_reserved_flows_are_refused_with_a_reason(kind):
    """**拒绝,不是忽略。** `authorization_code` 要在你机器上开回调端口 + 拉浏览器,
    那是另外几笔账;而"配了没生效、只看见一个看不懂的 401"比"明说不支持"坏得多。"""
    got = mcp_oauth.parse_oauth({"type": kind, "token_url": "https://a/t"})
    assert "没有实现" in got["problem"] and "client_credentials" in got["problem"]


def test_a_non_http_token_url_is_refused():
    got = mcp_oauth.parse_oauth({**GOOD, "token_url": "file:///etc/passwd"})
    assert "不是 http(s)" in got["problem"]


def test_an_unset_env_var_is_caught(monkeypatch):
    """**这一支是冒烟时抓到的真问题。**

    `os.path.expandvars` 对**没设**的变量**原样留下**,所以"展开之后是空的"这个判据
    永远不触发 —— 会拿着字面量 `${MCP_CLIENT_ID}` 去换令牌,回来的是一句服务端的
    `invalid_client`,而真相是"你没设那个环境变量"。判据必须是"还剩没剩 `${`"。
    """
    monkeypatch.delenv("OA_TEST_MISSING", raising=False)
    got = mcp_oauth.parse_oauth({**GOOD, "client_id": "${OA_TEST_MISSING}"})
    assert "环境变量" in got["problem"], got


def test_a_set_env_var_is_expanded(monkeypatch):
    monkeypatch.setenv("OA_TEST_PRESENT", "real-id")
    got = mcp_oauth.parse_oauth({**GOOD, "client_id": "${OA_TEST_PRESENT}"})
    assert "problem" not in got and got["client_id"] == "real-id"


# ------------------------------------------------------------------ 换令牌
def test_the_token_request_is_what_we_think_it_is(monkeypatch):
    calls = _stub(monkeypatch, lambda r: _Resp({"access_token": "TK", "expires_in": 3600}))
    token, until = mcp_oauth._fetch(GOOD)
    assert token == "TK"
    body = calls[0].data.decode("utf-8")
    assert "grant_type=client_credentials" in body and "client_id=cid" in body
    assert "scope=mcp.read" in body
    assert until > 0


def test_a_missing_expires_in_falls_back_to_five_minutes(monkeypatch):
    """**不许当永久。** 一个不回 `expires_in` 的服务器会让那次令牌用到进程退出,
    而它可能五分钟就废了 —— 现象是"跑着跑着开始 401",且只在别人机器上复现。"""
    _stub(monkeypatch, lambda r: _Resp({"access_token": "TK"}))
    before = mcp_oauth.time.monotonic()
    _token, until = mcp_oauth._fetch(GOOD)
    assert until - before <= 300.0, "缺 expires_in 时必须按 300 秒算"


@pytest.mark.parametrize("payload,needle", [
    ({"nope": 1}, "没有 access_token"),
    (b"not json at all", "不是 JSON"),
])
def test_a_bad_token_response_says_what_was_wrong(monkeypatch, payload, needle):
    _stub(monkeypatch, lambda r: _Resp(payload))
    with pytest.raises(mcp_oauth.OAuthError) as e:
        mcp_oauth._fetch(GOOD)
    assert needle in str(e.value)


def test_an_oversized_token_response_is_refused(monkeypatch):
    _stub(monkeypatch, lambda r: _Resp(b"x" * (mcp_oauth.TOKEN_MAX + 10)))
    with pytest.raises(mcp_oauth.OAuthError) as e:
        mcp_oauth._fetch(GOOD)
    assert "超过" in str(e.value)


def test_an_http_error_carries_the_code_and_the_body(monkeypatch):
    def boom(req):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", None,
                                     io.BytesIO(b'{"error":"invalid_client"}'))
    _stub(monkeypatch, boom)
    with pytest.raises(mcp_oauth.OAuthError) as e:
        mcp_oauth._fetch(GOOD)
    assert "401" in str(e.value) and "invalid_client" in str(e.value)


def test_a_connection_failure_is_not_a_silent_empty_token(monkeypatch):
    def boom(req):
        raise OSError("网络不通")
    _stub(monkeypatch, boom)
    with pytest.raises(mcp_oauth.OAuthError) as e:
        mcp_oauth._fetch(GOOD)
    assert "OSError" in str(e.value)


# --------------------------------------------------------------- 缓存与刷新
def test_the_token_is_cached_until_it_nearly_expires(monkeypatch):
    calls = _stub(monkeypatch, lambda r: _Resp({"access_token": "TK", "expires_in": 3600}))
    assert mcp_oauth.bearer(GOOD, "s1") == "TK"
    assert mcp_oauth.bearer(GOOD, "s1") == "TK"
    assert len(calls) == 1, "每一步请求都去换一次令牌 = 每一步都可能多花 20 秒"


def test_a_short_lived_token_is_used_but_almost_immediately_stale(monkeypatch):
    """`expires_in` 比提前作废那 60 秒还短时:**这一次仍然用它**(不然一个 10 秒寿命的
    令牌永远用不上),但缓存里的有效窗口只留 1 秒的兜底 —— 而不是照 `10 - 60` 算成负数
    或者当它还能用 10 秒。
    """
    _stub(monkeypatch, lambda r: _Resp({"access_token": "TK", "expires_in": 10}))
    before = mcp_oauth.time.monotonic()
    assert mcp_oauth._fetch(GOOD)[0] == "TK"
    _token, until = mcp_oauth._fetch(GOOD)
    assert until - before <= 1.5, "10 秒寿命的令牌不该在缓存里活 10 秒"


def test_forget_forces_a_new_token(monkeypatch):
    calls = _stub(monkeypatch, lambda r: _Resp({"access_token": "TK", "expires_in": 3600}))
    mcp_oauth.bearer(GOOD, "s1")
    mcp_oauth.forget("s1")
    mcp_oauth.bearer(GOOD, "s1")
    assert len(calls) == 2


def test_two_servers_do_not_share_a_token(monkeypatch):
    seen = {"n": 0}

    def handler(req):
        seen["n"] += 1
        return _Resp({"access_token": f"TK{seen['n']}", "expires_in": 3600})

    _stub(monkeypatch, handler)
    assert mcp_oauth.bearer(GOOD, "s1") == "TK1"
    assert mcp_oauth.bearer(GOOD, "s2") == "TK2"


def test_a_broken_config_never_reaches_the_network(monkeypatch):
    calls = _stub(monkeypatch, lambda r: _Resp({"access_token": "TK"}))
    bad = mcp_oauth.parse_oauth({"type": "authorization_code"})
    assert mcp_oauth.auth_header(bad, "s1") == ""
    assert calls == []


# --------------------------------------------------- 接进 mcp.py 的那两处
def _cfg(**kw):
    base = dict(sid="srv", transport="http", url="https://host/mcp")
    base.update(kw)
    return mcp.McpServerConfig(**base)


def test_an_explicit_authorization_header_wins(monkeypatch):
    """`oauth` 是替用户省事的那条路,**不是覆盖他手写配置的那条路**。"""
    _stub(monkeypatch, lambda r: _Resp({"access_token": "TK", "expires_in": 3600}))
    cfg = _cfg(oauth=mcp_oauth.parse_oauth(GOOD),
               headers={"Authorization": "Bearer 我自己写的"})
    t = mcp.HttpTransport(cfg)
    mcp_oauth.bearer(cfg.oauth, cfg.sid)
    assert t._headers()["Authorization"] == "Bearer 我自己写的"


def test_the_oauth_header_is_injected_when_there_is_no_explicit_one(monkeypatch):
    _stub(monkeypatch, lambda r: _Resp({"access_token": "TK", "expires_in": 3600}))
    cfg = _cfg(oauth=mcp_oauth.parse_oauth(GOOD))
    t = mcp.HttpTransport(cfg)
    mcp_oauth.bearer(cfg.oauth, cfg.sid)
    assert t._headers()["Authorization"] == "Bearer TK"


def test_no_oauth_means_no_authorization_header():
    assert "Authorization" not in mcp.HttpTransport(_cfg())._headers()


def test_a_broken_config_makes_the_server_unconnectable(monkeypatch):
    """口径③:**拿不到令牌不许静默退化** —— 那台服务器就当连不上并说清是哪一步。"""
    cfg = _cfg(oauth=mcp_oauth.parse_oauth({"type": "authorization_code"}))
    with pytest.raises(mcp.McpError) as e:
        mcp.HttpTransport(cfg)._oauth_ready()
    assert "oauth 配置有问题" in str(e.value) and "没有实现" in str(e.value)


def test_a_token_fetch_failure_makes_the_server_unconnectable(monkeypatch):
    def boom(req):
        raise OSError("不通")
    _stub(monkeypatch, boom)
    cfg = _cfg(oauth=mcp_oauth.parse_oauth(GOOD))
    with pytest.raises(mcp.McpError) as e:
        mcp.HttpTransport(cfg)._oauth_ready()
    assert "取不到访问令牌" in str(e.value) and "OSError" in str(e.value)


def test_parse_servers_carries_oauth_and_refuses_it_on_stdio():
    got = mcp.parse_servers([
        {"id": "a", "transport": "http", "url": "https://h/mcp", "oauth": GOOD},
        {"id": "b", "command": "npx", "oauth": GOOD},
    ])
    by_sid = {c.sid: c for c in got}
    ok = by_sid["a"].oauth
    assert ok.get("type") == "client_credentials" and "problem" not in ok
    # stdio 没有可注入的请求头 —— 配了要说清该走 `env` 那条路,不许什么都不发生
    assert "stdio" in by_sid["b"].oauth["problem"], by_sid["b"].oauth


def test_round_tripping_the_config_never_writes_the_expanded_secret(monkeypatch):
    """**这一支是防"把密钥落进 config.json"的。**

    连接那一份用的是**展开过** `${ENV}` 的凭据,而 `servers_to_raw` 会把配置回写
    (管理工具 add/remove 那一路)。拿展开后的那份回写,`client_secret` 就从"只活在
    环境变量里"变成了明文躺在配置里 —— 而这个仓库对个人数据边界的要求正好相反。
    """
    monkeypatch.setenv("OA_TEST_SECRET", "s3cr3t-value")
    raw = {**GOOD, "client_secret": "${OA_TEST_SECRET}"}
    cfg = mcp.parse_servers([{"id": "a", "transport": "http",
                              "url": "https://h/mcp", "oauth": raw}])[0]
    assert cfg.oauth["client_secret"] == "s3cr3t-value", "连接那一份要展开"
    back = mcp.servers_to_raw([cfg])[0]["oauth"]
    assert back["client_secret"] == "${OA_TEST_SECRET}", "回写那一份不许展开"
    assert "s3cr3t-value" not in json.dumps(back, ensure_ascii=False)


def test_the_401_self_heal_retries_exactly_once(monkeypatch):
    """**一次自愈,不无限重试** —— 一个真的无权的服务器该被打两遍,而不是打到天亮。"""
    posts: list = []
    tokens: list = []

    def handler(req):
        url = getattr(req, "full_url", "")
        if "auth.example" in url:                  # 取令牌那一路
            tokens.append(req)
            return _Resp({"access_token": f"TK{len(tokens)}", "expires_in": 3600})
        posts.append(req)
        if len(posts) == 1:
            raise urllib.error.HTTPError(url, 401, "Unauthorized", None, io.BytesIO(b""))
        return _Resp({"jsonrpc": "2.0", "id": 7, "result": {"ok": True}})

    _stub(monkeypatch, handler)
    cfg = _cfg(oauth=mcp_oauth.parse_oauth(GOOD))
    t = mcp.HttpTransport(cfg)
    # 真实顺序:连接时先取一次令牌(`open()` 里那句),之后请求才发现它作废了。
    # 第一版直接 `_post` 跳过了这一步,于是"重试前重取"看起来像多取了一次。
    t._oauth_ready()
    assert len(tokens) == 1
    out = t._post({"jsonrpc": "2.0", "id": 7, "method": "ping"}, 5.0, want_id=7)
    assert out["result"] == {"ok": True}
    assert len(posts) == 2, "只许重试一次"
    assert len(tokens) == 2, "重试之前必须**重取**令牌(旧的已经作废了)"
    assert t._headers()["Authorization"] == "Bearer TK2", "第二次带的是新令牌"


def test_a_401_without_oauth_is_not_retried(monkeypatch):
    posts: list = []

    def handler(req):
        posts.append(req)
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", None,
                                     io.BytesIO(b""))
    _stub(monkeypatch, handler)
    with pytest.raises(mcp.McpError) as e:
        mcp.HttpTransport(_cfg())._post({"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                        5.0, want_id=1)
    assert "HTTP 401" in str(e.value) and len(posts) == 1


def test_the_token_never_appears_in_the_error_text(monkeypatch):
    """口径①:**令牌不进日志、不进任何回话。**

    这条路最容易漏的地方是错误文案 —— 把 `Authorization` 或整份请求 dump 进报错里,
    令牌就跟着进了日志与聊天区(而它恰恰是这一层唯一要保密的东西)。
    """
    def handler(req):
        if "auth.example" in getattr(req, "full_url", ""):
            return _Resp({"access_token": "SUPERSECRET123", "expires_in": 3600})
        raise urllib.error.HTTPError(req.full_url, 500, "boom", None,
                                     io.BytesIO(b"server exploded"))

    _stub(monkeypatch, handler)
    cfg = _cfg(oauth=mcp_oauth.parse_oauth(GOOD))
    t = mcp.HttpTransport(cfg)
    mcp_oauth.bearer(cfg.oauth, cfg.sid)          # 令牌确实在缓存里
    assert mcp_oauth.cached(cfg.sid) == "SUPERSECRET123"
    with pytest.raises(mcp.McpError) as e:
        t._post({"jsonrpc": "2.0", "id": 2, "method": "ping"}, 5.0, want_id=2)
    text = str(e.value)
    assert "SUPERSECRET123" not in text and "Bearer" not in text, text
