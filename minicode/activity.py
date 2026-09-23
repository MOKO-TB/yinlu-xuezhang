"""本次会话的「活动」聚合:把这一轮真的做过什么归成几类,喂给右侧面板(v0.9.34)。

为什么在后端算,不让前端自己数:前端手里只有**渲染过的事件流**,一刷新、一切会话就没了,
而且它看到的是截断后的展示文本(工具卡片为了省地方会把路径中段省略)。真数据在
`session.messages` 里 —— 工具调用的完整参数(路径、网址、任务描述)与完整结果(成功 /
被拒 / 报错)只有后端有。所以这个模块是一个**纯函数**:给它消息列表,它给你一份
可以照着画表格的字典,不碰任何界面。

四条口径(都是"不能只是装饰"这条规矩的直接结果):
①**每一行都由一条真实的工具调用撑起来**。没有调用过 `task` 就不会有子代理行;
   没写过文件就不会有产出行。类别为空时前端整段不显示,而不是显示一排"暂无"。
②**产出行会去磁盘上核一遍**(在不在、多大、什么时候改的)。模型说"已生成 report.docx"
   而文件其实不存在(被用户删了、或它压根没写成)是真实会发生的事,面板不能跟着一起装。
③**运行中与失败分开标**:判失败用的是 `gui_ui.GuiUI.tool_done` 同一份前缀口径
   (`错误` / `工具执行出错` / `用户拒绝`) —— 两处若漂了,聊天里工具卡片显示失败、
   右侧面板却显示完成,那就是自相矛盾的装饰。
④**上下文压缩后早期动作会进摘要、不再出现在这里** —— 面板写的是「本次会话(可见)」,
   不假装能翻出已经被压缩掉的完整历史。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from minicode import provenance

CLIP = 160                # 单行主标题/副标题的截断长度
DETAIL_MAX = 4000         # 点开才看的全文(子代理报告、记忆内容)
GROUP_LIMIT = 12          # 每类最多列几行,超出只报个数
RECENT_FIRST = True       # 面板按"最新在上"排,和用户扫一眼的顺序一致

# 与 gui_ui.GuiUI.tool_done 同口径:结果以这些前缀开头 = 这一步没成
FAILED_PREFIXES = ("错误", "工具执行出错", "用户拒绝")

FILE_TOOLS = {"write_file": "写入", "edit_file": "编辑", "browser_screenshot": "截图"}
URL_TOOLS = {"web_crawl", "browser_open", "browser_eval", "page_parse"}
QUERY_TOOLS = {"web_search": "网页搜索", "search_knowledge": "知识库"}
MEMORY_LABELS = {"save": "记住", "forget": "忘记", "dream": "整理去重"}
_URL_RE = re.compile(r"https?://[^\s\"'<>）)】\]]+")
_KB_URL_RE = re.compile(r"来源网址[:：]\s*(\S+)")

GROUPS = (("subagents", "子代理", "🤖"),
          ("files", "产出", "📝"),
          ("web", "网页查阅", "🌐"),
          ("sources", "来源", "🔗"),
          ("memory", "记忆", "🧠"))


# ------------------------------------------------------------------ 小工具

def _clip(text, limit=CLIP) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[:limit - 1] + "…"


def _args(call: dict) -> dict:
    """tool_calls[i].function.arguments 是 JSON **字符串**;坏 JSON 当空参数。"""
    raw = ((call.get("function") or {}).get("arguments"))
    if isinstance(raw, dict):
        return raw
    try:
        val = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return val if isinstance(val, dict) else {}


def _as_list(val) -> list:
    return val if isinstance(val, list) else [val]


def _failed(result: str) -> bool:
    return isinstance(result, str) and result.startswith(FAILED_PREFIXES)


def _url_and_host(url: str) -> tuple[str, str, str]:
    """拆成 (干净的网址, 主机名, 给人看的标题)。

    标题带上路径是必要的:一次任务里学长可能在同一个域名下开了五六个页面,
    只显示 `jwc.gzasc.edu.cn` 的话,那五行在面板上长得一模一样。
    """
    url = (url or "").strip().rstrip(".,;")
    try:
        parts = urlparse(url)
        host = (parts.hostname or "").replace("www.", "")
        path = (parts.path or "").rstrip("/")
    except ValueError:
        host, path = "", ""
    label = host + (path[:60] if path else "")
    return url, host, label or url


def _rel(path, workspace) -> str:
    """工作区内的路径统一成相对写法显示(绝对路径一行读不完)。"""
    text = str(path or "").strip()
    if not text:
        return ""
    p = Path(text)
    if workspace and not p.is_absolute():
        p = Path(workspace) / p
    try:
        rp = p.resolve()
        if workspace:
            return rp.relative_to(Path(workspace).resolve()).as_posix()
    except (OSError, ValueError):
        pass
    return text.replace("\\", "/")


def _size_text(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / 1048576:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n} B"


def _disk_state(path, workspace) -> tuple[str, str]:
    """回到磁盘上核一遍,返回 (状态, 补充说明)。这一句是面板的诚实来源。"""
    text = str(path or "").strip()
    if not text:
        return "unknown", ""
    p = Path(text)
    if workspace and not p.is_absolute():
        p = Path(workspace) / p
    try:
        if not p.exists():
            return "missing", "现在磁盘上没有这个文件了"
        st = p.stat()
        when = time.strftime("%H:%M", time.localtime(st.st_mtime))
        kind = "目录" if p.is_dir() else _size_text(st.st_size)
        return "ok", f"{kind} · 改于 {when}"
    except OSError:
        return "unknown", ""


# ------------------------------------------------------------------ 各抽取器

def _call_index(messages) -> tuple[dict, dict, list]:
    """扫一遍消息:返回 (call_id -> {name,args}, call_id -> 结果文本, 调用顺序)。

    `session.add_tool_result` 不落 tool 的 name,只落 `tool_call_id`,所以工具名
    必须回到发起它的那条 assistant 消息里找 —— 这也是唯一能知道"哪个调用还没回来"
    (即正在跑)的办法。
    """
    calls: dict[str, dict] = {}
    order: list[str] = []
    for m in messages or []:
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        for call in m.get("tool_calls") or []:
            cid = str(call.get("id") or "")
            name = str((call.get("function") or {}).get("name") or "")
            if not name:
                continue
            if not cid:
                cid = f"{name}#{len(order)}"      # 没 id 的兜底:照样算一次调用
            calls[cid] = {"name": name, "args": _args(call)}
            order.append(cid)
    results: dict[str, str] = {}
    for m in messages or []:
        if isinstance(m, dict) and m.get("role") == "tool":
            cid = str(m.get("tool_call_id") or "")
            body = m.get("content")
            if cid and isinstance(body, str):
                results[cid] = body
    return calls, results, order


def _extract_subagents(calls, results) -> list[dict]:
    items = []
    for cid, call in calls.items():
        if call["name"] != "task":
            continue
        types = [str(t) for t in _as_list(call["args"].get("type"))]
        tasks = [str(t) for t in _as_list(call["args"].get("task"))]
        n = max(len(types), len(tasks)) or 1
        result = results.get(cid)
        # 并行派发时一次调用会出 N 个子代理:一调用一行还是 N 行?
        # N 行 —— 用户关心的正是"这四个探索各查了什么",合成一行就没法逐个看。
        for i in range(n):
            ttype = types[i] if i < len(types) else (types[0] if types else "")
            task = tasks[i] if i < len(tasks) else (tasks[0] if len(tasks) == 1 else "")
            if result is None:
                status, badge = "running", "运行中"
                detail = ""
            elif _failed(result):
                status, badge = "failed", "失败"
                detail = _clip(result, DETAIL_MAX)
            else:
                status, badge = "ok", "已返回报告"
                detail = result[:DETAIL_MAX]
            items.append({
                "key": f"sa:{cid}:{i}",
                "title": _clip(task) or f"{ttype} 子代理",
                "sub": (f"{ttype} · 报告 {len(result)} 字" if detail else ttype),
                "status": status, "badge": badge,
                "action": ({"type": "detail", "label": "看报告", "text": detail}
                           if detail else None),
            })
    return items


def _extract_files(calls, results, workspace) -> list[dict]:
    """产出:同一路径多次改动**合成一行**(次数累加),状态取最新一次。"""
    merged: dict[str, dict] = {}
    for cid, call in calls.items():
        name = call["name"]
        if name not in FILE_TOOLS:
            continue
        raw = str(call["args"].get("path") or "").strip()
        if not raw:
            continue
        disp = _rel(raw, workspace) or raw
        op = FILE_TOOLS[name]
        result = results.get(cid)
        if result is None:
            status, badge = "running", f"{op}中"
        elif _failed(result):
            status, badge = "failed", f"{op}失败"
        else:
            status, badge = "ok", f"{op}完成"
        state, note = _disk_state(raw, workspace) if status == "ok" else ("", "")
        row = merged.get(disp)
        if row:
            row["n"] += 1
            row["ops"].append(op)
            row["status"], row["badge"] = status, badge
            row["note"], row["state"] = note, state
        else:
            merged[disp] = {"path": raw, "title": disp, "n": 1, "ops": [op],
                            "status": status, "badge": badge,
                            "note": note, "state": state}
    items = []
    for disp, row in merged.items():
        ops = " / ".join(dict.fromkeys(row["ops"]))
        sub = f"{ops} {row['n']} 次"
        if row["note"]:
            sub += f" · {row['note']}"
        status = row["status"]
        badge = row["badge"]
        if row["state"] == "missing":       # 说写成写了,磁盘上却没有 —— 直说
            badge, status = "已不在磁盘", "missing"
        items.append({
            "key": f"f:{disp}", "title": disp, "sub": _clip(sub),
            "status": status, "badge": badge,
            "action": ({"type": "reveal", "label": "打开所在文件夹",
                        "path": row["path"]} if status != "missing" else None),
        })
    return items


def _extract_web(calls, results) -> list[dict]:
    """网页查阅:模型**发起**的联网动作(带网址的开网址,只有关键词的回填输入框)。"""
    items, seen = [], {}
    for cid, call in calls.items():
        name = call["name"]
        args = call["args"]
        url = str(args.get("url") or "").strip()
        if name in URL_TOOLS and url:
            u, host, label = _url_and_host(url)
            verb = {"web_crawl": "抓取", "browser_open": "打开页面",
                    "browser_eval": "页面取数", "page_parse": "解析表格"}[name]
            title, action = _clip(label), {"type": "open", "url": u}
            sub = verb
        elif name in QUERY_TOOLS:
            q = _clip(str(args.get("query") or "").strip())
            if not q:
                continue
            verb = QUERY_TOOLS[name]
            title, sub = q, verb
            action = {"type": "fill", "text": f"{verb}:补充查一下 {q}"}
        else:
            continue
        key = f"w:{name}:{title}"
        result = results.get(cid)
        status = ("running" if result is None
                  else "failed" if _failed(result) else "ok")
        row = seen.get(key)
        if row:                       # 同一个网址/同一个词查多次:一行,状态跟最新那次
            row["n"] += 1
            row["item"]["status"] = status
            row["item"]["badge"] = _badge(status)
            continue
        item = {"key": key, "title": title, "sub": sub,
                "status": status, "badge": _badge(status), "action": action}
        seen[key] = {"n": 1, "item": item}
        items.append(item)
    for row in items:
        n = seen[row["key"]]["n"]
        if n > 1:
            row["sub"] += f" ×{n}"
    return items


def _badge(status: str) -> str:
    return {"running": "进行中", "failed": "失败", "ok": "完成"}.get(status, status)


def _extract_sources(calls, results) -> list[dict]:
    """来源:**真的进了上下文**的外部内容 —— 围栏头里的来源 + 知识库结果里的来源网址。

    与「网页查阅」不重复:那一类是"模型做过哪些联网动作",这一类是"这次回答的依据
    是哪些页面/条目"(搜过但没用到、用到但没搜的不一定对得上)。
    """
    merged: dict[str, dict] = {}
    for cid, call in calls.items():
        result = results.get(cid)
        if not isinstance(result, str):
            continue
        found: list[str] = []
        if provenance.is_wrapped(result):
            _, source = provenance.strip_marks(result)
            found += _URL_RE.findall(source or "")
        if not found and call["name"] in URL_TOOLS:
            found = _URL_RE.findall(str(call["args"].get("url") or ""))
        found += _KB_URL_RE.findall(result)
        for raw in found:
            u, host, label = _url_and_host(raw)
            if not u:
                continue
            row = merged.setdefault(u, {"url": u, "title": label, "n": 0,
                                        "tools": []})
            row["n"] += 1
            if call["name"] not in row["tools"]:
                row["tools"].append(call["name"])
    return [{"key": f"s:{u}", "title": _clip(row["title"]),
             "sub": ("进过上下文 {} 次".format(row["n"])
                     + (" · " + "、".join(row["tools"][:2]) if row["tools"] else "")),
             "status": "ok", "badge": f"{row['n']} 次",
             "action": {"type": "open", "url": u}}
            for u, row in merged.items()]


def _extract_memory(calls, results) -> list[dict]:
    items = []
    for cid, call in calls.items():
        if call["name"] != "memory":
            continue
        action = str(call["args"].get("action") or "")
        if action not in MEMORY_LABELS:        # list/search 是查看,不算"记忆有变化"
            continue
        content = str(call["args"].get("content") or call["args"].get("query") or "")
        scope = str(call["args"].get("scope") or "project")
        result = results.get(cid)
        if result is None:
            status, badge = "running", "进行中"
        elif _failed(result):
            status, badge = "failed", "失败"
        else:
            status, badge = "ok", MEMORY_LABELS[action]
        items.append({
            "key": f"m:{cid}", "title": _clip(content) or MEMORY_LABELS[action],
            "sub": f"{'用户级' if scope == 'user' else '本项目'} · {action}",
            "status": status, "badge": badge,
            "action": ({"type": "detail", "label": "看全文",
                        "text": (content + ("\n\n—— 工具回话 ——\n" + result
                                            if result else ""))[:DETAIL_MAX]}
                       if content else None),
        })
    return items


# ------------------------------------------------------------------ 对外入口

def session_activity(messages, workspace=None) -> dict:
    """把会话消息聚合成右侧面板要的那份字典(纯函数,不碰磁盘以外的东西)。"""
    calls, results, order = _call_index(messages)
    groups = {
        "subagents": _extract_subagents(calls, results),
        "files": _extract_files(calls, results, workspace),
        "web": _extract_web(calls, results),
        "sources": _extract_sources(calls, results),
        "memory": _extract_memory(calls, results),
    }
    # 抽取器都按调用顺序 append,所以倒过来就是"最新在上"(合并过行的类别以最后一次
    # 改动为准,同样落在末尾)
    out, total = [], 0
    for key, label, icon in GROUPS:
        rows = list(reversed(groups[key]))
        total += len(rows)
        out.append({"key": key, "label": label, "icon": icon,
                    "count": len(rows), "items": rows[:GROUP_LIMIT],
                    "more": max(0, len(rows) - GROUP_LIMIT)})
    errors = sum(1 for cid in order if cid in results and _failed(results[cid]))
    running = sum(1 for cid in order if cid not in results)
    return {"ok": True, "groups": out,
            "summary": {"calls": len(order), "errors": errors, "running": running,
                        "workspace": _rel(workspace, None) if workspace else ""},
            # 前端拿它排"哪一类该显示";空类直接不渲染,不摆一屏"暂无"
            "empty": total == 0}
