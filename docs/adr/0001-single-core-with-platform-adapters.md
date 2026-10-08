---
status: accepted
---

# 使用单一核心 Skill 与平台适配层

ArbiBuddy 以仓库根目录的一份 Agent Skills 兼容核心实现承载全部业务流程，Codex、Claude Code、WorkBuddy、OpenClaw 和 Hermes 的差异只由适配层处理。相比维护五份平台副本，这一选择牺牲了部分平台专属优化，但能避免法律规则、风险门槛和文书流程分叉，并允许平台能力通过兼容性矩阵显式降级。

2026-10-08 分发补充：开发源码布局保持根目录，公开 `codex/source` 分支保留已筛选完整源码；公开 `main` 分支的 `skills/arbibuddy` 是按共享运行资源白名单生成的安装视图，不手工维护第二份核心。通用安装器只复制文件，平台无关发布清单在运行时校验完整性。流程见[分发维护](../agents/skills-distribution.md)。
