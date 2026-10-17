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
import sys
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
    fail_nonzero_first = 0        # 前 N 个"从第 N 字节起"的请求直接 503(抖一下)
    bytes_sent = 0                # 服务器一共被要走多少字节 —— "同一件下两遍"的判据
    _lock = threading.Lock()      # 并发下载下 `+=` 会丢计数,丢了针就假绿

    @classmethod
    def _tally(cls, n: int) -> None:
        with cls._lock:
            cls.bytes_sent += n

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
            if lo > 0 and _Handler.fail_nonzero_first > 0:
                _Handler.fail_nonzero_first -= 1
                self.send_response(503)
                self.end_headers()
                return
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
            _Handler._tally(len(body))
            self.wfile.write(body)
            return
        # /real、/short 之外与 /norange:整件、200、不认 Range
        if self.path.startswith("/norange"):
            body = PAYLOAD
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("ETag", '"abc123"')
            self.end_headers()
            _Handler._tally(len(body))
            self.wfile.write(body)
            return
        body = PAYLOAD
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        _Handler._tally(len(body))
        for i in range(0, len(body), 4096):
            self.wfile.write(body[i:i + 4096])


@pytest.fixture
def server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    _Handler.ranges_seen = []
    _Handler.full_when_ranged = False
    _Handler.fail_nonzero_first = 0
    _Handler.bytes_sent = 0
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


def test_the_rate_meter_is_a_window_not_an_average():
    """速率是**窗口**里的数,而且不许跨"被打回整件重下"那一次。

    为什么单独钉它:用户问的是"现在为什么不动"。一次 920 MB 的下载里,总平均会把
    "前 30 秒 3 MB/s、后面卡住"说成"还行"。
    """
    m = proactive._RateMeter(window=2.0)
    m.sample(0.0, 0)
    m.sample(2.0, 6 * 1024 * 1024)
    assert m.read() == 3 * 1024 * 1024, m.read()          # 2 秒 6 MB = 3 MB/s
    m.sample(4.0, 7 * 1024 * 1024)
    assert m.read() == int(1024 * 1024 / 2.0), m.read()   # 只看最近那个窗口
    # 服务器不认 Range → 字节数倒退:窗口必须重来,不能拿"倒退"算出个速率
    m.sample(6.0, 1024)
    m.sample(8.0, 1024 * 1024 * 3)
    assert m.read() == int((1024 * 1024 * 3 - 1024) / 2.0), m.read()
    assert proactive._RateMeter().read() == 0, "没采过样要说'不知道',不许编"


def test_a_partial_records_its_measured_rate(server, tmp_path, monkeypatch):
    """半截那条子上要留着**实测速率** —— 这是"慢在哪"唯一的证据来源。

    它不参与任何判定(该下哪一件由清单说了算、能不能装由 sha256 说了算),
    所以这一支针钉的是"记没记下来",不是"记成什么值"。
    """
    monkeypatch.setattr(proactive, "_expected_sha256", lambda *a, **k: (SHA, ""))
    seen = []
    r = proactive.download_asset(_asset(server + "/real"), tmp_path,
                                 should_stop=lambda: True,
                                 on_progress=lambda ev: seen.append(ev))
    assert r["ok"] is False and r.get("cancelled")
    part = tmp_path / (ASSET_NAME + ".part")
    rec = proactive._read_sidecar(part)
    assert isinstance(rec.get("bps"), int), (rec, "条子里要有实测速率")
    pend = proactive.pending_part(tmp_path)
    assert "bps" in pend, pend                          # 重启后界面也拿得到
    assert all("bps" in ev for ev in seen) or not seen, "进度事件里要带速率"


def _route_stub(url):
    """替掉择快路线那一步,只留"分段下载"本身 —— 探测自己也要发几个 Range 请求,
    不替掉它,下面几支针里"服务器被要了几个起点"就分不清是探测还是下载了。"""
    return lambda *a, **k: ({"url": url, "ctx": None, "label": "stub",
                             "bps": 1.0, "honored": True}, [])


def test_a_big_asset_goes_out_as_parallel_ranges_and_still_verifies(server, tmp_path,
                                                                    monkeypatch):
    """A:够大的一件**分几条连接各要一段**,而最后仍按整件 sha256 才算装得。

    判据不是"文件对上了"(从 0 单连接下也能对上),而是**服务器被要求从好几个不同
    偏移开始发** —— 那才是并发。第二条判据同样要紧:**服务器一共被要走 100 KB,
    一件不多**(并发下完之后不许再来一遍,见下面那支针)。
    """
    monkeypatch.setattr(proactive, "PARALLEL_MIN", 1024)
    monkeypatch.setattr(proactive, "_pick_route", _route_stub(server + "/ranged"))
    monkeypatch.setattr(proactive, "_expected_sha256", lambda *a, **k: (SHA, ""))
    _Handler.ranges_seen = []
    r = proactive.download_asset(_asset(server + "/ranged"), tmp_path)
    assert r["ok"] is True and r["verified"] == "ok", r
    assert (tmp_path / ASSET_NAME).read_bytes() == PAYLOAD, "分段拼回来必须逐字相等"
    starts = sorted({s.split("=")[1].split("-")[0] for _v, s in _Handler.ranges_seen
                     if s.startswith("bytes=")})
    assert len(starts) > 1, \
        "只看到一个起点 = 并发根本没开(把 PARALLEL_SEGS 改成 1 时这一句就该红):%s" \
        % _Handler.ranges_seen
    assert len(starts) >= proactive.PARALLEL_SEGS, \
        "只看到一个起点 = 并发没开(或它被静默降级了):%s" % _Handler.ranges_seen
    assert _Handler.bytes_sent == len(PAYLOAD), \
        "服务器被要走 %d 字节而这一件只有 %d —— 并发那一趟的结果没被当数" \
        % (_Handler.bytes_sent, len(PAYLOAD))


