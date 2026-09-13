"""引路学长 · 校园助手网页版入口。

用法:
  py -3.12 web_main.py                     # 监听 0.0.0.0:8480,同学从校园网访问
  py -3.12 web_main.py --open              # 顺带用本机浏览器打开
  py -3.12 web_main.py --host 127.0.0.1    # 只给自己用(同学访问不到)
  py -3.12 main.py --web                   # 等价入口(CLI 里的 --web 分支)
"""
from __future__ import annotations

import argparse

from minicode.config import ensure_config
from minicode.paths import app_path
from minicode.web_server import DEFAULT_PORT, serve


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        prog="web_main.py",
        description="引路学长 · 校园助手网页版(同学可用只读问答:知识库/图书馆/校园地图)")
    parser.add_argument("--host", default="0.0.0.0",
                        help="监听地址;默认 0.0.0.0(校园网可达),只给自己用就填 127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"端口,默认 {DEFAULT_PORT}")
    parser.add_argument("--open", action="store_true", help="启动后用本机默认浏览器打开")
    parser.add_argument("--sessions-dir", default=None, help="访客会话留档的根目录")
    parser.add_argument("--turn-cap", type=int, default=None, help="同时进行的回合上限(保护模型端点)")
    parser.add_argument("--daily-cap", type=int, default=None, help="每日发问总量上限")
    args = parser.parse_args(list(argv) if argv is not None else None)

    config = ensure_config()
    workspace = app_path()

    opts: dict = {}
    if args.sessions_dir:
        opts["sessions_root"] = args.sessions_dir
    if args.turn_cap:
        opts["turn_cap"] = args.turn_cap
    if args.daily_cap:
        opts["daily_cap"] = args.daily_cap

    serve(config, workspace, host=args.host, port=args.port,
          open_browser=args.open, **opts)


if __name__ == "__main__":
    main()
