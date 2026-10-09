"""出站网址策略 · N1 —— 模型能点开的地址,由这台机器的主人说了算。

**口径先写在最前面,因为这一层最坏的坏法是"界面上说挡住了,其实没挡":这是策略,
不是沙箱。**它只看得住从学长自己那几条 web 通路出去的请求。下面这些它**管不着**,
文档、确认框与回话里都不许说成管得着:

- `bash` / `py_script` 里的 `curl`、`Invoke-WebRequest`,以及任何程序自己发的包;
- 你在配置里填的模型 API 端点、MCP 服务器、天气与通知抓取源 —— 那些是**你选的源**,
  拦掉等于把产品关掉;
- 浏览器里已经打开的页面**自己**发起的子请求(图片、脚本、XHR);
- `web_search` 的搜索词(它出机器的是那句话,不是某个网址 —— 那是另一件事,
  要做的是"查询里带不带学生姓名"那一类提醒)。

真要"这台机器只能连这几个域",那是操作系统层的事(S5c AppContainer 或防火墙规则),
本项目到今天没做,欠账登记在 HANDOFF §6 第 52 条。这一层的价值是把**模型能自己
决定去访问哪个站**这一条路收住 —— 网页正文里的注入指令正是走这条路出去的。

`web_crawl` 的 BFS 只在**同域**内走(`_crawlable` 里 `p.netloc != host` 直接丢),
所以种子那一页问过/放过,整场爬取就在同一本账里 —— 不需要逐页再问,也不留一条
"种子在名单里、子页跑到别处"的缝。

三档语义(`config.json` 里 `net_policy.mode`):

| mode | 不在 allow 名单里的域 | deny 名单里的域 |
|---|---|---|
| `off`(默认) | 逐字回到今天:不问不拒 | 同左 |
| `ask` | **问一次**(每次,不因档位而免问) | 直接拒 |
| `deny` | 直接拒 | 直接拒 |

`allow` / `deny` 写的是主机名,支持 `*.example.com` 后缀;后缀匹配**必须**在边界处
有个点,所以 `evil-gzasc.edu.cn` 命不中 `*.gzasc.edu.cn` —— 这一条不是吹毛求疵,
是这类名单唯一真正要防的那种写法。
"""
from __future__ import annotations

from urllib.parse import urlsplit

ALLOW, ASK, REJECT = "allow", "ask", "reject"

# 模型能给 URL 的那几只工具:名字 → 装 URL 的参数名。
# 刻意不含 `mcp`(加服务器是用户自己的动作)、不含 `get_weather`/`campus_notices`
# (固定源,属于"你选的源"那一类)、不含 `web_search`(出机器的是查询词)。
# `page_parse` 的参数叫 `url_like`(它是一句"像 URL 的提示",不是规范 URL)——
# 写错这个键名,这一格就永远是 ALLOW 而没人会发现,所以它和工具签名一起有针钉着。
URL_TOOLS = {"web_crawl": "url", "page_parse": "url_like", "browser_open": "url"}


def host_of(url: str) -> str:
    """取主机名(小写、去端口)。取不出来就回空串 —— 空串不匹配任何名单。

    没有协议前缀的那种(`page_parse` 的 `url_like` 常是这个形状)按 `//host/path`
    再解一次:不解这一步,`jwxt.gzasc.edu.cn/kb` 的 host 会是空串,于是策略在
    最容易漏的那一类输入上静默失效。
    """
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
        netloc = parts.netloc
        if not netloc and not parts.scheme:
            netloc = urlsplit("//" + raw).netloc
    except ValueError:
        return ""
    host = netloc.rsplit("@", 1)[-1]          # 防 `http://evil.com#https://good.cn/`
    host = host.split(":")[0].strip().lower()
    # 带空白的不是主机名(`urlsplit("//not a url")` 会把整串当 netloc)。
    # 让它当主机名过,等于把"随手打的一句话"变成一个能进名单比对的域。
    return "" if any(c.isspace() for c in host) else host


def _match(host: str, pattern: str) -> bool:
    """`*.example.com` 匹配 example.com 与 a.example.com,不匹配 notexample.com。"""
    host = str(host or "").strip().lower()
    pattern = str(pattern or "").strip().lower()
    if not host or not pattern:
        return False
    if pattern.startswith("*."):
        bare = pattern[2:]
        return host == bare or host.endswith("." + bare)
    if pattern.startswith("*"):               # 裸 `*` 与 `*x.com` 里那种"任意前缀"
        return host.endswith(pattern[1:]) if pattern[1:] else True
    return host == pattern


def _any(host: str, patterns) -> bool:
    return any(_match(host, p) for p in (patterns or []))


def policy(config) -> dict:
    raw = getattr(config, "net_policy", None)
    return raw if isinstance(raw, dict) else {}


def decide(config, url: str) -> tuple[str, str]:
    """这一个网址该 放行 / 问一次 / 拒掉。返回 (结论, 主机名)。"""
    pol = policy(config)
    mode = str(pol.get("mode") or "off").strip().lower()
    host = host_of(url)
    if mode not in ("ask", "deny"):
        return ALLOW, host                    # off:一个字都不改今天的形状
    if _any(host, pol.get("deny")):
        return REJECT, host
    if _any(host, pol.get("allow")):
        return ALLOW, host
    return (ASK if mode == "ask" else REJECT), host


def gate(config, tool_name: str, args: dict) -> tuple[str, str]:
    """给权限门用的一层:只认那几只"模型能填 URL"的工具,其余一律 ALLOW。"""
    key = URL_TOOLS.get(str(tool_name or "").rsplit("·", 1)[-1])
    if key is None:
        return ALLOW, ""
    return decide(config, (args or {}).get(key, ""))


def note(config) -> str:
    """给人看的那一句:这一层现在是什么档、管什么、不管什么。"""
    mode = str(policy(config).get("mode") or "off").strip().lower()
    if mode not in ("ask", "deny"):
        return ""
    allow = policy(config).get("allow") or []
    return (f"出站策略:**{mode}** 档(允许名单 {len(allow)} 条)。"
            "它只管 `web_crawl`/`page_parse`/`browser_open` 这三条模型能填网址的路;"
            "`bash` 里的 curl、你配置的模型/MCP/天气/通知端点、以及页面自己的子请求"
            "都不在它范围内 —— 它是策略,不是沙箱。")
