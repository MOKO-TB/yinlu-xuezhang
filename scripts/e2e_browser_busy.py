"""真进程验证:两份浏览器配置被占用时,学长到底怎么办。

对着**真 Edge** 跑,不用任何替身。三步都是 2026-10-09 用户截图里那个死结的形状:
教务导入点「打开登录页」报「Edge 启动后立刻退出(退出码 0),无法启用浏览器能力」,
而点多少次「再读一次」都是同一句话 —— 因为上一个实例还占着那本 profile。

跑法:
  python scripts/e2e_browser_busy.py
它会真起两次 Edge(第二次是有窗口的那一发,一闪即关),所以别在演示中途跑。
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from minicode import browser as br  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  ok   " if cond else "  FAIL ") + name + (f"  [{detail}]" if detail else ""))
    if not cond:
        FAILS.append(name)


def main() -> int:
    found = br.find_browser_exe()
    if not found:
        print("SKIP:本机没有 Edge/Chrome,这一场验不了")
        return 0
    print(f"浏览器:{found[0]} @ {found[1]}")
    # 把数据目录换根到临时目录:这样走的是**默认那本配置**(与真机同形,
    # `ensure_visible` 也只认这一个入口),又不碰用户真实的教务登录态。
    root = Path(tempfile.mkdtemp(prefix="yinlu-busy-"))
    real_app_path = br.app_path
    br.app_path = lambda *p: root.joinpath(*p) if p else root
    profile = root / "browser-profile"
    sibling = root / "browser-profile-2"
    procs: list = []
    try:
        # ── 1. 第一发:正常起一个无窗口实例,并留下我们的实例标记
        p1, port1, info1 = br.launch_browser(headless=True)
        procs.append(p1)
        check("第一次启动拿到进程与端口", p1 is not None and port1 > 0, f"port={port1}")
        marker = profile / "YinluBrowserInstance.json"
        check("用的就是默认那本配置目录", profile.exists())
        check("标记文件落在配置目录里", marker.exists())

        # ── 2. 第二发:同一本配置已被占着 —— 旧版在这里就是用户截图那句话
        p2, port2, info2 = br.launch_browser(headless=True)
        check("同配置再起:复用而不是报错", p2 is None and port2 == port1,
              f"port={port2} vs {port1}")
        check("复用这件事要说给人听", "复用" in br.last_note(), br.last_note())

        # ── 3. 教务登录那一发必须有窗口:走真入口 `ensure_visible`(它才是向导
        #       第①步调的那一只手),无窗口的实例不许被拿去登录,于是换一份新配置。
        got = br.ensure_visible()
        p3 = br._STATE.proc
        procs.append(p3)
        check("要窗口时不复用无窗口实例", p3 is not None,
              "复用了就等于让用户对着看不见的页面输密码")
        check("换到了兄弟目录", sibling.exists(),
              " ".join(str(a) for a in p3.args)[:150])
        check("换目录的代价(要重登)写在话里", "重新登录" in str(got.get("note")),
              str(got.get("note")))

        # ── 4. 收工:只清"我们最后拿着的那一发"的标记,先起那发的实例还活着,
        #       它的标记必须留着 —— 删了就等于骗下一个人"这份配置没人用"。
        br.shutdown()
        check("收工后自己那份标记已清",
              not (sibling / "YinluBrowserInstance.json").exists())
        check("还活着的那一份标记不许被清掉", marker.exists())
    except br.BrowserUnavailable as exc:
        check("整场没抛 BrowserUnavailable", False, str(exc)[:200])
    finally:
        for p in procs:
            try:
                if p is not None:
                    p.terminate()
                    p.wait(timeout=5)
            except Exception:
                pass
        br.shutdown()
        br.app_path = real_app_path
        shutil.rmtree(root, ignore_errors=True)   # root 是这一场自己造的临时目录
    print("RESULT: " + ("CLEAN" if not FAILS else f"{len(FAILS)} 项红:{FAILS}"))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
