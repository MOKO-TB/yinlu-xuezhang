"""学校校历的自动发现与读取(v0.9.190)。

为什么只做到"找到 + 读正文 + 列出日期候选",不直接写库:校历是一份**每年会变**的
官方文件(日期、调休、考试周都会动),自动解析成条目直接落库,错一天就是全校学生
按错的日子复习/放假 —— 这与「绝不编日期」是同一条红线。所以这一层只负责把事实
摊到眼前:找到最新那份校历通知、正文原样读回来、把**带日期的整行**列出来;
要不要录、录哪几条,由人和模型用 `calendar_edit` 的核对单决定。

两条边界:
- **只认学校域名**(`gzasc.edu.cn` 及其子域):工具允许传 URL,不限制就是 SSRF 入口;
  发现页也只扫学校自己的要闻列表。
- **不猜**:正文里"9月下旬"这种没有具体日子的行照原样列出,绝不替它编一个日期。

发现路径(2026-10-30 实测):校历发布在**学校要闻**频道(`xxyw.htm` 及其分页
`xxyw/<n>.htm`),列表项标题形如「2026年08月24日 广应科2026-2027学年校历,正式发布」。
分页编号不是页序(第 2 页是 `xxyw/76.htm`),所以这里**从列表页自己解析分页链接**,
不写死编号;校历可能在最近十几页里的任意一页,默认扫 12 页、够用即停。
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

BASE = "https://www.gzasc.edu.cn/"
LIST_PAGE = BASE + "xxyw.htm"
SCHOOL_DOMAIN = "gzasc.edu.cn"
MAX_PAGES = 20
DEFAULT_PAGES = 12
TEXT_LIMIT = 6000
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

# 候选行的判据:带"年/月日/周次/上中下旬"或"报到/开学/考试周/放假/寒暑假"这类词。
# 只用来**挑行给人看**,不从这里解析出结构化日期。
_CAND_RE = re.compile(
    r"\d{4}\s*年|\d{1,2}\s*月\s*\d{1,2}\s*日|第\s*\d+\s*[-–~至]?\s*\d*\s*周"
    r"|上旬|中旬|下旬|开学|报到|返校|考试周|放假|寒假|暑假|离校")
_DATE_PREFIX = re.compile(r"^\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日\s*")


class CalendarFetchError(ValueError):
    """没查成。message 要能直接给人看。"""


def check_school_url(url: str) -> tuple[bool, str]:
    """只认学校域名 —— 这只工具允许传 URL,不设这道闸就是 SSRF 入口。"""
    from urllib.parse import urlparse
    text = str(url or "").strip()
    if not text:
        return False, "没有给网址"
    p = urlparse(text)
    if p.scheme.lower() not in ("http", "https"):
        return False, "只认 http/https 的网址"
    host = (p.hostname or "").lower().rstrip(".")
    if host != SCHOOL_DOMAIN and not host.endswith("." + SCHOOL_DOMAIN):
        return False, f"只认学校官网域名({SCHOOL_DOMAIN});拿到的是 {host or '空'}"
    return True, ""


def _client(client=None):
    if client is not None:
        return client, False
    import httpx
    return httpx.Client(follow_redirects=True, timeout=15.0,
                        headers={"User-Agent": _UA}), True


def _get(client, url: str) -> str:
    resp = client.get(url)
    if resp.status_code != 200:
        raise CalendarFetchError(f"{url} 回了 HTTP {resp.status_code}")
    try:
        resp.encoding = resp.charset_encoding or "utf-8"
    except Exception:  # noqa: BLE001
        pass
    return resp.text


def _pagination_urls(html: str, pages: int = DEFAULT_PAGES) -> list[str]:
    """取"第 2 页起"的地址:这个 CMS 的分页编号是**递减序**(第 2 页是最大号)。

    实测:首页只露出 2/3/4/5 与"尾页"几个链接,而校历在第 6 页 —— 所以不能只收
    页面上的链接,要拿**最大编号**(=第 2 页)往下**逐个枚举**。链接暴露不全时,
    枚举是唯一能翻到第 6 页的办法;页面改版换了编号规则时,这里会扫到 404 并
    如实记进 errors,不会假装"没有校历"。
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    nums: list[int] = []
    for a in soup.find_all("a", href=True):
        t = a.get_text(" ", strip=True)
        if not t.isdigit():
            continue
        m = re.search(r"xxyw/(\d+)\.htm", a["href"])
        if m:
            nums.append(int(m.group(1)))
    if not nums:
        return []
    top = max(nums)
    out: list[str] = []
    for n in range(top, max(1, top - max(0, pages - 1)), -1):
        out.append(urljoin(BASE, "xxyw/%d.htm" % n))
    return out


