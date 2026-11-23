# Codex 与 Claude Code 模型主导 Agent Eval

该 Eval 是 issue 10 的独立真实验证入口，不进入 `fast`、`slow` 或 `full` 测试套件，也不调用旧的结构化状态 Runtime。

以下完成路径与 PASS/FAIL 是严格 Scenario 指标。产品验收按[模型主导产品验收](model-led-product-acceptance.md)另列核心结果、实质风险与非阻断质量项；不要求所有对话完全符合预设轨迹，也不改写原始失败记录。候选准备有条件接受不等于可外发定稿或正式发布。

每条 Journey 都会在外部临时根创建独立的 Agent Workspace 和 ScenarioResponder Workspace，将当前工作树以 `copy` 安装到对应平台的发现目录，然后用平台原生 CLI 驱动模型 A。模型 B 通过独立 Codex SDK thread 读取有限 Scenario 上下文并只返回事实 ID/动作；Harness 确定性校验决定并从 Scenario 原文渲染消息。Harness 只观察安装回执、会话耗时、案情档案和公开文件副作用，不读取内部状态、不把模型 B 当作裁判。模型 B 动态扮演用户是本 Eval 的真实 Journey 组成部分：它只能依据隔离 Scenario 的既定事实回应模型 A 当前问题；无匹配事实时回答未知或安全停止，记录为 Scenario 覆盖/收敛证据，不自动判定 Skill 失败。

默认套件包含四条 Journey：工资/奖金/加班并交付仲裁材料；解除路径、高风险确认并交付通知书候选材料；中断恢复、事实纠正与动态官方核验不可用时的降级；律师阅案简报首次一次交付。完成路径必须满足交付触发事实、能力边界、公开产物、重启后里程碑和响应组；早期出现“无法/待补”等普通措辞不能单独放行。若当前宿主没有可调用的 Word 文书能力，前两条路径必须观察到模型明确拒绝虚称 DOCX 并给出合法降级，而不是把 Markdown 当作 DOCX；有能力时则必须观察到 canonical DOCX 与独立清单。简报的内部清单仍须存在并通过校验，但用户展示列表只有 Word。每条路径在 Codex 和 Claude Code 各运行一次，共八个隔离 Journey。

显式运行默认套件的全部平台和四条路径（实现阶段不隐式执行）：

```powershell
python -B -X utf8 -m scripts.model_led_agent_eval.cli suite evals/suites/model-led-codex-claude.yaml --source . --temp-root <仓库外临时目录>
```

默认模型：Codex 模型 A 和模型 B 均为 `gpt-6-luna`；Claude Code 模型 A 为 `sonnet`，模型 B 仍为 `gpt-6-luna`。可用 `--model`、`--codex-model`、`--claude-model` 或 `--simulator-model` 显式覆盖。

所有 Codex/Claude Code 真实 Journey 的模型 A 单轮上限默认 **1200 秒**，`run` 与 `suite` 一致，ML01 至 ML04 均适用。`--timeout-per-turn` 可显式覆盖；模型 B 的 `--simulator-timeout` 独立计时，默认 **300 秒**。超时必须保留失败证据，不得视作完成或放宽 Oracle 门禁。

