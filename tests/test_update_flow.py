"""就地更新那条链(v0.9.176)的针:挑件、下载、校验、续传、交出去、清旧件。

这一条比一般的下载代码更需要"真跑":它最后交出去的是用户会**双击执行**的 exe。
所以这里不起假对象糊弄 —— 下载那几支用**一个真的本地 HTTP 服务**(含 302 跳转,
因为 GitHub 的直链本来就跳 CDN,不跟跳转每次都会"下到 0 字节";v0.9.177 起还含
**真 Range/206**,因为"断点续传"这件事只有让服务器真的按偏移发字节才验得出来)。

网络出口只有一处被换掉:`proactive._http_get`(查版本 / 取校验文件),
换成喂假响应;`download_asset` 与 `list_releases` 的解析分支都走真代码。
"""
import hashlib
import http.server
import json
import re
import threading
from pathlib import Path

import pytest

from minicode import proactive
from minicode.config import Config, Provider
from minicode.gui_bridge import create_app

PAYLOAD = ("引路学长安装包的样子" * 4096).encode("utf-8")   # ~100 KB,够切好几块
SHA = hashlib.sha256(PAYLOAD).hexdigest()
ASSET_NAME = "yinlu-xuezhang-9.9.9-setup.exe"


class _Handler(http.server.BaseHTTPRequestHandler):
    """三种形状:跳一次的、诚实的短正文、**认 Range 的**(续传那一支要用)。

    `ranges_seen` 记下每次请求带来的 `Range`/`If-Range`:续传的判据不是"最后文件
    对上了"(从 0 重下也能对上),而是**服务器真的被要求从第 N 字节开始发**。
    """
    ranges_seen: list = []
    full_when_ranged = False      # /norange 用它:带着 Range 来也只给整件

    def log_message(self, *a):            # 测试里不往 stderr 打字
        pass

    def do_GET(self):
        if self.path == "/redirect":      # GitHub 的 browser_download_url 就是这个形状
            self.send_response(302)
            self.send_header("Location", "/real")
            self.end_headers()
            return
        if self.path == "/missing":       # 第一条路挂了(404):该退到兜底那条
            self.send_response(404)
            self.end_headers()
            return
        if self.path == "/short":         # 接口说有一整件,实发一小截(诚实的短正文)
            self.send_response(200)
            self.send_header("Content-Length", "3")
            self.end_headers()
            self.wfile.write(b"abc")
            return
        rng = str(self.headers.get("Range") or "")
        if self.path.startswith("/ranged") or self.path.startswith("/norange"):
            _Handler.ranges_seen.append((str(self.headers.get("If-Range") or ""), rng))
        if self.path.startswith("/ranged") and rng and not _Handler.full_when_ranged:
            m = re.match(r"bytes=(\d+)-(\d*)$", rng)
            lo = int(m.group(1)) if m else 0
            hi = int(m.group(2)) if (m and m.group(2)) else len(PAYLOAD) - 1
            if lo >= len(PAYLOAD):
                self.send_response(416)
                self.end_headers()
                return
            body = PAYLOAD[lo:hi + 1]
            self.send_response(206)
            self.send_header("Content-Range",
                             "bytes %d-%d/%d" % (lo, hi, len(PAYLOAD)))
            self.send_header("Content-Length", str(len(body)))
            self.send_header("ETag", '"abc123"')
            self.end_headers()
            self.wfile.write(body)
            return
        # /real、/short 之外与 /norange:整件、200、不认 Range
        if self.path.startswith("/norange"):
            body = PAYLOAD
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("ETag", '"abc123"')
            self.end_headers()
            self.wfile.write(body)
            return
        body = PAYLOAD
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        for i in range(0, len(body), 4096):
            self.wfile.write(body[i:i + 4096])


@pytest.fixture
def server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    _Handler.ranges_seen = []
    _Handler.full_when_ranged = False
    yield "http://127.0.0.1:%d" % srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def _asset(url, size=None, name=ASSET_NAME):
    return {"name": name, "url": url, "size": size if size is not None else len(PAYLOAD)}


