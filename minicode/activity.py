"""本次会话的「活动」聚合:把这一轮真的做过什么归成几类,喂给右侧面板(v0.9.34)。

v0.9.37 起这一栏叫「任务监控」,多了两样:**按真实先后的时间线**(`timeline`,
回答"它先做了什么、哪一步失败了")和**此刻在跑哪一步**(`now_line`)。
时刻这一列只可能来自 GUI 的实时表(`gui_ui.GuiUI.live_rows`)—— 会话消息里
压根没有时间戳,所以没记到的行一律不给时间,而不是拿刷新时刻凑一个。

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
③**运行中与失败分开标**:判失败问的是 `minicode.toolsum.is_failure`(v0.9.39 Z1
   起它是一份**唯一**口径,终端、GUI 工具卡片、这里三处都问同一个函数)—— 原来三处
   各抄一份前缀字面量,抄着抄着就会漂,而漂了的后果是自相矛盾的装饰:聊天里那张卡片
   打 ✓,这一栏却写着失败。
④**上下文压缩后早期动作会进摘要、不再出现在这里** —— 面板写的是「本次会话(可见)」,
   不假装能翻出已经被压缩掉的完整历史。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from minicode import provenance, toolsum

CLIP = 160                # 单行主标题/副标题的截断长度
DETAIL_MAX = 4000         # 点开才看的全文(子代理报告、记忆内容)
GROUP_LIMIT = 12          # 每类最多列几行,超出只报个数
RECENT_FIRST = True       # 面板按"最新在上"排,和用户扫一眼的顺序一致

# 唯一真相在 toolsum(v0.9.39 Z1);这里只是**沿用同一个名字**给下面的聚合用,
# 不再自己写一份元组 —— 自己写一份,就又有机会和界面那侧漂开。
FAILED_PREFIXES = toolsum.FAILED_PREFIXES

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
    return toolsum.is_failure(result)


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
            calls[cid] = {"name": name, "args": _args(call),
                          "ts": str(m.get("ts") or "")}
            order.append(cid)
    results: dict[str, str] = {}
    result_ts: dict[str, str] = {}
    for m in messages or []:
        if isinstance(m, dict) and m.get("role") == "tool":
            cid = str(m.get("tool_call_id") or "")
            body = m.get("content")
            if cid and isinstance(body, str):
                results[cid] = body
                result_ts[cid] = str(m.get("ts") or "")
    return calls, results, order, result_ts


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


_FILE_TOOLS_LINE = re.compile(r"^\[files\|([^\]]+)\]\s*(.+)$", re.MULTILINE)


def _extract_files(calls, results, workspace) -> list[dict]:
    """产出:同一路径多次改动**合成一行**(次数累加),状态取最新一次。

    两个来源:**主会话直调**的 write_file/edit_file/browser_screenshot,与
    **子代理**带回来的 `[files|档位]` 尾注(v0.9.76 —— 子代理的 tool_calls
    留在它自己的 session 里,主会话只有 task 报告文本,不解析尾注的话
    并行写三个文件、产出行一片空)。尾注里的路径同样回磁盘核一遍:没写成
    就是「已不在磁盘」,与直调同一条诚实口径;档位写在行首(如 campus),
    摆进副标题让用户知道这文件是谁写的。
    """
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
                            "note": note, "state": state, "agents": []}
    # 子代理的产出:解析 task 结果里的 [files|档位] 尾注(报告是人话,这一行
    # 是机器可读的账 —— subagents._files_note 写的)。失败的 task 不算产出。
    for cid, call in calls.items():
        if call["name"] != "task":
            continue
        result = results.get(cid)
        if not isinstance(result, str) or _failed(result):
            continue
        for m in _FILE_TOOLS_LINE.finditer(result):
            domain = _clip(m.group(1).strip(), 20) or "子代理"
            for raw in [p.strip() for p in m.group(2).split("|") if p.strip()]:
                disp = _rel(raw, workspace) or raw
                state, note = _disk_state(raw, workspace)
                row = merged.get(disp)
                if row:
                    row["n"] += 1
                    if domain not in row["agents"]:
                        row["agents"].append(domain)
                    if row["status"] == "running":
                        row["status"], row["badge"] = "ok", "产出完成"
                    if not row["state"]:
                        row["state"], row["note"] = state, note
                else:
                    merged[disp] = {"path": raw, "title": disp, "n": 1,
                                    "ops": [], "status": "ok", "badge": "产出完成",
                                    "note": note, "state": state, "agents": [domain]}
    items = []
    for disp, row in merged.items():
        ops = " / ".join(dict.fromkeys(row["ops"]))
        sub = f"{ops} {row['n']} 次" if ops else f"产出 {row['n']} 处"
        if row.get("agents"):
            sub += f" · {'、'.join(row['agents'])} 子代理"
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


_WEB_VERBS = {"web_crawl": "抓取", "browser_open": "打开页面",
              "browser_eval": "页面取数", "page_parse": "解析表格"}
_WEB_NOTE_LINE = re.compile(r"^\[web\|([^\]]+)\]\s*(\S+)\s+(\S+)\s+(.+)$",
                            re.MULTILINE)


def _web_row(name: str, value: str, status: str, domain: str = "") -> dict | None:
    """一行「网页查阅」:带网址的开网址,只有关键词的回填输入框。

    主会话直调与子代理尾注**共用这一份算法** —— 动词表、动作类型、key 口径只有
    一处,不然同一件事在两栏里会各说一套。`domain` 非空表示这一行是某一档子代理
    读的:写进副标题(用户知道是谁读的)也写进 key(同一个网址主会话读过、子代理
    也读过时是两行,不合并 —— 合并就把"谁读的"这件事抹掉了)。
    """
    if name in URL_TOOLS:
        u, _host, label = _url_and_host(value)
        if not u:
            return None
        title, sub = _clip(label), _WEB_VERBS.get(name, "联网")
        action = {"type": "open", "url": u}
    elif name in QUERY_TOOLS:
        title = _clip(value)
        if not title:
            return None
        sub = QUERY_TOOLS[name]
        action = {"type": "fill", "text": f"{sub}:补充查一下 {title}"}
    else:
        return None
    if domain:
        sub += f" · {domain} 子代理"
    return {"key": f"w:{domain or 'main'}:{name}:{title}", "title": title,
            "sub": sub, "status": status, "badge": _badge(status), "action": action}


def _extract_web(calls, results) -> list[dict]:
    """网页查阅:模型**发起**的联网动作(带网址的开网址,只有关键词的回填输入框)。

    两个来源:**主会话直调**,与子代理带回来的 `[web|档位]` 尾注(`subagents._web_note`
    写的)—— 不解析尾注的话,并行几路查网页这一栏一片空,与「产出」栏 v0.9.76
    补的是同一个盲区。失败的 task 不算(那一路根本没跑成)。
    """
    items, seen = [], {}

    def add(row):
        if row is None:
            return
        hit = seen.get(row["key"])
        if hit:                   # 同一个网址/同一个词查多次:一行,状态跟最新那次
            hit["n"] += 1
            hit["item"]["status"] = row["status"]
            hit["item"]["badge"] = row["badge"]
            return
        seen[row["key"]] = {"n": 1, "item": row}
        items.append(row)

    for cid, call in calls.items():
        name = call["name"]
        args = call["args"]
        result = results.get(cid)
        status = ("running" if result is None
                  else "failed" if _failed(result) else "ok")
        if name in URL_TOOLS:
            add(_web_row(name, str(args.get("url") or "").strip(), status))
        elif name in QUERY_TOOLS:
            add(_web_row(name, str(args.get("query") or "").strip(), status))
        elif name == "task" and isinstance(result, str) and not _failed(result):
            for m in _WEB_NOTE_LINE.finditer(result):
                domain = _clip(m.group(1).strip(), 20) or "子代理"
                got = m.group(3)
                add(_web_row(m.group(2), m.group(4).strip(),
                             got if got in ("ok", "failed", "running") else "ok",
                             domain))
    for row in items:
        n = seen[row["key"]]["n"]
        if n > 1:
            row["sub"] += f" ×{n}"
    return items


def _badge(status: str) -> str:
    return {"running": "进行中", "failed": "失败", "ok": "完成",
            "stale": "已中断"}.get(status, status)


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


# ------------------------------------------------------- 时间线(v0.9.37)

TURN_LIMIT = 12         # 一轮最多列几行(和类别那套一样:再多就是噪声)
TURNS_LIMIT = 8         # 面板最多往回翻几轮
TIMELINE_DETAIL = 1200  # 时间线一行点开能看的全文长度


def _turn_split(messages) -> list[dict]:
    """按用户发言把上下文切成轮次,返回 [{"n", "title", "cids": [调用 id…]}]。

    只认 `role=user` 这条真实边界 —— 一次"帮我改课表"到学长说完话就是一轮。
    没有用户发言(比如刚恢复的半截上下文)也照样归到第 1 轮,不丢调用。
    """
    turns: list[dict] = []

    def open_turn(title: str = "") -> dict:
        t = {"n": len(turns) + 1, "title": title, "cids": []}
        turns.append(t)
        return t

    cur: dict | None = None
    n_calls = 0
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role == "user":
            cur = open_turn(_clip(_content_text(m.get("content")), 60))
        elif role == "assistant":
            if cur is None:
                cur = open_turn("")
            for call in m.get("tool_calls") or []:
                name = str((call.get("function") or {}).get("name") or "")
                if not name:
                    continue
                # 没 id 的兜底键必须和 `_call_index` 同一条规则(全局第几次调用),
                # 否则两边算出来的键对不上,这一行会在时间线里凭空消失 = 少报。
                cid = str(call.get("id") or "") or f"{name}#{n_calls}"
                cur["cids"].append(cid)
                n_calls += 1
    return turns


def _content_text(content) -> str:
    """用户消息可能是多模态数组:只取文字部分(图片那格没什么可标题化的)。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(i.get("text")) for i in content
                        if isinstance(i, dict) and i.get("type") == "text")
    return ""


