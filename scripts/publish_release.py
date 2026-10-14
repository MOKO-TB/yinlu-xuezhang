"""发 GitHub Release 并把三件产物传上去(引路学长的发布动作,刻意不进包)。

用法:

    python scripts/publish_release.py v0.9.174 docs/release-notes/0.9.174.md

- `tag` 决定资产名(约定 `yinlu-xuezhang-<版本>-setup.exe` / `-win64.zip` /
  `-single-file.exe`),三件都从 `dist/` 里找,缺一件就停。
- 正文从第二个参数那份 markdown 读 —— **不在这里生成**:发布说明是给人读的,
  手写的那份就在仓库里,脚本只管传。
- 凭据:从 `git credential fill` 现取(`host=github.com`),不落盘、不回显。

**两处踩过的坑,写在这里免得下次再踩**:

1. **证书这关要试两次**。有些机器(加速器 / 公司代理 / 安全软件)把 HTTPS 接管了:
   根证书装进 **Windows 证书库**、不装进 Python 那份 `certifi`。表现是"浏览器上得去、
   python 一调就是 CERTIFICATE_VERIFY_FAILED"。所以先试默认,失败再用
   `ssl.create_default_context()`(Windows 上读系统证书库)。这与 `minicode/proactive.py`
   里那个 `_release_payload` 是**同一个坑的两处**,改一处别忘了另一处。
2. **上传大资产不要复用 `httpx.get` 那种一次性调用**:三件加起来 2.2 GB,得用
   `Client` + `content=<file object>` 流式发,并且 `timeout` 要单独放大(默认 5 秒
   会把大文件直接掐死在半路)。
"""
from __future__ import annotations

import ssl
import subprocess
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
REPO = "MOKO-TB/yinlu-xuezhang"
API = f"https://api.github.com/repos/{REPO}"
UP = f"https://uploads.github.com/repos/{REPO}"


def _token() -> str:
    out = subprocess.run(["git", "credential", "fill"], cwd=str(ROOT),
                         capture_output=True, text=True,
                         input="protocol=https\nhost=github.com\n\n").stdout
    for line in out.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("取不到 GitHub 凭据(git credential fill 没回 password)")


def _check(path: Path) -> "dict | None":
    if not path.exists():
        return None
    return {"name": path.name, "size": path.stat().st_size, "path": path}


def _assets(ver: str) -> "list[tuple[Path, str]]":
    """按约定找齐三件;缺一件就把它列出来(不猜路径,也不许只发一部分)。"""
    want = [
        (ROOT / "dist" / f"引路学长-{ver}-setup.exe",
         f"yinlu-xuezhang-{ver}-setup.exe", "安装版(推荐)"),
        (ROOT / "dist" / f"引路学长-{ver}-win64.zip",
         f"yinlu-xuezhang-{ver}-win64.zip", "免安装版,解压即用"),
        (ROOT / "dist" / "引路学长-单文件版.exe",
         f"yinlu-xuezhang-{ver}-single-file.exe", "单文件版,双击直接打开"),
    ]
    missing = [str(p) for p, _n, _l in want if not p.exists()]
    if missing:
        raise SystemExit("dist/ 里缺这几件,先跑 build_exe.py:\n  " + "\n  ".join(missing))
    return [(p, n) for p, n, _l in want]


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    tag = sys.argv[1].strip()
    ver = tag.lstrip("vV")
    body = Path(sys.argv[2]).read_text(encoding="utf-8")
    assets = _assets(ver)
    h = {"Authorization": "Bearer " + _token(),
         "Accept": "application/vnd.github+json"}

    def _client(**kw) -> httpx.Client:
        try:
            httpx.get(f"{API}/releases/latest", timeout=15,
                      headers=h).raise_for_status()
            return httpx.Client(**kw)
        except Exception as e:
            print(f"(默认证书链不通:{type(e).__name__} —— 换系统证书库再试)")
            return httpx.Client(verify=ssl.create_default_context(), **kw)

    with _client(timeout=60.0) as c:
        got = c.get(f"{API}/releases/tags/{tag}", headers=h)
        if got.status_code == 200:
            rel = got.json()
            print("release 已存在,复用:", rel["html_url"])
        else:
            r = c.post(f"{API}/releases", headers=h,
                       json={"tag_name": tag, "target_commitish": "main",
                             "name": f"引路学长 {tag}", "body": body,
                             "draft": False, "prerelease": False})
            if r.status_code >= 300:
                print("建 release 失败:", r.status_code, r.text[:400])
                return 1
            rel = r.json()
            print("已发布:", rel["html_url"])

        have = {a["name"] for a in rel.get("assets", [])}
        for path, name in assets:
            if name in have:
                print("资产已在:", name)
                continue
            print(f"上传 {name} ({path.stat().st_size:,} B) …", flush=True)
            with path.open("rb") as f:
                r = c.post(f"{UP}/releases/{rel['id']}/assets",
                           params={"name": name},
                           headers={**h, "Content-Type": "application/octet-stream"},
                           content=f, timeout=httpx.Timeout(3600.0))
            if r.status_code >= 300:
                print("上传失败:", r.status_code, r.text[:400])
                return 1
            print("  → ok")

        final = c.get(f"{API}/releases/tags/{tag}", headers=h).json()

    print("\n终验:", final["html_url"])
    tot = 0
    for a in final["assets"]:
        tot += a["size"]
        print(f"  · {a['name']:46s} {a['size']:>14,} B  {a['state']}")
    print("资产合计 %s B" % f"{tot:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
