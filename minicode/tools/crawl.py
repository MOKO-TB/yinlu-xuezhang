"""web_crawl 工具:基于 crawl4ai 抓取网页/整站为 markdown,可选写入本地知识库。

从起始 url 出发做同域 BFS 跟链(max_pages/max_depth 限流,页间 delay 限速,
跳过 pdf/图片等二进制链接),输出到 crawled/<域名>/ 下:每页一个带 frontmatter
的 .md,附 index.json。to_kb=True 时额外生成 kb/crawl-<域名>.json,条目格式与
kb.py 一致(credibility=A,附来源 url 供核实),可被 search_knowledge 直接检索。
crawl4ai 未安装时返回安装指引,不影响其他工具与单元测试(测试注入假 crawler)。
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from urllib.parse import urljoin, urlparse

from minicode.tools.base import (PathOutsideWorkspace, ToolContext,
                                 resolve_in_workspace)

SKIP_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
                   ".zip", ".rar", ".7z", ".jpg", ".jpeg", ".png", ".gif",
                   ".mp3", ".mp4", ".avi", ".exe", ".apk"}
KB_CONTENT_LIMIT = 2000      # 单条知识库内容截断,防 kb/*.json 膨胀
SUMMARY_TITLES_LIMIT = 20


def _md_text(markdown_obj) -> str:
    """crawl4ai 的 result.markdown 是对象(fit_markdown/raw_markdown),兜底转 str。"""
    if markdown_obj is None:
        return ""
    fit = getattr(markdown_obj, "fit_markdown", None)
    if fit:
        return str(fit)
    raw = getattr(markdown_obj, "raw_markdown", None)
    if raw:
        return str(raw)
    return str(markdown_obj)


def _slug(text: str, fallback: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff_-]+", "-", (text or "").strip()).strip("-")
    return (slug or fallback)[:60]


def _crawlable(url: str, host: str, match: str) -> bool:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or p.netloc != host:
        return False
    if p.path.lower().endswith(tuple(SKIP_EXTENSIONS)):
        return False
    if match and match not in url:
        return False
    return True


async def _crawl_site(start_url: str, *, max_pages: int, max_depth: int,
                      match: str, delay: float, site_dir, factory, run_cfg):
    """同域 BFS 抓取并落盘。返回 (pages, failed);pages 项含 content 全文。

    factory 返回支持 async with 的 crawler(真实 crawl4ai 或测试假对象),
    crawler.arun(url=..., config=...) 返回带 success/title/markdown/links 的结果。
    """
    site_dir.mkdir(parents=True, exist_ok=True)
    host = urlparse(start_url).netloc
    queue: list[tuple[str, int]] = [(start_url, 0)]
    visited = {start_url}
    pages: list[dict] = []
    failed: list[dict] = []
    async with factory() as crawler:
        while queue and len(pages) < max_pages:
            url, depth = queue.pop(0)
            try:
                result = await crawler.arun(url=url, config=run_cfg)
            except Exception as e:   # 网络/超时等异常:记录后继续下一页
                failed.append({"url": url, "error": f"{type(e).__name__}: {e}"[:200]})
                continue
            if not getattr(result, "success", False):
                failed.append({"url": url,
                               "error": (getattr(result, "error_message", "") or "抓取失败")[:200]})
                continue
            title = (getattr(result, "title", "") or url).strip()
            content = _md_text(getattr(result, "markdown", None))
            fname = f"{len(pages) + 1:03d}-{_slug(urlparse(url).path, 'index')}.md"
            stamp = time.strftime("%Y-%m-%d %H:%M")
            (site_dir / fname).write_text(
                f"---\nurl: {url}\ntitle: {title}\ncrawled_at: {stamp}\n---\n\n{content}\n",
                encoding="utf-8")
            pages.append({"url": url, "title": title, "file": fname, "content": content})
            if depth < max_depth:
                internal = (getattr(result, "links", None) or {}).get("internal", []) or []
                for link in internal:
                    href = link.get("href", "") if isinstance(link, dict) else ""
                    absolute = urljoin(url, (href or "").strip()).split("#", 1)[0]
                    if not absolute or absolute in visited:
                        continue
                    if not _crawlable(absolute, host, match):
                        continue
                    visited.add(absolute)
                    queue.append((absolute, depth + 1))
            await asyncio.sleep(delay)
    return pages, failed


def _write_kb(workspace, host: str, pages: list[dict], kb_source: str):
    """把抓取结果写成知识库条目(格式与 kb.py 的 KbEntry 对齐)。"""
    kb_dir = workspace / "kb"
    kb_dir.mkdir(exist_ok=True)
    path = kb_dir / f"crawl-{host}.json"
    entries = [{
        "id": f"crawl-{host}-{i:03d}",
        "title": p["title"],
        "content": p["content"][:KB_CONTENT_LIMIT],
        "url": p["url"],
        "source": kb_source or host,
        "tags": ["官网抓取", host],
        "credibility": "A",
    } for i, p in enumerate(pages, start=1)]
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def crawl_web(ctx: ToolContext, url: str, max_pages: int = 10, max_depth: int = 1,
              out_dir: str = "crawled", match: str = "", to_kb: bool = False,
              kb_source: str = "", delay: float = 1.0) -> str:
    """工具处理器:抓取 url 起的同域页面,落盘并返回摘要。"""
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return "错误: url 必须是带 http:// 或 https:// 的完整网址。"
    host = parsed.netloc
    max_pages = max(1, min(int(max_pages), 100))
    max_depth = max(0, min(int(max_depth), 5))
    delay = max(0.2, min(float(delay), 10.0))
    try:
        site_dir = resolve_in_workspace(ctx, f"{out_dir.strip('/')}/{host}")
    except PathOutsideWorkspace as e:
        return f"错误: {e}"
    try:
        from crawl4ai import (AsyncWebCrawler, BrowserConfig, CacheMode,
                              CrawlerRunConfig)
    except ImportError:
        return ("错误: 依赖 crawl4ai 未安装。请在项目环境执行:\n"
                "  pip install -U crawl4ai\n"
                "  crawl4ai-setup   # 首次需下载浏览器内核")
    browser_cfg = BrowserConfig(headless=True)
    run_cfg = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, page_timeout=30000,
                               verbose=False)

    def factory():
        return AsyncWebCrawler(config=browser_cfg)

    try:
        pages, failed = asyncio.run(_crawl_site(
            url, max_pages=max_pages, max_depth=max_depth, match=match.strip(),
            delay=delay, site_dir=site_dir, factory=factory, run_cfg=run_cfg))
    except Exception as e:
        return f"错误: 抓取过程异常({type(e).__name__}: {e}),未完成。"

    index = {"site": url, "crawled_at": time.strftime("%Y-%m-%d %H:%M"),
             "pages": [{"url": p["url"], "title": p["title"], "file": p["file"]}
                       for p in pages],
             "failed": failed}
    (site_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")

    lines = [f"爬取完成: 成功 {len(pages)} 页,失败 {len(failed)} 页。"
             f"输出目录 {out_dir.strip('/')}/{host}/(含 index.json 与每页 .md)。"]
    if to_kb and pages:
        kb_path = _write_kb(ctx.workspace, host, pages, kb_source.strip())
        lines.append(f"已写入知识库 kb/{kb_path.name}({len(pages)} 条,credibility=A),"
                     f"可用 search_knowledge 检索。")
    shown = pages[:SUMMARY_TITLES_LIMIT]
    lines.append("页面清单:")
    lines += [f"{i}. {p['title']}({p['file']},{len(p['content'])} 字)"
              for i, p in enumerate(shown, start=1)]
    if len(pages) > len(shown):
        lines.append(f"…其余 {len(pages) - len(shown)} 页见 index.json")
    if failed:
        lines.append("失败页面:")
        lines += [f"- {f['url']}: {f['error']}" for f in failed[:5]]
    return "\n".join(lines)
