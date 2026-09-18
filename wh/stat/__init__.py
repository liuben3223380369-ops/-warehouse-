# -*- coding: utf-8 -*-
"""统计模块 —— 门面

拆成两个文件，各管一摊：
    report.py   首页洞察、月报表、库存与流水导出
    center.py   统计中心八张分析表与导出

这里只做两件事：造路由表 bp，以及把两个子模块 import 进来
（@bp.route 在模块加载时执行，import 了就等于注册了路由）。
"""
from ..core.router import Router
bp = Router('stat')

from . import report, center            # noqa: F401  注册路由
from .report import csv_safe            # noqa: F401  导出共用
from .center import TABS                # noqa: F401
