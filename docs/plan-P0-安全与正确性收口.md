# P0 安全与正确性收口计划书

> 生成时间:2026-10-08
> 代码基线:v0.9.131(HEAD `3ba2263`;v0.9.131 检查点落盘已于 `71ecb39` 提交)
> 对标依据:`coding-agent-capability-matrix.md`(§9 汇总表 / §10D 差距自检)、`HANDOFF.md` §6、`docs/ROADMAP_IMPROVEMENTS.md`
> 取证方式:本计划书里每一处「现状」都来自对当前工作树的定点 `rg` / 读码,不是引用上一轮结论。可复核的命令列在 §八。
> 状态:**计划书本体未改逻辑;执行进度记在下面这一条修订里。**
> **进度修订(2026-10-08,执行会话自记)**:第〇批文档纠正 → **v0.9.132 已交**(矩阵 §10D 四处改口 + `check_docs` 新增「§2 最新里程碑必须等于 `__version__`」第二问);第一批 → **v0.9.133 已交**(P0-1 read-before-edit + P0-2 记住批准落本机规则,测试 +44、真 Chromium e2e 14 项);第二批 → **v0.9.134 已交**(P0-3 `minicode/patch.py` + `apply_patch`,测试 +27);第三批 P0-5 → **本轮只交 S5a(Job Object 进程树回收),S5b/S5c 按本文件 §三.5 的理由继续挂着**。
> 执行时的两处偏离计划书写法,都写在提交信息与 `HANDOFF.md` §2:① P0-1 的判定**没有**放进 `fs.py` 的 handler,而是放进 `PermissionGate`——因为「确认框是唯一的点头处」,而 handler 里弹不出框;② P0-1 **不在 `accept_all`/`full` 生效**(计划书只说"门之后",这里进一步明确:那两档对用户的承诺就是不再问,拿一枚启发式去拆承诺是另一种坏法)。
> 修订(2026-10-08,第二会话复核):原列五条缺口,其中 **P0-4「计划模式无只读态」经复核已不成立** ——
> v0.9.129 已落地 `SandboxState.plan_lock` + `PermissionGate.plan_locked/_plan_locked_denial` + `planning._set_plan_lock`,
> 并有 `tests/test_plan_lock.py` / `tests/test_plan_review.py` 钉住「只读放行、写类与命令拒、档位与白名单都放不过、只有人能解、锁传得进子代理」。
> 因此本轮真实 P0 是**四条**(P0-1 / P0-2 / P0-3 / P0-5);P0-4 改列 §三.4「已收口核验」,另交一条**文档纠正**。
> 同时明确:**网页端本轮不做**(见 §二.3)。

---

## 〇、这份计划书要解决什么

对标矩阵里,这个项目(引路学长)在**无头退出码、计划验收闸、个人数据闭环、只读工具并发、审批模型**这几块已经达到或超过八家竞品平均水位。真正落后的是**安全与正确性的底座**,经复核是**四条**,全部集中在 P0:

1. 没有 OS 级沙箱(只有黑名单 + 确认框)
2. 没有 `apply_patch`(只有 `edit_file` 字符串替换)
3. 没有「没读过的文件不许改」这枚钉
4. 「记住此批准」只有半格(用户点完头还要自己手写 `permissions.json`)

> 原列第五条「计划模式没有未批不许写的只读态」**已不成立**:v0.9.129 已落地,详见 §三.4。

这四条的共同点是:**它们不是新功能,而是「现有的门该更硬」**。所以这份计划书对每一条都先写清「它会碰哪条既有口径」,再写实现,避免为了补一格而拆掉项目反复清退掉的边界。

**不在这份计划书里的**:云/远程执行体、事后 accept/reject、语音、自动记忆、AGENTS.md 子目录嵌套、MCP OAuth。那些是 P1/P2,另有 ROI 排序,不混进这一轮,否则一轮改太多层、回归面收不住。

> **并行会话提示(2026-10-08,已复核为「已提交」)**:v0.9.131「改动前像落盘」已经进
> 版本库(`71ecb39`,收入 `3ba2263`)—— 新增 `minicode/checkpoints.py` +
> `tests/test_checkpoints.py`,做的是「把 `/undo` 的前像栈落盘,关窗/重启后还能退」。
> 那条线与本计划的 **P0-3 有交集**:`apply_patch` 的写前快照**直接复用已提交的
> `minicode/checkpoints.py`**,不再另起一套 undo 账;它已经把「超限整条不落盘」
> 「原子替换」「seq/epoch 一起还原」三条硬规矩钉住了,`apply_patch` 接上去即可。
> 本计划书只改了它自己这一份 md,未碰代码。

