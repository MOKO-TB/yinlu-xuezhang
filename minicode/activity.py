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

import hashlib
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
    batch_n = 0
    for m in messages or []:
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        batch_ids: list[str] = []
        for call in m.get("tool_calls") or []:
            cid = str(call.get("id") or "")
            name = str((call.get("function") or {}).get("name") or "")
            if not name:
                continue
            if not cid:
                cid = f"{name}#{len(order)}"      # 没 id 的兜底:照样算一次调用
            calls[cid] = {"name": name, "args": _args(call),
                          "ts": str(m.get("ts") or "")}
            batch_ids.append(cid)
            order.append(cid)
        # W9:同批 = 模型同一条 assistant 消息里**同时要下**的几步(≥2 才算批,
        # 单发不标)。执行是逐个的 —— 标记只说"同批下发",绝不说"并行执行"。
        if len(batch_ids) > 1:
            batch_n += 1
            bid = f"b{batch_n}"
            for cid in batch_ids:
                calls[cid]["batch"] = bid
    results: dict[str, str] = {}
    result_ts: dict[str, str] = {}
    result_ms: dict[str, int] = {}
    for m in messages or []:
        if isinstance(m, dict) and m.get("role") == "tool":
            cid = str(m.get("tool_call_id") or "")
            body = m.get("content")
            if cid and isinstance(body, str):
                results[cid] = body
                result_ts[cid] = str(m.get("ts") or "")
                # W10:留档带了实测毫秒(handler 全程)就收下 —— 时间线还原
                # 时它优先于 ts 秒级差(0.2s 的调用会漂成 0s/1s/2s)。
                ms = m.get("elapsed_ms")
                if isinstance(ms, int) and not isinstance(ms, bool) and ms >= 0:
                    result_ms[cid] = ms
                # H2:钩子的实测毫秒挂在**这次调用**上(它是这一步的属性,
                # 不是又要一份按 cid 平行的表 —— 少一份平行表就少一次对不上)。
                hm = m.get("hook_ms")
                if (cid in calls and isinstance(hm, int)
                        and not isinstance(hm, bool) and hm > 0):
                    calls[cid]["hook_ms"] = hm
    return calls, results, order, result_ts, result_ms


