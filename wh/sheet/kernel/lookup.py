# -*- coding: utf-8 -*-
"""表格模块 · 电子表格 函数库 · 查找与引用函数

参数已经求值过：标量或二维列表（区域）；错误值沿调用链传播。
"""
from .convert import _cmp_ge, _cmp_le, _same, flat, to_bool, to_num, to_text

def _vlookup(ctx, *a):
    key = a[0] if a else None
    tbl = a[1] if len(a) > 1 else []
    col = int(to_num(a[2]) or 1) if len(a) > 2 else 1
    approx = to_bool(a[3]) if len(a) > 3 and a[3] not in (None, '') else True
    if not isinstance(tbl, (list, tuple)) or not tbl:
        return '#N/A'
    rows = [r if isinstance(r, (list, tuple)) else [r] for r in tbl]
    ci = col - 1
    if ci < 0 or ci >= max(len(r) for r in rows):
        return '#REF!'
    if approx is False:
        for r in rows:
            if _same(r[0] if r else None, key):
                return r[ci] if ci < len(r) else ''
        return '#N/A'
    last = '#N/A'
    for r in rows:
        v = r[0] if r else None
        if _cmp_le(v, key):
            last = r[ci] if ci < len(r) else ''
        else:
            break
    return last


def _hlookup(ctx, *a):
    key = a[0] if a else None
    tbl = a[1] if len(a) > 1 else []
    row = int(to_num(a[2]) or 1) if len(a) > 2 else 1
    approx = to_bool(a[3]) if len(a) > 3 and a[3] not in (None, '') else True
    if not isinstance(tbl, (list, tuple)) or not tbl:
        return '#N/A'
    rows = [r if isinstance(r, (list, tuple)) else [r] for r in tbl]
    ri = row - 1
    if ri < 0 or ri >= len(rows):
        return '#REF!'
    hdr = rows[0]
    for j, v in enumerate(hdr):
        if _same(v, key) if approx is False else True:
            if approx is False:
                return rows[ri][j] if j < len(rows[ri]) else ''
    if approx is False:
        return '#N/A'
    best = '#N/A'
    for j, v in enumerate(hdr):
        if _cmp_le(v, key):
            best = rows[ri][j] if j < len(rows[ri]) else ''
        else:
            break
    return best


def _lookup(ctx, *a):
    """LOOKUP(查找值, 查找向量, [结果向量]) / LOOKUP(查找值, 数组)

    向量形式：在查找向量里找「不大于查找值的最大项」，返回【结果向量】同位置的值；
    省略结果向量时返回查找向量里找到的那个值。查找向量需按升序排列。
    """
    key = a[0] if a else None
    if len(a) < 2:
        return '#N/A'
    if len(a) > 2:
        look = flat([a[1]])
        res = flat([a[2]])
        n = min(len(look), len(res))
        pos = -1
        for i in range(n):
            if _cmp_le(look[i], key):
                pos = i
            else:
                break
        return res[pos] if pos >= 0 else '#N/A'
    arr = a[1]
    rows = arr if isinstance(arr, (list, tuple)) else [[arr]]
    rows = [r if isinstance(r, (list, tuple)) else [r] for r in rows]
    if not rows or not rows[0]:
        return '#N/A'
    nr, nc = len(rows), max(len(r) for r in rows)
    if nc >= nr:                       # 宽数组：搜第一行，返回最后一行
        look = [rows[0][j] for j in range(nc)]
        res = [rows[nr - 1][j] for j in range(nc)]
    else:                              # 高数组：搜第一列，返回最后一列
        look = [rows[i][0] for i in range(nr)]
        res = [rows[i][nc - 1] for i in range(nr)]
    pos = -1
    for i, v in enumerate(look):
        if _cmp_le(v, key):
            pos = i
        else:
            break
    return res[pos] if pos >= 0 else '#N/A'


def _xlookup(ctx, *a):
    key = a[0] if a else None
    lookup = flat([a[1]]) if len(a) > 1 else []
    ret = flat([a[2]]) if len(a) > 2 else []
    miss = a[3] if len(a) > 3 and a[3] not in (None,) else '#N/A'
    for i, v in enumerate(lookup):
        if _same(v, key):
            return ret[i] if i < len(ret) else '#N/A'
    return miss