---

## 一、总览

| 编号 | 缺口 | 竞品对照 | 依赖 | 建议轮次 | 风险 |
|---|---|---|---|---|---|
| P0-1 | 无 read-before-edit 守卫 | Claude Code 把「未读就 Edit」当防幻觉第一道 | 无 | 第一批(与 P0-2 合并) | 低 |
| P0-2 | 「记住此批准」不自动生成规则 | Claude Code 按仓库永久 / ZCode 双通道 / Qoder 会话允许 vs 持久写配置 | 无(碰 `permspec.py`) | 第一批 | 中 |
| P0-3 | 无 `apply_patch` | Codex `apply_patch` / Cline / Roo Code | 无(碰 registry 顺序) | 第二批 | 中 |
| ~~P0-4~~ | ~~计划模式无只读态~~ → **已收口(v0.9.129)** | Qoder `permissionMode: plan` / ZCode plan 禁令不可被 hook allow 绕过 | — | 仅剩文档纠正 | 低 |
| P0-5 | 无 OS 级沙箱 | Codex Landlock+seccomp / Claude Code / Cursor / Cline | 无 | 单独一轮,可拆三步 | **高** |

**建议顺序的理由**:P0-1 与 P0-2 都改 `permission.py` / `permspec.py` 这一层,合并一轮能省一次全量回归;P0-3 独立,但动工具注册表顺序(有逐字钉死的测试);P0-5 最深、最不可逆,单独一轮,内部再拆 S5a→S5b→S5c。P0-4 已收口,不再占轮次,只交一条文档纠正(见 §三.4)。

---

## 二、共同前置与共同税(每一条都要交)

这个仓库对「新增东西」的硬规矩写在 `CONTRIBUTING.md`,这里不重抄一份,只列这份计划书要额外遵守的:

### 2.1 每次改完必跑(不许跳)

```bash
python -m pytest -m "not slow" -q        # 快车道
python -m ruff check .                   # 静态检查
python scripts/check_docs.py --collect   # 八道闸 + 测试基线条数
python scripts/check_frontend_js.py      # 前端内联 JS(需 UTF-8 控制台)
python scripts/gen_changelog.py          # 重新生成 CHANGELOG
python -m pytest -q                      # 提交前全量
```

### 2.2 新增落盘文件 → 四处登记税

若某条引入新文件(例如沙箱的降级标记、patch 的临时快照),必须同时进:

- `minicode/state_files.py::SHIP_NEVER`(若绝不外发)
- `.gitignore`
- `minicode/mydata.py::TABLE`(个人数据一览)
- 打包清理清单(`scripts/build_exe.py` 走的就是 `SHIP_NEVER`)

### 2.3 网页版表态(本轮明确:网页端不做)

**本轮四条都不做网页端实现**,也不新增任何网页端 UI / 桥方法 / 对外接口。要交的只是 `minicode/web_profile.py` 里**逐条表态**,按项目现有口径写进 `EXCLUDED_TOOLS` 或注明「不适用」:

- read-before-edit:网页版没有 `read_file` / `edit_file`(已在 `EXCLUDED_TOOLS`),**不适用**。
- 记住批准:网页版访客没有持久身份,也碰不到 `permissions_trust`(已排除),**不适用**。
- `apply_patch`:写盘工具,网页版一律排除,写进 `EXCLUDED_TOOLS` 并给理由。
- OS 级沙箱:网页版不跑 `bash` / `py_script`(已排除),**不适用**。

> 这条是用户 2026-10-08 的口径:**网页端还是先不做**。桌面 GUI(pywebview)与 CLI 在范围内,浏览器对外只读版不在。

### 2.4 冻结针

新增的配置项、新模块、新公开函数要进 `scripts/check_frozen_version.py::TARGETS`,并由 `tests/test_frozen_targets.py` 盯住针本身。**漏收的表现是:配置文件里写了没人读、界面上填了保存说成功下次又没了。**

### 2.5 工具顺序

