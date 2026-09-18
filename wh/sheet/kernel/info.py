# -*- coding: utf-8 -*-
"""表格模块 · 电子表格 函数库 · 信息函数

参数已经求值过：标量或二维列表（区域）；错误值沿调用链传播。
"""
from .convert import is_blank, is_err, to_text

def _isnumber(*a):
    return isinstance(a[0] if a else None, (int, float)) \
        and not isinstance(a[0], bool)


def _istext(*a):
    return isinstance(a[0] if a else None, str) and not is_err(a[0])


def _isblank(*a):
    return is_blank(a[0] if a else None)


def _iserror(*a):
    return is_err(a[0] if a else None)


def _iserr(*a):
    v = a[0] if a else None
    return is_err(v) and v != '#N/A'


def _isna(*a):
    return (a[0] if a else None) == '#N/A'


def _islogical(*a):
    return isinstance(a[0] if a else None, bool)


def _isnontext(*a):
    return not _istext(*a)


def _isref(*a):
    return False


def _type(*a):
    v = a[0] if a else None
    if is_err(v):
        return 16.0
    if isinstance(v, bool):
        return 4.0
    if isinstance(v, (int, float)):
        return 1.0
    if isinstance(v, str):
        return 2.0
    if isinstance(v, (list, tuple)):
        return 64.0
    return 16.0


def _na(*a):
    return '#N/A'


def _error_type(*a):
    v = a[0] if a else None
    return float({'#NULL!': 1, '#DIV/0!': 2, '#VALUE!': 3, '#REF!': 4,
                  '#NAME?': 5, '#NUM!': 6, '#N/A': 7,
                  '#GETTING_DATA': 8}.get(v, 0)) if is_err(v) else '#N/A'


def _cell_info(ctx, *a):
    what = to_text(a[0] if a else '').lower()
    if what == 'row':
        return float((ctx.row if ctx else 0) + 1)
    if what == 'col':
        return float((ctx.col if ctx else 0) + 1)
    if what == 'address':
        return ctx.addr if ctx else ''
    return '#VALUE!'
