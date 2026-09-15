# v0.9.01 开发记录(移植星瑶 Y-code 已验证做法)

> 参考仓库:`https://gitee.com/feng-chenhao/xingyao-y-code`
> (本地只读副本 `D:/game/.fetch/ycode-ref`,HEAD=b00ed5b / dev / v1.2.26)。
> 基线:`mini-code v0.9.0`(87ad10b)。本版目标版本号 **v0.9.01**。
> 本文是本轮开发的需求、决策与验证的单一事实源。

## 本轮范围(5 个工作包,按风险从低到高)

### WP1 · 会话级状态隔离(收掉 B12 遗留 bug)✅

B12 的根因:新建/切换会话时后端推 `cleared` / `resumed` 事件,前端只重置了聊天区,
地图模块的 `boxes`/`pins`/`activeCat`/搜索框/详情卡是 `map.js` 里的模块级变量,
跨会话一直留着,于是"上一轮标的楼"会串到新会话里。

| 编号 | 内容 | 落点 |
| --- | --- | --- |
| S1 | 地图模块新增 `reset()`,清框/图钉/筛选/搜索/卡片/详情卡 | `frontend/map.js` |
| S2 | 前端 `resetMap()` 在 `cleared` 与 `resumed` 两个分支调用 | `frontend/index.html` |
| S3 | `reset()` 不复用 `apply({op:'clear'})`——它第一行 `openPanel()` 会把地图又弹开 | `frontend/map.js` |
| S4 | 面板隐藏时 `rect()` 宽高为 0,`fitAll()` 会算出歪缩放 → 改为回默认视角 | `frontend/map.js` |

### WP1b · Git Bash 探测(修掉一个既有的环境性失败)✅

`shell._pick_bash()` 原来只认 `C:\Program Files\Git` 两个固定路径。Git 装在非默认
盘符(本机是 `D:\git\Git`)时 `resolve_shell_argv("auto")` 返回 `None` → 静默退回 cmd,
于是 `sleep`/`grep` 这类命令直接报「不是内部或外部命令」,而且沙箱超时上限根本
测不出来(`test_shell_timeout_clamped_by_sandbox` 因此在改前是红的)。

新增 `_bash_beside_git()`:从 `shutil.which("git")` 反推安装根目录(兼顾
`<root>\cmd\git.exe` 的安装包布局与多一层的其它布局),再找 `<root>\bin\bash.exe`
或 `<root>\usr\bin\bash.exe`,并继续排除 System32 下的 WSL bash。

### WP2 · 计划依赖图 ✅

对齐星瑶 `core/plan.py` 的 `depends_on` + ready/blocked 判定,移植到引路学长更轻的
`plans/plan.md` 文件式计划上(不分库,保持"文件即状态、可进版本库")。

| 编号 | 内容 | 验收 |
| --- | --- | --- |
| P1 | 步骤支持 `depends_on:[编号]`,文件里落成 `  - 依赖: 1, 2`;老计划(无依赖行)照常解析 | 往返一致 + 老文件兼容 |
| P2 | 建图校验:未知依赖 / 自依赖 / 环 → 明确报错**不落盘** | 四类坏图各一条用例 |
| P3 | `ready_steps()` / `blocked_steps()`:依赖已满足的 pending 步骤、被上游失败卡住的步骤 | 1→2/4 逐个解锁,2 失败后 3 卡住 |
| P4 | `status` 扩到 `pending/done/failed`(文件标记 `[ ]`/`[x]`/`[!]`);`read`/`write`/`update` 都附调度视图 | 手动标 failed 后下游自动判卡住 |

顺带修掉两处既有缺陷:① `update` 的备注原来会被写到**每一步**下面;② 备注在
重写时丢失(解析不认备注行)。

### WP3 · 只读子代理并行派发 ✅

对齐星瑶 `core/tools.py` 的 `BoundedSemaphore(_MAX_CONCURRENT_SUBAGENTS=5)`,
本版上限取 4(`MAX_PARALLEL_TASKS`)。

