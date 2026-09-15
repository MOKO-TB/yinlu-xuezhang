"""浏览器工具:打开网页 / 跑 JS / 截图 / 关页面。

给 Agent 装上「眼睛」:能看 JS 渲染后的真实页面(校园系统、通知页、公众号、
自己写的 dev server 都算),也能把页面画面截下来交给用户看。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from minicode import browser as br
from minicode.tools.base import ToolContext, resolve_path

SHOT_DIR = "shots"


def _human_size(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n} B"


def _unavailable(exc: Exception) -> str:
    # 开头带"错误:":终端/GUI 的工具卡片据此判成败(同其它内置工具的约定)
    return (f"错误:浏览器能力当前不可用:{exc}\n"
            "（可以让用户在本机 Edge/Chrome 里开着远程调试重启一次，"
            f"或用环境变量 {br.ENV_CDP_URL} 指定调试端口；"
            "也可以先用 web_crawl 抓静态页面，或用 search_knowledge 查本地知识库。）")


def _push(ctx, payload: dict) -> bool:
    """把截图推给 GUI 直接显示;终端/无 UI 时静默跳过(文字里仍带路径)。"""
    ui = getattr(ctx, "ui", None)
    if ui is None:
        return False
    try:
        ui.push("browser", **payload)
        return True
    except Exception:
        return False


def _rel(ctx: ToolContext, path: Path) -> str:
    try:
        return path.relative_to(ctx.workspace).as_posix()
    except ValueError:
        return path.as_posix()


# ------------------------------------------------------------------ 工具实现

def browser_open(ctx: ToolContext, url: str, wait_ms: int = br.DEFAULT_WAIT_MS,
                 wait_for: str = "", max_chars: int = br.DEFAULT_MAX_CHARS,
                 mode: str = "auto", headless: bool = True,
                 reason: str = "") -> str:
    try:
        info = br.open_page(url, wait_ms=wait_ms, wait_for=wait_for,
                            max_chars=max_chars, mode=mode, headless=headless)
    except br.BrowserUnavailable as exc:
        return _unavailable(exc)
    except Exception as exc:
        return f"错误:打开网页失败:{exc}"

    way = "用户浏览器(带登录态)" if info["mode"] == "attach" else "独立实例"
    lines = [
        f"已打开:{info['title'] or '(无标题)'}",
        f"地址:{info['url']}",
        f"加载:{'完成' if info['loaded'] else '超时(可能有长期挂着的请求)'}"
        f"，用时 {info['waited_ms']}ms；渲染方式:{way}({info['browser']})",
    ]
    if info["wait_for"]:
        lines.append(f"等待的选择器:{info['wait_for']}"
                     f"（{'已出现' if info['loaded'] else '未出现'}）")
    if not info["text"]:
        lines.append("正文:（页面没有可读文本，可能是纯画布/需要登录/尚未渲染）")
    else:
        lines.append(f"正文（共 {info['chars']} 字符"
                     f"{'，已截断' if info['truncated'] else ''}）：\n{info['text']}")
    lines.append("需要看画面就用 browser_screenshot；需要精确取数（价格/表格/链接）"
                 "用 browser_eval；换页继续用 browser_open（复用同一个标签页）。")
    return "\n".join(lines)


def browser_eval(ctx: ToolContext, js: str, await_promise: bool = False,
                 max_chars: int = 4000, reason: str = "") -> str:
    expr = (js or "").strip()
    if not expr:
        return "错误:js 不能为空。"
    try:
        value = br.evaluate(expr, await_promise=await_promise)
    except br.BrowserUnavailable as exc:
        return _unavailable(exc)
    except Exception as exc:
        return f"错误:在页面里执行 JS 失败:{exc}"
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = "" if value is None else str(value)
    text = text.strip()
    if not text:
        return "JS 执行完成，返回值为空（undefined / null / 空字符串）。"
    limit = max(200, min(int(max_chars or 4000), br.MAX_CHARS))
    if len(text) > limit:
        text = text[:limit] + "\n…(已截断)"
    return f"JS 返回值:\n{text}"


def browser_screenshot(ctx: ToolContext, path: str = "", full_page: bool = True,
                       reason: str = "") -> str:
    rel = (path or "").strip()
    if not rel:
        rel = f"{SHOT_DIR}/网页-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"
    try:
        target = resolve_path(ctx, rel, for_write=True)
    except Exception as exc:
        return f"错误:截图保存路径不合法:{exc}"
    if target.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
        target = target.with_suffix(".png")
        rel = _rel(ctx, target)
    try:
        info = br.screenshot(target, full_page=full_page)
    except br.BrowserUnavailable as exc:
        return _unavailable(exc)
    except Exception as exc:
        return f"错误:截图失败:{exc}"

    rel = _rel(ctx, Path(info["path"]))
    w, h = info["viewport"]
    lines = [f"截图已保存:{rel}（{_human_size(info['bytes'])}"
             f"{f'，可视区 {w}×{h}' if w and h else ''}）"]
    if info.get("title") or info.get("url"):
        lines.append(f"页面:{info.get('title') or '(无标题)'} {info.get('url') or ''}".strip())
    lines.append(f"要在回答里给用户看这张图，就原样写上这一段：![]({rel})")
    _push(ctx, {"op": "shot", "path": rel, "title": info.get("title") or "",
                "url": info.get("url") or "", "bytes": info["bytes"]})
    return "\n".join(lines)


def browser_close(ctx: ToolContext, reason: str = "") -> str:
    try:
        closed = br.close_current()
    except Exception as exc:
        return f"错误:关闭页面失败:{exc}"
    if not closed:
        return "当前会话没有打开的页面，无需关闭。"
    return "已关闭当前页面（浏览器里对应的标签页也一起关掉了）。"


def browser_status(ctx: ToolContext, reason: str = "") -> str:
    info = br.health()
    if info["ok"]:
        return (f"浏览器能力可用（{info['mode']}，{info['browser']}）：{info['detail']}")
    return f"浏览器能力不可用：{info['detail']}"
