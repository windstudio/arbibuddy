# skills CLI 分发维护

唯一核心源码仍在开发仓库根目录。公开仓库 `codex/source` 分支保存已筛选的完整源码，`main` 分支是从该源码生成的分发视图：根目录只有用户说明、许可、版本、换行约束和 SOURCE.md 来源说明，安装入口位于 `skills/arbibuddy/SKILL.md`。安装目录不是手工维护的第二份业务实现。源码或参考变更先进入源码分支，再重新生成 main；禁止把私有开发树整库发布。

生成器以 `scripts.runtime_resources.RUNTIME_FILES` 为唯一白名单，额外加入 `arbibuddy.distribution.json`。通用安装视图把源码 `VERSION`、`LICENSE` 派生为 `VERSION.txt`、`LICENSE.txt`，安装视图 README 的许可链接同步调整；开发仓库仍保留唯一 VERSION 来源。身份按实际分发文件名和字节重新测量，不能沿用源码布局的旧清单。目标必须是源码树外的新目录，失败不覆盖既有输出；生成时核对完整资源集与摘要。清单记录版本、来源提交和运行身份摘要，没有路径、案件或平台信息；它检测漂移，不提供数字签名。来源提交是生成输入的 Git HEAD，发布时必须先提交并检查 source 干净，再从该提交生成；不能把脏工作树的构建称为对应提交的源码副本。

```powershell
git clone --branch codex/source https://github.com/windstudio/arbibuddy.git arbibuddy-source
cd arbibuddy-source
python -B -X utf8 -m scripts.release.skills_distribution --source . --output <源码树外的新目录>
```

`main` 的根目录不能有 SKILL.md，否则 skills CLI 优先选择根而复制开发资料。完整源代码、测试、WorkBuddy构建和受管安装说明在 `codex/source` 分支；固定 Release 标签仍保存原布局。此通道不创建或改写 WorkBuddy 正式 ZIP，若另行发布 ZIP，继续执行项目正式 dist 根目录与发布门禁规则。

发布前用实际 `npx skills add <本地生成目录>` 核验唯一发现、Codex/Claude Code copy 与默认 symlink、隔离的 global 路径及 update；从安装副本运行公开档案与文书工具。验证正常和篡改/额外文件拒绝，不能用安装器打印成功替代运行验证。安装后不保留 Git 元数据或开发文件；既有安装回执、WorkBuddy标记和外部信任锚优先，失败不回退到通用清单。

## 跨安装通道切换

旧受管安装在 Skill 目录旁保存 `arbibuddy.install.json`；skills CLI 换树不会管理或删除该文件。验收须覆盖“旧受管回执 + 新分发树”，不能只用干净用户目录。运行时仍优先验证回执；仅当新分发清单与完整资源集合独立通过、旁置回执失败时，附加 `installation_channel_conflict` 诊断，保持 `verified=false`，不自动回退或修改回执。

维护恢复顺序：确认用户已选择可信仓库的 skills CLI 通道，独立核对分发清单身份摘要、所有资源摘要和完整集合；确认旁置回执绑定当前安装且属于旧安装，再把原回执原样备份到 Skill 树外并移走有效回执名。重启后从安装副本启动新进程执行公开 `create`、`read-current`，检查 `ok=true`；使用虚构案件，不改用户案件。保留回执备份与修复前后的小型证据。分发包自身校验失败、来源不明、内置受管/WorkBuddy 标记冲突时不按此路径处理。

Python 3.11+ 与 python-docx 依赖由宿主预备；skills CLI 只安装文件，不安装 Python 或依赖。运行能力不足按平台参考降级。测试产物置于源码外并按制品生命周期清理，只保存小型摘要。

## SkillHub 固定规则适配

SkillHub GitHub 授权发布过滤 VERSION、LICENSE，并在服务端加入 `_meta.json`；WorkBuddy 安装时可再加入 `_skillhub_meta.json` 与 `_icon.png`。按平台规则适配分发视图，不依赖平台保留无扩展名文件。SkillHub 更新入口要求上传根含 SKILL.md、最多200文件、总大小不超过10 MB；生成器对安装视图的未压缩文件总量按保守的10,000,000字节限制检查，超限不晋级输出。

GitHub 导入选择生成视图中的 `skills/arbibuddy`。手动上传目录/ZIP时只取该目录内容，以 SKILL.md 位于上传根为准，不上传整库、测试或开发资料。正式更新仍从干净来源提交生成；候选构建不能称为已发布或平台验收完成。

宿主附加文件仅在分发清单通道接受：两个JSON限16 KiB、固定字段集合、slug和版本须匹配、时间为非负整数、安装来源与命名空间绑定；图标限1 MiB并检查PNG标识。链接、目录冒充、未知文件、额外脚本、非法元数据仍失败关闭。元数据只作为非执行宿主文件，不参与身份来源或资源摘要。源码/受管安装/便携WorkBuddy包沿用原有身份与集合规则；旧分发布局仍可读取，但缺文件的旧SkillHub包不会被自动补齐或放行。

验收覆盖服务端仅附加发布元数据、WorkBuddy完整附加文件、原生JSON实际样例、新进程create/read-current及文书render/view；同时覆盖必需资源丢失/篡改、非法元数据、未知文件、目录/链接冒充、旧安装回执优先和上传数量/大小边界。实际平台上传、下载及新会话结果独立记录，不能以模拟过滤测试替代。
