# WorkBuddy 客户端审计Harness

这是开发测试规范，不进入运行包，不承担案件编排。正常展示从scripts.documents.runtime_cli的只读view取得完整公开回执；candidate_ready与final_ready表示文件结果，不是对话阶段。档案/集合/请求/机器清单不展示；历史回执与当时文件摘要核验既有展示，不因当前修订改变追溯否定。

审计入口 scripts.workbuddy_acceptance.cli 只观察公开档案、DOCX、交付回执、原生trace与会话，按Harness、Scenario、Runtime Adapter、SUT/Skill、环境/模型波动归因。破坏性删除、案件越界、生成旁路与用户可见内部泄漏按直接行为证据判断。memory/USER.md 的宿主读写仅作运行时诊断观察，不是案件事实或文书工具门禁；事实进入业务结果才构成污染证据。

## 案情简报的原生验收

ML04 的虚构用户输入和修改卡以 `evals/model-led/codex-claude-lawyer-case-brief.yaml` 及 `variants/ml04-*.yaml` 为场景来源。首次目的明确后不补发“确认定稿”，只收一份 Word。原生 view / present_files 应包含简报 Word，内部清单仍保存在受管目录用于校验，不作为用户附件。收到有效首交后才发送隐名卡，收到更新稿后才发送日期更正卡，并分别保留原生 trace、对话及当时文件摘要。

隐名可沿用同一档案修订和文件路径。审计通过已验证的用户新轮次、其后的重新渲染、新交付集合和变化后的 Word 摘要识别新交付。档案工具的 transport 按实际命令或工具名审计，模型元数据中的命令字样不视为工具调用。

按[ML04 检查边界](model-led-agent-eval.md#案情简报-ml04)核对实际排版、材料来源、金额口径和改稿事实。Codex/Claude CLI 的快照检查不替代 WorkBuddy 原生文件打开、附件展示或 Office 编辑验证；本节提供原生验收步骤，不代表已取得 WorkBuddy ML04 通过证据，也不扩展既有 ML03 专属 contract。

## 不交付文书的 ML03-v2

使用 --allow-no-delivery --journey-contract 配合封存的原生两个session与transcript。contract不由人工编辑：由build-no-delivery-contract生成，绑定case_id、两个 session、有序trace文件SHA、规范Scenario摘要、事实边界、公开transcript摘要和恢复里程碑。参数--workbuddy-trace-root指向源外原生证据根。修改transcript后必须重新生成contract，缺摘要或不匹配拒绝通过。

第二 session 首个成功的实质案件动作必须读取同一权威档案；恢复-only之后无文书生成/展示且没有产生案件写入或其他副作用。ML03-v2 恢复-only错误入口、提前契约参考读取（recovery_reference_read_before_current_archive）、目录/环境/源码探查，即使随后恢复，也计入恢复协议失败。其他场景可按明确证据将无副作用的失败只读transport计为recoverable_case_archive_transport_retry诊断；不得扩张到ML03-v2。

核心产品验收允许非阻断质量项，严格Scenario的原始FAIL仍保留。原生客户端打开与显示确认、当前真实模型Journey、离线确定性测试分开记录，不相互替代。测试证据和临时安装树只能进入源码外受控目录。