def _leftovers(tmp_path):
    """目录里剩下的"下载产物"(含续传那张条子)。

    注意**不能**断言"目录是空的":`tests/conftest.py` 有个 autouse 夹具会在
    `tmp_path/app` 放隔离用的数据目录,那句"空目录"会因为它恒红 —— 而红在测试自己
    身上的闸,和没写闸一样糟。
    """
    return [p.name for p in tmp_path.iterdir()
            if p.is_file() and (p.name.endswith(".exe") or ".part" in p.name)]


# ---------------------------------------------------------------- 挑哪一件

def test_running_form_reads_the_exe_name(monkeypatch):
    import sys
    monkeypatch.setattr(sys, "executable", r"C:\x\引路学长-单文件版.exe")
    assert proactive.running_form() == "single"
    monkeypatch.setattr(sys, "executable", r"C:\x\引路学长.exe")
    assert proactive.running_form() == "setup"


def test_pick_asset_matches_the_form_and_needs_https():
    rel = {"assets": [
        {"name": "yinlu-xuezhang-1.2.3-setup.exe", "size": 900,
         "browser_download_url": "https://x/s.exe"},
        {"name": "yinlu-xuezhang-1.2.3-single-file.exe", "size": 100,
         "browser_download_url": "https://x/f.exe"},
        {"name": "yinlu-xuezhang-1.2.3-win64.zip", "size": 1200,
         "browser_download_url": "https://x/z.zip"},
    ]}
    assert proactive.pick_asset(rel, "setup")["name"].endswith("setup.exe")
    assert proactive.pick_asset(rel, "single")["name"].endswith("single-file.exe")
    # zip 故意不给:程序没法"解压覆盖正在运行的自己",给按钮就是给会失败的承诺
    assert proactive.pick_asset({"assets": [rel["assets"][2]]}, "setup") is None
    # 没有直链 / 不是 https ⇒ 界面那颗按钮该退回"打开发布页",而不是点了报错
    assert proactive.pick_asset({"assets": [{"name": "a-setup.exe", "size": 1}]},
                                "setup") is None
    assert proactive.pick_asset({"assets": [{"name": "a-setup.exe",
                                             "browser_download_url": "ftp://x"}]},
                                "setup") is None
    assert proactive.pick_asset(None, "setup") is None


# ---------------------------------------------------------------- 历史列表

class _Resp:
    def __init__(self, code, body=None, text=""):
        self.status_code, self._body, self.text = code, body, text

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


def _patch_get(monkeypatch, resp, why=""):
    monkeypatch.setattr(proactive, "_http_get", lambda *a, **k: (resp, why))


def test_list_releases_parses_real_shapes(monkeypatch):
    _patch_get(monkeypatch, _Resp(200, [
        {"tag_name": "v0.9.176", "published_at": "2026-10-15T12:00:00Z",
         "name": "引路学长 v0.9.176", "body": "## 本次更新\n- 一条"},
        {"tag_name": "v0.9.175", "published_at": "2026-10-15T04:00:00Z",
         "body": "x" * (proactive.NOTES_MAX + 50)},
        {"no_tag": True},
    ]))
    r = proactive.list_releases()
    assert r["ok"] and len(r["releases"]) == 2, "没 tag 的那条不该进列表"
    assert r["releases"][0]["version"] == "0.9.176"
    assert r["releases"][0]["date"] == "2026-10-15"
    assert "本次更新" in r["releases"][0]["notes"]
    assert r["releases"][1]["truncated"] is True, "超长的日志必须标出来,别让人以为看完了"


