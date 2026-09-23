"""任务激活条件：限定 price-only 验收任务的行为，不永久改变既有测试语义。

PR16-02 要求：
- 守卫与 skipif **不能没有任务激活条件**——否则命中硬编码路径的其他调用
  会永久受影响；
- 复用既有执行策略与测试组合根限定本任务行为；
- **不能靠任意环境变量放开真实数据**；
- 离开本任务不产生新研究授权。

因此本模块提供：

- ``price_only_task_active()``：本任务是否处于激活状态。激活条件是
  **本任务工作区的显式标记**（结果根存在），不是任意环境变量。
- ``real_gbbq_access_forbidden()``：真实 gbbq 是否被本任务禁止（= 任务激活）。
- ``REAL_DATA_INTEGRATION`` 标记：真实本机数据集成测试的独立分类标记，
  用于把它们与「合成可证的正确性」区分开。
"""
from __future__ import annotations

import os
from pathlib import Path

# 本任务工作区标记：结果根存在即视为本任务激活。
# 这是**工作区级**标记（随分支走），不是可随意设置的运行时开关。
_TASK_MARKER = Path(__file__).resolve().parents[2] / "reports" / "price_only_validation_v1"
_MARKER_FILE = _TASK_MARKER / "PRICE_ONLY_CAPABILITY_MATRIX_V1.json"


def price_only_task_active() -> bool:
    """本任务是否激活（工作区级标记，随分支存在）。"""
    return _MARKER_FILE.exists()


def real_gbbq_access_forbidden() -> bool:
    """真实 gbbq 访问是否被禁止（= 本任务激活时）。

    非本任务环境（如其他分支/其他任务）不激活，从而既有测试语义不被永久改变。
    """
    return price_only_task_active()


def real_data_integration_enabled() -> bool:
    """真实本机数据集成测试是否应执行。

    默认**不执行**（本任务禁止真实 gbbq）；显式请求也不放开——
    任务文档明确不接受靠环境变量打开真实数据。
    该函数存在的意义是让分类显式，而不是提供开关。
    """
    return False