def _pair_times(rows: list[dict], live: list[dict] | None) -> int:
    """把实时表里的真实时刻配到时间线行上。**配不上就不配**(返回配对数)。

    为什么能配:`tool_start` 推的摘要与这里从消息里复算的摘要是同一个函数
    (`toolsum.summarize`)算出来的,同一次调用必然同字。指针只往前走,所以
    "同一个文件读三次"会一一对上,不会三次都领第一次的时间。
    """
    if not live:
        return 0
    j, matched = 0, 0
    for row in rows:
        for k in range(j, len(live)):
            cand = live[k]
            if str(cand.get("name") or "") != row["tool"]:
                continue
            if str(cand.get("summary") or "") != row["raw"]:
                continue
            row["start"], row["end"], row["dur"] = (
                cand.get("start"), cand.get("end"), _dur(cand))
            j = k + 1
            matched += 1
            break
    return matched


def _dur(entry: dict):
    start, end = entry.get("start"), entry.get("end")
    if start is None or end is None:
        return None
    return max(0, int(round(float(end) - float(start))))


def _ts_epoch(ts: str) -> "float | None":
    """消息时间戳("YYYY-MM-DD HH:MM:SS")→ epoch;解析不了回 None。"""
    import time as _time
    try:
        return _time.mktime(_time.strptime(str(ts or "")[:19],
                                           "%Y-%m-%d %H:%M:%S"))
    except (ValueError, TypeError):
        return None