@pytest.mark.parametrize("resp,why", [
    (None, "网络或证书不通:两种都失败"),
    (_Resp(403), ""),
    (_Resp(200, None), ""),
    (_Resp(200, {"not": "a list"}), ""),
])
def test_list_releases_says_which_kind_of_failure_it_is(monkeypatch, resp, why):
    """四种拿不到要各说一句实话 —— 统一回"暂无历史"就是这一路不许的含糊。"""
    _patch_get(monkeypatch, resp, why)
    r = proactive.list_releases()
    assert r["ok"] is False and r["releases"] == []
    assert r["why"].strip(), "失败了却给不出原因,界面只能编一句"


# ---------------------------------------------------------------- 下载

def test_download_follows_the_redirect_and_lands_the_file(server, tmp_path, monkeypatch):
    monkeypatch.setattr(proactive, "_expected_sha256", lambda *a, **k: (SHA, ""))
    seen = []
    r = proactive.download_asset(_asset(server + "/redirect"), tmp_path,
                                 on_progress=seen.append)
    assert r["ok"], r
    assert r["verified"] == "ok" and r["sha256"] == SHA
    out = Path(r["path"])
    assert out.name == "yinlu-xuezhang-9.9.9-setup.exe"
    assert out.read_bytes() == PAYLOAD, "跟不跟 302 的差别就在这里:不跟会下到空文件"
    assert not (tmp_path / (out.name + ".part")).exists(), ".part 该被改名掉"
    assert seen and seen[-1]["pct"] == 100, "进度得走完,不然界面永远停在 99%"


def test_download_refuses_when_the_hash_does_not_match(server, tmp_path, monkeypatch):
    """校验不过 ⇒ **删掉**,不把一份对不上的 exe 留在能双击的地方。"""
    monkeypatch.setattr(proactive, "_expected_sha256",
                        lambda *a, **k: ("0" * 64, ""))
    r = proactive.download_asset(_asset(server + "/real"), tmp_path)
    assert r["ok"] is False and r["verified"] == "mismatch"
    assert _leftovers(tmp_path) == [], "半截或坏件不许留在目录里"


def test_download_reports_no_verification_loudly(server, tmp_path, monkeypatch):
    """拿不到校验文件时:文件留下(它确实是那份),但 verified=none + 原因。

    界面据此把按钮变成"不能装(校验没过)" —— 把"没验过"说成"验过了"是这一路
    最坏的假绿,所以这条断言钉的是**返回值里的形状**,不是文案。
    """
    monkeypatch.setattr(proactive, "_expected_sha256", lambda *a, **k: ("", "校验文件 HTTP 404"))
    r = proactive.download_asset(_asset(server + "/real"), tmp_path)
    assert r["ok"] and r["verified"] == "none"
    assert "校验" in r["why"]


def test_download_detects_a_short_body(server, tmp_path, monkeypatch):
    """半截要**报长度、留着可续** —— 悄悄截断或悄悄重来都不许。"""
    monkeypatch.setattr(proactive, "_expected_sha256", lambda *a, **k: (SHA, ""))
    r = proactive.download_asset(_asset(server + "/short", size=len(PAYLOAD)), tmp_path)
    assert r["ok"] is False and "120" in r["why"], r["why"]
    assert r["resumable"] is True and r["partial"] == 3
    assert (tmp_path / (ASSET_NAME + ".part")).read_bytes() == b"abc", "半截留着了"


def test_download_cancel_keeps_the_part_for_resume(server, tmp_path, monkeypatch):
    """取消时那份半截**留着**(v0.9.177 改了口径:它现在是续传的本钱,不是残骸)。

    两侧都要钉:留了半截却没写下"它是谁的、总长多少"= 下次照样从 0 重下,等于没留。
    """
    monkeypatch.setattr(proactive, "_expected_sha256", lambda *a, **k: (SHA, ""))
    calls = {"n": 0}

    def stop():
        calls["n"] += 1
        return True            # 第一块就取消:本地回环可能一次读完,不该依赖块数
    r = proactive.download_asset(_asset(server + "/real"), tmp_path, should_stop=stop)
    assert r["ok"] is False and r.get("cancelled") and r.get("resumable")
    assert calls["n"] >= 1, "should_stop 一次都没被问 = 取消是假的"
    part = tmp_path / (ASSET_NAME + ".part")
    assert part.exists(), "取消后把 920 MB 已下的删掉 = 下次从 0 重下"
    rec = proactive._read_sidecar(part)
    assert rec.get("name") == ASSET_NAME and rec.get("total") == len(PAYLOAD)
    assert rec.get("bytes") == part.stat().st_size, "条子写的字节数和盘上的不一致"


