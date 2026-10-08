"""无头模式(`--json`)的**真进程**验证:假 SSE 服务器 + dist 里那个 CLI。

为什么不能用 pytest 代替:pytest 里 `capsys` 只能证明"我们的代码没往 stdout 写",
证明不了 rich 的 Console —— 它自己握着流句柄,横幅与进度条完全可能从旁边那条缝漏进
stdout,而 `json.loads` 当场就废。这一支跑的是**真进程**,断的是那个进程的字面上
第一个字符就是 `{`。

三个结局各验一个:0 做成了 / 3 该问人却没人可问(且**没落盘**)/ 2 参数不对,
再加一个"模型那边根本没答"的 1 —— 这一支是 v0.9.137 那个崩溃的包外闸:进程照样
退 1(与文档里的运行时码同一个数),所以**光看退出码是好的**,坏在 stdout 那份 JSON
整个没了、错误落成一坨 traceback。只有真进程能照出来,pytest 的 capsys 不经过真流。

用法: python scripts/e2e_headless.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import HTTPServer
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import e2e_fake_server as fake          # noqa: E402  假服务器与请求体台账

CLI = PROJECT_ROOT / "dist" / "引路学长" / "引路学长-CLI.exe"
EXIT_RUNTIME = 1
EXIT_ARGS = 2


def _quiet() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _run(args: list[str], cwd: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run([str(CLI), *args], cwd=cwd, capture_output=True,
                          timeout=240, env=env, stdin=subprocess.DEVNULL)


def _supports_headless(env: dict) -> bool:
    """包里没有 `--json` 时**明说并换个退出码**,不当"验过了"。

    同一只手:`eval_behavior.py --live` 拿不到可用凭据时走 `ENV_PROBLEM=2` 那条出口,
    只报一次、不刷一片红。"这个包太旧"与"无头模式坏了"必须是两个样子。

    ⚠ 要跑**两次**才对:全新装的包第一次运行必然走到 `load_config` 从模板生成
    `config.json` 并 `sys.exit(1)`(那句"直接重新启动即可"是 CLI 一直以来的首启行为,
    与人无关),所以第一次的退出码永远是 1 而不是 2。第一次就当"让人把配置生成出来",
    第二次才是真探针。同理不能拿"stdout 空不空"当判据 —— 那是我们要验的东西。
    """
    first = _run(["--output-format", "bogus", "探针"], os.getcwd(), env)
    if first.returncode == EXIT_ARGS:
        return True
    second = _run(["--output-format", "bogus", "探针"], os.getcwd(), env)
    return second.returncode == EXIT_ARGS


def main() -> int:
    _quiet()
    if not CLI.exists():
        print(f"✗ 没有冻结 CLI:{CLI}\n  先跑 python scripts/build_exe.py。")
        return 1
    app_dir = CLI.parent
    config_path = app_dir / "config.json"
    sessions_dir = app_dir / "sessions"
    original = config_path.read_bytes() if config_path.exists() else None
    before = {p.name for p in sessions_dir.glob("*.jsonl")} if sessions_dir.is_dir() else set()
    env = {**os.environ,
           "YINLUXS_LLM_BASE_URL": "http://127.0.0.1:1/v1",   # 探针不真发请求
           "YINLUXS_LLM_API_KEY": "test-key-headless",
           "YINLUXS_LLM_MODEL": "fake-model"}
    if not _supports_headless(env):
        print("ENV_PROBLEM=2:这个包里没有无头模式(或参数出口不对)——"
              "它是在 --json 落地之前构建的,重新打包再验。")
        return EXIT_ARGS

    server = HTTPServer(("127.0.0.1", 0), fake.Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = {**env, "YINLUXS_LLM_BASE_URL": f"http://127.0.0.1:{port}/v1"}
    fails = []
    try:
        # ---- 结局 0:accept_all,一步都不用问人
        config_path.write_text(json.dumps({"permission_mode": "accept_all"},
                                          ensure_ascii=False), encoding="utf-8")
        ws1 = tempfile.mkdtemp(prefix="headless-ok-")
        # 假服务器的剧本计数器是模块级的:不归零,第二个用例就会直接拿到
        # "无工具调用"那一回合,于是根本没触发 ASK —— 那 4 项红是脚本自己的 bug,
        # 不是产品坏了(真跑起来才发现,这条只能真跑)。
        fake.SEEN_REQUESTS.clear()
        fake.REQ_COUNT["n"] = 0
        p0 = _run(["--json", "创建 e2e_demo.txt"], ws1, env)
        doc0 = _parse(p0.stdout)
        checks = [
            ("0 做成:退出码 0", p0.returncode == 0),
            ("0 做成:stdout 第一个字符就是 `{`(横幅一个字都没漏进来)",
             (p0.stdout or b"").lstrip()[:1] == b"{"),
            ("0 做成:stdout 是一份能解的 JSON", doc0 is not None),
            ("0 做成:response 是最后那条助手正文",
             bool(doc0) and doc0["response"] and doc0["status"] == "ok"),
            ("0 做成:工具那一步记成了 ok",
             bool(doc0) and [r["ok"] for r in doc0["tool_calls"]] == [True]),
            ("0 做成:ask_refused 是空的(真的一步都没问)",
             bool(doc0) and doc0["asks_refused"] == []),
            ("0 做成:文件真的落盘了",
             (Path(ws1) / "e2e_demo.txt").is_file()),
            ("0 做成:token 记的是端点报的真数(12+6)",
             bool(doc0) and doc0["turn_tokens"] == 18),
            ("0 做成:留档路径给了、且指向真存在的那份",
             bool(doc0) and doc0["session_file"] and Path(doc0["session_file"]).is_file()),
        ]

        # ---- 结局 3:confirm 档,该问人 —— 无头模式必须**不点头**,而且要说
        config_path.write_text(json.dumps({"permission_mode": "confirm"},
                                          ensure_ascii=False), encoding="utf-8")
        ws2 = tempfile.mkdtemp(prefix="headless-ask-")
        fake.SEEN_REQUESTS.clear()
        fake.REQ_COUNT["n"] = 0
        p3 = _run(["--json", "创建 e2e_demo.txt"], ws2, env)
        doc3 = _parse(p3.stdout)
        checks += [
            ("3 没人可问:退出码 3(不是 0,也不是 1)", p3.returncode == 3),
            ("3 没人可问:stdout 仍是能解的一份 JSON", doc3 is not None),
            ("3 没人可问:status 说的是 ask_refused 而不是 ok",
             bool(doc3) and doc3["status"] == "ask_refused" and doc3["ok"] is False),
            ("3 没人可问:指名是哪只工具在等",
             bool(doc3) and [r["name"] for r in doc3["asks_refused"]] == ["write_file"]),
            ("3 没人可问:**文件没被写出来**(换入口不等于换权限)",
             not (Path(ws2) / "e2e_demo.txt").exists()),
            ("3 没人可问:参数只记长度,正文没外发",
             bool(doc3) and "hello e2e" not in json.dumps(doc3, ensure_ascii=False)),
        ]

        # ---- 结局 2:参数不对,stdout 必须一个字都没有
        bad = _run(["--output-format", "stream-json", "任务"], ws2, env)
        checks += [
            ("2 参数错:退出码 2", bad.returncode == EXIT_ARGS),
            ("2 参数错:stdout 全空(错话说给 stderr)", bad.stdout.strip() == b""),
        ]

        # ---- 结局 1:模型根本没答上话(端点指向一个没人听的端口)
        # 这一支验的是"故障要变成一份说清故障的 JSON",而不是变成一坨 traceback。
        # 只有内置供应商这一条 provider 时不会触发回落(§C2 只挑非活动的那条),
        # 所以这里断的是确定性失败,不靠校园网关当天通不通。
        env_down = {**env, "YINLUXS_LLM_BASE_URL": "http://127.0.0.1:1/v1"}
        ws3 = tempfile.mkdtemp(prefix="headless-down-")
        p1 = _run(["--json", "创建 e2e_demo.txt"], ws3, env_down)
        doc1 = _parse(p1.stdout)
        err1 = (p1.stderr or b"").decode("utf-8", "replace")
        checks += [
            ("1 模型故障:退出码 1(EXIT_RUNTIME,与文档同一口径)",
             p1.returncode == EXIT_RUNTIME),
            ("1 模型故障:stdout 仍是一份能解的 JSON(不是空,也不是 traceback)",
             doc1 is not None),
            ("1 模型故障:status 说的是 llm_error,ok 是 False",
             bool(doc1) and doc1["status"] == "llm_error" and doc1["ok"] is False),
            ("1 模型故障:error 里写着是哪一类故障",
             bool(doc1) and str(doc1["error"] or "").startswith(("APIConnectionError",
                                                                 "APITimeoutError",
                                                                 "RuntimeError"))),
            ("1 模型故障:stderr 里没有未捕获异常(那句 'str' object is not callable)",
             "Traceback" not in err1 and "not callable" not in err1),
        ]

        for name, ok in checks:
            print(("  ✓ " if ok else "  ✗ ") + name)
            if not ok:
                fails.append(name)
        if fails:
            print("--- 第一次的 stdout 头部(排查用) ---")
            print((p0.stdout or b"")[:600].decode("utf-8", "replace"))
            print("--- 第三次的 stdout 头部 ---")
            print((p3.stdout or b"")[:600].decode("utf-8", "replace"))
        print(("=== 无头模式真进程验证 PASS ===" if not fails
               else f"=== 无头模式真进程验证 FAIL:{len(fails)} 项 ==="))
        return 0 if not fails else 1
    finally:
        server.shutdown()
        if original is None:
            config_path.unlink(missing_ok=True)
        else:
            config_path.write_bytes(original)
        for p in (sessions_dir or Path(".")).glob("*.jsonl"):
            if p.name not in before:
                p.unlink(missing_ok=True)   # 测试留档不留在要分享的包目录里


def _parse(raw: bytes) -> dict | None:
    try:
        return json.loads((raw or b"").decode("utf-8", "replace"))
    except Exception:
        return None


if __name__ == "__main__":
    sys.exit(main())
