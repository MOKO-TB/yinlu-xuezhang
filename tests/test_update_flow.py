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
        if self.path == "/sha-redirect":  # 校验清单在资产 API 上就是这形状:302 指走
            self.send_response(302)
            self.send_header("Location", "/sha256.txt")
            self.end_headers()
            return
        if self.path == "/sha256.txt":
            body = ("%s  %s\n" % (SHA, ASSET_NAME)).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
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


# ------------------------------------------------- 该下哪一件(v0.9.178 路 1)

MAN = {"identity": {"library": {"size": 1331774, "count": 9, "sha256": "a" * 64},
                    "browser_kernel": {"files": 3, "bytes": 4, "digest": "d" * 64}},
       "assets": {"app_setup": {"name": "yinlu-xuezhang-9.9.9-app-setup.exe",
                                "size": 141962981},
                  "setup": {"name": "yinlu-xuezhang-9.9.9-setup.exe",
                            "size": 920614741}}}
SAME = {"library": {"size": 1331774, "count": 9, "sha256": "a" * 64},
        "browser_kernel": {"files": 3, "bytes": 4, "digest": "d" * 64}}


@pytest.mark.parametrize("form,man,local,choice,needle", [
    ("single", MAN, SAME, "single_file", "单文件"),
    ("setup", {}, SAME, "setup", "没有清单文件"),
    ("setup", MAN, SAME, "app_setup", "跳过"),
    ("setup", MAN, {"library": {}, "browser_kernel": {}}, "setup", "没有凭证"),
    ("setup", MAN, {"library": {"size": 1, "count": 1, "sha256": "b" * 64},
                    "browser_kernel": SAME["browser_kernel"]}, "setup", "不一致"),
])
def test_plan_update_picks_and_explains(form, man, local, choice, needle):
    """五种形状各说一句实话。**退回整包却不给理由**是这一格最讨厌的坏法。"""
    p = proactive.plan_update(form, man, dict(local))
    assert p["choice"] == choice, p
    assert needle in p["reason"], p["reason"]
    assert p["reason"].strip(), "退回/跳过都得有一句话给用户"
    if choice == "app_setup":
        assert sum(s["bytes"] for s in p["skipped"]) == 1331774 + 4


def test_an_absent_credential_is_never_a_match():
    """`_same_identity` 里"本机没凭证"必须算**不等**。

    这一条是整个跳过机制的安全边界:把"证明不了一致"当成"一致",用户就会在
    馆藏库其实被删过/被换过的机器上装出一个没有图书馆的包,而界面还说"已跳过"。
    """
    assert proactive._same_identity({}, SAME["library"]) is False
    assert proactive._same_identity(SAME["library"], {}) is False
    assert proactive._same_identity({}, {}) is False
    assert proactive._same_identity(SAME["library"], SAME["library"]) is True
    # 只对上两个字段里的一个也不行(逐字段比,不是"看起来差不多")
    assert proactive._same_identity({"size": 1331774, "count": 8, "sha256": "a" * 64},
                                    SAME["library"]) is False


def test_pick_asset_by_name_beats_the_suffix():
    """两件都以 `setup.exe` 结尾时,**必须按名字取**。

    这是这一版最容易写错的一处:按后缀挑会挑到整包(920,614,741 B),于是
    "判定说下 135 MiB、手里拿着 878 MiB" —— 前面所有身份比对白做,而界面上一切正常。
    """
    rel = {"assets": [
        {"name": "yinlu-xuezhang-9.9.9-setup.exe", "size": 920614741,
         "url": "https://api.github.com/repos/o/r/releases/assets/1",
         "browser_download_url": "https://github.com/o/r/x/setup.exe"},
        {"name": "yinlu-xuezhang-9.9.9-app-setup.exe", "size": 141962981,
         "url": "https://api.github.com/repos/o/r/releases/assets/2",
         "browser_download_url": "https://github.com/o/r/x/app-setup.exe"},
    ]}
    a = proactive.pick_asset(rel, "setup", "yinlu-xuezhang-9.9.9-app-setup.exe")
    assert a["name"].endswith("app-setup.exe") and a["size"] == 141962981, a
    assert a["url"].endswith("/assets/2"), "别挑错那件的地址"
    b = proactive.pick_asset(rel, "setup", "yinlu-xuezhang-9.9.9-setup.exe")
    assert b["size"] == 920614741, b
    # 名字给了但发布里没这件 ⇒ 回 None,让调用方去退回整包(不是"随便挑一件像的")
    assert proactive.pick_asset(rel, "setup", "不存在的名字.exe") is None


