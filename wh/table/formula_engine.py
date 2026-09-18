# -*- coding: utf-8 -*-
"""公式求值层（可插拔）

把「AST 求值」从 sp_engine 的 Workbook/Sheet 数据模型里剥离出来，
使公式链成为一层独立实现::

    sheet._evaluator = AstEvaluator(sheet)
    sheet.eval(node, r, c)  ->  值

这样 Workbook 只负责单元格/样式/区域等数据模型，
公式语义（函数表、惰性函数、运算符、引用解析）全部收敛在本文件。
将来要换成别的公式实现（或接入 Univer 的计算结果），
只需替换 AstEvaluator，数据模型不用动。
"""
import math
import re

from . import sp_addr as A          # noqa: F401  地址解析（A.a1 / A.parse_ref）
from . import sp_funcs as F         # noqa: F401  函数表 + 类型转换
from . import sp_lexer as LX        # noqa: F401  错误字面量
from .sp_funcs import to_num, to_text, to_bool, is_err   # noqa: F401


def _clip_used(sh, r1, c1, r2, c2):
    """整行/整列引用裁到已用范围，避免遍历上百万格"""
    ur = sh.used_range()
    if sh.cells:
        r1 = max(r1, ur[0]) if r2 > ur[2] else r1
        c1 = max(c1, ur[1]) if c2 > ur[3] else c1
        r2 = min(r2, ur[2])
        c2 = min(c2, ur[3])
    else:
        r1, c1, r2, c2 = 0, 0, 0, 0
    return r1, c1, max(r1, r2), max(c1, c2)


# ------------------------------------------------------------------ 求值上下文
class FnCtx(object):
    """给需要"知道自己在哪"的函数用：ROW / COLUMN / OFFSET / INDIRECT / CELL"""

    def __init__(self, sheet, row, col):
        self.sheet = sheet
        self.row = row
        self.col = col

    @property
    def addr(self):
        return A.a1(self.row, self.col)

    def offset(self, args):
        base = args[0] if args else None
        dr = int(to_num(args[1]) or 0) if len(args) > 1 else 0
        dc = int(to_num(args[2]) or 0) if len(args) > 2 else 0
        h = int(to_num(args[3]) or 1) if len(args) > 3 and args[3] not in (None, '') else 1
        w = int(to_num(args[4]) or 1) if len(args) > 4 and args[4] not in (None, '') else 1
        if not isinstance(base, (list, tuple)) or not base:
            return '#REF!'
        rows = [r if isinstance(r, (list, tuple)) else [r] for r in base]
        r1, c1 = self.row + dr, self.col + dc
        if h == 1 and w == 1:
            return self.sheet.value(r1, c1)
        return [[self.sheet.value(r1 + i, c1 + j) for j in range(w)]
                for i in range(h)]

    def indirect(self, text):
        ref = A.parse_ref(to_text(text))
        if ref is None:
            return '#REF!'
        return self.sheet._eval_ref(ref, self.row, self.col, 0)

    def ref_row(self, v):
        if isinstance(v, (list, tuple)) and v:
            v = v[0]
            if isinstance(v, (list, tuple)) and v:
                v = v[0]
        ref = A.parse_ref(to_text(v))
        return float((ref.r1 if ref else 0) + 1)

    def ref_col(self, v):
        if isinstance(v, (list, tuple)) and v:
            v = v[0]
            if isinstance(v, (list, tuple)) and v:
                v = v[0]
        ref = A.parse_ref(to_text(v))
        return float((ref.c1 if ref else 0) + 1)


# ------------------------------------------------------------------ 运算符
def _binop(op, a, b):
    if op == '&':
        return to_text(a) + to_text(b)
    na, nb = to_num(a), to_num(b)
    if op in ('=', '<>', '<', '>', '<=', '>='):
        # 同类型比同类型；数字优先
        if na is not None and nb is not None:
            x, y = na, nb
        else:
            x, y = to_text(a).lower(), to_text(b).lower()
        return {'=': x == y, '<>': x != y, '<': x < y,
                '>': x > y, '<=': x <= y, '>=': x >= y}[op]
    if na is None:
        na = 0.0
    if nb is None:
        nb = 0.0
    if op == '+':
        return na + nb
    if op == '-':
        return na - nb
    if op == '*':
        return na * nb
    if op == '/':
        return na / nb if nb else '#DIV/0!'
    if op == '^':
        try:
            r = na ** nb
        except (OverflowError, ValueError, ZeroDivisionError):
            return '#NUM!'
        if isinstance(r, complex):
            return '#NUM!'
        return float(r)
    return '#NAME?'


