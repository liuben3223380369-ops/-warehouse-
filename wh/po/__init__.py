# -*- coding: utf-8 -*-
"""采购模块 —— 采购单、明细、到货、付款、供应商、模板

与库存完全独立：采购只做状态登记，唯一联系是入库时填的批次号。

分层（严格单向，不允许反向 import）::

    amount.py   金额口径、指纹、价格映射   ← 无内部依赖，本包最底层
    head.py     单头金额、已付、欠款
    summary.py  汇总快照（历史对账）
    status.py   状态推导、单号
    receive.py  到货入库（采购↔库存唯一连接点）
    query.py    首页看板、供应商列表
    web/        Web 路由（order / tpl / supplier / export）
"""
from ..core.router import Router

bp = Router('po')

from . import amount   # noqa: E402,F401
from . import head     # noqa: E402,F401
from . import summary  # noqa: E402,F401
from . import status   # noqa: E402,F401
from . import receive  # noqa: E402,F401
from . import query    # noqa: E402,F401
from . import web      # noqa: E402,F401
