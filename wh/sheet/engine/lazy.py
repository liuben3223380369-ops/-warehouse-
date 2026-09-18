# -*- coding: utf-8 -*-
"""惰性函数登记表

求值时若函数名在此表中，走惰性分支（收 AST 节点，可短路）；
否则走 kernel.funcs.FUNCS 的普通分支（收已求值参数）。"""
from .lazy_logic import (_lazy_if, _lazy_iferror, _lazy_ifna,
                         _lazy_ifs, _lazy_switch)
from .lazy_cond import (_lazy_sumif, _lazy_countif, _lazy_averageif,
                        _lazy_sumifs, _lazy_countifs, _lazy_averageifs,
                        _lazy_maxifs, _lazy_minifs, _lazy_subtotal)
from .lazy_agg import _lazy_aggregate
from .lazy_ref import (_lazy_offset, _lazy_row, _lazy_column, _lazy_rows,
                       _lazy_columns, _lazy_isref, _lazy_cell)

LAZY_IMPL = {
    'IF': _lazy_if, 'IFERROR': _lazy_iferror, 'IFNA': _lazy_ifna,
    'IFS': _lazy_ifs, 'SWITCH': _lazy_switch,
    'ISREF': _lazy_isref,
    'CELL': _lazy_cell,
    'SUMIF': _lazy_sumif, 'COUNTIF': _lazy_countif,
    'AVERAGEIF': _lazy_averageif,
    'SUMIFS': _lazy_sumifs, 'COUNTIFS': _lazy_countifs,
    'AVERAGEIFS': _lazy_averageifs, 'MAXIFS': _lazy_maxifs,
    'MINIFS': _lazy_minifs,
    'SUBTOTAL': _lazy_subtotal, 'AGGREGATE': _lazy_aggregate,
    'ROW': _lazy_row, 'COLUMN': _lazy_column,
    'ROWS': _lazy_rows, 'COLUMNS': _lazy_columns,
    'OFFSET': _lazy_offset,
}
