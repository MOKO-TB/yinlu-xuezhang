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


def _sha256(path: Path) -> str:
    """整件文件算一遍 sha256(分块读,920 MB 也不占内存)。"""
    import hashlib
    h = hashlib.sha256()
    with path.open("rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def published_names(ver: str) -> dict:
    """发布页上那六件**叫什么名字**,只有这一处说得出。

    构建端写清单(`build_exe.assets_manifest`)与发布端上传(`_assets`)都必须从这里取。
    v0.9.179 实测的破法:清单里记的是**本机文件名**(`引路学长-…-setup.exe`),发布页上是
    英文名,而 App 端 `pick_asset` 拿清单里的名字与发布资产**逐字比** —— 两边永远对不上,
    "该下哪一件"就退化成"找不到那件"(界面上是「打开发布页」,或按整包下并说清单在说谎)。
    与 §7 (o) 同族:一件事两处各写一份,各自都被测对,合起来是坏的。
    """
    return {
        "setup": f"yinlu-xuezhang-{ver}-setup.exe",
        "zip": f"yinlu-xuezhang-{ver}-win64.zip",
        "single_file": f"yinlu-xuezhang-{ver}-single-file.exe",
        "app_setup": f"yinlu-xuezhang-{ver}-app-setup.exe",
        "assets": f"yinlu-xuezhang-{ver}-assets.json",
        "sha256": f"yinlu-xuezhang-{ver}-sha256.txt",
    }


def _assets(ver: str) -> "list[tuple[Path, str]]":
    """按约定找齐三件;缺一件就把它列出来(不猜路径,也不许只发一部分)。"""
    nm = published_names(ver)
    want = [
        (ROOT / "dist" / f"引路学长-{ver}-setup.exe", nm["setup"], "安装版(推荐)"),
        (ROOT / "dist" / f"引路学长-{ver}-win64.zip", nm["zip"], "免安装版,解压即用"),
        (ROOT / "dist" / "引路学长-单文件版.exe",
         nm["single_file"], "单文件版,双击直接打开"),
    ]
    missing = [str(p) for p, _n, _l in want if not p.exists()]
    if missing:
        raise SystemExit("dist/ 里缺这几件,先跑 build_exe.py:\n  " + "\n  ".join(missing))
    out = [(p, n) for p, n, _l in want]
    # v0.9.178 的两件新产物:**有就必带,没有就说一句为什么**(不静默少发)。
    # 少了 app-setup 不要命 —— App 的 `plan_update` 会因为清单里没这件而退回整包;
    # 但少了 sha256.txt 里那一行就要命:下载回来"没核对过",那颗按钮是灰的。
    # 所以这两件**必须一起出现**,只传一件比两件都不传更坏。
    extra = [(ROOT / "dist" / f"引路学长-{ver}-app-setup.exe",
              nm["app_setup"], "仅 app 更新包(不含馆藏库与内核)"),
             (ROOT / "dist" / nm["assets"],
              nm["assets"], "发布清单:各件哈希与两块数据的身份")]
    have_extra = [(p, n) for p, n, _l in extra if p.exists()]
    for p, n, l in extra:
        if not p.exists():
            print(f"注意:没有 {n}({l})—— 这一版的更新会一律走整包,不会跳过那 84.6%")
    out += have_extra
    # **校验文件跟着一起发**(v0.9.176):App 里那条"下完先核 sha256,没核对就不让装"
    # 读的就是它(`minicode/proactive.py::_expected_sha256` 按同一个名字去取)。
    # 它防的是"下载坏了/被截了/链接被换到别处"这一类,**不防** GitHub 账号本身被接管
    # —— 那种情况下攻击者也能重写这份清单。说清边界,别把它当签名体系用。
    lines = "".join("%s  %s\n" % (_sha256(p), n) for p, n in out)
    sums = ROOT / "dist" / f"yinlu-xuezhang-{ver}-sha256.txt"
    sums.write_text(lines, encoding="utf-8", newline="\n")
    out.append((sums, sums.name))
    return out


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
