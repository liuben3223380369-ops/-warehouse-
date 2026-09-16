# -*- coding: utf-8 -*-
"""表格模块 · 公式引擎

自研的安全求值器：**不用 eval**，只接受白名单里的语法结构。
用户填的公式可能来自导入的 Excel，不能给它执行任意代码的能力。

支持的语法
    数字        12、3.5
    字段引用    qty、price、long、wide（表里已有的列 key）
    四则        + - * / % **
    比较        > < >= <= == !=
    逻辑        and or not
    条件        IF(cond, a, b)
    函数        SUM AVG COUNT MIN MAX ROUND INT CEIL FLOOR ABS MOD SQRT POW
                TEXT CONCAT LEN

求值规则
    * 字段缺失 / 为空 → 当作 0 参与运算
    * 除零 → 返回 0（不抛异常，一张表里有一行除零不该让整页挂掉）
    * 任何非法结构 → 返回 None（调用方显示留白）
"""
import ast as _ast
import math

# 允许出现在公式里的函数
_FUNCS = {
    'ABS': lambda *a: abs(_n(a[0])),
    'ROUND': lambda *a: round(_n(a[0]), int(_n(a[1])) if len(a) > 1 else 2),
    'INT': lambda *a: int(_n(a[0])),
    'CEIL': lambda *a: int(math.ceil(_n(a[0]))),
    'FLOOR': lambda *a: int(math.floor(_n(a[0]))),
    'SQRT': lambda *a: math.sqrt(max(0.0, _n(a[0]))),
    'POW': lambda *a: _n(a[0]) ** _n(a[1]),
    'MOD': lambda *a: (_n(a[0]) % _n(a[1])) if _n(a[1]) else 0.0,
    'MIN': lambda *a: min([_n(x) for x in a]) if a else 0.0,
    'MAX': lambda *a: max([_n(x) for x in a]) if a else 0.0,
    'SUM': lambda *a: sum(_n(x) for x in a),
    'AVG': lambda *a: (sum(_n(x) for x in a) / len(a)) if a else 0.0,
    'COUNT': lambda *a: float(len(a)),
    'LEN': lambda *a: float(len(str(a[0]) if a else '')),
    'TEXT': lambda *a: str(a[0]) if a else '',
    'CONCAT': lambda *a: ''.join(str(x) for x in a),
}

# 允许的条件函数（惰性求值，不能预先把参数都算出来）
_LAZY = {'IF'}


def _n(v):
    """把值变成数字；变不了就 0"""
    if v is None or v == '':
        return 0.0
    try:
        return float(v)
    except Exception:
        try:
            return float(str(v).replace(',', ''))
        except Exception:
            return 0.0


def _norm_name(s):
    """公式里的字段名规整化：去空格、大小写不敏感、常见别名归一"""
    s = (s or '').strip()
    low = s.lower()
    _ALIAS = {
        'qty': 'qty', 'quantity': 'qty', '数量': 'qty', 'num': 'qty',
        'price': 'price', '单价': 'price', 'up': 'price',
        'amount': 'amount', '金额': 'amount', 'total': 'amount',
        'long': 'long', 'length': 'long', '长': 'long', '规格': 'long',
        'wide': 'wide', 'width': 'wide', '宽': 'wide', '宽幅': 'wide',
        'sqm': 'sqm', '平米': 'sqm', '面积': 'sqm',
        'rolls': 'rolls', '卷料': 'rolls', '卷数': 'rolls',
        'conv': 'conv', '换算率': 'conv',
        'pieces': 'pieces', '件数': 'pieces',
        'per': 'per', '每件': 'per',
    }
    return _ALIAS.get(low, low)


