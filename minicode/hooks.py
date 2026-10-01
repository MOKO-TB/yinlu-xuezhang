"""工具钩子(F3,v0.9.100):用户在 config.json 里配置的 pre/post 命令。

对齐 ZCode/Codex 的 hooks 能力,本地落法只取一半 —— **配置在 config.json,
不认工作区文件**:

- 为什么不认工作区文件:钩子是**任意命令执行**,「clone 别人的仓库带进来一份
  hooks 配置」和 permissions.json 那条教训同源 —— 项目文件不能自证清白,
  任何能从工作区长出钩子的路都是在给恶意仓库发通行证。Config 只从用户的
  config.json 构造,这条路上没有第二个入口。
- 命令模板的占位符:`{tool}` = 工具名(不敏感,直接进命令行);
  `{args_file}` / `{result_file}` = 临时 JSON 文件的路径 —— 参数与结果
  **经文件交付**,不进命令行(§7 (f) 的口径:密钥暴露进命令行会被权限层拦,
  而工具参数里可能就有敏感内容)。
- pre 非零退出 / 超时 = **拦截**:把输出尾部作为拒绝理由回给模型;
  post 失败不拦截(工具结果照常回),在结果尾部如实附一行。
- 每次工具调用都会跑一次钩子 —— 这是用户自己配的,代价他配的时候就知道;
  文档写明。钩子命令走系统 shell(cmd.exe),要 PowerShell 就显式写
  `powershell -Command ...`。
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import time
from pathlib import Path

HOOK_TIMEOUT = 10       # 单条钩子命令的超时;超时按 pre 拦截处理


def _render(cmd: str, tool: str, payload: dict | None, payload_key: str) -> "tuple[str, Path | None]":
    """模板替换:{tool} 进命令行,{args_file}/{result_file} 落临时文件。"""
    text = str(cmd or "").replace("{tool}", tool)
    tmp: Path | None = None
    if "{" + payload_key + "}" in text and payload is not None:
        fd = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8")
        try:
            json.dump(payload, fd, ensure_ascii=False)
        finally:
            fd.close()
        tmp = Path(fd.name)
        text = text.replace("{" + payload_key + "}", str(tmp))
    return text, tmp


def run_hook(kind: str, hooks_cfg: dict | None, tool: str,
             args: dict | None = None, result: str | None = None) -> "dict | None":
    """跑一条钩子;没配置该类返回 None(调用方直接跳过,零开销)。

    返回 {"ok", "blocked", "output"}:pre 的非零退出/超时 → blocked=True、
    output 是输出尾部(调用方拿去当拒绝理由);post 的失败 → blocked=False、
    ok=False(调用方决定怎么如实说)。
    """
    cmd_tpl = str((hooks_cfg or {}).get(kind) or "").strip()
    if not cmd_tpl:
        return None
    payload_key = "args_file" if kind == "pre_tool" else "result_file"
    payload = args if kind == "pre_tool" else (
        {"tool": tool, "result": result} if result is not None else None)
    cmd, tmp = _render(cmd_tpl, tool, payload, payload_key)
    started = time.perf_counter()
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True,
                           text=True, timeout=HOOK_TIMEOUT,
                           encoding="utf-8", errors="replace")
        out = ((r.stdout or "") + (r.stderr or "")).strip()[-300:]
        ok = r.returncode == 0
    except subprocess.TimeoutExpired:
        out = f"钩子超过 {HOOK_TIMEOUT} 秒没跑完"
        ok = False
    except OSError as e:
        out = f"{type(e).__name__}: {e}"
        ok = False
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
    return {"ok": ok, "blocked": (kind == "pre_tool" and not ok),
            "output": out, "elapsed_ms": int((time.perf_counter() - started) * 1000)}