**关键安全决策:只对只读类型(explore/review)并发。** 理由——`PermissionGate.check()`
里 `read_only` 工具直接 `ALLOW`,不走确认握手;写类型并发会争抢同一条确认通道,
把"哪个子代理在等确认"搅乱。批量里混进写类型直接报错,不做静默降级。

| 编号 | 内容 | 验收 |
| --- | --- | --- |
| C1 | `type`/`task` 传数组即批量(`ThreadPoolExecutor`,上限 4);传字符串仍是单发 | 单发行为与 v0.9 完全一致 |
| C2 | 批量里混入写类型 → 明确报错并说明原因 | `implement` 被拒用例 |
| C3 | 批量用 `_QuietUI` 吞掉子代理的流式输出与工具卡片,只回填合并报告 | 子代理文本不出现于 `text_delta` |
| C4 | 报告按**请求顺序**合并(与完成先后无关) | 乱序完成仍按 types 顺序 |
| C5 | `Bridge.confirm_tool` 加确认锁:并发要确认时排队,不再互抢 event/choice | 只读子代理越界读文件时仍安全 |

### WP4 · 图书馆查书"查不到"修复(用户报障) ✅

**现象**:学生问某一本书,即使馆里有,也回"没有这一本书"。

**根因(本机 1.3 GB 真实库上复现并定位)**:检索把整个查询串当字面量,而学生输入常带
书名号与版次标注、库里标题未必有:

| 用户输入 | 库里实际标题 | 修复前 |
| --- | --- | --- |
| `C语言程序设计（第5版）` | `C语言程序设计 : 现代方法 : a modern approach. 第2版` | **0 命中** |
| `高等数学（上）` | `高等数学. 上册. 第2版` | **0 命中** |
| `《C程序设计》` | `C程序设计` | **0 命中** |
| `《三体》` | `三体 : 图文版` | 命中但答错书(返回 `《三体》中的物理学`) |

三条子路径叠加导致漏检:① FTS5 trigram 把整串当**连续短语**(`"C语言程序设计（第5版）"`)
不可能命中;② LIKE `%...%` 同理;③ 仅有的"多词退回"只在**含半角空格**时才触发。

| 编号 | 内容 |
| --- | --- |
| L1 | `_variants()` 检索变体阶梯(严→宽):原串 → 去书名号 → 去版次/卷册标注 → 拆词 |
| L2 | 整串被《》包住时,**去壳形态优先**——否则会被"恰好含《》的其他书"抢走首位 |
| L3 | 拆分 `_fts_rows()` / `_like_rows()`:每个变体先走"精确+FTS"(毫秒级),命中即停 |
| L4 | **耗时受控**:一次检索最多走一次 LIKE 全表扫,不因多试几级而翻倍 |
| L5 | 未命中提示语不再等同于"图书馆没有这本书",并引导换短关键词再试 |

**性能(实测,1.3 M 行真库)**:`C语言程序设计（第5版）` 1187 ms/0 命中 → **10 ms/8 命中**;
`三体 刘慈欣` 2803 ms → 1330 ms;常见查询持平。FTS 命中约 4 ms,LIKE 未命中约 1.3 s
(前导通配符用不上索引),这就是必须限制 LIKE 次数的原因。

### WP5 · 降一个全量回归下的时序偶发 ✅

`test_new_task_keeps_previous_run_in_background` 用 `SlowLLM`(按 `time.sleep` 配速)
制造"任务还在跑",断言 `running.busy is True` 本质上依赖"几秒窗口内断言一定能到达"。
机器负载一高就会 `assert False is True`(本次全量回归里碰到过一次,单独重跑 5/5 通过)。
换成 `GatedLLM`(显式 `threading.Event` 闸门):测试不 release,任务就不可能结束。
副作用是全量耗时从 148 s 降到 92 s。

### 验证

