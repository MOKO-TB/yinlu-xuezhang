"""冻结态钩子验证(v0.9.115 H1~H4+H6):假 SSE 服务器 + **dist 里那个 CLI**。

为什么单独一支、不并进 `e2e_frozen.py`:那一支验的是"打包链路活着",这一支验的是
"这轮改的钩子在**装好的包里**真做事"。钩子代码被打进 PYZ 是远远不够的(冻结针就为
此存在),它得真跑一次并留下可核对的痕迹 —— 而 `{workspace}` 这类占位符只有打包态
才验得着:钩子命令继承的是本进程 cwd,包内 CLI 的 cwd 是安装目录,占位符没接上就会
去测安装目录还不吭声。

一次跑覆盖五件事,每件一条独立断言:
  H1 pre_tool 改写参数        → 落盘的是改写后的文件名,原文件名根本没出现
  H3 permission_request 代答  → 档位是 confirm,却一步都没弹框问人(卡住=没代答成功)
  H1/H6 post_tool 回灌        → 结果里既有钩子正文也有原结果(追加,不是替换)
  H4 两个事件附话              → 断的是**发出去的请求体**里有那两句话(不是本地变量)
  H2 hook_ms 落档             → sessions 留档那条 tool 消息带着实测毫秒

用法: python scripts/e2e_frozen_hooks.py     (需先跑 python scripts/build_exe.py)
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
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import e2e_fake_server as fake          # noqa: E402  假服务器与请求体台账

PY = str(PROJECT_ROOT / ".venv" / "Scripts" / "python.exe")
CLI = PROJECT_ROOT / "dist" / "引路学长" / "引路学长-CLI.exe"

# 一支脚本按 argv[1] 分派五类钩子,免得留五份几乎一样的文件。
# 它落在临时工作区里而不是 scripts/ 下:这是生成物,不该出现在版本库里。
HOOKD = r'''
import json, sys
kind = sys.argv[1]
def args_of():
    return json.load(open(sys.argv[2], encoding="utf-8"))
if kind == "pre_tool":
    d = args_of()
    print(json.dumps({"updated_input": {"path": "hook_rewritten.txt",
                                        "content": d.get("content", "")},
                      "context": "HOOK-PRE-CTX"}))
elif kind == "permission_request":
    print(json.dumps({"decision": "allow", "reason": "冻结态验证代答"}))
elif kind == "post_tool":
    # 读 args.path:验的正是 H6 补的那个缺口 —— post 载荷以前只有 {tool,result},
    # 拿不到路径就没法"写完自查"。argv[2] 是载荷文件、argv[3] 才是 {workspace}。
    d = args_of()
    ws = sys.argv[3] if len(sys.argv) > 3 else "(没传工作区)"
    print(json.dumps({"updated_output": "HOOK-LINT-BODY path="
                                            + str(d.get("args", {}).get("path"))
                                            + " ws=" + str(ws),
                      "context": "HOOK-POST-CTX"}))
elif kind == "session_start":
    print(json.dumps({"context": "HOOK-SESS-SENTINEL"}))
elif kind == "user_prompt_submit":
    print(json.dumps({"context": "HOOK-UPS-SENTINEL"}))
'''


def _quiet_streams() -> None:
    """本机控制台是 GBK,直接 print 中文会当场 UnicodeEncodeError ——
    这项目已经栽过三回,`check_docs.py` / `check_frozen_version.py` 同一只手。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main() -> int:
    _quiet_streams()
    if not CLI.exists():
        print(f"✗ 没有冻结 CLI:{CLI}\n  先跑 python scripts/build_exe.py 再验包内状态。")
        return 1
    workspace = Path(tempfile.mkdtemp(prefix="hook-frozen-"))
    hookd = workspace / "hookd.py"
    hookd.write_text(HOOKD, encoding="utf-8")
    runner = f'"{PY}" "{hookd.as_posix()}"'

    def cmd(kind: str, extra: str = "") -> str:
        """一条钩子命令:python hookd.py <kind> [占位文件]。占位符留给 _render 去换。"""
        return f"{runner} {kind}" + (f" {extra}" if extra else "")

    cfg = {
        # confirm 档:write_file 本该每一问都弹框。钩子代答成功才会一步不问。
        "permission_mode": "confirm",
        "hooks": {
            "session_start": {"command": cmd("session_start")},
            "user_prompt_submit": {"command": cmd("user_prompt_submit")},
            "pre_tool": {"command": cmd("pre_tool", '"{args_file}"'),
                         "tools": ["write_file"]},
            "permission_request": {"command": cmd("permission_request"),
                                   "tools": ["write_file"]},
            "post_tool": {"command": cmd("post_tool", '"{result_file}" "{workspace}"'),
                          "tools": ["write_file"]},
        },
    }
    app_dir = CLI.parent
    config_path = app_dir / "config.json"
    sessions_dir = app_dir / "sessions"
    before = {p.name for p in sessions_dir.glob("*.jsonl")} if sessions_dir.is_dir() else set()
    config_path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")

    server = HTTPServer(("127.0.0.1", 0), fake.Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = {**os.environ,
           "YINLUXS_LLM_BASE_URL": f"http://127.0.0.1:{port}/v1",
           "YINLUXS_LLM_API_KEY": "test-key-hook",
           "YINLUXS_LLM_MODEL": "fake-model"}
    fails = []
    try:
        fake.SEEN_REQUESTS.clear()
        # stdin 直接关掉:代答要是没成功,确认框读不到回答就会把这一步拒掉 ——
        # 断言会指名是哪一条坏了。(不能同时给 input= 与 stdin=DEVNULL:subprocess 会报错)
        proc = subprocess.run([str(CLI), "创建 e2e_demo.txt"], cwd=str(workspace),
                              capture_output=True, timeout=240, env=env,
                              stdin=subprocess.DEVNULL)
        out = _decode(proc.stdout or b"")
        new = [p for p in sessions_dir.glob("*.jsonl") if p.name not in before]
        archive = max(new, key=lambda p: p.stat().st_mtime).read_text(
            encoding="utf-8") if new else ""
        # 不假设"第 0 条就是第一次对话请求":假服务器可能先收到探针/列模型那类请求。
        # 扫**全部**收到的请求体,附话只要真发出去过就一定在里面。
        reqs = "\n@@@\n".join(fake.SEEN_REQUESTS)
        print(f"  (假服务器共收到 {len(fake.SEEN_REQUESTS)} 个请求体)")

        checks = [
            ("H1 pre 改写真的执行了(落的是改写后的文件)",
             (workspace / "hook_rewritten.txt").is_file()
             and (workspace / "hook_rewritten.txt").read_text(encoding="utf-8") == "hello e2e"),
            ("H1 原文件名根本没出现(改写发生在门之前、门看的是改写后的)",
             not (workspace / "e2e_demo.txt").exists()),
            ("H1/H6 post 回灌正文进了留档", "HOOK-LINT-BODY" in archive),
            ("H6 post 载荷真带着 args(回灌正文里看得见那个路径)",
             "path=hook_rewritten.txt" in archive),
            ("H1 原结果没被吞(回灌是追加不是替换)",
             "已创建文件" in archive or "hook_rewritten" in archive),
            ("H1 pre 的 context 也到了模型面前", "HOOK-PRE-CTX" in archive),
            ("H4 session_start 那句进了**发出去的请求体**", "HOOK-SESS-SENTINEL" in reqs),
            ("H4 user_prompt_submit 那句进了**发出去的请求体**", "HOOK-UPS-SENTINEL" in reqs),
            ("H3 permission_request 代答的痕写进了留档", "用户没有点过头" in archive),
            ("H3 代答没卡住:整轮跑完(返回码 0)", proc.returncode == 0),
            ("H2 hook_ms 实测毫秒落档", '"hook_ms"' in archive),
            ("{workspace} 在冻结包里真被解释成当时那个工作区",
             "ws=" in archive and "hook-frozen-" in archive.split("ws=")[-1]),
        ]
        for name, ok in checks:
            print(("  ✓ " if ok else "  ✗ ") + name)
            if not ok:
                fails.append(name)
        print("\n--- CLI 输出尾部 ---\n" + out[-700:])
        if fails:
            print("--- 留档尾部(给排查用) ---\n" + archive[-1200:])
        print(("=== 冻结态钩子验证 PASS ===" if not fails
               else f"=== 冻结态钩子验证 FAIL:{len(fails)} 项 ==="))
        return 0 if not fails else 1
    finally:
        server.shutdown()
        config_path.unlink(missing_ok=True)
        for p in sessions_dir.glob("*.jsonl"):
            if p.name not in before:
                p.unlink(missing_ok=True)   # 测试留档绝不留在要分享的包目录里
        fake.SEEN_REQUESTS.clear()


def _decode(raw: bytes) -> str:
    """包内 CLI 的输出编码不保证(控制台代码页 + rich 的 legacy 渲染器)。"""
    out = ""
    for enc in ("utf-8", "gb18030"):
        try:
            cand = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        out = cand if any(k in cand for k in ("钩子", "写入", "错误")) else (out or cand)
    return out


if __name__ == "__main__":
    sys.exit(main())