def test_local_identities_reads_stamps_without_rehashing(tmp_path, monkeypatch):
    """本机身份是**读凭证文件**,不是现算 1.3 GB 的哈希(那会让"检查更新"贵十几秒)。"""
    import json as _json
    from minicode import library, paths
    db = tmp_path / "library.db"
    db.write_bytes(b"not really a db")            # 内容完全不参与:只读旁边那份 json
    (tmp_path / (db.name + library.STAMP_SUFFIX)).write_text(_json.dumps(
        {"size": 1331774, "count": 9, "sha256": "a" * 64, "built_at": "很久以前"}),
        encoding="utf-8")
    monkeypatch.setattr(library, "db_path", lambda workspace=None: db)
    (tmp_path / "browser-kernel.stamp.json").write_text(_json.dumps(
        {"files": 3, "bytes": 4, "digest": "d" * 64, "built_at": "很久以前"}),
        encoding="utf-8")
    monkeypatch.setattr(paths, "APP_DIR", tmp_path)
    got = proactive.local_identities()
    assert got["library"] == {"size": 1331774, "count": 9, "sha256": "a" * 64}, got
    assert got["browser_kernel"] == {"files": 3, "bytes": 4, "digest": "d" * 64}, got
    assert "built_at" not in got["library"], "时间噪声不能进身份比对"


def test_local_identities_on_an_old_package_is_empty_not_an_error(tmp_path, monkeypatch):
    """老包(没有这两份凭证)要回**空**,让上层退回整包并说清 —— 不许抛。"""
    from minicode import library, paths
    monkeypatch.setattr(library, "db_path", lambda workspace=None: None)
    monkeypatch.setattr(paths, "APP_DIR", tmp_path)
    got = proactive.local_identities()
    assert got == {"library": {}, "browser_kernel": {}}, got


def _fake_release(with_app=True):
    assets = [{"name": "yinlu-xuezhang-9.9.9-setup.exe", "size": 920614741,
               "url": "https://api.github.com/repos/o/r/releases/assets/1",
               "browser_download_url": "https://github.com/x/setup.exe"},
              {"name": "yinlu-xuezhang-9.9.9-sha256.txt", "size": 400,
               "url": "https://api.github.com/repos/o/r/releases/assets/3",
               "browser_download_url": "https://github.com/x/s.txt"},
              {"name": "yinlu-xuezhang-9.9.9-assets.json", "size": 900,
               "url": "https://api.github.com/repos/o/r/releases/assets/4",
               "browser_download_url": "https://github.com/x/a.json"}]
    if with_app:
        assets.insert(1, {"name": "yinlu-xuezhang-9.9.9-app-setup.exe",
                          "size": 141962981,
                          "url": "https://api.github.com/repos/o/r/releases/assets/2",
                          "browser_download_url": "https://github.com/x/app.exe"})
    return {"tag_name": "v9.9.9", "name": "引路学长 v9.9.9", "html_url": "https://x",
            "published_at": "2026-10-16T00:00:00Z", "body": "改一行", "assets": assets}


def test_check_update_end_to_end_selects_the_small_asset(monkeypatch):
    """整条判定链走一遍(假 release + 假清单,**真** `plan_update`/`pick_asset`)。"""
    monkeypatch.setattr(proactive, "_release_payload",
                        lambda timeout=10.0: (_fake_release(), ""))
    monkeypatch.setattr(proactive, "running_form", lambda: "setup")
    monkeypatch.setattr(proactive, "fetch_release_manifest",
                        lambda rel, ver, timeout=12.0: (MAN, ""))
    monkeypatch.setattr(proactive, "local_identities", lambda: dict(SAME))
    r = proactive.check_update("0.9.177", detail=True)
    u = r["update"]
    assert u["asset"]["name"] == "yinlu-xuezhang-9.9.9-app-setup.exe", u["asset"]
    assert u["asset"]["size"] == 141962981
    assert u["plan"]["choice"] == "app_setup" and "跳过" in u["plan"]["reason"]


