# 工具按需加载(工具面从 21,068 token 降下来)· 方案,**已落地 v0.9.173(默认关)**

> 起草于 2026-10-10。代码基线:`D:\game\mini-code` v0.9.147(HEAD `53fb209`)。
> ⚠ **本文那几个 token 数是 v0.9.147 那一次实测的,标题里的 21,068 对应的是当时册上 61 只** —— v0.9.173 那一轮最后落了 `view_image`/`session_message`/`tool_search` 三只,**今天注册表是 73 只**(`tests/test_registry_split.py:147` 钉住),而 `minicode/tool_profile.py:3` 写下时册上是 71 只。三个数各属于哪一个时刻都标在这里,**没有重测就不抄新数**:要引用分层省了多少,先跑 §二 那条命令重跑一遍。§2 里程碑里那句"71 只 / 两万七千 token"是**历史账,不改**(它记的是当初怎么写的那个句子,不是今天的现状)。
> 取证方式:本文每一处数字都是**当场跑出来的**(命令见 §二),每一处「现状」都带 `文件:行号`。
> **引用前先 `git log -1`** —— 这份工作树有多个会话并行改,行号会漂。
> 状态:**已落地(v0.9.173,提交 `ff44604`)。** 三层与那只常驻的 `tool_search` 都做了
> (`minicode/tool_profile.py` + `config.tool_loading` + `catalog_knowledge.tool_search_tool`),
> **默认 `all` = 逐字回到本方案之前的行为**。§五 那件「动手前必须先拿到的东西」
> (一个能真跑的端点,用来数「该用领域工具时它有没有先 tool_search」)**仍然没有** ——
> 所以这个开关今天还是关着的,谁要打开它,请先补那一件事。落地实测:**工具面 25,031 → 16,650 t**
> (常驻 51 只;可加载 22 只 8,381 t 换成索引段约 500 t)。比 §1.3 那份预估少省一点,因为
> **浏览器那 8 只按 §1.3 自己那条警告留在了常驻**(它们服务真编码工作,下沉它们是改行为面、
> 要单独一轮)。

---

## 〇、这份计划书要解决什么

一句话:**工具 schema 占了每轮固定开销的 67%,而且它只会涨。**

这不是"省点钱"的问题,是三件会同时恶化的事:

1. **成本与延迟**:每一轮、每一问都要重付这 21,068 token,与对话长短无关。
2. **压缩被提前触发**:窗口是固定的,固定开销越大,能装下的对话越短 —— 而压缩是**有损**的。
3. **架构上已经在撞墙**:`tests/test_registry_split.py:153` 断言每份 `catalog_*.py` `< 400` 行。
   这条约定本意是"拆分的目的是消热点,不是把 987 行搬到另一个文件里"(`:152`),
   但它的**实际效果**是每加一只描述写得诚实的工具就要新开一份目录 —— v0.9.146 为
   `bash_input` 新开了 `catalog_bg.py`,v0.9.147 为 `diagnostics` 新开了 `catalog_diag.py`。
   **两份都是被这道闸逼出来的**,不是那两个领域真的需要独立成册。

**这三件事的共同点是:不动的代价是复利的。** 按需工具检索在**已取证的八家**里
(仓库自带那份 374 行矩阵)一格都没记录,所以它属于「差异化亮点」而不是「行业标配」——
**这一点直接决定它的排期**(见 §六末段):该排在 OS 沙箱与指令文件第三级之后,
而不是插到它们前面。

---

## 一、现状(当场量出来的)

### 1.1 固定开销的构成

```
$ .venv\Scripts\python.exe scripts\audit_prompt_cost.py
每轮固定开销 ≈ 31057 tokens(system 9989 + 工具 schema 21068)
```

| 组成 | tokens | 占比 |
|---|---|---|
| system prompt | 9,989 | 32% |
| **工具 schema(61 只)** | **21,068** | **67%** |

尺子用的是仓库自己那把(`scripts/audit_prompt_cost.py:17-19`:CJK 一字一 token、
其余四字符一 token),与 `minicode.compaction` 同一条 —— 不另立一把尺。

