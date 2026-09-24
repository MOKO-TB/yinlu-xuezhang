"""模型能力探测测试:窗口多大 + 能否看图。

样本错误串是 2026-09-16 从校内服务(http://10.223.24.16:8080/v1,
deepseek-v4-flash)真实抓下来的 —— 探测的解析规则就是照着它写的,所以用
原文而不是编造的文本做回归。
"""
import time

from minicode import model_catalog, model_probe

# 真实服务在 max_tokens 超限时的两种措辞(原文摘录)
REAL_OVER_LIMIT = (
    '{"error":{"message":"max_tokens=1048576 cannot be greater than '
    'max_model_len=max_total_tokens=327680. Please request fewer output tokens. '
    '(parameter=max_tokens, value=1048576)","type":"BadRequestError"}}'
)
REAL_OVER_LIMIT_ALT = (
    '{"error":{"message":"This model\'s maximum context length is 327680 tokens. '
    'However, you requested 327680 output tokens and your prompt contains 54 '
    'characters","type":"BadRequestError"}}'
)
REAL_NO_VISION = (
    '{"error":{"message":"deepseek-v4-flash is not a multimodal model",'
    '"type":"BadRequestError","param":"","code":400}}'
)
REAL_QUOTA = (
    '{"error":{"message":"预扣费额度失败, 用户剩余额度: ＄187.08, 需要预扣费额度: '
    '＄1000.00","type":"new_api_error","code":"insufficient_user_quota"}}'
)


# --------------------------------------------------------------- 解析

def test_parse_window_from_real_error_text():
    assert model_probe._parse_window(REAL_OVER_LIMIT) == 327680
    assert model_probe._parse_window(REAL_OVER_LIMIT_ALT) == 327680


def test_parse_window_refuses_to_guess():
    """抠不到就不给数字 —— 宁可用静态目录兜底,也不编一个窗口出来。"""
    assert model_probe._parse_window("upstream exploded") is None
    assert model_probe._parse_window("") is None
    assert model_probe._parse_window("max_tokens too large") is None


# --------------------------------------------------------------- 窗口探测

def test_probe_window_reads_limit_from_rejection(monkeypatch):
    calls = []

    def fake_post(base_url, path, payload, api_key, timeout):
        calls.append(payload["max_tokens"])
        return 400, REAL_OVER_LIMIT

    monkeypatch.setattr(model_probe, "_post", fake_post)
    assert model_probe.probe_context_window("http://x/v1", "k", "m") == 327680
    assert calls == [1_048_576]        # 第一档就被拒 ⇒ 一次就问出来了


def test_probe_window_walks_up_when_not_rejected(monkeypatch):
    """服务端没拒绝 ⇒ 窗口比这一档大,抬高一档再问。"""
    seen = []

    def fake_post(base_url, path, payload, api_key, timeout):
        seen.append(payload["max_tokens"])
        if payload["max_tokens"] >= 4_194_304:
            return 400, REAL_OVER_LIMIT
        return 200, "{}"

    monkeypatch.setattr(model_probe, "_post", fake_post)
    assert model_probe.probe_context_window("http://x/v1", "k", "m") == 327680
    assert seen == [1_048_576, 4_194_304]


def test_probe_window_gives_up_on_quota_error(monkeypatch):
    """额度/鉴权类错误换个值也一样被拒 —— 不该继续试,直接认输。"""
    seen = []

    def fake_post(base_url, path, payload, api_key, timeout):
        seen.append(payload["max_tokens"])
        return 403, REAL_QUOTA

    monkeypatch.setattr(model_probe, "_post", fake_post)
    assert model_probe.probe_context_window("http://x/v1", "k", "m") is None
    assert len(seen) == 1


def test_probe_window_skips_without_endpoint_or_model():
    assert model_probe.probe_context_window("", "k", "m") is None
    assert model_probe.probe_context_window("http://x/v1", "k", "") is None


# --------------------------------------------------------------- 视觉探测

