# 模型主导案情档案提交器

本参考是 `create`、`read`、`commit` 三个档案操作的唯一公开契约。它只保护 Markdown 案情档案的持久化一致性，不决定下一问题、权益结论、法律适用、计算公式、文书类型或用户回复。

## 何时使用

- 纯通用法律知识问答不强制创建案件。
- 用户开始描述具体劳动争议、需要跨轮继续、需要记录事实修正、材料线索、分析结果或恢复中断时，模型尽早创建或读取档案。
- `create` 不收集姓名、身份证号、电话、地址或单位全称；主体隐私只在当前交付目标确实需要时，由模型通过后续 `commit` 记录。
- 每轮回复最多提出一个需要用户作答的问题。紧急风险提示、概念解释和非穷尽示例不算问题，但不得借此变相追问多个事项。

### 具体案件的第一步

模型判断进入具体案件办理后，第一项实质工具动作只能是当前工作区的公开档案操作：全新案件调用 `CaseArchive.create`；用户明确恢复当前工作区已保存案件时调用 `CaseArchive.read`。“继续梳理一件工资争议”这类延续工作目标的普通说法，不足以证明当前工作区已有案件，按新案建档。新会话恢复已保存案情、但尚未取得 `case_id` 时，使用 Runtime Adapter 的 `read-current` transport 读取当前工作区唯一案件；它仍返回公开 `read` 信封，不新增业务操作。若用户要求恢复但工作区没有案件或存在多个案件，transport 失败关闭，不得扫描目录、推测旧案或静默改建为新案；如用户同时提供了新案事实与目标，则按新案建档。档案创建/读取成功后，模型才选择一个最高信息价值的问题或分析动作；用户补充事实后先调用 `CaseArchive.commit`，再继续下一轮。不得先列目录、把平台 memory/`USER.md` 当作案件事实来源、读取脚本源码或执行临时 Python 来猜测契约。

### 恢复-only 回合的硬停

当用户明确说“继续”“刚才中断了”或“只读取当前工作区的案情档案”，本轮只做恢复读取，不做实现验证或法律核验。Skill 成功加载后，下一次工具调用必须是承载公开 `read-current` 或已知 `case_id` 的 `read` 的单条受管 transport；WorkBuddy 固定的 `cd <installed_skill_root> && python ...` 前缀属于该档案调用。除该 transport 外，不得调用目录列举、`Read` 其他参考、Glob、源码、版本检查或其他 Bash/PowerShell 探查，也不得串联第二项操作；WorkBuddy 宿主自动读写 memory/`USER.md` 属于运行时机制，不由 Skill 控制，验收只在其中事实进入当前答复、档案、计算、法律核验选择或文书时判定污染。读取成功后立即结束工具调用，回复只保留当前事实、必要风险和一个待回答问题；不得写恢复/验证报告，也不得披露 revision、版本变更、读取路径、契约、门禁、网络状态或“首个动作合规”等内部过程。

本参考只在 Skill 成功加载后的受管档案调用之后，或新建、提交、纠正等非 recovery-only 分支按需读取；恢复-only 不把本参考读取作为前置动作。

安装副本中的命令执行目录和案件工作区必须明确分开。`scripts.case_archive.cli` 是由
`scripts.runtime_context.build_case_archive_invocation()` 生成的 Runtime Adapter transport，
不是模型可自由拼接的另一套业务 API；验收只放行与该上下文精确一致的解释器参数、Skill 根
cwd、显式 `--root` 当前工作区和 `create/read/read-current/commit` transport。`read-current`
只负责在唯一案件的隔离工作区中派生 `case_id` 并调用公开 `read`。命令不得带管道、重定向、额外
`PYTHONPATH` 或状态掩盖。命令从已安装 Skill 根目录执行，
案情文件只由显式 `--root <workspace_root>` 指向当前隔离工作区；不要从工作区直接导入
`scripts`，不要注入源码 `PYTHONPATH`，也不要把 Skill 根目录当作案件根目录。下面是从任意
隔离工作区可复现的最小公开调用前缀（`<installed_skill_root>` 和 `<workspace_root>` 由运行时
上下文提供）：

