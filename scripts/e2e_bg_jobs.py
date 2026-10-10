# -*- coding: utf-8 -*-
"""后台命令(v0.9.84/85)的端到端验证:验的是**真机器上进程有没有死透**。

单元测试证明的是逻辑对;这支脚本证明的是四件单元测试够不着的事:

1. **`bash_kill` 杀的是整棵树,不只是那个 shell**。后台命令的形状是
   `bash.exe -c "python tick_bg.py"` —— 只杀直接子进程(bash)会留下
   `python` 继续吃 CPU 继续写文件。这里让真脚本每 0.4 秒往文件里写一行,
   杀完再等 3 秒:**文件不许再长**。长度就是"还活着"的铁证,不靠猜。
2. **学长退出时那层收口是真的**。另起一个真子进程当"学长":它起一个后台任务就正常退出,
   `atexit` 里注册的 `_bg_shutdown` 必须把那个任务一起带走 —— 同样用文件长度验。
   这条要是假的,用户的机器上就会积累一批"学长早关了、进程还在跑"的孤儿。
3. **状态注入真的走到了模型那一侧**:用真 `Agent` + 记录收到的 messages 的假 LLM,
   验"有后台任务时请求末尾多那一行、而 `session.messages` 一个字都不许多"。
   前缀不动是 KV Cache 的账,这条在单测里是断言,在这里是**真发出去的那份 payload**。
4. **喂进去的输入真的到了子进程手里(v0.9.146)**。`bash_input` 的单元测试用替身也能
   过,但"这条管道到底通没通"只有真进程能证明:DEVNULL 忘了换成 PIPE、`flush()` 漏了、
   编码写错 —— 三样都表现成"回话说已写入、子进程什么也没收到",而那正是这个功能
   最坏的一种坏法(静默无效)。这里让真脚本读一行回显一行,断言那句回显真出现在
   输出尾部。

跑法(项目根目录,需要 PYTHONIOENCODING=utf-8 否则中文断言在 GBK 控制台炸):
    PYTHONIOENCODING=utf-8 .venv\\Scripts\\python.exe scripts\\e2e_bg_jobs.py

副作用只在系统临时目录里写几个文件,不碰工作区、不联网。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TICK_SRC = """
import sys, time
path, rounds = sys.argv[1], int(sys.argv[2])
with open(path, "a", encoding="utf-8") as f:
    for i in range(rounds):
        f.write("tick %d\\n" % i)
        f.flush()
        time.sleep(0.4)