def test_probe_vision_false_on_multimodal_rejection(monkeypatch):
    monkeypatch.setattr(model_probe, "_post",
                        lambda *a, **k: (400, REAL_NO_VISION))
    assert model_probe.probe_vision("http://x/v1", "k", "m") is False


def test_probe_vision_true_when_accepted(monkeypatch):
    monkeypatch.setattr(model_probe, "_post", lambda *a, **k: (200, "{}"))
    assert model_probe.probe_vision("http://x/v1", "k", "m") is True


def test_probe_vision_undecided_on_auth_error(monkeypatch):
    """鉴权失败不能算"不支持视觉" —— 说不准就说不准。"""
    monkeypatch.setattr(model_probe, "_post", lambda *a, **k: (401, "unauthorized"))
    assert model_probe.probe_vision("http://x/v1", "k", "m") is None


# --------------------------------------------------------------- 结论发布

def test_detected_overrides_static_catalog():
    """探测结果优先于静态目录:学校换网关,面板/压缩阈值自动跟上。"""
    assert model_catalog.lookup("deepseek-v4-flash")["context_window"] == 327_680
    assert model_catalog.lookup("deepseek-v4-flash")["supports_vision"] is False

    model_catalog.set_detected("deepseek-v4-flash", context_window=131_072,
                               supports_vision=True)
    got = model_catalog.lookup("deepseek-v4-flash")
    assert got["context_window"] == 131_072 and got["supports_vision"] is True
    assert got["detected"] is True
    assert model_catalog.capabilities("deepseek-v4-flash") == ["文本", "视觉", "工具调用"]


def test_detected_alone_makes_model_known():
    """静态目录里没有的模型,只要探测到了就算已知。"""
    assert model_catalog.lookup("brand-new-model")["known"] is False
    model_catalog.set_detected("brand-new-model", context_window=65_536)
    got = model_catalog.lookup("brand-new-model")
    assert got["known"] is True and got["context_window"] == 65_536


def test_set_detected_ignores_empty_fields():
    """None / 非正数不覆盖已有结论(探测失败的那一半不该抹掉好数据)。"""
    model_catalog.set_detected("m", context_window=1000, supports_vision=True)
    model_catalog.set_detected("m", context_window=None, supports_vision=None)
    assert model_catalog.lookup("m")["context_window"] == 1000
    model_catalog.set_detected("m", context_window=0)
    assert model_catalog.lookup("m")["context_window"] == 1000


def test_publish_to_catalog():
    model_probe.publish("m", {"context_window": 4242, "supports_vision": False})
    assert model_catalog.lookup("m")["context_window"] == 4242
    assert model_catalog.context_window_for("m", 131072) == 4242


# --------------------------------------------------------------- 缓存

def test_cache_roundtrip_and_ttl(tmp_path):
    model_probe.save_cache({"m": {"context_window": 1, "checked_at": time.time()}})
    assert model_probe.cached("m") is not None
    old = time.time() - model_probe.CACHE_TTL_SECONDS - 1
    model_probe.save_cache({"m": {"context_window": 1, "checked_at": old}})
    assert model_probe.cached("m") is None            # 过期


def test_cache_discarded_when_endpoint_changed(tmp_path):
    """同一个模型名换了服务地址:旧结论作废,免得拿别家的窗口当数。"""
    fp = model_probe.endpoint_fingerprint("http://a/v1")
    model_probe.save_cache({"m": {"context_window": 1, "endpoint": fp,
                                  "checked_at": time.time()}})
    assert model_probe.cached("m", "http://a/v1") is not None
    assert model_probe.cached("m", "http://b/v1") is None


def test_cache_never_stores_the_raw_endpoint():
    """缓存落在 exe 旁边 = 用户能打开:内网地址不能明文出现在里面。"""
    fp = model_probe.endpoint_fingerprint("http://10.223.24.16:8080/v1")
    assert "10.223.24.16" not in fp and len(fp) == 16
    assert fp == model_probe.endpoint_fingerprint("http://10.223.24.16:8080/v1")
    assert fp != model_probe.endpoint_fingerprint("http://10.0.0.1:8080/v1")


