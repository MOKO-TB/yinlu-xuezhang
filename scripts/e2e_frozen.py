"""冻结态端到端:假 SSE 服务器 + 打包后的 Gcode.exe 完整链路。

验证:exe 找到内置 system_prompt → 生成/读取 exe 旁的 config.json →
流式请求 → 工具调用 → 在当前工作目录真实写出文件 → sessions 留档在 exe 旁。

用法: python scripts/e2e_frozen.py            # 默认剧本:模型调 write_file 真写文件
     python scripts/e2e_frozen.py --browser   # 浏览器剧本:冻结 exe 真开 Edge/Chrome 取渲染后正文

--browser 验的是打包专属的风险点:`websockets` 在源码里是函数内延迟导入,
PyInstaller 静态分析看不见;没收进包的话打包版跑浏览器能力会 ModuleNotFoundError。
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
    browser_mode = "--browser" in sys.argv
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
    task = "创建 e2e_demo.txt"
    # 浏览器剧本要点两个地方:假服务器跑在**本进程**(它自己读 os.environ 选剧本、
    # 也要读 BROWSER_TARGET 生成工具参数),子进程只需拿到 profile 覆盖。
    if browser_mode:
        os.environ["E2E_SCRIPT"] = "browser"
        os.environ["BROWSER_TARGET"] = f"http://127.0.0.1:{port}/e2e"
        env["E2E_SCRIPT"] = "browser"
        env["BROWSER_TARGET"] = os.environ["BROWSER_TARGET"]
        # 独立 profile 落到临时目录,不污染用户真实浏览器配置
        env["YINLUXS_BROWSER_PROFILE"] = str(workspace / "browser-profile")
        task = "用浏览器看看这个页面"
    try:
        # ---- CLI exe:全链路 ----
        proc = subprocess.run(
            [str(cli_exe), task],
            cwd=workspace, capture_output=True, timeout=180, env=env,
        )
        # 冻结 CLI 把 stdout 重定向时走系统 ANSI 码页(本机 cp936),两种都试
        raw = proc.stdout
        output = ""
        for enc in ("utf-8", "gb18030"):
            try:
                cand = raw.decode(enc)
            except UnicodeDecodeError:
                continue
            if any(k in cand for k in ("browser", "已打开", "错误", "PASS", "●")):
                output = cand
                break
            output = output or cand
        print(output[-500:])

        if browser_mode:
            import e2e_fake_server as fake   # noqa: WPS433 —— 同一个进程里的假服务器
            blob = fake.SEEN_REQUESTS[-1] if len(fake.SEEN_REQUESTS) > 1 else ""
            assert "E2E_BROWSER_SENTINEL" in blob, (
                "冻结 exe 没把 JS 渲染后的内容回填给模型。"
                "先看上面的输出里有没有 ModuleNotFoundError(websockets 没打进包)或"
                "「浏览器能力当前不可用」:\n" + output[-1200:])
            assert "已打开" in blob, "browser_open 的文案没回填给模型"
            print("=== FROZEN BROWSER E2E PASS(websockets 已收进包且能真开浏览器)==>")
            gui_proc = subprocess.Popen([str(gui_exe)], cwd=app_dir, env=env)
            time.sleep(10)
            assert gui_proc.poll() is None, \
                f"GUI exe 启动后退出,返回码 {gui_proc.returncode}"
            gui_proc.terminate()
            print("=== FROZEN GUI LAUNCH PASS ===")
            return

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
        if browser_mode:
            import e2e_fake_server as fake
            fake.SEEN_REQUESTS.clear()
            for k in ("E2E_SCRIPT", "BROWSER_TARGET"):
                os.environ.pop(k, None)


if __name__ == "__main__":
    main()
