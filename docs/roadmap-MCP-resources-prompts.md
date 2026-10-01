# MCP 的 resources / prompts / sampling / logging 要不要接 · 方案,未开工

> 基线:D:\game\mini-code v0.9.99(P3 之后,`mcp.summary_rows` 已是共用真相)。
> 来源:§6 #45 那条「确认过不是漏做而是压根没接」—— 本文档把它从"疑案"升级成
> "决策依据"。规矩:接哪一样都动的是 55 只工具的能力面,先出方案再动手。
> 下面的"现状"全部逐行核过(2026-10-01),不是回忆。

## 一、现状(接与不接的边界在哪)

- `minicode/mcp.py:51` 的 `CLIENT_PRIMITIVES = ("tools",)` 是**唯一真相**:
  学长只实现了 MCP 的 `tools` 一个原语。握手里客户端声明**空** capabilities
  (不假装支持),对端声明了什么则原样留在 `client.server_info["capabilities"]`,
  并由 `caps_line()` 拼成人话报给用户(v0.9.96 那轮做的"如实说")。
- 对端声明什么、学长用不上哪几样,在 `mcp` 工具的 add 回话、侧栏健康行
  (`summary_rows`)、`desktop_notify` 之外无处可见 —— 但**可见 ≠ 可用**,
  这就是本方案要回答的问题。

## 二、四样东西各是什么、值不值得接

| 原语 | 它给学长带来什么 | 判定 |
| --- | --- | --- |
| `resources` | 读服务器持有的数据(文件/数据库行/日志片段),以 `resources/read` 取内容 | **候选第一**。与 `search_knowledge` 同族:只读、低风险、有真实场景(接一个 SQLite-MCP 就能把"馆藏库"那类本地数据搬出去)。落点:新目录组 `catalog_mcp.py` 或 `catalog_knowledge`,新工具 `mcp_resource`(action=list/read,读要过确认框,内容过溯源围栏 trust="external") |
| `prompts` | 服务器提供的**提示词模板**(斜杠命令的 MCP 版) | **缓**。学长自己的技能系统(SKILL.md)+ slash_commands 已覆盖同一需求且更可控;接了要回答"两套模板谁优先",是产品问题不是技术问题。除非同学真实用上某台 prompts 服务器,否则不做 |
| `sampling` | 服务器反过来**请学长代调模型**(MCP 的反向通道) | **不接**。每一次 sampling 都是花同学的 Key 额度,触发方却是外部服务器 —— 与「未命中就联网只是指引、绝不自动联网」(§6 第三梯队 ⑦ 的口径)同一条理:花额度的事必须由人点头,而 sampling 的确认体验在桌面上很难做清(谁在要、要多少 token、答了给谁) |
| `logging` | 服务器推送日志消息 | **不接**。学长的日志已有 watchdog.log/tray.log/gui_warnings.log 三份各有读者(刚清完"只写不读"的账);接 MCP logging 是给"每台服务器一份日志流"开新账,没有读者就是第四份只写不读的日志 |

## 三、若接 resources,要动的面(先想清楚再开工)

1. **工具**:新 `mcp_resource` 一只(不要拆成 list/read 两只 —— 55 只工具
   的 tools 数组每轮都发给模型,能省则省)。action=list 列某台服务器的
   资源清单(read_only=True),action=read 取内容(**非只读**,过确认框,
   理由与 `web_crawl` 同:外部内容进上下文要用户点头)。
2. **权限门**:server 粒度沿用 `read_only` 配置字段 —— 服务器标了只读,
   read 也免确认?**不**。资源内容是外部数据,一律过确认框;服务器级
   read_only 只免 list。这条与现有 MCP 工具"read_only 免确认"的口径有
   张力,动工前要在工具描述里写死。
3. **溯源围栏**:资源内容按 `trust="external"` 过 provenance 围栏
   (⟦围栏⟧包裹,指令不是用户指令) —— 与 web_crawl/browser 同一条路。
4. **协议层**:`CLIENT_PRIMITIVES` 加 `"resources"`;握手声明不再是空的
   —— 有测试钉着「声明了就真实现了」(与 v0.9.82 那句"不假装支持"互为
   反面,改声明必须连实现一起改,测试拦)。
5. **测试**:`tests/test_mcp_admin.py` 加 resources 分支(list 形状/read
   过确认/围栏/服务器未声明 resources 时如实报"这台没这个")。

## 四、刻意不做

- **不一次全接四样**。tools 的经验是:一个原语接好 = 协议 + 权限 + 围栏 +
  可观测四处都要跟上。resources 一个先走通,其余三个才有判断依据。
- **不接 sampling**(§二):口径冲突,不是实现难度问题。
- **不把 resources 内容直接塞进系统提示词**:进上下文的内容一律经工具
  回话+围栏,不开第二条免确认的路。
- **不为"能力面板好看"接**:每接一样都要能回答"同学什么时候会用它"。

## 五、验收口径(动工时照此写测试)

- 握手声明与实现一致(声明 resources 而没有实现 = 红);
- 未声明 resources 的服务器:list/read 都如实说"这台服务器没声明 resources",
  不报一个空清单装成功;
- read 的内容带来源(哪台服务器、哪个 URI)与围栏标记;
- 并行子代理读资源:`_QuietUI` 静默照旧,`[web|档位]` 尾注带资源 URI
  (与网页查阅同一格式)。