def test_check_update_falls_back_when_the_manifest_lies(monkeypatch):
    """清单说"可以下小件"而 Release 里**没有那件** ⇒ 退回整包,并把矛盾说出来。

    不这样做的话表现是"下载了 878 MB 却告诉用户跳过了 743 MB" —— 那是这一格里
    最伤信任的一种不一致(两处各说各话)。
    """
    monkeypatch.setattr(proactive, "_release_payload",
                        lambda timeout=10.0: (_fake_release(with_app=False), ""))
    monkeypatch.setattr(proactive, "running_form", lambda: "setup")
    monkeypatch.setattr(proactive, "fetch_release_manifest",
                        lambda rel, ver, timeout=12.0: (MAN, ""))
    monkeypatch.setattr(proactive, "local_identities", lambda: dict(SAME))
    u = proactive.check_update("0.9.177", detail=True)["update"]
    assert u["asset"]["name"] == "yinlu-xuezhang-9.9.9-setup.exe", u["asset"]
    assert u["plan"]["choice"] == "setup" and "但发布里没有那件" in u["plan"]["reason"]


def test_fetch_release_manifest_reads_the_named_asset_and_says_why(monkeypatch):
    """清单那件的取法:按名字找、走 api 那一跳、Accept 是 octet-stream;取不到说实话。"""
    seen = []

    class _R:
        status_code = 200
        text = '{"schema": 1, "identity": {"library": {"size": 1}}}'

    def fake_get(url, timeout=10.0, headers=None):
        seen.append((url, dict(headers or {})))
        return _R(), ""
    monkeypatch.setattr(proactive, "_http_get", fake_get)
    man, why = proactive.fetch_release_manifest(_fake_release(), "9.9.9")
    assert why == "" and man["identity"]["library"]["size"] == 1, man
    assert seen[0][0].endswith("/releases/assets/4"), seen
    assert seen[0][1]["Accept"] == "application/octet-stream", seen
    m2, w2 = proactive.fetch_release_manifest(_fake_release(), "8.8.8")
    assert m2 == {} and "没有" in w2 and "8.8.8" in w2, w2


def test_prune_recognises_the_two_new_file_names(tmp_path):
    """v0.9.178 多了两件产物名:`app-setup.exe` 与 `assets.json`。

    它们**必须**进那道文件名形状闸,否则清理永远不认识它们 —— 表现是"用户按了清理,
    腾出 0 字节,而盘上躺着三件旧的小包"。这条针钉的就是"新产物有没有交登记税"。
    """
    cur = proactive._cur_version()
    for n in ("yinlu-xuezhang-0.9.50-app-setup.exe",
              "yinlu-xuezhang-0.9.50-assets.json",
              f"yinlu-xuezhang-{cur}-app-setup.exe"):
        (tmp_path / n).write_bytes(b"z" * 7)
    r = proactive.prune_updates(tmp_path, keep_version="")
    assert [x["name"] for x in r["removed"]] == [
        "yinlu-xuezhang-0.9.50-app-setup.exe", "yinlu-xuezhang-0.9.50-assets.json"], r
    assert (tmp_path / f"yinlu-xuezhang-{cur}-app-setup.exe").exists(), "当前版要留"


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

    def fake_get(url, timeout=10.0, headers=None, **kw):
        seen.append((url, dict(headers or {})))
        return _R(), ""
    monkeypatch.setattr(proactive, "_http_get", fake_get)
    got, why = proactive._expected_sha256(
        "9.9.9", ASSET_NAME, "https://api.github.com/repos/o/r/releases/assets/43")
    assert got == SHA and why == ""
    assert seen[0][0].startswith("https://api.github.com/"), "该先走 api 那一跳"
    assert seen[0][1].get("Accept") == "application/octet-stream", seen[0]


