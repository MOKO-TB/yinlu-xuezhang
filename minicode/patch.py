"""统一 diff 的解析与落盘(P0-3,v0.9.134)。

为什么要有这一只工具:`edit_file` 是**逐字字符串替换**,一次只能动一个文件的一处,而且
要求模型把 `old_string` 原样背出来。复杂一点的重构(三五个文件各改几行)就得来回好几轮,
每一轮都是一次"背错一个字就白烧一次"的赌。`apply_patch` 收的是**带上下文的补丁**:一处
不匹配整包不落盘,模型一次就能把多文件的改动交上来。竞品里 Codex / Cline / Roo Code 都
是标配(差距矩阵 §9 那一格)。

四条规矩长在这个模块里,每条都有测试钉着:

1. **整包原子**:先全部解析、全部算出新内容,任一文件失败就**一个字节都不写**,并逐文件
   说清坏在哪一个 hunk。半包落盘是这里最坏的坏法 —— 一次跨文件重构改了一半,代码直接
   编译不过,而模型以为改完了。
2. **路径判定不另写一套**:每份文件都走 `tools.fs.resolve_path(..., for_write=True)`,
   也就是与 `write_file` **同一个围栏**;越界与敏感目录在那里拦,不在这里重新发明"什么
   算越界"。权限门那侧同样逐份过(`PermissionGate._patch_gate`),所以补丁里藏一份 `.env`
   或 `.git/hooks/pre-commit` 该问的照样问、该拒的照样拒。
3. **行尾与编码跟着原文件走**:复用 `fs.py` 那一套(#49a)。一份 GBK 中文稿打完补丁还是
   GBK,一份 CRLF 文件不会因为这一包变成"每一行都改过"。
4. **偏移要说出口**:`@@` 行号与磁盘上的对不上时,按 `patch(1)` 的老办法在附近找(窗口
   `PATCH_FUZZ`),找到了**在回话里写明偏移几行**,绝不悄悄改完;找不到就整包拒。

**这一轮刻意不收的两种形状**(写进工具描述,不让模型以为都能干):
- Codex 的 `*** Begin Patch` 方言 —— 那是另一套语法,再写一个解析器就是两把尺子;
- `rename` / `copy` / 二进制补丁 —— 重命名不是"内容编辑",硬拆成 add+delete 会丢历史,
  还会"删掉一份这个补丁没带上下文的文件",所以整包拒并说清为什么。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from minicode.tools import fs

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

# 防御性上限:超限当场说出来,不静默截断(截了就等于学长自己少改了几处)。
MAX_FILES = 40
MAX_HUNK_LINES = 20000
PATCH_FUZZ = 300          # 行号对不上时往附近找多远

ADD, DELETE, UPDATE = "add", "delete", "update"


@dataclass
class Hunk:
    """一个 `@@` 段。`lines` 是 (标记, 正文),标记取 `" "` / `"-"` / `"+"`。"""

    old_start: int
    old_len: int
    new_start: int
    new_len: int
    header: str
    lines: list = field(default_factory=list)
    no_newline_new: bool = False      # 新侧最后一行后面标了「\ No newline」

    @property
    def old_side(self) -> list[str]:
        return [t for m, t in self.lines if m in (" ", "-")]

    @property
    def new_side(self) -> list[str]:
        return [t for m, t in self.lines if m in (" ", "+")]


@dataclass
class FilePatch:
    path: str
    action: str
    hunks: list = field(default_factory=list)

    def count(self, mark: str) -> int:
        return sum(1 for h in self.hunks for m, _ in h.lines if m == mark)


def parse(text: str) -> "tuple[list, list]":
    """统一 diff → `([FilePatch], [问题])`。认不了的形状进问题清单,**不猜路径**。"""
    problems: list[str] = []
    patches: list[FilePatch] = []
    cur: FilePatch | None = None
    hunk: Hunk | None = None
    old_side = new_side = None          # 当前这一段的两行头,凑齐才开一段
    lines = (text or "").replace("\r\n", "\n").split("\n")

    for i, line in enumerate(lines, start=1):
        if line.startswith("--- "):
            old_side = _side(line[4:])
            continue
        if line.startswith("+++ "):
            new_side = _side(line[4:])
            # 两行头凑齐 = 一个新文件段开始。路径以这对头为准(`@@` 前的 `diff --git`
            # 只是补充信息);删除段 `+++ /dev/null` → 用旧侧的名字。
            path = new_side or old_side or ""
            if not path:
                problems.append(f"第 {i} 行这段补丁给不出文件路径(两侧都是 /dev/null)")
                cur, hunk = None, None
                continue
            cur = FilePatch(path, UPDATE if (new_side and old_side) else
                            (ADD if new_side else DELETE))
            patches.append(cur)
            hunk = None
            old_side = new_side = None
            continue
        if line.startswith("@@"):
            m = _HUNK_RE.match(line)
            if not m:
                problems.append(f"第 {i} 行的 hunk 头认不出来:{line[:60]}")
                hunk = None
                continue
            if cur is None:
                problems.append(f"第 {i} 行的 hunk 不属于任何文件(缺 --- / +++ 那两行头)")
                hunk = None
                continue
            hunk = Hunk(int(m.group(1)), int(m.group(2) or 1),
                        int(m.group(3) or 1), int(m.group(4) or 1), line)
            cur.hunks.append(hunk)
            continue
        if line.startswith("diff --git "):
            # 只当分隔符:重新见到文件头时上面已经开过段了,这里清掉"上一段"的状态,
            # 让紧随其后的 ---/+++ 配对干净。
            cur, hunk, old_side, new_side = None, None, None, None
            continue
        if line.startswith(("rename from ", "rename to ", "copy from ", "copy to ")):
            return [], ["这一轮不收 rename/copy 补丁:重命名不是内容编辑,硬拆成"
                        "「新建 + 删除」会丢掉历史,也会删掉一份补丁里没有上下文的文件"]
        if line.startswith(("Binary files ", "GIT binary patch")):
            return [], ["二进制补丁不收:学长没法按行改一份图片/压缩包,"
                        "这类文件请由你自己或专门的工具替换"]
        if line.startswith(("index ", "old mode ", "new mode ", "similarity ",
                            "dissimilarity ", "new file mode ", "deleted file mode ")):
            if cur is not None and line.startswith("new file mode"):
                cur.action = ADD
            elif cur is not None and line.startswith("deleted file mode"):
                cur.action = DELETE
            continue
        if line.startswith("\\"):
            # 「\ No newline at end of file」:只有紧跟在 + 行后面才算**新侧**的声明。
            if hunk is not None and hunk.lines and hunk.lines[-1][0] == "+":
                hunk.no_newline_new = True
            continue
        if not line.strip():
            continue
        if cur is None or hunk is None:
            problems.append(f"第 {i} 行不属于任何 hunk,却带着 diff 正文:{line[:60]}")
            continue
        mark, body = line[0], line[1:]
        if mark not in (" ", "-", "+"):
            problems.append(f"第 {i} 行的行首标记「{mark!r}」不是 diff 认的那三种"
                            "(空格 / - / +)")
            hunk = None
            continue
        hunk.lines.append((mark, body))

    if not patches and not problems:
        problems.append("这份补丁里一个 hunk 都没有")
    if len(patches) > MAX_FILES:
        return patches[:MAX_FILES], [f"一份补丁最多改 {MAX_FILES} 个文件"
                                     f"(这份给了 {len(patches)} 个),请拆开再交"]
    total = sum(len(h.lines) for fp in patches for h in fp.hunks)
    if total > MAX_HUNK_LINES:
        return patches, [f"这份补丁一共 {total} 行,超过单只工具的上限 {MAX_HUNK_LINES} 行,"
                         "请拆成几包"]
    return patches, problems


def _side(text: str) -> str:
    """`a/src/x.py` / `/dev/null` → `src/x.py` / `""`。"""
    t = text.strip().split("\t")[0]
    if t.startswith("a/") or t.startswith("b/"):
        t = t[2:]
    return "" if t == "/dev/null" else t


def _find(hunk: Hunk, lines: list, start: int) -> "int | None":
    """从 `start`(0 基)出发,在 `PATCH_FUZZ` 窗口里找这个 hunk 的旧侧原文。

    顺序是 0、+1、-1、+2、-2… 交替往外扩 —— 这个顺序是刻意的:偏移小的一先中,才不会
    出现"远处有一段一模一样的上下文"把改动打到别处去。
    """
    want = hunk.old_side
    if not want:
        return None
    for delta in range(0, PATCH_FUZZ + 1):
        for cand in ((start + delta,) if delta == 0 else (start + delta, start - delta)):
            if cand < 0 or cand + len(want) > len(lines):
                continue
            if lines[cand:cand + len(want)] == want:
                return cand
    return None


def build(ctx, patches: list) -> "tuple[list, list]":
    """算出每份文件的新内容,**不落盘**。返回 `(结果清单, 失败原因清单)`。

    编码这一步也在这里做(`errors="strict"`):写一半才发现某个字装不下,比不写更糟,
    所以宁可在这里就整包失败。
    """
    outs, fails = [], []
    for fp in patches:
        try:
            outs.append(_build_one(ctx, fp))
        except fs._PATH_ERRORS as e:
            fails.append(f"{fp.path}:{e}")
        except _BadPatch as e:
            fails.append(f"{fp.path}:{e}")
        except (UnicodeEncodeError, LookupError) as e:
            fails.append(f"{fp.path}:这些字用那份文件的编码装不下({type(e).__name__})")
    return outs, fails


class _BadPatch(Exception):
    """补丁与磁盘上的内容对不上(或形状自相矛盾)—— 整包因此一个字节都不写。"""


def _read_lines(path) -> "tuple[list, bool, str, str]":
    """磁盘上那份 → (行清单, 末尾有没有换行, 编码, 行尾形状)。"""
    data = path.read_bytes()
    if b"\x00" in data[:fs.BINARY_SNIFF_BYTES]:
        raise _BadPatch("这是一份二进制文件,补丁按行改不动它")
    text, enc = fs._decode_full(data)
    if fs._unreadable(text):
        raise _BadPatch("这份文件里有读不出来的字节,学长不猜着改"
                        "(改下去会把猜出来的字永久写进你的文件)")
    eol = fs._dominant_eol(text)
    body = fs._to_lf(text) if eol in ("\r\n", "\r") else text
    had_nl = body.endswith("\n")
    lines = body[:-1].split("\n") if had_nl else body.split("\n")
    return lines, had_nl, enc, eol


def _render(lines: list, had_nl: bool, eol: str | None, enc: str) -> bytes:
    text = "\n".join(lines) + ("\n" if had_nl else "")
    if eol in ("\r\n", "\r"):
        text = text.replace("\n", eol)
    return text.encode(enc, errors="strict")


def _build_one(ctx, fp: FilePatch) -> dict:
    """算出这一份文件该写成什么样(或该删),顺带把围栏走一遍。

    结果里的 `target` 是要落的绝对路径,`bytes` 是内容(`None` = 删除)。
    """
    from minicode.tools.base import resolve_path

    path = resolve_path(ctx, fp.path, for_write=True)
    res = {"path": fp.path, "action": fp.action, "target": path,
           "added": fp.count("+"), "removed": fp.count("-"), "offset": 0}

    if fp.action == DELETE:
        if not path.exists():
            raise _BadPatch("要删的文件不在磁盘上(补丁与磁盘对不上)")
        res["bytes"] = None
        return res

    if fp.action == ADD:
        if path.exists():
            raise _BadPatch("补丁说这是新建文件,可它已经在磁盘上了"
                            "(要改它就得在补丁里给出它现在的内容)")
        if any(m == "-" for h in fp.hunks for m, _ in h.lines):
            raise _BadPatch("新建文件那段里出现了删除行,这份补丁自相矛盾")
        new_lines = [t for h in fp.hunks for m, t in h.lines if m in (" ", "+")]
        had_nl = not (fp.hunks and fp.hunks[-1].no_newline_new)
        res["bytes"] = _render(new_lines, had_nl, None, "utf-8")
        return res

    if not path.exists():
        raise _BadPatch("文件不在磁盘上(补丁说的是更新,却找不到那份文件)")
    lines, had_nl, enc, eol = _read_lines(path)
    out: list = []
    cursor = 0
    for h in fp.hunks:
        start = max(0, h.old_start - 1)
        at = _find(h, lines, start)
        if at is None:
            want = (h.old_side or ["(这个 hunk 里没有任何旧侧行)"])[0]
            raise _BadPatch(f"hunk「{h.header}」的上下文在这份文件里找不到"
                            f"(第一行要的是:{want[:60]!r})")
        if at != start:
            res["offset"] = at - start
        out.extend(lines[cursor:at])
        out.extend(h.new_side)
        cursor = at + len(h.old_side)
        if h is fp.hunks[-1] and h.no_newline_new:
            had_nl = False
    out.extend(lines[cursor:])
    res["bytes"] = _render(out, had_nl, eol, enc)
    return res


def apply(ctx, patches: list) -> tuple:
    """整包落盘。返回 `(结果清单, 失败清单)` —— **失败非空时一个字节都不写**。

    顺序上的取舍:先 `build` 全部算完(路径围栏、上下文匹配、编码全在那里),全成才开始写。
    写这一段本身不再做判断,所以中途抛异常的概率被压到"磁盘满/权限"那一类,而那已经不是
    补丁的错 —— 那种情况下确实可能半包,回话会照实说。
    """
    outs, fails = build(ctx, patches)
    if fails:
        return [], fails
    for r in outs:
        if r["action"] == DELETE:
            r["target"].unlink(missing_ok=True)
            continue
        r["target"].parent.mkdir(parents=True, exist_ok=True)
        r["target"].write_bytes(r["bytes"])
    return outs, []


def patch_paths(text: str) -> list:
    """这份补丁会动到哪几份文件(原样,不做归一)。

    给两个读者用同一份事实:权限门逐份过围栏(`PermissionGate._patch_gate`),以及
    改动前像(`Agent._snapshot_before_mutation` 要在动手**之前**把每一份的原文存下来)。
    解析失败时返回空清单 —— 那时门不需要拦,handler 会去说那句"这份补丁读不出"。
    """
    patches, _problems = parse(text or "")
    return [fp.path for fp in patches]


def apply_patch(ctx, patch: str = "", reason: str = "", dry_run: bool = False) -> str:
    """工具 handler:一次落一整包多文件补丁,任一文件对不上就整包不落盘。"""
    patches, problems = parse(patch or "")
    if problems or not patches:
        return ("错误: 这份补丁读不出来,一个字都没改。"
                + "".join(f"\n  · {p}" for p in problems[:8])
                + "\n(只收 git diff / patch 那种统一 diff:每段一对 `--- / +++` 头,"
                  "下面 `@@ -旧,行数 +新,行数 @@`,行首是空格/-/+;"
                  "Codex 的 `*** Begin Patch` 方言、rename、二进制补丁都不收。)")
    if dry_run:
        outs, fails = build(ctx, patches)
        if fails:
            return _fail_text(fails, checked=True)
        return ("预演:这份补丁能落盘,但**什么都没改**。"
                + _summary(outs)
                + "\n要去掉预演再真落盘,把 dry_run 设成 false。")
    outs, fails = apply(ctx, patches)
    if fails:
        return _fail_text(fails)
    return "已应用补丁(整包落盘,之前一个字节都没动)。" + _summary(outs)


def _fail_text(fails: list, checked: bool = False) -> str:
    head = ("错误: 预演就过不了,这份补丁落不了盘。" if checked
            else "错误: 整包没落盘(0 个文件被改动)。")
    return head + "失败原因:\n" + "\n".join(f"  · {f}" for f in fails[:8]) + (
        f"\n  · 另有 {len(fails) - 8} 条同类问题" if len(fails) > 8 else "")


def _summary(outs: list) -> str:
    lines = [f"  {r['path']}  " + {"add": "新建", "delete": "删除", "update": "改"}[r["action"]]
             + f"  +{r['added']} −{r['removed']}"
             + (f"(上下文偏移 {r['offset']:+d} 行 —— 补丁写的行号与磁盘上的对不上,"
                f"按上下文在原文件里找到的)" if r.get("offset") else "")
             for r in outs]
    total = f"共 {len(outs)} 个文件。"
    return "\n" + total + "\n" + "\n".join(lines)
