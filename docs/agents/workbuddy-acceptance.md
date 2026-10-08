# WorkBuddy 客户端审计Harness

这是开发测试规范，不进入运行包，不承担案件编排。正常展示从scripts.documents.runtime_cli的只读view取得完整公开回执；candidate_ready与final_ready表示文件结果，不是对话阶段。档案/集合/请求/机器清单不展示；历史回执与当时文件摘要核验既有展示，不因当前修订改变追溯否定。

审计入口 scripts.workbuddy_acceptance.cli 只观察公开档案、DOCX、交付回执、原生trace与会话，按Harness、Scenario、Runtime Adapter、SUT/Skill、环境/模型波动归因。破坏性删除、案件越界、生成旁路与用户可见内部泄漏按直接行为证据判断。memory/USER.md 的宿主读写仅作运行时诊断观察，不是案件事实或文书工具门禁；事实进入业务结果才构成污染证据。

## 不交付文书的 ML03-v2

使用 --allow-no-delivery --journey-contract 配合封存的原生两个session与transcript。contract不由人工编辑：由build-no-delivery-contract生成，绑定case_id、两个 session、有序trace文件SHA、规范Scenario摘要、事实边界、公开transcript摘要和恢复里程碑。参数--workbuddy-trace-root指向源外原生证据根。修改transcript后必须重新生成contract，缺摘要或不匹配拒绝通过。

第二 session 首个成功的实质案件动作必须读取同一权威档案；恢复-only之后无文书生成/展示且没有产生案件写入或其他副作用。ML03-v2 恢复-only错误入口、提前契约参考读取（recovery_reference_read_before_current_archive）、目录/环境/源码探查，即使随后恢复，也计入恢复协议失败。其他场景可按明确证据将无副作用的失败只读transport计为recoverable_case_archive_transport_retry诊断；不得扩张到ML03-v2。

核心产品验收允许非阻断质量项，严格Scenario的原始FAIL仍保留。原生客户端打开与显示确认、当前真实模型Journey、离线确定性测试分开记录，不相互替代。测试证据和临时安装树只能进入源码外受控目录。