def test_the_parallel_pass_is_never_followed_by_a_second_full_download(
        server, tmp_path, monkeypatch):
    """v0.9.181 的真缺陷:并发把 135 MB 下完之后,代码又走了一遍顺序那条路。

    顺序那条路从 0 重写(`_resumable()` 把 mode=parallel 的半截判为不可用 —— 那是对的),
    于是**已经下好的整件被截断重下**;那一遍再一失败,完成品已经被 `_drop_part` 删了,
    最后 `part.stat()` 直接抛异常 —— 用户看到的是进度条走到 100% 然后"下载这一步炸了",
    盘上一个文件都没有。判据不是"最后有没有文件",而是**服务器被要走多少字节**:
    一次探测(几条连接各一小段)+ 一趟整件,不许再来一整件。
    """
    monkeypatch.setattr(proactive, "PARALLEL_MIN", 1024)
    monkeypatch.setattr(proactive, "PROBE_BYTES", 1024)     # 探测只许试一小截,别让它顶一件
    monkeypatch.setattr(proactive, "_expected_sha256", lambda *a, **k: (SHA, ""))
    r = proactive.download_asset(_asset(server + "/ranged"), tmp_path)
    assert r["ok"] is True and r["verified"] == "ok", r
    budget = len(PAYLOAD) + 1024 * proactive.PROBE_SEGS
    assert _Handler.bytes_sent <= budget, \
        "整件被下了两遍:一趟 %d + 探测 %d = %d,实测 %d —— 并发那趟的结果又被顺序那条路重下" \
        % (len(PAYLOAD), 1024 * proactive.PROBE_SEGS, budget, _Handler.bytes_sent)


def test_the_parallel_path_prefers_the_cdn_link_and_keeps_api_as_the_fallback():
    """并发那条路先试 CDN,api 退回兜底;顺序那条路照旧 api 优先。

    判据是**顺序**本身而不是速率:小预算探测分不开两条路(2026-10-17 三轮里 api 赢八次,
    其中一次还是 api 自己回 403 才让 CDN 赢),而把探测开到 8/16 条会在一小时内把匿名
    配额(60 次)烧穿 —— 403 时 `_pick_route` 回 None,整条下载反而退回单连接。
    api 排首选的理由(有些机器 `github.com:443` 整台不通)是**可达性**,兜底那条正好用它。
    """
    api = "https://api.github.com/repos/x/releases/assets/1"
    cdn = "https://github.com/x/releases/download/v1/appupdate.exe"
    assert proactive._parallel_urls([api, cdn]) == [cdn, api]
    assert proactive._parallel_urls([api]) == [api], "只有一条时无序可排,别把它排没了"
    assert proactive._parallel_urls([cdn]) == [cdn]
    loop = ["http://127.0.0.1:1/a", "http://127.0.0.1:1/b"]
    assert proactive._parallel_urls(loop) == loop, "本地测试服务器不该被这条规则动到"


def test_a_big_file_is_cut_into_more_chunks_than_connections(server):
    """块数要**比连接数多**,否则"空闲的连接去领下一块"这句话没有对象。

    142 MB 按连接数均分 = 每块 8.9 MB:实测那种情况下最后 10% 走了两分半(其余
    十五条早空了,只剩一条 0.09 MB/s 的在收尾)。按 4 MB 切就有 36 块可领,尾巴最多
    一块。反证:把 `_chunk_ranges` 换成 `_byte_ranges(total, PARALLEL_SEGS)` 这一支就红。
    """
    total = 141988826                       # v0.9.181 那件"仅 app"的真实尺寸
    ch = proactive._chunk_ranges(total)
    assert len(ch) > proactive.PARALLEL_SEGS, \
        "块数 %d ≤ 连接数 %d = 每根连接一块跟到底,慢的那块就是整件的剩余时间" \
        % (len(ch), proactive.PARALLEL_SEGS)
    assert all(e - s + 1 <= proactive.SEG_CHUNK for s, e in ch), ch[:3]
    assert [s for s, _e in ch] == sorted({s for s, _e in ch}), "块与块不许重叠或有缝"
    assert sum(e - s + 1 for s, e in ch) == total, "拼起来必须正好是整件"
    # 小文件反过来:不该切出比连接数还多的碎块(握手成本),也不该一块都凑不齐
    assert len(proactive._chunk_ranges(1000)) == min(proactive.PARALLEL_SEGS, 1000)
    assert proactive._chunk_ranges(0) == []