def test_download_resumes_with_a_real_range_request(server, tmp_path, monkeypatch):
    """**真续传**:半截在盘上 ⇒ 服务器被要求从第 N 字节发,拼出来的是完整一件。

    判据不是"最后文件对上了"(从 0 重下也能对上),所以这里钉三样:
    ① 服务器确实收到 `Range: bytes=N-` 且带 `If-Range`;② 结果字节与摘要正确;
    ③ 返回里 `resumed` 为真(界面据此说"接着下"而不是"重新下载")。
    """
    monkeypatch.setattr(proactive, "_expected_sha256", lambda *a, **k: (SHA, ""))
    cut = 40000
    part = tmp_path / (ASSET_NAME + ".part")
    part.write_bytes(PAYLOAD[:cut])
    proactive._write_sidecar(part, {"name": ASSET_NAME, "total": len(PAYLOAD),
                                    "validator": '"abc123"', "bytes": cut})
    seen = []
    r = proactive.download_asset(_asset(server + "/ranged"), tmp_path,
                                 on_progress=seen.append)
    assert r["ok"] and r["verified"] == "ok" and r["sha256"] == SHA, r
    assert (tmp_path / ASSET_NAME).read_bytes() == PAYLOAD
    assert _Handler.ranges_seen[-1] == ('"abc123"', "bytes=%d-" % cut), _Handler.ranges_seen
    assert r["resumed"] is True
    assert seen[0]["got"] > cut and seen[0]["resumed"] is True, "进度该从那半截往上走"
    assert not part.exists() and not proactive._sidecar(part).exists(), "装完该收干净"


def test_download_starts_over_when_range_is_ignored(server, tmp_path, monkeypatch):
    """服务器不认 Range(回 200 给整件)⇒ **从 0 重写**,不能把整件接在半截后面。

    这一支防的是最坏的那种坏法:摘要对不上却"看起来下完了"。真接上去的文件会比
    整件长,`字节数对不上`那条会拦住 —— 但拦住不等于做对,所以这里同时钉最终字节。
    """
    monkeypatch.setattr(proactive, "_expected_sha256", lambda *a, **k: (SHA, ""))
    part = tmp_path / (ASSET_NAME + ".part")
    part.write_bytes(PAYLOAD[:40000])
    proactive._write_sidecar(part, {"name": ASSET_NAME, "total": len(PAYLOAD),
                                    "validator": '"abc123"', "bytes": 40000})
    r = proactive.download_asset(_asset(server + "/norange"), tmp_path)
    assert r["ok"] and r["sha256"] == SHA, r
    assert (tmp_path / ASSET_NAME).stat().st_size == len(PAYLOAD)
    assert _Handler.ranges_seen[-1][1] == "bytes=40000-", "第一次确实是去续的"
    assert r["resumed"] is False, "被打回整件之后不该再自称续传成功"


def test_resume_refuses_a_part_it_cannot_explain(server, tmp_path, monkeypatch):
    """没有条子(或条子说的是另一件/另一个总长)的半截:**先删掉**,不接新字节。"""
    monkeypatch.setattr(proactive, "_expected_sha256", lambda *a, **k: (SHA, ""))
    part = tmp_path / (ASSET_NAME + ".part")
    part.write_bytes("这一截是谁的?".encode("utf-8"))
    r = proactive.download_asset(_asset(server + "/real"), tmp_path)
    assert r["ok"] and r["sha256"] == SHA, r
    assert (tmp_path / ASSET_NAME).read_bytes() == PAYLOAD, "别人那半截一个字都不该混进来"


