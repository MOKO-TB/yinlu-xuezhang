"""网页图片抓取的口径测试(计划书阶段 4)。

每条测的都是**边界**而不是"能下载":SSRF 逐跳验、伪装图片、大小上限、
出站策略 deny 时一个请求都不发、落盘与来源小票。
HTTP 走 `httpx.MockTransport`(不发真网络),主机解析用一张固定表
(否则测试机没网时"解析不了"会把正常用例也拦掉)。
"""
import hashlib
import json
import socket
import types

import httpx
import pytest

from minicode import attachments, imgfetch


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch):
    table = {"img.example": "93.184.216.34", "cdn.example": "93.184.216.35",
             "internal.example": "10.0.0.5", "metadata.example": "169.254.169.254",
             "alias.example": "10.9.9.9",
             # 字面 IP:真 getaddrinfo 会原样解析,假表也得照做
             "10.0.0.9": "10.0.0.9", "169.254.169.254": "169.254.169.254",
             "localhost": "127.0.0.1", "127.0.0.1": "127.0.0.1"}

    def fake(host, *a, **k):
        ip = table.get(str(host))
        if ip is None:
            raise socket.gaierror(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 200


def _client(routes):
    """routes: {url: (status, headers, body)};handler 记下每次请求的真实 URL。"""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        spec = routes.get(str(request.url))
        if spec is None:
            return httpx.Response(404, content=b"nope")
        status, headers, body = spec
        return httpx.Response(status, headers=headers, content=body)

    return httpx.Client(transport=httpx.MockTransport(handler),
                        follow_redirects=False), calls


# ---------------------------------------------------------------- URL / 主机

def test_only_http_and_https_are_allowed():
    assert imgfetch.check_url("file:///C:/Windows/win.ini")[0] is False
    assert "只认 http/https" in imgfetch.check_url("file:///C:/x.png")[1]
    assert "只认 http/https" in imgfetch.check_url("data:image/png;base64,AA")[1]
    assert imgfetch.check_url("http://img.example/a.png") == (True, "")


def test_urls_with_credentials_are_refused():
    ok, why = imgfetch.check_url("https://user:pass@img.example/a.png")
    assert ok is False and "账号密码" in why


def test_private_and_metadata_hosts_are_refused_even_by_name():
    cases = {"localhost": "本机", "metadata.example": "链路本地",
             "internal.example": "私网", "127.0.0.1": "回环",
             "10.0.0.9": "私网", "169.254.169.254": "链路本地"}
    for host, mark in cases.items():
        ok, why = imgfetch.check_url("http://%s/a.png" % host)
        assert ok is False, host
        assert mark in why, (host, why)


def test_a_public_hostname_resolving_to_a_private_ip_is_refused():
    """域名本身干净、解析到 10.x —— 这是"看起来公网"的那种绕过。"""
    ok, why = imgfetch.check_url("http://alias.example/a.png")
    assert ok is False and "解析" in why


def test_sniff_reads_magic_bytes_not_the_suffix():
    assert imgfetch.sniff_image(PNG) == (".png", "image/png")
    assert imgfetch.sniff_image(JPG) == (".jpg", "image/jpeg")
    assert imgfetch.sniff_image(b"GIF89a....") == (".gif", "image/gif")
    assert imgfetch.sniff_image(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == (".webp", "image/webp")
    assert imgfetch.sniff_image(b"BM\x00\x00") == (".bmp", "image/bmp")
    assert imgfetch.sniff_image(b"<svg xmlns='http://www.w3.org/2000/svg'/>") is None
    assert imgfetch.sniff_image(b"<!doctype html><h1>x</h1>") is None


# ---------------------------------------------------------------- 抓取

def test_a_real_image_lands_in_uploads_with_a_source_receipt(tmp_path):
    client, calls = _client({"http://img.example/logo.png":
                             (200, {"content-type": "image/png"}, PNG)})
    got = imgfetch.fetch(tmp_path, "http://img.example/logo.png", client=client)
    assert calls == ["http://img.example/logo.png"]
    path = tmp_path / got["path"]
    assert path.is_file() and path.read_bytes() == PNG
    assert got["path"].startswith("uploads/") and got["mime"] == "image/png"
    assert got["sha256"] == hashlib.sha256(PNG).hexdigest()
    side = json.loads((path.parent / (path.name + ".source.json")).read_text(encoding="utf-8"))
    assert side["url"] == "http://img.example/logo.png"
    assert side["sha256"] == got["sha256"] and side["bytes"] == len(PNG)


def test_redirects_are_followed_and_each_hop_is_rechecked(tmp_path):
    client, calls = _client({
        "http://img.example/a.png": (302, {"location": "/real/b.png"}, b""),
        "http://img.example/real/b.png": (200, {"content-type": "image/jpeg"}, JPG)})
    got = imgfetch.fetch(tmp_path, "http://img.example/a.png", client=client)
    assert got["final_url"] == "http://img.example/real/b.png"
    assert calls == ["http://img.example/a.png", "http://img.example/real/b.png"]


def test_a_redirect_into_the_private_network_is_refused_before_the_second_request(tmp_path):
    """只验起始 URL 的写法会被「公网 302 到 127.0.0.1」绕过。"""
    client, calls = _client({
        "http://img.example/a.png": (302, {"location": "http://127.0.0.1/secret.png"}, b"")})
    with pytest.raises(imgfetch.FetchError, match="回环"):
        imgfetch.fetch(tmp_path, "http://img.example/a.png", client=client)
    assert calls == ["http://img.example/a.png"], "私网那一跳必须一个请求都不发"


def test_too_many_redirects_stop_and_say_so(tmp_path):
    routes = {"http://img.example/r%d.png" % i:
              (302, {"location": "/r%d.png" % (i + 1)}, b"") for i in range(10)}
    client, _ = _client(routes)
    with pytest.raises(imgfetch.FetchError, match="重定向超过"):
        imgfetch.fetch(tmp_path, "http://img.example/r0.png", client=client)


def test_html_masquerading_as_an_image_is_refused_twice_over(tmp_path):
    # ① 声明就是网页:直接拒
    client, _ = _client({"http://img.example/a.png":
                         (200, {"content-type": "text/html"}, b"<html>x</html>")})
    with pytest.raises(imgfetch.FetchError, match="网页/文本"):
        imgfetch.fetch(tmp_path, "http://img.example/a.png", client=client)
    # ② 声明是 image/png、内容是网页:按文件头认,照样拒
    client2, _ = _client({"http://img.example/b.png":
                          (200, {"content-type": "image/png"}, b"<html>x</html>")})
    with pytest.raises(imgfetch.FetchError, match="文件头认不出"):
        imgfetch.fetch(tmp_path, "http://img.example/b.png", client=client2)


def test_oversize_reports_without_leaving_a_half_file(tmp_path):
    body = PNG + b"\x00" * 4096
    client, _ = _client({"http://img.example/big.png":
                         (200, {"content-type": "image/png"}, body)})
    with pytest.raises(imgfetch.FetchError, match="上限"):
        imgfetch.fetch(tmp_path, "http://img.example/big.png", client=client,
                       max_bytes=1024)
    up = tmp_path / "uploads"
    assert not up.exists() or not [p for p in up.iterdir() if p.suffix == ".png"]


def test_declared_content_length_over_the_cap_is_rejected_early(tmp_path):
    client, calls = _client({"http://img.example/big.png":
                             (200, {"content-type": "image/png",
                                    "content-length": "99999999"}, PNG)})
    with pytest.raises(imgfetch.FetchError, match="太大"):
        imgfetch.fetch(tmp_path, "http://img.example/big.png", client=client)
    assert calls == ["http://img.example/big.png"]


def test_net_policy_deny_sends_zero_requests(tmp_path):
    cfg = types.SimpleNamespace(net_policy={"mode": "deny", "allow": [], "deny": []})
    client, calls = _client({})
    with pytest.raises(imgfetch.FetchError, match="出站策略"):
        imgfetch.fetch(tmp_path, "http://img.example/a.png", config=cfg, client=client)
    assert calls == []


def test_the_tool_text_says_it_did_not_send_the_image_to_the_model(tmp_path):
    client, _ = _client({"http://img.example/logo.png":
                         (200, {"content-type": "image/png"}, PNG)})
    ctx = types.SimpleNamespace(workspace=str(tmp_path), config=None)
    orig = httpx.Client
    httpx.Client = lambda **kw: client          # 工具出口不暴露 client 参数
    try:
        text = imgfetch.fetch_image(ctx, "http://img.example/logo.png")
    finally:
        httpx.Client = orig
    assert "已抓取图片" in text
    assert "没有发给我看" in text and "view_image" in text and "ocr_image" in text
    assert "uploads/" in text
    assert list((tmp_path / attachments.UPLOAD_DIR).glob("*.png"))
