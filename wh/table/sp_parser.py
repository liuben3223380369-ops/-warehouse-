# -*- coding: utf-8 -*-
"""表格模块 · 电子表格 公式语法分析（递归下降 Parser）

    token 流  →  语法树（AST）

AST 用元组表示，求值在 sp_engine 里做：
    ('num', 值)
    ('str', 文本)
    ('bool', True/False)
    ('err', '#N/A')
    ('ref', Ref对象)
    ('func', 名字, [参数...])
    ('bin', 运算符, 左, 右)
    ('un',  '-'/'+'/'%', 操作数)
    ('array', [[值...], ...])

运算符优先级（跟 Excel 一致，从低到高）：
    比较 = <> < > <= >=
    &   文本连接
    + -
    * /
    ^   幂
    %   百分号
    一元 - +
    : 区域 / 空格 交集 / , 并集
"""
from . import sp_lexer as L
from .sp_addr import parse_ref, Ref, a1

BUILTIN_FUNCS = set()      # 由 sp_funcs 填充，这里先留空集合


class ParseError(Exception):
    """公式语法错误。会显示成 #NAME? 或在编辑时给出提示"""


# ------------------------------------------------------------------ 节点构造
def _num(v):
    return ('num', v)


class Parser(object):
    def __init__(self, toks, src=''):
        self.t = toks
        self.i = 0
        self.src = src

    # --- 流操作 ---
    def peek(self, k=0):
        j = self.i + k
        return self.t[j] if j < len(self.t) else None

    def at(self, kind, val=None):
        tk = self.peek()
        return tk is not None and tk.kind == kind and (val is None or tk.val == val)

    def take(self, kind=None, val=None):
        tk = self.peek()
        if tk is None:
            return None
        if kind and tk.kind != kind:
            return None
        if val is not None and tk.val != val:
            return None
        self.i += 1
        return tk

    def expect(self, kind, val=None):
        tk = self.take(kind, val)
        if tk is None:
            raise ParseError('公式写法不对：该出现 %s 的地方没有' % (val or kind))
        return tk

    # --- 入口 ---
    def parse(self):
        node = self.p_compare()
        if self.peek() is not None:
            raise ParseError('公式后面还有认不出来的内容：%r' % (self.peek().val,))
        return node

    # --- 比较（最低优先级）---
    def p_compare(self):
        left = self.p_concat()
        while True:
            tk = self.peek()
            if tk and tk.kind == 'OP' and tk.val in ('=', '<>', '<', '>', '<=', '>='):
                self.i += 1
                right = self.p_concat()
                left = ('bin', tk.val, left, right)
            else:
                return left

    def p_concat(self):
        left = self.p_addsub()
        while self.at('OP', '&'):
            self.i += 1
            left = ('bin', '&', left, self.p_addsub())
        return left

    def p_addsub(self):
        left = self.p_muldiv()
        while True:
            tk = self.peek()
            if tk and tk.kind == 'OP' and tk.val in ('+', '-'):
                self.i += 1
                left = ('bin', tk.val, left, self.p_muldiv())
            else:
                return left

    def p_muldiv(self):
        left = self.p_power()
        while True:
            tk = self.peek()
            if tk and tk.kind == 'OP' and tk.val in ('*', '/'):
                self.i += 1
                left = ('bin', tk.val, left, self.p_power())
            else:
                return left

    def p_power(self):
        # Excel 里 ^ 是右结合：2^3^2 = 2^(3^2)
        base = self.p_unary()
        if self.at('OP', '^'):
            self.i += 1
            return ('bin', '^', base, self.p_power())
        return base

    def p_unary(self):
        tk = self.peek()
        if tk and tk.kind == 'OP' and tk.val in ('-', '+'):
            self.i += 1
            return ('un', tk.val, self.p_unary())
        node = self.p_postfix()
        # 后置百分号
        while self.at('OP', '%'):
            self.i += 1
            node = ('un', '%', node)
        return node

    def p_postfix(self):
        return self.p_primary()

    def p_primary(self):
        tk = self.peek()
        if tk is None:
            raise ParseError('公式不完整')

        if tk.kind == 'NUM':
            self.i += 1
            return _num(tk.val)

        if tk.kind == 'STR':
            self.i += 1
            return ('str', tk.val)

        if tk.kind == 'BOOL':
            self.i += 1
            return ('bool', tk.val)

        if tk.kind == 'ERR':
            self.i += 1
            return ('err', tk.val)

        if tk.kind == 'ARRAY':
            self.i += 1
            return ('array', _parse_array(tk.val))

        if tk.kind == 'LP':
            self.i += 1
            node = self.p_compare()
            self.expect('RP')
            return node

        if tk.kind == 'FUNC':
            self.i += 1
            return self.p_call(tk.val)

        if tk.kind == 'REF':
            return self.p_ref()

        raise ParseError('认不出来的内容：%r' % (tk.val,))

    def p_call(self, name):
        self.expect('LP')
        args = []
        if not self.at('RP'):
            while True:
                # 允许空参数：IF(A1,,B1)
                if self.at('COMMA') or self.at('SEMI') or self.at('RP'):
                    args.append(('empty',))
                else:
                    args.append(self.p_compare())
                tk = self.peek()
                if tk and tk.kind in ('COMMA', 'SEMI'):
                    self.i += 1
                    continue
                break
        self.expect('RP')
        return ('func', name, args)

    def p_ref(self):
        """引用：可能是单元格、区域、跨表、名称，也可能后面跟着 : """
        first = self.take('REF')
        if first is None:
            raise ParseError('引用解析失败')

        # A1:B2
        if self.at('COLON'):
            self.i += 1
            nxt = self.peek()
            if nxt is None or nxt.kind not in ('REF', 'NUM'):
                raise ParseError('冒号后面缺单元格')
            if nxt.kind == 'NUM':                    # A1:5 这种少见写法
                self.i += 1
                right = a1(int(nxt.val) - 1, 0)
            else:
                self.i += 1
                right = nxt.val
            merged = _merge_range(first.val, right)
            ref = parse_ref(merged)
            if ref is None:
                raise ParseError('认不出的区域：%s:%s' % (first.val, right))
            return ('ref', ref)

        ref = parse_ref(first.val)
        if ref is None:
            # 不是合法地址，就当命名区域（可能是用户自己起的名字）
            ref = Ref('name', name=first.val)
        return ('ref', ref)