def test_the_route_probe_measures_the_condition_it_will_be_used_under(server):
    """择快路线必须**按并发**探,不能按单连接探。

    为什么(2026-10-17 实测):这台机器 `api.github.com` 一条 0.056 / 八条 0.105 MB/s,
    CDN 那条一条 0.038 / 八条 0.423 MB/s —— 单连接探会把首选投给 api,而真下载开多条
    时它恰恰是最慢的那条。判据不是"探到了速率",而是**服务器看到好几个不同的起点**:
    只有一个起点 = 又是单连接探测。顺带钉"服务器不认 Range"这条要如实报不可用。
    """
    _Handler.ranges_seen = []
    bps, honored, why = proactive._probe_route(server + "/ranged", None, 10.0,
                                               proactive.PROBE_SEGS, 4096)
    starts = {s.split("=")[1].split("-")[0] for _v, s in _Handler.ranges_seen}
    assert honored is True and bps > 0, (bps, honored, why)
    assert len(starts) >= proactive.PROBE_SEGS, \
        "探测只发了 %d 个起点 = 单连接探测,选出来的路线不代表并发时的快慢:%s" \
        % (len(starts), _Handler.ranges_seen)
    _Handler.ranges_seen = []
    bps, honored, why = proactive._probe_route(server + "/norange", None, 10.0,
                                               proactive.PROBE_SEGS, 4096)
    assert honored is False and bps == 0.0 and "不认 Range" in why, (bps, honored, why)


def test_one_flaky_segment_costs_a_resume_not_a_full_restart(server, tmp_path,
                                                             monkeypatch):
    """十几条连接里"有一块抖一下"的概率本来就比单连接高 —— 一次失败**不许把已下的全扔**。

    判据不是"最后文件对上了"(重来也能对上),而是**同一个起点被要了两次**:
    那块先 503,补领与第二轮都必须从它自己的偏移再要一次,而不是整件从 0 重来。
    这里故意让前**两**个"从第 N 字节起"的请求都失败:第一次是本轮内的补领,
    第二次才逼出第二轮 —— 少设一个,第二轮根本没被走到(针就空了)。
    """
    monkeypatch.setattr(proactive, "PARALLEL_MIN", 1024)
    monkeypatch.setattr(proactive, "PARALLEL_SEGS", 2)
    monkeypatch.setattr(proactive, "_pick_route", _route_stub(server + "/ranged"))
    monkeypatch.setattr(proactive, "_expected_sha256", lambda *a, **k: (SHA, ""))
    _Handler.fail_nonzero_first = 2
    r = proactive.download_asset(_asset(server + "/ranged"), tmp_path)
    assert r["ok"] is True and r["verified"] == "ok", r
    assert (tmp_path / ASSET_NAME).read_bytes() == PAYLOAD
    starts = [int(s.split("=")[1].split("-")[0]) for _v, s in _Handler.ranges_seen
              if s.startswith("bytes=")]
    assert len([s for s in starts if s > 0]) >= 2, \
        "503 之后没有再从那一段的起点要过一次 = 整件重来了:%s" % _Handler.ranges_seen
    # 真正区分新旧行为的判据:**第一轮已经下完的那一段,第二轮不许再要一遍**。
    # 这里探测被替掉了,所以起点 0 只允许出现两次:一次是第一段下完、一次是第二轮续。
    assert starts.count(0) <= 2, \
        "第一轮下完的段被重下了(轮间没记住各自到哪儿):%s" % _Handler.ranges_seen


def test_parallel_steps_down_when_the_server_ignores_range(server, tmp_path,
                                                           monkeypatch):
    """服务器不认 Range(带 Range 来也只给整件)→ 照旧单连接,**不许拼出洞**。

    这是并发那条路的退路:并发是优化,不是新前提。
    """
    monkeypatch.setattr(proactive, "PARALLEL_MIN", 1024)
    monkeypatch.setattr(proactive, "_expected_sha256", lambda *a, **k: (SHA, ""))
    r = proactive.download_asset(_asset(server + "/norange"), tmp_path)
    assert r["ok"] is True and r["verified"] == "ok", r
    assert (tmp_path / ASSET_NAME).read_bytes() == PAYLOAD


