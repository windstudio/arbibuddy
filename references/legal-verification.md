# 分层法律核验

本参考是模型使用的法律基线、动态核验和最小落档契约。它不把模型变成法律规则执行器，也不把 Agent 的联网能力封装成新的下载子系统。

## 先读什么

- 稳定全国基础法的版本化元数据见 [`legal/legal-baseline.v1.json`](legal/legal-baseline.v1.json)。它只记录名称、制定机关、版本或修正信息、生效状态、适用地区、官方链接、最后核验日期和复核日期。链接存在不等于内容已核验。
- 13 份现有来源登记的逐项清理证据见 [`legal/source-cleanup.v1.json`](legal/source-cleanup.v1.json)。每份登记只能归入 `valid`（有效）、`replace`（替换）、`merge`（合并）；清单只描述现有登记，不证明本案已完成动态核验。

## 分层边界

### 直接使用版本化基线

当问题只依赖基线中已记录、稳定且全国适用的基础法时，直接使用该版本化元数据和对应领域参考。普通案件不为了重复证明基础法存在而联网，也不得把模型记忆说成当前版本证明。用户只要带待核项的 `candidate` 时，先判断正文是否真的需要某项易变规则的确定结论；不需要时，把时效中断、地方办理口径和未核金额列入待核或占位，按文书契约完成候选交付。

### 触发动态核验

模型使用 Agent 已有的浏览或联网能力，优先查找官方一手来源，并自己辨别来源是否适用于当前问题。先判断本轮请求是否命中下列触发条件；案情出现地名、候选稿留有待核项，本身不触发地方来源搜索：

- 地方规则、地方仲裁或行政办理口径；
- 最低工资、社会平均工资、补偿封顶、社保/公积金基数、节假日等动态数值；
- 新司法解释、指导性案例或会改变既有理解的官方解释；
- 基线超过复核日期，规则版本不明，或两个来源与既有基线发生来源冲突；
- 用户要求最新规则、精确官方出处或指定当前日期；
- 高风险 `external_final` 的结果实质依赖当前地方或新近规则。

联网由 Agent 现有能力完成。对于只交付 `candidate` 的本轮，确需动态核验时做一次集中官方来源检索；若无法取得足以核验的正文与来源信息，就把该事项记为 `unavailable`，保留待核并继续候选交付。后续补证或外发定稿时再核验，不在本轮反复搜索同一来源。不要建设专用 HTTP 下载器、候选 URL 状态机、响应正文仓库、回执账本或用户重试轮次；不要把搜索摘要、训练记忆或 URL 可访问性当作本案动态核验结论。

核验内容时先比对法律名称、具体命题、有效期间、规则适用地区与本案日期。年度节假日安排以正文的适用年度为准，发布日期、文号或 URL 中的年份不等于适用年度；上一年度安排不能验证本年度工作日，也不能因通知在上一年发布而排除正确年度安排。放假调休区间与法定节假日分别判断。官方网页已经取得但内容、年度或事项不匹配时，说明错配并保留待核，不能记录为本案 `verified`；无法取得适用正文时按 `unavailable` 处理。

向用户区分基础规则引用、实际取得并核对的官方内容，以及动态观察是否成功落档。工具失败或记录缺失时如实说明未完成该项动态核验；继续一般分析或候选交付不表示核验成功，也不否定仍可使用的稳定全国基线。

## 动态观察的公共接缝

`scripts.legal_verification.LegalVerificationTracer.assess` 接收一次性的模型观察。它只校验字段完整性并准备档案变更，不访问 URL、不判断网页正文、不计算降级边界，也不替模型决定法律适用。

WorkBuddy 只通过同一公共契约的窄 transport 调用，不反射类或运行内联 Python：

```text
python -B -X utf8 -m scripts.legal_verification.runtime_cli --workspace "<workspace_root>" describe
python -B -X utf8 -m scripts.legal_verification.runtime_cli --workspace "<workspace_root>" assess --input "<workspace_root>/.arbibuddy/runtime-input/<nonce>.json"
python -B -X utf8 -m scripts.legal_verification.runtime_cli --workspace "<workspace_root>" record --input "<workspace_root>/.arbibuddy/runtime-input/<nonce>.json"
```

`assess` 的输入就是下述 observation；`record` 输入只含 `case_id`、`expected_revision`、
`observation` 和可选 `change_summary`，并经公开 `CaseArchive.commit` 原子落档。`--input`
只接受当前工作区 `.arbibuddy/runtime-input/` 的直接 JSON 子文件并在解析业务请求前消费删除。
每次命令调用只执行一条 transport；不得追加管道、重定向、输出裁剪、第二条操作或临时清理。
联网仍由 Agent 现有能力完成，此 transport 不访问 URL。

调用前应具备以下字段：

```json
{
  "scope": "本次要核验的规则或数值",
  "trigger": "local_rule|dynamic_value|new_interpretation|source_conflict|stale_baseline|user_requested_latest|high_risk_finalization",
  "outcome": "verified|source_conflict|stale|unavailable",
  "official_sources": [
    {
      "title": "官方来源标题",
      "url": "https://官方来源",
      "final_url": "https://最终落地的官方页面",
      "content_sha256": "页面内容的 64 位 SHA-256",
      "jurisdiction": "规则适用地区",
      "publisher_jurisdiction": "发布机关或官方主机所在地区"
    }
  ],
  "accessed_on": "YYYY-MM-DD",
  "jurisdiction": "本案适用地区",
  "impact": "对本案分析、计算或文书的影响"
}
```