def test_a_complete_part_skips_the_network(tmp_path, monkeypatch):
    """上次下完没来得及校验就断了:这一趟**不出网**,直接算摘要。"""
    import httpx
    monkeypatch.setattr(proactive, "_expected_sha256", lambda *a, **k: (SHA, ""))

    def _no_client(*a, **k):
        raise AssertionError("半截已经齐了还去连服务器 = 白连一次")
    monkeypatch.setattr(httpx, "Client", _no_client)
    part = tmp_path / (ASSET_NAME + ".part")
    part.write_bytes(PAYLOAD)
    proactive._write_sidecar(part, {"name": ASSET_NAME, "total": len(PAYLOAD),
                                    "validator": '"abc123"', "bytes": len(PAYLOAD)})
    r = proactive.download_asset(_asset("https://127.0.0.1:9/never", size=len(PAYLOAD)),
                                 tmp_path)
    assert r["ok"] and r["verified"] == "ok" and r["resumed"] is True


# ---------------------------------------------------------------- 旧件清理(v0.9.177)

def test_prune_only_touches_its_own_files(tmp_path):
    """清理的权限来自"这是这条通道写下的",不来自"它在我这个目录下"。

    混进去的 `notes.txt` / `figure.png` / 一个不像发布件的 exe 必须**原样还在**:
    这条闸防的不是"少删了一个",是"顺手删了别人的东西"。
    """
    cur = proactive._cur_version()
    names = [
        "yinlu-xuezhang-0.9.100-setup.exe",          # 旧版安装包 → 删
        "yinlu-xuezhang-0.9.100-sha256.txt",         # 旧版校验清单 → 删
        "yinlu-xuezhang-0.9.101-single-file.exe",    # 旧版另一形态 → 删
        "yinlu-xuezhang-0.9.102-setup.exe.part",     # 旧版没下完的半截 → 删
        "yinlu-xuezhang-0.9.102-setup.exe.part.meta",
        "yinlu-xuezhang-%s-setup.exe" % cur,          # 正在跑的这一版 → 留
        "yinlu-xuezhang-0.9.177-setup.exe",          # 指定要留的那一版 → 留
    ]
    for n in names:
        (tmp_path / n).write_bytes(b"x" * 10)
    strangers = {"notes.txt": b"hi", "figure.png": b"\x89PNG",
                 "my-setup.exe": b"not ours", "updates": None}
    for n, data in strangers.items():
        (tmp_path / n).write_bytes(data) if data is not None else (tmp_path / n).mkdir()
    r = proactive.prune_updates(tmp_path, keep_version="0.9.177")
    assert r["ok"] and r["freed"] == 50, r
    assert [x["name"] for x in r["removed"]] == [
        "yinlu-xuezhang-0.9.100-setup.exe",
        "yinlu-xuezhang-0.9.100-sha256.txt",
        "yinlu-xuezhang-0.9.101-single-file.exe",
        "yinlu-xuezhang-0.9.102-setup.exe.part",
        "yinlu-xuezhang-0.9.102-setup.exe.part.meta"], r["removed"]
    for n in ("yinlu-xuezhang-%s-setup.exe" % cur, "yinlu-xuezhang-0.9.177-setup.exe"):
        assert (tmp_path / n).exists(), n
    for n in ("notes.txt", "figure.png", "my-setup.exe", "updates"):
        assert (tmp_path / n).exists(), "误删了不是这条通道写的东西:" + n


def test_prune_on_a_missing_directory_is_silence_not_error(tmp_path):
    r = proactive.prune_updates(tmp_path / "没建出来", keep_version="1.1.1")
    assert r["ok"] and r["removed"] == [] and r["freed"] == 0