def find_calendar_notices(*, pages: int = DEFAULT_PAGES, client=None) -> dict:
    """扫学校要闻最近 N 页,找标题里带「校历」的条目。回 {ok, items, scanned, why}。"""
    own = False
    try:
        client, own = _client(client)
        if own:
            client.__enter__()
        scanned, errors, items, seen = 0, [], [], set()
        try:
            first = _get(client, LIST_PAGE)
        except CalendarFetchError as e:
            return {"ok": False, "items": [], "scanned": 1, "why": str(e)}
        urls = [LIST_PAGE] + _pagination_urls(first, pages)[: max(0, pages - 1)]
        for url in urls:
            try:
                html = first if scanned == 0 else _get(client, url)
            except CalendarFetchError as e:
                errors.append(str(e))
                scanned += 1
                continue
            scanned += 1
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.find_all("a", href=True):
                title = a.get_text(" ", strip=True)
                if "校历" not in title:
                    continue
                href = urljoin(url, a["href"])
                if href in seen:
                    continue
                seen.add(href)
                clean = _DATE_PREFIX.sub("", title).strip()
                items.append({"title": clean or title, "raw_title": title,
                              "url": href, "page": url})
            if items:
                break                     # 找到就不翻了(新的总在靠前几页)
        if not scanned:
            return {"ok": False, "items": [], "scanned": 0,
                    "why": "一页都没打开:" + ";".join(errors[:2])}
        return {"ok": True, "items": items, "scanned": scanned,
                "why": ";".join(errors[:2])}
    finally:
        if own:
            client.__exit__(None, None, None)


def read_notice(url: str, *, client=None) -> dict:
    """读一篇校历通知:标题 + 正文 + 带日期的候选行(原样,不解析)。"""
    ok, why = check_school_url(url)
    if not ok:
        raise CalendarFetchError(why)
    own = False
    try:
        client, own = _client(client)
        if own:
            client.__enter__()
        html = _get(client, url)
    finally:
        if own:
            client.__exit__(None, None, None)

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.string or "").strip() if soup.title else ""
    box = (soup.select_one("div.v_news_content") or soup.select_one("#vsb_content")
           or soup.select_one("div.content") or soup.body)
    text = box.get_text("\n", strip=True) if box else ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    cands = [{"line": i + 1, "text": ln} for i, ln in enumerate(lines)
             if _CAND_RE.search(ln)]
    images = box.find_all("img") if box else []
    return {"ok": True, "title": title, "url": url,
            "text": "\n".join(lines)[:TEXT_LIMIT],
            "truncated": len("\n".join(lines)) > TEXT_LIMIT,
            "candidates": cands, "images": len(images)}


def calendar_fetch(ctx, action: str = "find", url: str = "",
                   pages: int = DEFAULT_PAGES) -> str:
    """工具出口:find 找校历通知;read 读正文与日期候选。"""
    act = str(action or "find").strip().lower()
    try:
        if act == "find":
            n = max(1, min(int(pages or DEFAULT_PAGES), MAX_PAGES))
            res = find_calendar_notices(pages=n)
            if not res.get("ok"):
                return f"没查成:{res.get('why') or '学校官网打不开'}"
            if not res["items"]:
                extra = f"(扫了 {res['scanned']} 页)" if res.get("scanned") else ""
                return (f"最近 {res['scanned']} 页学校要闻里没有标题带「校历」的通知{extra}。"
                        "可能是还没发布,或发布在别的频道 —— 把通知链接给我,我用 action=read 直接读。")
            lines = [f"找到 {len(res['items'])} 条校历通知(扫了 {res['scanned']} 页):"]
            for it in res["items"][:5]:
                lines.append(f"- {it['title']}\n  {it['url']}")
            lines.append("读正文用 action=read(不给 url 时读最新那条);"
                         "**读回来只当草稿** —— 核对后用 calendar_edit 的核对单录入,"
                         "校历没列的日期不要编。")
            return "\n".join(lines)
        if act == "read":
            target = str(url or "").strip()
            if not target:
                found = find_calendar_notices(pages=pages)
                if not found.get("ok") or not found["items"]:
                    return ("没有可读的校历通知:先 action=find 找到链接,"
                            "或直接把通知网址给我。")
                target = found["items"][0]["url"]
            got = read_notice(target)
            head = [f"校历通知:{got['title']}", f"来源:{got['url']}",
                    f"正文 {len(got['text'])} 字" +
                    (f"(已截断到 {TEXT_LIMIT})" if got["truncated"] else "")]
            body = ["--- 正文 ---", got["text"]]
            cand = ["--- 带日期的行(原样,未解析) ---"]
            if got["candidates"]:
                cand += [f"[{c['line']}] {c['text']}" for c in got["candidates"]]
            else:
                cand.append("(正文里没有找到带日期的行 —— 可能校历是图片版,"
                            "或正文结构变了;把图发我做 OCR,或让我按正文手工核对)")
            if got["images"]:
                cand.append(f"(正文里还有 {got['images']} 张图;文字版优先,"
                            "没有文字版时把图发我)")
            cand.append("注意:这些只是原文里的行,不是已经核对的条目:"
                        "录入请走 calendar_edit 的 preview → commit,"
                        "没有具体日子的(如「9月下旬」)不要编成某一天。")
            return "\n".join(head + body + cand)
        return f"action 只认 find / read,拿到的是「{action}」"
    except CalendarFetchError as e:
        return f"没查成:{e}"
    except Exception as e:  # noqa: BLE001 —— 网络栈深处的意外也要落到人话
        return f"没查成:{type(e).__name__}: {e}"