def _extract_subagents(calls, results, result_ts=None) -> list[dict]:
    items = []
    for cid, call in calls.items():
        if call["name"] != "task":
            continue
        types = [str(t) for t in _as_list(call["args"].get("type"))]
        tasks = [str(t) for t in _as_list(call["args"].get("task"))]
        n = max(len(types), len(tasks)) or 1
        result = results.get(cid)
        # 耗时:发起那条 assistant 消息的 ts → 结果落档那条 tool 消息的 ts。
        # 口径与时间线那一条一致(不含模型思考的间隙),老留档没 ts 就**不给时间**。
        secs = None
        start = _ts_epoch(call.get("ts") or "")
        end = _ts_epoch((result_ts or {}).get(cid) or "")
        if start is not None and end is not None:
            secs = max(0, int(round(end - start)))
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
            bits = [ttype or "子代理"]
            if detail:
                # 一次调用派了 N 路时,回来的只有**一份合并报告**:每行都写"报告 N 字"
                # 等于把同一份字数念 N 遍。标成"合并"二字,不假装各算各的。
                bits.append(("合并报告 " if n > 1 else "报告 ") + f"{len(result)} 字")
            if secs is not None:
                bits.append(f"用时 {secs}s")
            files = (len(_FILE_TOOLS_LINE.findall(result))
                     if (result and n == 1) else None)
            if files:
                bits.append(f"产出 {files} 处文件")
            items.append({
                "key": f"sa:{cid}:{i}",
                "title": _clip(task) or f"{ttype} 子代理",
                "sub": " · ".join(bits),
                "type": ttype, "secs": secs,
                "files": files,
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
# W7:一轮思考**拼接后**的总截断(各步在留档时已各自截过;拼起来还可能超)。
REASONING_JOIN_CLIP = 6000


def _turn_split(messages) -> list[dict]:
    """按用户发言把上下文切成轮次,返回 [{"n", "title", "cids": [调用 id…]}]。

    只认 `role=user` 这条真实边界 —— 一次"帮我改课表"到学长说完话就是一轮。
    没有用户发言(比如刚恢复的半截上下文)也照样归到第 1 轮,不丢调用。
    """
    turns: list[dict] = []

    def open_turn(title: str = "") -> dict:
        t = {"n": len(turns) + 1, "title": title, "cids": [],
             "reasoning": [], "reasoning_chars": 0}
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
            # W7:这一轮各步的思考摘要。字数优先取留档里的**真总字数**
            # (reasoning_chars);老留档没有这个键就按存的截断文本数 ——
            # 文本自带「截断,原 N 字」标记,不会冒充完整。
            r = m.get("reasoning")
            if isinstance(r, str) and r.strip():
                cur["reasoning"].append(r)
                cur["reasoning_chars"] += int(m.get("reasoning_chars") or len(r))
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


def _children(kids) -> list[dict]:
    """挂在父行下面的子步骤(并行子代理内部每一步)转成时间线要的形状。

    这些行**不来自会话消息** —— 子代理的 tool_calls 留在它自己的 session 里,
    主会话只有一条 `task`。所以它们:①不计进轮次抬头那句「执行工具 N 次」
    (那个数永远等于消息里的调用数,掺进子步骤就成了两个真相);②时刻只能
    来自实时表,重启后旧留档里没有,就不显示时间。
    """
    out: list[dict] = []
    for k in kids or []:
        if not isinstance(k, dict):
            continue
        name = str(k.get("name") or "")
        if k.get("end") is None:
            status = "running"
        elif k.get("ok"):
            status = "ok"
        elif k.get("abandoned"):
            status = "stale"
        else:
            status = "failed"
        summary = str(k.get("summary") or "")
        out.append({"tool": name, "verb": toolsum.verb(name),
                    "route": str(k.get("route") or ""),
                    "title": _clip(summary) or toolsum.verb(name),
                    "status": status, "badge": _badge(status),
                    "time": _clock(k.get("start")), "dur": _dur(k),
                    "dur_text": _dur_text(k),
                    "progress": str(k.get("progress") or "")})
    return out


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
            # 这一步下面挂着的子步骤(并行批次里子代理内部的每一步)跟着时刻一起搬过来
            row["children"] = _children(cand.get("children"))
            row["children_dropped"] = int(cand.get("children_dropped") or 0)
            j = k + 1
            matched += 1
            break
    return matched


def _dur(entry: dict):
    start, end = entry.get("start"), entry.get("end")
    if start is None or end is None:
        return None
    return max(0, int(round(float(end) - float(start))))


def _dur_text(entry: dict) -> str:
    """「用时 Ns」里那个字符串:格式化只这一处(时间线的行与子行同一份)。

    W10 起优先读 `entry["dur"]`(实测毫秒折的秒 / 实时表的真实间隔)——
    它比 start/end 的 ts 差准(消息 ts 只有秒级,0.2s 的调用会漂成 0s/1s/2s);
    dur 缺席时才退回 start/end 重算(老行为,时刻锚还在就照样能算)。
    """
    d = entry.get("dur")
    if d is None:
        d = _dur(entry)
    if d is None:
        return ""        # 既没实测也没时刻锚:这一格不显示,更不拿钩子那截凑一个数
    hm = entry.get("hook_ms")
    if isinstance(hm, int) and not isinstance(hm, bool) and hm > 0:
        # 钩子耗时**单列一截**,不并进 d:那一格问的是"这一步跑了多久"
        # (handler 全程,连确认框等待都不算),钩子跑在它之外 —— 折进去等于
        # 当场改掉那把尺的定义,而这栏的全部本钱就是尺子说得准。
        return f"{d}s(钩子 {hm / 1000:.1f}s)"
    return f"{d}s"


def _ts_epoch(ts: str) -> "float | None":
    """消息时间戳("YYYY-MM-DD HH:MM:SS")→ epoch;解析不了回 None。"""
    import time as _time
    try:
        return _time.mktime(_time.strptime(str(ts or "")[:19],
                                           "%Y-%m-%d %H:%M:%S"))
    except (ValueError, TypeError):
        return None


def _restore_from_ts(rows: list[dict], calls: dict, result_ts: dict,
                     result_ms: dict | None = None) -> int:
    """实时表没配到的行,用 v0.9.66 起的消息时间戳还原时刻与耗时。

    诚实边界:①只填 `start/end/dur` 为**空**的行 —— 实时表的时刻永远优先
    (它在调用那一刻记的,精度到秒;消息 ts 也是那一刻,但两者并存时没必要
    重新计算);②**老留档没有 ts,照旧不给时间** —— 宁可空着,不猜;
    ③dur 是"发起调用 → 结果落档"的真实间隔,不含模型思考的间隙,口径与
    实时表一致(实时表也是 start=tool_start/end=tool_done)。
    W10 起第④条:**实测毫秒优先** —— 留档消息带了 elapsed_ms(handler 全程
    实测)就用它折秒,消息 ts 的秒级取整漂移(0.2s 的调用会漂成 0s/1s/2s)
    从此不进时间线;没有 elapsed_ms 的老留档照旧走 ts 差,两档并存时实测
    永远在 ts 猜测前面。
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
        ms = (result_ms or {}).get(row.get("cid"))
        if isinstance(ms, int) and not isinstance(ms, bool) and ms >= 0:
            # W10:实测毫秒折秒(半秒进位)—— 真实测过 2900ms 就是 3s,
            # 不吃 ts 取整的漂移;end 仍取 ts(那是唯一知道的结束点)。
            row["dur"] = max(0, (ms + 500) // 1000)
        elif row.get("end") is not None:
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


def _group_member(r: dict) -> dict:
    """归并行的子行:原始那一次调用,带着**它自己**的时刻与状态。"""
    return {"tool": r["tool"], "verb": r["verb"], "title": r["summary"],
            "status": r["status"], "badge": r["badge"],
            "time": r.get("time") or "", "dur": r.get("dur"),
            "dur_text": r.get("dur_text") or "", "progress": "",
            # W4「定位现场」按 key 找行:被归并走的原始调用在 children 里
            # 照样带 key,前端逐行点亮不因归类而失联。
            "key": f"t:{r['cid']}" if r.get("cid") else ""}


def _group_rows(items: list[dict]) -> list[dict]:
    """W8 归类:同一轮里**可证明相同**的调用(工具名+规范化参数全等)合成
    一行 ×N;原始调用逐条挂在 children 下 —— 展开仍是那几次,一次不少。

    四条口径:①不同参数绝不合并(换参数重试是健康的思路调整,叠一起等于
    把调整抹掉);②合并行显示**最后一次**的状态与时刻(与「产出」栏
    "状态跟最新"同一条规矩),未命中标记同理跟最新;③单发不标 ×N,照旧
    直行,children 保持原样(可能是配到的子代理步骤,也可能是空)——
    **自己不当自己的子行**;④×N 的 children = 原有子行在前 + 全部原始调用
    在后,一次不少。
    """
    out: list[dict] = []
    index: dict[str, dict] = {}
    for r in items:
        sig = call_signature(r["tool"], r.get("args") or {})
        head = index.get(sig)
        if head is None:
            row = dict(r)
            row["n"] = 1
            row["_members"] = [r]          # 临时:同款的全部原始调用(含首秀)
            index[sig] = row
            out.append(row)
        else:
            head["n"] += 1
            head["_members"].append(r)
            head["status"] = r["status"]           # 状态跟最新一次
            head["badge"] = r["badge"]
            head["start"] = r.get("start")
            head["end"] = r.get("end")
            head["dur"] = r.get("dur")
            # 原文/参数也跟最新一次:点开"看这一步的原文"看到的
            # 必须是状态所指向的那一次,不是最早那次的残影。
            head["result"] = r.get("result")
            head["args"] = r.get("args")
            head["batch"] = r.get("batch") or head.get("batch")
            if r.get("repeat_text"):
                head["repeat_text"] = r["repeat_text"]   # 未命中标记跟最新
    for row in out:
        n = row.pop("n")
        members = row.pop("_members")
        if n > 1:
            # ×N:原有子行(同批 task 配到的子代理步骤)在前,原始调用在后
            # —— 归并不吃掉任何一步,展开仍是那几次。
            row["children"] = (list(row.get("children") or [])
                               + [_group_member(m) for m in members])
            row["group_n"] = n
    return out


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
    calls, results, _order, result_ts, result_ms = _call_index(messages)
    rows: list[dict] = []
    reason_by_turn: dict[int, dict] = {}
    for turn in _turn_split(messages):
        # W7:这一轮的思考记录(各步拼接,再整体截断;字数是**真总数**)。
        if turn.get("reasoning"):
            reason_by_turn[turn["n"]] = {
                "chars": turn["reasoning_chars"],
                "text": _clip("\n\n".join(turn["reasoning"]),
                              REASONING_JOIN_CLIP)}
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
                         # H2:这一步的钩子实测毫秒(没跑钩子就没有这个键,前端不显示)
                         "hook_ms": call.get("hook_ms"),
                         # W9:同批下发的批号(同一条 assistant ≥2 调用才有)
                         "batch": call.get("batch"),
                         # 并行子代理内部的每一步挂在下面(v0.9.92);形状先给齐,
                         # 前端就不用为"这一行到底有没有 children"再判一次
                         "children": [], "children_dropped": 0,
                         "result": result if isinstance(result, str) else ""})
    _pair_times(rows, live)
    _restore_from_ts(rows, calls, result_ts, result_ms)
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
        for r in items:
            # 时刻只可能是空串:实时表没记到的行(并行子代理内部、上一版程序跑的)
            # 就不显示时间。宁可少一列,不许拿"现在"凑一个。
            r["time"] = _clock(r.get("start"))
            r["dur_text"] = _dur_text(r)
            hits = empty_marks.get(r.get("cid"))
            if hits and hits >= mark_from:
                r["repeat_text"] = f"×{hits} 未命中"
        # W8:归类只叠**可证明相同**的调用 —— 先补齐时刻(子行要各自带着自己的
        # 时刻走),再合并;真总数(calls/errors)从合并前的 items 数出来,
        # 合并只动显示,绝不动口径。
        groups = _group_rows(items)
        # 轮与轮之间最新在上;**轮内按先后顺序读下来** —— 时间线存在的理由就是
        # "它先做了什么、后做了什么",轮内再倒过来就没人能顺着看了。
        # 截断保留这一轮最近的 TURN_LIMIT 行(少列行,不少报次数)。
        shown = groups[-TURN_LIMIT:]
        for r in shown:
            r["action"] = _timeline_action(r, r["result"])
            # 显示行(合并行在内)一律要净:args/result 只给 action 用,
            # 算完就剥 —— 不剥的话页面原文会整段躺进面板载荷(隐私和体积
            # 都是账,来源栏那次的教训)。合并行是副本,必须在这里单独剥。
            for k in ("args", "result", "cid", "raw", "turn_title", "end", "dur",
                      "hook_ms"):
                r.pop(k, None)
        for r in items:
            for k in ("args", "result", "cid", "raw", "turn_title", "end", "dur",
                      "hook_ms"):
                r.pop(k, None)
        turns.append({"n": n, "title": title,
                      "calls": len(items), "errors": counts["failed"],
                      "running": counts["running"], "stale": counts["stale"],
                      "when": when,
                      "reasoning": reason_by_turn.get(n),
                      "items": shown,
                      "more": max(0, len(groups) - len(shown))})
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
            # 并行批次里这一步属于哪一路(live_now 会往下钻一层,所以这里可能是子步骤)
            "route": str(now.get("route") or ""),
            "parent_tool": str(now.get("parent_tool") or ""),
            "start": _clock(now.get("start")),
            # 长命令的实时那一行(shell 报的"已跑多久/多少字/最后一行")。没有就是空串,
            # 前端那一行整条不出现 —— 拿"没有进度"装作"刚开始"是最容易看漏的谎。
            "progress": str(now.get("progress") or ""),
            "elapsed": max(0, int(now.get("elapsed") or 0))}


# ------------------------------------------------- 重复失败探测(W4)

FAIL_CHAIN_MIN = 3      # 同一调用连续失败几次立一张卡(与 agent.FAIL_STREAK_LIMIT 同节奏)
FINDINGS_LIMIT = 8      # 面板最多几张卡;超出是病态场景(止损闸每轮 3 次就会收尾)
CHAIN_KEEP = 10         # 一条链最多记几个调用 id(定位现场锚的是这几个)
FINDING_PREVIEW = 160   # 卡片上那段错误原文截多长


def call_signature(name: str, args) -> str:
    """「同一次调用」的钥匙 —— 与 agent._fail_streak_hit 同一条公式。

    那边是止损闸(连续 3 次同键失败就收尾),这边是显示器。同一件事两处
    各写一份"同样"的定义,迟早漂成「闸停了卡没立」或反过来 —— 所以这里
    不另写一份,参数规范化用同一个 `json.dumps(sort_keys=True)`。
    """
    try:
        flat = json.dumps(args if isinstance(args, dict) else {},
                          sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        flat = str(args)
    return f"{str(name or '')}|{flat}"


def _hash8(text: str) -> str:
    """卡片的稳定短 id:同一条链更新同一张卡,不按出现次序编号(0.5.0 的教训)。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def findings(messages) -> list[dict]:
    """重复失败探测:同一工具+同一组参数**连续**失败 ≥3 次,立一张可定位的卡。

    口径与 agent._fail_streak_hit 同源(失败问 toolsum.is_failure,"同一"=
    call_signature 那把钥匙,阈值同是 3)—— 止损闸停下的那一步,这里必须
    看得见,不然"它已经停了"和"面板上什么都没有"会同时成立。

    三条边界(每条都对应一种会骗人的画法):
    ①链**按轮计**:用户发了新话就是新链 —— 止损闸的失败连击只在本回合内
      计数(agent.py 同款口径),跨轮连着算就会出现"同一操作失败了 9 次"
      这种把两次独立尝试缝成一条的假链;
    ②没有回话的调用(在跑/已中断)**不计也不清**:它没有结果,既不构成
      失败也不构成成功,拿它断链等于拿"不知道"当"成功了";
    ③卡片的 id 锚在 (轮次, 钥匙) 上,链变长时**更新原卡**、count 往上走,
      而不是再立一张 —— 连续失败刷出一排重复卡,是最坏的一种刷屏。

    这是显示器不是判决书:卡片文案照实说"这是重复失败证据,不是确定的
    空转或质量判决" —— 换个参数重试是健康的思路调整,不立卡。
    """
    calls, results, order, _result_ts, _result_ms = _call_index(messages)
    turn_of: dict[str, int] = {}
    title_of: dict[int, str] = {}
    for t in _turn_split(messages):
        title_of[t["n"]] = t["title"]
        for cid in t["cids"]:
            turn_of[cid] = t["n"]
    out: list[dict] = []
    cur: dict | None = None            # 当前链:{"turn", "key", "tool", "args", ...}
    for cid in order:
        call = calls.get(cid) or {}
        result = results.get(cid)
        if result is None:
            continue                    # ② 还在跑/没回话:链保持原样
        if not _failed(result):
            cur = None                  # 成功重置(agent 闸同款)
            continue
        turn = turn_of.get(cid)
        key = call_signature(call.get("name", ""), call.get("args") or {})
        if cur is not None and cur["key"] == key and cur["turn"] == turn:
            cur["count"] += 1
            cur["cids"].append(cid)
        else:
            # ① 新链:换钥匙、换轮次都算 —— 跨轮的旧失败不缝进新链
            cur = {"turn": turn, "key": key, "tool": str(call.get("name") or ""),
                   "args": call.get("args") or {}, "count": 1, "cids": [cid]}
        cur["cids"] = cur["cids"][-CHAIN_KEEP:]
        cur["preview"] = result
        if cur["count"] < FAIL_CHAIN_MIN:
            continue
        fid = f"rf:{cur['turn']}:{_hash8(key)}"
        name = cur["tool"]
        row = {
            "id": fid, "turn": cur["turn"],
            "turn_title": title_of.get(cur["turn"], ""),
            "tool": name, "verb": toolsum.verb(name),
            "title": _clip(toolsum.summarize(name, cur["args"])) or toolsum.verb(name),
            "count": cur["count"],
            "cids": list(cur["cids"]),
            "preview": _clip(cur["preview"], FINDING_PREVIEW),
            # 「定位现场」(对齐 dsh-watcher 0.5.0):点卡片滚到那几步,不是
            # 滚到轮次标题就完事 —— 锚的是具体调用 id,前端按 key 对行。
            "action": {"type": "locate", "label": "定位现场",
                       "turn": cur["turn"], "cids": list(cur["cids"])},
        }
        old = next((r for r in out if r["id"] == fid), None)
        if old is not None:
            old.update(row)             # ③ 同一条链:更新原卡
        else:
            out.append(row)
    out.reverse()                       # 最新在上(与面板其余各行同序)
    return out[:FINDINGS_LIMIT]


# ------------------------------------------------------------------ 对外入口

def session_activity(messages, workspace=None, live=None, now=None, busy=True,
                     group_limit=None, retries: int = 0) -> dict:
    """把会话消息聚合成右侧面板要的那份字典(纯函数,不碰磁盘以外的东西)。

    `busy` = 这个会话此刻还在不在跑。它决定"没有回话的那一步"算进行中还是算
    已中断:用户按了停止之后,`tool_done` 不会再来到,消息里那条调用就永远没有
    回话 —— 这时还写"写入中"就是谎话。

    W1/W4 起多带两样:`retries` = 模型层重试事件数(桥从 UI 计数器递进来的
    会话级账,这里只转发);`findings` = 重复失败探测卡(见 `findings`)。
    """
    calls, results, order, result_ts, _result_ms = _call_index(messages)
    groups = {
        "subagents": _extract_subagents(calls, results, result_ts),
        "files": _extract_files(calls, results, workspace),
        "web": _extract_web(calls, results),
        "sources": _extract_sources(calls, results),
        "memory": _extract_memory(calls, results),
    }
    # 抽取器都按调用顺序 append,所以倒过来就是"最新在上"(合并过行的类别以最后一次
    # 改动为准,同样落在末尾)
    # 每类默认列 12 行(`GROUP_LIMIT`);界面点了「展开全部」时传 0 = 不截。
    # 为什么允许 0:截断提示要能变成"点一下就看完"的动作,否则那句「另有 N 条」
    # 是一句只能看不能办的死话 —— 而这一栏每一行都该点得动是它存在的理由。
    cap = GROUP_LIMIT if group_limit is None else max(0, int(group_limit))
    out, total = [], 0
    for key, label, icon in GROUPS:
        rows = list(reversed(groups[key]))
        if not busy:
            for r in rows:
                if r.get("status") == "running":
                    r["status"], r["badge"] = "stale", "已中断"
        total += len(rows)
        out.append({"key": key, "label": label, "icon": icon,
                    "count": len(rows), "items": rows[:cap or None],
                    "more": max(0, len(rows) - cap) if cap else 0})
    errors = sum(1 for cid in order if cid in results and _failed(results[cid]))
    silent = [cid for cid in order if cid not in results]
    running = len(silent) if busy else 0
    interrupted = 0 if busy else len(silent)
    # 时间线与上面五类同源同一份消息:两处各数一次就会出现"卡片说 12 次、
    # 时间线只有 9 行"那种自相矛盾。live 没传(终端/网页版)时行照排,只是没时间。
    line = timeline(messages, workspace=workspace, live=live, busy=busy)
    return {"ok": True, "groups": out,
            "timeline": line, "now": now_line(now) if busy else None,
            # W1:模型层重试的事件数(会话级,本程序这次开着期间的账)。
            # 由桥从 UI 计数器递进来;这里只转发,不另造第二个来源。
            "findings": findings(messages),
            # 「统计于 HH:MM:SS」的那个数:是**这次算**的时刻,不是动作发生的时刻
            "updated_at": time.strftime("%H:%M:%S", time.localtime()),
            "summary": {"calls": len(order), "errors": errors, "running": running,
                        "interrupted": interrupted,
                        "retries": max(0, int(retries or 0)),
                        "busy": bool(busy),
                        "workspace": _rel(workspace, None) if workspace else ""},
            # 前端拿它排"哪一类该显示";空类直接不渲染,不摆一屏"暂无"
            "empty": total == 0 and not line["calls"]}
