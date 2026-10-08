# ArbiBuddy（打工人维权小帮手）

面向中国大陆劳动者的劳动仲裁与劳动争议信息辅助。用于案情采集、主张与证据梳理、金额测算、协商调解、仲裁准备和后续程序指引；仅在用户提及劳动仲裁、劳动争议、欠薪、被迫降薪、被迫解除、违法解除或终止、未签劳动合同，或要求生成劳动仲裁/劳动争议相关文书时使用；全国通用规则优先，上海专项增强，不替代律师或有权机关判断，不承诺胜诉。

## 引言

刚刚经历了人生中的第一次劳动仲裁。

从被背刺后的痛苦挣扎、自我怀疑、迷茫纠结，到不甘、醒悟，再到最终拿起法律武器反击，这段经历对我来说，不只是一次仲裁，更是一场真实而艰难的蜕变。

我是那种喜欢研究、在意细节、在朋友眼中甚至有点“轴”的人：做项目调研喜欢刨根问底，装修小家也能把自己折腾成半个专家。这次准备仲裁，也不例外。

在整个过程中，我查阅了大量法律法规和相关资料，也和 GPT、Claude、豆包等大模型反复问答、推敲细节，同时有幸得到了专业律师的支持。这个过程让我一方面真切感受到，随着模型能力的进步，专业法律知识正在变得更加可获取，普通人也有机会更系统地理解和维护自己的权利；另一方面，也更清楚地看到，专业人士在实务判断、策略取舍和细节把控上，依然有着通用模型难以替代的经验和价值。

准备仲裁的过程中，我一直有一种强烈的分享欲：希望把自己一路摸索、学习和踩坑获得的知识，整理成一个可以增强通用模型能力的 Skill，帮助更多像我一样的普通劳动者，在维护自身权益时，多一些参考和支持，少走一些弯路。也希望让这次人生中勇敢的行动，产生更多一点的价值。

本 Skill 仍处于早期试用阶段，仅覆盖一些常见的劳动仲裁争议场景，内容也仍在持续完善中。它不能替代专业律师的法律服务，但希望能在了解自身权益、梳理案情、整理证据、准备文书、理解流程等方面，提供一些有用的辅助。后续我会持续迭代，也期待您在试用过程中提出反馈，一起把它优化得更好。

特别感谢青年律师付国峰在仲裁过程以及 Skill 法律专业审核方面提供的帮助。如需专业法律咨询服务，可联系小红书“付国峰律师”，也可以通过我转发联系方式。

## 最近更新

补充 Codex 与 Claude Code 的标准安装、更新、发现、版本识别、卸载和能力降级支持；文书交付继续提供 DOCX 与独立核验清单，平台能力不足时会明确说明限制。

## 能做什么

- 以风险优先、每轮单问和可追溯的 Markdown 案情档案采集事实；
- 区分用户陈述、证据支持事实和分析假设，辅助形成主张、证据矩阵和金额计算；
- 对劳动关系、欠薪、工资差额、绩效/年终奖、加班、未签合同二倍工资、违法解除或终止、被迫解除、年休假等常见问题提供分层辅助；
- 对工伤、职业病、竞业限制、劳务派遣、股权激励、涉外用工等复杂问题进行风险识别、来源核验和专业复核提示；
- 生成仲裁申请书、证据目录、答辩书、催告函、被迫解除通知书、财产保全申请书、强制执行申请书及上海专项申请材料等相关文书。

## 如何使用

在受支持的平台安装 Skill 后，直接用自然语言描述劳动争议或文书需求。按提示逐步补充事实、证据和目标；案情档案保存在案件目录 `.arbibuddy/cases/`，可以使用占位符，不必在测试或草稿中提供不必要的真实身份信息。

生成候选材料或正式文书后，请在 Word/WPS 中复核姓名、日期、金额、分页和落款。需要解除、保全、执行、和解或放弃权利等重要行动时，先确认后果并考虑专业复核。

不要把真实身份证号、联系方式、住址、银行账号、未脱敏证据或真实案情提交到仓库、公开 Issue、测试目录或发布包。

## 安装

核心 Skill、案情档案、分析、计算和平台打包使用 Python 标准库。Python 3.11+ 环境可在仓库根目录执行：

```powershell
git clone https://github.com/windstudio/arbibuddy.git
cd arbibuddy
python -B -X utf8 -m scripts.platform_adapters.cli inspect-source --platform codex --source .
```

Codex 项目级安装：

```powershell
python -B -X utf8 -m scripts.platform_adapters.cli install --platform codex --scope project --target-root <项目目录> --source . --mode copy
```

Claude Code 项目级安装：

```powershell
python -B -X utf8 -m scripts.platform_adapters.cli install --platform claude-code --scope project --target-root <项目目录> --source . --mode copy
```

更新使用相同平台和项目目录执行 `update`；卸载执行：

```powershell
python -B -X utf8 -m scripts.platform_adapters.cli uninstall --platform codex --scope project --target-root <项目目录>
```

Claude Code 只需将平台参数替换为 `claude-code`。安装后的能力与降级说明见[平台能力边界](references/platform-capabilities.md)。

WorkBuddy 安装：从 [1.2.7 发布页](https://github.com/windstudio/arbibuddy/releases/tag/v1.2.7) 下载 `arbibuddy-workbuddy.zip`，在客户端选择“上传技能”，安装后完全重启客户端或新建会话。安装包已带所需文书依赖，普通使用不需要自行构建或安装编译工具。

需要自行构建或修改依赖的开发者，请查看[WorkBuddy构建说明](docs/platforms/workbuddy.md)和发布页提供的配套源码、构建及替换说明。

原生上传只依赖包内 `arbibuddy/arbibuddy.runtime.json` 便携标记，不会生成受管安装回执；出现便携标记缺失或陈旧时，重新上传已验签 ZIP 并完全重启客户端，不要手工创建 `arbibuddy.install.json`。

目前支持 Codex、Claude Code 和 WorkBuddy；实际能力以当前环境的探测与使用结果为准。

独立运行统一 Word 生成器时安装直接依赖：

```powershell
python -m pip install -r requirements-documents.txt
```

文书请求由 Skill 的统一 `document.render-v1` 能力处理，普通使用不需要直接运行仓库内部文书脚本。

## 能力与降级

平台会实际探测文件系统、联网、脚本、Word 生成和 OOXML 结构检查能力，同时验证 UTF-8 路径及读写权限。缺少文件系统、脚本或 Word 生成器时，不能声称已生成正式 DOCX；缺少联网时，会降低动态规则核验强度并标明待核实事项。详细规则见[平台能力与降级契约](references/platform-capabilities.md)。

## 文书生成轻量化设计

DOCX 根据模型起草的全文生成，并通过字段、内容和结构检查；运行时不依赖 LibreOffice、Poppler 或视觉复核。普通用户仍应在 Word/WPS 中复核字段、金额、分页和落款。

## 免责声明

ArbiBuddy 只提供信息整理和材料准备辅助，不提供法律服务。法律规则、时效和地方口径可能变化；采取解除劳动合同、保全、执行、和解或放弃权利等重要行动前，应核对现行官方规则，并考虑寻求合格专业人士复核。

## 许可证

[MIT License](LICENSE)