def test_a_parallel_part_is_never_spliced_by_the_sequential_path(server, tmp_path,
                                                                 monkeypatch):
    """分段下到一半的 `.part` **中间有洞**:顺序那条路看见就该当它不可用。

    不然表现是"把两份不同来源的字节接在一起",最后那个 sha256 指向一个谁都没发布过
    的文件 —— 而它会被当成"下好了"。
    """
    part = tmp_path / (ASSET_NAME + ".part")
    part.write_bytes(b"X" * 40 + b"\x00" * 40)          # 前 40 字节是好的,后面是洞
    proactive._write_sidecar(part, {"name": ASSET_NAME, "total": len(PAYLOAD),
                                    "validator": '"abc123"', "bytes": 80,
                                    "mode": "parallel", "segs": [80, 80, 80, 80]})
    monkeypatch.setattr(proactive, "PARALLEL_MIN", 10 ** 12)   # 逼它走顺序那条路
    monkeypatch.setattr(proactive, "_expected_sha256", lambda *a, **k: (SHA, ""))
    r = proactive.download_asset(_asset(server + "/ranged"), tmp_path)
    assert r["ok"] is True and r["verified"] == "ok", r
    assert (tmp_path / ASSET_NAME).read_bytes() == PAYLOAD, "洞里那 40 个 X 没被清掉"


def test_cancel_midway_keeps_the_segment_offsets(server, tmp_path, monkeypatch):
    """取消之后条子上要留着**每段各到哪儿了**,下次是四段一起续而不是一段。"""
    monkeypatch.setattr(proactive, "PARALLEL_MIN", 1024)
    monkeypatch.setattr(proactive, "_expected_sha256", lambda *a, **k: (SHA, ""))
    calls = {"n": 0}

    def stop():
        calls["n"] += 1
        return calls["n"] > 3
    r = proactive.download_asset(_asset(server + "/ranged"), tmp_path,
                                 should_stop=stop)
    if r.get("cancelled"):
        part = tmp_path / (ASSET_NAME + ".part")
        rec = proactive._read_sidecar(part)
        assert rec.get("mode") == "parallel", rec
        assert len(rec.get("segs") or []) == proactive.PARALLEL_SEGS, rec
        assert isinstance(rec.get("bps"), int) and rec.get("bytes", 0) > 0, rec
        pend = proactive.pending_part(tmp_path)
        assert pend.get("bytes") == rec["bytes"], pend
    else:
        pytest.skip("这台机器太快,四段都在取消之前下完了(不是缺陷)")


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


def test_the_skip_line_quotes_the_download_saving_not_content_bytes():
    """卡上那句"跳过多少"必须是**少下的下载量** —— 包内探针替我抓出来的第二个口径。

    实测:那两块数据解压后合计 1946 MB,而整包(878 MB)与小件(135 MB)的差是 743 MB。
    原实现报的是前者,于是卡上写"这次跳过 1946 MB · 这次要下 135 MB",两个数字各说
    各话(发布说明里写的却是 743 MB)。没有整包大小可报时,退回说内容字节并把话说清。
    """
    p = proactive.plan_update("setup", MAN, dict(SAME), full_size=920614741)
    assert "少下 743 MB" in p["reason"], p["reason"]
    assert "1946 MB" not in p["reason"] and "1249 MB" not in p["reason"], p["reason"]
    # 清单里本来就带整包大小,调用方不传也应该报得出(这是 `check_update` 的实际形状)
    q = proactive.plan_update("setup", MAN, dict(SAME))
    assert "少下 743 MB" in q["reason"], q["reason"]
    # 两者都不肯说谎:宁可改口说"那两件有多大",也不编一个少下的数
    no_full = {"identity": MAN["identity"], "assets": dict(MAN["assets"])}
    no_full["assets"].pop("setup")
    r = proactive.plan_update("setup", no_full, dict(SAME))
    assert "跳过" in r["reason"] and "解压后" in r["reason"], r["reason"]


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


def test_the_two_sides_of_the_identity_use_one_projection(tmp_path, monkeypatch):
    """构建端写的身份与 App 端读的身份**必须是同一套字段** —— 这一条是包内实测替我补的。

    真实经过:路 1 在源码态 45 支测试全绿,拿装好的包跑端到端才发现"永远退回整包"。
    根因是清单里的身份多带了一个 `schema` 字段(构建端自己从凭证里整份抄),而本机
    投影只有三个字段 —— `_same_identity` 按"要比对的全部键"逐项比,永远差一个键,
    于是**永远判不一致**。两侧各写一份投影,就一定会漂;所以两边都改成调
    `proactive.identity_of`,而这一支针钉的就是"两侧对同一份凭证得到同一份身份"。
    """
    import json as _json
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import build_exe
    from minicode import library, paths

    dist = tmp_path / "dist" / "引路学长"
    (dist / "crawled" / "mfindgzyykj.libsp.cn").mkdir(parents=True)
    db = dist / "crawled" / "mfindgzyykj.libsp.cn" / "library.db"
    db.write_bytes(b"fake db")
    lib_stamp = db.with_name(db.name + library.STAMP_SUFFIX)
    lib_stamp.write_text(_json.dumps(
        {"schema": 1, "file": db.name, "size": 1309589504, "count": 1331774,
         "sha256": "b" * 64, "built_at": "2026-10-16 12:00:00",
         "app_version": "0.9.178"}), encoding="utf-8")
    (dist / "ms-playwright").mkdir()
    # 内核目录要**真的有东西**才算有内核(见 test_a_credential_without_its_kernel…)
    (dist / "ms-playwright" / "chromium-1234").mkdir()
    (dist / "ms-playwright" / "chromium-1234" / "chrome.exe").write_bytes(b"y")
    ker_stamp = dist / "browser-kernel.stamp.json"
    ker_stamp.write_text(_json.dumps(
        {"schema": 1, "kind": "browser-kernel", "files": 603, "bytes": 731426653,
         "digest": "d" * 64, "built_at": "2026-10-16 12:00:00"}), encoding="utf-8")
    # 构建端:写清单
    monkeypatch.setattr(build_exe, "ROOT", tmp_path)
    fake = tmp_path / "dist" / "fake-setup.exe"
    fake.parent.mkdir(exist_ok=True)
    fake.write_bytes(b"z" * 10)
    man_path = build_exe.assets_manifest(dist, {"setup": fake}, lib_stamp, ker_stamp)
    man = _json.loads(man_path.read_text(encoding="utf-8"))
    # App 端:读本机凭证
    monkeypatch.setattr(library, "db_path", lambda workspace=None: db)
    monkeypatch.setattr(paths, "APP_DIR", dist)
    local = proactive.local_identities()
    for kind in ("library", "browser_kernel"):
        want, got = man["identity"][kind], local[kind]
        assert "schema" not in want and "built_at" not in want, (kind, want)
        assert set(want) == set(got), (kind, want, got)
        assert proactive._same_identity(got, want) is True, \
            f"{kind}:两侧对同一份凭证算出了不同身份 ⇒ 路 1 永远退回整包"