def test_cache_survives_corrupt_file():
    model_probe._cache_path().write_text("{ not json", encoding="utf-8")
    assert model_probe.load_cache() == {}


# --------------------------------------------------------------- 触发

def test_ensure_async_publishes_cache_without_probing(monkeypatch):
    """有缓存就直接生效,一次网络请求都不发(启动不该被探测拖慢)。"""
    model_probe.save_cache({"m": {"context_window": 9999,
                                  "endpoint": model_probe.endpoint_fingerprint("http://x/v1"),
                                  "checked_at": time.time()}})
    monkeypatch.setattr(model_probe, "detect",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不该联网")))
    entry = model_probe.ensure_async("http://x/v1", "k", "m")
    assert entry["context_window"] == 9999
    assert model_catalog.lookup("m")["context_window"] == 9999


def test_ensure_async_sync_path_probes_and_caches(monkeypatch):
    monkeypatch.setattr(model_probe, "detect", lambda *a, **k: {
        "context_window": 1234, "supports_vision": True,
        "endpoint": model_probe.endpoint_fingerprint("http://x/v1"),
        "checked_at": time.time()})
    entry = model_probe.ensure_async("http://x/v1", "k", "m", background=False)
    assert entry["context_window"] == 1234
    assert model_probe.load_cache()["m"]["context_window"] == 1234
    assert model_catalog.lookup("m")["supports_vision"] is True


def test_ensure_async_does_not_probe_same_target_twice(monkeypatch):
    """多开一个会话会再建一次 Bridge:同一目标不该反复问。"""
    hits = []

    def fake_detect(base_url, api_key, model, timeout=30.0):
        hits.append(model)
        return {"context_window": 8, "supports_vision": None,
                "endpoint": model_probe.endpoint_fingerprint(base_url),
                "checked_at": time.time()}

    monkeypatch.setattr(model_probe, "detect", fake_detect)
    model_probe.ensure_async("http://x/v1", "k", "m", background=False)
    model_probe.ensure_async("http://x/v1", "k", "m", background=False)
    assert hits == ["m"]


def test_ensure_async_ignores_total_probe_failure(monkeypatch):
    """什么都没问到就不落盘 —— 一次网络抖动不该变成"这个模型没能力"。"""
    monkeypatch.setattr(model_probe, "detect", lambda *a, **k: {
        "context_window": None, "supports_vision": None,
        "endpoint": model_probe.endpoint_fingerprint("http://x/v1"),
        "checked_at": time.time()})
    assert model_probe.ensure_async("http://x/v1", "k", "m", background=False) is None
    assert model_probe.load_cache() == {}


def test_ensure_async_ignores_detect_exception(monkeypatch):
    """探测炸了也只是这次没测出来,不能影响启动。"""
    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(model_probe, "detect", boom)
    assert model_probe.ensure_async("http://x/v1", "k", "m", background=False) is None


def test_ensure_async_background_does_not_block(monkeypatch):
    """默认走后台线程:立刻返回,不等待探测完成。"""
    import threading

    release = threading.Event()

    def slow_detect(base_url, api_key, model, timeout=30.0):
        release.wait(5)
        return {"context_window": None, "supports_vision": None,
                "endpoint": model_probe.endpoint_fingerprint(base_url),
                "checked_at": time.time()}

    monkeypatch.setattr(model_probe, "detect", slow_detect)
    t0 = time.time()
    assert model_probe.ensure_async("http://x/v1", "k", "m") is None
    assert time.time() - t0 < 0.4          # 没等探测:立刻返回
    assert ("http://x/v1", "m") in model_probe._STARTED
    release.set()
    for t in threading.enumerate():        # 等它收尾,别跨测试留活线程
        if t.name == "model-probe":
            t.join(5)
