"""冻结态端到端:假 SSE 服务器 + 打包后的 Gcode.exe 完整链路。

验证:exe 找到内置 system_prompt → 生成/读取 exe 旁的 config.json →
流式请求 → 工具调用 → 在当前工作目录真实写出文件 → sessions 留档在 exe 旁。

用法: python scripts/e2e_frozen.py(需先 python scripts/build_exe.py --no-zip)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from http.server import HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from e2e_fake_server import Handler  # noqa: E402


def main() -> None:
    cli_exe = ROOT / "dist" / "引路学长" / "引路学长-CLI.exe"
    gui_exe = ROOT / "dist" / "引路学长" / "引路学长.exe"
    assert cli_exe.exists() and gui_exe.exists(), \
        "请先构建: python scripts/build_exe.py --no-zip"

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    app_dir = gui_exe.parent
    config_path = app_dir / "config.json"
    config_path.write_text(json.dumps({
        "permission_mode": "accept_all",
    }, ensure_ascii=False), encoding="utf-8")

    workspace = Path(tempfile.mkdtemp(prefix="gcode-frozen-"))
    # 模型固定为内置校园模型,测试用环境变量把端点临时指到本地假服务器
    env = {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "YINLUXS_LLM_BASE_URL": f"http://127.0.0.1:{port}/v1",
        "YINLUXS_LLM_API_KEY": "test-key-frozen",
        "YINLUXS_LLM_MODEL": "fake-model",
    }
    try:
        # ---- CLI exe:全链路 ----
        proc = subprocess.run(
            [str(cli_exe), "创建 e2e_demo.txt"],
            cwd=workspace, capture_output=True, timeout=120, env=env,
        )
        output = proc.stdout.decode("utf-8", errors="replace")
        print(output[-400:])

        # 假服务器的剧本固定写 e2e_demo.txt(与 dev 版 e2e 一致)
        content = (workspace / "e2e_demo.txt").read_text(encoding="utf-8")
        assert content == "hello e2e", f"文件内容不对: {content!r}"
        assert (app_dir / "sessions").exists(), "会话应留档在 exe 目录旁"
        print("=== FROZEN CLI E2E PASS ===")

        # ---- GUI exe:能启动并保持存活 ----
        gui_proc = subprocess.Popen([str(gui_exe)], cwd=app_dir, env=env)
        time.sleep(10)
        assert gui_proc.poll() is None, \
            f"GUI exe 启动后退出,返回码 {gui_proc.returncode}"
        gui_proc.terminate()
        print("=== FROZEN GUI LAUNCH PASS ===")
    finally:
        server.shutdown()
        config_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
