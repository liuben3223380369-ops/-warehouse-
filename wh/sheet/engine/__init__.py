# -*- coding: utf-8 -*-
"""制表求值层 —— 数据模型 + 公式求值（可插拔后端）

这一层建立在 kernel/ 之上：

    kernel/   地址 · 词法 · 语法 · 函数表 · 样式（零业务耦合）
    engine/   ★ 本层
        core.py         ★ 聚合入口（外部用 E.Cell / E.Sheet / E.Workbook）
          cell.py         Cell —— 一格：原文 / 类型 / 值 / 样式 / 批注
          sheet.py        Sheet —— 一张表：存取、依赖、重算、序列化
          book.py         Workbook —— 一本簿：装载、寻址、命名区域
        ctx.py          求值原语：上下文、运算符、条件匹配、引用取点
        lazy_logic.py   惰性·逻辑分支  IF / IFERROR / IFNA / IFS / SWITCH
        lazy_cond.py    惰性·条件聚合  SUMIF / COUNTIF / *IFS / SUBTOTAL
        lazy_agg.py     惰性·AGGREGATE
        lazy_ref.py     惰性·引用      OFFSET / ROW / COLUMN / ISREF / CELL
        lazy.py         惰性函数登记表（求值时先查这张表）
        eval.py         AstEvaluator —— 自研 AST 求值器
        ironcalc.py     第二计算层（可选，Apache-2.0 OR MIT，装不上不影响）

**可插拔**：换公式实现只需替换 `make_evaluator` 工厂，
数据模型（core）与业务代码都不用动。这是 v3.71 剥离公式链的目的。

对外只通过本文件导出，外部不要直接 import 具体子模块。
"""

from .core import Cell, Sheet, Workbook
from .eval import FnCtx, AstEvaluator, make_evaluator
from .lazy import LAZY_IMPL

try:                                   # 可选依赖：未安装时该子模块仍可 import
    from . import ironcalc
except Exception:                      # pragma: no cover
    ironcalc = None

__all__ = [
    # 数据模型
    'Cell', 'Sheet', 'Workbook',
    # 自研求值
    'FnCtx', 'AstEvaluator', 'make_evaluator', 'LAZY_IMPL',
    # 第二计算层（可能为 None）
    'ironcalc',
]
