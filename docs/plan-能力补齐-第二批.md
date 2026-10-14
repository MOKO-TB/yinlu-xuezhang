# 能力补齐 · 第二批计划书

> 生成时间:2026-10-12
> **代码基线:工作树 v0.9.172(未提交)**;HEAD = `9d63fa7`(v0.9.162)。**另一会话正在同一棵树里推进 v0.9.163~172**(工具 62→70;已落:符号工具三只 `call_hierarchy`/`symbol_definition`/`code_outline`、Shadow Git 全链路闭环、分层指令采纳/撤销闭环)。**引用本文任何行号前先 `git log -1` + 看工作树** —— 这份树有多个会话并行改,行号会漂。
> 对标依据:2026-10-12 对 ZCode / Qoder / Codex / Claude Code 的一次逐项对照(取证方式是**读本项目代码**,不是读竞品源码 —— 竞品那侧只按公开能力面记账);`docs/ROADMAP_IMPROVEMENTS.md` §四;`docs/plan-能力补齐-第一批.md`(五件已全交)。
> 状态:**计划书本体;未改任何代码。** 拿不准的接口名标「待核」,写实现时以工作树为准。

---

## 〇、这份计划书要解决什么

上一轮「看看还缺什么」的对照里,我列了七个候选,**逐条读码核实后六件当场作废** —— 它们在本项目里已经存在(证据见 §三)。真正剩下来的只有两件,共同点是:**都不是"编码智能体主轴"上的东西,而是输入侧的最后一公里与生态的最后一公里**。

| 编号 | 一句话 | 性质 | 为什么值得做 |
|---|---|---|---|
| **2-1** | 扫描版课件 / 图片版资料 → OCR 成可读文本 | 把已有的两个半格接上 | 校园里最常见的输入(扫描课件/拍照教材/纸质通知)恰好卡在"抽不出字"这一步,而两个半格(OCR 引擎、pypdf)都已经在包里 |
| **2-2** | 扩展中心页(底座第六页) | 把散的收成一处 | **装了没生效的侧边栏扩展,现在只有 `console.warn`** —— 用户看不见。这是"开放底座"承诺的最后一公里 |
| ~~**2-3**~~ | ~~App 更新检查(带更新日志卡)~~ | ~~分发闭环~~ | ✅ **已于 2026-10-14 交付**(通道从 gitee 换到 GitHub Release、整段更新日志 + 更新卡、可关)—— 见 §〇·补 |

两件都**不碰权限门与沙箱边界的判定**,也不碰并行会话正在改的符号/指令层,回归面可控,可以一轮一件地交。

**不在这份计划书里的**:语音输入(ROADMAP §四 仅剩的 K)、OS 沙箱 S5b/S5c(等拍板)、代码库语义索引、子代理 worktree 隔离、MCP prompts/sampling/elicitation —— 见 §四。

---

## 〇·补 2-3 App 更新检查(✅ 已交付 2026-10-14)

**为什么先做它**:它是用户点名插队的一件,而且**原以为是新功能、核实后发现半件已经在**(见下)。

**核实出来的真相**(读码 + 探仓库):
- `minicode/proactive.py::check_update` **早就有**,但通道是 gitee `inkzero/momo` —— 匿名已 403,**等于这条检查一直是静默死的**("维护者发 release 就亮"从没兑现过);
- 前端只有一条提示条,`notes` 在**后端就被截到 120 字**、前端再截 60 字 —— 更新日志等于没有;
- GitHub 仓(`MOKO-TB/yinlu-xuezhang`)用户已开放为公开仓,且**已有一个 v0.9.114 的 release**(1483 字日志)—— 现成的升级页。

**交付内容**:
- **通道**:`RELEASE_API` 换 `https://api.github.com/repos/MOKO-TB/yinlu-xuezhang/releases/latest`(匿名可读,60 次/时);docstring 如实记了换的原因(gitee 那条是死的);
- **整段日志**:`check_update` 回 `{version, url, notes(上限 `NOTES_MAX`=6000,超了标 `truncated`), date, name}`;
- **更新日志卡**(用户图 p1 同款):版本 + 日期 + markdown 正文(可滚)+「打开发布页」/「以后再说」;提示条那颗按钮改成「看更新内容」开卡(不再直接跳走);**侧栏版本号可点** = 手动查一次(与启动同一条后端,一份真相);
- **可关**:`config.json` 的 `update_check`(默认 true;关掉 = 完全不出网,**手动那条也一起拒**);SECURITY.md「数据流向」写明外发面(IP + 通用 UA,无本机数据);
- 测试 +5(`tests/test_proactive.py`:整段日志/超长截断标记/非对象不炸/开关关掉不出网);e2e +8(v09162:版本号→卡片、日志渲染、打开发布页过 `open_url`、提示条那条路);冻结针 +3(`check_update_now` / `NOTES_MAX` / `RELEASE_API`);《使用说明》第 145 条 + README + `config.example.json`。

