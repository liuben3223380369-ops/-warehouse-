# -*- coding: utf-8 -*-
"""制表内核 —— 词法、语法、地址、函数表、样式

这一层**零业务耦合**：不 import flask / db / 任何业务模块，
可以整块拿出去单独跑，也方便单独做单元测试。

对外只通过本文件导出，外部不要直接 import 具体子模块。
"""

from .addr import (col_letter, col_index, a1, rc_to_a1, a1_to_rc,
                   Ref, parse_ref, looks_like_ref, range_of, norm_rect,
                   shift_formula)
from .lexer import (ERRORS, Tok, LexError, tokenize)
from .parser import parse, parse as parse_formula
from .funcs import (is_err, to_num, to_text, to_bool, all_names)
from .style import (Style, CondRule, format_value, is_date_code, display)

__all__ = [
    # 地址
    'col_letter', 'col_index', 'a1', 'rc_to_a1', 'a1_to_rc',
    'Ref', 'parse_ref', 'looks_like_ref', 'range_of', 'norm_rect',
    'shift_formula',
    # 词法
    'ERRORS', 'Tok', 'LexError', 'tokenize',
    # 语法
    'parse', 'parse_formula',
    # 函数
    'is_err', 'to_num', 'to_text', 'to_bool', 'all_names',
    # 样式
    'Style', 'CondRule', 'format_value', 'is_date_code', 'display',
]
