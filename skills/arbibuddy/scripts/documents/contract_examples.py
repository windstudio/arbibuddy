"""Static, fictional worked examples; never used to compose a user's document.

The type registry remains in public.py.  These are executable documentation,
not templates, archive defaults, automatic bindings or a second fact source.
"""
from copy import deepcopy


_CASE = "case-0123456789abcdef01234567"
_DATE = "2026年9月7日"
_EVIDENCE = "工资流水｜银行流水｜拟证明工资支付情况，内容待核"
_CLAIMS = {
    "employment_obligation_demand_letter": "支付工资差额",
    "forced_termination_notice": "未及时足额支付劳动报酬",
    "arbitration_application": "支付工资差额",
    "arbitration_defense": "请求驳回对方请求",
    "evidence_catalog": "支付工资差额",
    "company_deregistration_restriction_request_shanghai": "请求核实注销风险并视情协调",
    "property_preservation_application": "请求依法保全财产",
    "enforcement_application": "请求执行生效仲裁裁决",
}
_TEXTS = {
    "employment_obligation_demand_letter": [
        ("收件人", "致：示例公司"),
        ("劳动关系说明", "本人张三与贵单位的劳动关系存续。"),
        ("具体义务", "请支付工资差额6000.00元。"),
        ("履行期限", "请于2026年9月15日前履行上述义务。"),
        ("沟通与保留权利", "请书面回复，本人保留依法主张权利的权利。\n通知人：张三\n2026年9月7日"),
    ],
    "forced_termination_notice": [
        ("收件人", "致：示例公司"),
        ("解除意思表示", "本人现通知贵单位解除劳动合同，解除生效日期为2026年9月15日。"),
        ("解除理由", "因贵单位未及时足额支付劳动报酬，本人据此提出解除。"),
        ("结算与手续", "请依法办理结算和离职手续。"),
        ("落款", "通知人：张三\n2026年9月7日"),
    ],
    "arbitration_application": [
        ("申请人", "申请人：张三\n联系电话：13800000000。"),
        ("被申请人", "被申请人：示例公司\n法定代表人：李四。"),
        ("仲裁请求", "请求支付工资差额6000.00元。"),
        ("事实与理由", "据本人陈述，合同约定月工资12000元，工资条列明应发12000元，已付6000元；合同、工资条及流水内容仍待核。\n请求支付上述工资差额6000.00元。"),
        ("落款", "此致\n上海市示例劳动人事争议仲裁委员会\n申请人：张三\n2026年9月7日"),
    ],
    "arbitration_defense": [
        ("答辩人", "答辩人：张三"),
        ("被答辩人", "被答辩人：示例公司"),
        ("答辩意见", "请求驳回对方请求，具体依据另行核对。"),
        ("落款", "答辩人：张三\n2026年9月7日"),
    ],
    "evidence_catalog": [
        ("标题", "证据目录"),
        ("五列表格", "1|工资流水|银行流水|拟证明工资支付情况，内容待核"),
    ],
    "company_deregistration_restriction_request_shanghai": [
        ("申请人", "申请人：张三"),
        ("被申请人", "被申请人：示例公司"),
        ("仲裁受理信息", "上海劳动仲裁已受理，案号为示例仲字第123号。"),
        ("申请事项", "请求核实注销风险并视情协调。"),
        ("事实与理由", "本案为上海劳动争议，存在企业注销风险。本材料为上海专项实务材料，不替代简易注销异议或财产保全，也不保证阻止注销。"),
        ("附件", "仲裁受理通知书、注销风险材料。"),
        ("落款", "申请人：张三\n2026年9月7日"),
    ],
    "property_preservation_application": [
        ("申请人", "申请人：张三"),
        ("被申请人", "被申请人：示例公司"),
        ("保全请求", "请求依法保全财产，限额6000.00元。"),
        ("事实与理由", "劳动仲裁已受理、裁决作出前，存在财产转移风险；申请错误可能承担损害赔偿责任。"),
        ("财产线索", "被申请人银行账户线索另附，真实性及可供保全情况待核。"),
        ("担保", "拟以保险保函提供担保，具体以法院审查为准。"),
        ("落款", "申请人：张三\n2026年9月7日"),
    ],
    "enforcement_application": [
        ("申请执行人", "申请执行人：张三"),
        ("被执行人", "被执行人：示例公司"),
        ("执行请求", "请求执行生效仲裁裁决，尚未履行金额6000.00元。"),
        ("事实与理由", "生效仲裁裁决已超过履行期限，被执行人尚未履行。申请执行时效及执行管辖依据待专业复核。"),
        ("财产线索", "被执行人银行账户线索另附，真实性及可供执行情况待核。"),
        ("附件", "生效仲裁裁决及送达证明。"),
        ("落款", "申请执行人：张三\n2026年9月7日"),
    ],
}