**顺带交付(用户同轮要求)**:把已提交的最新版本**推送到 GitHub**(origin/main)。

---

## 一、2-1 扫描版课件 → OCR 化

### 现状(带行号,2026-10-12 工作树)

| 事实 | 位置 |
|---|---|
| `lecture-notes` 技能**明说做不了**,并给"抽字率 > 0.5"作判据 | `skills/lecture-notes/SKILL.md:52-53`("直说:这份 PDF 每一页是一整张图片,我抽不到文字,所以做不了笔记。") |
| `pdf` 技能只有**文本提取**(pypdf),没有页→图那一步 | `skills/pdf/SKILL.md`(description:合并/拆分/提取文本/网页转 PDF) |
| `ocr_image` **只吃图片**,后端已含 Windows 自带 OCR(离线、不联网) | `minicode/tools/catalog_media.py:19-36`(参数 `path/backend/lang/max_chars`);`minicode/ocr.py`(system 引擎 + 可选 winrt 绑定,缺了如实说) |
| 包内**有 pypdf**(pdf 技能 `libs: pypdf`),**有 Pillow**(`minicode/images.py:107` 卡片出图用) | 两条依赖都不新增 |

**缺的只有中间一环:PDF 页 → 图。** 而扫描版 PDF 的每一页通常就是一张内嵌大图 —— `pypdf` 的 `page.images` 能直接抽出来,**零新依赖**。

### 要解决什么

学生手里最多的是**扫描/拍照版课件、教材、纸质通知**;现在它们到"抽不出字"就断了,模型只能说"做不了"。接上这一环之后:`扫描课件 → 逐页 OCR → 结构化笔记` 与 `拍照板书 → OCR`(已有)走同一条"待核对草稿"的口径。

### 设计(三个落点)

**落点 A(工具):`ocr_image` 扩 `.pdf`** —— `minicode/tools/catalog_media.py` + `minicode/ocr.py`
- 输入按后缀分流:图片走原路;`.pdf` 用 pypdf 逐页抽内嵌图(取该页面积最大的那张,多图时按面积降序取前 N 并点名"这一页有 M 张图,只读了最大的那张");
- 输出**按页锚点**组织(`--- 第 N 页 ---`),页数上限与 `max_chars` 截断都如实报"还有 N 页没读";
- **抽不出图的页如实点名**(`第 3 页没有可抽取的图 —— 可能是矢量图或带文字层,那几页请走 pdf 技能直接抽文本`),绝不跳过装看不见;
- 后端不可用时沿用 `ocr.py` 现有口径(缺什么、怎么补),不硬编内容。

**落点 B(技能改口):`lecture-notes`** —— 把"做不了"改成"先 OCR 再整理"
- 判据(抽字率)保留;命中扫描版时**先走 A 的 OCR 链**再进原有整理流程,并在产物里保留"OCR 是待核对草稿"这条既有口径(原文已写:只当草稿,不当已核实原文);
- 若 OCR 也不可用(没装 winrt、没视觉后端),才退回今天那句"做不了"并说清缺什么 —— **"做不了"从第一句降级成最后一句**。

**落点 C(可选,同轮):`pdf` 技能补一段"扫描版判别与分流"** —— 把抽字率判据提到 pdf 技能里与 lecture-notes 共用(同一件事别两处各写一套阈值)。

### 边界与不做
- **不做 PDF 光栅化**:不引 pymupdf / poppler / pdf2image(重依赖且要外部二进制)。抽不出内嵌图的那类 PDF(纯矢量)如实分流,不猜;
- 不做版面还原(表格/双栏还原成结构是另一笔账;第一版是"按页的文本流");
- OCR 结果**永远**是待核对草稿 —— 这条是 `ocr_image` 现有立场,不因换了输入源而放宽。

### 测试面(pytest,预计 +8~12)
- 抽图:单页一图 / 多页多图 / 一页多图(取最大+点名)/ 无图页(如实点名);
- 页锚点与截断文案形状(`还有 N 页没读`);
- 后端缺失路径(monkeypatch 掉 system/vision)→ 诚实回话,不出现"识别成功"字样;
- fixture:用 Pillow 造一张带文字的图 → pypdf 装进 PDF(两项依赖都在,可生成式造 fixture,不必往仓库塞二进制);
- lecture-notes 的改口:扫描版判据命中 → 走 OCR 分支(技能文本断言)。