class _SafeEval(_ast.NodeVisitor):
    """只走白名单节点的 AST 求值器"""

    def __init__(self, values):
        self.v = values or {}

    # 字段引用
    def visit_Name(self, node):
        key = _norm_name(node.id)
        # 先精确命中，再退到规整化后的键
        if node.id in self.v:
            return self.v[node.id]
        for k in self.v:
            if _norm_name(k) == key:
                return self.v[k]
        return 0.0

    def visit_Constant(self, node):
        return node.value

    def visit_BinOp(self, node):
        left = self.visit(node.left)
        right = self.visit(node.right)
        op = node.op
        try:
            if isinstance(op, _ast.Add):
                # 有一边是字符串就当拼接
                if isinstance(left, str) or isinstance(right, str):
                    return '%s%s' % (left, right)
                return _n(left) + _n(right)
            if isinstance(op, _ast.Sub):
                return _n(left) - _n(right)
            if isinstance(op, _ast.Mult):
                return _n(left) * _n(right)
            if isinstance(op, _ast.Div):
                r = _n(right)
                return (_n(left) / r) if r else 0.0
            if isinstance(op, _ast.Mod):
                r = _n(right)
                return (_n(left) % r) if r else 0.0
            if isinstance(op, _ast.Pow):
                try:
                    return _n(left) ** _n(right)
                except Exception:
                    return 0.0
        except Exception:
            return 0.0
        raise ValueError('不支持的运算符')

    def visit_UnaryOp(self, node):
        v = self.visit(node.operand)
        if isinstance(node.op, _ast.USub):
            return -_n(v)
        if isinstance(node.op, _ast.UAdd):
            return _n(v)
        if isinstance(node.op, _ast.Not):
            return not v
        raise ValueError('不支持的一元运算')

    def visit_Compare(self, node):
        left = self.visit(node.left)
        for op, comp in zip(node.ops, node.comparators):
            right = self.visit(comp)
            ok = self._cmp(op, left, right)
            if not ok:
                return False
            left = right
        return True

    @staticmethod
    def _cmp(op, a, b):
        # 一边是数字就按数字比
        na, nb = _n(a), _n(b)
        use_num = not (isinstance(a, str) and isinstance(b, str))
        a, b = (na, nb) if use_num else (a, b)
        if isinstance(op, _ast.Gt):
            return a > b
        if isinstance(op, _ast.GtE):
            return a >= b
        if isinstance(op, _ast.Lt):
            return a < b
        if isinstance(op, _ast.LtE):
            return a <= b
        if isinstance(op, _ast.Eq):
            return a == b
        if isinstance(op, _ast.NotEq):
            return a != b
        return False

    def visit_BoolOp(self, node):
        vals = [self.visit(x) for x in node.values]
        if isinstance(node.op, _ast.And):
            return all(bool(v) for v in vals)
        if isinstance(node.op, _ast.Or):
            return any(bool(v) for v in vals)
        return False

    def visit_IfExp(self, node):
        return (self.visit(node.body) if self.visit(node.test)
                else self.visit(node.orelse))

    def visit_Call(self, node):
        fname = getattr(node.func, 'id', '') or ''
        up = fname.upper()
        if up in _LAZY:
            if up == 'IF':
                if len(node.args) < 3:
                    return None
                cond = self.visit(node.args[0])
                return (self.visit(node.args[1]) if cond
                        else self.visit(node.args[2]))
        if up not in _FUNCS:
            raise ValueError('不支持的函数：%s' % fname)
        args = [self.visit(a) for a in node.args]
        try:
            return _FUNCS[up](*args)
        except Exception:
            return None

    def generic_visit(self, node):
        raise ValueError('公式里有不支持的写法')


def evaluate(expr, values=None):
    """求值一个公式表达式。

    :param expr:   'qty*price' 这类字符串
    :param values: {'qty': 10, 'price': 3.5}
    :return: 数字 / 字符串；算不出来返回 None（调用方显示留白）
    """
    if not expr:
        return None
    s = str(expr).strip()
    if not s:
        return None
    # 没有运算符也没有函数，当作纯字段引用
    try:
        tree = _ast.parse(s, mode='eval')
    except SyntaxError:
        return None
    except Exception:
        return None
    try:
        return _SafeEval(values or {}).visit(tree.body)
    except Exception:
        return None


def check(expr):
    """语法体检：返回 (是否合法, 说明)"""
    if not (expr or '').strip():
        return True, ''
    try:
        tree = _ast.parse(str(expr).strip(), mode='eval')
    except SyntaxError as e:
        return False, '公式写错了：%s' % e.msg
    except Exception as e:
        return False, '公式无法解析：%s' % e

    bad = []

    class _V(_ast.NodeVisitor):
        def visit_Call(self, node):
            fn = getattr(node.func, 'id', '') or ''
            if fn.upper() not in _FUNCS and fn.upper() not in _LAZY:
                bad.append(fn)
            self.generic_visit(node)

        def generic_visit(self, node):
            allowed = (_ast.Expression, _ast.BinOp, _ast.UnaryOp, _ast.Name,
                       _ast.Constant, _ast.Load, _ast.Add, _ast.Sub, _ast.Mult,
                       _ast.Div, _ast.Mod, _ast.Pow, _ast.USub, _ast.UAdd,
                       _ast.Compare, _ast.Gt, _ast.GtE, _ast.Lt, _ast.LtE,
                       _ast.Eq, _ast.NotEq, _ast.BoolOp, _ast.And, _ast.Or,
                       _ast.Not, _ast.IfExp, _ast.Call, _ast.Tuple)
            if not isinstance(node, allowed):
                bad.append(type(node).__name__)
            super(_V, self).generic_visit(node)

    try:
        _V().visit(tree)
    except Exception:
        pass
    if bad:
        return False, '不支持的写法：%s' % '、'.join(sorted(set(bad))[:3])
    return True, ''


def used_fields(expr):
    """公式里引用了哪些字段"""
    if not expr:
        return []
    try:
        tree = _ast.parse(str(expr), mode='eval')
    except Exception:
        return []
    out = []

    class _V(_ast.NodeVisitor):
        def visit_Name(self, node):
            out.append(_norm_name(node.id))
    try:
        _V().visit(tree)
    except Exception:
        pass
    return out