加工具会改 `minicode/tools/registry.py` 的装配顺序,而 `tests/test_registry_split.py` 把每个名字逐字钉住。加 `apply_patch` 必须同步改那张顺序表,并按历史顺序插到文件类工具组里。

### 2.6 文档税

每条都要动:`HANDOFF.md` §2(新行 + 按规矩把最老的搬进 `docs/handoff/`,主文档只留最近 5 轮)、`HANDOFF.md` §6(改口/收口)、`HANDOFF.md` §5 与 §7(基线条数)、`README.md`、`system_prompt.md`、随包《使用说明》(`scripts/build_exe.py::write_user_files` 的编号列表)。

> 提醒:`check_docs.py` 的「档位与数字口径同步」只查三份活文档(README / system_prompt / 使用说明),`HANDOFF` 与 `milestones` 的历史叙述不在管辖内 —— 旧轮次写「上限 4」是当时的实情,把它改成 6 就是造假冒充当前基线。

---

## 三、逐条实施计划

### P0-1 read-before-edit 守卫

**现状证据**:`rg read_before/have_read` 全仓零命中(只命中无关的 `unreadable`)。`minicode/tools/fs.py` 已有唯一性检查(`>1` 报命中行号)、行号前缀、CRLF/编码保真(v0.9.116 L1),但**没有「本会话读过没有」的状态**。

**目标**:`edit_file` / `write_file` 命中「既有文件且本会话没读过」时,默认**问一次**(不是硬拒);读过的、自己刚写出来的直接过。

**最小实现**:

1. 记账:在会话对象上维护一个归一化绝对路径集合 `read_paths`(子代理各自独立,互不污染)。
2. 记录点:`read_file` handler 成功后登记;`write_file` 成功后也登记(刚写的内容算「知道」)。
3. 判定点:`edit_file` 的 prepare 阶段查集合 —— 放在权限门**之后**(理由见「风险」)。
4. 默认口径:**ask**(走现有确认框),文案能追:「这个文件本会话没读过,先读一遍再改更稳」,并给「仍然改」的选项。
5. 开关:`config.json` 加 `read_before_edit`(默认 `true`);关掉后行为逐字回到今天(有测试钉着)。

**改动文件**:`minicode/session.py` 或 `minicode/agent.py`(记账)、`minicode/tools/catalog_files.py`(`edit_file` 描述与判定)、`minicode/permission.py` 或 handler 内的 ask、`minicode/config.py`(开关)、`tests/test_read_before_edit.py`(新)。

**测试**:

- ① 没读过 → 触发 ask
- ② 读过 → 直接过
- ③ 自己刚 `write_file` 的 → 直接过
- ④ 开关关掉 → 与开之前逐字一致
- ⑤ 子代理的读不点亮主会话,主会话的读不点亮子代理
- ⑥ 红线 / 凭据 / `.git` 路径不受影响(那类本来就硬拒,不该被这条降级)

**冻结针**:`check_frozen_version.py` 的 `minicode.config` 条目加 `read_before_edit`。

**风险与口径**:

- 最大的误伤是「改学长自己刚写出去的文件」—— 所以 `write_file` 必须登记,否则一条正常的「写→改」链会突然被问。
- 判定放在权限门之前会变成「未读就算越界」,那是硬拒;放在门之后是「门已判要问时才多问一句」,与项目「确认框是唯一的点头处」一致。**建议放在门之后。**

**验收标准**:真实场景里对一份没读过的既有文件调 `edit_file`,确认框出现且说明原因;点「仍然改」后正常落盘;下一轮同文件不再问。

---

### P0-2 「记住此批准」自动落规则

**现状证据**:`minicode/permspec.py` 全文**没有任何写规则的函数** —— `:201 _write_trust` 写的是本机信任记录,`:290 parse_rules` / `:353 load` 只读。用户在确认框点「这次允许」之后,想让它下次不再问,得自己手写一份 `permissions.json`。矩阵 §10D 把这格标成「只有半格」,并纠正过上一轮「点头即写 `permissions.json`」的错报(那个 `add_project_rule()` 函数全仓零命中)。

**目标**:确认框点「以后都允许」时,自动写一条规则到**本机信任记录**(按工作区隔离),**不写工作区文件**。

**最小实现**:

