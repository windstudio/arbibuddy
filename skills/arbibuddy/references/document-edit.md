# 已交付文书的局部更正与改稿

用户要求修改已交付文书的电话、地址等内容时，先读本指南，再开始业务调用；同会话压缩后和新会话接续也从这里恢复。仅询问候选状态走[候选稿复用与更新](document-render.md#候选稿复用与更新)；明确只读档案仍遵循 Skill 的恢复-only 硬停。

## 先取原稿，再改档案

1. **取得原稿。** 沿用成功的安装根、当前工作区和文书解释器，首个业务动作是下列 `edit-context`，在提交事实更正前取得可信原稿。case_id 未知时才通过公开 `read-current` 读取当前工作区唯一案件；安装或解释器确有变化时才重做 runtime-check。

   ```text
   cd "<installed_skill_root>" && "<document_python>" -B -X utf8 -m scripts.documents.runtime_cli --workspace "<workspace_root>" edit-context --json '{"case_id":"<case_id>"}'
   ```

   宿主支持 `workdir` 时用已成功的安装根作执行目录。命令原样执行并读取完整公开 JSON 回执；其他调用细节按需见[文书 transport](document-render.md#模型唯一入口与客户端-transport)。可带 `delivery_set_id` 核对已知集合；默认取得完整集合，`document_types` 筛选返回类型时仍校验整个集合。

2. **准备完整更正。** 取得 `ok:true` 完整回执后，结果的 `archive` 是当前权威档案，各成员 `blocks` 是可信原稿，`render_requirements` 提供该类型契约。直接据此准备整个集合，只改用户指定内容，保留其他正文、表格、编号与来源。所需内容已在结果中时直接使用；缺项才定向 `describe`，无需另读原 DOCX 或分多次打印字段。

3. **合批重交付。** 必要事实更正与真实确认关联合批 commit，按成功 revision 建一个完整 set，各成员顺序 render。复用未变范围的[统一确认](rights-scan-and-unified-confirmation.md#一次自然语言确认)，没有新金额或法律口径时沿用原计算。external_final 严格绑定当前 revision，因此电话更新后也重新生成未变目录。每次 `--input` 调用前用宿主 Write 写入完整单次 JSON；成功或业务失败后输入都已消费，修请求时重新 Write，在档案未变时复用 revision/set。

4. **展示并收尾。** 集合全部成功后取得完整 view，将 `present_files_arguments` 原样用于一次宿主展示，说明指定更正与交付状态；不再提取 XML 重验工具已通过的内容。正常受管生成更新当前文件，不承诺另有旧稿备份。

## 从返回区块重建正文与绑定

`document.edit-context-v1` 返回的是当前排版后的文本，不是旧请求或 bindings。模型结合 `archive` 重建正文与逐处绑定，保留 `render_requirements.sections` 的全部必需区块；可见标题对应独立标题区块，隐藏章节不虚构标题。

段落含 `text`、`style_id`、`numbering`、`source_locator`，表格按行、单元格及嵌套段落保留顺序。**申请书列表按 `render_requirements.numbered_section_input` 重建：返回的 `text` 不含自动编号，原编号项恢复 `1.`、`2.` 等输入标记，原本无编号的合计等段落保留普通文本。** renderer 再生成真实 Word 编号；仅拼接 text 或插空行不能保留这种区别。证据目录沿用既有行格式及名称、内容、证明目的。

按[锁定要素与档案绑定](document-render.md#锁定要素与档案绑定)核对来源：金额与 CAL 对应，全部完整日期及每个出现位置都需定位，包括未改的日期和范围起点。同一日期在请求与事实理由中出现时列全 occurrences。只改电话仍需重建完整绑定；单次正文经输入消费机制使用，不另存持久正文或转换脚本。

## 无法取得可信旧稿时

edit-context 只读校验安装身份、档案新鲜度、完整集合、机器清单、产物摘要与 OOXML，返回瞬时正文，不保存输入副本。摘要不符或旧稿过期时返回 `ok=false`、`result=null`，不提供可信原稿。保留文件，说明旧稿暂不能验证，依据当前档案重新起草；缺失内容由用户提供。私有会话日志、memory、旧请求文件不能用来恢复正文，清单也不能手工修改以绕过检查。

宿主 Office 保存只代表外部文件已保存。原文件被改写时，保留原件、按上述分支重建，外部正文只作待核参考；事实更正后仍重建完整集合、render 全部成员并通过 view，再展示。宿主交接和仅预览回写按[平台能力](platform-capabilities.md)处理。

只有回执明确报告 `external_generation_retained` 时，才能说明外部改写的旧代被保留为非交付材料，不代表任意历史版本可找回。view/edit-context 只使用核验通过的新文件，旧备份不作改稿来源或附件；不删除备份或补写旧清单。