def _merge_range(a, b):
    """把 'A1' 和 'B2' 合成 'A1:B2'；跨表时只保留一次表名"""
    sa, ba = _split(a)
    sb, bb = _split(b)
    sheet = sa or sb
    pre = (sheet + '!') if sheet else ''
    return pre + ba + ':' + bb


def _split(s):
    if s.startswith("'"):
        m = L._re.match(r"^'((?:[^']|'')*)'!(.*)$", s)
        if m:
            return m.group(1), m.group(2)
    if '!' in s:
        i = s.index('!')
        return s[:i], s[i + 1:]
    return '', s


def _parse_array(body):
    """解析数组常量 {1,2;3,4} → [[1,2],[3,4]]"""
    rows = []
    for rline in body.split(';'):
        row = []
        for item in rline.split(','):
            item = item.strip()
            if item == '':
                row.append(None)
            elif item.startswith('"') and item.endswith('"') and len(item) >= 2:
                row.append(item[1:-1].replace('""', '"'))
            else:
                up = item.upper()
                if up == 'TRUE':
                    row.append(True)
                elif up == 'FALSE':
                    row.append(False)
                else:
                    try:
                        row.append(float(item))
                    except ValueError:
                        row.append(item)
        rows.append(row)
    return rows


# ------------------------------------------------------------------ 对外接口
_CACHE = {}


def parse(src):
    """公式文本 → AST。语法错误抛 ParseError"""
    if src in _CACHE:
        return _CACHE[src]
    toks = L.tokenize(src)
    if not toks:
        raise ParseError('公式是空的')
    node = Parser(toks, src).parse()
    if len(_CACHE) > 2000:
        _CACHE.clear()
    _CACHE[src] = node
    return node


def try_parse(src):
    """不抛异常的 parse，失败返回 None"""
    try:
        return parse(src)
    except Exception:
        return None


def refs_of(node, out=None):
    """收集 AST 里用到的所有引用（给依赖图用）"""
    if out is None:
        out = []
    if not isinstance(node, tuple):
        return out
    k = node[0]
    if k == 'ref':
        out.append(node[1])
    elif k == 'bin':
        refs_of(node[2], out)
        refs_of(node[3], out)
    elif k == 'un':
        refs_of(node[2], out)
    elif k == 'func':
        for a in node[2]:
            refs_of(a, out)
    return out


def funcs_of(node, out=None):
    """收集 AST 里用到的函数名"""
    if out is None:
        out = set()
    if not isinstance(node, tuple):
        return out
    k = node[0]
    if k == 'func':
        out.add(node[1])
        for a in node[2]:
            funcs_of(a, out)
    elif k == 'bin':
        funcs_of(node[2], out)
        funcs_of(node[3], out)
    elif k == 'un':
        funcs_of(node[2], out)
    return out