# ------------------------------------------------------------------ 惰性函数
def _crit_matcher(crit):
    """'>10' / '<>苹果' / '5' / '苹果' → 判断函数"""
    if crit is None or crit == '':
        return lambda v: True
    if isinstance(crit, (int, float)) and not isinstance(crit, bool):
        return lambda v: abs((to_num(v) or 0) - crit) < 1e-9
    s = to_text(crit).strip()
    m = re.match(r'^(>=|<=|<>|>|<|=)(.*)$', s)
    if m:
        op, val = m.group(1), m.group(2).strip()
        nv = to_num(val)
        if '*' in val or '?' in val:
            pat = re.compile('^' + re.escape(val).replace(r'\*', '.*')
                             .replace(r'\?', '.') + '$', re.I)
            return lambda v: bool(pat.match(to_text(v)))
        if nv is not None:
            return {'>': lambda v: (to_num(v) or 0) > nv,
                    '<': lambda v: (to_num(v) or 0) < nv,
                    '>=': lambda v: (to_num(v) or 0) >= nv,
                    '<=': lambda v: (to_num(v) or 0) <= nv,
                    '=': lambda v: abs((to_num(v) or 0) - nv) < 1e-9,
                    '<>': lambda v: abs((to_num(v) or 0) - nv) >= 1e-9}[op]
        sv = val.lower()
        return {'>': lambda v: to_text(v).lower() > sv,
                '<': lambda v: to_text(v).lower() < sv,
                '>=': lambda v: to_text(v).lower() >= sv,
                '<=': lambda v: to_text(v).lower() <= sv,
                '=': lambda v: to_text(v).lower() == sv,
                '<>': lambda v: to_text(v).lower() != sv}[op]
    if '*' in s or '?' in s:
        pat = re.compile('^' + re.escape(s).replace(r'\*', '.*')
                         .replace(r'\?', '.') + '$', re.I)
        return lambda v: bool(pat.match(to_text(v)))
    n = to_num(s)
    if n is not None:
        return lambda v: abs((to_num(v) or 0) - n) < 1e-9
    sl = s.lower()
    return lambda v: to_text(v).lower() == sl


def _crit_range(sheet, node, r, c, depth):
    """取 SUMIF 的第一参数，返回一维值列表"""
    v = sheet._eval_ast(node, r, c, depth)
    if is_err(v):
        return v
    out = []

    def walk(x):
        if isinstance(x, (list, tuple)):
            for y in x:
                walk(y)
        else:
            out.append(x)
    walk(v)
    return out


def _lazy_if(sheet, r, c, args, depth):
    cond = sheet._eval_ast(args[0], r, c, depth) if args else False
    if is_err(cond):
        return cond
    b = to_bool(cond)
    if b is None:
        return '#VALUE!'
    if b:
        if len(args) < 2:
            return True
        return sheet._eval_ast(args[1], r, c, depth) \
            if isinstance(args[1], tuple) else args[1]
    if len(args) < 3:
        return False
    return sheet._eval_ast(args[2], r, c, depth) \
        if isinstance(args[2], tuple) else args[2]


def _lazy_iferror(sheet, r, c, args, depth):
    v = sheet._eval_ast(args[0], r, c, depth) if args else None
    if is_err(v):
        return sheet._eval_ast(args[1], r, c, depth) if len(args) > 1 else ''
    return v


def _lazy_ifna(sheet, r, c, args, depth):
    v = sheet._eval_ast(args[0], r, c, depth) if args else None
    if v == '#N/A':
        return sheet._eval_ast(args[1], r, c, depth) if len(args) > 1 else ''
    return v


def _lazy_ifs(sheet, r, c, args, depth):
    for i in range(0, len(args) - 1, 2):
        cv = sheet._eval_ast(args[i], r, c, depth)
        if is_err(cv):
            return cv
        if to_bool(cv):
            return sheet._eval_ast(args[i + 1], r, c, depth)
    return '#N/A'


