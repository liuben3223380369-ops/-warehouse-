# -*- coding: utf-8 -*-
"""求值原语：上下文、运算符、条件匹配、引用取点

本模块是最底层，不依赖任何惰性函数，只依赖 kernel。"""
import re

from ..kernel import addr as A          # noqa: F401  地址解析
from ..kernel import to_num, to_text, is_err   # noqa: F401

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

def _same_val(a, b):
    na, nb = to_num(a), to_num(b)
    if na is not None and nb is not None:
        return abs(na - nb) < 1e-9
    return to_text(a).lower() == to_text(b).lower()

def _node_ref(node):
    """从 AST 节点取出引用对象；不是引用则返回 None。

    ROW/COLUMN/ROWS/COLUMNS 必须看到「引用本身」而不是引用求出来的值，
    所以它们要惰性求值 —— 否则 ROW(B3) 拿到的是 B3 单元格里的数。
    """
    if isinstance(node, (list, tuple)) and len(node) >= 2 and node[0] == 'ref':
        return node[1]
    return None