def _index(ctx, *a):
    arr = a[0] if a else []
    r = a[1] if len(a) > 1 else None
    c = a[2] if len(a) > 2 else None
    if not isinstance(arr, (list, tuple)) or not arr:
        return '#REF!'
    rows = [x if isinstance(x, (list, tuple)) else [x] for x in arr]
    ri = (int(to_num(r)) - 1) if r not in (None, '') else 0
    ci = (int(to_num(c)) - 1) if c not in (None, '') else 0
    if isinstance(ri, int) and isinstance(ci, int):
        if ri < 0 or ri >= len(rows):
            return '#REF!'
        row = rows[ri]
        if ci < 0 or ci >= len(row):
            return '#REF!'
        return row[ci]
    if ri is not None and c in (None, ''):
        return [rows[ri]] if 0 <= ri < len(rows) else '#REF!'
    return [[row[ci]] for row in rows] if 0 <= ci else '#REF!'


def _match(ctx, *a):
    key = a[0] if a else None
    vec = flat([a[1]]) if len(a) > 1 else []
    # 注意：0 是精确匹配，不能用 `to_num(...) or 1` —— 0 是 falsy，会被换成 1
    _m = to_num(a[2]) if len(a) > 2 and a[2] not in (None, '') else None
    mode = int(_m) if _m is not None else 1
    if mode == 0:
        for i, v in enumerate(vec):
            if _same(v, key):
                return float(i + 1)
        return '#N/A'
    if mode == 1:
        best = '#N/A'
        for i, v in enumerate(vec):
            if _cmp_le(v, key):
                best = float(i + 1)
            else:
                break
        return best
    best = '#N/A'
    for i, v in enumerate(vec):
        if _cmp_ge(v, key):
            best = float(i + 1)
        else:
            break
    return best


def _offset(ctx, *a):
    """OFFSET(基点, 行偏移, 列偏移, 高, 宽) —— 需要引擎提供取区域能力"""
    return ctx.offset(a) if ctx and hasattr(ctx, 'offset') else '#REF!'


def _indirect(ctx, *a):
    return ctx.indirect(a[0] if a else '') if ctx and hasattr(ctx, 'indirect') \
        else '#REF!'


def _row(ctx, *a):
    if a and a[0] not in (None, ''):
        return ctx.ref_row(a[0]) if ctx else '#REF!'
    return float((ctx.row if ctx else 0) + 1)


def _column(ctx, *a):
    if a and a[0] not in (None, ''):
        return ctx.ref_col(a[0]) if ctx else '#REF!'
    return float((ctx.col if ctx else 0) + 1)


def _rows(ctx, *a):
    v = a[0] if a else None
    if isinstance(v, (list, tuple)):
        return float(len(v))
    return 1.0


def _columns(ctx, *a):
    v = a[0] if a else None
    if isinstance(v, (list, tuple)) and v:
        return float(max(len(x) if isinstance(x, (list, tuple)) else 1 for x in v))
    return 1.0


def _choose(ctx, *a):
    i = int(to_num(a[0]) or 1) if a else 1
    if i < 1 or i > len(a) - 1:
        return '#VALUE!'
    return a[i]


def _transpose(ctx, *a):
    v = a[0] if a else []
    if not isinstance(v, (list, tuple)):
        return [[v]]
    rows = [x if isinstance(x, (list, tuple)) else [x] for x in v]
    w = max(len(r) for r in rows) if rows else 0
    return [[(rows[i][j] if j < len(rows[i]) else '') for i in range(len(rows))]
            for j in range(w)]


def _unique(ctx, *a):
    seen, out = [], []
    for v in flat([a[0]] if a else []):
        k = to_text(v)
        if k not in seen:
            seen.append(k)
            out.append(v)
    return [[x] for x in out]


def _sort_range(ctx, *a):
    vs = flat([a[0]] if a else [])
    desc = (int(to_num(a[1]) or 1) if len(a) > 1 and a[1] not in (None, '') else 1) < 0
    vs = sorted(vs, key=lambda x: (to_num(x) is None, to_num(x) or 0, to_text(x)),
                reverse=desc)
    return [[x] for x in vs]


def _filter_range(ctx, *a):
    arr = flat([a[0]] if a else [])
    conds = flat([a[1]] if len(a) > 1 else [])
    out = [v for i, v in enumerate(arr)
           if to_bool(conds[i] if i < len(conds) else False)]
    return [[x] for x in out]
