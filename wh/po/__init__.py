# -*- coding: utf-8 -*-
"""采购模块 —— 采购单、明细、到货、付款、供应商

与库存完全独立：采购只做状态登记，唯一联系是入库时填的批次号。
    logic.py  纯业务逻辑（金额、状态推导、指纹、价格映射）
    routes.py Web 路由
"""
from ..core.router import Router

bp = Router('po')

from . import logic   # noqa: E402,F401
from . import routes  # noqa: E402,F401