`outcome` 必须是模型对当前核验的明确结论。不存在 `url_reachable` 或类似“能打开所以已核验”的结论。`verified` 必须列出至少一个官方来源，并记录最终落地链接 `final_url`、访问日期 `accessed_on` 和内容 `content_sha256`；最终链接必须与登记链接属于同一官方主机。`content_sha256` 只能来自实际取得的页面正文；搜索结果、工具附带的引用和截断包装、错误信息的哈希都不是页面内容哈希。现有浏览能力无法取得可核验正文或其摘要时，选择 `unavailable` 并按下表继续，不为凑齐摘要自行编写哈希实现或改用直连下载。`source_conflict`、`stale` 和 `unavailable` 仍可记录模型实际尝试核对的官方来源，不能补造成功摘要；这些降级结论的来源 provenance 可在确实取得时记录。`official_sources` 只保存来源标题、登记/最终链接、适用地区和内容摘要，不保存网页正文、搜索摘要、请求头或内部任务载荷。

### 规则适用地区与来源发布地区双绑定

`observation.jurisdiction` 和来源的 `jurisdiction` 表示规则适用地区；来源的 `publisher_jurisdiction` 表示发布机关或官方站点所在地区。两者不是同一个字段：上海官方站点发布一条全国规则时，规则适用地区仍可写“全国”，但来源发布地区仍是“上海市”。已知官方主机的地区判断优先于来源自报字段；例如 `shanghai.gov.cn`、`sh.gov.cn` 及其子域名按上海发布来源处理。

地方发布来源进入动态核验前，当前用户或当前案情档案必须已有可信地区绑定；可信绑定只来自当前档案元数据、当前档案中的“用户陈述”或“证据支持事实”。没有绑定时先只询问一个地区问题并结束本轮，不搜索、采用、评估或记录该地方来源；稳定全国基线或真正的中央/全国发布来源可以继续。来源与已有地区冲突时也不得采用。`LegalVerificationTracer.record` 是共享的最后门禁：未绑定返回可恢复的 `jurisdiction_confirmation_required`，冲突返回 `jurisdiction_conflict`，两者都不提交 `AUTH-*`、不改变档案 revision；`assess` 只做形状校验，模型不得用它绕过前置地区门禁。分析假设、既有 authority 记录、平台 memory、历史会话和其他案件不能自行绑定当前地区。

## 降级语义

| 动态状态 | 一般分析 | 确定性计算 | candidate 草稿 | 实质依赖该规则的高风险 external_final |
| --- | --- | --- | --- | --- |
| `verified` | 继续 | 继续 | 继续 | 允许继续，仍需其他高风险确认和确定性检查 |
| `source_conflict` | 继续并说明冲突 | 继续，以条件情景或待核验变量表达 | 继续 | 阻断 |
| `stale` | 继续并标明基线日期 | 继续，不把旧数值伪装成现行值 | 继续 | 阻断 |
| `unavailable` | 继续并说明未完成核验 | 继续，保留影响和假设 | 继续 | 阻断 |

只有在 `outcome` 非 `verified`、动作是 `external_final` 且结果实质依赖易变规则时阻断。一般分析、计算和候选文书不因官网暂时不可用而整体失败；若不实质依赖该易变规则，则不扩大阻断范围。

简要记忆：核验受阻时，一般分析、计算和候选草稿继续；只有实质依赖不确定易变规则的高风险外发定稿暂缓。

这里的“动作”和“是否实质依赖”来自模型对当前用户目标、事实和文书的判断，不是观察提交器的字段，也不是脚本维护的 gate。模型读取本表后决定用户可见的继续、条件化说明或暂缓定稿；观察提交器不得替模型选择法律适用、下一动作或用户文案。

## 最小落档

核验结论通过 `LegalVerificationTracer.record` 交给既有 `CaseArchive.commit`，仍只写一份 `案情档案.md` 的 `authority` 记录。档案最少包含：

- 核验事项；
- 核验结论；
- 官方来源；
- 登记链接、最终落地链接及内容 SHA-256（`verified` 必须具备）；
- 访问日期；
- 适用地区；
- 影响范围。

不得写入网页全文、多个摘要、候选 URL 的状态轨迹、请求/响应、`source_ids`、回执账本、逐轮 JSON、模型推理或用户重试计数；允许保留单一最终来源的 SHA-256 provenance。除当前案情地区与地方发布来源的绑定门禁外，档案提交器不判断来源真假、不选来源、不决定下一问题或用户文案；绑定门禁失败时不得进入 `CaseArchive.commit`。

模型向用户说明时使用普通语言：说明核验成功或未完成、来源和访问日期、适用地区及对当前分析的影响。不要展示 `AUTH-*`、JSON、记录 revision、CLI、案件目录、工具字段或内部失败载荷。

## 高风险最终化

动态核验不是高风险最终化本身。模型仍需解释不可逆后果、替代方案和不确定性，并将用户确认写入同一案情档案；文书工具只在真正生成外发定稿时检查必要记录、引用一致性和确定性文件检查。核验失败可以保留解释、分析、计算和候选草稿，不能把候选草稿称作已完成的外发定稿。

## 清理范围说明

来源清理清单的 13 项对应当前声明 `source_id` 的现有法律登记文件；登记分类不等于逐条来源验真，应结合登记中的已核和待核信息判断。`holiday-schedules.v1.json` 没有 `source_id`，其 `holiday_dates` 是放假调休期间而非全部法定节假日日期，不得直接作为 300% 加班日期；年份数据按官方年度安排另核验。旧的回执/authority 状态机不因本次审计恢复为模型主路径。