```text
cd "<installed_skill_root>" && python -B -X utf8 -m scripts.case_archive.cli --root "<workspace_root>" create --jurisdiction "待用户确认" --initial-goal "梳理劳动争议"
cd "<installed_skill_root>" && python -B -X utf8 -m scripts.case_archive.cli --root "<workspace_root>" read-current
cd "<installed_skill_root>" && python -B -X utf8 -m scripts.case_archive.cli --root "<workspace_root>" read <case_id>
Write 一次性 JSON 到 "<workspace_root>/.arbibuddy/runtime-input/<nonce>.json"，随后：
cd "<installed_skill_root>" && python -B -X utf8 -m scripts.case_archive.cli --root "<workspace_root>" commit --input "<workspace_root>/.arbibuddy/runtime-input/<nonce>.json"
```

成功的 `create` 会准备当前工作区的 `.arbibuddy/runtime-input/` 目录，供宿主的单次
`Write` 写入复杂 commit 请求；`commit --input` 只接受该目录的直接 `.json` 子文件。
transport 在解析业务请求前读取并消费该文件，路径越界、嵌套路径、符号链接、超限文件
和重复使用均 fail closed；失败也不得留下第二份案件事实。模型不得用 `New-Item`、管道、
重定向、临时输出文件或第二条清理命令准备或回收输入。WorkBuddy 推荐使用上述 Bash 单
transport；PowerShell 不属于公共业务 transport 的推荐入口，禁止用分号、重定向或串联操作
包裹档案命令。短 `create`、`read` 和 `read-current` 仍直接使用上面的单条 transport。

Codex、Claude Code 和 WorkBuddy 均使用以上同一档案 transport。每次命令工具调用只执行一条 transport；固定的 `cd ... && python ...` 是同一 transport 的启动前缀，执行目录必须是运行上下文给出的已安装 Skill 根目录，`--root` 必须是当前工作区。不得把命令从工作区根目录启动，也不得在 transport 前后串联 `describe`、第二次档案操作、`echo`、清理或检查命令；不加管道、重定向、分号或内联 Python。组合命令可能令外层退出码掩盖档案操作失败。标准输出本身就是完整公开信封，模型直接读取其中的 `contract_version`、`operation`、`ok`、`result`、`errors` 和 `warnings`；没有对应的 JSON 信封、`ok` 为 `false` 或命令非零退出时，停止当前档案动作，不声称已经读取、创建或保存。需要较短内容时在模型上下文中选择字段，不修改 transport。

`<case_id>`、`<commit_json>` 只表示一次调用的内部输入，不展示给用户。若使用 Python 公共
接缝而非 CLI，必须在命令执行目录为已安装 Skill 根目录，并把同一个显式工作区传给
`CaseArchive`；不得依赖当前检出目录。

如果运行环境直接使用 Python 公共接缝，调用仍只能是下面三个公开操作；不得读取
`scripts/case_archive/*.py` 猜测参数、直接导入 `scripts.case_archive.service`、运行
`python -c`/临时脚本、手工编辑 canonical 档案或清理案件目录。`CaseArchive` 的
业务实现不因安装方式复制一份：

```python
from scripts.case_archive import CaseArchive

archive = CaseArchive("<workspace_root>")
created = archive.create({"jurisdiction": "待用户确认", "initial_goal": "梳理劳动争议"})
case_id = created["result"]["case_id"]
read_result = archive.read(case_id)
archive.commit({
    "case_id": case_id,
    "expected_revision": int(read_result["result"]["revision"]),
    "change_summary": "记录本轮用户补充事实",
    "changes": [{
        "operation": "append",
        "record_type": "fact",
        "content_markdown": "事实状态：用户陈述\n用户刚刚明确提供的事实。",
    }],
})
```

`CaseArchive` 的事实写入只接受当前用户明确提供或确认的内容。`case_id`、revision、路径和错误字段是模型内部控制信息，回复用户时转成普通语言，不展示原值。

## 单一事实源与目录

工具从运行环境取得当前工作区根目录，并自行建立：

```text
<workspace>/.arbibuddy/cases/<case_id>/案情档案.md
```

案件语义只写入这一个 Markdown 文件。单次工具请求可以是结构化数据，但不得把逐轮问题、待答计划、工具信封或可重新派生的分析副本写入案件目录。锁和原子替换所需的短生命周期事务文件不承载案情语义。

