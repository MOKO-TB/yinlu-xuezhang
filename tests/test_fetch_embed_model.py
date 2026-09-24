"""`scripts/fetch_embed_model.py` 的真下载测试(v0.9.39)。

这个脚本存在的理由是:`minicode/embed.py` 的提示语里点名了它。所以测试不写
"提示语长什么样",而是**真起一个 HTTP 服务、真下两个文件、真比对字节** ——
下不下来的时候要留下什么、不留什么,才是这个功能唯一需要钉住的东西。
"""
from __future__ import annotations

import hashlib
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import fetch_embed_model as fem       # noqa: E402


@pytest.fixture
def server(tmp_path_factory):
    """本地假仓库:serve 一个临时目录,URL 形状与 HuggingFace 的 resolve/main 一致。"""
    root = tmp_path_factory.mktemp("repo")
    (root / "org" / "mini-bge" / "resolve" / "main").mkdir(parents=True)
    d = root / "org" / "mini-bge" / "resolve" / "main"
    (d / "model.onnx").write_bytes(b"\x00\x01\x02" * 4096)
    (d / "vocab.txt").write_bytes("你\n好\n".encode("utf-8"))   # 显式字节:别让 Windows 换行翻译改了内容
    httpd = ThreadingHTTPServer(("127.0.0.1", 0),
                                partial(SimpleHTTPRequestHandler, directory=str(root)))
    httpd.daemon_threads = True
    import threading
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_download_lands_the_exact_bytes(server, tmp_path):
    done = fem.fetch("org/mini-bge", "mini-bge", tmp_path, base=server,
                     files=["model.onnx", "vocab.txt"])
    sizes = {name: size for name, size, _ in done}
    assert sizes["model.onnx"] == (tmp_path / "mini-bge" / "model.onnx").stat().st_size
    onnx = (tmp_path / "mini-bge" / "model.onnx").read_bytes()
    assert onnx == b"\x00\x01\x02" * 4096
    digest = next(d for n, s, d in done if n == "model.onnx")
    assert digest == hashlib.sha256(onnx).hexdigest()


def test_no_partial_file_survives_a_failed_download(server, tmp_path):
    with pytest.raises(RuntimeError) as e:
        fem.fetch("org/mini-bge", "mini-bge", tmp_path, base=server,
                  files=["not-here.onnx"])
    assert "not-here.onnx" in str(e.value) and server in str(e.value)
    left = list((tmp_path / "mini-bge").glob("*")) if (tmp_path / "mini-bge").exists() else []
    assert left == [], f"没下成的文件在磁盘上留下了东西:{left}"


def test_pinned_checksum_rejects_a_different_file(server, tmp_path):
    with pytest.raises(RuntimeError) as e:
        fem.fetch("org/mini-bge", "mini-bge", tmp_path, base=server,
                  files=["vocab.txt"], sums={"vocab.txt": "0" * 64})
    assert "校验和不符" in str(e.value)
    assert not (tmp_path / "mini-bge" / "vocab.txt").exists()
    assert list((tmp_path / "mini-bge").glob("*.part")) == []


def test_pinned_checksum_that_matches_is_reported_as_verified(server, tmp_path, capsys):
    want = hashlib.sha256("你\n好\n".encode("utf-8")).hexdigest()
    rc = fem.main(["--repo", "org/mini-bge", "--base", server, "--dest", str(tmp_path),
                   "--files", "vocab.txt", "--sha256", f"vocab.txt={want}"])
    out = capsys.readouterr().out
    assert rc == 0 and (tmp_path / "mini-bge" / "vocab.txt").is_file()
    assert "已按你给的校验和核对" in out


def test_unpinned_download_never_claims_verification(server, tmp_path, capsys):
    rc = fem.main(["--repo", "org/mini-bge", "--base", server, "--dest", str(tmp_path),
                   "--files", "vocab.txt"])
    out = capsys.readouterr().out
    assert rc == 0 and "未钉校验和(只报指纹)" in out
    assert "校验通过" not in out


def test_repo_is_required_and_no_model_name_is_invented(tmp_path, capsys):
    assert fem.main(["--dest", str(tmp_path), "--files", "vocab.txt"]) == 2
    out = capsys.readouterr().out
    assert "--repo" in out and "不预设任何模型仓库名" in out


def test_check_mode_reports_what_is_missing(server, tmp_path, capsys):
    assert fem.main(["--repo", "org/mini-bge", "--dest", str(tmp_path), "--check"]) == 1
    assert "缺 model.onnx" in capsys.readouterr().out
    fem.fetch("org/mini-bge", "mini-bge", tmp_path, base=server, files=["model.onnx"])
    assert fem.main(["--repo", "org/mini-bge", "--dest", str(tmp_path),
                     "--check"]) == 1                       # 还缺 vocab.txt
    fem.fetch("org/mini-bge", "mini-bge", tmp_path, base=server, files=["vocab.txt"])
    assert fem.main(["--repo", "org/mini-bge", "--dest", str(tmp_path), "--check"]) == 0


def test_mirror_flag_overrides_the_base(tmp_path):
    args = fem.parse_args(["--repo", "org/x", "--mirror"])
    assert fem.url_for(args.base, "org/x", "model.onnx").startswith("https://huggingface.co")
    base = fem.MIRROR_BASE if args.mirror else args.base
    assert fem.url_for(base, "org/x", "model.onnx").startswith("https://hf-mirror.com/")


def test_detect_and_fetch_agree_on_the_layout(server, tmp_path, monkeypatch):
    """脚本写出来的目录结构,必须正好是 embed.detect() 认的那个。"""
    from minicode import embed
    monkeypatch.setattr(embed, "models_dir", lambda: tmp_path)
    cfg = type("C", (), {"capabilities": {"embeddings": {"backend": "local",
                                                         "model": "mini-bge"}},
                         "providers": []})()
    assert embed.detect(cfg)["ready"] is False              # 还没下
    fem.fetch("org/mini-bge", "mini-bge", tmp_path, base=server)
    info = embed.detect(cfg)
    # 文件齐了以后只剩一个可能的拦路石:本机装不装得到 onnxruntime。
    assert info["ready"] is embed._onnx_available()
    if not info["ready"]:
        assert "onnxruntime" in info["why"]
