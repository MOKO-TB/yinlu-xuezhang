# 引路学长

一个运行在 Windows 桌面的编程引路智能体——**图形界面**里交任务,它自己读代码、改文件、跑命令,直到把事情做完;像学长带学弟一样,动手前讲思路,关键处给理由。附赠同核引擎的终端版。

## 功能

- **桌面 GUI**:暮色玻璃风(全屏校园黄昏航拍做背景,侧栏/顶栏/气泡/工具卡都是磨砂玻璃层),深色/浅色一键切换;会话列表、Markdown 流式对话;**思考与工具调用收成一条可折叠的「工作过程」**(最终回复开始时自动收起,点标题行展开回看);**划词即弹「复制选区」**,消息右键有原生菜单(撤销/剪切/复制/粘贴/全选),Ctrl+C/V/X/A 正常可用
- **后台任务**:新建任务 / 切换会话都**不会打断**正在跑的任务——每个会话有自己的运行器(独立 Agent 与会话上下文),旧任务转后台继续跑完;侧栏对应会话显示转圈与「运行中」,点它即可切回去看实时进度(模型、MCP 连接、沙箱策略在会话间共享,多开一个会话不会重复起进程)
- **内置校园模型,开箱即用**:固定使用广州应用科技学院内网模型服务(`deepseek-v4-flash`),不需要填 API Key,界面也不提供模型切换入口(顶栏模型标识只展示;内网地址与 Key 都不下发前端)。**模型的上下文窗口与「是否支持视觉」由程序启动时自动向服务探测**(结论缓存一周),任务面板标签与压缩触发阈值据此自动对齐 —— 学校换模型/换网关不必改代码
- **工作区(自己选文件夹)**:顶栏「📁 工作区」胶囊点开就能挑一个文件夹当 Agent 的工作台——读写围栏、附件 `uploads/`、计划 `plans/`、长期记忆 `memory/` 全部落在那个文件夹里;弹窗列出当前/默认与最近用过的 8 个(可一键移出列表,只动记录不删文件夹),「选择文件夹…」走系统原生对话框。CLI 用 `/workspace <路径>` 或启动参数 `--workspace` 拿到同一份记忆(详见「工作区」一节)
- **沙箱边界**:工作区内的路径直接放行、工作区外逐次确认、`~/.yinluxs` `~/.ssh` 等敏感目录永远拒之门外;还能禁网命令、加自定义禁配规则、卡命令超时;GUI 顶栏一键开关(详见「沙箱」一节)
- **附件上传**:聊天框 📎 选文件/图片,自动复制进工作区 `uploads/` 并把路径注入消息;图片按长边 1536 / 1MB 自动压缩,`vision: true` 时以 base64 直接发给视觉模型
- **本地知识库检索**:`kb/` 目录放 JSON 资料库(校园通知、经验库等,自带 credibility 可信度分级与来源网址),`search_knowledge` 工具按 中文 2-gram 打分检索(标题×3+标签×2+正文×1),回答引用时输出来源网址供用户核实,非 A 级数据强制标注"未经核实"
- **图书馆馆藏查询(离线全库)**:`library_search` 直接查本地 SQLite 馆藏库(133 万条 OPAC 书目,毫秒级),返回书名/作者/出版社/索书号/中图法/馆藏册数与馆藏地,并自动把中图法大类映射成楼层。**查询会自动逐级放宽**:`《C程序设计》`、`C语言程序设计（第5版）`、`高等数学（上）`、`三体：地球往事` 这类带书名号/版次标注/副标题的问法都能命中(以前会漏检并误报"没有这一本书"),且一次检索最多只走一次全表 LIKE,不会因多试几级而变慢
- **SKILL.md 技能系统**:`skills/` 目录放技能(带 name/description 的 SKILL.md),自动注入系统提示词,模型用 `use_skill` 工具按需读取全文;内置 git-commit / python-testing / code-review 三个示例
- **权限确认**:写文件(带预览)、编辑(带 diff)、执行命令先弹窗确认,可选"本轮总是允许"或切"自动放行"
- **安全兜底**:`rm -rf /`、`format c:`、fork 炸弹等灾难性命令硬拒绝;文件工具拒绝越出工作区
- **`/undo` 回滚**(CLI):写前快照,可连续撤销
- **上下文自动压缩**:token 估算接近窗口上限时,把早期对话折叠成摘要(按完整轮切分)
- **会话持久化**:每轮写入 `sessions/*.jsonl`;GUI 侧栏一键恢复,CLI `/resume`
- **桌面提醒(v0.9.25)**:主动推送(今日简报、课前 20 分钟、今天第一节课、临期/逾期待办、校历倒计时、官网新通知、新版本)**除了聊天栏顶部那条提示栏,同时弹一条 Windows 桌面通知**——窗口最小化在托盘里也能收到,不会再出现"人不在窗口前 = 提醒没发生"。正文**只复用聊天里已有的那行文字**,缺字段就整条不发,绝不为了"通知看着饱满"编课名或时间;托盘菜单里有「桌面提醒」总开关(打勾状态实时)和「测试一条桌面通知」;对话里也能说「提醒直接弹桌面」「别弹通知」「看不到通知」,走 `desktop_notify` 工具(status / on / off / test,可只动一类)。**两条默认克制**:窗口正在前景有焦点时不重复弹(那行字本来就读得到)、同一条内容 ~90 秒内只响一次;拿不准是否在前景时按"不在前景"处理(宁可多弹,不能把"快上课了"悄悄吃掉)。偏好落 `notify-pref.json`(坏文件回落默认全开,只读目录写不进去会如实回话)。**通道是 Windows 自己的 toast**(借系统自带的 PowerShell 5.1 走 WinRT,零新依赖;正文只经环境变量交付、不进命令行),第一次弹时自动在当前用户下登记应用身份「引路学长」,通知里就有中文名和图标;旧的托盘气球只作回落,**且永远标着「未验证」—— Windows 11 根本不显示气球**,两条通道都不通时直说「都没发出去」并指向 `tray.log`,绝不假装已弹
- **校园地图(肇庆校区,手绘版)**:用线下交付的手绘地图 + 36 张单栋标注图做"位置图"——问「图书馆在哪」「E栋怎么走」,agent 会把该建筑在手绘地图上框出来、并给出它的单栋标注图(整图 + 一个红框),面板支持滚轮缩放/拖动平移/点框看大图;也能**不用问模型自己找**:面板里有搜索框(支持 E栋、s5 这类俗称与「食堂」这类类别词,命中多处会全列出来让你挑)、图例上的类别徽章一点即筛(只看食堂/只看宿舍)、邻近建筑可点跳转、一键复制位置说明;首页首位的「🗺 打开校园地图」按钮点开即是地图并自动聚焦搜索框。**A栋~E栋在地图上是两栋楼(学思苑X / 集贤苑X座),工具会返回歧义并把候选都摆出来,绝不自作主张挑一个**。这份数据没有路网,所以只答"在哪、旁边是什么",不编路线与步行时间。数据是 `frontend/campus-index.json`(交付包索引副本),底图与建筑图由 `scripts/build_campus_assets.py` 从交付 PNG 生成
- **权限确认解释"为什么"**:确认框顶部给出「这一步为什么要做 / 这一步会做什么 / 影响」——目的优先取模型填的 `reason`,没填就用同一轮它写的说明文字,再不行按命令形态做启发式解读(如"用 Python 读取并解析数据文件(一条命令里含多个步骤)"),并标出影响面(只读 / 会写文件 / 会联网 / 删除不可恢复 / bash 改动不在撤销范围)
- **36 个内置工具**:文件与命令 `read_file` / `write_file` / `edit_file` / `bash` / `py_script` / `glob` / `grep` + 联网 `web_crawl` / `web_search` + 浏览器 `browser_open` / `browser_eval` / `browser_snapshot` / `browser_click` / `browser_fill` / `browser_screenshot` / `browser_close` / `browser_status` / `page_parse`(把页面表格结构化) + 计划与技能 `plan` / `use_skill` + 知识与校园 `search_knowledge` / `kb_suggest` / `campus_calendar` / `gpa_calc` / `library_search` / `library_guide` + 个人数据 `memory` / `reminder` / `schedule` / `get_weather` / `desktop_notify`(桌面提醒开关与试弹) + 地图 `map_find_place` / `map_show_list` / `map_highlight_building` / `map_drop_pin` / `map_clear`;另有 `task`(子代理派发,由应用装配层注入)
- **计划依赖图**:`plans/plan.md` 的步骤可以声明 `depends_on`(文件里落成 `- 依赖: 1, 2`),写入时校验未知依赖/自依赖/环(不合法就不落盘);读计划时附一份「调度视图」——哪些步可执行(依赖已满足,可并行)、哪些被上游失败卡住(标 `[!]` 后下游自动判卡住,不会静默跳过)。老计划文件(无依赖行)照常读写
- **GUI 计划面板**(v0.9.24):顶栏「🗂 计划」(带 `已完成/总数` 角标)打开后是进度条 + 「现在能动」与「被上游失败卡住」两栏 + 每步的依赖与备注;模型每 `plan(action=update)` 一次面板就自己动(`plan_changed` 推送)。**面板严格只读**——打勾仍由 plan 工具写文件,免得"文件即状态"变成两处真相;「↩ 派发到输入框」把「接着做哪几步」按依赖顺序填进输入框,用户过一眼再发(**刻意不并发派写子代理**:并发写会互相踩文件、确认门还会串到别的答案上)
- **只读子代理并行派发**:一次要查多件互不相干的事时,`task` 的 `type` 与 `task` 都传数组就能并发(上限 4 个,报告按请求顺序合并)。**只对只读类型 explore/review 开放**——写类型并发会争抢同一条权限确认通道,混入会直接报错;并发时子代理的流式输出与工具卡片被静默(toast 与错误照常透传),避免几路 `text_delta` 在同一聊天区里互相穿插
- **网页抓取**:`web_crawl` 抓网页/整站为 markdown(同域 BFS 跟链、限页限速),入库前自动去掉每页重复的导航/页脚;可选 to_kb 一键写入本地知识库 `kb/crawl-<域名>.json`,用 `kb_category`/`kb_tags` 打主题标签(检索时标签权重×2),由 search_knowledge 带来源网址检索。源码环境用 crawl4ai(真浏览器,能渲染 JS);**打包版自动切到内置轻量抓取器(httpx + BeautifulSoup)**,不带浏览器内核(否则 79MB 的包会涨到约 500MB),静态站点效果一致,需要 JS 的页面会明确提示(或改用下面的浏览器能力)
- **浏览器能力(CDP 直连,不背浏览器内核)**:给 agent 装上"眼睛"——它能真的开一个浏览器看页面,而不只能抓静态 HTML。`browser_open` 返回 **JS 渲染完成后**的标题与正文(教务/通知页这类靠 JS 渲染的站点终于是真内容);`browser_eval` 在页面里跑一段 JS 精确取数(价格/表格/链接列表)或点按钮、滚动;**页面交互三件套**:`browser_snapshot` 列出页面上所有可见可交互元素(链接/按钮/输入框/下拉框,带序号与字段名,只读不弹确认),`browser_click` 按快照序号/按钮文字/CSS 选择器点击并自动回报点击后的页面状态(序号是 snapshot 在页面元素上打的临时标记——页面一变标记就失效,**永远不会点错元素**;页面上有多个同名按钮时拒绝盲点),`browser_fill` 按字段名批量填表单(name/id/占位符/aria/标签文字都认,下拉框按选项文字匹配、勾选框接受 true/false),文本框用「原生 setter + input/change 事件」写入——React/Vue 的受控组件也能感知到变化;可选 `submit=true` 全部填好后自动点提交,任何字段失败都不会提交半张表单;`browser_screenshot` 把页面截成 PNG 存进工作区,**图直接出现在对话里**(点图可放大);`browser_close` 收标签页。两条接入路线:**launch**(默认,零配置:自己找 Edge/Chrome 用独立 profile 起一个无窗口实例)与 **attach**(连用户自己开了远程调试的浏览器,**带他的登录态**,能看只看得到登录用户的页面)。**attach 只在显式 `mode="attach"` 或设了 `YINLUXS_CDP_URL` 时才用**——默认不动用户那个有几十个标签页和真实登录态的浏览器,对分发型程序太侵入,实测也更容易卡住(所以 auto 探到调试端口也会先走 launch,只有本机起不了浏览器时才拿 attach 兜底)。attach 的探测要**三级兜底**:`127.0.0.1:<port>/json/version` → `localhost:<port>` → **直接读用户目录里的 `DevToolsActivePort` 文件第二行**;实测本机 Edge 153 对 `/json/*` 一律返回 404,只有文件里那条 ws 路径连得上,只依赖 HTTP 探测就会永远报"没找到开着远程调试的浏览器"。且**探到端口不等于能用**(残留文件/端口被别的程序占),要真跑一次 `Browser.getVersion` 握手才算成立。靠纯 Python 的 `websockets` 说 CDP,所以轻量版安装包**不**需要 playwright 与 270MB 内核。安全三条:只允许 http/https(`file://`、`javascript:`、`chrome://` 一律拒);每个线程/会话/子代理**各自一个标签页**,并发不串台,已结束线程留下的页会被回收;网页版(对外只读)**不给浏览器工具**(可被用来打内网 + 在服务器上起浏览器)。终端 `/browser` 看当前能不能用、走的哪条路线
- **校园主题语料库**:`scripts/crawl_school_topics.py` 按"用户会问什么"分主题爬官网(奖助学金 / 入党党建 / 团学推优 / 学科竞赛 / 证书与等级考试 / 考研升学 / 转专业与专业选择 / 选课与学分 / 教务考试 / 就业与考公考编 / 榜样风采 / 心理与情绪 / 学风与学习方法 / 招生专升本 / 校园活动与文体 / 宿舍与生活服务 / 社团与学生组织 / 军训与国防教育 / 校园新闻动态 / 学生故事与风采 / 学生事务办理 / 校园讲座与专题 / 视频与图文校园 / 服务大厅与办事入口,共 24 个),关键词过滤 + 翻页 + 发布日期提取 + 去导航,产出 `kb/school-gzasc.json`(每条都带原文链接与发布日期;视频页与图文指引页也建条目,正文里给出观看/查看链接),换学校只改 `scripts/school_topics.json`,不用改代码
- **OpenAI 兼容内核**:底层走 OpenAI 协议(流式 + 工具调用),所以默认的校园模型之外,任何 OpenAI 兼容服务(DeepSeek / Kimi / Qwen / 智谱)都能在源码层接;本地服务(127.0.0.1)与校园内网(10.x)自动绕过系统代理