def _restore_from_ts(rows: list[dict], calls: dict, result_ts: dict) -> int:
    """实时表没配到的行,用 v0.9.66 起的消息时间戳还原时刻与耗时。

    诚实边界:①只填 `start/end/dur` 为**空**的行 —— 实时表的时刻永远优先
    (它在调用那一刻记的,精度到秒;消息 ts 也是那一刻,但两者并存时没必要
    重新计算);②**老留档没有 ts,照旧不给时间** —— 宁可空着,不猜;
    ③dur 是"发起调用 → 结果落档"的真实间隔,不含模型思考的间隙,口径与
    实时表一致(实时表也是 start=tool_start/end=tool_done)。
    """
    restored = 0
    for row in rows:
        if row.get("start") is not None:
            continue
        call = calls.get(row.get("cid")) or {}
        start = _ts_epoch(call.get("ts"))
        if start is None:
            continue
        row["start"] = start
        end = _ts_epoch(result_ts.get(row.get("cid")))
        if end is not None and end >= start:
            row["end"] = end
            row["dur"] = _dur({"start": start, "end": end})   # 与实时表同一格式
        restored += 1
    return restored


def _clock(epoch) -> str:
    if epoch is None:
        return ""
    try:
        return time.strftime("%H:%M:%S", time.localtime(float(epoch)))
    except (OSError, OverflowError, ValueError, TypeError):
        return ""


