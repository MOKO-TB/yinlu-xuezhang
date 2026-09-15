"""截图对比浅色/深色两套主题(开发自检用,不进测试)。"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from minicode import browser as br                       # noqa: E402
from minicode.tools import browser as bt                 # noqa: E402
from minicode.tools.base import ToolContext              # noqa: E402

out = ROOT / "_tmp_dev_check"
out.mkdir(exist_ok=True)

ctx = ToolContext(workspace=ROOT, config=__import__("minicode.config", fromlist=["Config"]).Config())
bt.browser_open(ctx, "http://127.0.0.1:8765/index.html", wait_ms=4000)
time.sleep(1.2)
js_on = "document.documentElement.classList.contains('dark')"
print("initial dark =", br.evaluate(js_on, timeout=10))
r = br.screenshot(out / "theme_light.png")
print("light:", r["path"])
br.evaluate("document.documentElement.classList.add('dark')", timeout=10)
time.sleep(1.2)
r = br.screenshot(out / "theme_dark.png")
print("dark:", r["path"])
br.evaluate("document.documentElement.classList.remove('dark')", timeout=10)
