"""下载本地向量模型的权重 —— 权重不进包,要用时才下(v0.9.39)。

为什么要有这个脚本:`minicode/embed.py` 的 local 后端只认
`<数据目录>/models/<模型名>/model.onnx` + `vocab.txt` 两个文件,缺了就在检索
结果里如实写"本地模型缺文件"。要是那句提示点名的下载脚本本身不存在,提示就成了
装饰 —— 所以这里把那条路真的做出来。

三条口径(动这个文件前先读):

1. **没有默认的仓库名**。某个模型仓库在不在、叫什么,是会变的第三方事实;本机
   连 huggingface 的检索 API 都没打通,更没有核实过任何一个仓库 id,所以写死一个
   "看起来对"的仓库等于替用户赌它存在。必须用 `--repo` 明确给出。
2. **拿到多少字节就说多少字节**,并且打印每个文件的 sha256。校验和只有在你用
   `--sha256` 钉过之后才叫"验过";没钉时它只是"这次拿到的这段字节的指纹",
   供你下次钉 —— 绝不显示成"校验通过"。
3. **只往 `models/` 下写,且先写 `.part` 再改名**。半个文件不会被 detect() 当成
   "权重已就位"(那正是最坏的情况:装好了却每次都算错向量)。

用法::

    python scripts/fetch_embed_model.py --check
    python scripts/fetch_embed_model.py --repo <组织>/<仓库> [--model 起个目录名]
        [--files model.onnx vocab.txt] [--mirror] [--sha256 model.onnx=<64位十六进制>]

下载成功不等于向量档可用:local 后端还要装得上 `onnxruntime`(纯 Python 不带的
依赖),那一层缺不缺由 `embed.detect()` 自己说。
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_FILES = ("model.onnx", "vocab.txt")
MAIN_BASE = "https://huggingface.co"
MIRROR_BASE = "https://hf-mirror.com"
TIMEOUT_SECONDS = 30
CHUNK = 1 << 16


def url_for(base: str, repo: str, name: str) -> str:
    """HuggingFace 兼容的单文件直链(仓库 + resolve/main + 文件名)。"""
    return f"{base.rstrip('/')}/{repo.strip('/')}/resolve/main/{name.lstrip('/')}"


def default_model_name(repo: str) -> str:
    """目录名取仓库名的后半段(`组织/模型` -> `模型`),不另编一个。"""
    tail = repo.strip("/").rsplit("/", 1)[-1]
    safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in tail).strip("-")
    return safe or "local-embedding"


def check(dest_dir: Path, model: str, files=DEFAULT_FILES) -> list[str]:
    """返回缺的文件名列表(空 = 齐了)。"""
    d = dest_dir / model
    return [n for n in files if not (d / n).is_file()]


def fetch_one(url: str, target: Path, sha256: str = "", opener=urllib.request.urlopen) -> int:
    """下载一个文件到 target,返回落盘字节数;失败抛异常且不留完整文件。

    写临时名再 `os.replace`:中途断掉只会留下一个 `.part`,不会被当成权重。
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".part")
    digest = hashlib.sha256()
    got = 0
    try:
        with opener(url, timeout=TIMEOUT_SECONDS) as resp:
            code = getattr(resp, "status", 200)
            if code not in (200, None):
                raise RuntimeError(f"服务端返回 HTTP {code}")
            with tmp.open("wb") as fh:
                while True:
                    chunk = resp.read(CHUNK)
                    if not chunk:
                        break
                    fh.write(chunk)
                    digest.update(chunk)
                    got += len(chunk)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    if sha256 and digest.hexdigest().lower() != sha256.strip().lower():
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"校验和不符:期望 {sha256},实际 {digest.hexdigest()}")
    tmp.replace(target)
    return got