- `pytest -q` 全量:**458 passed / 0 failed**,耗时 92 s。
  基线是 438 项(437 passed + 1 failed),本版净增 20 项测试,
  且原那 1 项失败被 WP1b 一并修好。
  新增分布:查询归一化 7 项、计划依赖图 5 项、只读子代理并行 5 项、Git Bash 探测 3 项。
- 图书馆修复是在**本机 1.3 GB / 133 万条真实库**上复现并验证的(不只是临时小库),
  上面那张表里的形态现已全部命中,且首位是用户真正要的那本书。
- 并发有真实证据:测试用 `threading.Barrier(2)` 强制两路只读子代理重叠,
  断言假 LLM 观察到的并发峰值 `max_live >= 2`——不是「开了线程就算并发」。
- B12 走真实浏览器核对(`scripts/e2e_map_reset.py`):Playwright + 真 Chromium,
  按后端真实事件序推 `map{focus}` → `cleared`,断言 `.cm-box`/`.cm-pin` 归零、
  面板收起、搜索框清空、副标题复位、灯箱关闭,并回归「清零后地图仍可正常标注」。

## WP6 · 馆藏库不可读不再被误报为「没有这本书」(2026-09-14 追加)

**报障**:用户用 `引路学长-0.9.01-setup.exe` 装到 `D:\ylxz\引路学长`,18:31 问「三体这本书在哪里」,
工具 `library_search(query=三体,kind=paper, top_k=10)` 回「没有匹配」,模型答「没能检索到」。

**定位(可复核的三个事实)**:
1. 那次会话留档还在:`D:\ylxz\引路学长\sessions\session_20260914_183115.jsonl`(第 4/6 行是两条 tool 结果,
   原文都是「本地馆藏书目库没有匹配…(已自动放宽重试:—)」)→ 说明库文件**找到了**,
   否则会走「库不存在」分支。
2. 同一条查询在本机 1.31 GB 真库上命中 **10 条**(`三体 : 图文版` 排第一);`kind=paper` 不是凶手。
3. 把 0.9.01 安装包**重新装一遍**(真 Inno 产物,`D:\ylxz-test\引路学长`)后,同一条工具调用
   在 0.8 s 内命中 10 条 → **打包/安装形态没问题**。

⇒ 结论:用户那次运行时**库文件读不出来**(空壳/被截断/被占用),而旧代码把它静默成了「没命中」。

**根因**:`_fts_rows` / `_like_rows` 里的 `except sqlite3.Error: return []`——库坏了、表缺了、
文件被截断了,统统变成「0 命中」,于是模型有据可依地回用户「没检索到」。这正是 B01/WP4
「显示没有这一本书」的**第二形态**(第一形态是查询归一化,已在 WP4 修掉)。

| 编号 | 内容 | 落点 |
| --- | --- | --- |
| U1 | 新增 `CatalogUnavailable`:底层 sqlite 错误逐变体记录(去重),全落空且有过错 → 显式抛出 | `minicode/library.py` |
| U2 | `library_search` 区分「库读不出来」/「库是空的(0 条)」/「真没命中」三种文案,前者明确「不是馆里没有这本书」,并附库路径+大小+原始错误 | 同上 |
| U3 | 没命中时重新报出「本次对照的库(库内共 N 条书目)」——数字为 0 或明显偏小即看出库没装好(旧文案把「全库 133 万条」这句丢了) | 同上 |
| U4 | 系统提示词:库不可读 → 「馆藏书目库(异常)」段落,禁止模型说「没这本书」;导览段落声明条数是**抓取快照**、一律以 `library_search` 实时结果为准 | `library.py` / `library_guide.py` |
| U5 | 新增 `scripts/check_library_db.py` 现场自检(库路径/大小/条数/5 个样本查询;只用标准库,源码态可直接跑) | `scripts/` |

**验证**:
- 全量 `pytest -q`:**464 passed / 0 failed**(基线 458,+6 项)。
- 坏库源码态 e2e(0 字节空壳 / 截断库):模型看到的是
  「库读不出来(no such table: books / file is not a database)」而不是「没有匹配」。