### 文档税 / 冻结针 / 成本
- README(能力表)、`使用说明` 新条、HANDOFF §2 新行 + §7 基线、`skills/lecture-notes/SKILL.md` 与 `skills/pdf/SKILL.md` 改口;
- 冻结针:新函数名进 `minicode.ocr` 组(待核该组现有点位);
- **成本:约一轮**(中)。

---

## 二、2-2 扩展中心页(底座第六页)

### 现状(带行号)

| 事实 | 位置 |
|---|---|
| 底座现有**五页**(activity / git / subagents / files / sidechat)+ 底部终端坞;注册表 `SidePages.register` 可直接加页 | `frontend/index.html:11559` 起五处 `SidePages.register({ id: ... })` |
| **侧边栏三方扩展只有装填器,没有 UI**:`loadExtensions()` 把"没装上"的原因只写进 `console.warn` | `frontend/index.html:12423` 起(`(r.skipped \|\| []).forEach(s => console.warn(...))`) |
| 后端**已经**把跳过原因备好了 | `minicode/extensions.py::discover` → `{items, skipped:[{dir,msg}], root}`;桥 `get_extensions`(`minicode/gui_bridge.py:343` 门面 / `:1513` 实现) |
| 其余四类各有出处:技能(`get_skills`,`gui_bridge.py:580`)、技能渠道(`skills.skill_dirs` 三通道)、MCP(健康行 + `mcp_admin`)、钩子/包(`hooks.py`、`hook_packs`/`skill_pack`) | 分散在各面板/config,没有一处汇总 |
| 打开数据目录的现成先例 | `mydata_open_dir`(`gui_bridge.py:515` 门面 / `:4083` 实现) |

### 要解决什么
1. **装了没生效看不见**(最伤的一条):manifest 坏、entry 越目录、id 不合规 —— 现在这些原因只活在开发者控制台里;
2. 五类扩展没有一处"一眼看全":想确认"我这台机器上到底挂了什么"要翻四个地方。

### 设计

**落点 A(桥):`extensions_overview()` 组合方法** —— `minicode/gui_bridge.py`
后端拼五份**现成**数据,前端不各敲五条(与 `get_board` 的"四处现成结论拼一屏"同一形状):
```
{ok, skills:[…], skill_channels:[{path,source,count}],
 mcp:[…], sidepages:{items:[…], skipped:[{dir,msg}], root},
 hooks:{count, entries:[…]}, packs:[…]}
```

**落点 B(前端):新页 `id:"extensions"`, icon 🧩, title「扩展」** —— `frontend/index.html`
- 五个分区,每区一行标题 + 若干行;每行给"名字 + 来源 + 状态",能点开看详情;
- **「没装上」整段摊开**(skipped 逐条:`目录名 — 原因`),并把 `root` 路径亮出来(附「打开这个目录」按钮,走 `mydata_open_dir` 同款);
- 分区标题带计数(如「侧边栏扩展 2 个 · 1 个没装上」),计数为 0 的区不渲染(沿用活动面板"没做过的一类整段不渲染"的既有规矩);
- 刷新按钮重拉 `extensions_overview`。

**落点 C(动作,只做已经有的)**:「打开数据目录」(`mydata_open_dir` 先例);MCP 那一半若已有启停入口就复用,没有就不造。

### 边界与不做
- **不做安装器/市场**:分发仍是"手工放进数据目录",信任模型不变(见 `SECURITY.md` 专节);
- **不给侧边栏扩展加启停开关**:没有运行时语义(文件在=已装),加一个点了不动的开关就是"装饰板"——本项目刚在智能配置上踩过这个坑;
- **不改信任模型**:这一页是**只读展示 + 打开目录**,一句话写进 SECURITY.md 那一节(避免读者以为它放松了什么)。

### 测试面(pytest + e2e)
- pytest(+4~6):组合形状五区键齐 / 跳过项带原因与 root / 空目录不炸 / **网页版墙**(`extensions_overview` 不进 `PublicFacade`,有针先例);
- e2e(新场或并进 v09162,+6~8):新 tab 出现且可点 / 五区渲染 / **skipped 可见**(stub 造一个坏 manifest)/ 「打开目录」过桥 / 零页面错误;
- `test_rail_entries` 不动(入口栏不加键 —— 页面的家在顶栏「切换面板」与底座标签条,这是 v0.9.162 定的形状)。

### 文档税 / 冻结针 / 成本
- README(底座那一段补第六页)、`使用说明` 新条、SECURITY.md 一行、HANDOFF §2/§7;
- 冻结针:`minicode.gui_bridge` +1(`extensions_overview`)+ `FRONTEND_TARGETS` +2~3(页 id 与新容器名);
- **成本:约一轮**(低-中)。

---