### 1.2 一个反直觉的分布

**最重的工具几乎全是"领域类",最轻的全是"通用类"。**

| 最重 12 只 | tokens | | 最轻 8 只 | tokens |
|---|---|---|---|---|
| `plan` | 941 | | `bash_kill` | 166 |
| `schedule` | 764 | | `grep` | 158 |
| `reminder` | 688 | | `permissions` | 157 |
| `worktree` | 656 | | `read_file` | 142 |
| `desktop_notify` | 644 | | `browser_status` | 128 |
| `mood` | 639 | | `map_clear` | 104 |
| `calendar_edit` | 623 | | `glob` | 103 |
| `ask_user` | 610 | | `use_skill` | 101 |

**这条分布决定了分层该怎么切**:不是"重的下沉、轻的常驻"(那会把 `read_file` 之外
的东西全下沉),而是**"通用常驻、领域按需"** —— 而这两件事在这里恰好重合。

### 1.3 分层能省多少(实测)

把 61 只按"每一轮都可能用到的干活工具"与"特定领域工具"分开:

```
通用常驻 32 只 = 10931 t
领域按需 29 只 = 10137 t   (现在全都要每轮付)
领域换成一行索引 ≈ 515 t

工具面: 21068 → ~11446 t   省 ~9622 t
固定开销: 31057 → ~21435 t  省 ~9622 t (30%)
```

被移出常驻的 29 只(按重量):`schedule` 764、`reminder` 688、`desktop_notify` 644、
`mood` 639、`calendar_edit` 623、`browser_open` 542、`render_chart` 406、
`map_find_place` 397、`scores` 394、`generate_image` 381、`library_search` 363、
`browser_fill` 330、`ocr_image` 329、`library_guide` 317、`browser_click` 300、
`campus_notices` 297、`browser_eval` 284、`export_ics` 274、`get_weather` 259、
`map_drop_pin` 251、`browser_screenshot` 242、`gpa_calc` 233、`campus_calendar` 220、
`map_show_list` 189、`map_highlight_building` 183、`browser_close` 180、
`browser_snapshot` 176、`browser_status` 128、`map_clear` 104。

> ⚠ **上面那份"通用 32 只"是本文的一次粗切,不是最终名单。** 最明显的争议是
> **浏览器那 8 只(合计约 1,700 t)**:它们服务的是"打开页面看一眼再改"这类**真编码
> 工作**,而不是校园生活,所以更像通用类。把它们留在常驻、只下沉校园/生活/地图/
> 馆藏那一族(约 8,400 t),仍然能省 ~27%。**最终名单是 §七 要拍的板之一。**

### 1.4 现状的机制(改哪里)

| 事实 | 位置 |
|---|---|
| 模型每轮拿到的 tools 数组 = 注册表原样 | `minicode/agent.py` 的 `self.registry`(由 `app_setup` 装配) |
| 注册表顺序被逐字钉死 | `minicode/tools/registry.py:1-11`(文件 docstring 明写"顺序不许随手改")+ `tests/test_registry_split.py:28` 的 `ORDER` |
| 已经有一层"按面裁剪"的先例 | `minicode/web_profile.py:53` 的 `EXCLUDED_TOOLS`(网页版**显式列举**排除,理由逐条写) |
| 已经有一层"按档收窄"的先例 | `minicode/agent_profiles.py`(子代理档位,红线"只能收窄") |
| 工具 schema 的出口只有一处 | `minicode/tools/base.py::Tool.to_openai_schema()` |

**所以这不是要发明一种新机制** —— 仓库里已经有两次"给某个面裁剪工具集"的先例
(网页版、子代理档),本文要做的是**第三层**,而且它必须与那两层同源、不另立一套。

---

## 二、可复核的命令(§一 的每个数字)

```bash
# 固定开销与最重的工具
.venv\Scripts\python.exe scripts\audit_prompt_cost.py

# 上面 §1.3 那张分层账(通用常驻 / 领域按需 / 索引成本)
#   见本文附录 A 的那段一次性脚本(它只读,不碰任何文件)

# 目录行数那道闸
.venv\Scripts\python.exe -m pytest tests/test_registry_split.py -q
```

