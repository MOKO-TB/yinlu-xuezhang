# 校园问答知识库:多源抓取记录(2026-09-12/13)

目标:学生问到「广应科肇庆校区」「肇庆周边吃玩」「学习/考证」时,Agent 直接命中
已抓内容 + 可点击链接,不用现场爬取。

## 当前知识库(kb/,共 442 条)

| 文件 | 条数 | 内容 |
|---|---|---|
| school-gzasc.json | 136 | 学校官网文章(最早建的库) |
| bili-videos.json | 125 | B 站视频(标题/UP主/时长/播放量 + 观看地址) |
| knowledge.json | 72 | 校内知识/问答 |
| xhs-notes.json | 51 | 小红书笔记(正文 + 笔记地址) |
| wx-articles.json | 28 | 微信公众号文章(搜狗微信搜索) |
| notices.json / notices-sample.json | 15 / 15 | 通知公告 |

检索入口:`minicode/kb.py::search_knowledge`,读 `kb_dirs(workspace)`。
非官网来源的条目 credibility 为 B-*(引用时必须提示"待核实")。

## 各源现状与坑

### 小红书 ✅ 可用(需一次扫码)
- 新脚本 `scripts/crawl_xhs_notes.py`(自包含,51 条笔记已入库)
- 登录态在 `.automation/xhs-storage-state.json`(由 `crawl_xhs_topics.py --login`
  弹窗扫码后导出;本脚本不弹窗、不碰密码)
- **两个真坑**(都已修):
  1. 详情 URL 的 `xsec_source` 必须带值(`pc_search`),留空时详情页
     拒返正文 → 旧脚本"详情失败"的根因;
  2. 整份 `__INITIAL_STATE__` 含 `new Map()` 残留,整体 `json.loads` 会在
     无关 store(`AiNoteDetailStore`)上炸 → 改为定位 `noteDetailMap` 后
     **配平括号定向解析**,并优先用浏览器内 JS(`READ_NOTE_JS`)直接读状态。
- 旧脚本 `scripts/crawl_xhs_topics.py` 仍保留用于 `--login`。

### B 站 ✅ 可用(免登录)
- `scripts/crawl_bili_topics.py`;关键词配置 `scripts/bili_topics.json`
- 新增 `SCHOOL_TOPIC` 校名白名单过滤(`filter_relevant`):搜「广州应用科技学院
  肇庆校区」会混进广州华商学院肇庆校区、广州交校等同城学校,词面相似度拦不住
- 已清理 5 条跨校噪声

### 微信公众号 ✅ 可用(搜狗微信搜索,慢)
- `scripts/crawl_wx_topics.py`;配置 `scripts/wx_topics.json`
- 已清理 4 条:广金肇庆校区(跨校)+ 3 条「西部计划/国创计划」(「学习计划」
  关键词误伤,语义无关)
- 高价值命中:广应科肇庆校区首条公交专线、接入广州地铁、校园指南、食堂宿舍

### 抖音 ❌ 不可用(风控)
- `scripts/crawl_douyin_topics.py` 已写好(6 个解析测试全绿),登录也能过,
  但**搜索页必撞滑块验证码**(验证码自身加载失败,自动化窗口过不了)
- 抓到的"搜索结果"实际是页面骨架里的精选流(46 条无关游戏/影视),被
  `filter_relevant` 全拦,知识库 0 污染
- 要爬抖音只能真人在弹出窗口里手过一次验证码(`_fetch_search` 已支持等待)

### 美团/大众点评 ❌ 不可用
- 探测结果:点评搜索页 → 验证中心;美团搜索页 → 登录墙;肇庆首页能开但
  店铺列表是 JS 渲染 + 需登录,拿不到结构化店铺数据

## 回归测试
- `tests/test_crawl_xhs_notes.py` 7 项(配平括号/坏 store/ID 校验/相关性过滤)
- `tests/test_crawl_bili_topics.py` 8 项(含校名白名单)
- 全量 `pytest -q`:214 passed
