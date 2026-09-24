"""从 git log 生成 `CHANGELOG.md`(v0.9.39 R 批 #86)。

为什么不手抄一份:这个仓库的每一版都在提交信息里写了人话(形如
`v0.9.37: 右侧「任务监控」栏 —— 此刻/计划/时间线`),手抄的变更日志一定会漂 ——
而一份漂掉的 CHANGELOG 比没有更坏,它会让人按假历史去理解代码。所以:
**提交信息是真相,CHANGELOG 是它的一个视图**。

规则很少,也刻意不做聪明的事:

- 只看提交**标题**,标题里没有 `v0.9.x` / `v0.3.0` 这类版本标记的一律不进 ——
  那些是过程性提交(文档、跑测、重出包),写进变更日志只会淹掉真正的里程碑。
- 一行同时提到多个版本(`v0.9.18-v0.9.21: 课表/天气/简报…`)时挂在**第一个**版本下,
  原文照抄不改一个字:区间本来就在标题里写着,拆开反而是我在编。
- 版本号按数值从新到旧排;同一版本内按 git log 的顺序(新在前)。

用法:
    python scripts/gen_changelog.py            # 重新生成 CHANGELOG.md
    python scripts/gen_changelog.py --check    # 只判断它是否已经最新(测试用)
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "CHANGELOG.md"

# `v0.9.37` / `v0.9.0` / `v0.3.0` —— 第三段可省(v0.9.0 那种当年就是这么写的)
VERSION_RE = re.compile(r"\bv(\d+)\.(\d+)(?:\.(\d+))?\b")

HEADER = (
    "# 更新日志\n"
    "\n"
    "本文件**由脚本生成**:`python scripts/gen_changelog.py`(提交前跑一次,"
    "`pytest tests/test_changelog.py` 会替你核对它有没有漂)。不要手改 —— "
    "手写的那一份一定会和 git log 分家。\n"
    "\n"
    "只收录标题里点了版本号的提交;没点版本的过程性提交(文档、跑测、重出包)"
    "不进这里。每一行都是**当初的提交标题原文**,不是我事后改写的说法,"
    "所以括号里的取舍(\u201c仍只本地提交\u201d\u201c用户点名\u201d这类)也一并留着。\n"
    "\n"
    "想看某一轮**为什么**长成这样、踩了什么坑:读 `HANDOFF.md` §2 与"
    " `docs/handoff/milestones.md`(那里是按轮写的流水账,这里是索引)。\n"
)


def version_of(subject: str) -> tuple[tuple[int, ...], str] | None:
    """标题里的第一个版本标记 `(排序键, 原文写法)`;没有就给 None(这行不进变更日志)。

    排序键与原文分开是因为**标题里怎么写就怎么标**:有人写 `v0.9`,我就不能给他
    补成 `v0.9.0` —— 那是我在编历史。
    """
    m = VERSION_RE.search(subject or "")
    if not m:
        return None
    key = tuple(int(x) if x else 0 for x in m.groups())
    return key, m.group(0)


def entries(log_lines: list[str]) -> list[tuple[tuple[int, ...], str, str]]:
    """把 `%ad%x09%s` 形态的日志行摊成 (版本键, 版本写法, 一行) ,保持 git 给的先后。"""
    out: list[tuple[tuple[int, ...], str, str]] = []
    for line in log_lines:
        date, _, subject = line.partition("\t")
        found = version_of(subject)
        if found:
            key, label = found
            out.append((key, label, f"{subject.strip()}  · {date.strip()}"))
    return out


def render(pairs: list[tuple[tuple[int, ...], str, str]]) -> str:
    """按版本分组渲染。同组内保持传入顺序(新在前)。"""
    buckets: dict[tuple[int, ...], list[str]] = {}
    labels: dict[tuple[int, ...], str] = {}
    for key, label, line in pairs:
        labels.setdefault(key, label)
        buckets.setdefault(key, []).append(line)
    parts = [HEADER]
    for key in sorted(buckets, reverse=True):
        parts.append(f"\n## {labels[key]}\n")
        parts.extend(f"- {line}\n" for line in buckets[key])
    return "".join(parts)


def log_lines(rev: str | None = None) -> list[str]:
    args = ["git", "log", "--date=short", "--pretty=%ad%x09%s"]
    if rev:
        args.append(rev)
    proc = subprocess.run(args, cwd=str(ROOT), capture_output=True,
                          text=True, encoding="utf-8")
    if proc.returncode != 0:
        # 宁可抛出让测试红,也不静默生成一份空日志 —— 空 CHANGELOG 看起来"很正常"
        raise RuntimeError(f"git log 失败(退出码 {proc.returncode}):{proc.stderr.strip()}")
    return [ln for ln in proc.stdout.splitlines() if ln.strip()]


def short_sha(rev: str | None = None) -> str:
    proc = subprocess.run(["git", "rev-parse", "--short", rev or "HEAD"],
                          cwd=str(ROOT), capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"git rev-parse 失败:{proc.stderr.strip()}")
    return proc.stdout.strip()


def generate(rev: str | None = None) -> str:
    """生成整份文件。`rev` 给定时只收到那个提交为止(测试就是这么对的)。"""
    sha = short_sha(rev)
    body = render(entries(log_lines(rev)))
    # 尾注不是装饰:它写明这份索引**收到哪个提交为止**。
    # 为什么必须有 —— 生成这件事有个先有鸡还是先有蛋的死结:任何"提交标题里点了
    # 版本号"的提交,一旦提交完成就会让刚生成的那份 CHANGELOG 少一行(它自己那一行)。
    # 于是"必须与 HEAD 逐字一致"这条断言**永远红**。改成"与自己记录的提交一致"就
    # 自洽了:落后不叫漂,文件自己说清落后到哪儿;跑一次脚本就推到最新。
    return body + f"\n<!-- 生成自 git 提交 {sha};此后的提交还没收进来 -->\n"


def main() -> int:
    if "--check" in sys.argv:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        m = re.search(r"生成自 git 提交 ([0-9a-f]+)", current)
        if not m:
            print("CHANGELOG.md 没有「生成自 git 提交 …」尾注,先跑一次 "
                  "`python scripts/gen_changelog.py`")
            return 1
        if current != generate(m.group(1)):
            print(f"CHANGELOG.md 与它自己记的提交 {m.group(1)} 对不上,跑一次 "
                  "`python scripts/gen_changelog.py`")
            return 1
        behind = short_sha() != m.group(1)
        print(f"CHANGELOG.md 自洽(生成自 {m.group(1)})"
              + (";HEAD 之后还有没进来的提交,要发版就重跑一次" if behind else ";已是最新"))
        return 0
    OUT.write_text(generate(), encoding="utf-8")
    print(f"已写出 {OUT.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
