"""性能回归针(v0.9.188):启动路上最贵的那两笔开销不许被悄悄加回来。

用户反馈「用起来卡、慢」,量出来的两处在下面钉着 —— 它们都**不报错、只变慢**,
所以必须由针来守:

1. `minicode.llm` / `minicode.gui_bridge` 在**模块导入**阶段不许把 openai 拉进来
   (冷进程 1.25 s,pydantic 占大头,而窗口要等它导完才出现);
2. `LLMClient()` 构造不许建真客户端 —— 那是"第一次真发请求"才该付的钱;
3. `proactive._cert_variants` 的两份 SSLContext 必须缓存复用(每次新建实测 176 ms
   纯 CPU,而启动后 15 秒那轮「主动推荐」正好在用户开始操作时跑)。
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run(code: str) -> str:
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, cwd=str(ROOT))
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_importing_llm_does_not_pull_in_openai():
    """openai 只在**第一次真发请求**时导;提前导 = 窗口晚出现 1.25 秒。"""
    assert _run("import minicode.llm, sys; print('openai' in sys.modules)") == "False"


def test_importing_the_gui_bridge_does_not_pull_in_openai():
    assert _run("import minicode.gui_bridge, sys; print('openai' in sys.modules)") == "False"


def test_constructing_the_llm_client_does_not_build_a_real_client():
    from minicode.config import Config, Provider
    from minicode.llm import LLMClient

    cfg = Config(providers=[Provider(id="p", name="P", base_url="https://x/v1",
                                     api_key="k", model="m")],
                 active_provider_id="p")
    client = LLMClient(cfg)
    assert client._client_obj is None, "构造就建客户端 = 启动时又把 openai 导回来了"


def test_ssl_contexts_are_built_once_and_reused():
    from minicode import proactive

    first = proactive._certifi_context()
    assert first is proactive._certifi_context(), "每次出网都现建证书链(176 ms 纯 CPU)"
    sysctx = proactive._system_context()
    assert sysctx is proactive._system_context()