def test_pending_part_needs_the_sidecar(tmp_path):
    """报"可以接着下"的前提是那张条子在 —— 否则界面写着一半,用户点了却从 0 重下。"""
    name = "yinlu-xuezhang-9.9.9-setup.exe"
    (tmp_path / (name + ".part")).write_bytes(b"abc")
    assert proactive.pending_part(tmp_path) == {}, "没条子的半截不该被报出来"
    proactive._write_sidecar(tmp_path / (name + ".part"),
                             {"name": name, "total": len(PAYLOAD),
                              "validator": "", "bytes": 3})
    p = proactive.pending_part(tmp_path)
    assert p["version"] == "9.9.9" and p["bytes"] == 3
    assert p["total"] == len(PAYLOAD) and p["pct"] == 0


# ---------------------------------------------------------------- 挑哪一条路(v0.9.177)

def test_pick_asset_prefers_the_api_url_and_keeps_the_fallback():
    """`url` 用 api.github.com 那个,`browser_url` 留着兜底。

    这条钉的是 v0.9.176 那个真缺陷:装好的包按 `browser_download_url`(宿主
    `github.com`)去下,而本机实测过"`github.com:443` 连不上而 `api.github.com` 通"
    的网络状态。两条最终都 302 到同一个 CDN,换的是**第一跳**。
    """
    rel = {"assets": [
        {"name": "yinlu-xuezhang-1.2.3-setup.exe", "size": 900,
         "url": "https://api.github.com/repos/o/r/releases/assets/42",
         "browser_download_url": "https://github.com/o/r/releases/download/v1.2.3/s.exe"},
        {"name": "yinlu-xuezhang-1.2.3-sha256.txt", "size": 300,
         "url": "https://api.github.com/repos/o/r/releases/assets/43",
         "browser_download_url": "https://github.com/o/r/releases/download/v1.2.3/s.txt"},
    ]}
    a = proactive.pick_asset(rel, "setup")
    assert a["url"].startswith("https://api.github.com/"), a["url"]
    assert a["browser_url"].startswith("https://github.com/"), "兜底那条不能被扔了"
    assert a["sha_url"].endswith("/assets/43"), "校验清单也该走同一跳"
    # 接口没给 api 地址(老形状)时退回 browser_download_url,不报"没有直链"
    only = {"assets": [{"name": "yinlu-xuezhang-1.2.3-setup.exe", "size": 9,
                        "browser_download_url": "https://github.com/x"}]}
    assert proactive.pick_asset(only, "setup")["url"] == "https://github.com/x"


def test_download_falls_back_to_the_browser_url(server, tmp_path, monkeypatch):
    """第一条路挂了 ⇒ 退到 `browser_url` 再试一次,而不是直接报"下载没成"。"""
    monkeypatch.setattr(proactive, "_expected_sha256", lambda *a, **k: (SHA, ""))
    a = _asset(server + "/missing")                  # 404 的那条
    a["browser_url"] = server + "/real"              # 兜底这条是好的
    r = proactive.download_asset(a, tmp_path)
    assert r["ok"] and r["sha256"] == SHA, r
    assert "HTTP 404" in r["why"] or r["verified"] == "ok"


def test_expected_sha256_asks_the_api_url_as_octet_stream(monkeypatch):
    """取校验清单那一步:**Accept 必须是 application/octet-stream**。
    给成 text/plain 的话 api 那个接口回的是 JSON 元数据,里面没有哈希,于是
    "校验文件里没有这一件"会成一句假话(而看起来像实话)。
    """
    seen = []

    class _R:
        status_code = 200
        text = "%s  %s\n" % (SHA, ASSET_NAME)

    def fake_get(url, timeout=10.0, headers=None):
        seen.append((url, dict(headers or {})))
        return _R(), ""
    monkeypatch.setattr(proactive, "_http_get", fake_get)
    got, why = proactive._expected_sha256(
        "9.9.9", ASSET_NAME, "https://api.github.com/repos/o/r/releases/assets/43")
    assert got == SHA and why == ""
    assert seen[0][0].startswith("https://api.github.com/"), "该先走 api 那一跳"
    assert seen[0][1].get("Accept") == "application/octet-stream", seen[0]