---

## 三、设计

### 3.1 三层,而不是两态

```
① 常驻层(resident)     —— 每轮都在 payload 里,与今天一样
② 索引层(index)        —— 每轮都在,但每只只占一行「名字 + 一句它干什么」
③ 可加载层(loadable)   —— 默认不在 payload 里;模型要了才进来,并**留在本会话**
```

**为什么必须有"索引层",不能只做"常驻 / 不在"**:模型不会要一个它不知道存在的东西。
索引层是这一整套能成立的前提 —— 它的成本约 515 t(§1.3),换掉 10,137 t。

### 3.2 谁来加载:`tool_search` 一只常驻工具

新增一只**常驻**工具 `tool_search(query)`:

- 入参:一句自然语言查询(或空 = 列出全部可加载工具的清单)
- 出参:**匹配到的工具名 + 它们完整的中文描述**(不是 JSON schema —— 模型不需要看
  schema 才会用工具,它需要知道"这只能干什么、什么时候该用、有什么边界")
- 副作用:把这些工具**标记为"本会话已加载"**

之后每一次请求都把"已加载"那批的**完整 schema** 拼进 payload —— 与今天一模一样,
所以模型一旦加载过,行为与今天**逐字相同**。

### 3.3 三条不许含糊的口径

1. **默认关。** `config.json` 新增 `tool_loading: "all" | "lazy"`,**默认 `"all"`**,
   也就是**逐字回到今天**。这条与仓库既有的一串开关同源(`net_policy` 默认 `off`、
   `auto_config` 默认关、`read_before_edit` 默认 true 但可关)。理由不是保守,
   而是**这一改动的核心主张在本机无法验证**(见 §五),不能拿"我觉得能行"去换
   一个核心循环的行为。
2. **注册表不动。** 分层只发生在"给模型看的那一份投影"上,`build_default_registry()`
   返回的**集合与顺序一个字都不改** —— `tests/test_registry_split.py` 那两道钉子
   (顺序逐字、目录份数)照旧全绿。这与 `web_profile` 的做法一致:它也是投影,
   不是把工具从注册表里拿掉。
3. **加载是不可逆的(本会话内)。** 一旦加载就不再收回 —— 收回等于"我明明用过它,
   下一轮它却不见了",那会让模型把"工具没了"误读成"这个能力不存在"。
   会话结束自然归零。

### 3.4 索引行怎么写

每只一行,**内容取自它自己的 `description` 首句**(不另写一份 —— 两份文案迟早对不上):

```
- schedule: 登记与查看个人课表(空档/换楼/作息表/回滚)
- mood: 情绪日记(记一天 / 回读一周 / 导出 / 清空)
```

首句的切法要定死并写测试(按第一个句号或换行切,超长截断并**在索引里标出"已截断"**)。
**不许**为了让索引好看而手写一份摘要:那就是"第二处真相"。

---

## 四、落点清单

| 位置 | 改什么 |
|---|---|
| **新增模块** `minicode/tool_profile.py` | 三层的判定(常驻 / 索引 / 可加载)、`tool_search` 的匹配、会话级"已加载"集合。**纯函数为主**,便于喂假数据测试 |
| **新增配置** `minicode/config.py` | `tool_loading`(默认 `"all"`);照 `net_policy` 的写法带 `_说明` 与"写错值退回默认" |
| **新增工具** `minicode/tools/catalog_diag.py` 或新目录 | `tool_search` 一只。**它常驻**,所以要进 `ORDER` 与份数钉子 |
| `minicode/agent.py` | 发请求前把 `self.registry` 换成**投影后的那一份**(已加载 + 常驻);**不改 `self.registry` 本身**(它还要给子代理、给 `task` 用) |
| `minicode/app_setup.py` | 系统提示词里追加索引段(只在 `lazy` 时);份数与来源照 `instruction_layers` 那套"只报份数不贴内容"的口径 |
| `tests/test_registry_split.py` | `ORDER` 加 `tool_search`;份数 61 → 62 |
| `tests/test_registry.py` | 名字清单与 `read_only` 表各加一条(`tool_search` 是只读) |
| `minicode/web_profile.py` | 表态:网页版**永远 `all`**(访客不需要省这个) |
| `scripts/check_frozen_version.py` | 新模块 `minicode.tool_profile` 一整组 |
| `README.md` / `system_prompt.md` / 《使用说明》 | 工具数、开关说明、"哪些工具默认不在 payload 里、怎么让它们出来" |
| `HANDOFF.md` §1/§2/§7 | 计数、新行、基线 |