- `scripts/check_library_db.py D:\ylxz\引路学长`:1.31 GB / 1331774 条 / 5 个样本全命中。
- zip 包 CRC 核对:`引路学长/crawled/…/library.db` 的 CRC32 与 `dist\引路学长` 内一致(3715ffa0,
  1,309,589,504 字节)→ 分发出去的包是完整的。

**未做**:本轮修复已在 **v0.9.02** 重新出包(见 WP7),不再停留在源码树。

## WP7 · v0.9.02:馆藏库完整性凭证 + 三处启动自检(2026-09-14)

用户追问「分享给别人到底发哪个包才能用图书馆搜书」。顺着这个问题发现:
**真正会坏的环节根本没人监管**——库是 1.31 GB 的大文件,目标机磁盘不够或拷贝
被中断时只剩半截,而程序一路把它表现成「没有这一本书」。

| 编号 | 内容 | 落点 |
| --- | --- | --- |
| H1 | 构建时写完整性凭证 `library.db.stamp.json`(size / count / sha256 / 版本),随 setup 与 zip 分发 | `scripts/build_exe.py` + `minicode/library.py` |
| H2 | `catalog_health()` 启动自检:missing / incomplete / unreadable / empty / ok 五态(只比文件大小 + 开库查表,毫秒级);`health_notice()` 翻成用户可读的一句 | `minicode/library.py` |
| H3 | **Inno 装完就校验**:`[Code]` 比对 `library.db` 存在与字节数,不一致直接弹窗。坑:`FileSize()` 在本机 Inno 6 不存在(编译期就挂),改用 `FindFirst` 取 `SizeHigh`/`SizeLow` 组合比较 | `packaging/gcode.iss` |
| H4 | GUI `get_info` 下发 `library_health`,库异常时输入框下方橙色警示(正常时不显示);CLI 启动自检 + `/library` 命令 | `gui_bridge.py` / `frontend/index.html` / `main.py` |
| H5 | **分发顺序修正**:《使用说明.txt》原来在编译 setup **之后**才写 → 安装包里没它。现由 `write_user_files()` 在 ISCC 前写好,并写明「需 2 GB 空闲磁盘」与「**单文件版不带馆藏库、单独用无法搜书**」 | `scripts/build_exe.py` |
| H6 | `check_library_db.py` 加凭证比对(含 sha256 全量核对)+ 启动自检结论预览;新增 `scripts/e2e_frozen_health.py`(冻结版验库不完整必须报 incomplete);`build_exe.py` 新增 `--skip-build`(只用现有 dist 重出包,省 9 分钟) | `scripts/` |

**验收(全部在真产物上跑)**:
- 全量 `pytest -q`:**474 passed / 0 failed**(v0.9.02 新增 10 项:凭证往返、
  无/坏凭证容错、五态判定、条数不符、`get_info` 下发)。
- `scripts/check_library_db.py dist\引路学长` → sha256 一致(`b44a47ea…`)、
  1331774 条、5 样本全命中、启动自检 ok。
- 冻结版三件套全 PASS:`e2e_frozen.py`(CLI 全链路 + GUI 启动)、
  `e2e_frozen_library.py`(包内真库 4/4 命中)、`e2e_frozen_health.py`
  (空壳库 + 真凭证 → 启动就报「现为 0.00 GB,应为 1.31 GB」、`/library` 回 incomplete)。
- 0.9.02 setup 静默装完实测:`library.db`(1,309,589,504 字节)与凭证**均落盘**;
  测试安装目录已卸除。

**分享结论**:能搜书的 = `引路学长-0.9.02-setup.exe`(578 MB)或
`引路学长-0.9.02-win64.zip`(732 MB,绿色便携);**`引路学长-单文件版.exe`(51 MB)不带馆藏库**。

### 顺手记一笔现场坑

