"""把知识库里"爬回来的但被标成 A 级"的条目降级(注入防御 M4 的存量清理)。

背景:以前 `web_crawl(to_kb=True)` 无条件给条目标 `credibility: "A"`,等于把任何
爬来的页面当权威资料;一条投毒页面入库后会被之后每次 search_knowledge 召回。
入库规则已在 `minicode/tools/crawl.py` + `minicode/kb.py` 修好(只有学校官网白名单
域名能标 A),本脚本负责清理**已经躺在 kb/ 里的旧数据**。

用法::

    py -3.12 scripts/fix_kb_credibility.py              # 只看会改什么(dry-run)
    py -3.12 scripts/fix_kb_credibility.py --apply      # 真的改(原件备份到 kb/history/)
    py -3.12 scripts/fix_kb_credibility.py --apply --all      # 连人工整理的文件一起扫
    py -3.12 scripts/fix_kb_credibility.py --apply --include wx-gzasc.json

默认只处理 `crawl-*.json` —— 那是 `web_crawl` 自己写出来的文件。其余知识库文件
(公众号整理稿、馆藏信息等)是人工审过的,标 A 有它的道理,要动请显式指定。

幂等:降级后的条目再跑一遍不会继续变化(重复备份由文件名里的时间戳隔开)。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from minicode.kb import CRAWLED_UNVERIFIED, host_is_trusted  # noqa: E402


def _host_of(url: str) -> str:
    text = str(url or "").strip()
    if "://" in text:
        text = text.split("://", 1)[1]
    return text.split("/", 1)[0].split("?", 1)[0].split("@")[-1].split(":")[0].lower()


def plan_for_file(path: Path, *, is_crawl_file: bool) -> tuple[list[dict], int]:
    """返回 (降级后的条目列表, 被降级的条数)。不合规的文件原样返回。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"  跳过 {path.name}: 读不了({e})")
        return [], 0
    if not isinstance(data, list):
        print(f"  跳过 {path.name}: 顶层不是列表")
        return [], 0

    changed = 0
    out = []
    for entry in data:
        if not isinstance(entry, dict):
            out.append(entry)
            continue
        cred = str(entry.get("credibility") or "")
        host = _host_of(str(entry.get("url") or ""))
        needs_down = False
        if cred.strip().upper().startswith("A"):
            if host and not host_is_trusted(host):
                needs_down = True                       # A 级但来源不在白名单
            elif not host and is_crawl_file:
                needs_down = True                       # 爬取文件却没有来源网址
        if needs_down:
            entry = dict(entry)
            entry["credibility"] = CRAWLED_UNVERIFIED
            note = str(entry.get("note") or "").strip()
            entry["note"] = (note + "; " if note else "") + \
                "v0.9.18 存量清理:抓取来源不在学校官网白名单,可信度由 A 降级"
            changed += 1
        out.append(entry)
    return out, changed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="知识库凭据级存量清理(注入防御 M4)")
    ap.add_argument("--kb-dir", default="kb", help="知识库目录,默认 kb/")
    ap.add_argument("--apply", action="store_true", help="真的写盘(默认只 dry-run)")
    ap.add_argument("--all", action="store_true",
                    help="扫描目录下所有 *.json(默认只扫 crawl-*.json)")
    ap.add_argument("--include", action="append", default=[],
                    help="额外指定文件名,可重复")
    args = ap.parse_args(argv)

    kb_dir = Path(args.kb_dir)
    if not kb_dir.is_dir():
        print(f"找不到知识库目录:{kb_dir}")
        return 1

    names = set(args.include)
    files = []
    for p in sorted(kb_dir.glob("*.json")):
        is_crawl = p.name.startswith("crawl-")
        if is_crawl or args.all or p.name in names:
            files.append((p, is_crawl))
    if not files:
        print("没有需要检查的文件(crawl-*.json 不存在;要全扫请加 --all)")
        return 0

    total = 0
    touched = []
    history = kb_dir / "history"
    for path, is_crawl in files:
        entries, changed = plan_for_file(path, is_crawl_file=is_crawl)
        tag = "抓取" if is_crawl else "人工"
        print(f"  [{tag}] {path.name}: {changed} 条待降级")
        if not changed:
            continue
        total += changed
        touched.append(path)
        if not args.apply:
            continue
        history.mkdir(exist_ok=True)
        backup = history / f"{time.strftime('%Y%m%d-%H%M%S')}-{path.name}"
        backup.write_bytes(path.read_bytes())        # 备份原件(与 Dream 整理同惯例)
        path.write_text(json.dumps(entries, ensure_ascii=False, indent=1),
                        encoding="utf-8")
        print(f"      已重写,原件备份到 {backup.relative_to(kb_dir.parent)}")

    if args.apply:
        print(f"\n完成:降级 {total} 条,改动 {len(touched)} 个文件。")
    else:
        print(f"\ndry-run:共 {total} 条会被降级(加 --apply 才写盘)。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
