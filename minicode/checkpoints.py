"""改动前像的落盘(v0.9.131):让 `/undo`、`/rewind` 与「本轮改动」跨重启还在。

为什么值得做:那份栈此前只活在 `Agent.__init__` 的一个 list 里,于是"关一次窗口
就退不回去"是真实会发生的事 —— 同学改了三个文件、程序崩了或他重启,回来清单是空的,
而磁盘上那三个文件还是改过的样子。竞品三家都把前像持久化(ZCode 随会话存成
`SESSION_ENTRY_WORKSPACE_CHECKPOINT`,Claude Code 的检查点 `--resume` 之后仍可
`/rewind`,Qoder 每个提交建一个恢复标记),我们只有内存那一半。

四条规矩,都是"宁可少退、不可退错"这一条根上长出来的:

1. **存不下的就整条不要,不许留一条 `data: null` 的空壳**。读回来时
   `data is None` 的含义是"这个文件当时不存在"(新建文件),按那个含义回退会
   **删掉用户现在的文件**。所以超限的记录一律不落盘:少一条能退的,只是清单短了;
   多一条假的前像,是拿用户的文件去赌。
2. **原子替换**。半截的 JSON 会被读成"没有检查点",那等于静默丢掉整本账 ——
   写临时文件再 `replace`,读不动就当没有(退回今天的行为,不是报错)。
3. **它记的是内容,不是路径**。里面可能是同学课件原文里的任意字,所以它
   进 `SHIP_NEVER`(不进安装包、不进迁移包),且删会话时一起删。
4. **`seq` 与 `epoch` 必须一起还原**。staleness 判的是 `rec.epoch != self._epoch`
   这个**精确不等**,只还原记录不还原纪元,恢复出来的每一条都会被判成
   "这中间压过一次历史,我不敢退" —— 那这份持久化等于白做。
"""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path

CKPT_DIR_NAME = "checkpoints"
CKPT_MAX_RECORDS = 200           # 一本账最多记多少处改动(超了丢最老的)
CKPT_MAX_FILE_BYTES = 1_500_000  # 单份前像超过这个数就整条不落盘(见模块开头第 1 条)
CKPT_MAX_TOTAL_BYTES = 8_000_000  # 整本账的体积盖:超了就从最老的开始扔
CKPT_VERSION = 1


def _encode(rec: dict) -> dict | None:
    """一条记录 → 能进 JSON 的形状。返回 None 表示"这条不存"。"""
    data = rec.get("data")
    if data is not None:
        if not isinstance(data, (bytes, bytearray)):
            return None
        if len(data) > CKPT_MAX_FILE_BYTES:
            return None
    try:
        path = str(Path(rec["path"]))
    except (KeyError, TypeError, ValueError):
        return None
    out = {
        "seq": int(rec["seq"]),
        "path": path,
        "existed": data is not None,
        "b64": base64.b64encode(bytes(data)).decode("ascii") if data else "",
        "display": str(rec.get("display") or path),
        "at": float(rec.get("at") or 0.0),
        "post": rec.get("post"),
        "turn": int(rec.get("turn") or 0),
        "epoch": int(rec.get("epoch") or 0),
    }
    return out


def encode_stack(stack: list[dict], seq: int, epoch: int) -> str:
    """整本账 → JSON 文本。`seq` / `epoch` 与记录一起存,原因见模块开头第 4 条。"""
    rows = []
    for rec in stack[-CKPT_MAX_RECORDS:]:
        row = _encode(rec)
        if row is not None:
            rows.append(row)
    while len(rows) > 1 and len(json.dumps(rows, ensure_ascii=False)) > CKPT_MAX_TOTAL_BYTES:
        rows.pop(0)          # 扔最老的:新改动更可能是他正要退的那一个
    return json.dumps({"version": CKPT_VERSION, "seq": seq, "epoch": epoch,
                       "saved_at": time.time(), "records": rows},
                      ensure_ascii=False, default=str)


def decode_stack(text: str) -> tuple[list[dict], int, int]:
    """JSON 文本 → (记录, seq 计数器, epoch)。读不懂就回空 —— 退回今天的行为。

    这里**不抛异常**是刻意的:检查点文件是缓存性质的东西,它坏了的代价应该是
    "这次退不回去",而不是"会话根本打不开"。
    """
    try:
        raw = json.loads(text)
    except (ValueError, TypeError):
        return [], 0, 0
    if not isinstance(raw, dict) or raw.get("version") != CKPT_VERSION:
        return [], 0, 0
    out = []
    for row in raw.get("records") or []:
        if not isinstance(row, dict):
            continue
        try:
            data = base64.b64decode(row.get("b64") or "", validate=True) \
                if row.get("existed") else None
        except Exception:
            continue          # 单条解不动就丢这一条,不牵连整本账
        out.append({"seq": int(row["seq"]), "path": Path(row["path"]),
                    "data": data, "display": row.get("display") or str(row["path"]),
                    "at": float(row.get("at") or 0.0), "post": row.get("post"),
                    "turn": int(row.get("turn") or 0),
                    "epoch": int(row.get("epoch") or 0)})
    return out, int(raw.get("seq") or 0), int(raw.get("epoch") or 0)


def save(path: Path | None, stack: list[dict], seq: int, epoch: int) -> None:
    """原子写。写不成不报错(磁盘满/只读目录),但也不假装存了。"""
    if path is None:
        return
    if not stack:
        drop(path)
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(encode_stack(stack, seq, epoch), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def load(path: Path | None) -> tuple[list[dict], int, int]:
    if path is None:
        return [], 0, 0
    try:
        return decode_stack(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], 0, 0


def sweep_orphans(sessions_dir: Path | None) -> int:
    """删掉那些**留档已经不在了**的账本,返回删了几份。

    为什么要有这一件:账本里装的是别人文件改之前的原文。会话被删(侧栏删除、
    保留策略清理、手工删)而账本留着,就是**同学的课件原文无声地留在盘上**。
    放在启动那条路上(`apply_retention`)是因为它无条件跑 —— `prune_sessions`
    只在配了 `session_keep_last` 时才动,靠它兜不住。
    """
    from minicode.paths import app_path
    if not sessions_dir:
        return 0
    here = app_path(CKPT_DIR_NAME)
    if not here.is_dir():
        return 0
    alive = {p.stem for p in Path(sessions_dir).glob("session_*.jsonl")}
    n = 0
    for f in here.glob("*.json"):
        if f.stem in alive:
            continue
        drop(f)
        n += 1
    return n


def drop(path: Path | None) -> None:
    """删掉一本账(会话被清、或留档被删时跟着删)。"""
    if path is None:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass
