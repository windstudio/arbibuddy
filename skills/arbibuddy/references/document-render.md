# `document.render` 模型主导文书契约

这是模型调用的统一 Word 文书工具。模型负责根据已经确认的案情档案、计算结果和用户目标起草完整正文；工具只负责读取指定档案 revision、核对显式锁定要素、排版、占位符与内部泄漏检查、DOCX/OOXML 检查、原子交付和独立核验清单。工具不补写、概括、改写法律正文，不选择法律口径，也不自动提交、发送或签署。

本公共接缝支持八种 `document_type`：

- `employment_obligation_demand_letter`（劳动用工义务催告函）；
- `forced_termination_notice`（被迫解除劳动合同通知书）；
- `arbitration_application`（劳动人事争议仲裁申请书）；
- `arbitration_defense`（劳动人事争议仲裁答辩书）；
- `evidence_catalog`（证据目录）；
- `company_deregistration_restriction_request_shanghai`（限制公司注销申请书，上海专项）；
- `property_preservation_application`（财产保全申请书）；
- `enforcement_application`（强制执行申请书）。

八类正式文书全部通过同一 `document.render` 接缝；不创建第二套文书工具，也不由 renderer 代替法院、仲裁机构或其他有权机关作程序决定。

## 模型唯一入口与客户端 transport

模型或 WorkBuddy 的唯一可调用 Python 公共入口是
`scripts.documents.public.render(request, workspace_root)`，对应
`document.render-v1`；渲染实现是该接缝的内部实现，不另行暴露生成类 API。需要文书时先调用 `document.delivery-set-v1` 的 `scripts.documents.public.create_delivery_set` 建立完整集合；案件档案必须使用 `CaseArchive` 的
`case-archive-v1` 契约，并保持 10 个顶层章节：案件元数据、事实、主张与权益、证据材料、分析与假设、计算结果、法律核验、风险与确认、下一步、更新记录。

Codex、Claude Code、WorkBuddy 均从当前安装 Skill 根目录通过同一公共 API 的窄 transport 调用，不使用 `python -c` 或源码反射：

```text
python -B -X utf8 -m scripts.documents.runtime_cli --workspace "<workspace_root>" describe
python -B -X utf8 -m scripts.documents.runtime_cli --workspace "<workspace_root>" delivery-set --input "<workspace_root>/.arbibuddy/runtime-input/<nonce>.json"
python -B -X utf8 -m scripts.documents.runtime_cli --workspace "<workspace_root>" render --input "<workspace_root>/.arbibuddy/runtime-input/<nonce>.json"
python -B -X utf8 -m scripts.documents.runtime_cli --workspace "<workspace_root>" view --json '{"case_id":"<case_id>","delivery_set_id":"<delivery_set_id>"}'
```

按 `delivery-set → 各成员 render → view` 完成本轮工具调用；全部 render 成功后直接调用上述 `view`，再处理宿主附件展示。`view` 返回受管完整列表后，宿主存在 `present_files` 时原样传给一次该工具；宿主没有附件工具时，说明文件已生成及受管交付状态，逐个提供列表内文件的完整绝对路径或可打开的文件链接，保留完整工作区前缀和文件名，不用省略号、目录树片段或仅文件名代替。明确宿主附件展示尚未验证；不能把没有展示通道说成没有生成文件。文书生成、受管交付与宿主展示分别按实际回执报告。

`--input` 只接受 `.arbibuddy/runtime-input/` 的直接 JSON 子文件；transport 在解析业务请求前先消费并删除该文件，删除失败则不执行文书操作。它不改变 `document.delivery-set-v1` 或 `document.render-v1` 的业务信封。短请求也可用 `--json`。`view` 是只读受管出口，只有集合完整且 `delivery_set_id` 一致才返回展示列表；失败或集合不完整时文件列表为空。运行结束后不得留下 `runtime-input` 文件，也不得建立 `render-requests`、工作区根 JSON 或其他事实副本。字段和示例只读本参考与 `describe` 结果，不读取、反射或搜索 `scripts/*.py`。

若 render 已成功而 view 返回 `next_action=retry_managed_view_with_access`，保留现有 DOCX、清单和机器交付清单；先在同一工作区按已获授权的文件访问权限重试一次原 view 请求。读取仍失败时如实报告候选文件已生成但受管交付尚未验证，停止展示；无需重新 render 或改动档案 revision。

