# Renderer portability OOXML baseline

这八份 DOCX 与 `inputs.json` 固定来源于迁移基点 `fde9089`，用于比较相同输入下的
正文 XML、样式、编号、页脚、页面设置、关系和可见文本。

测试只读取这些 fixture，绝不根据当前实现自动刷新。若未来有意修改文书内容或版式，
必须通过独立的基线迁移评审提交替换 fixture，并同时变更 `base_commit`；渲染器实现
或依赖升级不得更新本目录来掩盖差异。