1. `permspec.add_rule(workspace, rule) -> None`:追加到 trust 表的规则数组(复用现有 `_row` / `_put_row` / `_write_trust` 结构)。
2. `Rule` 已有 `matches` / `describe` / `effective` / `hit`,复用;新增序列化(rule ↔ JSON)。
3. 确认框新增一档:「以后都允许(本机,仅此工作区)」,与现有的「本轮总是允许」「自动放行」并列。
4. 规则范围:只允许**收窄**型规则(工具 + 路径前缀 / 命令前缀 + `args_match`)。以下四类**不给记住**,永远问人:红线路径、凭据、`.git`、外部内容升格(`gate.note_external()` 那一门)。
5. 显示:`permissions` 工具报出「本机记住的规则 N 条」,并区分「项目文件里的」vs「本机记住的」。
6. 撤销:复用现有 `revoke()`,或新增 `forget_rule(workspace, index)`。

**改动文件**:`minicode/permspec.py`、`minicode/permission.py`(确认框选项与 ask_kind)、`minicode/gui_bridge.py` + `frontend/index.html`(新按钮)、`tests/test_permspec.py`(扩)、`tests/test_permission.py`(扩)。

**测试**:

- ① 点一次头后,同类动作不再问
- ② 红线 / 凭据 / `.git` / 外部内容即使点过「以后都允许」也不记住
- ③ 不同工作区互不影响
- ④ 撤销后重新问
- ⑤ `permissions.json` 的内容一个字节都不被改写
- ⑥ 规则只收窄不扩权(与 v0.9.122 的 `allow_tools`/`deny_tools` 同一口径)

**冻结针**:`permspec` 加 `add_rule` / `forget_rule`。

**风险与口径**:

- 这是「权限自动增殖」。必须先定三条:**只记本机、只收窄、可撤销**;否则就变成 ZCode 那个「仓库里长出权限」的问题 —— 与项目「hooks 只从安装目录 `config.json` 读」是同一条根。
- 写进 trust 记录(按工作区隔离)而不是 `permissions.json`,是这条的关键取舍:仓库 clone 走一份 `permissions.json` 等于作者批的权限变成你的。

**验收标准**:真点一次「以后都允许」→ 下一次同类动作不再弹框;换一个工作区仍然弹;`permissions.json` 没被动过。

---

### P0-3 `apply_patch` 工具

**现状证据**:`rg apply_patch` 全仓零命中。当前只有 `edit_file` 字符串替换,复杂重构时会爆炸。Codex / Cline / Roo Code 都是标配,矩阵 §9 这一格是「专用补丁工具」。

**目标**:新增 `apply_patch`,支持统一 diff / Codex 风格 patch,多文件、dry-run、逐文件结果、整包原子。

**最小实现**:

1. 新模块 `minicode/patch.py`:解析 patch → 结构 `[{path, action, hunks}]`;apply 复用 `fs.py` 的读写(保 CRLF / 编码 / 无尾换行)。
2. 工具 `apply_patch` 注册进 `catalog_files.tools()`;同步改 `tests/test_registry_split.py` 的顺序表。
3. 权限:整包走**一次**确认(显示每个文件 + 加减行数),不做「逐 hunk accept/reject」—— 那是 P2,且与「写前一次点头」口径冲突(见 §五)。
4. 失败语义:任一文件失败 → **整包不落盘**(原子),返回逐文件原因;`dry_run=true` 只解析不写。
5. 写前快照**复用已提交的 `minicode/checkpoints.py`**(v0.9.131 的落盘前像账),保证 `/undo` 能整体回退这一包;不另起一套快照。
6. 并发:写类,**不进** `CONCURRENT_SAFE`。

**改动文件**:`minicode/patch.py`(新)、`minicode/tools/catalog_files.py`、`minicode/tools/registry.py`、`tests/test_registry_split.py`、`tests/test_patch.py`(新)、`scripts/check_frozen_version.py`。

**测试**:

- ① 多文件统一 diff 正确落盘
- ② CRLF / 无尾换行 / 非 UTF-8 文件保真
- ③ 上下文不匹配 → 整包拒 + 报哪一 hunk
- ④ 路径穿越(`..`)、绝对路径、符号链接、红线路径 → 拒(走现有 `_norm_rel` + 红线)
- ⑤ dry-run 不落盘
- ⑥ 写前快照进 undo,能整体回退
- ⑦ 只读工具名单里没有它(默认 False)

