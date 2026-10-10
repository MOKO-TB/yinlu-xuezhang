"""手工验一遍 patch.py 的解析与落盘(临时,跑完就删)。"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from minicode.config import Config
from minicode.patch import apply, parse
from minicode.sandbox import SandboxPolicy, SandboxState
from minicode.tools.base import ToolContext

tmp = Path(tempfile.mkdtemp())
(tmp / "a.py").write_bytes(b"l1\nl2\nl3\nl4\n")
(tmp / "b.txt").write_bytes("x\r\ny\r\nz\r\n".encode("gbk"))
ctx = ToolContext(workspace=tmp, config=Config(),
                  sandbox=SandboxState(SandboxPolicy(), tmp))

PATCH = """--- a/a.py
+++ b/a.py
@@ -2,2 +2,3 @@
 l2
-l3
+l3 changed
+new line
--- a/b.txt
+++ /dev/null
@@ -1,3 +0,0 @@
-x
-y
-z
--- /dev/null
+++ b/c/new.md
@@ -0,0 +1,2 @@
+hello
+world
"""
patches, problems = parse(PATCH)
print("problems:", problems)
for fp in patches:
    print(fp.path, fp.action, [(m, t) for h in fp.hunks for m, t in h.lines])
outs, fails = apply(ctx, patches)
print("fails:", fails)
print("outs:", [(o["path"], o["action"], o["added"], o["removed"]) for o in outs])
print("a.py ->", repr((tmp / "a.py").read_bytes()))
print("b.txt exists:", (tmp / "b.txt").exists())
print("c/new.md ->", repr((tmp / "c" / "new.md").read_bytes()))

# 半包原子:第二个文件的上下文对不上,第一个也不该写
(tmp / "d.py").write_text("keep\n", encoding="utf-8")
BAD = """--- a/d.py
+++ b/d.py
@@ -1,1 +1,1 @@
-keep
+changed
--- a/d.py
+++ b/d.py
@@ -1,1 +1,1 @@
-nope
+other
"""
patches2, _ = parse(BAD)
outs2, fails2 = apply(ctx, patches2)
print("atomic fails:", fails2[:1])
print("d.py after failed patch ->", repr((tmp / "d.py").read_text(encoding="utf-8")))