def test_identity_of_drops_everything_but_content_fields():
    """投影只留内容字段:`schema`(文档格式版本)与 `built_at`(时间)都不是身份。"""
    raw = {"schema": 1, "size": 5, "count": 6, "sha256": "a", "built_at": "x",
           "app_version": "0.9.178", "file": "library.db"}
    assert proactive.identity_of(raw, "library") == {"size": 5, "count": 6, "sha256": "a"}
    assert proactive.identity_of(None, "library") == {}
    assert proactive.identity_of(raw, "没有这一类") == {}


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
    # 凭证要**配得上本体**:内核目录得真的在(见下面那支"空头凭证"的针)
    (tmp_path / "ms-playwright" / "chromium-1234").mkdir(parents=True)
    (tmp_path / "ms-playwright" / "chromium-1234" / "chrome.exe").write_bytes(b"x")
    monkeypatch.setattr(paths, "APP_DIR", tmp_path)
    got = proactive.local_identities()
    assert got["library"] == {"size": 1331774, "count": 9, "sha256": "a" * 64}, got
    assert got["browser_kernel"] == {"files": 3, "bytes": 4, "digest": "d" * 64}, got
    assert "built_at" not in got["library"], "时间噪声不能进身份比对"


def test_a_credential_without_its_kernel_is_not_a_credential(tmp_path, monkeypatch):
    """躺在根目录的那份内核凭证,**不能替一台没有内核的机器作证**。

    两条真会发生的来路:①用户为了腾磁盘把 `ms-playwright/` 删了(凭证还在);
    ②有人拿"仅 app"包去新机器上装 —— 那份凭证在包的**根目录**,不在被排除的
    `ms-playwright/` 底下,漏排就等于把"我有一整套内核"这句话装过去,以后每次更新
    都会跳过内核而**永远补不回来**。iss 那边已经把它一起排除了(下面那支针),这一支
    管的是运行时这一半:证明不了就当不一致,这是这一格唯一不许犯的错。
    """
    import json as _json
    from minicode import library, paths
    monkeypatch.setattr(library, "db_path", lambda workspace=None: None)
    (tmp_path / "browser-kernel.stamp.json").write_text(_json.dumps(
        {"files": 3, "bytes": 4, "digest": "d" * 64}), encoding="utf-8")
    monkeypatch.setattr(paths, "APP_DIR", tmp_path)
    assert proactive.local_identities()["browser_kernel"] == {}, \
        "凭证在、内核不在 —— 这不是'对上了'"
    (tmp_path / "ms-playwright").mkdir()          # 空目录也不算有内核
    assert proactive.local_identities()["browser_kernel"] == {}
    plan = proactive.plan_update("setup", MAN, proactive.local_identities())
    assert plan["choice"] == "setup" and "内核目录不在了" in plan["reason"], plan


