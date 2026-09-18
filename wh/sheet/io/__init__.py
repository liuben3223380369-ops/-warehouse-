# -*- coding: utf-8 -*-
"""制表 IO 层 —— 工作簿格式的读写入口

建立在 kernel/ + engine/ 之上：

    kernel/   地址 · 词法 · 语法 · 函数表 · 样式
    engine/   数据模型（Sheet / Workbook）+ 求值
    io/       ★ 本层
        xlsx.py   读/写 xlsx（公式原文、样式、合并、列宽、冻结一并带出）
        csv.py    读/写 csv（导出带 BOM，导入自动探测编码）

与 wh/core/export.py 的区别：
    本层处理「工作簿」——保留公式与样式，用于电子表格本身的存取；
    core/export.py 处理「报表」——headers + rows 直接出文件，供各业务模块共用。

对外只通过本文件导出，外部不要直接 import 具体子模块。
"""

from .xlsx import (to_xlsx, from_xlsx, HAVE_OPENPYXL)
from .csv import to_csv, from_csv

__all__ = [
    # xlsx
    'to_xlsx', 'from_xlsx', 'HAVE_OPENPYXL',
    # csv
    'to_csv', 'from_csv',
]