def _timeline_action(row: dict, result: str) -> dict | None:
    """一行的点击去处:能开文件夹就开、能开网页就开、否则看这一行的原文。"""
    args = row.get("args") or {}
    name = toolsum.short_name(row["tool"])
    if name in FILE_TOOLS and str(args.get("path") or "").strip():
        return {"type": "reveal", "label": "打开所在文件夹",
                "path": str(args["path"]).strip()}
    url = str(args.get("url") or "").strip()
    if name in URL_TOOLS and url:
        return {"type": "open", "url": url}
    if result:
        return {"type": "detail", "label": "看这一步的原文",
                "text": result[:TIMELINE_DETAIL]}
    return None


def timeline(messages, workspace=None, live=None, busy=True) -> dict:
    """按**真实先后**排的任务时间线:每次工具调用一行,按用户发言切成轮次。

    与上面那五类(`session_activity` 的 groups)是两种问法:类别回答"这一轮总共
    产出/查过什么",时间线回答"它先做了什么、后做了什么、哪一步失败了"。
    两边都从同一份 `session.messages` 里数,不会一个说 12 次一个说 9 次。

    诚实口径三条:①轮次上的次数永远是**真总数**,`MAX/TURN_LIMIT` 截断只少列行、
    并写明"另有 N 次没列出";②时刻只来自 GUI 实时表,没记到的行不给时间
    (并行子代理的内部工具、上一版程序跑的都算没记到;v0.9.66 起重启后可用
消息时间戳还原,**再老的留档没有 ts 仍旧不给时间**);③在跑的那一步单独标,
    不和"已经失败"混在一个数里。
    """
    calls, results, _order, result_ts = _call_index(messages)
    rows: list[dict] = []
    for turn in _turn_split(messages):
        for cid in turn["cids"]:
            call = calls.get(cid)
            if not call:
                continue
            name, args = call["name"], call["args"]
            summary = toolsum.summarize(name, args)
            result = results.get(cid)
            if result is not None:
                status = "failed" if _failed(result) else "ok"
            else:
                status = "running" if busy else "stale"
            rows.append({"key": f"t:{cid}", "cid": cid, "tool": name,
                         "turn": turn["n"], "turn_title": turn["title"],
                         "verb": toolsum.verb(name, args),
                         "raw": summary, "summary": _clip(summary),
                         "args": args,
                         "status": status, "badge": _badge(status),
                         "start": None, "end": None, "dur": None,
                         "result": result if isinstance(result, str) else ""})
    _pair_times(rows, live)
    _restore_from_ts(rows, calls, result_ts)
    by_turn: dict[int, list[dict]] = {}
    for row in rows:
        by_turn.setdefault(row["turn"], []).append(row)
    turns = []
    # 「×N 未命中」:同一个查询词在可见上下文里第几次搜空。计数**不在这里另算一份**
    # —— 与工具结果末尾那句(B0)共用 `feedback_recall.empty_hits` 那一次扫描,
    # 两边各算一次迟早对不上(那就是"聊天里说没搜到、监控栏写着搜到了")。
    try:
        from minicode import feedback_recall
        empty_marks = feedback_recall.empty_hits_by_call(messages)
        mark_from = feedback_recall.MARK_FROM
    except Exception:                 # 回灌坏了不该让整栏画不出来
        empty_marks, mark_from = {}, 2
    for n, items in sorted(by_turn.items(), reverse=True):
        counts = {"running": 0, "failed": 0, "stale": 0}
        for r in items:
            if r["status"] in counts:
                counts[r["status"]] += 1
        title = items[0]["turn_title"]
        when = _clock(items[0].get("start"))
        # 轮与轮之间最新在上;**轮内按先后顺序读下来** —— 时间线存在的理由就是
        # "它先做了什么、后做了什么",轮内再倒过来就没人能顺着看了。
        # 截断保留这一轮最近的 TURN_LIMIT 行(少列行,不少报次数)。
        shown = items[-TURN_LIMIT:]
        for r in shown:
            r["action"] = _timeline_action(r, r["result"])
        for r in items:
            # 时刻只可能是空串:实时表没记到的行(并行子代理内部、上一版程序跑的)
            # 就不显示时间。宁可少一列,不许拿"现在"凑一个。
            r["time"] = _clock(r.get("start"))
            r["dur_text"] = f"{r['dur']}s" if r.get("dur") is not None else ""
            hits = empty_marks.get(r.get("cid"))
            if hits and hits >= mark_from:
                r["repeat_text"] = f"×{hits} 未命中"
            for k in ("args", "result", "cid", "raw", "turn_title", "end", "dur"):
                r.pop(k, None)
        turns.append({"n": n, "title": title,
                      "calls": len(items), "errors": counts["failed"],
                      "running": counts["running"], "stale": counts["stale"],
                      "when": when,
                      "items": shown,
                      "more": max(0, len(items) - len(shown))})
    hidden = max(0, len(turns) - TURNS_LIMIT)
    return {"turns": turns[:TURNS_LIMIT], "turns_total": len(turns),
            "turns_more": hidden, "calls": len(rows),
            "errors": sum(1 for r in rows if r["status"] == "failed"),
            "running": sum(1 for r in rows if r["status"] == "running"),
            "stale": sum(1 for r in rows if r["status"] == "stale")}