- **网页版(校园助手)**:一条命令把同一套前端搬到浏览器(`python web_main.py`)——同学打开网址就能用,只读白名单(校园知识库/图书馆/校园地图),每访客独立会话与留档、限流与并发上限、同源校验(详见「网页版」一节)

## 快速开始(源码运行)

```bash
cd yinlu-xuezhang
py -3.12 -m venv .venv                     # Python 3.10+
.venv\Scripts\activate
pip install -r requirements.txt
crawl4ai-setup                            # web_crawl 首次使用前执行,下载浏览器内核

copy config.example.json config.json       # 一般不用改:默认就用内置校园模型

python gui_main.py                         # 图形界面
python main.py                             # 终端 REPL
```

### 常见模型的 base_url(仅源码层自定义时参考)

> 校内分发版固定用内置校园模型,下面这些只在你要自己改造源码、替换默认模型时才用得上
> (`config.json` 里的其他供应商配置会被保留但不会激活)。

| 供应商 | base_url | model 示例 |
|---|---|---|
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-4.6` |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| Kimi | `https://api.moonshot.cn/v1` | `kimi-k2-0905-preview` |
| Qwen | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen3-coder-plus` |

## 用法

```bash
python gui_main.py                  # 图形界面
python main.py                      # 交互式 REPL
python main.py "修复 pytest 报错"    # 一次性执行
echo "列出 TODO" | python main.py   # 管道输入
python main.py --workspace "D:\我的资料"   # 指定工作区启动(路径不合法直接报错退出)
```

CLI 命令:`/help` `/clear` `/model`(供应商列表)`/history` `/resume [<n>]` `/undo` `/compact`
`/sandbox` `/workspace [路径|default]` `/memory` `/remember` `/dream` `/skills` `/kb`(知识库统计)`/plan`
`/library`(馆藏库状态)`/usage [1|7|30]`(token 用量)`/browser`(浏览器能力状态)`/exit`;
`Ctrl+C` 中断当前回合。

## 网页版(校园助手)

同学打开网址就能用,不用装任何东西。**只读问答**:校园知识库 / 图书馆馆藏 / 校园地图 / 技能说明,没有文件与命令能力。

```bash
python web_main.py                  # 默认监听 0.0.0.0:8480,同学从校园网访问
python web_main.py --open            # 顺带用本机浏览器打开
python web_main.py --host 127.0.0.1  # 只给自己用
python main.py --web                 # 等价入口
```

- **同一套前端**:`frontend/index.html` 一行不改 —— 桥接 shim `frontend/bridge-web.js` 在服务时注入,把 `pywebview.api` 换成 `POST /api/<方法>`,把引擎事件用 SSE(`GET /api/events`)送到页面。
- **引擎零改动**:`agent.py` / `llm.py` / `tools/*` / `sandbox.py` / `session.py` 全部原样复用;新增的只有 `web_ui.py`(队列推送)、`web_profile.py`(白名单与门面)、`web_server.py`(HTTP/SSE)。
- **只读白名单**:`web_profile.PUBLIC_TOOLS` 显式列举(frozenset),**不用 `Tool.read_only` 过滤** —— 那个标志的语义是"执行前不需要确认",不是"读不到敏感数据"(`read_file`/`grep` 都是 `read_only=True`,而工作区里就有含内置 Key 的 `config.local.json`)。
- **多访客隔离**:每个浏览器一个 cookie 身份,各自的会话、Agent、事件队列与留档目录(`web-sessions/<id>/`),互相看不到对方的对话。
- **滥用控制**:每访客 12 次/分钟、全局同时 3 个回合(超了回"前面还有 N 位同学")、每日 2000 次上限;发问与来源 IP 记在 `web-usage.log`。
- **跨站防护**:校验 `Origin`/`Referer` 与 `Host` 同源 —— 否则你浏览器里任意网站都能朝这台服务器发请求。
- **部署注意**:服务必须跑在**能访问校园内网模型端点**的机器上(公网云主机连不通 `10.x`);监听非本机地址时,同网段任何人都能用本服务并消耗学校模型额度。只读白名单下没有 shell,但请勿把非只读版本开到公网。

更完整的设计与安全取舍见 `D:\game\agent网页版-实施方案.md`;开发时看效果可用 `py -3.12 _tmp_dev_check/serve_web_e2e.py --port 8481`(带一个本地假模型,不连校园网)。

## 技能(SKILL.md)

把技能放进程序目录的 `skills/`(每个技能一个文件夹,内含 `SKILL.md`):

```markdown
---
name: git-commit
description: 生成符合规范的 git 提交信息
---
(给模型的指导正文……)
```

系统提示词会列出可用技能,模型接到相关任务时自动用 `use_skill` 读取全文后再行动。CLI 用 `/skills` 查看已装技能。

## 沙箱

Agent 能碰到什么,由三层边界决定:

| 边界 | 行为 |
|---|---|
| 敏感路径(`~/.yinluxs`、`~/.ssh`、`~/.aws`、`~/.gnupg` …) | **永远拒绝**,任何权限模式、任何用户授权都不能越过 |
| 工作区内路径 | 直接放行 |
| 工作区外路径 | 默认**逐次确认**;用户放行后本回合内有效;`allow_outside_workspace: true` 则直接放行 |

命令侧:灾难命令照旧硬拒绝(`rm -rf /`、`format c:`、fork 炸弹…);沙箱还支持
`allow_network: false` 禁掉 curl/wget/ssh 等联网命令、`deny_patterns` 加自定义正则
(如 `git push`)、`timeout_seconds` 收紧命令执行超时上限。

GUI 顶栏的「🛡 沙箱开/关」可随时切换(写回 config.json);CLI 用 `/sandbox [on|off]` 查看或临时切换。
注意:**沙箱边界优先于权限模式**——`accept_all` 也不能让 Agent 在没有确认的情况下访问工作区外。
沙箱判的「工作区」就是「工作区」那一节选定的文件夹:**换工作区,围栏跟着换**,
且切换时会清掉本回合已放行的工作区外路径(不把旧授权带到新工作区)。

## 工作区

**Agent 在哪个文件夹里干活,由你定。**

| 入口 | 说明 |
|---|---|
| GUI 顶栏「📁 工作区」 | 弹窗里选:当前 / 默认 / 最近用过;点一行即切过去 |
| CLI `/workspace` | 无参=查看当前与最近;`/workspace <文件夹路径>`=切换;`/workspace default`=回默认 |
| CLI 启动参数 | `--workspace "D:\我的资料"` / `-w`(也可写 `--workspace=...`) |

- **默认工作区** = 程序目录(exe 旁 / 开发态仓库根),也就是改造前写死的那个,老用户无感。
- **只接受已存在的文件夹**:不做隐式创建(免得在用户盘上凭空长出目录);
  `~/.yinluxs`、`~/.ssh`、系统凭据目录这类放钥匙的地方**永远不能**当工作区。
- **切换的代价说清楚**:旧会话运行器一律停掉(它的工具上下文与 system prompt 都是按旧工作区装的),
  从新会话开始;有任务在跑时**直接拒绝**,不偷偷换掉正在运行的围栏。会话留档不丢,
  侧栏里不属于当前工作区的历史会话会标出 `📁 目录名`,提醒你那些文件不在当前文件夹里。
- **记忆是全局一份**(`workspaces.json`,跟 exe 走):GUI 与 CLI 读到的是同一个「上次在哪干活」;
  记录的文件夹被删/改名时静默回退默认工作区,绝不因为一个失效设置拦启动。
- 网页版(对外形态)**不开放**工作区管理:访客按 POST 调那四个方法一律被拒,前端也会把胶囊整个藏掉。

## 附件(文件 / 图片)

聊天框左侧 📎 打开系统文件选择框,可以多选:

- 文件先复制进工作区 `uploads/`(重名自动加 `-1`/`-2`),消息里注入相对路径清单,
  模型用 `read_file` / `bash` 等工具自行处理 —— PDF 这类二进制不会被硬塞进上下文;
- 图片另做压缩:长边超过 1536 等比缩小,JPEG/WebP 逐级降质量到 1MB 以内,
  带透明的 PNG 保持 PNG,GIF 保留动画;单文件上限 20MiB、解码后不超过 4000 万像素;
- `config.json` 里 `vision: true`(且模型确实支持看图)时,图片会以 base64 data URL
  随消息直接发给模型;否则只给路径并在消息里注明「当前模型不支持看图」。

## 长期记忆

跨会话记住四类事实:**用户偏好**(user)/**反馈纠正**(feedback)/**项目约定**(project)/
**参考资料**(reference)。分两级作用域:

| 作用域 | 存放位置 | 适用 |
|---|---|---|
| 项目级(默认) | 工作区 `memory/MEMORY.md` | 只跟这个项目有关:技术选型、目录约定、踩过的坑 |
| 用户级(`scope=user`) | `~/.yinluxs/memory/MEMORY.md` | 跨项目通用:个人偏好、沟通习惯、常用环境 |

两个文件都是**一条一行、人工可直接编辑**的 Markdown:

```markdown
- [2026-09-13][user] 喜欢先给结论再给理由
- [2026-09-13][project] 打包前必须先关掉运行中的实例
```

模型通过 `memory` 工具增删查(`save` / `list` / `search` / `forget`),系统提示词会自动
注入两级记忆(合计截断 4000 字,用户级在前)。

**整理去重(Dream `action=dream`)** —— 对齐星瑶 Y-Code 的 `/dream` 思路,分两步:

1. **确定性去重**:同类别内按中文 2-gram 相似度聚类(整体相似 ≥0.75,或短的一条被
   覆盖 ≥0.85 视为同一条),合并时保留信息最全的正文 + 较新的日期,完全重复直接丢弃;
2. **模型归纳**(有 LLM 时):让模型合并同义、删掉被后续条目推翻的,每条压成一句话,
   输出严格 JSON;解析失败或返回空就退回第 1 步的结果,绝不把记忆整理没了。

覆盖前会把原文件备份到同目录 `history/MEMORY-<时间>.md`,所以整理永远可回溯。

入口:GUI 顶栏「🧠 记忆」(点击整理两级)、CLI `/dream [project|user|all]`、
模型在用户说「整理记忆」时自行调用 `memory action=dream`。

## MCP 服务器

支持接入任意[MCP](https://modelcontextprotocol.io)服务器,外部工具会以
`mcp_<服务器>_<工具>` 的名字挂进工具集,和内置工具一样被模型调用。三种传输都支持:
**stdio**(给 `command`,子进程按行收发)、**http**(Streamable HTTP:给 `url`,POST 过去,
响应可能是 JSON 也可能是 `text/event-stream`,`Mcp-Session-Id` 会自动带上)、
**sse**(老式 HTTP+SSE:`GET` 一条长流拿到 `endpoint` 事件后再 POST 到那条地址)。
只有网址、没有启动命令的服务器从此也能连,而且**不额外依赖任何第三方库**(走标准库 `urllib`)。

在 `config.json` 里配置(完整示例见 `config.example.json`):

```json
"mcp_servers": [
  {
    "id": "filesystem",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-filesystem", "D:/data"],
    "enabled": true,
    "read_only": false,
    "description": "文件系统"
  },
  {
    "id": "remote-demo",
    "transport": "http",
    "url": "https://example.com/mcp",
    "headers": { "Authorization": "${MY_MCP_TOKEN}" },
    "enabled": true,
    "read_only": true,
    "description": "只有网址的服务器;密钥留在环境变量里,不写进 config.json"
  }
]
```

`headers` 里的 `${VAR}` 在**发请求时**才展开(`MY_MCP_TOKEN` 从环境变量取),配置文件中不落明文;
日志与报错里的 URL 一律**剥掉 query**,所以 token 不会顺着错误文案漏进对话。
`transport` 留空即按 `command` 走 stdio;`url` 缺失、`file:` 协议、不认识的 transport 会被跳过并说明。

| 字段 | 说明 | 默认 |
|---|---|---|
| `id` | 服务器标识,用于拼工具名(会规范化成字母数字下划线) | 必填 |
| `transport` | `stdio`(按 command 起子进程)/ `http` / `sse`;写 `streamable-http`、`http+sse` 等别名也认 | `stdio` |
| `command` / `args` | 启动命令,支持 `npx` / `uvx` / `python` 等(**stdio 时必填**) | 空 |
| `url` | 服务器地址(`http` / `sse` 时必填,只认 http/https) | 空 |
| `headers` | 附加请求头(值支持 `${VAR}` 展开,发请求时才代入) | 空 |
| `env` | 追加的环境变量(值支持 `%VAR%` / `$VAR` 展开) | 空 |
| `enabled` | 关掉可保留配置不启动 | `true` |
| `read_only` | 标记为只读 → 免确认直接执行;默认**需要用户确认** | `false` |
| `start_timeout` / `call_timeout` | 握手 / 调用超时(秒) | 10 / 60 |
| `description` | 显示名与工具描述前缀 | 空 |

行为约定:

- **连不上不崩应用**:命令不存在、握手超时、协议错误都只在日志里提示并跳过该服务器;
- **进程级长连接 + 退出回收**:窗口关闭 / CLI 退出时统一终止子进程,不留孤儿;
- 工具自带 JSON Schema,权限门按 `read_only` 决定免确认还是逐个确认;
- 侧栏底部会显示 `MCP 服务器名(工具数)`。

## 配置项(config.json)

| 字段 | 说明 | 默认 |
|---|---|---|
| `providers` | 供应商数组:`{id, name, base_url, api_key, model}`(**保留字段**:校内分发版固定用内置校园模型) | 内置校园模型 |
| `active_provider` | 同上,不参与切换;启动时一律指向内置校园模型 | `gzasc` |
| `permission_mode` | `confirm` 变更逐次确认;`accept_all` 自动放行 | `confirm` |
| `shell` | `auto` 自动选 Git Bash→cmd;可指定 `bash` / `cmd` / `powershell` | `auto` |
| `max_output_chars` | 工具输出回填给模型前的截断长度 | `20000` |
| `request_timeout_seconds` | 单次 LLM 请求超时 | `300` |
| `context_window` | 模型上下文窗口(token),用于压缩触发判断 | `131072` |
| `compact_at_ratio` | 估算 token 达窗口该比例时自动压缩 | `0.7` |
| `mcp_servers` | MCP 服务器数组(见上一节) | `[]` |
| `vision` | 当前模型是否支持看图:开启后上传的图片以 base64 随消息发送 | `false` |
| `sandbox` | 沙箱策略:`enabled` / `allow_outside_workspace` / `allow_network` / `timeout_seconds` / `deny_patterns` | 见「沙箱」一节 |

> **模型固定**:面向校内分发的版本只使用内置的广州应用科技学院模型
> (`gzasc` / `deepseek-v4-flash`),界面不提供切换入口。
> `config.json` 里若残留其他供应商配置会被保留(不删用户数据),但不会被激活;
> 顶栏模型标识是纯展示,不可点选。
>
> **内网地址不面向用户展示**:模型管理弹窗与顶栏对内置项只显示模型名,
> `base_url` 一律留空下发(见 `Bridge.get_providers` / `get_info`)。
>
> **能力自动探测**:上下文窗口与「是否支持视觉」由 `minicode/model_probe.py`
> 在启动时问一次模型服务 —— 窗口靠发一条 `max_tokens` 超限的请求、从服务端
> 拒绝文本里读真实上限(请求被拒=没生成 token,几乎不花钱);视觉靠发一张 1×1
> 图片看是否被回「not a multimodal model」。结论落 `model_caps.json`(缓存一周)
> 并发布给 `model_catalog`,任务面板的能力标签、窗口上限,以及**压缩触发阈值**
> 都会自动跟上,学校换模型/换网关不必改代码。探测走后台线程,失败只是"这次没
> 测出来",不阻塞启动。
>
> **内置模型的 Key 不随仓库分发**(本仓库公开):放在本地未跟踪的
> `config.local.json` 的 `builtin_api_key` 字段,或用环境变量
> `YINLUXS_BUILTIN_API_KEY` 临时覆盖。两者都没有时应用照常启动,只是内置模型
> 不可用;打包脚本会把 `config.local.json` 一并打进 exe,所以发给用户的包仍是开箱即用。
>
> ```json
> {"builtin_api_key": "sk-在这里填入内网模型的 Key"}
> ```
>
> 离线测试/调试可以用环境变量把当前端点临时指到别处(**不写回 config.json,
> 普通用户无需设置**),冻结版 e2e 脚本就是靠它把 exe 指到本地假 LLM:
>
> ```bash
> YINLUXS_LLM_BASE_URL=http://127.0.0.1:8000/v1 YINLUXS_LLM_MODEL=fake 引路学长-CLI.exe "任务"
> ```

## 项目结构

```
minicode/
├── agent.py        # Agent 主循环(回合内 模型↔工具 迭代)+ 快照回滚 + 压缩触发
├── app_setup.py    # CLI/GUI 共享装配:system prompt(含技能注入)+ Agent 构建
├── llm.py          # OpenAI 兼容流式调用 + 工具调用增量聚合 + 重试 + 本地代理绕行
├── skills.py       # SKILL.md 技能加载 + use_skill 工具处理器
├── mcp.py          # MCP 客户端:stdio / Streamable HTTP / HTTP+SSE 三种传输 + 工具发现 / 调用 / 进程回收
├── sandbox.py      # 沙箱策略:路径分级(工作区/外部/敏感)+ 命令管控 + 超时
├── workspace.py    # 工作区:选择校验(拒敏感目录)+ 最近列表持久化 + 启动回退
├── attachments.py  # 附件:落盘去重 / 图片压缩 / 多模态消息构造
├── suggestions.py  # 首页建议 chips:按聊天记录给校园主题打分
├── kb.py           # 本地知识库:异构 JSON 加载 + 2-gram 检索 + 来源网址输出
├── compaction.py   # token 估算 + 按完整轮切分的上下文压缩
├── campus.py       # 校园地图:建筑索引加载 + 地名解析(四态)+ map_* 工具
├── explain.py      # 权限确认框的"为什么要做这一步":命令人话解读 + 影响面提示
├── permission.py   # 权限门 + 灾难命令黑名单
├── session.py      # 消息历史 + JSONL 留档 + 会话恢复
├── paths.py        # 源码/打包双形态路径解析
├── ui.py           # 终端 UI:rich 流式 Markdown / 工具卡片 / 确认
├── gui_ui.py       # GUI 适配器:引擎事件 → webview 推送,停止=中断
├── gui_bridge.py   # pywebview js_api(门面对象)+ 供应商/密钥管理
├── config.py       # 多供应商配置:解析/迁移/保存
└── tools/          # base(Tool 定义与路径安全)+ fs / shell / search / crawl / registry
main.py             # CLI 入口与 REPL
gui_main.py         # GUI 入口(pywebview 窗口)
frontend/           # 免构建单页前端(暮色玻璃风,深浅双色;img/campus_dusk.jpg 为背景图)
frontend/map.js     # 校园地图面板:手绘底图 + 标注框/图钉/列表 + 标注图裁切窗与灯箱(零依赖)
frontend/campus-index.json # 交付包索引副本(36 处建筑 + 203 条别名 + 20 条歧义 + 10 处非建筑)
frontend/img/campus/      # map.jpg(手绘底图原图) + 1280/NN.webp(36 张单栋标注图,约 3.8MB)
skills/             # 内置技能(git-commit / python-testing / code-review / school-qa)
kb/                 # 本地知识库 JSON(notices / knowledge / school-gzasc,带来源网址与可信度)
packaging/gcode.spec  # PyInstaller 双 exe 配置
scripts/            # 打包与端到端验证脚本 + 校园主题爬虫 + build_campus_assets.py(生成本地地图资源)
```

## 校园主题语料库

```bash
python scripts/crawl_school_topics.py --dry-run            # 只看会抓哪些链接
python scripts/crawl_school_topics.py --only 奖助学金       # 只刷新一个主题
python scripts/crawl_school_topics.py                      # 全量刷新(24 个主题,约 8 分钟)
python scripts/crawl_school_topics.py --prune              # 清理已入库、但被 exclude 关键词排除的条目(离线)
```
爬取结果:正文归档到 `crawled/www.gzasc.edu.cn/school-qa/`(文件名带主题前缀,附
`index.json` 清单),知识库条目写入 `kb/school-gzasc.json`(按 url 合并,重爬只更新
不丢旧数据)。学生问校园问题时,`search_knowledge` 会直接命中这些带原文链接的条目;
问细节时可加大 `snippet_chars` 一次取回更多原文。

栏目按"学生会问什么"划分:奖助学金 / 入党党建 / 团学推优 / 学科竞赛 / 证书与等级考试 /
考研升学 / 转专业与专业选择 / 选课与学分 / 教务考试 / 就业与考公考编 / 榜样风采 /
心理与情绪 / 学风与学习方法 / 招生专升本 / 校园活动与文体 / 宿舍与生活服务 /
社团与学生组织 / 军训与国防教育 / 校园新闻动态 / 学生故事与风采 / 学生事务办理 /
校园讲座与专题 / 视频与图文校园 / 服务大厅与办事入口。

正文不在文字里的页面也建成条目,保证链接能送到学生手上:

| 页面类型 | 抓到的文字 | 条目正文 |
|---|---|---|
| 正文全在视频播放器 | 0 字 | 标题 + 发布日期 + `观看地址:` 原页链接 |
| 二维码/图文操作指引(热水充值、报修、校园服务公众号) | 0 字 | 标题 + 发布日期 + `查看地址:` 原页链接 |
| 只有附件的管理办法/培养方案 | 附件名 | 标题 + 发布日期 + 附件名清单 + 原页链接 |
| 失效链接(官网把 404 也渲染成 200) | 提示文字 | 不入库(靠标题识别拦掉) |

## 两个便携版(轻量版 / 完整版)

打包版联网抓取有两个引擎:轻量版不带 crawl4ai,走内置抓取器(httpx + BeautifulSoup);
完整版把 crawl4ai 与浏览器内核一起打进去,能抓需要 JS 渲染的页面。运行时自动选择,
降级时会在工具输出里说明原因。

```bash
python scripts/build_exe.py            # 轻量版:文件夹 + zip + 单文件版 + 安装包
python scripts/build_crawl4ai_exe.py   # 完整版:文件夹 + zip + 安装包(需本机有浏览器内核)
```

| | 轻量版 | 完整版 |
| --- | --- | --- |
| 产物 | `引路学长-<版本>-win64.zip`(便携)<br>`引路学长-<版本>-setup.exe`(安装包)<br>`引路学长-单文件版.exe` | `引路学长-<版本>-crawl4ai-win64.zip`(便携)<br>`引路学长-<版本>-crawl4ai-setup.exe`(安装包) |
| 抓取引擎 | 内置抓取器(无浏览器) | crawl4ai(真浏览器,可渲染 JS) |
| 解压后体积 / zip | 约 110MB / 76MB | 约 660MB / 281MB |
| 浏览器内核 | 无 | `ms-playwright/chromium_headless_shell-*`(272MB,放在 exe 同级,不塞进 exe) |
| 适用 | 日常分发、静态站点抓取 | 需要抓 JS 页面时 |

两个安装包用不同的 `AppId`,可以**同时装在一台电脑上互不覆盖**(完整版装到
`%LOCALAPPDATA%\Programs\引路学长-完整版`);便携版则是把整个文件夹拷走就能用,
配置与会话都在 exe 同目录。

完整版的浏览器内核目录可以单独拷进轻量版目录(与 exe 同级),即把轻量版升级成完整版;
反过来删掉 `ms-playwright/` 就会自动降级,不会报错。

验收(两版都可以用同一个脚本验,断言期望的引擎):

```bash
python scripts/e2e_frozen_crawl.py
python scripts/e2e_frozen_crawl.py --exe dist/引路学长-crawl4ai/引路学长-CLI.exe --expect-engine crawl4ai
```

## 测试

```bash
python -m pytest                        # 单元测试,不依赖网络
python scripts/e2e_fake_server.py       # CLI 端到端冒烟(假服务器,不花 token)
python scripts/e2e_frozen.py            # 打包后验证:CLI 全链路 + GUI 启动
python scripts/e2e_frozen_crawl.py      # 打包后验证:联网爬取 → 自动入库 → 下次提问命中
python scripts/e2e_frozen_mcp.py        # 打包后验证:MCP 服务器连上 → 调工具 → 结果回填模型
python scripts/e2e_frozen_mcp.py --exe dist/引路学长-crawl4ai/引路学长-CLI.exe
python scripts/e2e_frozen_sandbox.py    # 打包后验证:工作区外访问触发确认且拒绝后不泄露内容
python scripts/e2e_web.py               # 网页版:静态/shim/越权拒绝/跨站拦截/SSE 回合全链路
python scripts/e2e_map_reset.py         # 前端:切会话后地图状态是否清零(B12 回归,真实浏览器)
python scripts/e2e_browser.py           # 浏览器能力:真 Edge/Chrome 全链路(渲染后取正文/取数/截图/并发隔离/file 拒绝)
python scripts/e2e_browser.py --attach  # 同上,但改用已开远程调试的浏览器(带登录态那条路线)
python scripts/e2e_browser_shot.py      # 前端:Agent 截的图是否真的画进对话(取图/放大/后台会话不串台)
python scripts/e2e_workspace.py         # 前端:工作区胶囊/弹窗/切换/恢复默认/选文件夹/移除/网页版隐藏
python scripts/e2e_frozen_library.py    # 打包后验证:用包内 1.3 GB 真库查书(书名号/版次标注都能命中)
```

## 打包发布

```bash
pip install -r requirements-dev.txt
python scripts/build_exe.py        # 构建 dist/引路学长/ 并生成 引路学长-<版本>-win64.zip
python scripts/e2e_frozen.py       # 对 exe 做端到端验证
```

产物三件套(dist/ 下):
- **引路学长-<版本>-setup.exe** —— Windows 安装包(推荐分享):中文向导、
  仅当前用户安装(无需管理员)、自动创建桌面/开始菜单快捷方式、带卸载器
- **引路学长-单文件版.exe** —— 免安装单文件,双击直接用(启动稍慢)
- **引路学长-0.x-win64.zip** —— 绿色便携 zip,解压即用

安装包内置全部三种 exe 形态对应的 GUI 版;`config.json` 首次启动生成在安装目录;
用户数据(config/sessions/memory)卸载时保留。

`config.json` 在 exe 旁自动生成;图标、system_prompt、前端页面、内置技能已打包。

## 路线图(下一阶段)

- [x] MCP 客户端(配置即用,连不上不阻塞启动);传输三种齐了(stdio / Streamable HTTP / HTTP+SSE,v0.9.24)
      - 待做:`mcp_add_server` 让模型自助配置、预设模板库、断线重连与重试
- [x] 长期记忆(用户偏好/项目约定,`memory/MEMORY.md`)
- [x] 长期记忆整理:去重 / Dream 式归并,用户级全局记忆(`~/.yinluxs/memory/`)
- [x] 子代理(`task` 工具):explore / implement / review 分工,**只读类型并行派发**(上限 4 个)
- [x] 计划模式:复杂任务先建计划,再按 `depends_on` 依赖图执行(含可执行/卡住调度视图)
- [x] 工作区:自己选文件夹当工作台(GUI 胶囊 / CLI `/workspace` / `--workspace`),围栏与会话跟着走
- [ ] 写子代理并行:需要把确认通道改成“每个子代理独立确认队列”
- [ ] MCP 增强:HTTP/SSE 传输、AI 自助配置服务器(星瑶的 `mcp_add_server` 一类)
- [ ] 工作区进阶:按工作区分组的会话视图与“随会话自动切回它的工作区”

## License

[MIT](LICENSE)
