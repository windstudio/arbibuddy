# Codex 与 Claude Code 模型主导 Agent Eval

该 Eval 是独立真实模型验证入口，不进入 `fast`、`slow` 或 `full` 测试套件，也不调用旧的结构化状态 Runtime。

以下完成路径与 PASS/FAIL 是严格 Scenario 指标。产品验收按[模型主导产品验收](model-led-product-acceptance.md)另列核心结果、实质风险与非阻断质量项；不要求所有对话完全符合预设轨迹，也不改写原始失败记录。候选准备有条件接受不等于可外发定稿或正式发布。

每条 Journey 都会在外部临时根创建独立的 Agent Workspace 和 ScenarioResponder Workspace，将当前工作树以 `copy` 安装到对应平台的发现目录，然后用平台原生 CLI 驱动模型 A。模型 B 通过独立 Codex SDK thread 读取有限 Scenario 上下文并只返回事实 ID/动作；Harness 确定性校验决定并从 Scenario 原文渲染消息。Harness 只观察安装回执、会话耗时、案情档案和公开文件副作用，不读取内部状态、不把模型 B 当作裁判。模型 B 动态扮演用户是本 Eval 的真实 Journey 组成部分：它只能依据隔离 Scenario 的既定事实回应模型 A 当前问题；无匹配事实时回答未知或安全停止，记录为 Scenario 覆盖/收敛证据，不自动判定 Skill 失败。

套件包含三类显式完成路径：工资/奖金/加班并交付仲裁材料；解除路径、高风险确认并交付通知书候选材料；中断恢复、事实纠正与动态官方核验不可用时的降级。完成路径必须满足交付触发事实、能力边界、公开产物、重启后里程碑和响应组；早期出现“无法/待补”等普通措辞不能单独放行。若当前宿主没有可调用的 Word 文书能力，前两条路径必须观察到模型明确拒绝虚称 DOCX 并给出合法降级，而不是把 Markdown 当作 DOCX；有能力时则必须观察到 canonical DOCX 与独立清单。每条路径在 Codex 和 Claude Code 各运行一次，共六个隔离 Journey。

显式运行全部平台和三条路径（实现阶段不隐式执行）：

```powershell
python -B -X utf8 -m scripts.model_led_agent_eval.cli suite evals/suites/model-led-codex-claude.yaml --source . --temp-root <仓库外临时目录>
```

默认模型：Codex 模型 A 和模型 B 均为 `gpt-6-luna`；Claude Code 模型 A 为 `sonnet`，模型 B 仍为 `gpt-6-luna`。可用 `--model`、`--codex-model`、`--claude-model` 或 `--simulator-model` 显式覆盖。

所有 Codex/Claude Code 真实 Journey 的模型 A 单轮上限默认 **1200 秒**，`run` 与 `suite` 一致，ML01、ML02、ML03 均适用。`--timeout-per-turn` 可显式覆盖；模型 B 的 `--simulator-timeout` 独立计时，默认 **300 秒**。超时必须保留失败证据，不得视作完成或放宽 Oracle 门禁。

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

## 轮次与报告优化（2026-09-25）

- max_unavailable_uses 的原意是防止事实池覆盖不足时反复回答未知，但它会把第七个不同且合理的池外问题误判为必须停止。ML01、ML02 现在设为 null，由 max_turns 兜底；数值上限仍可用于确实需要限额的场景。unavailable 只能发送 Scenario 固定的“未核实、先按已知继续”文本，不得捏造案情、法律依据或高风险授权。
- ML01、ML02 首轮以自然语言一次给出主要案情；initial_fact_ids 把这些事实登记为已发送，避免模型 B 再投喂或在确认门槛上重复计数。ML01 的最终确认、ML02 的候选稿请求仍须在后续轮次由用户明确给出。ML02 在候选稿请求后可对制作范围另行确认；两案最低轮次为 2，ML01 最多 10 轮、ML02 最多 14 轮。ML02 的额外轮次容纳事实确认之后的候选稿请求与交付，不要求 A 在事实确认当轮同时完成风险说明和 DOCX 渲染。
- ML01 的 `confirmation_summary_required_groups` 向模型 B 提示欠薪、这期季度绩效奖金未收、加班的基本状态和本轮三项范围；词组仅作语义示例。权益范围收口与最终补充尚未完成时，摘要核对使用 `initial_summary_confirmation`；`rights_scan_scope` 和 `final_supplement_none` 均已在此前轮次发送后，才允许 `final_confirmation` 授权候选材料。初步与最终摘要确认都拦截已知事实冲突；确认按语义及已发送事实判断，不以轮次或固定词句播放，不要求逐项复述全部日期、金额和材料。确认卡与单位/工作地卡允许在各自次数上限内回应明确的再次核对，未知地址、金额和法律口径保持待核。
- ML01 仅当三项基本状态或本轮范围明显缺失时，请 A 简要补正一次；不用“具体信息我说不准”回应确认问题。模型 B 调用使用上述独立时限；完整 Journey 可按需求显式覆盖 `--simulator-timeout`。超时仍保留 Runtime Adapter 失败证据，不自动改写为 Skill 失败。
- ML02 的 DOCX 生成也使用模型 A 单轮 1200 秒默认值；单次定向排查可显式设 `--retries 0`，但不得放宽最终轮次完成和受管展示判据。
- 模型 A 的私有运行时上下文明确指定本次 `installed_skill_root/SKILL.md`，参考与工具使用同一安装根。Codex 轨迹中若出现读取其他标准发现目录中同名 ArbiBuddy 的绝对路径命令，Runtime Adapter 以 `skill_source_mismatch` 失败并记录目录哈希；不把混用旧安装版本的运行当成当前 SUT 验收。此检查覆盖可观察命令，不等于文件系统读取沙箱。
- 每次 Codex/Claude Code 运行在 evidence.json 旁自动生成 conversation.md；CLI 同时打印 conversation= 路径。Markdown 包含场景、客户端、结果、轮次、会话 ID、双方原文、场景回复器停止但未送达模型 A 的轮次，以及已接受但没有完成助手回应的最后一条场景消息。重跑后最终报告随最终 evidence 归因更新；每次尝试的原始运行目录保留。
- 要从已有 evidence 重新生成报告，可执行 python -B -X utf8 -m scripts.model_led_agent_eval.conversation_report --evidence <evidence.json> --output <仓库外报告路径.md>。人工 WorkBuddy 可执行 python -B -X utf8 -m scripts.model_led_agent_eval.conversation_report --workbuddy-transcript <transcript.jsonl> --journey-id ML01-v2 --output <仓库外报告路径.md>，并按实际场景替换 ID。WorkBuddy 报告仅展示 transcript 中真实存在的用户/助手消息和会话，结果仍以验收器为准；导出若无可见对话，命令报错，不补写臆测轮次。长期保存时将报告与原始 evidence/transcript 放在仓库外同一验收目录。
