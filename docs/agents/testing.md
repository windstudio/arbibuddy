# 分层测试

验证依赖见根目录 `requirements-validation.txt`；真实模型Harness依赖另见 `requirements-agent-eval.txt`。WorkBuddy离线包另需精确匹配 `references/workbuddy-document-runtime.lock.json` 的受控wheelhouse与CPython 3.13 Windows amd64运行环境。缺环境要记录为缺测/环境边界，不能算PASS；当前原wheel的公开分发整改尚未完成。

在源码checkout中显式运行所需层次：

```text
python -B -X utf8 -m scripts.test_suites.cli fast --temp-root <源码外可写临时根> --wheelhouse <受控离线wheelhouse>
python -B -X utf8 -m scripts.test_suites.cli slow --temp-root <源码外可写临时根> --wheelhouse <受控离线wheelhouse>
```

各组独立进程与独立超时，组清单以 `scripts/test_suites/cli.py` 为准；full按组依次运行两层，不使用600秒统一外层超时。测试安装与产物均进入临时根。简单来源确认不重跑业务；迁移改动只验证受影响工具和夹具接缝。

真实模型测试见 [Journey规范](model-led-agent-eval.md)，WorkBuddy见 [原生审计](workbuddy-acceptance.md)。固定源码、版本、安装清单及运行身份，分开记录测试负责人模型和被测平台模型。原始测试PASS/FAIL与 [产品验收结论](model-led-product-acceptance.md) 并列；确定性夹具不冒充真实Journey，历史结果不自动覆盖新对象。

业务资料来源见 [夹具说明](../../tests/fixtures/README.md)，公开证据边界见 [验收摘要](../acceptance-summary.md)。