完整文书正文和核验清单只放在单次 `render` 输入中；案情档案只保存必要的持久化分析摘要、事实变化和下一步，不重复存储整篇正文或清单。先完成会改变档案 revision 的提交，再创建绑定最终 revision 的 delivery set，避免中途改档案后重复建 set。每个 render 请求使用单次 `runtime-input` 文件；transport 会消费并删除它。

WorkBuddy 推荐以已安装 Skill 根目录为 cwd，通过 Bash 执行每一条精确 transport；PowerShell
不是公共业务 transport 的推荐入口，不得用分号、管道、重定向、输出文件或串联清理命令包裹
`delivery-set`、`render` 或 `view`。若随包 `runtime-manifest.json` 的 ABI 与当前解释器不一致，
`runtime_cli` 会在加载文书业务模块前，只从当前用户 `.workbuddy\\binaries\\python\\envs\\` 下按
`arbibuddy`、`default` 顺序探测受管 Python，使用 `-S` 验证实际 ABI/platform 后最多自举一次，
并以原始参数和退出码重新执行；不存在、路径越界、ABI 不匹配或循环自举时保持稳定的
`document_runtime_unavailable`，不联网、不 pip、不建 venv，也不使用宿主 site-packages。

`document.delivery-set-v1.requested_templates` 可以直接使用下文列出的 `document_type` 值；仲裁申请书需要配套证据目录时也可使用 `labor-arbitration-application`，由工具规范化为两份文书。

### 起草前确认

起草前先按[权益扫描、最终补充与统一确认](rights-scan-and-unified-confirmation.md)核对当前已完成的对话步骤，只补尚未完成的一步并等待答复。该路由适用于普通 candidate，包括被迫解除通知书；“理解风险、先要草稿、不发送”是草稿授权，不替代当前完整摘要确认。已有步骤和未变化的确认直接复用，候选稿无需用户确认实施解除或外发。仲裁申请书的候选稿同样要在权益范围选择、最终开放式补充和当前完整摘要的自然语言确认之后准备。

`delivery-set` 对仲裁申请书的机械检查只核对当前案件档案中是否存在绑定档案修订版本的确认记录，不要求确认记录使用固定列表或五项字段标签；唯一机器可读字段是独立一行 `绑定档案修订版本：<提交后的纯数字版本号>`，写法见上述统一确认参考。其他候选能通过 set 不表示对话确认已完成。确认是否真实、摘要是否完整仍由模型依据用户原话判断；未完成时回到该参考的当前步骤，不补写或猜测确认记录，也不改用单份 render 绕过受管集合。若返回 `confirmation_revision_unreadable`，先核对原确认记录的绑定字段，再向用户如实说明交付受阻，不用重复调用掩盖记录格式错误。用户要求申请书及配套证据目录时，集合创建失败后不得改建只有证据目录的集合来声称本轮已交付；应如实说明整包未完成。候选稿允许缺项占位，和上述对话顺序是两回事。

普通 `candidate` 不因用户已做事实、范围或口径的统一确认就自动填写
`confirmation_refs`，也不得为了让 renderer 通过而新建、改写或猜测确认记录。除下文明确
要求确认的高风险 `external_final` 外，该字段保持空数组；若确需引用，确认记录必须按高风险
确认参考中的公开字段一次写对，不能读取 canonical Markdown 或用内联脚本探测格式。

普通候选的 delivery-set 请求和每一份 render 请求都明确写 `"confirmation_refs": []`。两者必须一致；若错误地把普通统一确认写入 set 而导致成员不匹配，按当前档案重新建立空引用的完整 set，再为其全部成员 render。修正请求即可，沿用真实的摘要确认记录，不增写确认或让用户重新确认已未变化的事实。

## 候选稿复用与更新

