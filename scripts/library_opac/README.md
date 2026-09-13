# 图书馆 OPAC 全量书目抓取

把本校图书馆 OPAC（`https://mfindgzyykj.libsp.cn`，超星统一检索）的**全部书目**
抓成本地 SQLite，供 `library_search` 工具与 `crawled/` 归档使用。

## 数据在哪

| 产物 | 位置 |
| --- | --- |
| 离线检索库（agent 用） | `crawled/mfindgzyykj.libsp.cn/library.db` |
| 可读归档（人看/CSV） | `crawled/mfindgzyykj.libsp.cn/opac-catalog/` |
| 知识库条目（search_knowledge 用） | `kb/library-gzasc.json` |
| 抓取源库（工作库） | `D:\game\library\books.db` |

`crawled/` 已在 `.gitignore` 中，数据不入仓库；本目录只放脚本。

## 为什么要分区抓

OPAC 的检索接口有两个硬限制：

1. **深分页上限**：`page` 最多约 200 页 × 50 条 = **10,000 条/查询**，
   再往后翻会重复返回同一批数据。
2. **没有批量馆藏接口**：馆藏位置只能一条书目一次请求。

而全馆有 130 万+ 条书目，远超单次上限。所以按
**「中图法分类号前缀 + 出版年区间」自适应二分**，把全馆切成 488 个
≤9000 条的小分区，每区逐页抓取，跨分区用 `record_id` 去重。

分区正确性依据两条实测结论：
- 出版年分桶求和 == 父分区总数（分桶互斥且完备）；
- 分类号前缀匹配是**模糊匹配**、会重叠，所以只作辅助维度。

## 用法

必须用 Anaconda 的 Python（系统默认 `python` 是 Store 占位符）：

```powershell
$PY = "D:\anaconda\python.exe"

& $PY harvest.py status         # 查看进度（分区数、已抓页数、记录数）
& $PY harvest.py discover       # 只做分区枚举
& $PY harvest.py all            # 全量：枚举 + 抓取 + 馆藏地字典（约 25 分钟）
& $PY harvest.py enrich 400000  # 补馆藏位置（断点续跑，约 1.5 小时跑完 35 万册）
& $PY harvest.py supplement 0,1,2,3,4,5,6,7,8,9   # 补 A-Z 之外的根分区

& $PY archive.py                # 生成 crawled 归档 + library.db 快照
& $PY make_kb.py                # 生成 kb/library-gzasc.json
```

所有抓取步骤都**可重复运行、断点续传**（已完成的页/记录会跳过）。

## 注意

- `library.db` / `books.db` 是 **WAL 模式**：要给别处复制，必须用 SQLite
  在线备份（见 `archive.py` 的 `snapshot()`）。直接复制 `.db` 文件会丢掉
  仍在 `-wal` 里的数据，导致复制出来的库表都是空的。
- 脚本里的路径常量指向 `D:\game\library`（抓取工作目录）；换机器时改
  `harvest.py` 顶部的 `OUT_DIR` / `DB_PATH` 与 `archive.py` 的 `SRC_DB` / `DEST`。
- 覆盖面：与官网精确匹配比对约 **99.97%**（1,331,774 / 1,332,155）；
  ISBN、索书号逐条抽查一致。
