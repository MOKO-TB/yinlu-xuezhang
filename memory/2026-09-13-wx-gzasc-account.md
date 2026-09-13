# 2026-09-13 定向爬「广州应用科技学院」公众号（肇庆校区）

## 做了什么

新增 `scripts/crawl_wx_account.py` + `scripts/wx_account.json`，定向爬该公众号的
肇庆校区内容，产出 `kb/wx-gzasc.json`（200 条，credibility=A），agent 检索时可直接命中。

## 关键事实（花了一轮才摸清，别再重复试）

- **用户可点的微信永久链接是** `mp.weixin.qq.com/s?__biz=&mid=&idx=&sn=&chksm=`
  - 五要素缺一不可：**缺 chksm 只返回 17KB 空壳页**（带 chksm 才是 3MB 完整文章）
  - **chksm 必须正好 64 位**：从 HTML 里挖常带尾巴（抓到 76 位），多一字符就打不开
  - `sn` 必须 32 位
- `<meta og:url>` **只在直连永久链接时才有值**；经搜狗一次性签名链进来时为空 →
  这就是"搜狗能爬到内容却拿不到用户可点链接"的根因
- 可用发现路径：搜狗微信搜索（分页）+ **文章正文内链 BFS**（编辑手插的"往期回顾"
  类链接是永久链接金矿，一篇能带 33~62 条）
- **不可用**：账号历史页 `/mp/profile_ext`、合集页 `appmsgalbum`（要登录）；
  短链 `/s/xxx`、不含 sn/chksm 的链接（打不开）；谷歌必应 360 头条（不吐规范链接）
- 百度是**唯一**能吐出 sn+chksm 的搜索引擎，但只收录新文章（老文章返回主题相近的
  别篇，被我们的标题校验挡下），且连查几次就返回"安全验证"页 → 产出低、约 1 分钟/条

## 结果

- 200 条：**118 条带规范永久链接**（用户点击直达），82 条退回搜狗入口链接
  （正文里已注明"多一步点击"；内容本身是全文入库，回答问题不依赖链接）
- 30 条含视频号视频（`data-mpvid`）；视频号没有公开网页地址，只能在原文里看
- B站另有 15 条本校视频（探校/宿舍/报考）带可点链接，见 `kb/bili-videos.json`
- 顺手清掉旧库 `kb/wx-articles.json` 里与新库重复的 7 条

## 维护命令

```bash
.venv/Scripts/python.exe scripts/crawl_wx_account.py                # 增量爬取
.venv/Scripts/python.exe scripts/crawl_wx_account.py --fix-links    # 离线归一化链接/回填身份
.venv/Scripts/python.exe scripts/crawl_wx_account.py --resolve-links # 百度补缺链接（慢，可断点续跑）
```

## 教训（工具层面）

- **不要用 PowerShell 的 Get-Content/Set-Content 改 UTF-8 文件**：会按 GBK 误读并
  吃掉引号，把 `wx_account.json` 搞坏过一次，只能用 Python 重写
- 判断元组真值：`bool(parse_canonical(url))` 恒为真（空元组也是元组），必须取 `[0]`