def test_the_app_only_installer_excludes_the_kernel_credential_too():
    """`gcode.iss` 的"仅 app"那一支必须把 `browser-kernel.stamp.json` 一起排掉。

    这是对**构建脚本**的针,不是对源码的:漏了它不会有任何测试变红,表现是上面那支
    运行时针救不了的形状(新机器装完自带一张空头凭证)。
    """
    iss = (Path(__file__).resolve().parent.parent / "packaging" / "gcode.iss")
    ls = iss.read_text(encoding="utf-8").splitlines()
    # 按**整行**切段:那份 .iss 里 "[Files]" 与 "#ifdef AppOnly" 都在注释里出现过,
    # 拿 str.split 切会切到注释那一截,表现是"针找不到 Source 行"。
    a = ls.index("[Files]")
    b = next(i for i, l in enumerate(ls) if i > a and l == "[Icons]")
    files = ls[a:b]
    i_app = next(i for i, l in enumerate(files) if l.startswith("#ifdef AppOnly"))
    i_else = next(i for i, l in enumerate(files) if l.startswith("#else"))
    src = [l for l in files[i_app:i_else] if l.startswith("Source:")]
    assert len(src) == 1, src
    for name in ("crawled\\*", "ms-playwright\\*", "browser-kernel.stamp.json"):
        assert name in src[0], (name, src[0])
    tail = [l for l in files[i_else:] if l.startswith("Source:")]
    assert len(tail) == 1 and "Excludes" not in tail[0], \
        "整包那一支不该排任何东西:" + str(tail)


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
        # **排在整包之前** —— 这是 GitHub 的真实形状(资产按名字字母序返回,
        # `…-app-setup.exe` 字母序就在 `…-setup.exe` 前面)。上一版这份 fixture 把
        # 整包写在第一位,于是"后缀兜底会选中哪件"这件事从来没被测对过。
        assets.insert(0, {"name": "yinlu-xuezhang-9.9.9-app-setup.exe",
                          "size": 141962981,
                          "url": "https://api.github.com/repos/o/r/releases/assets/2",
                          "browser_download_url": "https://github.com/x/app.exe"})
    return {"tag_name": "v9.9.9", "name": "引路学长 v9.9.9", "html_url": "https://x",
            "published_at": "2026-10-16T00:00:00Z", "body": "改一行", "assets": assets}


def test_the_manifest_names_are_the_names_we_publish(tmp_path, monkeypatch):
    """清单里的 `name` = 发布端真正上传的那个名字 = App 挑得到那件的名字(**三方同源**)。

    v0.9.179 实测抓到:清单记的是**本机文件名** `引路学长-…-setup.exe`,而发布页上叫
    `yinlu-xuezhang-…-setup.exe`(上传时改名),App 端 `pick_asset` 拿清单里的名字逐字比 ——
    永远挑不到,于是"该下哪一件"退化成"找不到那件"(界面变成「打开发布页」,或按整包下
    并告诉用户"清单说可以只下小包但发布里没有那件")。反证:把 build_exe 改回 `Path(v).name`
    → 这一支红。
    """
    import json as _json
    import sys
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root / "scripts"))
    import build_exe
    from publish_release import published_names

    dist = tmp_path / "dist" / "引路学长"
    dist.mkdir(parents=True)
    v = build_exe.__version__
    files = {}
    for key, local in (("setup", f"引路学长-{v}-setup.exe"),
                       ("zip", f"引路学长-{v}-win64.zip"),
                       ("single_file", "引路学长-单文件版.exe"),
                       ("app_setup", f"引路学长-{v}-app-setup.exe")):
        p = dist / local
        p.write_bytes(b"z" * 8)
        files[key] = p
    monkeypatch.setattr(build_exe, "ROOT", tmp_path)
    man_path = build_exe.assets_manifest(dist, files, None, None)
    man = _json.loads(man_path.read_text(encoding="utf-8"))
    want = published_names(build_exe.__version__)
    rel = {"assets": [{"name": want[k], "size": 8,
                       "url": "https://api.github.com/repos/o/r/releases/assets/%d" % i}
                      for i, k in enumerate(files)]}
    for k in files:
        assert man["assets"][k]["name"] == want[k], (k, man["assets"][k], want[k])
        # App 那一头:按清单里的名字去真发布页挑,必须挑得着、且就是这一件
        got = proactive.pick_asset(rel, "setup", man["assets"][k]["name"])
        assert got and got["name"] == want[k], (k, got)


def test_a_machine_without_the_credential_computes_it_once_then_reads(tmp_path, monkeypatch):
    """老包(0.9.178 及更早)没有那份凭证 —— 更新判定那一次允许**现算并补写**。

    为什么允许:实测 697 MB / 603 个文件 1.0 秒算完,而它换来的是少下 743 MB;
    不补写的话每次检查都要重算,所以算完要落盘,第二次只读几十个字节的 JSON。
    """
    from minicode import library, paths
    exe = tmp_path / "exe"
    (exe / "ms-playwright" / "chromium-1").mkdir(parents=True)
    (exe / "ms-playwright" / "chromium-1" / "chrome.exe").write_bytes(b"abc")
    (exe / "ms-playwright" / "chromium-1" / "note.txt").write_bytes(b"de")
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(library, "db_path", lambda workspace=None: None)
    monkeypatch.setattr(paths, "FROZEN", True)
    monkeypatch.setattr(sys, "executable", str(exe / "引路学长.exe"))
    monkeypatch.setattr(paths, "APP_DIR", data)

    assert proactive.local_identities()["browser_kernel"] == {}, \
        "默认(启动那条路)不付这笔计算"
    assert not (exe / "browser-kernel.stamp.json").exists(), "没让算就不许写盘"
    got = proactive.local_identities(allow_compute=True)["browser_kernel"]
    assert got == {"files": 2, "bytes": 5, "digest": got["digest"]}, got
    stamp = exe / "browser-kernel.stamp.json"
    assert stamp.is_file(), "算过就要落盘,否则每次检查都重算 731 MB"
    assert "computed_by" in stamp.read_text(encoding="utf-8"), "凭证要说清它是本机现算的"
    assert proactive.local_identities()["browser_kernel"] == got, "第二次走读凭证那条路"


