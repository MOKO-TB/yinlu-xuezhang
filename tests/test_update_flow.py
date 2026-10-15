"""就地更新那条链(v0.9.176)的针:挑件、下载、校验、交出去。

这一条比一般的下载代码更需要"真跑":它最后交出去的是用户会**双击执行**的 exe。
所以这里不起假对象糊弄 —— 下载那几支用**一个真的本地 HTTP 服务**(含 302 跳转,
因为 GitHub 的直链本来就跳 CDN,不跟跳转每次都会"下到 0 字节")。

网络出口只有一处被换掉:`proactive._http_get`(查版本 / 取校验文件),
换成喂假响应;`download_asset` 与 `list_releases` 的解析分支都走真代码。
"""
import hashlib
import http.server
import json
import threading
from pathlib import Path

import pytest

from minicode import proactive
from minicode.config import Config, Provider
from minicode.gui_bridge import create_app

PAYLOAD = ("引路学长安装包的样子" * 4096).encode("utf-8")   # ~100 KB,够切好几块
SHA = hashlib.sha256(PAYLOAD).hexdigest()


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):            # 测试里不往 stderr 打字
        pass

    def do_GET(self):
        if self.path == "/redirect":      # GitHub 的 browser_download_url 就是这个形状
            self.send_response(302)
            self.send_header("Location", "/real")
            self.end_headers()
            return
        if self.path == "/short":         # 接口说有一整件,实发一小截(诚实的短正文)
            self.send_response(200)
            self.send_header("Content-Length", "3")
            self.end_headers()
            self.wfile.write(b"abc")
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
    yield "http://127.0.0.1:%d" % srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def _asset(url, size=None, name="yinlu-xuezhang-9.9.9-setup.exe"):
    return {"name": name, "url": url, "size": size if size is not None else len(PAYLOAD)}


def _leftovers(tmp_path):
    """目录里剩下的"下载产物"。

    注意**不能**断言"目录是空的":`tests/conftest.py` 有个 autouse 夹具会在
    `tmp_path/app` 放隔离用的数据目录,那句"空目录"会因为它恒红 —— 而红在测试自己
    身上的闸,和没写闸一样糟。
    """
    return [p.name for p in tmp_path.iterdir()
            if p.is_file() and (p.name.endswith(".exe") or p.name.endswith(".part"))]


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
    monkeypatch.setattr(proactive, "_expected_sha256", lambda *a, **k: (SHA, ""))
    r = proactive.download_asset(_asset(server + "/short", size=len(PAYLOAD)), tmp_path)
    assert r["ok"] is False and "对不上" in r["why"]
    assert _leftovers(tmp_path) == []


def test_download_cancel_leaves_nothing_behind(server, tmp_path, monkeypatch):
    monkeypatch.setattr(proactive, "_expected_sha256", lambda *a, **k: (SHA, ""))
    calls = {"n": 0}

    def stop():
        calls["n"] += 1
        return True            # 第一块就取消:本地回环可能一次读完,不该依赖块数
    r = proactive.download_asset(_asset(server + "/real"), tmp_path, should_stop=stop)
    assert r["ok"] is False and r.get("cancelled")
    assert calls["n"] >= 1, "should_stop 一次都没被问 = 取消是假的"
    assert _leftovers(tmp_path) == [], "取消后留一个 920 MB 的 .part 是最难看的坏"


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