---

## 五、诚实边界(这一节是本文最重要的部分)

### 5.1 这一改动的核心主张**本机验证不了**

整套设计成立的前提是:**模型会主动去要它需要的工具。**

而"模型会不会要"这件事,**只有在真端点上跑才知道**。本机的现状是:
内置校园 Key 被网关拒着(`HANDOFF.md` §6 第 54 条,2026-10-08 实测
`401 … new_api_error`),而那正是唯一能真跑的那个端点。

**所以本文把它写成"方案,未开工",而不是直接改。** 拿一个"我觉得模型会要"去换
核心循环的行为,正是这个仓库列在禁忌里的东西 —— 界面上说省了 30%,实际把
`schedule` 变成了模型看不见的能力,那是**更贵**的一种坏。

**动手前必须先拿到一件东西**:一个能真跑的端点(自己的 Key 或内网恢复),
然后做一件小事:**同一个任务,`all` 与 `lazy` 各跑 20 遍,数"该用领域工具时它有没有
先 `tool_search`"**。这个数就是这一整套的准入门槛,而它现在没有。

### 5.2 省下来的不是"成本",是"能装多少对话"

本文从头到尾说的是 **token 数**。它能推出两件事,但**不能**直接推出"账单降了 30%":

- 能推出:固定开销降 30% → 同样的窗口能多装约 9,600 token 的对话 → 压缩触发得更晚。
- **不能**推出:账单降 30%。因为缓存命中的部分本来就便宜,而固定前缀**恰恰是最容易
  命中的那一段**。**改完之后前缀变了,第一次必然全价**,之后才回到命中 ——
  这件事要按 `token-usage.db` 的 `cached_tokens` 那一列量,不能按 `prompt_tokens` 推。
  (仓库从 v0.9.117 起就记了这一列,所以这件事**量得出来**。)

### 5.3 不做的事

- **不做"按模型自动决定"**:不做小模型分类器去猜这一问要不要哪些工具 —— 那是把一个
  可解释的名单换成一次不可解释的调用,而猜错的表现是"这个能力突然没了"。
- **不做跨会话的加载记忆**:加载状态不落盘。落了盘就会出现"我昨天加载过 `mood`,
  今天它还在" —— 而用户昨天为什么加载、今天还需不需要,没有任何依据。