单场景 Codex 模型 B 路径：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m scripts.model_led_agent_eval.cli run evals/model-led/codex-claude-wage-bonus-overtime.yaml --platform codex --responder model --simulator-provider codex-sdk --source . --temp-root <仓库外临时目录> --keep-workspace-on-failure
```

受管工作区判据：退出码为 0、输出 `PASS`，且 evidence schema v2 的 `oracle.completion_path`
为明确路径；`scenario_responder` 的每个决定均为 `status=accepted`，事实 ID 均来自
当前 Scenario，`responder.actual_identity` 来自实际 adapter。每轮记录
`request_sha256`、attempt/repair 和校验结果；最终 `observation` 满足声明的
CaseArchive、DOCX 和独立清单交付。SDK 不可用或版本不兼容时必须报告
`Runtime Adapter`，不得自动切换 `rule`；真实 SDK 默认使用 pinned runtime，只有显式
`--simulator-codex-bin` 才允许覆盖。

CLI `PASS` 证明隔离工作区内的受管交付，不证明 Codex Desktop 或 Claude Code 宿主已向用户附加文件；`evidence.json.host_presentation_verified` 仍为 false。Issue 10 的用户可见展示还须在真实宿主核验。对虚构场景可显式加 `--retain-deliveries`，只在受管交付通过后把 DOCX 与独立清单复制到证据目录的 `retained-deliveries/`，保留路径、大小和 SHA-256；案情档案及完整工作区按生命周期清理。宿主验证人员只能从这些已校验副本打开文件，不把本地清单当成宿主展示成功。

离线重放使用 `--responder replay --simulator-replay <evidence.json>`，不调用模型 B，
且会校验 scenario、turn、prompt/schema 版本和每轮 request hash；旧 v1 evidence 不静默
当作 v2 消费。

ML01 使用相对月份生成工资争议、离职和最后催讨日期。CLI 在一次 `run` 或 `suite` 中固定上海时区的基准日；需要重放同一场景时传 `--as-of-date YYYY-MM-DD`，取原证据的 `scenario_as_of_date`。实际渲染日期同时记录在 `resolved_dates`。事实卡的 `requires_sent_facts` 由校验器按已发送轮次检查；`immediately_after` 和 `simulator.after_restart_fact_id` 指定下一轮必须选择的事实，错误选择进入一次修复决定，不会被当作已发送。

若平台命令不在当前 PATH，可分别传入命令数组：

```powershell
python -B -X utf8 -m scripts.model_led_agent_eval.cli suite evals/suites/model-led-codex-claude.yaml --codex-command codex --claude-code-command claude --temp-root <仓库外临时目录>
```

运行目录保留 `evidence.json`，记录平台、版本/共享核心身份、A/B 隔离边界、自然语言轮次、B 决定摘要、耗时、观察到的文件和失败归因。CLI 默认在失败后重跑同一 Journey 一次，并把每次结果写入 `retries`；一致的 Runtime Adapter/SUT 失败保留原归因，结果不一致或一次重跑通过时标记环境/模型波动或通过，不把一次失败直接当作 Skill 缺陷。默认只保留证据并清理两个 Workspace；需要失败诊断时才使用 `--keep-workspace-on-failure`。

场景编写以当事人自然表述为准：事实卡提供事实、未知信息、用户目标和必要的明确选择，不向模型 A 提示内部命令、预期计算或 Oracle 判据。原始用户消息、模型 B 的事实选择、公开文件观察和失败归因分别保留；词句标记只作有限的过程线索，正式交付必须核验真实 DOCX 与独立清单。Harness 修复不得改写既有原始 evidence；场景内容变更后旧 Journey 通过记录只作历史基线。

### 律师阅案简报 ML04

`evals/suites/model-led-lawyer-case-brief.yaml` 包含三个独立场景，使用真实 Codex / Claude Code 模型 A 及既有 responder adapter。首次交付场景只有一轮用户输入，用来证明用户不再回复也能得到文档，因此该场景不会额外调用模型 B。改稿场景的后两轮由真实模型 B 选择事实卡，Harness 不伪造用户答复。

| 场景 | 核心判据 |
| --- | --- |
| `ML04-lawyer-case-brief` | 第一轮即得到同案当前版本的 `external_final` 简报。保留未提供截图及两种工资金额口径，简报只用一页概要。Harness 检查是否加了续页或已知来源错误，实际页数由 Word 渲染复核。 |
| `ML04-complex-hearing` | 首页概要包含用户抄录的开庭安排，后续只有一条共用时间线，并使用显式分页。通知和截图均未上传，不能冒充已阅原件。 |
| `ML04-postdelivery-edits` | 首交后才能要求隐名，隐名后才能更正入职日期。隐名保留档案事实，更正须更新事实并重建当前交付。各轮仍只展示一份 Word，其他正文按用户要求保持原样。 |

运行简报专项套件可用以下入口。Windows 建议选择较短的源码外临时根，避免嵌套安装目录触发路径限制。

```powershell
python -B -X utf8 -m scripts.model_led_agent_eval.cli suite evals/suites/model-led-lawyer-case-brief.yaml --platform codex --source . --temp-root <源码外短路径> --retries 0 --retain-deliveries
```

更换 `--platform claude-code` 可验证另一平台。默认模型及超时沿用本文设置。本套件以 Word 交付为目标，环境不具备文书能力时不能用降级回复记作该场景 PASS，也不由此直接认定 Skill 缺陷。

`expect.lawyer_case_brief` 声明首次交付轮次、正文及开头的备选词组、金额出处片段、禁止出现在正文或档案证据材料中的场景特定用语，以及有序修改卡。`complex_timeline` 为 `true` 时检查独立时间线和显式分页，为 `false` 时检查未插入时间线或显式分页，省略时不检查简报续页结构。loader 拒绝未知字段、无效轮次、未定义或初始修改卡、无法从场景追溯的新旧值。修改卡按最近一次已验证公开交付开放，B 只能看到交付是否有效和文书类型，不会收到内部正文、档案或断言。它仍须通过真实 responder 的事实 ID 校验。

独立 artifact observer 验证档案、DOCX、机器清单及内部核验清单。展示回执按类型校验文件数、顺序、类型、路径、摘要和案件身份，简报只允许 Word，其他类型仍遵循原附件规则。快照从 OOXML 独立读取正文，不复用 SUT 的 edit-context。丢失/篡改内部清单、过期档案、错案交付、候选替代可转交稿、第二轮才交付、金额出处交换及隐名污染事实均不能通过相应断言。

正文词组和金额邻近片段属于可重复的场景检查，不能独立证明完整语义正确。`opening_text` 只表示 OOXML 首个显式分页符之前的内容，不是 Office 实际排版页。真实运行后仍须在 Word/WPS 核对简单版一页、复杂版首页可独立理解、来源是转述还是已阅、相反材料是否紧邻、金额含义及问题是否有用。无自然分页溢出、语义保真和宿主附件可见须另留人工证据。`--retain-deliveries` 保留的内部清单仅作验收证据，不作为交给律师的附件。

确定性测试位于 `tests.agent_eval.test_lawyer_case_brief_journey`，纳入 fast 的 `model_led_agent_eval` 组。它使用独立正反例及一次公开 renderer 接缝验证，不能冒充真实 Journey。新增或改动场景后，旧 ML01—ML03 或单元测试通过记录不证明 ML04 已通过。

### 交付后更正电话的独立场景

canonical ML01 继续只验证首次交付。显式变体在 `expect.postdelivery_phone_correction` 声明 `fact_id`、`old_phone`、`new_phone`：旧号码必须出现在初始虚构用户消息，新号码必须在对应非初始更正卡中，其他事实不得依赖该终段卡。此场景要求完整申请书和证据目录，不能以降级路径替代改稿。

仅启用此配置时，runner 在每轮完成后保存 `public_artifact_snapshot`。独立 artifact observer 校验当前档案、两成员清单和文件 SHA，Harness 另核对公共 view；正文比较由标准库独立解包 OOXML，保留段落、表格单元格顺序、样式和编号属性，不复用 SUT 的 edit-context 提取逻辑。首交完整校验成功前，B 看不到更正卡；成功后 B 获得仅含验证结果和文书类型的公开观察，并须在下一轮选择更正卡。B 仍通过真实 responder 执行决定，未由 runner 伪造用户消息。

Oracle 要求更正卡前一轮有有效首交快照，最终快照同案、新 revision、新集合，两成员及四文件均绑定当前版本。仅允许指定旧号码替换为新号码，其余有序正文、表格和编号须保持一致；清单可随版本更新，未变目录 DOCX 的 SHA 不强制变化。外部写入导致 SHA 不符、只改一份、旧集合或“已改好”文字均不能放行。快照只进入源外虚构测试证据，不进入安装白名单、不成为案情事实源。此结果仍不能替代 WorkBuddy 原生 Office 路由及附件展示验证。

重新生成时保留的旧 generation 属于非交付材料。独立观察器仅排除符合受管备份命名、同案件/类型及完整三文件形状、且当前 canonical 已另行完整验证的旧备份；未提交 regeneration 另须与当前 render identity 一致。旧文件可能已被外部改写，因此不将其旧 SHA 校验结果当作当前文书结果，也不读取其正文；未知隐藏目录、错案/错类型或多余文件仍拒绝。当前 canonical 的 SHA、清单、版本和集合检查不变。

## 轮次与报告优化（2026-09-25）

- max_unavailable_uses 的原意是防止事实池覆盖不足时反复回答未知，但它会把第七个不同且合理的池外问题误判为必须停止。ML01、ML02 现在设为 null，由 max_turns 兜底；数值上限仍可用于确实需要限额的场景。unavailable 只能发送 Scenario 固定的“未核实、先按已知继续”文本，不得捏造案情、法律依据或高风险授权。
- ML01、ML02 首轮以自然语言一次给出主要案情；initial_fact_ids 把这些事实登记为已发送，避免模型 B 再投喂或在确认门槛上重复计数。ML01 的最终确认、ML02 的候选稿请求仍须在后续轮次由用户明确给出。ML02 在候选稿请求后可对制作范围另行确认；两案最低轮次为 2，ML01 最多 10 轮、ML02 最多 14 轮。ML02 的额外轮次容纳事实确认之后的候选稿请求与交付，不要求 A 在事实确认当轮同时完成风险说明和 DOCX 渲染。
- ML01 的 `confirmation_summary_required_groups` 向模型 B 提示欠薪、这期季度绩效奖金未收、加班的基本状态和本轮三项范围；词组仅作语义示例。权益范围收口与最终补充尚未完成时，摘要核对使用 `initial_summary_confirmation`；`rights_scan_scope` 和 `final_supplement_none` 均已在此前轮次发送后，才允许 `final_confirmation` 授权候选材料。初步与最终摘要确认都拦截已知事实冲突；确认按语义及已发送事实判断，不以轮次或固定词句播放，不要求逐项复述全部日期、金额和材料。确认卡与单位/工作地卡允许在各自次数上限内回应明确的再次核对，未知地址、金额和法律口径保持待核。
- ML01 仅当三项基本状态或本轮范围明显缺失时，请 A 简要补正一次；不用“具体信息我说不准”回应确认问题。模型 B 调用使用上述独立时限；完整 Journey 可按需求显式覆盖 `--simulator-timeout`。超时仍保留 Runtime Adapter 失败证据，不自动改写为 Skill 失败。
- ML02 的 DOCX 生成也使用模型 A 单轮 1200 秒默认值；单次定向排查可显式设 `--retries 0`，但不得放宽最终轮次完成和受管展示判据。
- 模型 A 的私有运行时上下文明确指定本次 `installed_skill_root/SKILL.md`，参考与工具使用同一安装根。Codex 轨迹中若出现读取其他标准发现目录中同名 ArbiBuddy 的绝对路径命令，Runtime Adapter 以 `skill_source_mismatch` 失败并记录目录哈希；不把混用旧安装版本的运行当成当前 SUT 验收。此检查覆盖可观察命令，不等于文件系统读取沙箱。
- 每次 Codex/Claude Code 运行在 evidence.json 旁自动生成 conversation.md；CLI 同时打印 conversation= 路径。Markdown 包含场景、客户端、结果、轮次、会话 ID、双方原文、场景回复器停止但未送达模型 A 的轮次，以及已接受但没有完成助手回应的最后一条场景消息。重跑后最终报告随最终 evidence 归因更新；每次尝试的原始运行目录保留。
- 要从已有 evidence 重新生成报告，可执行 python -B -X utf8 -m scripts.model_led_agent_eval.conversation_report --evidence <evidence.json> --output <仓库外报告路径.md>。人工 WorkBuddy 可执行 python -B -X utf8 -m scripts.model_led_agent_eval.conversation_report --workbuddy-transcript <transcript.jsonl> --journey-id ML01-v2 --output <仓库外报告路径.md>，并按实际场景替换 ID。WorkBuddy 报告仅展示 transcript 中真实存在的用户/助手消息和会话，结果仍以验收器为准；导出若无可见对话，命令报错，不补写臆测轮次。长期保存时将报告与原始 evidence/transcript 放在仓库外同一验收目录。