def now_line(now: dict | None) -> dict | None:
    """「此刻在跑哪一步」那一行。没有真的在跑的东西就给 None,前端整段不显示。"""
    if not now or now.get("start") is None:
        return None
    name = str(now.get("name") or "")
    summary = str(now.get("summary") or "")
    return {"tool": name, "verb": toolsum.verb(name),
            "title": _clip(summary) or toolsum.verb(name),
            "agent_type": toolsum.agent_type(name),
            "start": _clock(now.get("start")),
            # 长命令的实时那一行(shell 报的"已跑多久/多少字/最后一行")。没有就是空串,
            # 前端那一行整条不出现 —— 拿"没有进度"装作"刚开始"是最容易看漏的谎。
            "progress": str(now.get("progress") or ""),
            "elapsed": max(0, int(now.get("elapsed") or 0))}


# ------------------------------------------------------------------ 对外入口

def session_activity(messages, workspace=None, live=None, now=None, busy=True) -> dict:
    """把会话消息聚合成右侧面板要的那份字典(纯函数,不碰磁盘以外的东西)。

    `busy` = 这个会话此刻还在不在跑。它决定"没有回话的那一步"算进行中还是算
    已中断:用户按了停止之后,`tool_done` 不会再来到,消息里那条调用就永远没有
    回话 —— 这时还写"写入中"就是谎话。
    """
    calls, results, order, result_ts = _call_index(messages)
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
        if not busy:
            for r in rows:
                if r.get("status") == "running":
                    r["status"], r["badge"] = "stale", "已中断"
        total += len(rows)
        out.append({"key": key, "label": label, "icon": icon,
                    "count": len(rows), "items": rows[:GROUP_LIMIT],
                    "more": max(0, len(rows) - GROUP_LIMIT)})
    errors = sum(1 for cid in order if cid in results and _failed(results[cid]))
    silent = [cid for cid in order if cid not in results]
    running = len(silent) if busy else 0
    interrupted = 0 if busy else len(silent)
    # 时间线与上面五类同源同一份消息:两处各数一次就会出现"卡片说 12 次、
    # 时间线只有 9 行"那种自相矛盾。live 没传(终端/网页版)时行照排,只是没时间。
    line = timeline(messages, workspace=workspace, live=live, busy=busy)
    return {"ok": True, "groups": out,
            "timeline": line, "now": now_line(now) if busy else None,
            # 「统计于 HH:MM:SS」的那个数:是**这次算**的时刻,不是动作发生的时刻
            "updated_at": time.strftime("%H:%M:%S", time.localtime()),
            "summary": {"calls": len(order), "errors": errors, "running": running,
                        "interrupted": interrupted,
                        "busy": bool(busy),
                        "workspace": _rel(workspace, None) if workspace else ""},
            # 前端拿它排"哪一类该显示";空类直接不渲染,不摆一屏"暂无"
            "empty": total == 0 and not line["calls"]}
