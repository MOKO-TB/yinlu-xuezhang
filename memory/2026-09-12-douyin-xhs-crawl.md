# 校园问答知识库:多源抓取记录(2026-09-12/13)

目标:学生问到「广应科肇庆校区」「肇庆周边吃玩」「学习/考证」时,Agent 直接命中
已抓内容 + 可点击链接,不用现场爬取。

## 当前知识库(kb/,共 560 条)

| 文件 | 条数 | 内容 |
|---|---|---|
| school-gzasc.json | 254 | 学校官网文章(24 个栏目,含宿舍/活动/学生故事/视频/办事入口) |
| bili-videos.json | 125 | B 站视频(标题/UP主/时长/播放量 + 观看地址) |
| knowledge.json | 72 | 校内知识/问答 |
| xhs-notes.json | 51 | 小红书笔记(正文 + 笔记地址) |
| wx-articles.json | 28 | 微信公众号文章(搜狗微信搜索) |
| notices.json / notices-sample.json | 15 / 15 | 通知公告 |

检索入口:`minicode/kb.py::search_knowledge`,读 `kb_dirs(workspace)`。
非官网来源的条目 credibility 为 B-*(引用时必须提示"待核实")。

### 口语提问怎么才不空军:用 category 锁栏目

关键词打分(标题×3/标签×2/正文×1,中文 2-gram)对付不了同义词——问"学校有什么
比赛可以参加"时"比赛"命不中标题写"竞赛/挑战杯"的官网条目,反而被 B 站长正文顶掉。
解法:栏目名已写进条目 tags,用 `search_knowledge(category="学科竞赛")` 先限定再搜,
六类实测都精准命中(学科竞赛/校园活动与文体/宿舍与生活服务/视频与图文校园/
服务大厅与办事入口/学生故事与风采),而且每条都带 url。`kb_prompt_section`
已把这条用法写进系统提示词(含可用栏目名清单)。
题外:栏目 tags 里另加了口语同义词(比赛/大赛/宿舍/寝室/视频/宣传片/学长学姐…),
不用 category 时也能提高命中率。

### 官网库(school-gzasc.json)怎么扩栏目、怎么处理"正文不在文字里"的页

栏目在 `scripts/school_topics.json` 的 `topics[]` 里配(seeds 列列表页 + include/exclude
关键词);列表页翻页:站群是 `<栏目>/N.htm`,新闻网子站是倒序(`/news/xxyw/120.htm`),
拿不准就用 `list_pages` 写死。列表页抓不到链接的页(服务导航/二维码指引)用 `articles` 写死。

**四类"没有正文"的页面都要留条目,否则学生拿不到链接**:

| 页面类型 | 判定 | 条目正文 |
|---|---|---|
| 仅附件(管理办法/培养方案) | 链接或正文里有 `e=.pdf/.docx/...` | 附件名清单 + 原文链接 |
| 视频页 | html 有 `<video` / `.mp4` / `.m3u8` | `观看地址: 原页链接` |
| 图文/二维码指引(热水充值、报修、校园服务公众号) | 正文容器里有 `<img>` | `查看地址: 原页链接` |
| 失效链接(官网 404 也返 200) | 标题含 404/系统提示 | 不入库 |

**踩过的四个坑(都已修,别改回去)**:
1. `.vsb` 附件既送文档也送二维码图片/视频,"有 .vsb 就是视频"会把热水充值页标成视频页
   → 改成看 `<video` 标签 + 文档后缀白名单(`_doc_ext_from_url`);
2. 正文里残留转义过的裸 `(javascript:void\(0\);)` 会撑到 200 字以上,让"这页只有页脚"的
   判定失效、把页脚地址电话当正文入库 → 清理正则必须容忍反斜杠(`_JS_VOID`);
3. 只按 `KB_MIN_CONTENT`(200 字)判空不够:页脚+地址+电话也能凑过。所以加了
   `_body_is_boilerplate_only`(去掉标题/发布时间/页脚后不足 60 字就不算正文);
4. 只看整页 `<img>` 会把页眉 logo 当内容图 → 只看正文容器(`#vsb_content`/`.v_news_content`)
   里的图,或正文 markdown 里的图片链接。

跑法:`.venv/Scripts/python.exe scripts/crawl_school_topics.py`(全量 24 栏目约 6 分钟)、
`--only 宿舍与生活服务`(单栏目)、`--dry-run`(只看候选链接)、`--prune`(离线清理)。
测试:`tests/test_school_topics.py`(34 项,含上面四类判定的回归用例)。

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
