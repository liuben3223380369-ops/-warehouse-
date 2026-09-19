# -*- coding: utf-8 -*-
"""采购模块 Web 路由

    order.py     采购单路由聚合入口（导入即注册）
      common.py    共享：汇总快照同步
      home.py      看板与汇总
      new.py       新建与表单辅助
      detail.py    详情、状态、明细
      settle.py    到货、付款、删除
    supplier.py  供应商：列表与维护
    export.py    采购台账导出（xlsx）

导入即注册：每个模块的 @bp.route 在 import 时挂到蓝图上。
"""
from . import order, supplier, export  # noqa: E402,F401
