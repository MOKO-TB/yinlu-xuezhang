"""`--version`:零成本的本地旗标 —— 打印名字与版本就退,不发请求、不读配置。

为什么单开一支钉它(而不是顺手写在别的 CLI 测试里):这枚旗标的**全部价值**
就在于它不依赖任何外部东西。从前"问版本"会被当成一次性任务发给模型(花一次
额度),而网关不通时回来的是一串 401 —— 版本号是最不该依赖网络的东西。所以
这里钉的不只是"打印得对",还有"它排在配置与工作区之前"这条**顺序**:

1. 打印得对、退出码 0、stderr 干净(调用方要能 `$(...)` 直接取);
2. 带一个任务参数也只出版本 —— 证明它没有走到发请求那条路;
3. 配一个**不存在的工作区**也照样出版本 —— `--workspace` 的校验在 `load_config()`
   之后,所以这一条证明它确实短路在后面那些事之前;
4. 源码顺序闸:`--version` 那段必须出现在 `config = load_config()` **之前**。
   第 3 条是行为证据,这一条是结构证据 —— 哪天有人把它挪到下面,第 3 条会红,
   但红的原因不够直白;这一条直接把"不许挪"写在脸上。
"""
import locale
import re
import subprocess
import sys
from pathlib import Path

from minicode import APP_NAME, __version__

MAIN_PY = Path(__file__).resolve().parent.parent / "main.py"
EXPECTED = f"{APP_NAME} {__version__}"


def _decode(raw: bytes) -> str:
    """子进程的 stdout 是管道时,Python 用的是**本地代码页**(本机 cp936),不是 UTF-8。

    这不是将就:真机上 `引路学长-CLI.exe --version > v.txt` 出来的就是那个编码,
    拿 UTF-8 硬解会把中文变成替换符(仓库里 `test_headless.py` 记过同一个坑:
    `_fix_console_encoding` 设的是控制台代码页,对管道无效)。所以先试 UTF-8
    —— 那是有人显式配了 `PYTHONIOENCODING=utf-8` 的情形 —— 不成立再按本地代码页解。
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    for enc in (locale.getpreferredencoding(False), "gbk"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "main.py", *args],
        capture_output=True, timeout=60,
        cwd=".", creationflags=subprocess.CREATE_NO_WINDOW)


def test_version_flag_prints_name_and_version():
    proc = _run("--version")
    out = _decode(proc.stdout)
    err = _decode(proc.stderr)
    assert proc.returncode == 0, f"退出码应为 0,实际 {proc.returncode}:{err}"
    assert out.strip() == EXPECTED, f"stdout 应为 {EXPECTED!r},实际 {out!r}"
    assert err.strip() == "", f"stderr 应干净(调用方要能直接取值),实际 {err!r}"


def test_version_flag_does_not_send_the_task_it_was_given():
    """带任务也只出版本 —— 它没有走到"发一次请求"那条路。"""
    proc = _run("--version", "帮我把这个项目的版本号报一下")
    out = _decode(proc.stdout)
    assert proc.returncode == 0
    assert out.strip() == EXPECTED
    assert len(out.strip().splitlines()) == 1


def test_version_answers_even_with_an_impossible_workspace():
    """`--workspace` 的校验在 load_config 之后 —— 这一条证明旗标短路在它之前。"""
    proc = _run("--version", "--workspace", str(Path("Z:/绝对不存在的目录/x")))
    out = _decode(proc.stdout)
    assert proc.returncode == 0, "不该因为工作区不存在就退非 0"
    assert out.strip() == EXPECTED


def test_version_block_sits_before_load_config_in_source():
    """结构证据:那一段不许被挪到 `config = load_config()` 之后。"""
    src = MAIN_PY.read_text(encoding="utf-8")
    at_version = src.index('if "--version" in sys.argv:')
    at_config = src.index("config = load_config()")
    assert at_version < at_config, (
        "`--version` 必须在 load_config() 之前 —— 挪到后面它就变成了"
        "「配置坏了就问不出自己几版」,那正是这枚旗标要消灭的形状")
    # 顺带钉住它打印的是唯一那份版本号,不是另抄一个字面量
    assert re.search(r'print\(f"\{APP_NAME\} \{__version__\}"\)', src), (
        "版本号只能来自 minicode.__version__ 这一处,不许在 main.py 里另写字面量")