`canonical_location` 和 Markdown 正文是内部恢复与绑定数据，不是用户交付物。不得调用 `present_files`、附件展示或类似能力展示《案情档案.md》；用户只看到自然语言事实摘要。

## 案件来源门禁

模型只能使用两类案件事实：本轮用户明确提供的事实，以及当前工作区中当前
`case_id` 对应的本文件。平台 memory、全局 `USER.md`、历史任务摘要、侧边栏任务、
父级或兄弟工作区、其他案件档案和模型上下文中未由本轮用户确认的内容，均视为不可信
案件来源。宿主对 memory/`USER.md` 的自动读写属于运行时机制，不由 Skill 控制，访问本身只作
诊断观察；模型不得把其中未经确认的事实复述、引用、据此提问、写入当前档案、用于计算、
法律核验选择或文书。

适用地区未由当前用户确认时，档案的事实、风险、分析、下一步和提交 transport 只使用“待用户确认”或“地方规则待核验”等中性表达，不得把任何未由用户确认的具体地名作为适用地区或地方规则写入（本参考或其他参考中的地名仅供知识参考）；用户明确确认地区后，才可记录该地区并进入相应法律核验。

新会话默认创建新案件，不扫描历史目录寻找可续接案件。只有用户明确要求继续当前工作区
内的目标案件，且模型已经取得该 `case_id`，才可以读取对应档案。正常路径只通过本公开
档案工具以及公开金额、文书工具工作；工具失败时重新读取当前档案并按错误路径修正，
不得删除或重建 `.arbibuddy`、手工编辑 canonical 输出、执行旧 CLI、运行临时脚本或读取
源码猜测契约。平台 memory 的开关、宿主注入、后台自动写入和模型读写只属于客户端诊断
观察，不是本工具的事实源，也不能被模型当作案件依据；只有其中的历史事实实际影响答复、
档案、计算、法律核验选择或文书时，才按事实污染处理。

如果模型上下文出现未由本轮用户提供的旧案内容，不展示旧内容，只能中性说明“这是新会话，
我只依据本轮提供的信息”。如果旧内容已经影响答复、问题、档案、计算、法律核验选择或文书，停止当前
动作，等待用户重新明确提供事实；不要通过补写、删除或重建文件掩盖影响。

## 单问与记录分类

每轮选择一个能改变下一步判断的信息点，等待回答后再问下一个。回复前核对所有要求用户作答的句子，只保留本轮选定的信息点；“顺带确认”“有错请改”及“有的话请一并……”等条件补问也计入待答点。同一事件的是否发生、形式和时间分轮采集。工资拖欠时，可只问“涉及哪些工资月份？”；支付日期、已付金额另轮采集。地区未确认时，可只问“劳动合同实际履行地是哪里？”；单位注册地、摘要确认另轮核对。“月份、到账日期和金额分别是什么？”或“工作城市区县是否与单位注册地相同？”均包含多个信息点，即使只有一个问号也须拆开。本轮询问地区时，事实摘要只作已记录情况说明，下一轮再请求摘要确认。已知事实直接复用，不借“最后一个问题”的示例追加追问。

新增或更正案情事实、主张、材料、分析、金额、依据、风险与确认分别进入对应记录类型。重复已知信息无需再次提交；仅保留候选稿、安排核对顺序或重申已明确的不代签、不代发、不上传边界，如确有保存价值，使用 `next_step`。实际改变主张范围、高风险授权、法律判断或文书字段的内容仍进入业务记录，不能放入 `next_step` 来保留旧稿。记录分类由模型判断，提交器不解释业务含义。

## 事实分层

档案、回复、确认摘要与候选稿保持同一来源强度，使用普通语言标签：

- `事实状态：用户陈述`：用户明确表达、尚未由材料初步支持的事实；
- `事实状态：证据支持事实`：模型实际查看了对应材料内容，能指出材料位置与它支持的具体事实；不等于仲裁机构或法院最终认定；
- `事实状态：分析假设`：为继续分析提出、尚待验证的推断，不得作为已确认事实写入正式文书。

提交器保存这些内容并保持其标识，不替模型判断真伪或证据充分性。

