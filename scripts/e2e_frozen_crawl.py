"""冻结态「爬取 → 入库 → 下次命中」端到端验收。

模拟真实流程,全部用打包后的 exe:
  阶段 1:用户问"国家励志奖学金申请条件" → 假 LLM 决定调用 web_crawl(to_kb=true)
          → 打包版联网抓取 → 正文归档 + 条目写入工作区 kb/
  阶段 2:新起一个进程(等价于"用户下次再问") → 假 LLM 调用 search_knowledge
          → 断言检索结果里带着阶段 1 入库的标题与原文链接(无需再联网)
  阶段 3:全新目录(无爬取缓存)→ 应命中随包内置的校园主题语料

假 LLM 服务器会记录每个请求的 messages,用来直接检查工具返回内容。

用法:
  python scripts/build_exe.py --no-zip                 # 先构建(轻量版)
  python scripts/e2e_frozen_crawl.py                   # 验轻量版(内置轻量抓取器)
  python scripts/build_crawl4ai_exe.py                 # 再构建完整版
  python scripts/e2e_frozen_crawl.py --exe dist/引路学长-crawl4ai/引路学长-CLI.exe \\
      --expect-engine crawl4ai                          # 验完整版(真浏览器)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TARGET = "https://www.gzasc.edu.cn/xsc/info/1274/18391.htm"
TITLE_KEY = "国家励志奖学金"
NATIVE_MARK = "内置轻量抓取器"      # 轻量引擎在工具输出里的标识
REQUESTS: list[dict] = []          # 每个请求的 messages,用于检查工具返回


def chunk(payload: dict) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


def _tool_call(call_id: str, name: str, args: dict) -> bytes:
    return b"".join([
        chunk({"id": "1", "object": "chat.completion.chunk", "created": 1, "model": "fake",
               "choices": [{"index": 0, "delta": {"content": "好的,我来处理。\n"},
                            "finish_reason": None}]}),
        chunk({"id": "1", "object": "chat.completion.chunk", "created": 1, "model": "fake",
               "choices": [{"index": 0, "delta": {"tool_calls": [
                   {"index": 0, "id": call_id, "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]},
                   "finish_reason": None}]}),
        chunk({"id": "1", "object": "chat.completion.chunk", "created": 1, "model": "fake",
               "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}),
        b"data: [DONE]\n\n",
    ])


def _final(text: str) -> bytes:
    return b"".join([
        chunk({"id": "2", "object": "chat.completion.chunk", "created": 1, "model": "fake",
               "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]}),
        chunk({"id": "2", "object": "chat.completion.chunk", "created": 1, "model": "fake",
               "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}),
        b"data: [DONE]\n\n",
    ])


def _scenario(turn: int) -> bytes:
    """按请求序号返回剧本:抓取 → 收尾 → 检索 → 收尾 → 检索 → 收尾。"""
    if turn == 1:
        return _tool_call("call_crawl", "web_crawl", {
            "url": TARGET, "max_pages": 1, "max_depth": 0, "to_kb": True,
            "kb_source": "广州应用科技学院", "kb_category": "奖助学金",
            "kb_tags": ["奖学金", "国家励志奖学金"]})
    if turn == 2:
        return _final("已抓取官网并写入本地知识库,下次问同类问题可直接命中。")
    if turn in (3, 5):
        return _tool_call("call_search", "search_knowledge", {
            "query": "国家励志奖学金申请条件", "top_k": 3, "snippet_chars": 800})
    return _final("这是从本地知识库回答的。")


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            REQUESTS.append(json.loads(raw.decode("utf-8")))
        except Exception:
            REQUESTS.append({})
        turn = len(REQUESTS)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(_scenario(turn))

    def log_message(self, *args):
        pass


def _run_exe(exe: Path, prompt: str, workspace: Path, port: int) -> subprocess.CompletedProcess:
    # 模型固定为内置校园模型,e2e 用环境变量把端点临时指到本地假 LLM
    env = {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "YINLUXS_LLM_BASE_URL": f"http://127.0.0.1:{port}/v1",
        "YINLUXS_LLM_API_KEY": "test-key-frozen",
        "YINLUXS_LLM_MODEL": "fake-model",
    }
    return subprocess.run([str(exe), prompt], cwd=workspace, capture_output=True,
                          timeout=240, env=env)


def _tool_results(messages_request: dict) -> str:
    """取出请求里所有 role=tool 的消息内容(即工具返回给模型的东西)。"""
    out = []
    for msg in (messages_request or {}).get("messages", []) or []:
        if isinstance(msg, dict) and msg.get("role") == "tool":
            content = msg.get("content")
            out.append(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False))
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="冻结态爬取→入库→命中验收")
    ap.add_argument("--exe", default=str(ROOT / "dist" / "引路学长" / "引路学长-CLI.exe"),
                    help="被验收的 CLI exe 路径")
    ap.add_argument("--expect-engine", choices=["native", "crawl4ai"], default="native",
                    help="期望的抓取引擎:native=内置轻量抓取器,crawl4ai=真浏览器")
    args = ap.parse_args()

    cli_exe = Path(args.exe)
    assert cli_exe.exists(), f"请先构建: {cli_exe}"
    app_dir = cli_exe.parent
    print(f"验收目标: {cli_exe}(期望引擎: {args.expect_engine})")

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    config_path = app_dir / "config.json"
    original = config_path.read_bytes() if config_path.exists() else None
    config_path.write_text(json.dumps({
        "permission_mode": "accept_all",
    }, ensure_ascii=False), encoding="utf-8")

    workspace = Path(tempfile.mkdtemp(prefix="gcode-frozen-crawl-"))
    try:
        # ---- 阶段 1:未命中 → 爬取 → 入库 ----
        p1 = _run_exe(cli_exe, "查一下国家励志奖学金申请条件,本地没有再爬官网", workspace, port)
        assert p1.returncode == 0, \
            f"抓取阶段 exe 异常退出({p1.returncode}): {p1.stderr.decode('utf-8','replace')[-500:]}"
        kb_file = workspace / "kb" / "crawl-www.gzasc.edu.cn.json"
        assert kb_file.exists(), f"没有写入知识库: {kb_file}"
        entries = json.loads(kb_file.read_text(encoding="utf-8"))
        assert entries, "知识库文件为空"
        entry = next((e for e in entries if TITLE_KEY in e["title"]), entries[0])
        assert "申请条件" in entry["content"] or "平均分" in entry["content"], "正文不像通知"
        assert "首页" not in entry["content"][:200], "正文里还带着导航"
        crawl_output = _tool_results(REQUESTS[1])
        if args.expect_engine == "native":
            assert NATIVE_MARK in crawl_output, "轻量版应该走内置抓取器"
        else:
            assert NATIVE_MARK not in crawl_output, \
                f"完整版降级成了轻量抓取器(浏览器内核没找到?):\n{crawl_output[:400]}"
        archived = [f.name for f in (workspace / "crawled").rglob("*.md")]
        assert archived, "没有归档页面"
        print(f"阶段 1 PASS:爬取入库 {len(entries)} 条,归档 {archived}")
        print(f"          {entry['title'][:46]} | {len(entry['content'])} 字")

        # ---- 阶段 2:新进程再问同一个问题,应命中刚入库的内容 ----
        p2 = _run_exe(cli_exe, "国家励志奖学金申请条件是什么", workspace, port)
        assert p2.returncode == 0, \
            f"检索阶段 exe 异常退出({p2.returncode}): {p2.stderr.decode('utf-8','replace')[-500:]}"
        search_results = _tool_results(REQUESTS[-1])
        assert search_results, "没有捕获到 search_knowledge 的返回"
        assert TITLE_KEY in search_results, f"检索没有命中入库内容:\n{search_results[:400]}"
        assert entry["url"] in search_results, "检索结果里缺少原文链接"
        assert "平均分" in search_results, "加长摘要没带回原文要点"
        print("阶段 2 PASS:新进程直接命中本地知识库(无需联网)")

        # ---- 阶段 3:全新目录(没有爬取缓存),应命中随包内置的主题语料 ----
        fresh = Path(tempfile.mkdtemp(prefix="gcode-frozen-bundled-"))
        p3 = _run_exe(cli_exe, "国家励志奖学金申请条件是什么", fresh, port)
        assert p3.returncode == 0, f"内置语料检索 exe 异常退出({p3.returncode})"
        bundled_results = _tool_results(REQUESTS[-1])
        assert TITLE_KEY in bundled_results, \
            f"打包版没命中内置语料(检查 kb/ 有没有打进包):\n{bundled_results[:300]}"
        assert "gzasc.edu.cn" in bundled_results, "内置语料条目缺少原文链接"
        print("阶段 3 PASS:打包版命中随包内置的校园语料(136 条已随 exe 分发)")
        print("\n=== FROZEN CRAWL → KB → NEXT ASK E2E PASS ===")
    finally:
        server.shutdown()
        if original is None:
            config_path.unlink(missing_ok=True)
        else:
            config_path.write_bytes(original)


if __name__ == "__main__":
    main()