## 三、被核实为"已经有"的(别再当待办)

| 我上一轮以为是缺口 | 核实结果(证据) |
|---|---|
| 确认框不给 diff | **已有真 diff**:`gui_bridge._permission_detail_text` 对 `write_file` 覆盖与 `edit_file` 出 unified diff(行号 + 超长截断) |
| 改完不自检 | **已有两套**:`.py` 语法底线(`agent._py_syntax_feedback`,`ast.parse` 当场回填)+ **多语言诊断回灌 v0.9.147**(`minicode/diagnostics.py`,argv 由扩展名查表、检查器缺失就跳过并说明、超时不等于通过) |
| 没有符号/引用工具 | **已有整套**:`call_hierarchy`(v0.9.168)/`symbol_definition`(v0.9.169)/`code_outline`(v0.9.170)+ `find_references`/`rename_symbol`/`repo_outline`/`symbol_search` |
| 没有通用网页搜索 | **已有** `web_search`(+ `web_crawl`/`page_parse`/`browser_*` 家族) |
| 不能产出 Office 文档 | **已有四技能**:`docx`/`xlsx`(openpyxl)/`pptx`/`pdf`(pypdf+Edge 打印)+ `batch-docs`/`photo-print`/`scientific-figure-making` |
| 没有仓库百科/提交信息/代码审查 | **已有**:`repo_wiki` 技能、`git-commit` 技能、`code-review` 技能、`test_runner` 工具 |
| 回滚只有一层 | **已有三层**:文件级 undo(`checkpoints`)/ 影子 git(v0.9.157~171 全链路闭环)/ 会话 rewind |

**结论:编码智能体主轴上,本项目该有的都在。** 剩下的差距集中在"输入侧最后一公里(2-1)"与"生态最后一公里(2-2)"。

---

## 四、后续候选(不在这份计划书里)

| 候选 | 来源 | 价值 | 备注 |
|---|---|---|---|
| **K 语音输入** | ROADMAP §四 仅剩未收的一条 | 中 | `--voice` 或输入框 🎤 → winrt `SpeechRecognizer` 离线 STT;winrt 绑定与 OCR 同一族(缺了如实说) |
| **S5b/S5c OS 沙箱降权** | §6 第 52 条 | 高(安全) | **等用户拍板**;fail-open 语义:做错了不是"没保护",是每条命令都跑不起来 |
| 代码库语义索引 | 批次 2/3 | 中 | `retrieval.py`+`embed.py` 只接 KB;工作区已有 grep/符号工具兜底,**建议等真实"找不到代码"的反馈** |
| 子代理 worktree 隔离与 steer | 批次 2/3 | 中 | 长任务不污染主工作区 + 给在跑的子代理递话 |
| MCP prompts / sampling / elicitation | 批次 2/3 | 低-中 | 依赖具体 MCP 服务端的用法 |

---

## 五、节奏与风险

**建议节奏**:两件各一轮 —— 2-1 → 2-2(先做工具后做页面;工具那件会顺手把两个技能的描述改对,是别处的输入)。

**版本号待核**:工作树已是 0.9.172 未提交,**起新轮前先看并行会话落没落**(`git log -1` + `git status`);若它已提交,新轮从 0.9.173 起;若仍在飞,**先等它提交**(§6 158 的教训:PyInstaller 认工作树,包会带着别人没提交的改动出去)。

**两件各自的风险**:
- 2-1:winrt 绑定在打包版里是**可选**的(与 OCR 现状同一条),没有它时整条链退回"说清缺什么";pypdf 与 Pillow 在包内(已核实),不新增依赖;
- 2-2:纯展示 + 打开目录,**不碰信任模型**;唯一要小心的是"别顺手给不存在的启停语义做开关"(§二边界里已写死)。

**开工前的三条自检**(照 §6 的既有纪律):`git log -1` 核基线 → `git status` 核并行会话 → 本文行号逐个 grep 复核(行号取自 2026-10-12 的工作树快照,会漂)。

---

## 六、可复核的命令

```bash
git log --oneline -3 && git status --short | head        # 基线与并行会话
grep -n "扫描版" skills/lecture-notes/SKILL.md            # 2-1 的"做不了"原文
sed -n '14,36p' minicode/tools/catalog_media.py           # 2-1 的 ocr_image 参数面
grep -n "skipped" minicode/extensions.py                  # 2-2 的跳过原因形状
grep -n "console.warn(\"扩展没装上" frontend/index.html    # 2-2 的现状(只有控制台)
grep -n 'id: "' frontend/index.html | head                # 2-2 的底座现有页清单
./.venv/Scripts/python.exe -m pytest -q                   # 全量基线(改前/改后各一次)
```
