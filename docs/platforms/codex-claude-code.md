# Codex 与 Claude Code 安装及能力验证

本仓库根目录是唯一的 ArbiBuddy 核心 Skill。安装器只复制或链接这一目录的发布资源，不生成平台业务副本。以下命令在仓库根目录运行。

## Codex

Codex 项目级和用户级 Skill 的发现目录均为 `.agents/skills/`。`agents/openai.yaml` 是 Codex 的界面元数据。

项目级复制安装：

```text
python -m scripts.platform_adapters.cli install --platform codex --scope project --target-root <项目目录> --source . --mode copy
```

用户级复制安装（`<用户目录>` 是用户主目录）：

```text
python -m scripts.platform_adapters.cli install --platform codex --scope user --target-root <用户目录> --source . --mode copy
```

安装目标为 `.agents/skills/arbibuddy`。官方依据：[Codex Skills](https://developers.openai.com/codex/skills)。

ArbiBuddy 正常运行不需要额外的客户端事件或信任配置。安装器只处理 Skill
文件，不创建或修改客户端配置。

已有目录安装使用 `update` 刷新 Skill、重写安装回执并复验版本与资源摘要。

`update` 只支持 `--mode copy`，不会把普通目录更新伪装成链接；需要链接时请对新目标重新执行 `install --mode link`。更新会先在同文件系统暂存、完整复验，再可恢复换入。

## Claude Code

Claude Code 项目级 Skill 位于 `.claude/skills/`，个人 Skill 位于用户目录的 `.claude/skills/`；官方文档说明这些目录可以链接到其他目录。

项目级复制安装：

```text
python -m scripts.platform_adapters.cli install --platform claude-code --scope project --target-root <项目目录> --source . --mode copy
```

如源码目录位于项目目录之外，也可将 `--mode copy` 改为 `--mode link`。用户级安装把 `--scope` 改为 `user`，并令 `<项目目录>` 指向用户目录。安装目标为 `.claude/skills/arbibuddy`。官方依据：[Claude Code Skills](https://code.claude.com/docs/en/skills)。

已有目录安装同样使用 `update` 刷新 Skill、重写安装回执并复验版本与资源摘要。

安装或更新后必须新建 Claude Code 会话再调用 `/arbibuddy`，不要在已经自行选择过其他 DOCX 生成路径的旧会话中继续验收。新会话首次案情采集应实际创建 `<当前项目>/.arbibuddy/cases/<案件编号>/案情档案.md`；文书生成只能通过 `document.render-v1` 的 `scripts.documents.public.render(request, workspace_root)`，使用 10 个顶层章节的 `CaseArchive`。不得调用 Claude Code 自带 docx Skill 或临时生成脚本。

Claude Code 的真实加载与执行必须在同一会话的 transcript 中分别取证，不把“安装成功”当成“已执行”。验收器按以下状态报告：`not_discovered`（没有可验证的项目 Skill 发现）、`not_invoked`（已安装但本轮没有调用）、`loaded_not_executed`（观察到 `/arbibuddy` 展开或结构化 Skill 调用，但尚未出现当前案件的 `CaseArchive.create/read`）、`executed`（Skill 加载后进入公开档案接缝）、`executed_then_bypassed`（已进入档案接缝后又改走旧 CLI、源码/临时脚本、通用 DOCX 或非受管展示）。若已展开 Skill 却读取 `scripts/case_archive` 源码猜契约，报告 `loaded_then_contract_discovery_bypass`，该证据优先作为执行旁路处理。结构化 Skill 调用、`<command-name>/arbibuddy</command-name>`、项目 `.claude/skills/arbibuddy/SKILL.md` 路径和实际注入的 `name/description` 只证明相应加载阶段；最终行为仍以当前案件档案、公开工具调用和受管产物为准。

## 发现验证

安装命令会在返回成功前验证安装回执、`SKILL.md`、核心资源和平台元数据。也可独立复验：

```text
python -m scripts.platform_adapters.cli verify-install --platform <codex|claude-code> --skill-root <安装后的arbibuddy目录>
```

返回的 `discovery_contract_valid` 和 `resources_complete` 必须均为 `true`。这只证明安装符合平台发现目录契约，不冒充平台运行时已加载。

再由实际平台 CLI 验证：

```text
python -m scripts.platform_adapters.cli verify-platform --platform <codex|claude-code> --skill-root <安装后的arbibuddy目录> --platform-command <平台CLI>
```

Codex 使用本地 `debug prompt-input` 解析一条普通劳动争议请求，能证明该自然语言入口被运行时发现，返回 `level: runtime-discovery`。Claude Code 在不向外部模型发送仓库内容的前提下，用本地 `--bare --help` 验证技能解析契约，返回 `level: local-platform-contract` 及 `runtime_discovered: false`；Claude 的实际加载由独立的真实模型主导 Journey 以普通用户消息验证，不使用 Skill 命令触发。

## 当前会话能力探测

平台版本不代表会话权限。每个实际运行环境都应分别探测文件、联网、脚本、统一 Word 生成器和 OOXML 结构检查能力，并保留版本、安装方式与 UTC 测试时间：

```text
python -m scripts.platform_adapters.cli probe --platform <codex|claude-code> --skill-root <安装后的arbibuddy目录> --platform-command <平台CLI> --workspace <可写目录> --network-check official-source --network-url https://www.gov.cn/
```

探测分别报告 DOCX 生成和 OOXML 结构检查，不依赖系统级转换工具。把联网探测设为 `--network-check skip` 时联网记为“未测试”。文件、脚本、Word 或结构检查能力不足时必须按[平台能力与降级契约](../../references/platform-capabilities.md)处理，不得根据平台名称推定能力。

## 动态核验与能力降级

Codex 与 Claude Code 都从普通自然语言消息开始；平台适配器只负责把当前会话的文件、脚本、Word、OOXML 和联网探测结果交给 Skill。联网探测不可用时，Skill 应降低动态规则结论强度，保留“待核实”事项并继续可安全完成的案情档案和候选材料，不把平台名称或一次权限结果伪装成官方核验成功。

真实模型主导 Journey 会记录每轮耗时、自然语言阶段、能力探测、案情档案和公开文件副作用。平台权限或网络失败按 Runtime Adapter/环境边界记录，用户可见回复仍只说明影响和下一步，不展示内部命令、JSON、案件 ID 或临时路径。
