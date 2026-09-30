"""从前端 index.html 里抽出内联 <script> 块,交给 node --check 做语法校验。

等价于一个"提交前先编译一遍":前端没有构建步骤,语法错要等窗口打开才暴露,
而那时候的错误信息只有一个白屏。这里几秒钟就能查出来。
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

HTML = Path(sys.argv[1] if len(sys.argv) > 1 else "frontend/index.html")

# 这道闸自己打印 ✓/✗:Windows 默认控制台是 GBK,print 当场 UnicodeEncodeError ——
# 照 CONTRIBUTING「全量别带 PYTHONIOENCODING」的写法执行就是 traceback,等于没有
# 这道闸(check_docs.py 踩过同一个坑,那边已修;这里补上,别让第三道闸独缺)。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass          # 被重定向/没有 reconfigure 的输出流:原样让它去

src = HTML.read_text(encoding="utf-8")
blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", src, re.S)
print(f"{HTML}: 找到 {len(blocks)} 个内联 script 块")

failed = 0
for i, body in enumerate(blocks, start=1):
    if not body.strip():
        continue
    with tempfile.NamedTemporaryFile("w", suffix=f".block{i}.js", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(body)
        tmp = fh.name
    proc = subprocess.run(["node", "--check", tmp], capture_output=True, text=True,
                          # 不写 encoding 时按本机 locale(中文 Windows = GBK)解:
                          # node 报的是 UTF-8 源码里的中文标识符,解不开就整个丢掉,
                          # 界面上只剩一个没有原因的 ✗(v0.9.37 就是这么撞上的)。
                          encoding="utf-8", errors="replace")
    if proc.returncode == 0:
        print(f"  ✓ 块 {i}({len(body)} 字符)")
    else:
        failed += 1
        print(f"  ✗ 块 {i}({len(body)} 字符)")
        print((proc.stderr or "").strip()[:1500])
    Path(tmp).unlink(missing_ok=True)

sys.exit(1 if failed else 0)
