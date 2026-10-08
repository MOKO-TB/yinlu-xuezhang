"""包内**本地语义档**端到端:验证装好的 exe 真能加载 onnxruntime、真跑那份 ONNX 权重,
并把池化口径如实写进「检索口径」那一行。

为什么这一场不能由 pytest 代替(v0.9.123 起 onnxruntime 与 numpy 进了包):
  1. 包里的 DLL 到底加载得起来吗 —— 构建机的 Anaconda 基底上 `import onnxruntime`
     会在 pyd 的 PyInit 里直接 access violation(2026-10-06 实测)。那种坏法 Python
     抓不住,不是异常,是进程没;只有真进程能给出答案。
  2. 权重是使用者手工放的(不进安装包),`models/<模型名>/model.onnx` 这个契约在包里
     认不认、那句提示对不对,源码态永远测不到那个目录布局。
  3. `embed.detect()` 只按 find_spec 判"有没有这个包",少收一件它照样报 ready ——
     这条链上没有任何一步会红,所以必须在包里回头看一次。

**读的是结构化出口,不是人话**(§6 #50(e)① 的第一个实例):跑打包 CLI 的 `--json`,
断言 stdout 第一个字符就是 `{`;「检索口径」那一行住在**工具返回值**里,而工具正文
只进留档不进 stdout,所以从 `session_file` 那一份里取 —— 顺带把"留档路径本身能不能
真打开"也钉上(v0.9.120 那个管道乱码的表现就是这条路打不开)。两样都比解析彩色
控制台字面稳。

链路:把 model.onnx + vocab.txt 放进 `<exe 目录>/models/bge-small-zh-v1.5/`
  → config.json 写 capabilities.embeddings 的 local 档 → 假 SSE 第一轮脚本化调用
  `search_knowledge`(semantic=true, build_index=true)→ 包里真的建索引并取查询向量。
  再跑一次**把权重挪走**的:必须如实写「向量档未跑」并点名缺哪两个文件,不许假称已跑。

跑完扫干净:权重、kb-vectors/、config.json、sessions/ 与两份运行账都不留在产物目录里
(下一轮出包是递归收 `dist/引路学长/*` 的,PyInstaller 不清它没重建的东西)。

用法:
  python scripts/e2e_frozen_embed.py
  python scripts/e2e_frozen_embed.py --weights _tmp_embed/modelstore
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from e2e_probe import is_probe_request, refuse_probe  # noqa: E402

MODEL_DIR_NAME = "bge-small-zh-v1.5"
CLI = ROOT / "dist" / "引路学长" / "引路学长-CLI.exe"
QUERY = "最近情绪很低落想找人聊聊去哪里"
ANSWER = "按语义找到了心理类资料。"
REQ = {"n": 0}


def _quiet() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _chunk(payload: dict) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


def _turn_tool_call() -> bytes:
    parts = [
        _chunk({"id": "1", "object": "chat.completion.chunk", "created": 1, "model": "fake",
                "choices": [{"index": 0, "delta": {"role": "assistant"},
                             "finish_reason": None}]}),
        _chunk({"id": "1", "object": "chat.completion.chunk", "created": 1, "model": "fake",
                "choices": [{"index": 0, "delta": {"content": "先按意思找一遍。\n"},
                             "finish_reason": None}]}),
        _chunk({"id": "1", "object": "chat.completion.chunk", "created": 1, "model": "fake",
                "choices": [{"index": 0, "delta": {"tool_calls": [{
                    "index": 0, "id": "call_kb", "type": "function",
                    "function": {"name": "search_knowledge",
                                 "arguments": json.dumps(
                                     {"query": QUERY, "top_k": 3,
                                      "semantic": True, "build_index": True},
                                     ensure_ascii=False)}}]},
                    "finish_reason": None}]}),
        _chunk({"id": "1", "object": "chat.completion.chunk", "created": 1, "model": "fake",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}),
        b"data: [DONE]\n\n",
    ]
    return b"".join(parts)


def _turn_text() -> bytes:
    parts = [
        _chunk({"id": "2", "object": "chat.completion.chunk", "created": 1, "model": "fake",
                "choices": [{"index": 0, "delta": {"content": ANSWER},
                             "finish_reason": None}]}),
        _chunk({"id": "2", "object": "chat.completion.chunk", "created": 1, "model": "fake",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}),
        b"data: [DONE]\n\n",
    ]
    return b"".join(parts)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8")
        if is_probe_request(body):
            refuse_probe(self)
            return
        REQ["n"] += 1
        # **按请求内容挑回合,不按第几次**:打包版一次对话里到底发几个请求不是定数
        # (启动核对之类会插进来),拿计数器排队就会把"该调工具那一回合"发给一个无关
        # 请求 —— 表现是真进程一句工具都没调,而红的是脚本自己(同 e2e_fake_server 的
        # REQ_COUNT 那一类坑)。
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {}
        msgs = payload.get("messages") if isinstance(payload, dict) else None
        msgs = msgs if isinstance(msgs, list) else []
        has_tool = any(isinstance(m, dict) and m.get("role") == "tool" for m in msgs)
        asked = any(isinstance(m, dict) and m.get("role") == "user"
                    and QUERY in str(m.get("content") or "") for m in msgs)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(_turn_tool_call() if (asked and not has_tool) else _turn_text())

    def log_message(self, *_a):
        pass


def _write_config(workspace: Path) -> Path:
    cfg = {"permission_mode": "accept_all",
           "capabilities": {"embeddings": {"backend": "local", "model": MODEL_DIR_NAME,
                                           "pooling": "cls", "dim": 512}}}
    path = workspace / "config.json"
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _copy_writable(src: Path, dst: Path) -> None:
    """拷进来之后**必须可写**:权重常来自微信/U 盘那种只读介质(原件就是 0444),
    连带只读位拷进来,下一次覆盖直接 PermissionError。"""
    shutil.copyfile(src, dst)
    os.chmod(dst, stat.S_IWRITE | stat.S_IREAD)


def _drop_tree(path: Path) -> None:
    """删一棵树先清只读位 —— 否则 `rmtree(ignore_errors=True)` 会**静默留下**那个
    只读的 model.onnx,下一轮 PermissionError,报出来的又是脚本自己的问题。"""
    if not path.is_dir():
        return
    for p in path.rglob("*"):
        if p.is_file():
            try:
                os.chmod(p, stat.S_IWRITE)
            except OSError:
                pass
    shutil.rmtree(path, ignore_errors=True)


def _cleanup(workspace: Path) -> None:
    (workspace / "config.json").unlink(missing_ok=True)
    # 这一场跑起来会往数据目录写这两份运行账,而下一轮出包是递归收 dist/引路学长/ 的
    # (PyInstaller 不清它没重建的东西)—— 留着就被打进分享包。
    for f in ("injection-events.log", "token-usage.db"):
        (workspace / f).unlink(missing_ok=True)
    for extra in ("sessions", "kb-vectors"):
        _drop_tree(workspace / extra)


def _tool_texts(session_file: str) -> str:
    """从留档里取所有 role=tool 的正文。「检索口径」就住在这里,不在 stdout。"""
    path = Path(session_file or "")
    if not path.is_file():
        return ""
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("role") == "tool":
            out.append(str(row.get("content") or ""))
    return "\n".join(out)


def _run_cli(exe: Path, workspace: Path, env: dict, prompt: str) -> dict:
    """跑一次打包 CLI 的 `--json`,返回退出码 / stdout 原文 / 解析出的那份结果。"""
    proc = subprocess.run([str(exe), "--json", prompt], cwd=str(workspace),
                          capture_output=True, env=env, timeout=1800,
                          stdin=subprocess.DEVNULL)
    raw = proc.stdout or b""
    # --json 那条路自 v0.9.120 起把 stdout 钉成 UTF-8(管道默认按 cp936 建流那次
    # 把留档路径编成了乱码)。这里**严格**解,解不动就是回归,不做 errors="replace"。
    text = raw.decode("utf-8", "strict") if raw else ""
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        doc = None
    return {"rc": proc.returncode, "stdout": text, "doc": doc,
            "tool": _tool_texts((doc or {}).get("session_file") or "")}


def main() -> int:
    _quiet()
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default=str(CLI))
    ap.add_argument("--weights", default=str(ROOT / "_tmp_embed" / "modelstore"),
                    help="放着 bge-small-zh-v1.5/model.onnx + vocab.txt 的目录")
    args = ap.parse_args()
    exe = Path(args.exe)
    if not exe.exists():
        print(f"!! 找不到 exe: {exe}(先运行 scripts/build_exe.py)")
        return 1
    src = Path(args.weights) / MODEL_DIR_NAME
    if not (src / "model.onnx").is_file() or not (src / "vocab.txt").is_file():
        print(f"!! 本机没有权重: {src} 里缺 model.onnx 或 vocab.txt")
        print("   这一场验的是包内这条路,不是「这个包不支持」—— 先放权重再跑。")
        return 2

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = {**os.environ,
           "YINLUXS_LLM_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
           "YINLUXS_LLM_API_KEY": "test-key",
           "YINLUXS_LLM_MODEL": "fake-model"}

    workspace = exe.parent
    model_dir = workspace / "models" / MODEL_DIR_NAME
    cache_dir = workspace / "kb-vectors"
    checks: dict[str, bool] = {}
    notes: list[str] = []
    try:
        model_dir.mkdir(parents=True, exist_ok=True)
        _copy_writable(src / "model.onnx", model_dir / "model.onnx")
        _copy_writable(src / "vocab.txt", model_dir / "vocab.txt")
        _write_config(workspace)

        # ① 带着权重跑一次:要真建索引、真取查询向量,并把口径写进留档
        REQ["n"] = 0
        one = _run_cli(exe, workspace, env, f"用语义找一下:{QUERY}")
        doc = one["doc"] or {}
        cache_files = list(cache_dir.glob("*.json")) if cache_dir.is_dir() else []
        items = 0
        if cache_files:
            try:
                items = len((json.loads(cache_files[0].read_text(encoding="utf-8"))
                             or {}).get("items") or {})
            except (OSError, json.JSONDecodeError):
                items = 0
        checks["退出码 0 且 stdout 是一份能解析的 JSON"] = one["rc"] == 0 and bool(doc)
        checks["stdout 第一个字符就是 {(没横幅漏进来)"] = one["stdout"][:1] == "{"
        checks["留档路径真存在(那条曾经被管道编码改坏的路)"] = bool(
            doc.get("session_file")) and Path(doc["session_file"]).is_file()
        checks["工具正文回填给模型了"] = doc.get("response") == ANSWER and bool(one["tool"])
        checks["包内向量档真跑了"] = "向量档已跑" in one["tool"]
        checks["口径里写着池化 cls"] = "池化 cls" in one["tool"]
        checks["默认不给查询加指令"] = "查询无指令" in one["tool"]
        checks["索引落在 kb-vectors/"] = bool(cache_files)
        checks["索引条数不是空的"] = items > 0
        checks["缓存名带着口径"] = any("-cls-512" in p.name for p in cache_files)
        notes.append("索引 %d 条 · 缓存文件 %s · 建索引那一步 %s ms" % (
            items, cache_files[0].name if cache_files else "没有",
            (doc.get("elapsed_ms") or "?")))

        # ② 把权重挪走再跑一次:必须如实说未跑,并且不许出现已跑
        _drop_tree(model_dir)
        _drop_tree(cache_dir)
        REQ["n"] = 0
        two = _run_cli(exe, workspace, env, f"用语义找一下:{QUERY}")
        checks["缺权重那次也正常收尾(退出码 0)"] = two["rc"] == 0
        checks["缺权重时如实说未跑"] = "向量档未跑" in two["tool"]
        checks["并且点名缺什么文件"] = "model.onnx" in two["tool"] and "vocab.txt" in two["tool"]
        checks["缺权重时不许假称已跑"] = "向量档已跑" not in two["tool"]
        for tag, text in (("带着权重", one["tool"]), ("缺权重", two["tool"])):
            line = next((l for l in text.splitlines() if "检索口径" in l), "")
            notes.append("%s口径:%s" % (tag, line[:240] or "(留档里没有口径行)"))
    except subprocess.TimeoutExpired:
        checks["没超时"] = False
        notes.append("CLI 超时(1800s):建索引那一步慢得离谱或被卡住")
    finally:
        server.shutdown()
        _cleanup(workspace)
        _drop_tree(model_dir)
        parent = workspace / "models"
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()

    for name, ok in checks.items():
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    for n in notes:
        print("     ·", n)
    if not all(checks.values()):
        print("=== FROZEN EMBED E2E FAIL ===")
        return 1
    print("=== FROZEN EMBED E2E PASS(包内本地语义档跑得通,缺权重时也会如实说,"
          "而且走的是 --json 结构化出口) ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
