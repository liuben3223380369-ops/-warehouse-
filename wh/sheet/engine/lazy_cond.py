# -*- coding: utf-8 -*-
"""惰性函数·条件聚合：SUMIF / COUNTIF / AVERAGEIF / *IFS / SUBTOTAL"""
from ..kernel import to_num, is_err
from .ctx import _crit_matcher, _crit_range

def _lazy_sumif(sheet, r, c, args, depth):
    rng = _crit_range(sheet, args[0], r, c, depth) if args else []
    if is_err(rng):
        return rng
    crit = sheet._eval_ast(args[1], r, c, depth) if len(args) > 1 else None
    m = _crit_matcher(crit)
    if len(args) > 2:
        sr = _crit_range(sheet, args[2], r, c, depth)
        if is_err(sr):
            return sr
        src = sr
    else:
        src = rng
    tot = 0.0
    for i, v in enumerate(rng):
        if m(v) and i < len(src):
            n = to_num(src[i])
            if n is not None:
                tot += n
    return tot


def _lazy_countif(sheet, r, c, args, depth):
    rng = _crit_range(sheet, args[0], r, c, depth) if args else []
    if is_err(rng):
        return rng
    m = _crit_matcher(sheet._eval_ast(args[1], r, c, depth)
                      if len(args) > 1 else None)
    return float(sum(1 for v in rng if m(v)))


def _lazy_averageif(sheet, r, c, args, depth):
    rng = _crit_range(sheet, args[0], r, c, depth) if args else []
    if is_err(rng):
        return rng
    m = _crit_matcher(sheet._eval_ast(args[1], r, c, depth)
                      if len(args) > 1 else None)
    src = _crit_range(sheet, args[2], r, c, depth) if len(args) > 2 else rng
    if is_err(src):
        return src
    vs = [to_num(src[i]) for i, v in enumerate(rng)
          if m(v) and i < len(src)]
    vs = [x for x in vs if x is not None]
    return (sum(vs) / len(vs)) if vs else '#DIV/0!'


def _multi_ifs(sheet, r, c, args, depth, agg):
    """SUMIFS / COUNTIFS / AVERAGEIFS 的公共逻辑"""
    if not args or len(args) < 2:
        return '#VALUE!'
    # SUMIFS(求和区, 条件区1, 条件1, ...) —— 求和区在【最前】，条件成对排在后面；
    # COUNTIFS/AVERAGEIFS 没有求和区，条件从第 0 个参数开始成对出现。
    base = 1 if agg == 'sum' else 0
    pairs = (len(args) - base) // 2
    rngs, crits = [], []
    for i in range(pairs):
        rv = _crit_range(sheet, args[base + i * 2], r, c, depth)
        if is_err(rv):
            return rv
        rngs.append(rv)
        crits.append(_crit_matcher(
            sheet._eval_ast(args[base + i * 2 + 1], r, c, depth)))
    n = min(len(x) for x in rngs) if rngs else 0
    if agg == 'sum':
        src = _crit_range(sheet, args[0], r, c, depth)
        if is_err(src):
            return src
        tot = 0.0
        for i in range(n):
            if all(crits[k](rngs[k][i]) for k in range(len(rngs))):
                v = to_num(src[i]) if i < len(src) else None
                if v is not None:
                    tot += v
        return tot
    cnt = 0
    for i in range(n):
        if all(crits[k](rngs[k][i]) for k in range(len(rngs))):
            cnt += 1
    return float(cnt)


def _lazy_sumifs(sheet, r, c, args, depth):
    return _multi_ifs(sheet, r, c, args, depth, 'sum')


def _lazy_countifs(sheet, r, c, args, depth):
    return _multi_ifs(sheet, r, c, args, depth, 'count')


def _lazy_averageifs(sheet, r, c, args, depth):
    """AVERAGEIFS(求值区, 条件区1, 条件1, ...)"""
    if len(args) < 3:
        return '#VALUE!'
    src = _crit_range(sheet, args[0], r, c, depth)
    if is_err(src):
        return src
    rngs, crits = [], []
    rest = args[1:]
    for i in range(len(rest) // 2):
        rv = _crit_range(sheet, rest[i * 2], r, c, depth)
        if is_err(rv):
            return rv
        rngs.append(rv)
        crits.append(_crit_matcher(sheet._eval_ast(rest[i * 2 + 1], r, c, depth)))
    n = min([len(src)] + [len(x) for x in rngs]) if rngs else len(src)
    vs = [to_num(src[i]) for i in range(n)
          if all(crits[k](rngs[k][i]) for k in range(len(rngs)))]
    vs = [x for x in vs if x is not None]
    return (sum(vs) / len(vs)) if vs else '#DIV/0!'


def _lazy_maxifs(sheet, r, c, args, depth):
    src = _crit_range(sheet, args[0], r, c, depth) if args else []
    rngs, crits = [], []
    rest = args[1:]
    for i in range(len(rest) // 2):
        rv = _crit_range(sheet, rest[i * 2], r, c, depth)
        if is_err(rv):
            return rv
        rngs.append(rv)
        crits.append(_crit_matcher(sheet._eval_ast(rest[i * 2 + 1], r, c, depth)))
    n = min([len(src)] + [len(x) for x in rngs]) if rngs else len(src)
    vs = [to_num(src[i]) for i in range(n)
          if all(crits[k](rngs[k][i]) for k in range(len(rngs)))]
    vs = [x for x in vs if x is not None]
    return max(vs) if vs else 0.0


def _lazy_minifs(sheet, r, c, args, depth):
    v = _lazy_maxifs(sheet, r, c, args, depth)
    if is_err(v):
        return v
    src = _crit_range(sheet, args[0], r, c, depth) if args else []
    rngs, crits = [], []
    rest = args[1:]
    for i in range(len(rest) // 2):
        rv = _crit_range(sheet, rest[i * 2], r, c, depth)
        if is_err(rv):
            return rv
        rngs.append(rv)
        crits.append(_crit_matcher(sheet._eval_ast(rest[i * 2 + 1], r, c, depth)))
    n = min([len(src)] + [len(x) for x in rngs]) if rngs else len(src)
    vs = [to_num(src[i]) for i in range(n)
          if all(crits[k](rngs[k][i]) for k in range(len(rngs)))]
    vs = [x for x in vs if x is not None]
    return min(vs) if vs else 0.0


_SUBTOTAL = {1: 'avg', 2: 'count', 3: 'counta', 4: 'max', 5: 'min',
             6: 'product', 9: 'sum', 101: 'avg', 102: 'count',
             103: 'counta', 104: 'max', 105: 'min', 106: 'product',
             109: 'sum'}


def _lazy_subtotal(sheet, r, c, args, depth):
    if not args:
        return '#VALUE!'
    n = int(to_num(sheet._eval_ast(args[0], r, c, depth)) or 9)
    how = _SUBTOTAL.get(n, 'sum')
    vs = [to_num(x) for x in _crit_range(sheet, args[1], r, c, depth)] \
        if len(args) > 1 else []
    vs = [x for x in vs if x is not None]
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
    return 0.0