def test_the_credential_is_read_from_next_to_the_exe(tmp_path, monkeypatch):
    """装进 `Program Files` 时 `APP_DIR` 会搬到 `%LOCALAPPDATA%`,而凭证是**随包装在 exe 旁**的。

    只认 APP_DIR 的那版代码在这类机器上永远读不到凭证 → 永远下整包(安全,但一分没省)。
    """
    import json as _json
    from minicode import library, paths
    exe = tmp_path / "ProgramFiles"
    (exe / "ms-playwright").mkdir(parents=True)
    (exe / "ms-playwright" / "a").write_bytes(b"x")
    exe.joinpath("browser-kernel.stamp.json").write_text(_json.dumps(
        {"schema": 1, "files": 1, "bytes": 1, "digest": "d" * 64,
         "built_at": "构建时写的"}), encoding="utf-8")
    data = tmp_path / "LocalAppData"      # 只读装的机器上数据搬到这里,那里没有凭证
    data.mkdir()
    monkeypatch.setattr(library, "db_path", lambda workspace=None: None)
    monkeypatch.setattr(paths, "FROZEN", True)
    monkeypatch.setattr(sys, "executable", str(exe / "引路学长.exe"))
    monkeypatch.setattr(paths, "APP_DIR", data)
    assert proactive.local_identities()["browser_kernel"] == {
        "files": 1, "bytes": 1, "digest": "d" * 64}


def test_kernel_roots_point_at_the_exe_dir_not_the_internal_dir(tmp_path, monkeypatch):
    """包内 `paths.RESOURCE_DIR` 是 `_internal`(= `_MEIPASS`),而内核与凭证在 **exe 旁**。

    这一条是包内探针第⑦段实测抓到的:用 RESOURCE_DIR 当第一根时,现算的凭证被写进
    `_internal/browser-kernel.stamp.json`,而装进 `Program Files` 的机器上 exe 旁那份读不到、
    数据目录里又没有 ⇒ 永远下整包,这一版等于白做。
    """
    from minicode import paths
    exe = tmp_path / "app"
    exe.mkdir(exist_ok=True)
    monkeypatch.setattr(paths, "FROZEN", True)
    monkeypatch.setattr(paths, "RESOURCE_DIR", exe / "_internal")
    monkeypatch.setattr(paths, "APP_DIR", tmp_path / "LocalAppData")
    monkeypatch.setattr(sys, "executable", str(exe / "引路学长.exe"))
    roots = proactive.kernel_roots()
    assert roots[0] == exe, roots
    assert exe / "_internal" not in roots, "不许把运行时算出来的凭证写进 _MEIPASS"


def test_the_build_and_the_app_share_one_kernel_digest(tmp_path, monkeypatch):
    """构建端写的那份摘要必须与 App 端算的**出自同一份代码**(§7 (o) 的第三次现身)。"""
    import sys
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root / "scripts"))
    import build_exe

    exe = tmp_path / "exe"
    (exe / "ms-playwright" / "sub").mkdir(parents=True)
    (exe / "ms-playwright" / "sub" / "a.bin").write_bytes(b"hello")
    (exe / "ms-playwright" / "b.bin").write_bytes(b"world!!")
    out = build_exe.stamp_kernel(exe)
    assert out and out.name == proactive.KERNEL_STAMP_NAME
    written = json.loads(out.read_text(encoding="utf-8"))
    assert proactive.kernel_identity(exe / "ms-playwright") == {
        "files": written["files"], "bytes": written["bytes"],
        "digest": written["digest"]}, "两侧算出了两个摘要 ⇒ 这台机器永远跳不过内核"
    # 拷贝一遍(时间戳全变)身份不许变 —— 这是"按目录字节流哈希"会坏掉的地方
    import shutil
    copy = tmp_path / "copy" / "ms-playwright"
    shutil.copytree(exe / "ms-playwright", copy)
    assert proactive.kernel_identity(copy)["digest"] == written["digest"]


def test_the_suffix_fallback_never_hands_out_the_app_only_package():
    """**没给名字**时按后缀兜底,也绝不许把 `…-app-setup.exe` 当整包递出去。

    这是 v0.9.178 上炉刚发出去的那个形状:老机器(没有内核凭证)判成"下整包",
    `want_name` 留空走后缀,而 `-app-setup.exe` 也以 `-setup.exe` 结尾、字母序又排在
    前面 → 用户装回一台**没有馆藏库也没有内核**的机器,卡上却写着"按整包下"。
    """
    rel = _fake_release()
    assert [a["name"] for a in rel["assets"]][0].endswith(proactive.APP_ONLY_SUFFIX)
    got = proactive.pick_asset(rel, "setup", "")
    assert got and got["name"] == "yinlu-xuezhang-9.9.9-setup.exe", got
    assert got["size"] == 920614741, "选中的必须是整包那一件"
    # 按名字取那一路照旧赢(清单里写了哪件就是哪件),这条针不许把它一起"修"回去
    small = proactive.pick_asset(rel, "setup", "yinlu-xuezhang-9.9.9-app-setup.exe")
    assert small and small["size"] == 141962981, small


