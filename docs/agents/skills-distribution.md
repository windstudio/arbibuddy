# skills CLI 分发维护

唯一核心源码仍在开发仓库根目录。公开仓库 `codex/source` 分支保存已筛选的完整源码，`main` 分支是从该源码生成的分发视图：根目录只有用户说明、许可、版本、换行约束和 SOURCE.md 来源说明，安装入口位于 `skills/arbibuddy/SKILL.md`。安装目录不是手工维护的第二份业务实现。源码或参考变更先进入源码分支，再重新生成 main；禁止把私有开发树整库发布。

生成器只复制 `scripts.runtime_resources.RUNTIME_FILES`，额外加入 `arbibuddy.distribution.json`。目标必须是源码树外的新目录，失败不覆盖既有输出；生成时核对完整资源集与摘要。清单记录版本、来源提交和运行身份摘要，没有路径、案件或平台信息；它检测漂移，不提供数字签名。来源提交是生成输入的 Git HEAD，发布时必须先提交并检查 source 干净，再从该提交生成；不能把脏工作树的构建称为对应提交的源码副本。

```powershell
git clone --branch codex/source https://github.com/windstudio/arbibuddy.git arbibuddy-source
cd arbibuddy-source
python -B -X utf8 -m scripts.release.skills_distribution --source . --output <源码树外的新目录>
```

`main` 的根目录不能有 SKILL.md，否则 skills CLI 优先选择根而复制开发资料。完整源代码、测试、WorkBuddy构建和受管安装说明在 `codex/source` 分支；固定 Release 标签仍保存原布局。此通道不创建或改写 WorkBuddy 正式 ZIP，若另行发布 ZIP，继续执行项目正式 dist 根目录与发布门禁规则。

发布前用实际 `npx skills add <本地生成目录>` 核验唯一发现、Codex/Claude Code copy 与默认 symlink、隔离的 global 路径及 update；从安装副本运行公开档案与文书工具。验证正常和篡改/额外文件拒绝，不能用安装器打印成功替代运行验证。安装后不保留 Git 元数据或开发文件；既有安装回执、WorkBuddy标记和外部信任锚优先，失败不回退到通用清单。

Python 3.11+ 与 python-docx 依赖由宿主预备；skills CLI 只安装文件，不安装 Python 或依赖。运行能力不足按平台参考降级。测试产物置于源码外并按制品生命周期清理，只保存小型摘要。
