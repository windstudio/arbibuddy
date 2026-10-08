# 历史技术映射摘要

下表仅保留旧代码/测试与当前参考和工具的责任映射，供回归测试核对。旧工单编号是历史分类标签；原工单、审核过程及完整验收报告不在公开仓库。表中的旧路径不代表当前可调用入口。当前架构见 [架构说明](domain.md)。

| 来源/分支/场景 | 类型 | 处置 | 新权威位置或证据 | 说明 |
| --- | --- | --- | --- | --- |
| `scripts/employment_relationship/service.py:STANDARD_FORM` | 旧分支 | preserve | `references/model-led/employment-relationship.md#能力边界` | 标准用工深度支持语义保留 |
| `scripts/employment_relationship/service.py:COMPLEX_FORMS` | 旧分支 | revise | `references/model-led/employment-relationship.md#能力边界` | 复杂用工改为分层支持，不作确定性认定 |
| `scripts/employment_relationship/service.py:analyze_employment_relationship_case` | 运行层 | obsolete | 本表；模型分析卡 Scenario | 不保留专项分析执行器 |
| `scripts/employment_relationship/service.py:_prepare_amounts` | 金额边界 | calculator | `references/amount-calculate.md` | 劳动关系本身无金额；具体金额由模型另行选择 |
| `scripts/employment_relationship/service.py:_complex_judgment` | 判断分支 | revise | `references/model-led/employment-relationship.md#四类最小行为样本` | 保留信息不足边界，取消脚本改写判断 |
| `scripts/employment_relationship/knowledge.py` 与 `references/legal/employment-relationship-rules.v1.json` | 规则来源 | preserve | `references/model-led/employment-relationship.md` + 法律基线 | 规则与官方来源继续作为模型参考 |
| `references/employment-relationship.md` | 旧领域参考 | revise | `references/model-led/employment-relationship.md` | 旧 CLI 段落只作迁移素材，模型主路径改读新参考 |
| `tests/scenarios/test_employment_relationship.py::test_complete_virtual_case_persists_supported_employment_relationship_analysis` | 成立场景 | preserve | `tests/scenarios/test_model_led_employment_routing_wage_calculation.py` | 观察分析卡与证据引用，不固定旧输出 |
| `tests/scenarios/test_employment_relationship.py::test_virtual_case_with_only_user_statement_stays_information_insufficient` | 信息不足场景 | preserve | `references/model-led/employment-relationship.md#四类最小行为样本` | 保留条件化判断 |
| `tests/scenarios/test_employment_relationship.py::test_dispatch_arrangement_is_limited_to_layered_support_and_professional_review` | 复杂主体场景 | revise | 同上 | 由模型解释分层边界，旧动态核验输入不作运行协议 |
| `scripts/dispute_routing/service.py:社会保险/住房公积金` | 路由分支 | revise | `references/model-led/dispute-routing.md#路由状态与边界` | 保留行政路径和请求对象差异 |
| `scripts/dispute_routing/service.py:conditional_cross_route` | 交叉分支 | preserve | `references/model-led/dispute-routing.md#事实、证据与交叉影响` | 保留待遇损失、解除等条件交叉 |
| `scripts/dispute_routing/service.py:六类复杂争议` | 分层分支 | preserve | `references/model-led/dispute-routing.md` | 只提供风险、核验和专业复核 |
| `scripts/dispute_routing/service.py:_verification` | 核验降级 | revise | `references/legal-verification.md` | 核验由模型完成，档案只记录最小结论 |
| `scripts/dispute_routing/service.py:_scan_routes_for_update` | 旧扫描门禁 | obsolete | 03 号工单行为参考 | 不把运行期注册表作为主链阻断 |
| `scripts/dispute_routing/knowledge.py` 与 `references/legal/dispute-routing-rules.v1.json` | 规则来源 | preserve | `references/model-led/dispute-routing.md` | 保留法源线索和上海差异 |
| `references/dispute-routing.md` | 旧领域参考 | revise | `references/model-led/dispute-routing.md` | 新路径不使用旧 CLI 和绝对路径 |
| `tests/scenarios/test_social_insurance_and_complex_routing.py::test_complete_case_routes_social_insurance_and_housing_fund_separately` | 行政路由场景 | preserve | 新路由参考与行为 Scenario | 保留社保/公积金分开 |
| `tests/scenarios/test_social_insurance_and_complex_routing.py::test_six_complex_disputes_are_limited_to_risk_and_professional_review` | 复杂争议场景 | preserve | 新路由参考与行为 Scenario | 保留六类分层边界 |
| `tests/scenarios/test_social_insurance_and_complex_routing.py::test_social_insurance_cross_route_is_conditional_and_recommends_review` | 交叉路由场景 | preserve | 新路由参考与行为 Scenario | 保留条件化与专业复核 |
| `tests/scenarios/test_social_insurance_and_complex_routing.py::test_housing_fund_loss_request_is_not_left_on_plain_complaint_route` | 公积金交叉场景 | revise | 新路由参考与行为 Scenario | 重新表达请求对象，不固定旧卡片文本 |
| `tests/scenarios/test_social_insurance_and_complex_routing.py::test_verification_failure_downgrades_every_route_to_pending` | 核验失败场景 | preserve | `references/legal-verification.md` | 一般分析继续，受影响路径降级 |
| `tests/scenarios/test_social_insurance_and_complex_routing.py::test_invalid_or_out_of_scope_inputs_are_rejected_without_persistence` | 私有输入校验 | obsolete | 新公共工具契约 | 不把旧 CLI 字段拒绝当作模型主链门禁 |
| `scripts/wage_analysis/service.py:_validate_wage_calculation_dimensions` | 维度分支 | calculator | `references/amount-calculate.md#数学与舍入` | 仅保留单位匹配和显式数学约定 |
| `scripts/wage_analysis/structured_claims.py:拖欠/降薪/法定津贴` | 工资主张分支 | revise | `references/model-led/wage-analysis.md` | 法律适用回到模型，证据角色和状态保留 |
| `scripts/wage_analysis/variable_remuneration.py:提成/项目/绩效/年终奖/十三薪` | 浮动报酬分支 | revise | `references/model-led/wage-analysis.md#收入性质与条件` | 保留支付条件、收入身份和重复获偿边界 |
| `scripts/wage_analysis/wage_floor.py` | 工资下限规则 | unresolved | `references/legal/wage-floor-rules.v1.json` + 动态核验 | 地区、生效日和适用性需继续核验 |
| `scripts/wage_analysis/partial_month.py` 与 `holiday_schedule.py` | 日期/分段算术 | calculator | `references/amount-calculate.md#数学与舍入` | 本工单只迁移纯数学边界，不删除旧实现 |
| `references/wage-analysis.md` | 旧领域参考 | revise | `references/model-led/wage-analysis.md` | 新模型路径读取新参考，旧 CLI 内容仅保留追踪 |
| `references/topics/wage-analysis.md` | 旧 PublicTurn 主题参考 | obsolete | 本表与新工资参考 | 只验证旧阶段/旧契约，不是新权威 |
| `references/legal/wage-rules.v1.json` | 全国工资法源登记 | preserve | `references/model-led/wage-analysis.md` + 法律基线 | 稳定法元数据继续使用 |
| `references/legal/wage-floor-rules.v1.json` | 地方工资下限登记 | unresolved | `references/legal-verification.md` | 动态数值和适用性不由计算器决定 |
| `tests/scenarios/test_wage_analysis.py::test_complete_virtual_wage_case_persists_traceable_analysis` | 工资成立场景 | preserve | 新闭环 Scenario | 迁移事实、证据、公式和合计 |
| `tests/scenarios/test_wage_analysis.py::test_unavailable_dynamic_verification_is_recorded_and_downgrades_judgment` | 核验降级场景 | preserve | 新工资参考与法律核验参考 | 保留 candidate 可继续 |
| `tests/scenarios/test_wage_analysis.py::test_analytical_hypothesis_cannot_enter_confirmed_analysis` | 事实状态场景 | preserve | 新工资/劳动关系参考 | 保留事实与假设区分 |
| `tests/scenarios/test_wage_analysis.py::test_claim_cannot_use_a_key_fact_outside_the_confirmed_summary` | 确认范围门禁 | revise | 03 号工单确认参考 | 由模型确认语义替代旧 payload 门禁 |
| `tests/scenarios/test_wage_analysis.py::test_partial_month_wage_rejects_monthly_basis_times_workdays_without_write` | 单位错配场景 | calculator | `tests/amount_calculator/test_model_led_amount_contract.py` | 改为公开 amount.calculate 错误路径 |
| `tests/scenarios/test_wage_analysis.py::test_partial_month_exact_wage_requires_structured_calendar_inputs_without_write` | 期间输入不足场景 | calculator | 同上 | 缺少明确期间/分段不产精确结果 |
| `tests/scenarios/test_wage_analysis.py::test_partial_month_exact_wage_rejects_workdays_that_conflict_with_official_calendar` | 日历冲突场景 | calculator | 同上 + 动态核验 | 纯日期关系与法源触发分开 |
| `tests/scenarios/test_wage_analysis.py::test_partial_month_exact_wage_rejects_official_source_from_wrong_year_without_write` | 来源年份场景 | unresolved | `references/legal-verification.md`；[有界验收摘要](../acceptance-summary.md) | 保留旧迁移分类；2026-10-05 年度错配与正确年度行为已按实质内容有界补证，不追认旧工作日数或工资公式，个案来源适用仍需核对 |
| `tests/scenarios/test_structured_wage_claims_cli.py` 中 exact/placeholder/scenario、替代证据和抗辩场景 | 工资行为组 | revise | `references/model-led/wage-analysis.md` | 保留业务意图，去除旧 CLI/私有路径断言 |
| `tests/scenarios/test_variable_remuneration_claims_cli.py` 中五类收入条件、共享收入和重复主张场景 | 奖金行为组 | revise | `references/model-led/wage-analysis.md` | 保留支付条件、primary/fallback 和重复获偿边界 |
| `tests/amount_calculator/test_cli.py` 与 `tests/amount_calculator/test_rc14_amount_invariants.py` | 算术回归 | calculator | `tests/amount_calculator/test_model_led_amount_contract.py` | 旧 CLI 仍保留；新公开契约覆盖 golden |
| `scripts/amount_calculator/cli.py:main -> core.calculate_batch` | legacy Runtime Adapter/CLI | obsolete | `scripts.amount_calculator.public.AmountCalculator.calculate` 与 `references/amount-calculate.md` | 旧 CLI 只接受顶层 `items` 私有载荷；仅作迁移/回归素材，不属于 `amount.calculate-v1`，不新增双载荷兼容 |
| `tests/cli_orchestration/test_analysis_examples.py`、`scripts/cli_orchestration/registry.py`、三项旧 `cli.py` | 私有编排/CLI | obsolete | 本表与新 Skill 路由 | 不调用旧分析器，不删除旧文件 |
| `tests/scenarios/test_social_insurance_and_complex_routing.py::test_routing_preserves_existing_claim_and_amount_sections` | 档案合并场景 | preserve | 档案唯一事实源 Scenario | 保留分析结果不覆盖既有事实与计算 |
| `tests/scenarios/test_social_insurance_and_complex_routing.py::test_social_insurance_termination_next_step_is_forced_through_high_risk_gate` | 高风险交叉场景 | revise | 新路由参考 + 高风险参考 | 保留门槛语义，不保留旧阶段轨迹 |
| `tests/scenarios/test_social_insurance_and_complex_routing.py::test_expired_or_conflicting_sources_are_automatically_downgraded` | 来源降级场景 | preserve | `references/legal-verification.md` | 保留过期/冲突降低结论强度 |
| `tests/scenarios/test_social_insurance_and_complex_routing.py::test_route_ids_cannot_be_silently_reassigned_after_reordering` | 固定 ROUTE 编号门禁 | obsolete | 新档案提交器稳定编号原则 | 不把旧客户端编号顺序当分析语义 |
| `tests/scenarios/test_wage_analysis.py::test_public_cli_blocks_valid_analysis_until_rights_scan_is_complete` | 旧权益门禁 | obsolete | 03 号模型行为参考 | 不恢复脚本阶段门禁 |
| `tests/scenarios/test_wage_analysis.py::test_analysis_rejects_duplicate_calculation_ids` | 旧计算 ID 门禁 | calculator | `amount.calculate` 稳定派生 ID | 由公共计算结果和档案提交约束替代 |
| `tests/scenarios/test_wage_analysis.py::test_malformed_calculation_returns_a_stable_cli_error` | 旧 CLI 错误形状 | obsolete | 新 `amount.calculate` 错误契约 | 不保留私有 CLI payload |
| `tests/scenarios/test_structured_wage_claims_cli.py::test_missing_wage_period_id_error_names_exact_json_path` | 旧 JSON 路径错误 | obsolete | 新工具公共 path 错误 | 不让用户理解私有 JSON |
| `tests/scenarios/test_structured_wage_claims_cli.py::test_missing_business_difficulty_review_error_names_exact_json_path` | 经营困难抗辩错误 | revise | `references/model-led/wage-analysis.md` | 保留待核查语义，改为模型分析卡 |
| `tests/scenarios/test_structured_wage_claims_cli.py::test_each_canonical_claim_with_sufficient_facts_yields_exact_traceable_result` | 工资精确结果 | calculator | 新闭环 Scenario + amount golden | 算术迁移，法律口径仍由模型选择 |
| `tests/scenarios/test_structured_wage_claims_cli.py::test_exact_wage_amount_with_evidence_gaps_remains_exact_draft_ready` | 证据缺口候选 | revise | 新工资参考 | 保留条件化结果，不固定旧 draft 状态 |
| `tests/scenarios/test_structured_wage_claims_cli.py::test_wage_arrears_exact_accepts_contract_bank_flow_and_tax_record_without_payslip` | 替代证据 | preserve | 新工资参考#支持范围与事实状态 | 保留无工资条不机械阻断 |
| `tests/scenarios/test_structured_wage_claims_cli.py::test_each_canonical_claim_with_missing_key_fact_yields_placeholder` | 输入不足占位 | revise | 新工资参考 + amount contract | 保留 placeholder 语义 |
| `tests/scenarios/test_structured_wage_claims_cli.py::test_each_canonical_claim_with_conflicting_evidence_yields_scenarios` | 证据冲突情景 | preserve | 新工资参考#四类行为样本 | 保留 conflict 与分情景 |
| `tests/scenarios/test_structured_wage_claims_cli.py::test_pay_cut_rejects_treating_passive_conduct_as_consent_without_write` | 降薪同意边界 | preserve | 新工资参考#收入性质与条件 | 沉默/继续工作不自动等于同意 |
| `tests/scenarios/test_structured_wage_claims_cli.py::test_allowance_rejects_welfare_misclassification_without_write` | 津贴性质边界 | revise | 新工资参考 | 由模型区分法定津贴、福利和报销 |
| `tests/scenarios/test_variable_remuneration_claims_cli.py::test_project_bonus_rejects_exact_amount_when_acceptance_or_approval_failed` | 项目奖金条件 | preserve | 新工资参考#收入性质与条件 | 验收/审批条件保留 |
| `tests/scenarios/test_variable_remuneration_claims_cli.py::test_performance_bonus_rejects_exact_amount_without_publication_or_passing_result` | 绩效奖金条件 | preserve | 同上 | 公示/考核条件保留 |
| `tests/scenarios/test_variable_remuneration_claims_cli.py::test_each_type_has_sufficient_missing_and_conflicting_public_cli_scenarios` | 五类收入三态样本 | revise | 新工资参考#四类行为样本 | 改为模型公共接缝，不固定 CLI |
| `tests/scenarios/test_variable_remuneration_claims_cli.py::test_sales_trigger_variants_never_treat_performance_as_automatically_due` | 提成触发条件 | preserve | 新工资参考#收入性质与条件 | 业绩发生不自动到期 |
| `tests/scenarios/test_variable_remuneration_claims_cli.py::test_project_bonus_keeps_approval_contribution_and_partial_payment_scenarios_separate` | 项目奖金冲突 | preserve | 新工资参考 | 保留个人贡献、审批和部分支付分开 |
| `tests/scenarios/test_variable_remuneration_claims_cli.py::test_thirteenth_salary_has_stable_principal_and_wage_component_references` | 十三薪双重作用 | preserve | 新工资参考#交叉主张与高风险边界 | 本金与工资构成分开引用 |
| `tests/scenarios/test_variable_remuneration_claims_cli.py::test_multiple_legal_settlement_schemes_require_comparison_and_favor_higher_result` | 合法方案比较 | revise | 新工资参考#方案比较与金额接缝 | 先审查合法性，再推荐 primary |
| `tests/scenarios/test_variable_remuneration_claims_cli.py::test_same_income_cannot_be_claimed_under_two_names` | 重复收入主张 | preserve | 新工资参考#收入性质与条件 | 同一收入不得重复请求 |
| `tests/scenarios/test_variable_remuneration_claims_cli.py::test_same_name_but_distinct_income_natures_remain_separate` | 同名异质收入 | preserve | 同上 | 不因名称自动合并 |
| `tests/scenarios/test_variable_remuneration_claims_cli.py::test_prompt_injection_is_rejected_without_analysis_write` | 提示注入安全 | revise | 核心 Skill 用户可见安全边界 | 拒绝污染模型分析，不复制旧 CLI 拒绝形状 |
| `tests/scenarios/test_variable_remuneration_claims_cli.py::test_historical_performance_and_annual_bonus_names_remain_accepted` | 历史名称兼容 | revise | 新工资参考 | 保留业务语义，不保留旧载荷 |
| `tests/static/test_skill_structure.py:旧 PublicTurn/target/CLI 断言` | 旧结构测试 | obsolete | 现有跳过记录与新静态契约 | 按工单要求保留文件，未来统一收缩 |
| `annual-leave-pay` 旧服务、参考和场景 | 后续专项知识 | revise | 05 号工单；`references/model-led/annual-leave-pay.md` | 由 05 号工单完成模型参考、公式迁移和行为接缝；旧执行层仍按本工单非目标保留 |
| `forced-termination` 旧服务、参考和场景 | 后续专项知识 | revise | `references/model-led/forced-termination.md` 与 06 号 Scenario | 已移交 06 号模型参考；旧执行层和旧门禁仍待统一收缩 |
| `overtime-pay` 旧服务、参考和场景 | 后续专项知识 | revise | 05 号工单；`references/model-led/overtime-pay.md` | 由 05 号工单完成模型参考、公式迁移和行为接缝；旧执行层仍按本工单非目标保留 |
| `paid-leave-routing` 旧服务、参考和场景 | 后续专项知识 | revise | 05 号工单；`references/model-led/paid-leave-routing.md` | 由 05 号工单完成模型参考、路由迁移和行为接缝；旧执行层仍按本工单非目标保留 |
| `unlawful-termination` 旧服务、参考和场景 | 后续专项知识 | revise | `references/model-led/unlawful-termination.md` 与 06 号 Scenario | 已移交 06 号模型参考；旧执行层和旧门禁仍待统一收缩 |
| `unsigned-contract-double-wage` 旧服务、参考和场景 | 后续专项知识 | revise | 05 号工单；`references/model-led/unsigned-contract-double-wage.md` | 由 05 号工单完成模型参考、公式迁移和行为接缝；旧执行层仍按本工单非目标保留 |
## 05 号工单：工时、休假与未签合同双倍工资
| 来源/分支/场景 | 类型 | 处置 | 新权威位置或证据 | 说明 |
| --- | --- | --- | --- | --- |
| `scripts/overtime_pay/service.py:OVERTIME_TYPES`（工作日/休息日/法定休假日） | 法律输入分支 | preserve | `references/model-led/overtime-pay.md#金额与 amount.calculate` | 三类主张和 1.5/2/3 倍数语义迁入模型参考；倍数由模型显式输入 |
| `scripts/overtime_pay/service.py:_validate_overtime_claim` 的倍数与数量单位校验 | 纯数学边界 | calculator | `amount.calculate@overtime_pay` | 小时/日单位配对、Decimal、分项舍入和封顶由公共计算器校验 |
| `scripts/overtime_pay/service.py:work_schedule` 标准/综合/不定时/未知分支 | 工时制度分支 | revise | `references/model-led/overtime-pay.md#能力边界` | 保留特殊工时的条件化与动态核验，不由脚本作法律结论 |
| `scripts/overtime_pay/service.py:rest_day_compensation_status` 补休分支 | 竞合分支 | preserve | 加班参考#交叉影响与重复获偿 | 已补休排除休息日未补休金额；法定休假日不得以补休替代 |
| `scripts/overtime_pay/service.py` 缺失/冲突/exact/scenarios/placeholder 分支 | 分析状态分支 | revise | 加班参考#四类最小行为样本 | 保留业务状态，改为模型条件情景和用户解释 |
| `scripts/overtime_pay/knowledge.py` | 规则加载器 | revise | 加班参考#全国基线、上海增强与核验元数据 | 只提取规则和来源元数据，模型按需读取；不保留运行期分析入口 |
| `references/legal/overtime-rules.v1.json` 及来源登记 | 法律来源 | preserve | 加班参考#全国基线、上海增强与核验元数据 | 全国基线保留；地方、节假日和特殊工时按触发条件动态核验 |
| `references/overtime-pay.md` | 旧领域参考 | revise | `references/model-led/overtime-pay.md` | 移除旧 CLI/执行轨迹，补齐四类样本、交叉和工具接缝 |
| `tests/scenarios/test_overtime_pay.py::test_complete_virtual_case_calculates_three_overtime_types` | 成立场景 | preserve | `tests/scenarios/test_model_led_working_time_leave_double_wage.py` | 三类加班及分项算术意图保留，不固定旧档案输出 |
| `tests/scenarios/test_overtime_pay.py::test_conflicting_work_time_evidence_produces_scenarios_without_total` | 冲突场景 | preserve | 加班参考#四类最小行为样本 | 冲突分情景且不合计 |
| `tests/scenarios/test_overtime_pay.py::test_missing_wage_basis_produces_placeholder_without_exact_amount` | 信息不足场景 | preserve | 加班参考#触发、最低必要事实与停止条件 | 基数缺失使用占位 |
| `tests/scenarios/test_overtime_pay.py::test_conflicting_work_time_cannot_be_submitted_as_exact` | 负向场景 | revise | 新自然语言 Scenario | 由模型保持条件化，不固定旧错误文本 |
| `tests/scenarios/test_overtime_pay.py::test_unconfirmed_paid_amount_cannot_be_submitted_as_exact` | 负向场景 | revise | 加班参考#主张分析卡与方案比较 | 已付金额缺失不伪造精确金额 |
| `tests/scenarios/test_overtime_pay.py::test_special_work_schedule_requires_dynamic_verification` | 动态核验场景 | preserve | 加班参考#动态核验与降级 | 核验失败降低结论但不阻断一般分析 |
| `scripts/paid_leave_routing/service.py:LEAVE_TYPES` 与 `ROUTE_LABELS` | 路由分支 | preserve | `references/model-led/paid-leave-routing.md#能力边界` | 六类假种和八类路由语义保留 |
| `scripts/paid_leave_routing/service.py:QUESTION_ORDER` | 追问编排 | revise | 带薪假参考#触发、最低必要事实与停止条件 | 由模型选择单一最高价值问题，不保留固定问卷 |
| `scripts/paid_leave_routing/service.py:FORBIDDEN_CASH_*` | 重复/误路由边界 | preserve | 带薪假参考#能力边界与交叉影响 | 不把全部未休假统一折现 |
| `scripts/paid_leave_routing/service.py:_verification` 的 verified/unavailable/current/expired/conflicting | 动态核验分支 | revise | 带薪假参考#动态核验与降级 | 地区和待遇来源由模型按现行规则核验 |
| `scripts/paid_leave_routing/service.py:SOURCE_IDS_BY_TYPE`、`LOCAL_SOURCE_REGIONS` | 地区来源分支 | preserve | 带薪假参考#全国基线、上海增强与核验元数据 | 上海/北京来源不得交叉支持 |
| `scripts/paid_leave_routing/service.py:_components` 的产假待遇拆分 | 交叉路由分支 | preserve | 带薪假参考#交叉影响与重复获偿 | 休假权、生育津贴、单位工资差额分别识别 |
| `scripts/paid_leave_routing/service.py:_write_scan_status` 的固定状态迁移 | 旧状态门禁 | obsolete | 新 Skill 权益扫描参考与档案提交器 | 不恢复专项路由状态机；模型写回自然语言分析 |
| `scripts/paid_leave_routing/knowledge.py` | 规则加载器 | revise | 带薪假参考#全国基线、上海增强与核验元数据 | 保留来源和地区元数据；模型判断适用性 |
| `references/legal/paid-leave-routing-rules.v1.json` 及来源登记 | 法律来源 | preserve | 带薪假参考#全国基线、上海增强与核验元数据 | 全国基线和上海/北京来源线索保留 |
| `references/paid-leave-routing.md` | 旧领域参考 | revise | `references/model-led/paid-leave-routing.md` | 路由知识迁移为模型参考，不保留精确金额/CLI入口 |
| `tests/scenarios/test_other_paid_leave_routing.py::test_unregistered_region_cannot_claim_verified_with_national_source_only` | 地区核验场景 | preserve | 带薪假参考#动态核验与降级 | 未登记地区不冒充已核验 |
| `tests/scenarios/test_other_paid_leave_routing.py::test_unavailable_non_registry_region_downgrades_to_professional_review` | 核验失败场景 | preserve | 带薪假参考#四类最小行为样本 | 一般分析继续、路径降级 |
| `tests/scenarios/test_other_paid_leave_routing.py::test_region_name_containing_shanghai_does_not_match_shanghai_source` | 地区精确匹配场景 | preserve | 带薪假参考#全国基线、上海增强与核验元数据 | 地区名称不能模糊匹配 |
| `tests/scenarios/test_other_paid_leave_routing.py::test_beijing_parental_leave_rejects_shanghai_local_source_without_write` | 来源错配场景 | revise | 同上 | 改为模型记录来源适用性，不产生第二事实源 |
| `tests/scenarios/test_other_paid_leave_routing.py::test_next_question_returns_only_highest_value_missing_fact` | 单问场景 | revise | 新自然语言 Scenario | 保留单问意图，删除固定问题顺序断言 |
| `tests/scenarios/test_other_paid_leave_routing.py::test_shanghai_marriage_leave_routes_entitlement_and_updates_scan` | 成立路由场景 | preserve | 带薪假参考#四类最小行为样本 | 路由与权益范围意图保留 |
| `tests/scenarios/test_other_paid_leave_routing.py::test_beijing_parental_leave_uses_local_rule_and_wage_difference_route` | 地方差额场景 | preserve | 带薪假参考#金额与 amount.calculate | 明确差额后才能计算 |
| `tests/scenarios/test_other_paid_leave_routing.py::test_maternity_benefit_and_employer_wage_difference_are_separate_cross_routes` | 产假交叉场景 | preserve | 带薪假参考#交叉影响与重复获偿 | 待遇组件不重复获偿 |
| `tests/scenarios/test_other_paid_leave_routing.py::test_unverifiable_sick_leave_downgrades_without_precise_amount` | 信息不足场景 | preserve | 带薪假参考#动态核验与降级 | 不生成伪精确金额 |
| `tests/scenarios/test_other_paid_leave_routing.py::test_all_untaken_leave_cashout_request_is_blocked_without_mutation` | 误路由负向场景 | revise | 带薪假参考#能力边界 | 由模型解释假种差异，不保留旧 CLI 拒绝形状 |
| `scripts/annual_leave_pay/service.py` 的资格、档位、排除、折算、时效和 judgment 分支 | 法律分析分支 | preserve | `references/model-led/annual-leave-pay.md` | 业务语义全部迁入领域参考 |
| `scripts/annual_leave_pay/service.py:PER-NNN` 逐期间和 exact/scenarios/placeholder 分支 | 期间/状态分支 | revise | 年休假参考#金额与 amount.calculate | 逐期间意图保留；模型选择分段，工具不维护状态机 |
| `scripts/annual_leave_pay/amount_confirmation.py` 的 12 个月、5/10/15 天、365 向下取整、21.75、3/1/2 倍数 | 纯数学边界 | calculator | `amount.calculate@annual_leave_pay` | 除数、分段、零值、精度、封顶和舍入迁入公共计算器 |
| `scripts/annual_leave_pay/amount_confirmation.py` 的事实/证据绑定和快照 | 事实确认边界 | revise | 年休假参考#事实、证据与分析假设 | 由模型和唯一档案表达，不保存私有金额载荷 |
| `scripts/annual_leave_pay/knowledge.py` | 规则加载器 | revise | 年休假参考#全国基线、上海增强与核验元数据 | 保留规则和主题来源元数据，不保留专项执行入口 |
| `references/legal/annual-leave-pay-rules.v1.json` 及主题来源分组 | 法律来源 | preserve | 年休假参考#全国基线、上海增强与核验元数据 | 资格/计算、时效、上海口径来源分别保留 |
| `references/annual-leave-pay.md` | 旧领域参考 | revise | `references/model-led/annual-leave-pay.md` | 完成模型主路径和算术接缝迁移 |
| `tests/scenarios/test_annual_leave_pay.py::test_cross_year_exact_case_uses_semantic_snapshot_and_period_formulas` | 跨年成立场景 | preserve | 新金额 Scenario | 跨年分段和公式意图保留 |
| `tests/scenarios/test_annual_leave_pay.py::test_exact_snapshot_cross_checks_service_tier_and_departure_proration` | 档位/折算场景 | preserve | 年休假参考#触发、最低必要事实与停止条件 | 事实语义保留，取消私有快照依赖 |
| `tests/scenarios/test_annual_leave_pay.py::test_excluded_arranged_and_written_waiver_periods_are_other_conclusions` | 排除场景 | preserve | 年休假参考#主张分析卡与方案比较 | 其他结论不生成精确金额 |
| `tests/scenarios/test_annual_leave_pay.py::test_missing_eligibility_period_basis_taken_and_paid_stay_placeholders` | 信息不足场景 | preserve | 年休假参考#四类最小行为样本 | 逐期间占位 |
| `tests/scenarios/test_annual_leave_pay.py::test_taken_days_and_wage_basis_conflicts_produce_scenarios_without_total` | 冲突场景 | preserve | 年休假参考#金额与 amount.calculate | 情景不合计 |
| `tests/scenarios/test_annual_leave_pay.py::test_exact_amount_rejects_snapshot_drift_and_reused_semantic_facts` | 输入一致性场景 | revise | 唯一档案与公共工具契约 | 保留事实不漂移意图，不保留旧载荷 |
| `tests/scenarios/test_annual_leave_pay.py::test_unavailable_verification_forces_placeholder_and_downgrades_judgment` | 动态核验降级场景 | preserve | 年休假参考#动态核验与降级 | candidate/分析继续 |
| `tests/scenarios/test_annual_leave_pay.py::test_prohibited_certainty_language_is_rejected_before_persistence` | 安全表达场景 | revise | 核心 Skill 用户可见边界 | 保留不承诺结果语义 |
| `tests/scenarios/test_annual_leave_pay.py::test_snapshot_can_cover_only_exact_period_when_earlier_period_is_missing` | 分段信息不足场景 | preserve | 年休假参考#金额与 amount.calculate | 只有确认期间精算 |
| `tests/scenarios/test_annual_leave_pay.py::test_cumulative_service_under_one_year_cannot_enter_exact_tier` | 资格负向场景 | preserve | 年休假参考#能力边界 | 不把不足一年纳入精算档位 |
| `tests/scenarios/test_annual_leave_pay.py::test_evidence_matrix_requires_structured_coverage_for_each_annual_leave_purpose` | 证据覆盖场景 | revise | 年休假参考#事实、证据与分析假设 | 证明目的进入模型分析卡 |
| `tests/scenarios/test_annual_leave_pay.py::test_verified_topics_must_be_backed_by_registered_official_sources` | 法源场景 | preserve | 年休假参考#全国基线、上海增强与核验元数据 | 主题来源对应关系保留 |
| `tests/scenarios/test_annual_leave_pay.py::test_evidence_coverage_rejects_id_with_wrong_semantic_role` | 证据角色负向场景 | revise | 同上 | 不固定旧 JSON path |
| `tests/scenarios/test_annual_leave_pay.py::test_written_waiver_requires_explicit_written_waiver_evidence_role` | 书面放弃负向场景 | preserve | 年休假参考#事实、证据与分析假设 | 无书面证据不得认定放弃 |
| `scripts/unsigned_contract_double_wage/service.py` 的 exclusion/limitation/status/mode 分支 | 法律分析分支 | preserve | `references/model-led/unsigned-contract-double-wage.md` | 排除、时效、期间和判断语义迁入 |
| `scripts/unsigned_contract_double_wage/service.py` 的满一年上限、月/工作日单位、倍数 1 | 纯数学边界 | calculator | `amount.calculate@unsigned_contract_double_wage` | 单位、分段、封顶、零值、精度和舍入由公共工具执行；成立期间由模型判断 |
| `scripts/unsigned_contract_double_wage/service.py:_verification_inputs` | 动态核验分支 | revise | 未签合同参考#动态核验与降级 | 履行地/申请日期/时效由模型按需核验 |
| `scripts/unsigned_contract_double_wage/knowledge.py` | 规则加载器 | revise | 未签合同参考#全国基线、上海增强与核验元数据 | 保留规则和来源元数据，不保留专项执行入口 |
| `references/legal/unsigned-contract-double-wage-rules.v1.json` 及来源登记 | 法律来源 | preserve | 未签合同参考#全国基线、上海增强与核验元数据 | 全国稳定规则与动态地方口径分层保留 |
| `references/unsigned-contract-double-wage.md` | 旧领域参考 | revise | `references/model-led/unsigned-contract-double-wage.md` | 补齐四类样本、交叉和模型接缝 |
| `tests/scenarios/test_unsigned_contract_double_wage.py::test_supported_virtual_case_uses_confirmed_period_and_wage_basis` | 成立场景 | preserve | 新自然语言交叉 Scenario | 成立期间、基数、证据和金额意图保留 |
| `tests/scenarios/test_unsigned_contract_double_wage.py::test_equivalent_written_agreement_is_excluded_without_exact_amount` | 等效协议排除场景 | preserve | 未签合同参考#能力边界 | 排除不精算 |
| `tests/scenarios/test_unsigned_contract_double_wage.py::test_missing_period_basis_and_limitation_stay_information_insufficient` | 信息不足场景 | preserve | 未签合同参考#四类最小行为样本 | 占位且降低判断 |
| `tests/scenarios/test_unsigned_contract_double_wage.py::test_exact_amount_rejects_values_outside_confirmed_snapshot` | 输入一致性场景 | revise | 唯一档案与公共工具契约 | 保留不漂移意图，不保留私有 snapshot |
| `tests/scenarios/test_unsigned_contract_double_wage.py::test_exact_amount_requires_completed_limitation_verification_inputs` | 动态核验负向场景 | preserve | 未签合同参考#动态核验与降级 | 核验不足不得精确 |
| `tests/scenarios/test_unsigned_contract_double_wage.py::test_exclusion_or_unresolved_limitation_overrides_conflict_scenarios` | 竞合负向场景 | preserve | 未签合同参考#主张分析卡与方案比较 | 排除/时效优先于高额情景 |
| `scripts/cli_orchestration/registry.py` 的四项目标、旧 `cli.py` 和旧专项服务调用 | 旧运行层 | obsolete | 四份模型参考、amount.calculate 和自然语言 Scenario | 旧文件和旧测试不删除，本工单不新增兼容入口 |
| `tests/scenarios/test_model_led_working_time_leave_double_wage.py` | 新公共 Scenario | preserve | 自然语言→档案→分析/计算闭环 | 观察共享工资基数、劳动关系前提、休假路由和重复获偿排除 |
## 06 号工单迁移追踪表
| 来源/分支/场景 | 类型 | 处置 | 新权威位置或证据 | 说明 |
| --- | --- | --- | --- | --- |
| `tests/scenarios/test_model_led_termination_analysis_and_confirmation.py` | 新模型行为 Scenario | preserve | 本文件 06 号追踪表与三项工具接缝 | 覆盖两域四类事实样本、路由、confirmation、金额竞合和用户可见边界 |
| `scripts/forced_termination/service.py:analyze_forced_termination_case` | 运行层 | obsolete | 06 号模型参考与 Scenario | 不保留专项分析执行器；只迁移法律语义和历史回归 |
| `scripts/unlawful_termination/service.py:analyze_unlawful_termination_case` | 运行层 | obsolete | 06 号模型参考与 Scenario | 不保留专项分析执行器；只迁移法律语义和历史回归 |
| `scripts/forced_termination/service.py:_termination_action` | 解除事实分支 | revise | 解除分析与轻量高风险确认#三类路由 | 主体、状态、日期、送达改由模型自然语言分流 |
| `scripts/forced_termination/service.py:_validate_claim` | 主张校验分支 | revise | 被迫解除参考#主张分析卡与事实状态 | 每项理由独立分析，不保留旧 CLI 校验形状 |
| `scripts/forced_termination/service.py:_render_claim_context` | 用户/档案表达 | revise | 被迫解除参考#证据、抗辩与交叉影响 | 保留可解释字段，用户表达由模型负责 |
| `scripts/forced_termination/service.py:_validate_gate_prerequisites` | 重复高风险门禁 | obsolete | 解除分析与轻量高风险确认#唯一档案接缝 | 不迁移 gate/receipt 状态机 |
| `scripts/unlawful_termination/service.py:_termination_event` | 解除事件分支 | revise | 违法解除参考#触发、最低事实与停止追问 | 四项事实分别保持主体、日期、理由和程序 |
| `scripts/unlawful_termination/service.py:_validate_claim` | 救济校验分支 | revise | 违法解除参考#主张分析卡、救济竞合 | 赔偿金与继续履行由模型比较且互斥 |
| `scripts/unlawful_termination/service.py:_render_claim_context` | 用户/档案表达 | revise | 违法解除参考#证据、抗辩与金额口径 | 保留事实/证据语义，不保留旧私有载荷 |
| `scripts/forced_termination/notice_authority.py:canonical_notice_legal_basis` | 通知文书分支 | obsolete | 06 非目标；08 号工单 | 本工单不生成或规范化解除通知书 |
| `scripts/forced_termination/cli.py` | 私有 CLI | obsolete | 核心 Skill + CaseArchive/amount.calculate | 不把 target、私有载荷或命令行作为模型路径 |
| `scripts/unlawful_termination/cli.py` | 私有 CLI | obsolete | 核心 Skill + CaseArchive/amount.calculate | 不把 target、私有载荷或命令行作为模型路径 |
| `scripts/forced_termination/knowledge.py` | 规则加载 | obsolete | 被迫解除模型参考 + 法律核验 | 不保留专项 analyzer 的运行期规则加载 |
| `references/legal/forced-termination-rules.v1.json` | 规则来源 | revise | 被迫解除模型参考 + 法律基线/动态核验 | 保留法源语义，退出旧分析器输入 |
| `scripts/unlawful_termination/knowledge.py` | 规则加载 | obsolete | 违法解除模型参考 + 法律核验 | 不保留专项 analyzer 的运行期规则加载 |
| `references/legal/unlawful-termination-rules.v1.json` | 规则来源 | revise | 违法解除模型参考 + 法律基线/动态核验 | 保留赔偿与继续履行边界 |
| `references/forced-termination.md` | 旧领域参考 | revise | `references/model-led/forced-termination.md` | 旧 CLI、PublicTurn 和六步 gate 只作迁移素材 |
| `references/topics/forced-termination.md` | 旧主题参考 | obsolete | `references/model-led/forced-termination.md` | 只验证旧阶段和旧运行协议，不进入新主路径 |
| `references/unlawful-termination.md` | 旧领域参考 | revise | `references/model-led/unlawful-termination.md` | 旧 CLI 和私有载荷不进入新主路径 |
| `references/documents/forced-termination-notice.md` | 解除通知文书 | obsolete | 08 号工单 | 06 只提供分析与 confirmation，不生成通知书 |
| `references/analysis-examples/forced-termination.json` | 私有分析样例 | obsolete | 06 模型参考与 Scenario | 不迁移 analyzer payload 或逐轮 JSON |
| `references/case-pipeline-examples/forced-termination-exact.json` | 私有案件包样例 | obsolete | 06 模型参考 + amount.calculate | 不把旧案件包作为模型主路径 |
| `references/case-pipeline-examples/combined-wage-thirteenth-forced-termination/forced-termination-exact.json` | 组合私有样例 | obsolete | 06 模型参考 + amount.calculate | 只迁移工资构成和竞合业务意图 |
| `references/risk-gates.md` 的解除风险提示与替代方案 | 风险语义 | revise | `termination-analysis-and-confirmation.md` | 保留用户可理解的风险/替代方案，不迁移 gate 顺序或 receipt 状态 |
| `tests/scenarios/test_forced_termination.py::test_analysis_reference_validation_scans_every_composed_section` | 迁移完整性 | preserve | 06 号领域参考与迁移追踪 | 保留各分析区块均需可追溯的业务意图 |
| `tests/scenarios/test_forced_termination.py::test_section_transform_failure_leaves_archive_bytes_unchanged` | 档案事务 | preserve | `CaseArchive.commit` 与档案测试 | 保留原子失败不污染唯一档案 |
| `tests/scenarios/test_forced_termination.py::test_wage_and_forced_termination_compose_equivalently_in_both_orders` | 跨主张 | preserve | 06 号 Scenario 的共享证据/金额边界 | 不固定旧分析执行顺序 |
| `tests/scenarios/test_forced_termination.py::test_multiple_reasons_remain_separate_and_traceable_through_public_cli` | 理由拆分 | preserve | 被迫解除参考#主张分析卡 | 每项理由独立引用事实、证据和抗辩 |
| `tests/scenarios/test_forced_termination.py::test_exact_compensation_uses_only_semantic_confirmation_snapshot` | 金额确认 | calculator | `economic_compensation` + CaseArchive confirmation | 只保留已确认基数和月数的算术意图 |
| `tests/scenarios/test_forced_termination.py::test_capped_compensation_keeps_actual_wage_and_applied_basis_distinct` | 工资基数 | calculator | 被迫解除参考#金额口径 | 实际平均工资与适用/封顶基数不混同 |
| `tests/scenarios/test_forced_termination.py::test_cap_state_conflicts_are_rejected_before_analysis_write` | 金额冲突 | revise | 被迫解除参考#四类最小行为样本 | 冲突保留条件情景，不写精确合计 |
| `tests/scenarios/test_forced_termination.py::test_missing_or_conflicting_amount_inputs_never_produce_exact_total` | 输入不足 | calculator | amount.calculate placeholder/互斥情景 | 缺口不伪造精确金额 |
| `tests/scenarios/test_forced_termination.py::test_gate_is_ordered_and_incomplete_gate_blocks_external_final` | 旧高风险 gate | obsolete | 解除分析与轻量高风险确认 | 只保留风险说明和确认事实，不迁移 gate 顺序 |
| `tests/scenarios/test_forced_termination.py::test_gate_cannot_self_report_completion_when_prerequisites_are_unresolved` | 旧高风险 gate | obsolete | 同上 | 不迁移脚本自报完成门禁 |
| `tests/scenarios/test_forced_termination.py::test_last_three_gate_steps_require_structured_semantic_evidence` | 旧高风险 gate | obsolete | 同上 | 不迁移逐步 gate JSON |
| `tests/scenarios/test_forced_termination.py::test_internal_amount_review_cannot_pass_without_real_exact_snapshot` | 旧金额门禁 | obsolete | amount.calculate + 模型条件情景 | 计算器不判断法律适用 |
| `tests/scenarios/test_forced_termination.py::test_completed_gate_records_release_but_does_not_generate_ticket_13_notice` | 旧文书耦合 | obsolete | 非目标：不生成解除通知书 | 旧通知生成路径由 08 号工单处理 |
| `tests/scenarios/test_forced_termination.py::test_historical_termination_claim_does_not_require_future_action_gate` | 历史索赔路由 | revise | 解除分析与轻量高风险确认#三类路由 | 历史分析不触发新动作确认 |
| `tests/scenarios/test_forced_termination.py::test_other_path_and_excluded_reasons_do_not_become_exact_compensation` | 反向路由 | preserve | 被迫解除参考#四类样本 | 其他路径/排除理由不生成精确补偿 |
| `tests/scenarios/test_forced_termination.py::test_dynamic_official_sources_limitation_stage_delivery_and_evidence_are_recorded` | 动态核验 | revise | 法律核验参考 + 被迫解除参考 | 只记录最小来源结论和影响 |
| `tests/scenarios/test_forced_termination.py::test_prohibited_certainty_and_employer_termination_path_are_rejected` | 安全边界 | preserve | 被迫解除参考#能力目的 | 不把用人单位解除改写为被迫解除 |
| `tests/scenarios/test_forced_termination.py::test_each_reason_requires_nonempty_fact_evidence_defense_and_pending_links` | 理由证据 | preserve | 被迫解除参考#证据、抗辩与交叉影响 | 保留每项理由的证明目的和待核验项 |
| `tests/scenarios/test_unlawful_termination.py::test_exact_amount_rejects_unconfirmed_paid_and_unbound_snapshot_inputs` | 金额确认 | calculator | `unlawful_termination_compensation` + amount Scenario | 未确认输入不出精确赔偿 |
| `tests/scenarios/test_unlawful_termination.py::test_exact_amount_requires_semantic_snapshot_with_distinct_confirmed_facts` | 金额确认 | calculator | 违法解除参考#金额口径 | 保留基数、工龄和已付金额的独立引用 |
| `tests/scenarios/test_unlawful_termination.py::test_invalid_judgment_path_relief_combination_is_rejected` | 救济竞合 | revise | 违法解除参考#主张分析卡、救济竞合 | 赔偿金与继续履行不合并 |
| `tests/scenarios/test_unlawful_termination.py::test_continuation_status_is_part_of_relief_state_machine` | 旧状态机 | obsolete | 违法解除参考#主张分析卡、救济竞合 | 保留继续履行事实前提，不迁移状态机 |
| `tests/scenarios/test_unlawful_termination.py::test_prohibited_deterministic_language_is_rejected_before_persistence` | 安全边界 | preserve | 违法解除参考#事实状态 | 不使用确定违法/胜率承诺 |
| `tests/scenarios/test_unlawful_termination.py::test_exact_amount_requires_in_time_limitation_and_official_current_source` | 动态核验 | revise | 法律核验参考 + 违法解除参考 | 时效/来源不确定时只候选或占位 |
| `tests/scenarios/test_unlawful_termination.py::test_supported_compensation_uses_confirmed_wage_and_service_years` | 金额算术 | calculator | `unlawful_termination_compensation` | 只迁移明确基数和工龄的算术 |
| `tests/scenarios/test_unlawful_termination.py::test_continue_performance_records_prerequisites_without_compensation_total` | 继续履行 | preserve | 违法解除参考#救济竞合 | 继续履行不换算为赔偿金额 |
| `tests/scenarios/test_unlawful_termination.py::test_worker_resignation_is_routed_elsewhere_without_deciding_ticket_06` | 反向路由 | preserve | 被迫解除/违法解除参考 | 主体不符时分流，不替用户决定 |
| `tests/scenarios/test_unlawful_termination.py::test_missing_and_conflicting_inputs_remain_information_insufficient_placeholders` | 信息不足/冲突 | preserve | 违法解除参考#四类样本 | 缺失或冲突不编造金额与理由 |
| `tests/claim_analysis/test_compensation_wage_components_cli.py::test_verified_wage_composition_rejects_non_official_https_source` | 来源边界 | revise | 法律核验参考 + 06 Scenario | 来源可访问不等于已核验 |
| `tests/claim_analysis/test_compensation_wage_components_cli.py::test_verified_wage_composition_rejects_future_verification_date` | 来源边界 | revise | 06 confirmation source freshness | 未来/失配日期不形成有效确认依据 |
| `tests/claim_analysis/test_compensation_wage_components_cli.py::test_forced_termination_exact_includes_thirteenth_salary_with_audit_trail` | 经济补偿算术 | calculator | `economic_compensation` + 06 Scenario | 只迁移工资构成和算术，不迁移旧载荷 |
| `tests/claim_analysis/test_compensation_wage_components_cli.py::test_unlawful_termination_exact_excludes_unsatisfied_thirteenth_salary` | 赔偿金算术 | calculator | `unlawful_termination_compensation` + 06 Scenario | 未满足收入条件不进入基数 |
| `tests/claim_analysis/test_compensation_wage_components_cli.py::test_both_remedies_emit_controlled_scenarios_and_favorable_recommendation` | 互斥方案 | revise | 违法解除参考#救济竞合 | 模型比较，计算器不推荐最大值 |
| `tests/claim_analysis/test_compensation_wage_components_cli.py::test_cap_keeps_actual_average_applied_basis_and_total_cap_separate` | 工资基数 | calculator | 解除参考#金额口径 | 实际、适用和封顶基数分开 |
| `tests/claim_analysis/test_compensation_wage_components_cli.py::test_missing_period_and_cross_remedy_reuse_are_rejected_without_write` | 输入/竞合 | revise | 06 Scenario + CaseArchive | 缺口或跨救济复用不写精确结果 |
| `tests/documents/test_forced_termination_notice_cli.py::test_explicitly_withheld_party_fields_force_placeholder_draft` | 解除通知文书 | obsolete | 08 号工单 | 06 不生成通知书 |
| `tests/documents/test_forced_termination_notice_cli.py::test_explicitly_unverified_evidence_cannot_support_notice_reason` | 解除通知文书 | obsolete | 08 号工单 | 06 只迁移证据风险，不消费文书门禁 |
| `tests/documents/test_forced_termination_notice_cli.py::test_notice_externalizes_internal_consultation_voice` | 解除通知文书 | obsolete | 08 号工单 | 正式正文边界由后续工单处理 |
| `tests/documents/test_forced_termination_notice_cli.py::test_model_invented_party_fields_cannot_be_promoted_to_candidate` | 解除通知文书 | obsolete | 08 号工单 | 不在 06 提前实现候选文书 |
| `tests/documents/test_forced_termination_notice_cli.py::test_confirmed_ticket_06_record_generates_isolated_notice_candidate` | 旧 confirmation 消费 | obsolete | 08 号工单 | 06 新 confirmation 由后续文书工具消费 |
| `tests/documents/test_forced_termination_notice_cli.py::test_incomplete_six_step_gate_can_only_generate_preparation_draft` | 旧高风险 gate | obsolete | 08 号工单 + 统一收缩 | 不迁移六步 gate 状态机 |
| `tests/documents/test_forced_termination_notice_cli.py::test_structurally_incomplete_gate_record_can_only_generate_preparation_draft` | 旧高风险 gate | obsolete | 08 号工单 + 统一收缩 | 不迁移 gate JSON |
| `tests/documents/test_forced_termination_notice_cli.py::test_ticket_06_gate_record_drift_after_snapshot_blocks_generation` | 旧 confirmation 漂移 | obsolete | 08 号工单 | 新档案 revision 语义由文书窄门禁处理 |
| `tests/documents/test_forced_termination_notice_cli.py::test_user_statement_cannot_be_used_as_notice_reason_fact` | 解除通知文书 | obsolete | 08 号工单 | 06 保留用户陈述/证据支持事实区分 |
| `tests/documents/test_forced_termination_notice_cli.py::test_user_statement_cannot_be_used_as_notice_field` | 解除通知文书 | obsolete | 08 号工单 | 不在 06 生成正式字段 |
| `tests/documents/test_forced_termination_notice_cli.py::test_notice_legal_basis_must_match_current_official_rule_mapping` | 解除通知文书 | obsolete | 08 号工单 + 法律核验 | 06 只记录动态核验影响 |
| `tests/documents/test_forced_termination_notice_cli.py::test_reason_title_cannot_leak_internal_id_or_risk_score` | 文书泄漏 | obsolete | 08 号工单 | 不在 06 生成正文 |
| `tests/documents/test_forced_termination_notice_cli.py::test_missing_critical_notice_field_stays_non_promotable_draft` | 解除通知文书 | obsolete | 08 号工单 | 06 只提供候选分析，不晋升文书 |
| `tests/core_workflow/test_cli.py::test_historical_forced_termination_claim_skips_future_action_receipt_and_replays` | 路由场景 | revise | 06 号 Scenario | 只保留历史索赔不新增动作的用户语义 |
| `tests/core_workflow/test_cli.py::test_planned_sendable_termination_notice_stops_without_current_receipt` | 路由场景 | revise | 06 号 Scenario | 计划动作先说明风险并等待明确选择，不迁移 receipt |
| `tests/scenarios/test_unlawful_termination.py::test_worker_resignation_is_routed_elsewhere_without_deciding_ticket_06` | 反向路由 | preserve | 被迫解除/违法解除模型参考 | 劳动者主动辞职不得直接进入违法解除 |
| `tests/high_risk_action/test_semantic_confirmation.py::test_only_explicit_confirm_or_decline_is_accepted` | 用户选择 | preserve | 06 号 Scenario | 只观察明确选择与拒绝，不固定内部调用 |
| `tests/high_risk_action/test_semantic_confirmation.py::test_ambiguous_response_is_rejected_at_boundary` | 用户选择 | preserve | 06 号 Scenario | 歧义不能晋升为确认 |
| U-01 解除事实、证据和实务提示精度 | 专业复核 | unresolved | 最终法律专业复核清单 | 已知限制，不阻塞本工单或技术测试 |
## 08 号工单迁移追踪表
| 来源/分支/场景 | 类型 | 处置 | 新权威位置或证据 | 说明 |
| --- | --- | --- | --- | --- |
| references/model-led/demand-and-forced-termination-documents.md | 新领域参考 | preserve | 08 号模型参考与 SKILL.md 路由 | 模型负责目标分流、完整正文、风险说明和候选范围 |
| references/document-render.md 的 document.render 契约 | 公共文书接缝 | revise | scripts/documents/public.py | 同一工具新增两类文书，保留五类公开枚举和确定性发布边界 |
| scripts/documents/docx_builder.py:build_demand_letter | 旧文书生成器 | preserve | 新公共渲染器的共享 DOCX/OOXML 基础能力 | 旧构建器不删除、不作为 08 模型路径入口 |
| scripts/documents/docx_builder.py:build_forced_termination_notice | 旧文书生成器 | preserve | 新公共渲染器的共享 DOCX/OOXML 基础能力 | 旧构建器不删除、不消费旧 gate 或固定载荷 |
| references/documents/demand-letter.md 与旧催告函 CLI/快照路径 | 旧领域/运行层 | obsolete | 08 模型参考 + document.render Scenario | 仅保留迁移素材，不把旧阶段和默认话术作为新契约 |
| references/documents/forced-termination-notice.md 与旧解除通知 CLI/快照路径 | 旧领域/运行层 | obsolete | 06 confirmation + 08 模型参考 | 仅保留迁移素材，不把旧 gate、receipt 或私有 payload 作为新入口 |
| tests/documents/test_demand_letter_cli.py | 旧催告函固定载荷测试 | obsolete | tests/documents/test_model_led_demand_forced_render.py | 保留文件；新证据改测模型正文、绑定、候选/定稿和原子交付 |
| tests/documents/test_forced_termination_notice_cli.py | 旧解除通知阶段/gate 测试 | obsolete | tests/documents/test_model_led_demand_forced_render.py | 保留文件；旧六步阶段和确认快照断言不迁入新路径 |
| tests/documents/test_model_led_demand_forced_render.py | 新公共接缝测试 | preserve | DocumentRenderer + CaseArchive | 覆盖两类文书候选/定稿、占位符、绑定、冲突、泄漏、OOXML、幂等、warning 和原子失败边界 |
| tests/documents/model_led_demand_forced_fixtures.py | 新测试 fixture | preserve | 08 公共接缝与 Scenario 测试 | 独立构造档案、确认和现行依据，不从测试模块私有实现导入 |
| tests/scenarios/test_model_led_demand_forced_documents.py | 自然语言行为 Scenario | preserve | CaseArchive + DocumentRenderer | 覆盖历史索赔、计划动作、已发送送达不明和明确确认分流，不引入状态机 |
| tests/static/test_model_led_demand_forced_document_contract.py | 静态契约测试 | preserve | Skill、领域参考与唯一公共渲染器 | 防止新入口回退到旧 CLI、PublicTurn 或第二套文书工具 |
| scripts/documents/public.py 的 `PUBLIC_ENTRYPOINT`/`ARCHIVE_CONTRACT` 与 `DocumentRenderer.describe` 元数据 | 公共入口可发现性边界 | preserve | `document.render-v1` + 10 节 `CaseArchive` | 明确模型/WorkBuddy 唯一调用点；不增加第二个渲染接缝 |
| scripts/documents/cli.py 与 scripts/documents/service.py | 旧 14 节文书运行层 | obsolete | 08 模型参考、统一 renderer 参考与入口静态回归 | 保留供后续迁移/历史回归；显式标记非模型，不作为兼容包装器或正常入口 |
| 真实 WorkBuddy 旧 CLI 选择证据（留在仓库外） | 跨边界回归证据 | revise | 08 入口隔离回归 + public renderer 10 节档案闭环 | 真实会话曾从旧 CLI 进入 14 节读取器并失败；案件与 transcript 未复制或修改 |
