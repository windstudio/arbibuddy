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
python -B -X utf8 -m scripts.documents.runtime_cli --workspace "<workspace_root>" describe --operation delivery-set
python -B -X utf8 -m scripts.documents.runtime_cli --workspace "<workspace_root>" describe --operation render
python -B -X utf8 -m scripts.documents.runtime_cli --workspace "<workspace_root>" runtime-check
"<document_python>" -B -X utf8 -m scripts.documents.runtime_cli --workspace "<workspace_root>" delivery-set --input "<workspace_root>/.arbibuddy/runtime-input/<nonce>.json"
"<document_python>" -B -X utf8 -m scripts.documents.runtime_cli --workspace "<workspace_root>" render --input "<workspace_root>/.arbibuddy/runtime-input/<nonce>.json"
"<document_python>" -B -X utf8 -m scripts.documents.runtime_cli --workspace "<workspace_root>" view --json '{"case_id":"<case_id>","delivery_set_id":"<delivery_set_id>"}'
"<document_python>" -B -X utf8 -m scripts.documents.runtime_cli --workspace "<workspace_root>" edit-context --json '{"case_id":"<case_id>"}'
```

按 `delivery-set → 各成员 render → view` 完成本轮工具调用；全部 render 成功后直接调用上述 `view`，再处理宿主附件展示。`view` 返回受管完整列表后，宿主存在 `present_files` 时原样传给一次该工具；宿主没有附件工具时，说明文件已生成及受管交付状态，逐个提供列表内文件的完整绝对路径或可打开的文件链接，保留完整工作区前缀和文件名，不用省略号、目录树片段或仅文件名代替。明确宿主附件展示尚未验证；不能把没有展示通道说成没有生成文件。文书生成、受管交付与宿主展示分别按实际回执报告。

首次建立文书集合前执行一次 `runtime-check`。这是 `document.runtime-check-v1` 只读准备操作：先核对安装身份，再实测当前解释器的文书依赖；当前环境不可用且Skill位于WorkBuddy安装根时，按顺序检查该根下已有的 `arbibuddy`、`default` 受管环境。成功后把 `result.python_executable` 作为本轮 `<document_python>`，替换此前命令里的解释器，后续 `delivery-set`、`render`、`view` 都使用同一个值。例如回执选择受管环境时，不再调用先前的基础Python。解释器或安装发生变化后重新检查；业务输入错误按原契约修正，环境不变时沿用已成功的选择。

```text
cd "<installed_skill_root>" && "<document_python>" -B -X utf8 -m scripts.documents.runtime_cli --workspace "<workspace_root>" render --input "<workspace_root>/.arbibuddy/runtime-input/<nonce>.json"
```

宿主工具支持 `workdir` 时，将它设为此前成功调用的完整已安装 Skill 根目录，原样复用；它与 `--workspace` 的案件工作区、`<document_python>` 的解释器路径分别保存。不要从工作区重新拼接 `.agents/skills/arbibuddy`，也不要截掉嵌套目录。后续更正沿用同一组已成功的值。若宿主明确报目录无效且进程未启动（如 Windows `os error 267`），原输入尚未消费，使用已知正确的完整 `workdir` 重试原命令；不因此重新提交档案或重建集合。

没有可用环境时，回执返回 `next_action=prepare_document_environment` 和 `requirements_file`；说明尚未生成DOCX，并请用户先完成环境准备。用户可在选定Python的环境中安装该requirements文件所列依赖，再重试检查。此操作不安装依赖、不创建虚拟环境、不读取案件或创建交付文件；随包vendor继续按清单校验，vendor失败不回退宿主依赖。内部路径和检查细节只用于工具调用，对用户只说明文书能力是否可用及所需准备。

`--input` 只接受 `.arbibuddy/runtime-input/` 的直接 JSON 子文件；transport 在解析业务请求前先消费并删除该文件，删除失败则不执行文书操作。成功或业务失败后，该输入都已消费；修正请求或更换解释器重试时，先用宿主 `Write` 写入完整的单次JSON，再调用transport，不直接重跑旧 `--input` 路径。它不改变 `document.delivery-set-v1` 或 `document.render-v1` 的业务信封。短请求也可用 `--json`。`view` 是只读受管出口，只有集合完整且 `delivery_set_id` 一致才返回展示列表；失败或集合不完整时文件列表为空。运行结束后不得留下 `runtime-input` 文件，也不得建立 `render-requests`、工作区根 JSON 或其他事实副本。字段和示例只读本参考与 `describe` 结果，不读取、反射或搜索 `scripts/*.py`。

若 render 已成功而 view 返回 `next_action=retry_managed_view_with_access`，保留现有 DOCX、清单和机器交付清单；先在同一工作区按已获授权的文件访问权限重试一次原 view 请求。读取仍失败时如实报告候选文件已生成但受管交付尚未验证，停止展示；无需重新 render 或改动档案 revision。

完整文书正文和核验清单只放在单次 `render` 输入中；案情档案只保存必要的持久化分析摘要、事实变化和下一步，不重复存储整篇正文或清单。先完成会改变档案 revision 的提交，再创建绑定最终 revision 的 delivery set，避免中途改档案后重复建 set。每个 render 请求使用单次 `runtime-input` 文件；transport 会消费并删除它。

WorkBuddy 以已安装 Skill 根目录为 cwd，通过 Bash 原样执行每条 transport，完整读取公开 JSON 的 `ok`、`result`、`errors` 和 `warnings` 后决定下一步；对用户的摘要在回复时组织。沿用单条公开操作，不用 `head -c` 等管道或重定向裁剪 `describe`、`edit-context`、`delivery-set`、`render`、`view` 的回执，管道末端退出0不能证明业务成功。宿主转存大结果时继续读取该次完整结果；回执仍缺失或截断时说明结果尚不能确认，按公开恢复契约处理。具体错误按本参考的恢复规则集中修正。

若随包 `runtime-manifest.json` 的 ABI 与当前解释器不一致，
`runtime_cli` 会在加载文书业务模块前，只从当前用户 `.workbuddy\\binaries\\python\\envs\\` 下按
`arbibuddy`、`default` 顺序探测受管 Python，使用 `-S` 验证实际 ABI/platform 后最多自举一次，
并以原始参数和退出码重新执行；不存在、路径越界、ABI 不匹配或循环自举时保持稳定的
`document_runtime_unavailable`，不联网、不 pip、不建 venv，也不使用宿主 site-packages。

字段尚不明确时使用 `describe --operation delivery-set|render|view|edit-context` 读取对应完整契约；无参数 `describe` 保留兼容的完整渲染描述。无需通过截取输出查字段。

`document.delivery-set-v1` 只接受 `case_id`、`archive_revision`、`mode`、`confirmation_refs` 以及二选一的 `requested_templates` / `requested_document_types`。两个选择字段均接受文书类型或注册别名，不能同时提供；`authority_refs` 属于 render，不放入集合请求。选择 `arbitration_application` 或 `labor-arbitration-application` 都自动包含 `evidence_catalog`，以成功回执的 `expected_document_types` 为准，顺序生成全部成员；不再另建“仅申请书”集合。

最小候选集合请求（须已有真实摘要确认及相应档案）：

```json
{"case_id":"case-0123456789abcdef01234567","archive_revision":1,"mode":"candidate","requested_templates":["labor-arbitration-application"],"confirmation_refs":[]}
```

### 起草前准备

在写长正文前，模型完成本次交付的来源准备：

需要字段说明时，用 `describe --operation render --document-type <document_type>` 取得共享字段、该类型必需区块、candidate / external_final 条件和可执行虚构示例；已掌握契约时不增加调用。`--document-type` 仅与 `--operation render` 配合，使用上文完整文书类型名；不带筛选仍保留原完整契约。render 请求通常省略 `title`，工具按文书类型派生注册标题，并由 `mode` 统一添加候选标识；显式提供的 title 仍须与注册标题相同，标题来源绑定继续使用该注册标题。`examples.worked_example` 连贯展示 create、合批 commit、set、各成员 render、view，申请书示例包含配套证据目录；其身份、事实、计算和确认只供虚构演示，不得复制到真实案件。

1. 确定文书类型、成熟度及必要主体、主张、金额、日期、落款与证据来源；已有档案信息直接复用。主体与落款引用记录须以“姓名/单位名称/角色：完整身份”字段支持完整值，不能靠更长姓名的子串或自由叙述支持。已有来源只用叙述时，在同一事实记录中补充明确字段，保留原陈述、来源与事实等级，不推断身份、不提升确认状态；与其他必要变化合批落档。先按下一节补齐尚缺的摘要确认，再依据已确认口径调用必要计算。
2. 请求分项及正文合计分别取成功计算结果；合同工资标准等原始事实金额按下文 `fact_amount` 绑定，不为一个已知数额补做恒等算术。按[金额 transport](amount-calculate.md#公共金额工具)优先用安全可表达的短 `--json` 请求，逐项取得成功结果；存在真实依赖时先取得前项结果再构造后项，不把预填常量冒充工具结果。落档后以成功回执分配的 CAL 编号逐项核对“项目、结果、引用”，不按计算调用顺序猜编号；计算记录保留结果标签。标题引用含同一标题的记录；主张锁定值使用 `CL-*` 已支持的短语，例如“支付拖欠工资”，完整请求句由模型另外起草。
3. 同一轮准备正文、来源和绑定：新建或确需整理来源时，完整身份优先单独成行（例如 `申请人：张三`），电话、证件与括号说明另行。已有来源包含完整姓名/单位名称或对应角色字段时直接复用；除了换行、分号、逗号，明确字段之间的中文句号也可使用，例如 `申请人：张三。身份证号码：……`，下一字段枚举见 describe 的 `party_source_field_boundaries`。不为这种格式差异改档案或升版；姓名与单位全称逐字匹配，来源括注、主体不明或引用错误仍按来源核对处理。主张绑定使用来源中实际存在的原样短语，不为匹配而新增请求。文书标题作为模型的排版准备信息记录，注明并非新增用户陈述。逐处从最终正文复制金额、完整日期和签名的原样单行片段作为 `occurrences`；同一来源、同一含义的数值重复出现时在同一绑定中列全，每个定位片段在对应区块内唯一。金额片段只含当前绑定的金额；同一行有多个金额时，分别取各自唯一的原样短语，不将整行同时用作多个金额的定位。计算结果用 CAL，原始事实金额用 F / fact_amount；数值相同但业务含义不同的事实与计算结果分别绑定，不能按数值合并。申请书公开示例展示同一工资事实两处定位、已付事实金额与请求结果分别绑定。正文保留必要事实和请求结论，公式展开与重复核算说明优先放独立核验清单；正文保留的每处金额仍须完整绑定，不为规避定位而删减必要事实。
4. 集合含证据目录时，先准备全部目录行，再核对各行“证据名称｜证据内容｜证明目的”与对应 `E-*` 来源。分组证据记录可以支持多行，名称的展开和证明目的仍需有该记录及已知材料依据；必要整理与其他来源一并提交，保留“用户陈述持有、尚未查看、证明力待核”等事实等级，不把拟证明内容写成已证实事实。不要先生成申请书，再为配套目录补来源而使整套升版返工。
5. 本次必要分析、计算、整个集合的交付来源与真实确认记录按[统一确认](rights-scan-and-unified-confirmation.md#一次自然语言确认)合批落档，以成功回执的最终 revision 建立完整集合，再提交已准备的正文和绑定。文字、数值展示或定位问题只修正输入，无事实与口径变化时不再改档案。一次返回的全部错误集中修正，不逐错试渲染；真实新事实、并发或必要确认仍按对应恢复处理。

正文和锁定要素由模型组织，工具不生成法律叙事、不推断确认范围。已有参考和 `describe` 已提供所需字段时直接使用；出现新的字段问题再按返回的聚合错误查对应契约，不重复读取整个契约或枚举源码。

正常首次交付在准备齐全后集中一次 commit、一次 delivery-set、每成员一次 render，再 view 和展示；出现真实新事实或门禁错误才走对应恢复。起草时同时核对主体与落款完整角色、证据名称和正文每处日期/金额定位，沿用下文绑定契约，避免用试渲染代替来源准备。

### 按错误范围修复

一次处理回执的全部错误，先查看 `path`、`reason` 与 `repair_scope`：

| repair_scope | 修复动作 |
| --- | --- |
| request_only | 标题、正文或定位参数问题只修改本次请求；档案与集合未变化时复用当前 revision、delivery_set_id 重试。原文中的两处日期分别截取各自唯一短语即可，无需改写事实或为了定位而拆开自然段。 |
| review_source | 核对引用的当前档案记录与锁定值。引用错或值填错只改请求；确需补充或纠正档案来源时，合批必要变化，再以新 revision 重建完整集合。主体仍须满足完整身份字段校验。 |

`reason` 将区块不存在、片段未出现、片段重复、含多个日期/金额、缺少完整值、值不符、数值截断或重复归属分别定位。只修请求不新增档案记录、确认或集合；若同时有来源、版本、确认等其他错误，先处理这些依赖，不能凭某一项 request_only 绕过其余校验。无实质事实变化时复用已有用户确认，必要的档案格式修正仍遵守原确认范围与版本规则。

### 起草前确认

起草前先按[权益扫描、最终补充与统一确认](rights-scan-and-unified-confirmation.md)核对当前已完成的对话步骤，只补尚未完成的一步并等待答复。该路由适用于普通 candidate，包括被迫解除通知书；“理解风险、先要草稿、不发送”是草稿授权，不替代当前完整摘要确认。已有步骤和未变化的确认直接复用，候选稿无需用户确认实施解除或外发。仲裁申请书的候选稿同样要在权益范围选择、最终开放式补充和当前完整摘要的自然语言确认之后准备。

`delivery-set` 提前核对所有显式 `confirmation_refs`：记录存在、字段可解析、版本等于当前档案，与 `render` 使用同一检查；高风险文书的candidate集合沿用其候选降级契约，确认过期只返回warning，不能因此外发。仲裁申请书还要求档案已有普通摘要确认；`candidate` 空引用允许使用不晚于当前版本的统一确认，`external_final` 要求其中有当前版本确认。唯一机器可读字段是独立一行 `绑定档案修订版本：<提交后的纯数字版本号>`，常见加粗与列表可解析，冲突或不明确值拒绝。确认是否真实、摘要是否完整由模型依据用户原话判断；其他候选能通过 set 不代表对话确认已完成。未完成时补当前缺失步骤，不猜测确认，不绕过完整集合。返回 `confirmation_revision_unreadable` 或 `confirmation_stale` 时按统一确认参考区分格式修复、原确认范围内补录和实质变化，一次合批处理后再建集合；不自动续期或靠改数字伪造授权。申请书及配套证据目录的集合失败时，整包尚未完成。

普通 `candidate` 不因用户已做事实、范围或口径的统一确认就自动填写
`confirmation_refs`，也不得为了让 renderer 通过而新建、改写或猜测确认记录。除下文明确
要求确认的高风险 `external_final` 外，该字段保持空数组；若确需引用，确认记录必须按高风险
确认参考中的公开字段一次写对，不能读取 canonical Markdown 或用内联脚本探测格式。

普通候选的 delivery-set 请求和每一份 render 请求都明确写 `"confirmation_refs": []`。两者必须一致；若错误地把普通统一确认写入 set 而导致成员不匹配，按当前档案重新建立空引用的完整 set，再为其全部成员 render。修正请求即可，沿用真实的摘要确认记录，不增写确认或让用户重新确认已未变化的事实。

## 局部更正与改稿

修改已交付文书时先读[改稿短指南](document-edit.md)，在改档案前取得可信原稿。局部更正、压缩后接续、旧稿失效及外部改写的流程以该指南为唯一说明。

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

仲裁申请书的“事实与理由”、催告函的“劳动关系说明”、解除通知书的“解除理由”和证据目录的“五列表格/证据列表/证据目录表格”可用 `fact_amount` 引用含同一数值的 `F-*`，保留合同工资标准等原始事实金额，并如实标明来源；例如 `{ "kind": "fact_amount", "archive_record_ref": "F-001", "rendered_value": "15000元", "occurrences": [{ "section_id": "grounds", "text": "据本人陈述，约定月薪15000元" }] }`。candidate 可保留待核陈述，external_final 仍须以已确认的当前事实为依据，不能使用分析假设或未确认事实。`fact_amount` 不作为算术结果，也不能进入请求、履行、结算或赔偿金额区块；所有计算结果（含基数平均值、分项和合计）使用 `amount` / `CAL-*`，即使在事实区块或证据说明中出现也沿用该计算来源。模型按金额的业务含义选来源，不为避免报错改写事实或把计算结果转存 `F-*`。

金额与日期使用显式正文定位，不能靠全篇数字搜索判断其业务归属：

正文货币值使用完整半角阿拉伯数字加“元/圆”；万元/亿元、人民币/RMB/CNY/¥ 前缀和中文金额须由模型先改为明确元值，再与计算来源逐处绑定。繁体、口语或全角写法（如“壹萬圓整”“一万块钱”“６０００元”）以及元后的角分、整/正后缀同样返回可定位的 `binding_mismatch`，先规范表达再重建绑定。这些表达不会被当作“没有金额”放行，也不由 renderer 猜测换算或改写正文。主体与落款绑定使用“角色：完整姓名/单位全称”；external_final 必须在正文存在可校验的对应标签，各处同标签身份精确一致。申请人、被申请人、答辩人、被答辩人、执行主体和收件人专用章节须各自包含完整身份绑定，不能仅用其他章节或落款替代主体位置。劳动者可对应“通知人”，用人单位可对应“收件人/致”；其他身份信息另列字段，不能作为任意姓名后缀。

- `occurrences` 为非空对象数组，每项只有 `section_id` 和 `text`，均为非空单行字符串。`section_id` 引用本请求正文区块；`text` 从该区块原样复制，是在该区块恰好出现一次、包含完整金额、日期或签署角色与姓名的短片段，不填写字符偏移或内部编号。
- 一个金额在正文中多次出现时，逐处列出定位。每个正文货币金额只能归属一个金额绑定；原始事实金额按上述 `fact_amount` 处理，不同计算主张、计算所得基数和合计各自绑定对应计算记录，不要求全篇金额相等。金额定位必须覆盖完整数字，不能把 `6000.00元` 当成 `16000.00元` 的一部分。
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

单次 JSON 输入沿用[档案文件边界与失败清理](model-led-case-archive.md#文件边界与容量)。发布失败后，受管事务确认旧稿已回滚、或 canonical 完整且没有未决备份时，可接收重读档案后重建的新请求；回滚本身中断且 canonical 缺失时，受管事务先核验旧备份与旧事务的身份、完整性及案件归属，再原子恢复旧稿并继续新请求。未证明完整恢复时继续返回 `publication_conflict`，保留唯一旧稿。已提交备份只在核验新 canonical、原备份和事务身份后归为非 canonical；同请求重放会再次受管回收，清理失败是 warning，不反转已成功的新稿，也不把备份作为正式交付成员。

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

遇到隐私缺失，模型在 candidate 中声明占位符并交付候选，补齐信息、重新读取档案、取得必要确认后重新调用；遇到绑定冲突或事实冲突，模型核对档案和成功计算结果，不自行改值。一次处理回执中所有错误：正文内容正确而只缺引用或定位时沿用原正文、修正绑定，不重做计算、不改档案；只有事实、结果来源或确认内容确实需改时才合批提交，再建集合。遇到 `archive_revision_conflict`、`confirmation_stale` 或 external_final 的 `authority_stale`，先 `CaseArchive.read` 当前档案再重建请求。动态现行规则核验受阻、程序阶段待核实或高风险确认过期时，三类程序文书的 candidate 可以保留为候选并返回可恢复 warning，但不得把它标为 `final_ready`。任何错误都不发布半成品。

恢复示例：

```json
{
  "when": "archive_revision_conflict、confirmation_stale、authority_stale 或 binding_mismatch",
  "action": "重新 read 当前案情档案，按错误 path 修正文书请求，再以新的 archive_revision 重试",
  "guarantee": "失败不会创建或替换 canonical DOCX；成功重放返回同一 canonical 结果"
}
```

`installed_skill_modified` 表示安装树或其身份链校验失败；返回 `recoverable=false`，在预检阶段停止且不创建 staging 或 canonical 文件。若错误回显 `native_upload_marker_missing`、`native_upload_marker_stale` 或 `native_upload_vendor_runtime_*`，说明是 WorkBuddy 原生上传的便携标记或随包运行时问题，应重新上传已验签 ZIP 并完全重启客户端；原生上传不生成、也不需要 `arbibuddy.install.json`。若错误回显 `external_trust_anchor_*` 或 `install_receipt_*`，说明是本地受管安装的外部信任锚/回执问题，应重新执行受管安装或更新；不得把这两条恢复路径混用。`document_runtime_unavailable` 表示当前解释器与随包 WorkBuddy 文书运行时不匹配、vendor 摘要不一致或 DOCX 依赖不可导入；返回 `recoverable=false`，明确“未生成 DOCX”，只能重新安装兼容包或更换已支持环境后重试，不得运行期 pip、创建虚拟环境或临时脚本。

`ooxml_invalid`、`permission_denied` 和 `io_failure` 表示其他运行环境或文件事务边界，说明本次新的 Word 未成功交付。覆盖被拒绝时保留原 DOCX、独立核验清单和机器清单；先关闭 Word/WPS 及文件预览中占用的目标文件，再通过宿主 `Write` 重写已消费的单次输入，按同一公开生成请求重试。旧产物及事务锁由受管事务维护，不删除、移走或手工拼装旧产物，也不手工清除档案锁或交付锁来绕过拒绝。关闭占用后同一受管请求仍失败时，停止生成并回报限制，不用临时脚本试删旧稿或诊断进程；不能因锁内进程号仍存在或删除成功就断言锁已过期、没有占用或旧稿无损。需要保留故障经验时只记录错误现象和受管恢复路径，清单的“请勿外发”等提醒原样保留；案件记忆的来源边界见[案情档案参考](model-led-case-archive.md)。

工具不引入 LibreOffice、Poppler 或视觉模型作为运行时依赖；模型不得调用宿主通用 documents 技能、`load_workspace_dependencies` 或 `render_docx.py`，也不得在 canonical `output` 目录创建 PDF、PNG、QA 子目录或其他旁路文件。提交或发送前，用户仍须在 Word/WPS 中预览字体、层级、间距、分页、表格、金额和落款。