“我有劳动合同、流水、聊天和考勤”只证明用户陈述持有材料，记录为材料线索。未取得并查看内容时，工资标准写“用户陈述合同约定”；回答“材料能支持什么”时，说明拟证明目的与条件，例如“若合同内容与你陈述一致，可用于证明约定工资，具体内容和证明力待核”，不能写“劳动合同原文记载”“已有材料支持”或“能初步支撑（仍需核对原件）”。用户确认摘要也不能提升这一来源等级。可以按陈述继续条件分析并生成注明待核的候选稿，无需为候选交付强制收齐所有材料。查看后分别说明每项材料支持什么、有哪些限度，不把一份材料的支持扩大到其他事实。

待核项也保留来源：用户明确说某项说不准时，记录“用户表示该项暂不能确认”；暂无法提供信息或材料，不等于明确确认该事实或材料不存在。只有用户明确否认时才记录为否定事实，歧义答复保留原意并标待核。尚未询问或用户未提供的项目写“尚未采集，模型列为待核”，放入 `analysis` 或 `next_step` 的缺口说明。连续几个未知答复可以支持停止追问，不能代表用户对剩余项目也逐项作了未知陈述。回复、摘要与候选稿沿用这一差别；后续确认不追溯改变原话来源。

### 录音原件与辅助材料

录音材料区分完整原文件与辅助提交件。完整保留未经改动的原始录音、原始载体和上下文，确保可随时核对；可以另附标注清晰的录音节录或文字整理稿作为辅助举证材料，注明对应原文件、时间位置及必要上下文，不以辅助件替代原文件，也不把节录冒充完整录音。

整理须忠实呈现原意，无法辨认处如实标注；实质删改关键内容、拼接造成误导或伪造不能因标为“节录”而被允许。提交形式及证明力按具体程序、受理机构要求和质证情况判断；未查看或核对的用户所述材料仍保留为待核线索。

## 公共结果信封

每次操作返回：

```json
{
  "contract_version": "case-archive-v1",
  "operation": "create|read|commit",
  "ok": true,
  "result": {},
  "errors": [],
  "warnings": []
}
```

成功结果固定提供不可编辑的 `user_visible_summary`（创建、读取或更新案情档案的普通语言摘要）；`generated_record_ids`、`applied_changes[*].record_id`、路径和 revision 只用于后续工具绑定。失败时 `ok` 为 `false`，`errors` 一次聚合可发现问题；错误至少包含 `code`、`path`、`message`、`expected`、`received` 和 `recoverable`。模型将这些控制信息转成普通语言，不向用户展示 JSON、路径、内部 ID、revision、CLI 或诊断细节。

## create

输入是可选对象，只允许：

```json
{
  "case_label": "工资争议",
  "jurisdiction": "待用户确认",
  "initial_goal": "梳理事实并准备下一步"
}
```

工具随机生成不可预测的 `case_id`，创建最小 Markdown 档案并返回：

```json
{
  "case_id": "case-…",
  "canonical_location": "…/案情档案.md",
  "revision": 0,
  "markdown": "# 案情档案 …"
}
```

`create` 不接受主体姓名、身份证号、电话、地址或单位全称字段。

## read

输入只有工具生成的 `case_id`。成功结果包含完整权威 Markdown、当前 revision、最后更新时间和只读 canonical 位置：

```json
{
  "case_id": "case-…",
  "canonical_location": "…/案情档案.md",
  "revision": 1,
  "updated_at": "2026-09-07T08:00:00+00:00",
  "markdown": "# 案情档案 …"
}
```

`case_not_found`、`permission_denied`、`archive_corrupt` 和 `io_failure` 保持不同错误码。结构损坏或权限问题不会被当成空档案继续写入。

## commit

输入：

```json
{
  "case_id": "case-…",
  "expected_revision": 0,
  "change_summary": "记录用户刚补充的欠薪事实",
  "changes": [
    {
      "operation": "append",
      "record_type": "fact",
      "content_markdown": "**事实状态：用户陈述**\n用户表示 2026 年 2 月工资尚未支付。"
    }
  ]
}
```