- 冻结版 CLI 的 stdout 被重定向(走管道)时**仍按系统 ANSI 码页输出**,
  `_fix_console_encoding()` 只修了控制台。写验收脚本时不能直接按 utf-8 解码,
  得 utf-8 / gb18030 都试一遍。
- ISCC 编译 1.3 GB 馆藏库耗时约 5 分钟(lzma2/max);`--skip-build` 可单独重跑后半程。

## 不做 / 暂缓(对齐 HANDOFF §6,避免范围膨胀)

- MCP HTTP/SSE 传输、沙箱项目级白名单、附件拖拽、浏览器验证能力:本轮不碰。
- 写子代理并发:等确认通道改成"每个子代理独立确认队列"后再议。
- 地图状态按 session key 分桶恢复(交接 §4 的"更彻底做法"):本版只做清零;
  恢复旧会话各自的标注留到有真实需求时,避免过度设计。

## 进度

- [x] WP1 会话级状态隔离(B12) —— 2026-09-14
- [x] WP1b Git Bash 探测(既有环境性失败) —— 2026-09-14
- [x] WP2 计划依赖图 —— 2026-09-14
- [x] WP3 只读子代理并行 —— 2026-09-14
- [x] WP4 图书馆查书"查不到"修复 —— 2026-09-14
- [x] WP5 全量回归时序偶发降级 —— 2026-09-14
- [x] 实装打包 —— 2026-09-14
      dist/引路学长-0.9.01-setup.exe(606 MB)/ 引路学长-0.9.01-win64.zip(767 MB)/
      引路学长-单文件版.exe(53 MB);包内自带 1.31 GB 馆藏库。
      冻结态验收:`scripts/e2e_frozen.py`(CLI 全链路 + GUI 启动)与新增
      `scripts/e2e_frozen_library.py`(用打包内真库查书,4/4 命中)均 PASS。
- [x] WP6 馆藏库不可读显式报错(安装版报障追加) —— 2026-09-14
- [x] WP7 v0.9.02 馆藏库完整性凭证 + 三处启动自检 + 重新出包 —— 2026-09-14
      dist/引路学长-0.9.02-setup.exe(578 MB)/ 引路学长-0.9.02-win64.zip(732 MB);
      包内自带 1.31 GB 馆藏库 + 完整性凭证,安装器与程序启动都会校验。
      全量 474 passed;三项冻结态验收均 PASS。

## WP8 · v0.9.03–0.9.05(2026-09-15,详见仓库外层记忆与 HANDOFF §2)

- **v0.9.03 自助添加模型 + 每日 token 用量**(提交 `fbc4d01`):模型管理弹窗、
  内置行永不回传 key、`usage.py` 只记服务端真 usage(不估算)、GUI 圆环图 + CLI `/usage`。
  两个真坑记在 HANDOFF:属性隐式回退不能拿来判空;测试污染真实主目录凭据文件。
- **v0.9.04 模型管理保存/删除链路修复 + 任务面板**(提交 `0c9da36`):修「存不进自己的模型」
  死循环、删除确认弹窗 z-index、`model_catalog.py` 能力/窗口目录、会话内切模型不写回全局。
- **v0.9.05 浏览器能力(CDP 直连真实浏览器)**:本轮。`minicode/browser.py` + 5 个 `browser_*` 工具
  + 截图直接进对话。**为什么不用 playwright**:轻量包排除内核(见 gcode.spec),CDP 只需 `websockets`。
  全量 **516 passed**;真浏览器 e2e(`e2e_browser.py`)、假 LLM 驱真浏览器
  (`E2E_SCRIPT=browser scripts/e2e_fake_server.py`)、前端截图卡片回归(`e2e_browser_shot.py`)均 PASS。

### 不做 / 暂缓

- 页面交互动作专用工具(`browser_click`/`browser_fill`):目前靠 `browser_eval` 写 JS 能完成,
  等真觉得绕再立项。
- 多标签页的 UI 管理入口、按域名的登录态授权粒度。
- 写子代理并发:仍等确认通道改成「每个子代理独立确认队列」。
