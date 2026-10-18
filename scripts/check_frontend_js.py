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

# ---- id 唯一性:同一个 id 在文档里出现两次,`getElementById` 只回第一个,另一个容器
# 永远是空的。v0.9.183 的课表面板就是这么整块空白 —— 侧聊那条线程流的容器也叫
# `scBody`,与课表对话框的 `#scBody` 撞了,`renderSchedule()` 把课表渲染进了侧栏。
# 这类撞车静态标记 vs 脚本模板之间也会发生,所以两边都扫。
static = re.sub(r"<script(?![^>]*\bsrc=)[^>]*>.*?</script>", "", src, flags=re.S)
ids_static = re.findall(r'\bid="([A-Za-z0-9_\-]+)"', static)
ids_js = re.findall(r'\bid="([A-Za-z0-9_\-]+)"', "".join(blocks))


def _dups(seq):
    seen, out = set(), set()
    for x in seq:
        (out if x in seen else seen).add(x)
    return sorted(out)


dup_static = _dups(ids_static)
across = sorted(set(ids_static) & set(ids_js))
if dup_static or across:
    failed += 1
    print("  ✗ id 撞车:静态标记里重复 %s;静态 id 又被脚本模板用一次 %s"
          % (dup_static, across))
    print("    —— 两个同名容器只认得第一个,另一个永远空白(课表面板那次就是这样)。")
else:
    print("  ✓ id 唯一(静态 %d 个,且没有一个被脚本模板再用一次)" % len(set(ids_static)))
js_dup = _dups(ids_js)
if js_dup:
    print("  注意:脚本模板内部重名 %s —— 若它们是互斥分支(同一时刻只渲染一个)不致出事,"
          "但值得逐条确认。" % js_dup)

sys.exit(1 if failed else 0)
