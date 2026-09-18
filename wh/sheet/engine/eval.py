# -*- coding: utf-8 -*-
"""公式求值层（可插拔）

把「AST 求值」从数据模型里剥离出来，成为一层独立实现::

    sheet._evaluator = AstEvaluator(sheet)
    sheet.eval(node, r, c)  ->  值

这样 Workbook/Sheet 只负责单元格、样式、区域等数据模型，
公式语义（运算符、引用解析、惰性分发）收敛在 engine 层。
将来换公式实现只需替换 make_evaluator，数据模型不用动。"""
from ..kernel import funcs as F         # noqa: F401  函数表
from ..kernel import to_num, is_err
from .ctx import FnCtx, _binop, _clip_used
from .lazy import LAZY_IMPL

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