"""

fails: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    """记一条断言并回它成没成 —— 回值让调用方能"这一条不成立就别往下走"。

    从前它回 None,于是 `if not check(...)` 这种写法会**永远成立**(None 是假),
    看起来像在短路,其实每次都在短路。回一个真布尔值,那个意图才落地。
    """
    print(("  ✓ " if ok else "  ✗ ") + label + (f" —— {detail}" if detail else ""))
    if not ok:
        fails.append(label)
    return bool(ok)


def _ctx(workspace: Path):
    from minicode.config import Config, Provider
    from minicode.tools.base import ToolContext
    cfg = Config(providers=[Provider(id="t", name="T", base_url="https://x",
                                     api_key="k", model="m")],
                 active_provider_id="t")
    return ToolContext(workspace=workspace, config=cfg)


def _size(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0


def _start_tick(workspace: Path, name: str, rounds: int = 60) -> Path:
    """真起一个后台任务:它每 0.4 秒往 <name> 写一行,持续 rounds 次。"""
    from minicode.tools import shell
    tick = workspace / f"{name}.py"
    out = workspace / f"{name}.txt"
    tick.write_text(TICK_SRC, encoding="utf-8")
    cmd = f'"{sys.executable}" "{tick}" "{out}" {rounds}'
    res = shell.bash(_ctx(workspace), cmd, background=True)
    if "已放进后台:" not in res:
        raise RuntimeError(f"后台启动失败:{res[:200]}")
    return out


def case_kill_kills_the_tree() -> None:
    print("\n[1] bash_kill 要杀透整棵进程树(文件不再长 = 真死了)")
    from minicode.tools import shell
    workspace = Path(tempfile.mkdtemp(prefix="e2e_bg_kill_"))
    out = _start_tick(workspace, "killme")
    time.sleep(1.6)
    before = _size(out)
    job_id = shell._bg_list_snapshot()[-1]
    res = shell.bash_kill(_ctx(workspace), id=job_id)
    check("已终止" in res or "已经结束" in res, f"{job_id} 的终止回话", res[:90])
    time.sleep(3.0)
    after = _size(out)
    # 杀完到量完之间可能还有一行在路上,所以只许 1 行的余量(约 8 字节)
    check(after - before <= 8, "杀完后 3 秒内文件几乎没再长",
          f"{before} → {after} 字节(差 {after - before})")
    check(not shell._bg_jobs[job_id].running, "表里那一路的 poll 已经拿到结果")


def case_exit_reaps_orphans() -> None:
    """学长退出时把还在跑的后台任务一起收掉(atexit 那条承诺)。

    起一个真子进程当"学长":它起一个后台任务就正常退出。要是那层收口是假的,
    用户的机器上就会积累一批"学长早关了、进程还在跑"的孤儿 —— 所以判据还是
    **文件有没有继续长**,不去数进程(数进程要靠第三方库,而且名字对不上)。
    """
    print("\n[2] 学长退出时把还在跑的后台任务一起收掉(atexit 那条承诺)")
    tmp = Path(tempfile.mkdtemp(prefix="e2e_bg_exit_"))
    tick = tmp / "tick_bg.py"
    tick.write_text(TICK_SRC, encoding="utf-8")
    out = tmp / "out.txt"
    runner = tmp / "owner_app.py"
    # 路径必须用 repr 生成:直接嵌进源码里,`C:\Users` 的 \U 会被当成非法转义,
    # 脚本自己在语法层就炸(第一版就栽在这,报错长得像被测功能的错,容易误判)。
    job_cmd = f'"{sys.executable}" "{tick}" "{out}" 200'
    runner.write_text(
        "import sys, time\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from pathlib import Path\n"
        "from minicode.config import Config, Provider\n"
        "from minicode.tools import shell\n"
        "from minicode.tools.base import ToolContext\n"
        f"ws = Path({str(tmp)!r})\n"
        f"out = Path({str(out)!r})\n"
        "cfg = Config(providers=[Provider(id='t', name='T', base_url='https://x',\n"
        "                             api_key='k', model='m')], active_provider_id='t')\n"
        "ctx = ToolContext(workspace=ws, config=cfg)\n"
        f"cmd = {job_cmd!r}\n"
        "print(shell.bash(ctx, cmd, background=True))\n"
        "# **等它真写出第一行再退出** —— 这是这一场能不能成立的关键。\n"
        "# `atexit` 的收口在子进程退出那一刻立刻跑,而 tick 脚本从解释器启动到写出\n"
        "# 第一行要 100~300 ms;不等就退,收口可能赶在它写出任何东西之前 —— 那样\n"
        "# 「退出后文件不再长」证明的是「它本来就没开始写」,不是「收口真的生效了」。\n"
        "# 实测这就是它一直红着的原因(第 2 场报「0 字节」),与 atexit 无关。\n"
        "deadline = time.time() + 10\n"
        "while time.time() < deadline:\n"
        "    if out.exists() and out.stat().st_size > 0:\n"
        "        break\n"
        "    time.sleep(0.05)\n"
        "print('TICK_BYTES', out.stat().st_size if out.exists() else -1)\n",
        encoding="utf-8")
    proc = subprocess.run([sys.executable, str(runner)], cwd=str(ROOT),
                          capture_output=True, text=True, timeout=90,
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                          # 子进程里 shell._child_env 给它设了 PYTHONIOENCODING=utf-8,
                          # 父进程不显式指定就会按本机 GBK 解中文 → 直接 UnicodeDecodeError,
                          # 症状长得像被测功能坏了(第一版就栽在这里)。
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        check(False, "那个当学长的子进程正常退出",
              f"退出码 {proc.returncode}:{proc.stderr[:200]}")
        return
    check("已放进后台:" in proc.stdout, "子进程里的后台确实起来了",
          proc.stdout.strip().splitlines()[-1][:70] if proc.stdout else "")
    m2 = re.search(r"TICK_BYTES (-?\d+)", proc.stdout or "")
    wrote = int(m2.group(1)) if m2 else -1
    check(wrote > 0, "退出前它真的在写(所以这一场测的是收口,不是启动)",
          f"退出那一刻 {wrote} 字节")
    time.sleep(0.6)
    mid = _size(out)
    time.sleep(4.0)
    grew = _size(out) - mid
    # atexit 是在子进程退出时同步执行的:它真杀了,这里就几乎不会再长
    check(grew <= 16, "学长退出 4 秒后,那个后台任务不再写文件",
          f"{mid} → {_size(out)} 字节(差 {grew})")


def case_brief_reaches_the_model() -> None:
    print("\n[3] 有后台任务时,状态那一行真的进了发出去的请求,而没进留档")
    from minicode.agent import Agent
    from minicode.llm import AssistantTurn
    from minicode.permission import PermissionGate
    from minicode.session import Session
    from minicode.tools import shell
    from minicode.tools.registry import build_default_registry

    class RecordingLLM:
        """记下每一次收到的 messages,并立刻让这一轮结束(不派工具)。

        **`**kwargs` 不是可选的**:`Agent.run_turn` 会传 `fallback_provider_id` 与
        `on_fallback`,而这个替身从前没接 —— 于是调用抛 `TypeError`,被 `run_turn`
        的 `except Exception` 当成"模型报错"**吞掉**(它只回一句 llm_error 就 return),
        `seen` 因此是空的,脚本再在 `seen[-1]` 上 IndexError 崩掉。
        症状("脚本崩了")离病因("替身签名没跟上")隔了两层,所以下面还专门有一条
        断言把"模型一次都没被调用"当场说出口 —— 下次接口再长参数,红的是那句话,
        不是一句 IndexError。
        """

        def __init__(self):
            self.seen: list[list[dict]] = []
            self.errors: list[BaseException] = []

        def stream_chat(self, messages, tools, on_text=None, on_reasoning=None,
                        on_retry=None, **kwargs):
            self.seen.append(list(messages))
            if on_text:
                on_text("好")
            return AssistantTurn(text="好")

    tmp = Path(tempfile.mkdtemp(prefix="e2e_bg_brief_"))
    ctx = _ctx(tmp)
    out = _start_tick(tmp, "watchme", rounds=40)
    llm = RecordingLLM()
    agent = Agent(llm=llm, registry=build_default_registry(),
                  session=Session(system_prompt="SYS-PREFIX"),
                  gate=PermissionGate(ctx.config), ui=_QuietUI(), ctx=ctx)
    agent.run_turn("第一问")
    # 先钉"模型真被调用了" —— 否则下面那句 `seen[-1]` 会抛 IndexError,而那个报错
    # 长得像脚本自己的毛病,看不出真正的原因是替身签名没跟上(见 RecordingLLM)。
    if not check(bool(llm.seen), "模型这一轮真被调用了(替身签名与 Agent 对得上)",
                 f"seen={len(llm.seen)}"):
        return
    last = llm.seen[-1]
    check(last[0]["content"] == "SYS-PREFIX", "系统提示词那一条原样没动(前缀 = KV Cache)")
    check(any("本机后台命令现状" in str(m.get("content")) for m in last[1:]),
          "状态那一行确实发给了模型")
    stored = " ".join(str(m.get("content")) for m in agent.session.messages)
    check("本机后台命令现状" not in stored, "但它没被写进留档(不会一路重复带下去)")
    shell.bash_kill(ctx, id=shell._bg_list_snapshot()[-1])
    _ = out


class _QuietUI:
    """Agent 要的界面接口:这里只关心请求里多了什么,不关心渲染。"""

    def __getattr__(self, name):
        return lambda *a, **k: None


STDIN_ECHO_SRC = (
    "import sys\n"
    "for line in sys.stdin:\n"
    "    print('echo:' + line.rstrip(), flush=True)\n"
)


def case_input_reaches_the_child() -> None:
    """v0.9.146:写进管道的那一行,子进程**真读得到**。

    单元测试用假替身也能过,但"管道到底通没通"只有真进程能证明 —— 中间任何一层
    (DEVNULL 没换成 PIPE、flush 漏了、编码写错)都会表现成"回话说已写入、子进程却
    什么也没收到",而那正是这个功能最坏的一种坏法。
    """
    print("\n[4] bash_input 写进去的那一行,子进程真读得到")
    from minicode.tools import shell
    workspace = Path(tempfile.mkdtemp(prefix="e2e_bg_input_"))
    script = workspace / "echo_stdin.py"
    script.write_text(STDIN_ECHO_SRC, encoding="utf-8")
    res = shell.bash(_ctx(workspace), f'"{sys.executable}" -u "{script}"',
                     background=True)
    check("已放进后台:" in res, "后台起得来", res[:90])
    if "已放进后台:" not in res:
        return
    job_id = shell._bg_list_snapshot()[-1]
    ctx = _ctx(workspace)
    got = shell.bash_input(ctx, id=job_id, text="hello-from-e2e")
    check("输入已写入" in got, "写这一行没有报错", got[:90])
    # 轮询而不是 sleep 定值:输出要经 `_pump` 按行读到才会出现在 tail 里
    deadline = time.monotonic() + 10
    tail = ""
    while time.monotonic() < deadline:
        tail = shell.bash_status(ctx, id=job_id)
        if "echo:hello-from-e2e" in tail:
            break
        time.sleep(0.1)
    check("echo:hello-from-e2e" in tail, "子进程读到了那一行并回显", tail[-160:])
    shell.bash_kill(ctx, id=job_id)


def main() -> int:
    # 逐条打印 ✓/✗:GBK 控制台上 print 会当场 UnicodeEncodeError,
    # 而"脚本自己崩在半路"比"验不过"更坏 —— 你会以为是被测功能坏了。
    # 与 check_docs / check_frontend_js / check_frozen_version 同一套守卫。
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    from minicode.tools import shell
    print("shell 解析到的 bash:", shell._pick_bash() or "(没有 bash,会退回 cmd)")
    # **一场崩掉不许让后面几场根本没机会跑**。上面那句注释("脚本自己崩在半路比
    # 验不过更坏")本来是句口号:v0.9.147 之前第 3 场会抛 IndexError,于是第 4 场
    # 从来没跑过,而人只看到"脚本崩了" —— 看不出到底验过哪几场。现在每一场各自兜住,
    # 异常本身算一条失败(退出码照样非 0),后面照跑。
    for _case in (case_kill_kills_the_tree, case_exit_reaps_orphans,
                  case_brief_reaches_the_model, case_input_reaches_the_child):
        try:
            _case()
        except Exception as e:          # 兜住是为了"跑完",不是为了"放过"
            import traceback
            check(False, f"{_case.__name__} 自己崩了", f"{type(e).__name__}: {e}")
            traceback.print_exc()
    print()
    if fails:
        print(f"FAIL: {len(fails)} 项没过 → {fails}")
        return 1
    print("PASS: 后台命令杀得透树、学长退出收得掉、状态行只搭请求不进留档、"
          "喂进去的输入子进程真读得到")
    return 0


if __name__ == "__main__":
    sys.exit(main())