用户在交付后要求“保留候选稿、说明还缺什么”时，先判断是否有实际新增案情；重复说明已有边界无需提交，确需保存的沟通安排按[记录分类](model-led-case-archive.md#单问与记录分类)写入 `next_step`。无新增案情或只保存上述安排后，都调用原 set 的只读 `view` 核对候选状态，再说明定稿缺口。该回合只答状态与下一步，不重复展示附件、索取已完成的确认或重新 render；无需为调用 view 而新增记录。

新生成 candidate 的控制对象和机器交付清单保存档案业务章节及被引用下一步记录的摘要，不复制案情正文。事实、主张、证据、分析、计算、法律核验、风险或确认的任何变化均使旧候选失效；被正文绑定或业务记录引用的 `N-*` 变化也会失效。只有未被引用的下一步备注与更新记账变化可通过复用核对。模型不能自行声明“无关”、手工改摘要或把业务变化藏入下一步记录。

view 成功时可保留原候选，内部回执另列 `current_archive_revision` 和 `archive_dependencies_verified`；`archive_revision` 永远是原始生成版本，DOCX、清单和机器交付清单不改写。向用户说明“候选稿仍可用于核对，正式发送条件仍待补齐”，无需披露内部版本与摘要，也不因无关备注重新索取确认或声称已更新文书。

复用失败时先读取当前档案，完成必要补充或确认，然后重建完整 set、重新 render 全部成员并统一展示。旧版本未带依赖摘要的产物仍严格绑定原 revision；升级后若需跨版本复用，先按当前档案重新生成一次。`external_final` 始终严格绑定当前 revision，不适用上述复用规则。


## 请求

操作名为 `document.render`，契约版本为 `document.render-v1`，版式模板版本为 `1.0.0`。请求必须一次性提供：

| 字段 | 类型与要求 |
| --- | --- |
| `case_id` | 档案工具生成的案件标识；工具自行定位 `.arbibuddy/cases/<case_id>/案情档案.md` |
| `archive_revision` | 非负整数；正文和锁定要素所依据的档案版本 |
| `delivery_set_id` | 可选；若当前案件已有 `document.delivery-set-v1`，必须绑定同一集合；没有集合的兼容单文书调用仍只返回非授权 artifact 诊断 |
| `document_type` | 八类已开放文书之一：`employment_obligation_demand_letter`、`forced_termination_notice`、`arbitration_application`、`arbitration_defense`、`evidence_catalog`、`company_deregistration_restriction_request_shanghai`、`property_preservation_application` 或 `enforcement_application` |
| `template_version` | 当前值 `1.0.0` |
| `mode` | `candidate` 或 `external_final`；它是文件成熟度，不是对话状态 |
| `title` | 必须与文书类型的注册标题完全一致 |
| `sections` | 完整、有序、非空的正文区块数组；每项必须有 `section_id`、`heading`、`full_text` |
| `locked_bindings` | 非空数组；每项至少有 `kind`、`archive_record_ref`、`rendered_value`；金额、日期以及 `external_final` 签名另须提供 `occurrences` 定位正文 |
| `placeholders` | 可选的占位符对象数组；每项有 `placeholder_id` 和明确的占位文本 `text`，只能用于 candidate |
| `confirmation_refs` | 可选的档案确认记录标识数组；被迫解除通知书 `external_final` 必须提供当前完整确认，其余文书若提供也必须仍存在 |
| `authority_refs` | 可选的档案现行依据核验记录标识数组；被迫解除通知书 `external_final` 必须提供当前适用核验，其余文书若提供也必须仍存在 |
| `additional_checklist_items` | 可选的普通语言提交前核验事项数组，只进入独立清单 |

`locked_bindings` 中每个 `(kind, archive_record_ref, rendered_value)` 组合只能出现一次。同一事实记录可以支持多个不同的锁定值（例如一条就业事实中分别记录入职日和离职日）；这些值分别绑定到同一 `archive_record_ref`。同一值在正文多处出现时，只保留一个绑定对象，并在其 `occurrences` 数组中列出每个位置。调用前按三元组检查唯一性，可避免可恢复的 `binding_mismatch`。证据引用只绑定档案记录，不证明模型已查看材料原件或内容。

每个 `section` 的 `full_text` 是模型拥有的完整正文。工具只把它按模板标题和段落放入 DOCX，不从文本中猜测请求、金额、主体、日期或法律结论，也不事后正则修补正文。

写正文前，模型须逐项对照用户本轮选定的请求、当前档案事实和待核项：仲裁申请书不增加未选请求，劳动争议仲裁依法不收费，不能加“仲裁费用由被申请人承担”；被迫解除候选稿也不自行加入未选的年休假等具体待遇。仅说社保由公司处理不等于确认实际缴纳；工资总额构成待核时，三个月应发合计只能写成条件假设或待补金额，不能当作已确定事实。

付款状态与金额分别沿用档案中的已知表述，并在整篇正文中保持一致，包括事实、理由、请求依据和结尾总结。用户明确陈述未足额支付时，保留“未足额支付”，具体应发、已付与差额的未知数额另标待核；是否足额支付尚未确定时，各处都写“支付情况及差额待核”，请求额保留待补占位。用户要求主张某款项只确定处理范围，不补充付款事实。起草总结时按各项状态分别写，例如“请求核算并支付尚欠工资、奖金及加班费，其中加班费支付情况和差额待核”，不把所有项目合并成已确认未付。

材料仅由用户声称持有、模型尚未查看时，正文可写“劳动合同、流水等拟作为证明材料，内容和证明力待核”，不得写“上述事实有这些材料佐证／予以印证／为证”；证据目录的拟证明目的也不代表材料内容已核实。只有实际查看具体支持内容后，才按对应事实和材料限定证明力。

证据目录涉及录音节录或文字整理稿时，按[录音原件与辅助材料](model-led-case-archive.md#录音原件与辅助材料)区分完整原文件与辅助提交件，说明可追溯来源和证明限度。

欠薪仲裁申请书正文保留用户已陈述的月薪数值，说明来自用户陈述、合同原文及工资构成待核；聊天、档案或核验清单不替代正文，不能仅写“以合同约定为准”。该数值不是已核实的工资基数或欠薪差额，不据此推算请求额。欠薪解除候选稿按[该类文书的已知值核对](model-led/demand-and-forced-termination-documents.md#欠薪解除候选稿的已知值)起草。

写完申请书、证据目录或解除通知书，调用 `render` 前，按档案逐句核对事实来源、付款状态、证明力、行为人及其已知身份、行为对象、时间与渠道、所涉请求和证据证明目的；正文和目录使用同一事实范围。只知道“领导”安排加班，就不能写“部门负责人”安排；只确认催要工资，就不能用“催要上述款项”涵盖奖金与加班费。未核实的身份或催要范围保留原称并标为待核，不为填满候选稿而推断。

推荐的章节顺序为：

- `employment_obligation_demand_letter`：`收件人`、`劳动关系说明`、`具体义务`、`履行期限`、`沟通与保留权利`；
- `forced_termination_notice`：`收件人`、`解除意思表示`、`解除理由`、`结算与手续`、`落款`；
- `arbitration_application`：`申请人`、`被申请人`、`仲裁请求`、`事实与理由`、`落款`；
- `arbitration_defense`：`答辩人`、`被答辩人`、`答辩意见`、`落款`；
- `evidence_catalog`：`标题`（也可写 `证据目录`）、`五列表格`（也可写 `证据列表`）。`五列表格.full_text` 每项单独一行，按 `编号|证据名称|证据内容|证明目的` 四段输入；工具生成第五列“页码”供提交前整理，不要求模型填页码。表头行可以省略。
- `company_deregistration_restriction_request_shanghai`：`申请人`、`被申请人`、`仲裁受理信息`、`申请事项`、`事实与理由`、`附件`、`落款`；
- `property_preservation_application`：`申请人`、`被申请人`、`保全请求`、`事实与理由`、`财产线索`、`担保`、`落款`；
- `enforcement_application`：`申请执行人`、`被执行人`、`执行请求`、`事实与理由`、`财产线索`、`附件`、`落款`。

催告函只能表达劳动关系存续期间对具体用工义务的履行要求，不能写成生效文书履行催告、被迫解除通知或“逾期即解除”。其 external final 不要求高风险动作确认，但仍需主体、义务、日期、标题和落款等绑定；金额义务必须同时提供金额绑定。

被迫解除通知书的 candidate 可以在确认前交付供核对；external final 必须引用当前 revision 下完整的高风险实质确认，并提供当前适用的规则核验记录。确认记录至少要能定位动作类别、动作范围、风险、替代方案、用户明确选择、确认时间、绑定 revision 和来源引用；历史解除索赔不得被文书工具当成新的解除动作。金额、日期、主体、主张和证据仍由模型显式绑定，工具不从正文猜理由或补充事实。

三类程序文书的正文、程序适用判断和风险解释均由模型提供。限制公司注销申请书只服务上海专项实务材料，必须把注销风险核实/协调材料与简易注销异议、财产保全和和解协议审查分开；不得承诺当然阻止注销。财产保全申请书必须说明诉前或仲裁/诉讼中的程序阶段、具体财产线索、担保方案以及错误保全和赔偿风险，不得改写成强制执行申请。强制执行申请书必须说明已经生效且可执行的执行依据、履行期限和履行情况、申请执行时效、管辖与财产线索；尚未生效或不能执行的材料不能被表述为执行依据，追加责任、执行异议和破产债权申报等相邻流程应单独处理。

上述三类文书属于高风险程序文书：candidate 可以在资料缺口、确认过期或动态核验待完成时交付供人工复核，并清楚保留占位符或可修复 warning；external final 必须同时绑定当前档案 revision、覆盖本次主体/主张/证据/金额/日期等锁定要素的实质确认和必要的当前 authority。工具只消费档案中的 authority 记录，不自行联网、补写办事口径或自动执行外部动作。

### 锁定要素与档案绑定

`locked_bindings.kind` 可取 `party`、`claim`、`amount`、`fact_amount`、`date`、`document_heading`、`signature`、`evidence` 或 `attachment`。`archive_record_ref` 只能引用当前档案中已有记录；计算金额用 `amount` 引用 `CAL-*`，主张引用 `CL-*`，证据引用 `E-*`。`rendered_value` 是要显示给用户的完整值，工具只核对它与引用记录一致，不把档案记录标识写进正式文书。

候选仲裁申请书的“事实与理由”、催告函的“劳动关系说明”、解除通知书的“解除理由”可用 `fact_amount` 引用含同一数值的 `F-*`，保留用户陈述的合同工资标准等事实金额，并如实标明来源及未核要素；例如 `{ "kind": "fact_amount", "archive_record_ref": "F-001", "rendered_value": "15000元", "occurrences": [{ "section_id": "grounds", "text": "据本人陈述，约定月薪15000元" }] }`。这不是计算结果，只限 candidate 的上述事实区块；请求、结算、计算结果及 external_final 仍使用 `amount` / `CAL-*`。

金额与日期使用显式正文定位，不能靠全篇数字搜索判断其业务归属：

- `occurrences` 为非空对象数组，每项只有 `section_id` 和 `text`，均为非空单行字符串。`section_id` 引用本请求正文区块；`text` 从该区块原样复制，是在该区块恰好出现一次、包含完整金额、日期或签署角色与姓名的短片段，不填写字符偏移或内部编号。
- 一个金额在正文中多次出现时，逐处列出定位。每个正文货币金额只能归属一个金额绑定；候选事实金额按上述 `fact_amount` 处理，不同计算主张、基数、已付额和合计各自绑定对应计算记录，不要求全篇金额相等。金额定位必须覆盖完整数字，不能把 `6000.00元` 当成 `16000.00元` 的一部分。
- 每个 `CAL-*` 记录须有且仅有一个明确的最终结果标签，形式为 `确定性计算结果：6000.00元`（允许在标签前加“工资差额”等语义名称），并保留公式、输入及工具来源说明。若一次计算有多个分项/合计，分别提交结果记录；渲染器只核对该标签后的结果，不从基数、期间或中间步骤寻找相同数字。
- 日期允许 `2026年9月7日` 和 `2026-09-07` 两种等值形式，且必须是有效日历日期。`external_final` 正文各区块中的每个完整中文或 ISO 日期都必须通过 occurrence 单独定位，并由所引用的档案记录支持；同一日期在同一区块或不同区块重复出现时，每处都要单独绑定。不能把历史日期当作自动豁免项。落款日期仍须单独绑定。
- 除证据目录外，`external_final` 必须至少绑定一名签署人；`rendered_value` 必须包含完整角色与姓名，且该完整值必须在实际落款区块中出现，并由 occurrence 精确定位。仅出现相同姓名、但角色不同，不构成签名匹配。催告函将末区块中的签署信息作为落款。只有绑定对象、正文没有相应落款，不能定稿。生成器返回错误，由模型补正文，不替模型补写。

示例（片段，完整顶层示例由 `describe_contract()` 提供）：

```json
{
  "kind": "amount",
  "archive_record_ref": "CAL-001",
  "rendered_value": "6000.00元",
  "occurrences": [
    {"section_id": "requests", "text": "请求支付工资差额6000.00元。"}
  ]
}
```

`external_final` 签名绑定示例：

```json
{
  "kind": "signature",
  "archive_record_ref": "F-001",
  "rendered_value": "申请人：张三",
  "occurrences": [
    {"section_id": "closing", "text": "申请人：张三"}
  ]
}
```

各类 `external_final` 的最小锁定要素分别是：

- 劳动用工义务催告函：一个标题、两个主体、一个主张、一个日期和一个落款；若正文包含金额，还必须有金额绑定；
- 被迫解除劳动合同通知书：一个标题、两个主体、一个主张、一个证据、两个日期和一个落款；若正文包含金额，还必须有金额绑定；
- 仲裁申请书：一个标题、两个主体、一个主张、一个金额、一个日期和一个落款；
- 仲裁答辩书：一个标题、两个主体、一个主张、一个日期和一个落款；
- 证据目录：一个标题和至少一项证据；
- 限制公司注销申请书：一个标题、两个主体、一个主张、一个证据、一个日期和一个落款，并引用上海专项现行依据；
- 财产保全申请书：一个标题、两个主体、一个主张、一个金额、一个证据、一个日期和一个落款，并在正文解释担保、财产线索和错误保全风险；
- 强制执行申请书：一个标题、两个主体、一个主张、一个金额、一个证据、一个日期、一个附件和一个落款，并在正文解释有效执行依据、履行情况和管辖。

candidate 可以缺少尚未取得的隐私字段或其他锁定要素，但必须在正文中使用已声明的 `【待补：...】`、`{{...}}` 或 `[待补：...]` 占位符。`external_final` 不得有任何占位符，也不得仅通过把模式改成 `external_final` 来晋升 candidate。

分析或核验清单可以把事项描述为“待核实”，但 `【待核：...】` 不是有效的 DOCX 占位符。candidate 正文中的缺失字段必须声明并原样使用 `【待补：...】`、`{{...}}` 或 `[待补：...]`；分析状态用语不能替代占位标记。已知事实金额按上述候选事实绑定保留，未知金额才占位；计算出的工资基数、已付额、请求额和合计使用 `CAL-*` 并逐处定位，不得为了通过渲染创建伪计算绑定。

## 成功响应

成功响应固定包含 `contract_version`、`operation`、`ok`、`result`、`errors` 和 `warnings`。`result` 固定包含：

`delivery_state`（`candidate_ready` 或 `final_ready`）、`document_type`、`mode`、`archive_revision`、`delivery_set_id`、`canonical_docx`、`verification_checklist`、`machine_manifest`、`content_digest`、`artifact_files`、`presentation_files`、`presentation_authorized=false` 和 `validation_summary`。单份 render 的 `artifact_files`/`presentation_files` 只用于诊断，不授予平台展示；平台必须等待 delivery set 全部成员完成，再从 managed view 原样消费完整列表，不得重新扫描或重组。

对用户描述状态必须以本轮公开回执为准：只有每项 `render` 成功才能说相应文书已生成；只有完整 `view` 回执和 `present_files` 成功后才能说文件已展示或可下载。任一步失败、缺少回执或集合不完整时，明确说明相应文件尚未生成或交付；不得按计划、模型文本或预期结果宣称已完成。声称案情档案已更新，也必须有本轮成功的 `commit` 回执及对应 revision。

交付能力不可用时，简要说明未生成或未交付的文件及下一步；不得在聊天中输出完整文书正文、证据目录或核验清单，避免把对话内容混作交付。

`document.delivery-set-v1` 控制对象记录案件、原始 revision、模式、请求模板、规范化 `expected_document_types`、确认引用、随机 `delivery_set_id` 和稳定摘要；新 candidate 另有 `archive_dependencies` 摘要证明，不复制案情正文。`labor-arbitration-application` 规范化为 `arbitration_application + evidence_catalog`。成员缺失时返回 `delivery_set_incomplete` 与空列表；档案业务依赖变化或严格 revision 不匹配时返回 `stale_managed_delivery` 与空列表。

文件唯一发布到 `.arbibuddy/cases/<case_id>/output/<document_type>/`。同一案件、revision、类型、模板、模式和正文摘要的重放返回相同 canonical 路径和摘要，不生成第二个权威副本。重放与最终原子晋级均在档案提交共用的事务锁内重读 revision；渲染期间档案变化时返回 `archive_revision_conflict`，不得覆盖既有输出。核验清单与 DOCX 分离，必须明确写有“仅供核对，请勿外发”；它只提供普通语言的补全、字段、材料、表格和 Word/WPS 预览事项，不显示档案记录标识、路径、JSON、哈希或模型推理。

清单核验项按 `document_type` 和 `mode` 生成，每个必需类别是一条独立、非空并指向具体对象的 bullet；通用关键词堆叠不能替代。证据目录核对证据名称、内容、证明目的、编号和页码，不要求金额或落款签署项。其他文书分别核对主体与身份、事实或请求、关键日期与程序条件、证据引用或附件完整性；正文实际含金额时另列金额及计算依据。`external_final` 另列与该文书角色相符的落款角色与日期核验项，`candidate` 不要求最终签署项。所有模式均保留 Word/WPS 预览提醒和“不得与正式文书一并提交或发送”提示。

完整成功响应示例（路径为示意值；真实路径由工具派生）：

```json
{
  "contract_version": "document.render-v1",
  "operation": "document.render",
  "ok": true,
  "result": {
    "delivery_state": "final_ready",
    "document_type": "arbitration_application",
    "mode": "external_final",
    "archive_revision": 3,
    "canonical_docx": "<workspace>/.arbibuddy/cases/case-0123456789abcdef01234567/output/arbitration_application/《劳动人事争议仲裁申请书》.docx",
    "verification_checklist": "<workspace>/.arbibuddy/cases/case-0123456789abcdef01234567/output/arbitration_application/《劳动人事争议仲裁申请书》内部核验清单（仅供核对，请勿外发）.txt",
    "machine_manifest": "<workspace>/.arbibuddy/cases/case-0123456789abcdef01234567/output/arbitration_application/《劳动人事争议仲裁申请书》机器交付清单.json",
    "content_digest": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    "presentation_files": [
      {
        "delivery_label": "arbitration_application",
        "kind": "document",
        "order": 1,
        "path": "<workspace>/.arbibuddy/cases/case-0123456789abcdef01234567/output/arbitration_application/《劳动人事争议仲裁申请书》.docx",
        "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        "state": "final_ready"
      },
      {
        "delivery_label": "arbitration_application",
        "kind": "checklist",
        "order": 2,
        "path": "<workspace>/.arbibuddy/cases/case-0123456789abcdef01234567/output/arbitration_application/《劳动人事争议仲裁申请书》内部核验清单（仅供核对，请勿外发）.txt",
        "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        "state": "final_ready"
      }
    ],
    "validation_summary": {
      "status": "passed",
      "checks": [
        "ZIP包、内容类型、关系目标和XML部件完整",
        "A4纵向页面与显式页边距",
        "标题、正文与落款使用命名样式",
        "独立页脚使用PAGE / NUMPAGES及Times New Roman小五",
        "分页、孤行和孤立落款控制",
        "具体义务使用真实Word编号"
      ],
      "placeholder_state": "none",
      "locked_binding_count": 7
    }
  },
  "errors": [],
  "warnings": []
}
```

## 聚合错误与恢复

所有可发现错误尽量一次返回，每项错误必须包含 `code`、准确 `path`、`message`、`expected`、`received` 和 `recoverable`。核心错误至少包括：

`unsupported_document_type`、`template_version_mismatch`、`archive_revision_conflict`、`missing_section`、`binding_not_found`、`binding_mismatch`、`document_type_conflict`、`undeclared_placeholder`、`placeholder_in_final`、`confirmation_missing`、`confirmation_stale`、`authority_required`、`authority_stale`、`internal_content_leak`、`ooxml_invalid`、`publication_conflict`、`permission_denied` 和 `io_failure`。

完整聚合失败示例：

```json
{
  "contract_version": "document.render-v1",
  "operation": "document.render",
  "ok": false,
  "errors": [
    {
      "code": "archive_revision_conflict",
      "path": "archive_revision",
      "message": "请求所依据的档案修订版本已过期，请重新读取并重写文书",
      "expected": 4,
      "received": 3,
      "recoverable": true
    },
    {
      "code": "binding_mismatch",
      "path": "locked_bindings[4].rendered_value",
      "message": "金额渲染值与确定性计算记录不一致",
      "expected": "value present in referenced CAL record",
      "received": "7000.00元",
      "recoverable": true
    },
    {
      "code": "internal_content_leak",
      "path": "sections[3].full_text",
      "message": "正文包含内部路径、协议、记录标识、推理或哈希",
      "expected": "user-facing text only",
      "received": "record id or tool protocol marker",
      "recoverable": true
    }
  ],
  "warnings": []
}
```

遇到隐私缺失，模型在 candidate 中声明占位符并交付候选，补齐信息、重新读取档案、取得必要确认后重新调用；遇到绑定冲突或事实冲突，模型不得自行改值，必须回到档案和计算结果；遇到 `archive_revision_conflict`、`confirmation_stale` 或 external_final 的 `authority_stale`，必须先 `CaseArchive.read` 当前档案再重建请求。动态现行规则核验受阻、程序阶段待核实或高风险确认过期时，三类程序文书的 candidate 可以保留为候选并返回可恢复 warning，但不得把它标为 `final_ready`。任何错误都不发布半成品。

恢复示例：

```json
{
  "when": "archive_revision_conflict、confirmation_stale、authority_stale 或 binding_mismatch",
  "action": "重新 read 当前案情档案，按错误 path 修正文书请求，再以新的 archive_revision 重试",
  "guarantee": "失败不会创建或替换 canonical DOCX；成功重放返回同一 canonical 结果"
}
```

`installed_skill_modified` 表示安装树或其身份链校验失败；返回 `recoverable=false`，在预检阶段停止且不创建 staging 或 canonical 文件。若错误回显 `native_upload_marker_missing`、`native_upload_marker_stale` 或 `native_upload_vendor_runtime_*`，说明是 WorkBuddy 原生上传的便携标记或随包运行时问题，应重新上传已验签 ZIP 并完全重启客户端；原生上传不生成、也不需要 `arbibuddy.install.json`。若错误回显 `external_trust_anchor_*` 或 `install_receipt_*`，说明是本地受管安装的外部信任锚/回执问题，应重新执行受管安装或更新；不得把这两条恢复路径混用。`document_runtime_unavailable` 表示当前解释器与随包 WorkBuddy 文书运行时不匹配、vendor 摘要不一致或 DOCX 依赖不可导入；返回 `recoverable=false`，明确“未生成 DOCX”，只能重新安装兼容包或更换已支持环境后重试，不得运行期 pip、创建虚拟环境或临时脚本。

`ooxml_invalid`、`permission_denied` 和 `io_failure` 表示其他运行环境或文件事务边界，说明本次新的 Word 未成功交付。覆盖被拒绝时保留原 DOCX、独立核验清单和机器清单；先关闭 Word/WPS 及文件预览中占用的目标文件，再按同一公开生成请求重试。旧产物及事务锁由受管事务维护，不删除、移走或手工拼装旧产物，也不手工清除档案锁或交付锁来绕过拒绝。关闭占用后同一受管请求仍失败时，停止生成并回报限制，不用临时脚本试删旧稿、诊断进程或重建已消费的请求文件；不能因锁内进程号仍存在或删除成功就断言锁已过期、没有占用或旧稿无损。需要保留故障经验时只记录错误现象和受管恢复路径，清单的“请勿外发”等提醒原样保留；案件记忆的来源边界见[案情档案参考](model-led-case-archive.md)。

工具不引入 LibreOffice、Poppler 或视觉模型作为运行时依赖；模型不得调用宿主通用 documents 技能、`load_workspace_dependencies` 或 `render_docx.py`，也不得在 canonical `output` 目录创建 PDF、PNG、QA 子目录或其他旁路文件。提交或发送前，用户仍须在 Word/WPS 中预览字体、层级、间距、分页、表格、金额和落款。
