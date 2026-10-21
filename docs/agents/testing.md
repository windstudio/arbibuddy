# 分层测试执行

在源码树外指定可写的临时根；各组单独启动 Python 进程，超时杀掉该组子进程并继续汇总其余结果。禁止给完整套件套单个600秒外层超时。

```powershell
python -B -X utf8 -m scripts.test_suites.cli fast --temp-root <external-temp>/<VERSION>
python -B -X utf8 -m scripts.test_suites.cli slow --temp-root <external-temp>/<VERSION> --wheelhouse <受控离线wheelhouse>
python -B -X utf8 -m scripts.test_suites.cli full --temp-root <external-temp>/<VERSION> --wheelhouse <受控离线wheelhouse>
```

`fast` 包含档案、公共金额、法源观察登记、九领域与文书场景、模型主导静态约束、收缩契约、测试调度和网络诊断、自然语言Journey Harness的确定性测试；每组300秒。`slow` 包含八类文书核心、交付与OOXML支持性测试、三平台安装/身份/事务、发布证据原语；文书与平台各900秒，身份与发布各300秒。权威组清单是 `scripts/test_suites/cli.py`；`full` 依次运行两层，没有统一总超时。

临时案件、安装树、ZIP和日志放在源码外；使用 TEMP/TMP/ARBIBUDDY_TEST_TEMP_ROOT 与 `tests/sitecustomize.py` 的清理约束。WorkBuddy锁定依赖按显式参数、ARBIBUDDY_WORKBUDDY_WHEELHOUSE、锁文件受控目录解析，不能联网下载补齐。缺环境必须报告边界，不能默默算PASS。

项目默认离线依赖目录为 `references/workbuddy-wheels`；缓存的完整性、摘要和准备边界见 [WorkBuddy 离线构建依赖](workbuddy-wheelhouse.md)。这不改变测试与构建入口禁止自动联网补齐的规则。

真实模型测试使用 [模型主导Journey规范](model-led-agent-eval.md) 与 `scripts.model_led_agent_eval.cli`，不进入fast/slow/full，不用程序夹具冒充真实Journey。测试前固定源码快照、VERSION、安装清单和关键资源身份，区分Harness、Scenario、Runtime Adapter、SUT/Skill、环境/模型波动。历史真实Journey仅有界复用；测试设置失败保留原日志，定向复验不得改写旧失败。

发布证据入口 `python -m scripts.release.cli --source <源码> --evidence <记录>` 只校验显式报告文件摘要和运行期身份，不执行测试、不生成制品、不把候选或历史Journey提升成当前验收。正式发布仍须按 [制品生命周期](artifact-lifecycle.md) 核对完整发布清单、包、门禁记录和证据，只能交付项目正式根的版本目录。本轮Ticket12不创建新版本或发布包。

Skill元数据用独立验证环境中的 `quick_validate.py` 检查；PyYAML属于验证依赖，不加入文书运行时。
