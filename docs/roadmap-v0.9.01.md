# v0.9.01 开发记录(移植星瑶 Y-code 已验证做法)

> 参考仓库:`https://gitee.com/feng-chenhao/xingyao-y-code`
> (本地只读副本 `D:/game/.fetch/ycode-ref`,HEAD=b00ed5b / dev / v1.2.26)。
> 基线:`mini-code v0.9.0`(87ad10b)。本版目标版本号 **v0.9.01**。
> 本文是本轮开发的需求、决策与验证的单一事实源。

## 本轮范围(4 个工作包,按风险从低到高)

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

### 验证

- `pytest -q` 全量:**451 passed / 0 failed**。
  基线是 438 项(437 passed + 1 failed),本版净增 13 项测试,
  且原那 1 项失败被 WP1b 一并修好。
  新增分布:计划依赖图 5 项、只读子代理并行 5 项、Git Bash 探测 3 项。
- 并发有真实证据:测试用 `threading.Barrier(2)` 强制两路只读子代理重叠,
  断言假 LLM 观察到的并发峰值 `max_live >= 2`——不是「开了线程就算并发」。
- B12 走真实浏览器核对(`scripts/e2e_map_reset.py`):Playwright + 真 Chromium,
  按后端真实事件序推 `map{focus}` → `cleared`,断言 `.cm-box`/`.cm-pin` 归零、
  面板收起、搜索框清空、副标题复位、灯箱关闭,并回归「清零后地图仍可正常标注」。

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
