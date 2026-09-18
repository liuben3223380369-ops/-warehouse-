# -*- coding: utf-8 -*-
"""惰性函数·AGGREGATE：19 个功能码 + 忽略选项"""
import math

from ..kernel import to_num, is_err
from .ctx import _crit_range

#: AGGREGATE 功能码 -> 聚合方式（覆盖 Excel 1~19 号）
_AGG_FN = {
    1: 'avg', 2: 'count', 3: 'counta', 4: 'max', 5: 'min', 6: 'product',
    7: 'stdev', 8: 'stdevp', 9: 'sum', 10: 'var', 11: 'varp',
    12: 'median', 13: 'mode', 14: 'large', 15: 'small',
    16: 'percentile_inc', 17: 'quartile_inc',
    18: 'percentile_exc', 19: 'quartile_exc',
}


def _agg_apply(how, vs, k=None):
    """对已清洗的数值列表做聚合"""
    if how == 'sum':
        return float(sum(vs))
    if how == 'avg':
        return (sum(vs) / len(vs)) if vs else '#DIV/0!'
    if how in ('count', 'counta'):
        return float(len(vs))
    if how == 'max':
        return max(vs) if vs else 0.0
    if how == 'min':
        return min(vs) if vs else 0.0
    if how == 'product':
        p = 1.0
        for x in vs:
            p *= x
        return p
    if how in ('stdev', 'var'):
        n = len(vs)
        if n < 2:
            return '#DIV/0!'
        m = sum(vs) / n
        ss = sum((x - m) ** 2 for x in vs)
        return math.sqrt(ss / (n - 1)) if how == 'stdev' else ss / (n - 1)
    if how in ('stdevp', 'varp'):
        if not vs:
            return '#DIV/0!'
        m = sum(vs) / len(vs)
        ss = sum((x - m) ** 2 for x in vs)
        return math.sqrt(ss / len(vs)) if how == 'stdevp' else ss / len(vs)
    if how == 'median':
        if not vs:
            return '#NUM!'
        sv = sorted(vs)
        n = len(sv)
        return sv[n // 2] if n % 2 else (sv[n // 2 - 1] + sv[n // 2]) / 2.0
    if how == 'mode':
        if not vs:
            return '#NUM!'
        cnt = {}
        for v in vs:
            cnt[v] = cnt.get(v, 0) + 1
        top = max(cnt.values())
        if top < 2:
            return '#N/A'
        for v in vs:
            if cnt[v] == top:
                return v
        return '#N/A'
    if how in ('large', 'small'):
        if not vs or k is None:
            return '#NUM!'
        sv = sorted(vs, reverse=(how == 'large'))
        ki = int(k)
        if ki < 1 or ki > len(sv):
            return '#NUM!'
        return sv[ki - 1]
    if how in ('percentile_inc', 'percentile_exc',
               'quartile_inc', 'quartile_exc'):
        # PERCENTILE/QUARTILE 的 k 参数：百分位传 0~1，四分位传 1~4
        if not vs or k is None:
            return '#NUM!'
        sv = sorted(vs)
        n = len(sv)
        if 'quartile' in how:
            q = to_num(k)
            if q is None or q < 1 or q > 4:
                return '#NUM!'
            kk = [0.0, 0.25, 0.5, 0.75, 1.0][int(q)]
        else:
            kk = to_num(k)
            if kk is None:
                return '#NUM!'
        if 'exc' in how:
            if kk <= 0 or kk >= 1 or n < 2:
                return '#NUM!'
            pos = kk * (n + 1)
        else:
            if kk < 0 or kk > 1:
                return '#NUM!'
            pos = kk * (n - 1)
        lo = int(math.floor(pos))
        hi = int(math.ceil(pos))
        if lo == hi:
            return sv[min(lo, n - 1)]
        if lo < 0:
            return sv[0]
        if hi > n - 1:
            return sv[n - 1]
        return sv[lo] + (sv[hi] - sv[lo]) * (pos - lo)
    return '#VALUE!'


def _lazy_aggregate(sheet, r, c, args, depth):
    """AGGREGATE(功能码, 选项, 区域, [k]) —— 与 SUBTOTAL 的关键区别：

    1) 第二个参数是「选项」而不是数据区，不能当成区域求和；
    2) 支持忽略错误值（选项 2/3/6/7）；
    3) 大/小/百分位类功能码还需要第四个参数 k。
    """
    if len(args) < 3:
        return '#VALUE!'
    fn = to_num(sheet._eval_ast(args[0], r, c, depth))
    opt = to_num(sheet._eval_ast(args[1], r, c, depth))
    if fn is None or opt is None:
        return '#VALUE!'
    fn, opt = int(fn), int(opt)
    how = _AGG_FN.get(fn)
    if how is None:
        return '#VALUE!'
    skip_err = opt in (2, 3, 6, 7)      # 忽略错误值
    k = None
    if how in ('large', 'small', 'percentile_inc', 'percentile_exc',
               'quartile_inc', 'quartile_exc'):
        if len(args) < 4:
            return '#VALUE!'
        k = sheet._eval_ast(args[3], r, c, depth)
        raw = list(_crit_range(sheet, args[2], r, c, depth))
    else:
        raw = []
        for nd in args[2:]:
            v = _crit_range(sheet, nd, r, c, depth)
            if is_err(v):
                if skip_err:
                    continue
                return v
            raw.extend(v)
    vs = []
    for x in raw:
        if is_err(x):
            if skip_err:
                continue
            return x
        n = to_num(x)
        if n is None:
            if how in ('counta',):
                vs.append(0.0)
            continue
        vs.append(n)
    if how == 'count':
        return float(len(vs))
    if how == 'counta':
        return float(len([x for x in raw if not is_err(x)]))
    return _agg_apply(how, vs, k)