def test_the_checksum_fetch_follows_the_redirect_for_real(server):
    """**这一支是本轮最贵的一支**:它不 monkeypatch `_http_get`,走真 HTTP 客户端。

    为什么必须存在:v0.9.177 把校验清单的取法改到资产 API 那一跳,而那个接口发的是
    **302 + Location**。`_http_get` 当时没有 `follow_redirects=True` —— 于是清单取回来
    是空的,`verified` 永远是 `none`,界面上那颗「运行安装程序」**永远灰着**,而**下载本身
    是好的**(那一条有跟跳转)。上面那些 `_expected_sha256` 的针全把 `_http_get` 换成假的
    200 正文,所以这个坏法在**测试里根本不存在** —— 是出包前那次真连 GitHub 的实测把它
    抓出来的(`校验文件 HTTP 302`,同一份文件的 sha256 本地能对上)。
    **教训与 §7 那条同族:契约落在"真字节 / 真跳转"上时,必须有一支不替换网络出口的针。**
    """
    got, why = proactive._expected_sha256("9.9.9", ASSET_NAME,
                                          sha_url=server + "/sha-redirect")
    assert got == SHA, (got, why)
    assert why == ""


def test_download_verifies_end_to_end_over_real_http(server, tmp_path):
    """整条路一次走通(不 stub 校验):下回来的字节**与发布那份对得上** ⇒ verified=ok。

    上一支测"取得到清单",这一支测"取得了清单之后那颗按钮真的会变亮"。
    """
    a = _asset(server + "/real")
    a["sha_url"] = server + "/sha-redirect"
    r = proactive.download_asset(a, tmp_path)
    assert r["ok"] and r["verified"] == "ok", r
    assert r["sha256"] == SHA


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
                 "update_install", "update_skip", "update_history", "update_prune",
                 "check_update_now"):
        assert callable(getattr(PublicApi, name, None)), name


def test_update_prune_reports_bytes_in_human_form(tmp_path, monkeypatch):
    """清理那颗按钮要拿得到"腾出多少",而且**默认留着当前这版与最新那一版**。

    走桥不是多余的一层:文件名闸、`keep_version` 的默认值(从下载状态里取)、
    把字节数翻成人话,三件事都发生在桥这一层,而 `prune_updates` 自己都不知道。

    ⚠ 版本号一律**算出来**(`_cur_version()` 与一个明显的"未来版"),不写死:
    上一版这条测试里写死了 `0.9.177`,抬到 0.9.178 之后它自己变成"该被清掉的旧件",
    于是红在**测试的假设**上而不是代码上 —— 那种红最费时间。
    """
    b = _bridge(tmp_path)
    cur = proactive._cur_version()
    newer = "99.0.0"
    updir = tmp_path / "app" / "updates"
    updir.mkdir(parents=True)
    for n in (f"yinlu-xuezhang-{cur}-setup.exe",
              "yinlu-xuezhang-0.9.100-setup.exe",
              f"yinlu-xuezhang-{newer}-setup.exe"):
        (updir / n).write_bytes(b"z" * 100)
    b._upd = {"state": "error", "version": newer, "resumable": False}
    r = b.update_prune()
    assert r["ok"] and r["removed_count"] == 1, r      # 只删被取代的那件旧件
    assert r["removed"][0]["name"] == "yinlu-xuezhang-0.9.100-setup.exe", r
    assert r["freed"] == 100 and r["freed_size"].endswith(("B", "KB", "MB"))
    assert (updir / f"yinlu-xuezhang-{cur}-setup.exe").exists()
    assert (updir / f"yinlu-xuezhang-{newer}-setup.exe").exists(), "下载状态里那一版要留"
    # 界面指定 keep_version 时,那一版也留着
    r2 = b.update_prune(keep_version=newer)
    assert r2["removed_count"] == 0, r2


def test_download_state_surfaces_a_part_from_a_previous_run(tmp_path, monkeypatch):
    """重启之后那句"有半截可以接着下"必须来自盘,不是内存(内存那份早没了)。"""
    b = _bridge(tmp_path)
    updir = tmp_path / "app" / "updates"
    updir.mkdir(parents=True)
    name = "yinlu-xuezhang-0.9.177-setup.exe"
    (updir / (name + ".part")).write_bytes(b"a" * 123)
    proactive._write_sidecar(updir / (name + ".part"),
                             {"name": name, "total": 1000, "validator": "", "bytes": 123})
    s = b.update_download_state()
    assert s["state"] == "partial" and s["resumable"] is True, s
    assert s["partial"] == 123 and s["total"] == 1000 and s["version"] == "0.9.177"
    assert "接着走" in s["why"]
    assert "path" not in s                                  # 与 `_upd_public` 同一条口径
    b._upd = {"state": "downloading", "pct": 40}
    assert b.update_download_state()["state"] == "downloading", "在跑的那一份优先于盘上的"
