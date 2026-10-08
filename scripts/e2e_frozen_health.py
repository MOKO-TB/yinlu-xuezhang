"""冻结版(打包 exe)验 v0.9.02 的馆藏库自检:库不完整时当场就说,不等搜书失败。

用 dist/引路学长/引路学长-CLI.exe(安装版同源)。cwd 指向一个临时工作区,里面放
0 字节的 library.db + 真凭证(凭证说应有 1.31 GB)→ 大小对不上 → 必须报 incomplete。
不改动构建产物本身 —— 但**首启那一次会在应用目录里生成 config.json**(那是运行时文件,
不是构建产物),所以这份脚本跑完之后由打包那一步把它连同 sessions/ 一起清掉再分享。
"""
from __future__ import annotations
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "dist" / "引路学长"
CLI = APP / "引路学长-CLI.exe"
DB = APP / "crawled" / "mfindgzyykj.libsp.cn" / "library.db"
STAMP = DB.with_name(DB.name + ".stamp.json")

print("CLI:", CLI, CLI.exists(), " stamp:", STAMP.exists())

ws = Path(tempfile.mkdtemp(prefix="gcode-frozen-health-"))
try:
    d = ws / "crawled" / "mfindgzyykj.libsp.cn"
    d.mkdir(parents=True)
    (d / "library.db").write_bytes(b"")          # 空壳:模拟没拷全
    shutil.copy2(STAMP, d / "library.db.stamp.json")

    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    # 先跑一次**不看结果**的暖机:刚构建完的那份 `dist/引路学长/` 里没有 config.json,
    # 第一次运行必然走"从模板生成 + 退出码 1"那条首启路(与 e2e_headless 的探针同一个坑,
    # v0.9.120 就为那支脚本修过),拿这一次的输出去判"包没识别出库不完整"是误判。
    subprocess.run([str(CLI), "/library"], cwd=str(ws), capture_output=True,
                   timeout=180, env=env)
    out = subprocess.run([str(CLI), "/library"], cwd=str(ws), capture_output=True,
                         timeout=180, env=env)
    raw = out.stdout + out.stderr
    # 冻结版 CLI 把 stdout 重定向时走的是系统 ANSI 码页(本机 cp936),
    # 只有真控制台才会被 _fix_console_encoding 切到 UTF-8 —— 验收脚本两种都试。
    text = ""
    for enc in ("utf-8", "gb18030"):
        try:
            cand = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        if "incomplete" in cand:
            text = cand
            break
        text = text or cand
    assert text, "冻结 CLI 没有任何输出"
    print("rc:", out.returncode)
    keep = [ln for ln in text.splitlines()
            if any(k in ln for k in ("馆藏", "library", "不完整", "状态", "书目",
                                     "大小", "应为", "⚠", "incomplete"))]
    print("\n".join(keep[:20]))
    assert "incomplete" in text, "冻结版没识别出库不完整:\n" + text[:800]
    assert "不完整" in text, "冻结版没给出用户可读的提示:\n" + text[:800]
    print("\n=== 冻结版自检通过:库不完整时当场报警 ===")
finally:
    shutil.rmtree(ws, ignore_errors=True)
