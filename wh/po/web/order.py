# -*- coding: utf-8 -*-
"""采购单 Web 路由 —— 按职责拆到同级四个模块

    common.py  共享：汇总快照同步
    home.py    采购首页看板、汇总台账（2 条路由）
    new.py     新建采购单与表单辅助计算（1 条）
    detail.py  详情、状态流转、明细增删（5 条）
    settle.py  到货、付款、删除（5 条）

导入即注册：每个模块的 @bp.route 在 import 时挂到蓝图上。
"""
from . import common, home, new, detail, settle   # noqa: F401