def fetch(repo: str, model: str, dest_dir: Path, base: str = MAIN_BASE,
          files=DEFAULT_FILES, sums: dict[str, str] | None = None,
          opener=urllib.request.urlopen) -> list[tuple[str, int, str]]:
    """逐个下载,返回 [(文件名, 字节数, sha256)];有一个拿不到就整体抛错。"""
    sums = sums or {}
    done: list[tuple[str, int, str]] = []
    for name in files:
        url = url_for(base, repo, name)
        target = dest_dir / model / name
        try:
            size = fetch_one(url, target, sums.get(name, ""), opener=opener)
        except (urllib.error.URLError, OSError, RuntimeError) as e:
            raise RuntimeError(f"{name}:没拿到({e};试过 {url})") from e
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        done.append((name, size, digest))
    return done


def parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="下载本地向量模型权重(权重不进包)")
    ap.add_argument("--repo", help="模型仓库 id,如 <组织>/<模型名>;--check 之外必填")
    ap.add_argument("--model", help="存进 models/ 下的目录名(默认取仓库名后半段)")
    ap.add_argument("--files", nargs="+", default=list(DEFAULT_FILES),
                    help=f"要下的文件名(默认 {' '.join(DEFAULT_FILES)})")
    ap.add_argument("--base", default=MAIN_BASE, help=f"直链前缀(默认 {MAIN_BASE})")
    ap.add_argument("--mirror", action="store_true", help=f"用国内镜像 {MIRROR_BASE}")
    ap.add_argument("--sha256", action="append", default=[], metavar="文件=十六进制",
                    help="钉住某个文件的校验和;不钉就只报告指纹,不判过与不过")
    ap.add_argument("--check", action="store_true", help="只列出缺哪些文件,不下载")
    ap.add_argument("--dest", help="覆盖 models/ 目录(测试用)")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    # 重定向到文件/管道时 Windows 按 cp936 建流,中文会变成一串乱码(2026-10-06 这个
    # 脚本自己的下载日志就是这么坏的)。与 check_frozen_version.py 同一手法。
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    args = parse_args(argv)
    base = MIRROR_BASE if args.mirror else args.base
    if args.dest:
        dest_dir = Path(args.dest)
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from minicode.embed import models_dir
        dest_dir = models_dir()
    sums: dict[str, str] = {}
    for item in args.sha256:
        k, _, v = item.partition("=")
        if not v:
            print(f"--sha256 要写成 文件=十六进制,收到的是「{item}」")
            return 2
        sums[k] = v

    if not args.repo:
        if args.check:
            have = sorted(p.name for p in dest_dir.glob("*") if p.is_dir()) \
                if dest_dir.is_dir() else []
            print(f"models/ 目录:{dest_dir}")
            print("已下过:" + ("、".join(have) if have else "一个都没有"))
            print("要下就给出 --repo <组织>/<仓库>(这里不替你猜仓库名)。")
            return 0 if have else 1
        print("缺 --repo:本脚本不预设任何模型仓库名(没核实过的第三方地址不写死)。"
              "\n用法:python scripts/fetch_embed_model.py --repo <组织>/<仓库> "
              "[--model 目录名] [--mirror] [--sha256 model.onnx=...]")
        return 2

    model = args.model or default_model_name(args.repo)
    if args.check:
        missing = check(dest_dir, model, args.files)
        print(f"{dest_dir / model}: " + ("缺 " + "、".join(missing) if missing else "文件齐了"))
        return 0 if not missing else 1

    print(f"往 {dest_dir / model} 下 {len(args.files)} 个文件(直链前缀 {base})")
    try:
        done = fetch(args.repo, model, dest_dir, base=base, files=args.files, sums=sums)
    except RuntimeError as e:
        print(f"没下成:{e}")
        return 1
    for name, size, digest in done:
        pinned = "已按你给的校验和核对" if name in sums else "未钉校验和(只报指纹)"
        print(f"  ✓ {name} {size:,} B · sha256 {digest[:16]}… · {pinned}")
    print("\n下一步:在 config.json 里写 "
          '"capabilities": {"embeddings": {"backend": "local", "model": "%s"}},'
          "并确认本机 import 得到 onnxruntime。" % model)
    print("向量索引**不会**自己建:第一次按语义检索时缺多少条就如实报多少条并停下,"
          "要建就明确说一声(那是几百次向量计算,花的是使用者的时间与额度)。"
          "建好落在数据目录 kb-vectors/(不进包);换池化或换截断宽度会自动另起一套缓存。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