`operation` 只能是 `append`、`replace`、`remove`；`record_type` 只能是 `fact`、`claim`、`evidence`、`analysis`、`calculation`、`authority`、`risk`、`confirmation`、`next_step`。

事实采集或纠正阶段的 commit 只保存本轮用户事实、证据线索、由这些事实直接产生的即时风险和一个下一步；通用风险清单不自动进入当前案件。法律时效、请求性质或救济判断应在读取对应领域参考和全国基线后再写入分析或风险记录。仅缺少具体欠薪月份时，把它记录为请求期间、金额和证据对应的待核实项；在职欠薪的时效边界按[工资、奖金与工资差额](model-led/wage-analysis.md)处理。用户没有提供企业注销、经营异常或财产转移迹象时，这些主题不进入事实、风险或下一步。

- `append` 不提供 `record_id`，工具按类型生成稳定编号；
- `replace` 必须提供现有 `record_id`、新 `content_markdown` 和 `reason`；
- `remove` 必须提供现有 `record_id` 和 `reason`，不得提供 `content_markdown`；
- 删除仍被其他记录引用的记录会返回 `dangling_reference`；先移除或改写引用后才能删除；
- `content_markdown` 不能破坏档案的顶层章节锚点；
- 一个 commit 是一个 revision：整批成功或整批不写入；
- `expected_revision` 不匹配时返回 `revision_conflict` 和 `current_revision`，不覆盖并发更新。恢复方法是重新 `read`，基于新 revision 重新生成变更；
- 同一请求安全重放返回原提交结果，不重复记录、不再次升版；
- 已删除记录的编号不会被后续 append 复用；
- 提交成功后临时清理失败只返回 warning，不能把已经写入的事实报告成失败。

成功的 `commit.result` 固定包含：

```json
{
  "case_id": "case-…",
  "canonical_location": "…/案情档案.md",
  "previous_revision": 0,
  "revision": 1,
  "applied_changes": [
    {"operation": "append", "record_type": "fact", "record_id": "F-001"}
  ],
  "generated_record_ids": ["F-001"]
}
```

稳定错误码至少包括：`case_not_found`、`revision_conflict`、`invalid_change`、`record_not_found`、`duplicate_record`、`dangling_reference`、`archive_corrupt`、`permission_denied`、`io_failure`、`installed_skill_modified`。安装完整性失败时 `recoverable=false`，不得创建/写入案件；原生 WorkBuddy 上传的便携标记问题应重新上传已验签 ZIP 并重启，受管安装回执/外部信任锚问题应重新执行受管安装或更新，不得要求原生上传生成受管回执。工具不通过错误反复引导模型猜字段；一次错误返回所有能定位的问题。

## 模型主导 tracer

自然语言主路径的最小闭环是：

1. 用户描述具体劳动争议；模型判断进入案件办理，调用 `create`，随后用普通语言说明已建立可恢复档案；
2. 模型读取档案和当前目标，复用已有信息，只提出一个最高信息价值的问题并结束本轮；
3. 用户回答后，模型把用户陈述、证据支持事实、分析假设和材料线索分别组织为 Markdown 变更，调用一次 `commit`；
4. 用户自然语言纠正旧事实时，模型读取最新 revision，使用 `replace`，保留当前事实并在更新记录中留下简洁修正原因；
5. 客户端中断或上下文压缩后，模型先 `read` 同一 `case_id`；新会话不知道编号时以 `read-current` transport 完成同一个公开读取，再从档案继续，不重复询问已经记录的信息；
6. 用户只看到自然语言问题、风险说明、当前事实和下一步，不看到工具协议、内部编号、revision、JSON、路径或推理过程。

恢复后的回复只陈述“已从当前工作区档案继续”及案情摘要。模型不得输出“未读取平台 memory”“这是第一个实质动作”等无法由当前业务结果证明的合规自证。

恢复、纠正或实现验证期间，版本比较、读取路径、契约/门禁、并发保护、错误码、网络状态和测试动作均先折叠为业务结果；不要在当前案件中提交模拟错误、地方规则或测试记录。若只是验证实现，停留在测试接缝，不把验证过程写入用户回复或案情档案。

该工具不实现步骤 2 的问题选择，也不判断步骤 3 的法律意义；这些是模型依据核心 Skill 和领域参考完成的行为。