**冻结针**:`minicode.patch` 加解析与 apply 的公开符号。

**文档税**:README 工具段、system_prompt 工具清单、《使用说明》。

**风险与口径**:

- patch 解析是**安全面**:路径穿越、绝对路径、`..`、符号链接指向工作区外。必须复用现有归一化与红线,不许另写一套路径判定。
- 与 `edit_file` 的关系:不是替换,是并列。`edit_file` 仍适合小改;`apply_patch` 适合多文件。两者都走同一个权限门与同一套快照。

**验收标准**:真跑一个三文件重构的 patch,三个文件同时更新,`/undo` 能整体退回。

---

### P0-4 Plan mode 只读态 —— 已收口(v0.9.129),本轮只做核验 + 文档纠正

**复核结论**:**这一条已经做完,不是缺口。** 原判断来自 `rg plan_mode/approval_pending/EnterPlanMode` 零命中 —— 关键词找错了:实现用的名字是 `plan_lock` / `plan_locked`。

**已落地的实现(2026-10-08 在当前工作树逐处读到)**:

| 位置 | 内容 |
|---|---|
| `minicode/sandbox.py:669-685` | `SandboxState.lock_plan()` / `unlock_plan()`,状态住在 `sandbox` 上(子代理共用父会话那一份,锁传得下去) |
| `minicode/permission.py:181-211` | `PermissionGate.plan_locked` 属性 + `lock_plan`/`unlock_plan` + `_plan_locked_denial()`(放行读与 `plan` 工具、放行写 `plans/plan.md`、其余写类与命令一律拒) |
| `minicode/permission.py:586-591` | 判定位置排在**档位与白名单之前**,`accept_all`/`full`/项目白名单/「本轮总是允许」都放不过它 |
| `minicode/planning.py:487-500` | `_set_plan_lock()`,状态落 `ctx.sandbox` |
| `minicode/planning.py:502-533` | 核对单批准(approve)才 `_set_plan_lock(ctx, False)`;`revise`/`unanswered` 不解锁 |
| `minicode/planning.py:578` | `plan(action=write)` 上锁 |
| `minicode/gui_bridge.py:1257-1260` | 界面「批准这版计划」走 `sandbox.unlock_plan()` 同一个动作,不在界面另记一份状态 |
| `tests/test_plan_lock.py` | ① 锁上后写类与命令拒、读与改计划放行;② 排在档位之前;③ 只有人能解(模型侧无解锁路);④ 锁传得进子代理 |
| `tests/test_plan_review.py` | 核对单三种 verdict 与解锁的关系 |
| `scripts/check_frozen_version.py:615-618` | 冻结针已收 `plan_locked` / `_plan_locked_denial` |

**ZCode 那条红线已满足**:plan 的写禁令在 `PermissionGate.check()` 里执行,而 pre hook 的 `updated_input` 改写后会**重跑门**(v0.9.115 H1),所以 hook 的 `allow` 绕不过它 —— 与「hook 只从安装目录 `config.json` 读」同一条根。

**本轮要交的(只有文档)**:

1. `coding-agent-capability-matrix.md` §10D 的 P0「规划模式」那一格,现状仍写着「✖ 没有未批不许写这个态」——**改成 ✅**,并写明 v0.9.129 与证据行号。
2. 同一份 §10D 末段「五条真缺口」里第 ③ 条(规划模式只读态)删掉或改成已收口。
3. `HANDOFF.md` §6 第 51 条 (c)「规划模式没有未批不许写这个态」改口,标注 v0.9.129 收口。
4. `docs/ROADMAP_IMPROVEMENTS.md` §三 P0 第 3 条、§四 阶段一 D 条同步改口。
5. `README.md` 路线图段若提到 plan mode 只读态,一并核对。

**不改代码。** 上面这些是文字纠正,目的就是不让「假缺口」再被下一轮当活干一遍。

---

### P0-5 Windows 沙箱底座

**现状证据**:`rg JobObject/AppContainer/restricted token/landlock/seccomp/seatbelt` 在 `minicode/sandbox.py`、`minicode/permission.py` 全仓零命中。现在的形状是**黑名单 + 确认框**,即「问人」路线;Codex / Claude Code / Cursor / Cline 都有 OS 级约束。矩阵 §10D 把 OS 级沙箱列为 Codex「Landlock+seccomp / Seatbelt / 受限令牌」、Claude Code「Bash/PowerShell」、Cursor「工作区读写 + 网络默认阻断」。

