# `amount.calculate` 确定性计算器公共契约

这是模型调用的唯一金额工具契约。模型先完成法律适用、事实缺口、方案比较和公式选择；计算器只验证显式输入的类型、单位、日期范围、非负约束和数学关系，并执行 Decimal/ROUND_HALF_UP 算术。计算器不判断法律适用、不选择主张、不自动选择最大金额、不输出胜诉或最优结论，也不写案件文件。

## 公共金额工具

模型主路径的唯一 Python 公共接缝是 `scripts.amount_calculator.public.AmountCalculator.calculate`（或同模块的 `calculate`）；它接收本文件定义的 `amount.calculate-v1` 请求，并返回 `operation: "amount.calculate"` 的标准结果。模型或平台适配层不得把旧命令行当作这个公共接缝。

WorkBuddy 通过该公共接缝的窄 transport 调用，不使用 `python -c`或源码反射：

```text
python -B -X utf8 -m scripts.amount_calculator.runtime_cli --workspace "<workspace_root>" describe
python -B -X utf8 -m scripts.amount_calculator.runtime_cli --workspace "<workspace_root>" calculate --input "<workspace_root>/.arbibuddy/runtime-input/<nonce>.json"
```

短请求也可用 `--json`。`--input` 只接受 `.arbibuddy/runtime-input/` 的直接 JSON 子文件；transport 在计算前消费并删除该文件，不保留请求副本。失败时按返回的错误修正同一公开请求，不改试旧 CLI、内联 Python 或源码探查。


## 正式计算记录门禁

只有一次成功的 `amount.calculate-v1` 响应中 `ok=true` 的确定性结果，才能通过 `CaseArchive.commit` 形成正式 `CAL-*` 计算记录，或作为正式文书的金额绑定。模型心算、内嵌 Python `Decimal`、旧 CLI、临时脚本和未成功工具调用得到的数值不得写入正式计算区；它们最多只能作为普通语言中的明确待核实示例，不能冒充正式结果。工具调用失败时保留缺口并按错误契约恢复，不回退到手算或另一个入口。

供文书绑定的每个 `CAL-*` 记录保留一个明确结果字段，例如 `工资差额确定性计算结果：6000.00元`，并说明对应成功计算及分项/合计口径。一项记录只承载一个供文书引用的确定性结果；公式、基数和中间步骤仍可同时保留，但不作为最终金额的替代来源。多个分项或合计分别落档，正文每处数额按[文书契约](document-render.md)绑定。


## 请求

操作名为 `amount.calculate`，契约版本为 `amount.calculate-v1`。请求必须一次性提供：

| 字段 | 类型与要求 |
| --- | --- |
| `calculation_label` | 非空单行文本，供模型识别本次计算 |
| `formula_id` | 公式目录中的显式标识，如 `wage_difference`、`overtime_pay`、`annual_leave_pay`、`unsigned_contract_double_wage`、`variable_remuneration`、`wage_arrears_total`、`economic_compensation`、`unlawful_termination_compensation` |
| `formula_version` | 与公式目录匹配的版本，例如 `1.0.0` |
| `legal_basis_summary` | 模型选择的法律口径说明；计算器只回显，不作法律判断 |
| `source_refs` | 案情事实、证据或法律来源引用数组；计算器不读取或验证档案 |
| `aggregation_policy` | `single_scenario`、`sum_scenarios` 或 `mutually_exclusive`；由模型明确选择 |
| `scenarios` | 一个或多个条件情景；每个情景有 `scenario_id`、`assumptions`、`period`、`inputs` |
| `rounding_policy` | `money-independent-unit-half-up-v1`、`0.01`、`ROUND_HALF_UP` 及“独立分项先舍入再汇总” |

每个量纲输入是 `{ "value": "十进制定点字符串", "unit": "公开单位" }`。允许单位为：`yuan`、`yuan_per_month`、`yuan_per_day`、`yuan_per_hour`、`month`、`day`、`hour`、`ratio`、`count`。禁止 JSON 数字、二进制浮点、负值、未知单位和单位错配。

`period` 必须显式提供 `start_date`、`end_date` 和数学约定（如 `completed_months`、`calendar_days`、`hours`、`event_count`、`explicit_period`）。计算器只校验日期顺序，不从“欠薪”“奖金”“加班”或“年休假”等名称推断期间数量。模型选择的跨月、跨年或分段要在情景的 `line_items` 中分别列出。

## 数学与舍入

`wage_difference` 和 `variable_remuneration` 的每个独立分项使用：

```text
max(0, 基数 × 数量 × 倍数 − 期间已付金额)
```

解除相关的 `economic_compensation`（经济补偿）和 `unlawful_termination_compensation`（违法解除或终止赔偿金）使用同一纯数学形状，分别由模型选择适用工资基数、补偿/赔偿月数、倍数、已付金额、上限和情景。工具不判断解除主体、理由、日期、程序、救济竞合或用户选择；赔偿金与继续履行等互斥请求必须作为 `mutually_exclusive` 互斥情景分别返回，不能合并求和。

基数和数量的维度必须匹配，例如 `yuan_per_month × month`、`yuan_per_day × day`。`cap` 若提供，必须是模型明确选择的 `yuan` 输入；工具只执行 `min(显式结果, 显式上限)`，不判断上限是否有法律依据。`wage_arrears_total` 与 `independent_amount_total` 只汇总模型明确提供的独立 `yuan` 分项。

