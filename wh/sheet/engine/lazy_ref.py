# -*- coding: utf-8 -*-
"""惰性函数·引用：OFFSET / ROW / COLUMN / ROWS / COLUMNS / ISREF / CELL

这些必须惰性——它们要的是"引用本身"，不是引用算出来的值。"""
from ..kernel import addr as A          # noqa: F401
from ..kernel import to_num, is_err
from .ctx import _node_ref

def _lazy_offset(sheet, r, c, args, depth):
    """OFFSET(基点, 行偏移, 列偏移, [高], [宽])

    关键是必须拿到基点【引用本身】的地址 —— 之前走普通函数表时收到的是
    基点区域求出来的值，丢失了位置，只能拿当前单元格当基准，结果恒为 #REF!。
    """
    if not args:
        return '#REF!'
    ref = _node_ref(args[0])
    if ref is None:
        v = sheet._eval_ast(args[0], r, c, depth)
        if is_err(v):
            return v
        return '#REF!'
    dr = int(to_num(sheet._eval_ast(args[1], r, c, depth)) or 0) \
        if len(args) > 1 else 0
    dc = int(to_num(sheet._eval_ast(args[2], r, c, depth)) or 0) \
        if len(args) > 2 else 0
    h = args[3] if len(args) > 3 else None
    w = args[4] if len(args) > 4 else None
    hh = int(to_num(sheet._eval_ast(h, r, c, depth)) or 1) if h is not None else 1
    ww = int(to_num(sheet._eval_ast(w, r, c, depth)) or 1) if w is not None else 1
    if hh < 1 or ww < 1:
        return '#REF!'
    # 惰性函数收到的是求值器（AstEvaluator），真正的 Sheet 在它的 .sheet 上
    sh = getattr(sheet, 'sheet', sheet)
    r1, c1 = ref.r1 + dr, ref.c1 + dc
    if r1 < 0 or c1 < 0:
        return '#REF!'
    if hh == 1 and ww == 1:
        return sh.value(r1, c1)
    return [[sh.value(r1 + i, c1 + j) for j in range(ww)]
            for i in range(hh)]


def _lazy_row(sheet, r, c, args, depth):
    """ROW() 当前行；ROW(引用) 区域首行（1 起）"""
    if not args:
        return float(r + 1)
    ref = _node_ref(args[0])
    if ref is not None:
        return float(ref.r1 + 1)
    v = sheet._eval_ast(args[0], r, c, depth)
    if is_err(v):
        return v
    n = to_num(v)
    return float(int(n)) if n is not None and n >= 1 else '#REF!'


def _lazy_column(sheet, r, c, args, depth):
    """COLUMN() 当前列；COLUMN(引用) 区域首列（1 起）"""
    if not args:
        return float(c + 1)
    ref = _node_ref(args[0])
    if ref is not None:
        return float(ref.c1 + 1)
    v = sheet._eval_ast(args[0], r, c, depth)
    if is_err(v):
        return v
    n = to_num(v)
    return float(int(n)) if n is not None and n >= 1 else '#REF!'


def _lazy_rows(sheet, r, c, args, depth):
    """ROWS(区域) 行数"""
    if not args:
        return '#VALUE!'
    ref = _node_ref(args[0])
    if ref is not None:
        return float(ref.r2 - ref.r1 + 1)
    v = sheet._eval_ast(args[0], r, c, depth)
    if is_err(v):
        return v
    if isinstance(v, (list, tuple)):
        return float(len(v))
    return 1.0


def _lazy_columns(sheet, r, c, args, depth):
    """COLUMNS(区域) 列数"""
    if not args:
        return '#VALUE!'
    ref = _node_ref(args[0])
    if ref is not None:
        return float(ref.c2 - ref.c1 + 1)
    v = sheet._eval_ast(args[0], r, c, depth)
    if is_err(v):
        return v
    if isinstance(v, (list, tuple)) and v and isinstance(v[0], (list, tuple)):
        return float(len(v[0]))
    if isinstance(v, (list, tuple)):
        return float(len(v))
    return 1.0


def _lazy_isref(sheet, r, c, args, depth):
    """ISREF(引用) → TRUE；非引用 → FALSE。

    必须惰性求值：只要语法上是引用就为真，不去算它的值
    （ISREF(1/0) 是 FALSE，不是 #DIV/0!）。
    """
    if not args:
        return False
    return _node_ref(args[0]) is not None


def _lazy_cell(sheet, r, c, args, depth):
    """CELL("row"|"col"|"address", [引用])

    给了引用就报该引用的坐标，没给就报公式所在单元格的坐标。
    必须惰性求值：CELL("row", B3) 要看到 B3 这个引用本身，
    而不是先算出 B3 的值（那样只剩数字，坐标就丢了）。
    """
    if not args:
        return '#VALUE!'
    n0 = args[0]
    if isinstance(n0, (list, tuple)) and len(n0) >= 2 and n0[0] == 'str':
        what = str(n0[1]).lower()
    else:
        v = sheet._eval_ast(n0, r, c, depth)
        what = ('' if v is None else str(v)).lower()
    ref = _node_ref(args[1]) if len(args) > 1 else None
    rr, cc = (ref.r1, ref.c1) if ref is not None else (r, c)
    if what == 'row':
        return float(rr + 1)
    if what == 'col':
        return float(cc + 1)
    if what == 'address':
        t = getattr(ref, 'text', '') or ''
        if t:
            return t.split('!')[-1].split(':')[0]
        return A.rc_to_a1(rr, cc)
    return '#VALUE!'