def _lazy_switch(sheet, r, c, args, depth):
    if not args:
        return '#N/A'
    key = sheet._eval_ast(args[0], r, c, depth)
    for i in range(1, len(args) - 1, 2):
        if _same_val(sheet._eval_ast(args[i], r, c, depth), key):
            return sheet._eval_ast(args[i + 1], r, c, depth)
    if len(args) % 2 == 0:
        return sheet._eval_ast(args[-1], r, c, depth)
    return '#N/A'


def _same_val(a, b):
    na, nb = to_num(a), to_num(b)
    if na is not None and nb is not None:
        return abs(na - nb) < 1e-9
    return to_text(a).lower() == to_text(b).lower()


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


def _node_ref(node):
    """从 AST 节点取出引用对象；不是引用则返回 None。

    ROW/COLUMN/ROWS/COLUMNS 必须看到「引用本身」而不是引用求出来的值，
    所以它们要惰性求值 —— 否则 ROW(B3) 拿到的是 B3 单元格里的数。
    """
    if isinstance(node, (list, tuple)) and len(node) >= 2 and node[0] == 'ref':
        return node[1]
    return None


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
F.LAZY = set(LAZY_IMPL)

# ------------------------------------------------------------------ 求值器
class AstEvaluator(object):
    """AST 求值器。宿主是 Sheet，通过 self.sheet 访问单元格。"""

    def __init__(self, sheet):
        self.sheet = sheet

    def _ctx(self, r, c):
        return FnCtx(self.sheet, r, c)

    def _eval_ast(self, node, r, c, depth=0):
        if node is None:
            return None
        k = node[0]
        if k == 'num' or k == 'str' or k == 'bool' or k == 'err':
            return node[1]
        if k == 'empty':
            return None
        if k == 'array':
            return node[1]
        if k == 'ref':
            return self._eval_ref(node[1], r, c, depth)
        if k == 'un':
            return self._eval_un(node, r, c, depth)
        if k == 'bin':
            return self._eval_bin(node, r, c, depth)
        if k == 'func':
            return self._eval_func(node, r, c, depth)
        return '#NAME?'

    def _eval_un(self, node, r, c, depth):
        op, operand = node[1], node[2]
        v = self._eval_ast(operand, r, c, depth)
        if is_err(v):
            return v
        if op == '%':
            n = to_num(v)
            return (n / 100.0) if n is not None else '#VALUE!'
        n = to_num(v)
        if n is None:
            return '#VALUE!'
        return -n if op == '-' else n

    def _eval_bin(self, node, r, c, depth):
        op, l, rr = node[1], node[2], node[3]
        a = self._eval_ast(l, r, c, depth)
        b = self._eval_ast(rr, r, c, depth)
        if is_err(a):
            return a
        if is_err(b):
            return b
        return _binop(op, a, b)

    def _eval_ref(self, ref, r, c, depth):
        """引用 → 值。1×1 返回标量，区域返回二维列表"""
        sh = self.sheet
        if ref.sheet:
            sh = (self.sheet.book.sheet(ref.sheet) if self.sheet.book else None)
            if sh is None:
                return '#REF!'
        if ref.kind == 'name':
            return sh.book.value_of_name(ref.name) if sh.book else '#NAME?'
        r1, c1, r2, c2 = ref.r1, ref.c1, ref.r2, ref.c2
        if ref.kind in ('col', 'row'):
            r1, c1, r2, c2 = _clip_used(sh, r1, c1, r2, c2)
        if r1 == r2 and c1 == c2:
            return sh.value(r1, c1, depth + 1)
        if (r2 - r1 + 1) * (c2 - c1 + 1) > 200000:
            return '#REF!'
        return [[sh.value(i, j, depth + 1) for j in range(c1, c2 + 1)]
                for i in range(r1, r2 + 1)]

    def _eval_func(self, node, r, c, depth):
        name, argnodes = node[1], node[2]
        if name in LAZY_IMPL:
            return LAZY_IMPL[name](self, r, c, argnodes, depth)
        fn = F.FUNCS.get(name)
        if fn is None:
            return '#NAME?'
        args = [self._eval_ast(a, r, c, depth) for a in argnodes]
        return fn(self.sheet._ctx(r, c), *args)


def make_evaluator(sheet):
    """工厂：为 Sheet 创建求值器。

    单一取点——将来换公式实现只改这里。
    """
    return AstEvaluator(sheet)
