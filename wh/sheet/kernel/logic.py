# -*- coding: utf-8 -*-
"""表格模块 · 电子表格 函数库 · 逻辑函数

参数已经求值过：标量或二维列表（区域）；错误值沿调用链传播。
"""
from .convert import flat, to_bool

def _if(ctx, *a):
    """IF 需要惰性求值，由引擎特殊处理；这里兜底（参数已算好时）"""
    c = to_bool(a[0]) if a else False
    if c is None:
        return '#VALUE!'
    if c:
        return a[1] if len(a) > 1 and a[1] not in (None,) else True
    return a[2] if len(a) > 2 and a[2] not in (None,) else False


def _and(*a):
    vs = flat(a, skip_blank=True)
    if not vs:
        return '#VALUE!'
    for v in vs:
        b = to_bool(v)
        if b is None:
            return '#VALUE!'
        if not b:
            return False
    return True


def _or(*a):
    vs = flat(a, skip_blank=True)
    if not vs:
        return '#VALUE!'
    for v in vs:
        b = to_bool(v)
        if b is None:
            return '#VALUE!'
        if b:
            return True
    return False


def _xor(*a):
    vs = flat(a, skip_blank=True)
    if not vs:
        return '#VALUE!'
    c = 0
    for v in vs:
        b = to_bool(v)
        if b is None:
            return '#VALUE!'
        c += 1 if b else 0
    return c % 2 == 1


def _not(*a):
    b = to_bool(a[0]) if a else False
    if b is None:
        return '#VALUE!'
    return not b


def _true(*a):
    return True


def _false(*a):
    return False
