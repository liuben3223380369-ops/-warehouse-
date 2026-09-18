# -*- coding: utf-8 -*-
"""电子表格引擎 · 数据模型

一本工作簿 = 多张 Sheet + 命名区域；一张 Sheet = 稀疏单元格字典。

    cell.py     Cell —— 一格：原文 / 类型 / 值 / 样式 / 批注
    sheet.py    Sheet —— 一张表：存取、依赖、重算调度、序列化
    book.py     Workbook —— 一本簿：装载、寻址、命名区域

本文件只是聚合入口，方便外部写 `from ..engine import core as E`
再用 `E.Cell / E.Sheet / E.Workbook`。新代码请直接 import 具体子模块。
"""

from .cell import Cell, MAX_CELLS                       # noqa: F401
from .sheet import Sheet                                # noqa: F401
from .book import Workbook                              # noqa: F401

__all__ = ['Cell', 'Sheet', 'Workbook', 'MAX_CELLS']
