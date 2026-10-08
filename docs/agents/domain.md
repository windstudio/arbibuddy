# 单核心架构

共享Skill负责采集、权益发现、法律分析、方案与完整文书正文。平台适配器负责安装、发现、能力与运行身份，不维护平台业务副本。

Markdown案情档案是案件事实的唯一权威来源。档案工具保护修订、并发与原子写入；金额工具执行确定性算术；文书工具保护锁定字段、候选/定稿边界、OOXML与原子交付。脚本不决定法律策略、不编排业务对话。

运行资源唯一清单为 `scripts/runtime_resources.py`；开发Harness、测试、docs不进入安装包。当前设计理由见 [ADR-0008](../adr/0008-model-led-orchestration-and-minimal-tools.md) 和 [ADR-0009](../adr/0009-model-authored-documents-and-lightweight-finalization.md)。历史技术映射见 [摘要](domain-migration.md)，不是旧路径兼容承诺。