def _binding(kind, ref, value, *locations):
    result = {"kind": kind, "archive_record_ref": ref, "rendered_value": value}
    if locations:
        result["occurrences"] = [{"section_id": section, "text": text} for section, text in locations]
    return result


def render_worked_example(document_type, requirements):
    """Return one coherent create/commit/set/render/view example for a type."""
    title = requirements["title"]
    # Ordinary examples also demonstrate final mode. High-risk examples are
    # deliberately candidates: fictional setup is no authority verification.
    high_risk = document_type in {"forced_termination_notice", "company_deregistration_restriction_request_shanghai",
                                 "property_preservation_application", "enforcement_application"}
    mode = "candidate" if high_risk else "external_final"
    sections = [{"section_id": f"s{i}", "heading": heading, "full_text": text}
                for i, (heading, text) in enumerate(_TEXTS[document_type])]
    bindings = [_binding("document_heading", "F-003", title)]
    if document_type != "evidence_catalog":
        roles = ("劳动者", "用人单位", "通知人") if document_type in {
            "employment_obligation_demand_letter", "forced_termination_notice"} else (
                "答辩人", "被答辩人", "答辩人") if document_type == "arbitration_defense" else (
                "申请执行人", "被执行人", "申请执行人") if document_type == "enforcement_application" else (
                "申请人", "被申请人", "申请人")
        closing_id = sections[-1]["section_id"]
        bindings.extend([
            _binding("party", "F-001", f"{roles[0]}：张三"),
            _binding("party", "F-002", f"{roles[1]}：示例公司"),
            _binding("claim", "CL-001", _CLAIMS[document_type]),
            _binding("signature", "F-001", f"{roles[2]}：张三", (closing_id, f"{roles[2]}：张三")),
        ])
        for value in (_DATE, "2026年9月15日"):
            locations = [(s["section_id"], line) for s in sections
                         for line in s["full_text"].splitlines() if value in line]
            if locations:
                bindings.append(_binding("date", "F-004", value, *locations))
        amounts = [(s["section_id"], line) for s in sections
                   for line in s["full_text"].splitlines() if "6000.00元" in line]
        if amounts:
            bindings.append(_binding("amount", "CAL-001", "6000.00元", *amounts))
    if document_type == "arbitration_application":
        bindings.extend([
            _binding("fact_amount", "F-005", "12000元",
                     ("s3", "合同约定月工资12000元"), ("s3", "工资条列明应发12000元")),
            _binding("fact_amount", "F-006", "6000元", ("s3", "已付6000元")),
        ])
    evidence = _EVIDENCE
    if document_type in {"company_deregistration_restriction_request_shanghai", "property_preservation_application", "enforcement_application"}:
        evidence = {
            "company_deregistration_restriction_request_shanghai": "仲裁受理通知书｜受理材料｜拟证明已经受理，内容待核",
            "property_preservation_application": "银行账户线索｜账户线索材料｜拟证明财产线索，内容待核",
            "enforcement_application": "生效仲裁裁决书｜生效仲裁裁决及送达证明｜拟证明执行依据，内容待核",
        }[document_type]
    if document_type in {"evidence_catalog", "forced_termination_notice", "company_deregistration_restriction_request_shanghai",
                         "property_preservation_application", "enforcement_application"}:
        bindings.append(_binding("evidence", "E-001", evidence))
    if document_type == "enforcement_application":
        bindings.append(_binding("attachment", "E-001", "生效仲裁裁决及送达证明"))
    request = {"case_id": _CASE, "archive_revision": 1, "document_type": document_type,
               "template_version": "1.0.0", "mode": mode,
               "sections": sections, "locked_bindings": bindings, "placeholders": [],
               "confirmation_refs": [], "authority_refs": []}
    titles = f"文书准备信息（模型排版元数据，非新增用户陈述）：{title}。"
    if document_type == "arbitration_application":
        titles += "配套文书标题：证据目录。"
    contents = [
        ("fact", "事实状态：用户陈述\n姓名：张三；联系电话：13800000000。"),
        ("fact", "事实状态：用户陈述\n单位名称：示例公司；法定代表人：李四。"),
        ("fact", titles),
        ("fact", "文书日期：2026年9月7日；履行期限或解除生效日期（按本示例目标）：2026年9月15日。"),
        ("fact", "事实状态：用户陈述\n约定月薪：12000元。" + (
            "用户陈述合同约定及工资条所列应发均为12000元，材料内容仍待核。"
            if document_type == "arbitration_application" else "")),
        ("claim", f"本示例用户选定请求：{_CLAIMS[document_type]}。"),
        ("calculation", "示例成功计算摘要：应发12000元、已付6000元，工资差额。\n确定性计算结果：6000.00元。"),
        ("evidence", evidence),
        ("confirmation", "用户自然语言确认摘要：本虚构示例已完成权益范围选择、最终补充和摘要确认。\n绑定档案修订版本：1\n关键事实：双方身份及所述事实。\n权益范围：本例选定请求。\n主要口径：必要金额来自成功计算结果。\n重要假设：材料内容及未核口径仍待核。\n拟交付内容：本例文书及配套目录（如适用）。"),
    ]
    # Keep narrative premises in the fictional archive as well as locked fields.
    contents.append(("fact", {
        "employment_obligation_demand_letter": "事实状态：用户陈述\n劳动关系存续，应发工资12000元、已付6000元；用户选择本例履行期限。",
        "forced_termination_notice": "事实状态：用户陈述\n未及时足额支付劳动报酬，用户拟按本例生效日准备解除候选稿，尚未确认实施或外发。",
        "arbitration_application": "事实状态：用户陈述\n双方存在劳动关系，本例应发工资12000元、已付6000元；合同、工资条及流水内容仍待核。",
        "arbitration_defense": "事实状态：用户陈述\n用户作为答辩人请求驳回对方请求，具体答辩依据待核。",
        "evidence_catalog": "事实状态：用户陈述\n用户持有工资流水，内容尚未查看，拟用于核对工资支付情况。",
        "company_deregistration_restriction_request_shanghai": "事实状态：用户陈述\n上海劳动仲裁已受理，案号为示例仲字第123号；存在企业注销风险，持有受理通知书和风险材料，内容待核。",
        "property_preservation_application": "事实状态：用户陈述\n劳动仲裁已受理、裁决作出前，存在财产转移风险；持有银行账户线索，拟以保险保函担保，具体待法院审查。示例拟保全限额与工资差额相同，实际案件由模型确定口径。",
        "enforcement_application": "事实状态：用户陈述\n仲裁裁决已生效且超过履行期限，尚未履行；持有裁决及送达证明、银行账户线索，时效和管辖待专业复核。示例未履行额与工资差额相同，实际案件以执行依据确定。",
    }[document_type]))
    renders = [request]
    if document_type == "arbitration_application":
        catalog = render_worked_example("evidence_catalog", {"title": "证据目录"})["render_requests"][0]
        renders.append(catalog)
    return {
        "purpose": "只在虚构工作区演示公开字段；不要把示例身份、事实、计算、确认或法律选择写入真实案件。",
        "substitution": "create 后用回执 case_id 替换所有示例 case_id；以 commit 成功 revision 建 set，再把 set 回执 delivery_set_id 填入全部 render_requests，顺序执行后 view。新档案各类编号从001起。实际案件须按回执编号绑定。",
        "calculation_provenance": "CAL-001 是本虚构例预置的成功计算摘要，不是生产调用的默认值；真实案件先调用 amount.calculate，再将实际结果合批提交。",
        "create": {"jurisdiction": "上海市", "initial_goal": f"虚构公开契约示例：{title}"},
        "commit": {"case_id": _CASE, "expected_revision": 0, "change_summary": "一次准备虚构来源和真实示例确认",
                   "changes": [{"operation": "append", "record_type": kind, "content_markdown": content} for kind, content in contents]},
        "delivery_set": {"case_id": _CASE, "archive_revision": 1, "mode": mode,
                         "requested_document_types": [document_type], "confirmation_refs": []},
        "render_requests": deepcopy(renders),
        "view": {"case_id": _CASE},
        "completion": "成功 view 的完整列表才授权展示；单份 render 成功不等于集合完成。高风险示例为 candidate，不证明可以外发。",
    }
