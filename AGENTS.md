# 开发规则

正常产品路径由 `SKILL.md` 路由。修改入口或参考时保持渐进披露：主路径与关键门禁放入口，条件细节放唯一权威参考。

- 修改业务/工具责任边界前读 [架构](docs/agents/domain.md) 和相关 ADR。
- 执行测试前读 [分层测试](docs/agents/testing.md)；真实模型Journey不在确定性套件中隐式执行。
- 构建或保存证据前读 [制品规则](docs/agents/artifact-lifecycle.md)；输出放源码外，完整会话保持私有。
- 评审先固定对象与SUT边界，按Harness、Scenario、Runtime Adapter、SUT/Skill、环境/模型波动归因；测试失败只是待归因证据。

README面向使用者，保留作者引言；开发、测试与验收细节放docs。原始FAIL保留；按实际结果与实质后果验收，非阻断质量项记录改进，不要求固定措辞、卡序或工具次数。