`overtime_pay` 使用相同的线性差额公式，但只接受 `yuan_per_hour × hour` 或 `yuan_per_day × day`；工作日、休息日和法定休假日的倍数由模型分别选择并作为 `ratio` 输入。`unsigned_contract_double_wage` 接受 `yuan_per_month × month` 或 `yuan_per_day × day`，计算器不判断劳动关系、成立期间或时效。

`annual_leave_pay` 使用逐期间日工资差额公式：

```text
max(0, (basis ÷ divisor) × quantity × multiplier − paid)
```

其中 `basis` 是模型确认的 `yuan_per_month`，`divisor` 是带 `count` 单位的显式日工资折算除数（通常为 `21.75`），`quantity` 是 `day`，`multiplier` 和 `paid` 仍必须带 `ratio`/`yuan` 单位。计算器只做除法和算术，不从公式名称推导 21.75、3、1 或 2，也不判断年休假资格、排除、已休天数或时效。

连续公式保留未舍入的 Decimal 中间值；每个独立分项在最终展示点按人民币分使用 `ROUND_HALF_UP` 舍入；情景小计使用已展示的分项相加。`mutually_exclusive` 情景分别返回小计，`grand_total` 为 `null`，不隐式求和或选最大值。只有模型明确提交 `sum_scenarios` 时才汇总多个情景。

## 成功响应

成功响应固定包含 `contract_version`、`operation`、`ok`、`result`、`errors`、`warnings`。`result` 至少包含：

- `calculation_id`（由规范化输入稳定派生）；
- `formula_id`、`formula_version`、`legal_basis_summary`、`source_refs`；
- `normalized_inputs`（单位和十进制输入回显）、`formula_display`；
- `line_items`（每项公式、未舍入结果、舍入后结果、显式上限信息）；
- `intermediate_trace`（中间值及舍入点）；
- `scenario_totals`、`grand_total`、`grand_total_status`；
- `rounding_policy`。

成功示例（省略不影响契约含义的第二个分项）：

```json
{
  "contract_version": "amount.calculate-v1",
  "operation": "amount.calculate",
  "ok": true,
  "result": {
    "calculation_id": "calc-稳定派生值",
    "formula_id": "wage_difference",
    "formula_version": "1.0.0",
    "legal_basis_summary": "模型选择按月工资差额口径；计算器只执行数学。",
    "source_refs": ["F-001", "E-001"],
    "normalized_inputs": {"units": ["month", "ratio", "yuan", "yuan_per_month"]},
    "formula_display": "max(0, basis × quantity × multiplier - paid)",
    "line_items": [{"scenario_id": "primary", "line_id": "2026-03", "result": "6000.00"}],
    "intermediate_trace": [{"scenario_id": "primary", "line_id": "2026-03", "raw_result": "6000", "rounded_result": "6000.00"}],
    "scenario_totals": [{"scenario_id": "primary", "total": "6000.00"}],
    "grand_total": "6000.00",
    "grand_total_status": "calculated",
    "rounding_policy": {"id": "money-independent-unit-half-up-v1", "mode": "ROUND_HALF_UP"}
  },
  "errors": [],
  "warnings": []
}
```

成功结果只存在于本次调用内。模型应把必要口径、假设和结果转换为可读 Markdown，通过 `CaseArchive.commit` 写入唯一 `案情档案.md`；不要把请求或响应 JSON 作为逐轮档案。

## 聚合失败响应与恢复

输入错误一次聚合返回，不因第一个错误停止。输入不足、单位错配和公式冲突都必须在同一响应中指出。每项错误至少有 `code`、准确 `path`、`message`、`expected`、`received` 和 `recoverable`。稳定错误至少包括：`unknown_formula`、`formula_version_mismatch`、`missing_input`、`unknown_field`、`invalid_text`、`invalid_object`、`invalid_decimal`、`negative_value`、`unit_mismatch`、`invalid_date_range`、`invalid_period_semantics`、`division_by_zero`、`rounding_policy_mismatch`、`constraint_conflict`、`duplicate_scenario`、`non_reproducible_result` 和 `installed_skill_modified`。安装完整性失败时 `recoverable=false`，不得处理输入或产生案件/临时文件副作用；原生 WorkBuddy 上传的便携标记问题应重新上传已验签 ZIP 并重启，受管安装回执/外部信任锚问题应重新执行受管安装或更新，不得要求原生上传生成受管回执。

例如负值、单位错配和另一情景缺少输入应在同一响应中同时指出：

```json
{
  "contract_version": "amount.calculate-v1",
  "operation": "amount.calculate",
  "ok": false,
  "errors": [
    {
      "code": "negative_value",
      "path": "scenarios[0].inputs.line_items[0].basis.value",
      "message": "计算输入不得为负值",
      "expected": "value >= 0",
      "received": "-1",
      "recoverable": true
    },
    {
      "code": "unit_mismatch",
      "path": "scenarios[0].inputs.line_items[0].quantity.unit",
      "message": "基数与数量的单位维度不匹配",
      "expected": ["yuan_per_month × month", "yuan_per_day × day"],
      "received": "yuan_per_month × day",
      "recoverable": true
    }
  ],
  "warnings": []
}
```

模型根据 `path` 一次修正请求后重试；不得把错误信息逐次猜字段，也不得把工具错误转写成法律结论。计算器失败时不产生档案写入或半成品。