def test_download_without_a_url_is_refused_before_any_network(tmp_path):
    r = proactive.download_asset({"name": "a", "url": ""}, tmp_path)
    assert r["ok"] is False and "直链" in r["why"]


# ---------------------------------------------------------------- 桥的那一层

def _bridge(tmp_path, **kw):
    cfg = Config(providers=[Provider(id="p", name="P", base_url="https://x/v1",
                                      api_key="k", model="m")],
                 active_provider_id="p", mcp_servers=[], sandbox={}, **kw)
    b, _ui, _s, _a = create_app(cfg, tmp_path)
    return b


def test_update_download_refuses_when_the_channel_is_off(tmp_path):
    b = _bridge(tmp_path, update_check=False)
    r = b.update_download()
    assert r["ok"] is False and "update_check" in r["why"], "关掉就是不出网,不该被按钮绕过"


def test_update_download_refuses_to_run_two_at_once(tmp_path, monkeypatch):
    b = _bridge(tmp_path)
    upd = {"version": "9.9.9", "url": "https://x", "notes": "n",
           "asset": {"name": "a-setup.exe", "url": "https://127.0.0.1:1/x", "size": 1}}
    monkeypatch.setattr(proactive, "check_update", lambda *a, **k: {"ok": True, "update": upd, "why": ""})
    b._upd_lock = threading.Lock()
    b._upd_stop = threading.Event()
    b._upd = {"state": "downloading"}
    r = b.update_download()
    assert r["ok"] is False and "已经有一份在下载" in r["why"]


def test_update_install_refuses_an_unverified_file(tmp_path):
    b = _bridge(tmp_path)
    b._upd = {"state": "done", "path": str(tmp_path / "x.exe"), "name": "x.exe",
              "verified": "none", "why": "校验文件 HTTP 404"}
    (tmp_path / "x.exe").write_bytes(b"not really an installer")
    r = b.update_install()
    assert r["ok"] is False and "校验" in r["why"], "没核对过的包直接开起来就是这一路的红线"


def test_the_local_path_never_leaves_the_backend(tmp_path):
    """绝对路径不许出现在给界面那一份快照里(与 `undo_records` 同一条口径)。

    两侧都要钉:漏了"擦掉"就是路径外流到 JS;漏了"后端还留着"就是连安装要用的那份
    一起擦坏 —— 后者表现是"下完了点运行说找不到文件"。
    """
    b = _bridge(tmp_path)
    b._upd = {"state": "done", "path": r"C:\Users\me\updates\a.exe",
              "name": "a.exe", "verified": "ok"}
    sent = b.update_download_state()
    assert "path" not in sent, list(sent)
    assert b._upd["path"].endswith("a.exe"), "后端自己那份不能被一起抹掉"


def test_update_skip_lands_in_config_json_and_comes_back(tmp_path):
    """"跳过此版本"必须跨重启还在 —— 它落 config.json,而 config 那层刚修过白名单。"""
    b = _bridge(tmp_path)
    r = b.update_skip("v9.9.9")
    assert r["ok"] and r["skipped"] == "9.9.9", "v 前缀该被吃掉,否则比不中"
    raw = json.loads(Path(b.config_path).read_text(encoding="utf-8"))
    assert raw["update_skip_version"] == "9.9.9"
    from minicode.config import load_config
    assert load_config(Path(b.config_path)).update_skip_version == "9.9.9"
    assert b.update_skip("").get("ok") is False, "没给版本号的跳过不该静默成功"


def test_bridge_exposes_the_whole_surface_to_the_frontend():
    """前端只认 PublicApi 那一层:少转一个方法,界面上就是"点了没反应"。"""
    from minicode.gui_bridge import PublicApi
    for name in ("update_download", "update_download_state", "update_download_cancel",
                 "update_install", "update_skip", "update_history", "check_update_now"):
        assert callable(getattr(PublicApi, name, None)), name
