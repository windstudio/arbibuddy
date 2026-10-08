"""兼容导出：安装副本可复现的 CaseArchive 命令调用描述。

这是平台运行时的唯一实现入口；``scripts.case_archive.__init__`` 只做兼容导出。
新的业务逻辑不得继续添加到转发层，若所有外部调用迁移到 ``scripts.runtime_context``，
下一次主版本可删除本模块。
"""

from __future__ import annotations

from scripts.runtime_context import CaseArchiveInvocation, build_case_archive_invocation

__all__ = ["CaseArchiveInvocation", "build_case_archive_invocation"]