**目标**:给 `bash` / `py_script` 的执行加 OS 级约束。至少做到**进程树可回收 + 资源上限**;进阶做降权;最重做 AppContainer。

**最小实现(分三步,可独立交付、可回退)**:

**S5a — Job Object 进程树回收 + 资源上限**(建议先做,零新依赖):

- `ctypes` 调 `CreateJobObjectW` + `SetInformationJobObject`(`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`,可选 `JOB_OBJECT_LIMIT_PROCESS_MEMORY` / `JOB_OBJECT_LIMIT_ACTIVE_PROCESS`)+ `AssignProcessToJobObject`。
- 这一步**不改权限语义**,只保证「杀得干净、跑不飞」—— 现在 `bash_kill` 要连子进程树一起终止,Job Object 是 OS 给的保证,不是自己数进程。
- 关键:不要把主进程自己关进这个 job(否则关窗把 pywebview 一起杀)。

**S5b — 低完整性 / 受限令牌**(真正开始「限制能写哪」):

- `CreateRestrictedToken`(去掉管理员组)+ `CreateProcessAsUser` / `CreateProcessWithTokenW`;或用低完整性级别(`SetTokenInformation(TokenIntegrityLevel)`)。
- 这一步才动「能不能写工作区外」。
- 与现有确认门的关系:沙箱是「操作系统不让做」,权限门是「先问人」。两层并存,**沙箱不可用时必须降级并如实说**,不能假装有沙箱 —— 与项目「不许样子货」一致。

**S5c — AppContainer**(可选,最重):

- `CreateAppContainerProfile` + capability SID 授权工作区目录 + 禁网。
- 这是最接近 Codex 的形状,但要处理 Python 运行时依赖、临时目录、网络等一堆兼容问题,建议最后做且可回退。

**改动文件**:新模块 `minicode/win_sandbox.py`、`minicode/tools/shell.py`(起进程路径)、`minicode/tools/catalog_files.py`(`py_script`)、`minicode/config.py`(开关)、`minicode/sandbox.py`(策略合并)、`tests/test_win_sandbox.py`(新)、`scripts/e2e_frozen_sandbox.py`(扩)。

**测试**:

- ① Job Object 真杀子进程树(起一个 spawn 子进程的命令,kill 后孙进程不在)
- ② 内存上限真触发
- ③ 不可用时降级并报「未启用」
- ④ 不影响现有确认门
- ⑤ e2e 真跑(`scripts/e2e_frozen_sandbox.py`)
- ⑥ 主进程不被关进 job(关窗不杀自己)

**冻结针**:`win_sandbox` 的公开符号进 TARGETS;config 新开关进 TARGETS。

**风险与口径**:

- 这是四条里风险最高的。Windows API 在无管理员、无开发者模式下的行为差异;PyInstaller 打包后 ctypes 调用;与 pywebview 主进程的关系。
- 建议先在独立分支 + 真机验证,不与其他四条混在一轮。
- 网络控制要单独定:Job Object 不管网络;真正禁网要 AppContainer 或防火墙规则(后者要管理员)。**S5a/S5b 不做网络**,别在文档里把它说成「沙箱齐了」。

**验收标准**:一条会 spawn 子进程的命令,kill 后进程树清空;一条超内存命令被 job 限制;沙箱不可用时界面如实显示「未启用」。

---

## 四、执行顺序与依赖

```
第〇批  文档纠正(纯 md)      ── P0-4 已收口:改矩阵 §10D / HANDOFF §6 / ROADMAP,不留假缺口
              │
第一批  P0-1 read-before-edit  ┐
        P0-2 记住批准落规则     ┘  同一层(permission/permspec),合并回归
              │
第二批  P0-3 apply_patch  ── 独立(registry 顺序 + 快照)
              │
第三批  P0-5 Windows 沙箱底座 ── 单独一轮,内部 S5a → S5b → S5c
```

- 每一批独立提交、独立跑全量、独立点版本号;不要四条一次提交。
- P0-5 的 S5a/S5b/S5c 也各自可独立提交(可回退)。

---

## 五、明确不做的事(守住边界)

这几条不是漏做,是**撞项目自己写下的边界**,重提会引出错误的补齐动作:

1. **事后 accept/reject(改完再批)** —— 等于把确认档从「写之前」挪到「写之后」,要新增暂存盘 + 两套回滚,正是项目反复清退的「第二套权限真相」。要动先推翻现有口径。
2. **云 / 远程执行体** —— Cursor Cloud / Claude cloud sessions / Codex cloud / Qoder Cloud Agents 都是服务端算力与调度位,与「本地优先桌面 agent」直接冲突。
3. **plan 态被 hook `allow` 绕过** —— ZCode 明确禁止;这条是安全红线,不因为「用户想省一次确认」而放开。**v0.9.129 已实现**(判定在重跑门之后仍生效,见 §三.4),这里留作「以后也别拆」的边界。
4. **沙箱「假装有」** —— 不可用时必须如实降级。这个项目最贵的坏法是「界面上说安全,实际没拦」。
5. **`apply_patch` 逐 hunk accept/reject** —— 与 §五.1 同根;补丁工具先做到「多文件 + 整包原子 + 可回退」,逐 hunk 审批不在这条计划里。

---

## 六、总体验收清单

每条收口时必须同时满足:

- [ ] 快车道 + 全量 + ruff + `check_docs --collect` + `check_frontend_js` + `gen_changelog` 全绿
- [ ] 新增行为有测试钉住;**关掉开关后与今天逐字一致**(有对照测试)
- [ ] 新配置项 / 新模块 / 新公开符号进了 `check_frozen_version.py::TARGETS`
- [ ] 新落盘文件过了四处登记税(§2.2)
- [ ] `web_profile` 表过态
- [ ] `HANDOFF` §2/§5/§6/§7、README、system_prompt、《使用说明》同步
- [ ] 提交标题点了版本号
- [ ] 若动界面,有一条真浏览器 e2e(只改源码文本的断言钉不住「点开真的看得见」)

---

## 七、风险登记表

| 风险 | 触发条件 | 影响 | 缓解 |
|---|---|---|---|
| read-before-edit 误伤「刚写的文件」 | `write_file` 未登记 | 正常写→改链被反复问 | `write_file` 成功即登记(测试 ③) |
| 「记住批准」权限增殖 | 写进 `permissions.json` | 仓库 clone 带来别人的权限 | 只写本机 trust、按工作区隔离、可撤销 |
| apply_patch 路径穿越 | 解析器自写路径判定 | 写到工作区外 | 复用 `_norm_rel` + 红线,不另起一套 |
| ~~plan 态被 hook 绕过~~ | 已收口(v0.9.129):判定在 `PermissionGate.check()` 内、重跑门之后 | — | 已由 `tests/test_plan_lock.py` 钉住;这是核验项,不是待办 |
| 沙箱关掉主进程 | 主进程进 job | 关窗杀自己 | 只为子进程建/分配 job(测试 ⑥) |
| 沙箱 API 在打包版失效 | PyInstaller + ctypes | 装了包却没沙箱 | 不可用即降级 + 真机 e2e |
| 文档与代码漂移 | 手抄基线/数字 | 假 ✅ 再次出现 | 代码是唯一真相,文档引用测试 |

---

## 八、附录:现状证据(可复核)

以下命令在仓库根目录运行,输出即本计划书「现状证据」那一列的依据:

```bash
rg -n "apply_patch" minicode tests                       # 期望:零命中
rg -n "read_before|have_read" minicode                    # 期望:零命中(只有 unreadable)
rg -n "def " minicode/permspec.py                         # 期望:无写规则函数
rg -n "JobObject|AppContainer|landlock|seccomp|seatbelt" minicode  # 期望:零命中
rg -n "plan_locked|lock_plan|unlock_plan" minicode        # 期望:命中(plan 只读态已落地,别再当缺口)
rg -n "plan_mode|approval_pending|EnterPlanMode" minicode # 期望:零命中 —— 但这不是缺口,只是实现换了名字
rg -n "MAX_PARALLEL_TASKS|MAX_SUB_STEPS" minicode/subagents.py
rg -n "EVENT_KINDS" minicode/hooks.py
```

> 引用行号前先 `git log -1`:这个工作树有多个会话并行改,行号会漂。
> 方法论一条:凡是「有」,必须能顺着行号读到;读不到就降级为「半格 / 无」并登记进 `HANDOFF.md` §6。