def test_check_update_names_the_full_package_when_it_falls_back(monkeypatch):
    """清单在场时**两件的名字都从清单取** —— 判定与手里那件必须是同一件事。"""
    monkeypatch.setattr(proactive, "_release_payload",
                        lambda timeout=10.0: (_fake_release(), ""))
    monkeypatch.setattr(proactive, "running_form", lambda: "setup")
    monkeypatch.setattr(proactive, "fetch_release_manifest",
                        lambda rel, ver, timeout=12.0: (MAN, ""))
    empty = {"library": {}, "browser_kernel": {}}      # 老包:证明不了一致
    monkeypatch.setattr(proactive, "local_identities",
                        lambda allow_compute=False: dict(empty))
    u = proactive.check_update("0.9.177", detail=True)["update"]
    assert u["plan"]["choice"] == "setup" and "没有凭证" in u["plan"]["reason"]
    assert u["asset"]["name"] == "yinlu-xuezhang-9.9.9-setup.exe", \
        "说下整包却递出小包 = 用户装回一台没有馆藏库的机器"
    assert u["asset"]["size"] == 920614741


def test_check_update_end_to_end_selects_the_small_asset(monkeypatch):
    """整条判定链走一遍(假 release + 假清单,**真** `plan_update`/`pick_asset`)。"""
    monkeypatch.setattr(proactive, "_release_payload",
                        lambda timeout=10.0: (_fake_release(), ""))
    monkeypatch.setattr(proactive, "running_form", lambda: "setup")
    monkeypatch.setattr(proactive, "fetch_release_manifest",
                        lambda rel, ver, timeout=12.0: (MAN, ""))
    monkeypatch.setattr(proactive, "local_identities",
                        lambda allow_compute=False: dict(SAME))
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
    monkeypatch.setattr(proactive, "local_identities",
                        lambda allow_compute=False: dict(SAME))
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
    """v0.9.178 多了两件产物名,v0.9.181 又把其中一件**改了名** —— 三种形状都得认。

    它们**必须**进那道文件名形状闸,否则清理永远不认识它们 —— 表现是"用户按了清理,
    腾出 0 字节,而盘上躺着三件旧的小包"。这条针钉的就是"新产物有没有交登记税",
    而**改名也是一次登记**:旧名字 `app-setup.exe` 还躺在很多人的 `updates/` 里。
    """
    cur = proactive._cur_version()
    for n in ("yinlu-xuezhang-0.9.50-app-setup.exe",
              "yinlu-xuezhang-0.9.51-appupdate.exe",
              "yinlu-xuezhang-0.9.50-assets.json",
              f"yinlu-xuezhang-{cur}-appupdate.exe"):
        (tmp_path / n).write_bytes(b"z" * 7)
    r = proactive.prune_updates(tmp_path, keep_version="")
    assert [x["name"] for x in r["removed"]] == [
        "yinlu-xuezhang-0.9.50-app-setup.exe",
        "yinlu-xuezhang-0.9.50-assets.json",
        "yinlu-xuezhang-0.9.51-appupdate.exe"], r
    assert (tmp_path / f"yinlu-xuezhang-{cur}-appupdate.exe").exists(), "当前版要留"


def test_the_app_only_package_is_not_named_like_the_full_setup():
    """「仅 app」那件的名字**不许以 `setup.exe` 结尾** —— 老程序只按后缀挑。

    装在 0.9.177/0.9.178 上的那份字节码里 `pick_asset(release, form)` 没有 `want_name`
    参数、也**不读发布清单**,它取第一个以 `setup.exe` 结尾的资产;GitHub 按字母序返回,
    `…-app-setup.exe` 就在整包前面 ⇒ 老机器会下那件不含馆藏库与内核的小包,装完功能
    悄悄是坏的(§6 第 86 条:0.9.180 那件的发布页资产因此被撤)。改名是唯一能从根上
    挡住的办法 —— 我们改不了别人机器上已经装好的程序。
    """
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from publish_release import published_names

    nm = published_names("9.9.9")
    assert not nm["app_setup"].endswith("setup.exe"), nm
    assert nm["setup"].endswith("-setup.exe"), nm
    assets = sorted(({"name": nm[k]} for k in ("assets", "app_setup", "setup",
                                               "sha256")), key=lambda a: a["name"])
    old_pick = next(a for a in assets if a["name"].endswith("setup.exe"))
    assert old_pick["name"] == nm["setup"], \
        "老程序按后缀会挑到:%s —— 那件小包又回到陷阱位置了" % old_pick["name"]


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