- **不动 `apply_patch` / `read_file` 这类**:**通用类的名单一旦确定就不许"顺手挪两只"** ——
  挪一只就是改一次行为面,要单独一轮并说清为什么(与 `registry.py` 那句"顺序不许
  随手改"同一条根)。
- **不给 MCP 工具做分层**:MCP 工具的名字与数量由外部服务器决定,`tool_search`
  对它们的匹配质量无从保证;而且它们本来就已经是"用户配了才有"。

---

## 六、别人怎么做(**只写本仓库矩阵里取到证的**)

本仓库那份 `coding-agent-capability-matrix.md`(八家、374 行、逐条带出处)**没有记录任何一家的
「按需工具检索」**。§9 汇总表与 §10D 差距自检里也没有这一格。

**起草时我写过"Claude Code 有 `ToolSearch`、默认开启"——那一句在本文里没有取证,已删。**
理由要留着,因为它正是本文开头那份对标文档反复吃亏的形状:矩阵里查不到,而本机的
web 检索端点此刻回 `401`(与 §5.1 是同一个故障面),**无法复核**。拿"我记得"当依据写进
一份要给下一轮照着做的方案书,就是把一个未取证的结论洗成事实。

所以这一节只留两条**站得住**的:

- **可以确定的**:在已取证的八家那 374 行里,这一格是空白 —— 也就是说按需工具检索属于
  「差异化亮点」而不是「行业标配」(`matrix` §10 那三档的分类口径)。
- **不可以确定的**:别家到底有没有、默认开不开。**下结论前先补一次取证**
  (从 `code.claude.com/docs` 找 `ToolSearch` 那一页),补完把出处贴进这一节,再改上面那句。

**这对排期有直接影响**:既然它是"加分项而非底线",它就该排在
**OS 沙箱 S5b/S5c** 与**指令文件第三级**之后,而不是插到它们前面 ——
本文的价值在于"把账量清楚、把方案定死",不是"抢一个更靠前的位次"。

---

## 七、要用户拍的板(四件事)

1. **通用类到底留哪几只?** 本文 §1.3 那份"通用 32 只"是粗切。最需要你表态的是
   **浏览器那 8 只(约 1,700 t)算通用还是领域** —— 算通用则省 ~27%,算领域则省 ~30%,
   代价是"打开页面看一眼"这类活儿要多一次 `tool_search`。
2. **默认值给 `all` 还是 `lazy`?** 本文建议 `all`(逐字回到今天),理由是 §5.1 那个
   主张还没验过。若你手上有一个能真跑的端点、愿意先验那 20 遍,就可以直接给 `lazy`。
3. **索引里要不要带"什么时候用"的一句?** 带则每只多约 15-25 t(合计多约 600 t),
   换来的是模型更可能在正确的时机想起它。
4. **`tool_search` 的匹配用关键词还是向量?** 关键词:零依赖、可解释、与
   `router.py` 现有那套同源;向量:召回更好,但要接 `embed.py`(那档在本机**权重
   一个都没下过**,`HANDOFF.md` §6 记着"语义召回比关键词准"目前没有本机证据)。
   **本文建议关键词** —— 与 `router.py` 同一套计分,两份路由口径合一。

---

## 附录 A:§1.3 那段分层账(只读,可直接跑)

```python
import json, tempfile, sys
from pathlib import Path
sys.path.insert(0, ".")
from minicode.app_setup import build_system_prompt
from minicode.tools.registry import build_default_registry

def tokens(text):
    cjk = sum(1 for ch in text if ord(ch) > 0x2E80)
    return cjk + (len(text) - cjk) // 4

GENERAL = {  # §1.3 那次粗切的名单;§七.1 要拍的就是它
    # 注:`task` 不在 `build_default_registry()` 里 —— 它由 `app_setup` 装配时追加
    # (`app_setup.py:317` 一带)。留在这里是为了让这份名单与"模型实际能用的工具"
    # 对齐;下面的账按注册表算,所以它自然不参与(差 0 t)。
    "read_file","write_file","edit_file","apply_patch","bash","bash_status","bash_kill",
    "bash_input","py_script","glob","grep","repo_outline","diagnostics",
    "permissions","permissions_trust","hooks","hooks_apply","worktree",
    "web_crawl","web_search","page_parse","mcp","mcp_resource",
    "use_skill","plan","ask_user","search_knowledge","search_history",
    "export_trace","export_feedback","kb_suggest","memory","task",
}
ts = build_default_registry()
gen = [(t.name, tokens(json.dumps(t.to_openai_schema(), ensure_ascii=False)))
       for t in ts if t.name in GENERAL]
dom = [(t.name, tokens(json.dumps(t.to_openai_schema(), ensure_ascii=False)))
       for t in ts if t.name not in GENERAL]
g_t, d_t = sum(x[1] for x in gen), sum(x[1] for x in dom)
sp_t = tokens(build_system_prompt(Path(tempfile.mkdtemp())))
idx_t = sum(tokens(f"- {n}: ") + 14 for n, _ in dom)
print(f"通用常驻 {len(gen)} 只 = {g_t} t;领域按需 {len(dom)} 只 = {d_t} t;索引 ≈ {idx_t} t")
print(f"工具面 {g_t + d_t} → ~{g_t + idx_t} t(省 ~{d_t - idx_t} t)")
print(f"固定开销 {sp_t + g_t + d_t} → ~{sp_t + g_t + idx_t} t")
```
