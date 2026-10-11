# 平台能力与降级

支持 Codex、Claude Code、WorkBuddy。适配器负责安装、身份核对、能力观察和文件展示，不维护业务状态或替模型推进对话。安装成功或平台宣传不能替代当前能力的实际观察。

## 能力观察

- `filesystem`：案件目录的读取、写入、摘要校验和原子替换是否可用。
- `network`：当前任务能否访问要求的官方来源。
- `scripts`：能否执行随 Skill 提供的 Python 模块。
- `word`：能否导入 `python-docx` 并生成 DOCX。
- `structure_inspection`：能否检查 DOCX 的正文、样式、页脚和表格等 OOXML 结构。

探测结果记录方法、时间及“可用、未实测、不可用”，不能互相推断。联网探测跳过属于未实测。遇到宿主权限限制时按宿主现有授权机制处理，并保留不含凭据的错误类别；普通搜索结果、网络连通或网站可访问都不能替代案件法源核验。动态核验及其降级以[法律核验参考](legal-verification.md)为准。

`attachment_presentation` 单独观察。本地 `present-files` 只核对文件清单可读；创建文件或打印路径不能证明宿主已经展示。只有原生展示通道的成功回执才能报告展示成功，缺少证据时明确未观察。

## 文书与安装边界

模型按[文书契约](document-render.md)起草全文、声明本轮交付集合并调用 `document.render`。工具校验锁定要素、档案绑定和 OOXML，输出 DOCX、内部核验清单及机器回执。集合全部成功后从受管只读 view 获取展示参数并结束本轮；候选稿仍需补核，不能称正式版。提交或发送前由用户在 Word/WPS 预览字段、金额、分页和落款，该提示不增加机器验收门槛。

WorkBuddy 文书包携带 `requirements-documents.txt` 和锁定目标 Python 的 vendor 层。安装前须验签，目标解释器在禁用用户 site 后须能生成 DOCX 并检查 OOXML。原生上传通过包内便携身份标记核对；受管安装通过绑定回执与外部信任锚核对，不能混用恢复方法。宿主原生注册、触发和显示分别观察；热重载不等于注册成功。

## skills CLI 安装

Codex、Claude Code 可以通过 `npx skills add windstudio/arbibuddy` 安装。分发树只含运行资源与 `arbibuddy.distribution.json`，不依赖平台专用安装回执或安装后 Hook；通用清单绑定版本、源码提交和完整资源摘要，公共工具在使用前验证。清单用于检测资源缺失、修改和额外执行文件，不是发布者数字签名。安装器创建的 Skill 根目录链接允许使用，Skill 树内部的资源链接仍拒绝。

Node.js 和 Git 用于安装；Python 3.11+ 用于受管工具。此安装方式不包含 WorkBuddy 的 Windows vendor 层，生成 DOCX 需要宿主 Python 已安装 `requirements-documents.txt` 中的依赖。缺少依赖时说明环境准备方法，等待用户完成后重试，不在案件工具运行中安装依赖。通用清单损坏或资源漂移时，重新从同一可信仓库安装；已有平台受管安装回执失败时不能回退到通用清单。开发维护和生成流程见源码分支的 `docs/agents/skills-distribution.md`，不进入案件回复。

从旧受管安装切换到 skills CLI 时，安装器可能保留 Skill 目录旁的旧 `arbibuddy.install.json`。工具只有在新分发包完整校验通过、旁置回执仍失败时才报告“安装通道冲突”，案件操作继续停止。向用户说明需要安装维护核验来源、备份移走旧回执并重启；重复 `skills add` 不会清理旁置回执。该处理由维护流程执行，案件对话不修改安装树或回执；skills CLI 使用完整性清单，恢复指引不要求 WorkBuddy 验签包。

Codex 在 Windows 上可用原生命令工具指定 `workdir=<installed_skill_root>`，单独执行契约中的 `python -B -X utf8 -m ... --root <workspace_root>`。该方式与固定 `cd ... && python ...` 前缀具有相同的执行目录和工作区绑定，不依赖 WSL；Bash 启动失败时可使用原生 PowerShell 的单条 Python transport，仍以公开 JSON 回执判断成功。

### SkillHub 安装

WorkBuddy 从 SkillHub 安装的通用仓库分发包采用相同的完整性清单，不包含验签 WorkBuddy ZIP 的 vendor 层。文书能力依赖宿主 Python 和 `requirements-documents.txt` 中的依赖；生成前按[文书契约](document-render.md)的 `runtime-check` 选择已经准备好的环境，没有可用环境时先提示用户准备依赖。SkillHub 的发布、安装元数据与图标只作为宿主附加文件，不是版本或案件事实来源。

此通道完整性失败时，提示从同一可信 SkillHub 来源安装适配后的更新包并完全重启客户端；旧包缺少运行资源时，重复安装同一旧包不能恢复。安装维护在源码和分发流程中完成，案件对话不补写版本文件、不删除宿主元数据、不修改校验清单，也不将通用分发安装引导到原生上传验签包的恢复流程。

## 降级与恢复

- 文件系统不可用：提供可由用户保存的案情内容，说明不能持久化；不宣称档案已保存。
- 联网不可用：动态核验标记未完成，降低结论强度，保留待核事实和官方来源线索。
- 脚本或 DOCX 依赖不可用：继续可完成的信息整理，说明未生成 DOCX及可行安装恢复方法。
- OOXML 检查失败、调用失败或回执摘要不一致：停止该交付，不展示不合格文件。

能力不足不能省略事实确认、知情选择、档案保护或文件检查。运行期不安装依赖、不创建虚拟环境、不执行临时生成脚本，也不通过手工删除、复制、改名或补写产物掩盖失败。失败后按公共工具返回的恢复方法处理。
