# -*- coding: utf-8 -*-
"""表格模块 · 电子表格引擎（工作簿 / 工作表 / 单元格 / 重算）

这是"像 Excel / WPS"那一层的内核：

    Workbook  一本工作簿 = 多张 Sheet + 命名区域
    Sheet     一张表 = 稀疏单元格字典 + 列宽行高 + 冻结 + 合并 + 筛选 + 条件格式
    Cell      一格 = 原文(raw) / 类型 / 值 / 样式 / 数字格式 / 批注 / 有效性

重算策略
    惰性 DFS + 记忆化：要哪个格子算哪个，算过的记下来。
    碰到自己依赖自己（A1=A1+1）返回 #CIRC!，不无限递归。
    改一格只把它下游的缓存清掉，不用整表重算。

安全
    公式只走自写的词法/语法分析器 + 白名单函数表，
    不 eval、不 exec、不碰内置命名空间。
"""
import copy
import re

from . import sp_addr as A
from . import sp_lexer as LX
from . import sp_parser as P
from . import sp_funcs as F
from .sp_style import Style, CondRule, format_value, is_date_code
from .sp_funcs import to_num, to_text, to_bool, is_err

MAX_CELLS = 400000          # 单表单元格上限，防止被恶意公式撑爆


class Cell(object):
    __slots__ = ('row', 'col', 'raw', 'kind', 'ast', 'v', 'fmt', 'style',
                 'note', 'valid', 'bad')

    def __init__(self, row=0, col=0, raw=''):
        self.row = row
        self.col = col
        self.raw = raw or ''
        self.kind = 'blank'
        self.ast = None
        self.v = None
        self.fmt = ''
        self.style = None
        self.note = ''
        self.valid = None
        self.bad = ''          # 公式解析错误信息

    @property
    def addr(self):
        return A.a1(self.row, self.col)

    def display(self):
        """按数字格式渲染显示文本"""
        if is_err(self.v):
            return str(self.v)
        return format_value(self.v, self.fmt)

    def to_dict(self, with_value=True):
        d = {'r': self.row, 'c': self.col, 'raw': self.raw,
             'kind': self.kind, 'fmt': self.fmt,
             'note': self.note}
        if self.style:
            d['style'] = self.style.to_dict()
        if with_value:
            d['v'] = _jsonable(self.v)
            d['text'] = self.display()
        if self.bad:
            d['bad'] = self.bad
        return d


def _jsonable(v):
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v
    if v is None:
        return ''
    return str(v)


# ------------------------------------------------------------------ 工作表
class Sheet(object):
    def __init__(self, name='Sheet1', rows=200, cols=26):
        self.name = name
        self.cells = {}           # (r,c) -> Cell
        self.rows = rows
        self.cols = cols
        self.col_width = {}       # c -> 字符宽
        self.row_height = {}      # r -> 像素
        self.frozen_rows = 0
        self.frozen_cols = 0
        self.merges = []          # [(r1,c1,r2,c2)]
        self.filter = None        # {'r1','c1','r2','c2','hide':[行...]}
        self.cond = []            # [CondRule]
        self.sort_spec = []       # [(col, asc)]
        self.tab_color = ''
        self.hidden = False
        self.gridlines = True
        self.book = None
        # 类 Excel 的几样：隐藏行列、数据有效性、图表
        self.hidden_rows = set()      # {行号}
        self.hidden_cols = set()      # {列号}
        self.validations = []         # [{rect, type, op, v1, v2, items, msg}]
        self.charts = []              # [{type,title,rect,...}]

    # ---------------- 基础存取 ----------------
    def cell(self, r, c, create=False):
        k = (r, c)
        cl = self.cells.get(k)
        if cl is None and create:
            if len(self.cells) >= MAX_CELLS:
                return None
            cl = Cell(r, c)
            self.cells[k] = cl
        return cl

    def get(self, r, c):
        """取单元格（没有就返回 None，不创建）"""
        return self.cells.get((r, c))

    def raw(self, r, c):
        cl = self.cells.get((r, c))
        return cl.raw if cl else ''

    def set_raw(self, r, c, text):
        """写入一格。返回 (单元格, 是否变化)"""
        text = '' if text is None else str(text)
        cl = self.cell(r, c, create=True)
        if cl is None:
            return None, False
        old = cl.raw
        if old == text:
            return cl, False
        cl.raw = text
        cl.bad = ''
        cl.ast = None
        self._classify(cl)
        self.invalidate(r, c)
        if r + 1 >= self.rows:
            self.rows = r + 2
        if c + 1 >= self.cols:
            self.cols = c + 2
        return cl, True

    def _classify(self, cl):
        """判断一格是公式 / 数字 / 文本 / 布尔 / 错误"""
        s = (cl.raw or '').strip()
        if s == '':
            cl.kind = 'blank'
            cl.v = None
            return
        if s.startswith('='):
            cl.kind = 'formula'
            cl.v = None
            try:
                cl.ast = P.parse(s)
            except Exception as e:
                cl.ast = None
                cl.bad = str(e)
                cl.v = '#NAME?'
            return
        if s.startswith("'"):                      # 强制文本
            cl.kind = 'str'
            cl.v = s[1:]
            return
        up = s.upper()
        if up in ('TRUE', 'FALSE', '真', '假'):
            cl.kind = 'bool'
            cl.v = up in ('TRUE', '真')
            return
        if up in LX.ERRORS:
            cl.kind = 'err'
            cl.v = up
            return
        # 数字（含 1,234.5 / 50% / 科学计数）
        t = s.replace(',', '').replace('，', '')
        if re.match(r'^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?%?$', t):
            if t.endswith('%'):
                cl.kind = 'num'
                cl.v = float(t[:-1]) / 100.0
            else:
                try:
                    cl.kind = 'num'
                    cl.v = float(t)
                except ValueError:
                    cl.kind = 'str'
                    cl.v = s
            return
        # 日期
        if re.match(r'^\d{4}[-/年.]\d{1,2}[-/月.]\d{1,2}', s):
            sn = F.to_serial(s)
            if sn is not None:
                cl.kind = 'num'
                cl.v = sn
                if not cl.fmt:
                    cl.fmt = 'yyyy-mm-dd'
                return
        cl.kind = 'str'
        cl.v = s

    # ---------------- 依赖与重算 ----------------
    def invalidate(self, r=None, c=None):
        """清掉缓存。不指定坐标就整表清"""
        self._cache = {}
        if r is not None:
            self._stack = set()

    _cache = None
    _stack = None

    def _ensure(self):
        if self._cache is None:
            self._cache = {}
        if self._stack is None:
            self._stack = set()
        return self._cache

    def value(self, r, c, _depth=0):
        """取一格的值（会算公式）"""
        cache = self._ensure()
        k = (r, c)
        if k in cache:
            return cache[k]
        cl = self.cells.get(k)
        if cl is None:
            return None
        if cl.kind != 'formula':
            cache[k] = cl.v
            return cl.v
        if k in self._stack:
            return '#CIRC!'
        if _depth > 200:
            return '#CIRC!'
        self._stack.add(k)
        try:
            v = self._eval_ast(cl.ast, r, c, _depth) if cl.ast \
                else (cl.v if cl.v is not None else '#NAME?')
        except RecursionError:
            v = '#CIRC!'
        except Exception:
            v = '#VALUE!'
        finally:
            self._stack.discard(k)
        cache[k] = v
        return v

    # ---------------- 求值 ----------------
    def _ctx(self, r, c):
        return FnCtx(self, r, c)

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
        sh = self
        if ref.sheet:
            sh = (self.book.sheet(ref.sheet) if self.book else None)
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
        return fn(self._ctx(r, c), *args)

    # ---------------- 范围 ----------------
    def used_range(self):
        if not self.cells:
            return (0, 0, 0, 0)
        rs = [k[0] for k in self.cells]
        cs = [k[1] for k in self.cells]
        return (min(rs), min(cs), max(rs), max(cs))

    def max_used_row(self):
        return max((k[0] for k in self.cells), default=0)

    def max_used_col(self):
        return max((k[1] for k in self.cells), default=0)

    def region(self, r1, c1, r2, c2):
        r1, c1, r2, c2 = A.norm_rect(r1, c1, r2, c2)
        return [[(self.cells.get((i, j)).v if self.cells.get((i, j)) else None)
                 for j in range(c1, c2 + 1)] for i in range(r1, r2 + 1)]

    def grid(self, r1, c1, r2, c2, text=False):
        """取出一块区域用于渲染。text=True 返回显示文本"""
        r1, c1, r2, c2 = A.norm_rect(r1, c1, r2, c2)
        out = []
        for i in range(r1, r2 + 1):
            row = []
            for j in range(c1, c2 + 1):
                cl = self.cells.get((i, j))
                if cl is None:
                    row.append('')
                    continue
                if cl.kind == 'formula':
                    v = self.value(i, j)
                else:
                    v = cl.v
                row.append(cl.display() if (text and cl.kind != 'formula')
                           else (format_value(v, cl.fmt) if text else v))
            out.append(row)
        return out

    # ---------------- 结构编辑 ----------------
    def insert_rows(self, at, n=1):
        for i in range(self.rows - 1, at - 1, -1):
            for j in range(self.cols):
                cl = self.cells.pop((i, j), None)
                if cl:
                    cl.row = i + n
                    self.cells[(i + n, j)] = cl
        self.rows += n
        self.invalidate()

    def delete_rows(self, at, n=1):
        for i in range(at, self.rows):
            for j in range(self.cols):
                cl = self.cells.pop((i, j), None)
                if cl and i - n >= at:
                    cl.row = i - n
                    self.cells[(i - n, j)] = cl
        self.rows = max(2, self.rows - n)
        self.invalidate()

    def insert_cols(self, at, n=1):
        for j in range(self.cols - 1, at - 1, -1):
            for i in range(self.rows):
                cl = self.cells.pop((i, j), None)
                if cl:
                    cl.col = j + n
                    self.cells[(i, j + n)] = cl
        self.cols += n
        self.invalidate()

    def delete_cols(self, at, n=1):
        for j in range(at, self.cols):
            for i in range(self.rows):
                cl = self.cells.pop((i, j), None)
                if cl and j - n >= at:
                    cl.col = j - n
                    self.cells[(i, j - n)] = cl
        self.cols = max(2, self.cols - n)
        self.invalidate()

    # ---------------- 序列化 ----------------
    def to_dict(self, r1=0, c1=0, r2=None, c2=None):
        r2 = self.max_used_row() if r2 is None else r2
        c2 = self.max_used_col() if c2 is None else c2
        cells = []
        for i in range(r1, r2 + 1):
            for j in range(c1, c2 + 1):
                cl = self.cells.get((i, j))
                if cl and (cl.raw or cl.style or cl.note or cl.fmt):
                    cells.append(cl.to_dict())
        return {'name': self.name, 'rows': self.rows, 'cols': self.cols,
                'cells': cells, 'col_width': self.col_width,
                'row_height': self.row_height,
                'frozen_rows': self.frozen_rows,
                'frozen_cols': self.frozen_cols,
                'merges': self.merges, 'filter': self.filter,
                'cond': [x.to_dict() for x in self.cond],
                'tab_color': self.tab_color, 'hidden': self.hidden,
                'hidden_rows': sorted(self.hidden_rows),
                'hidden_cols': sorted(self.hidden_cols),
                'validations': self.validations,
                'charts': self.charts}

    @staticmethod
    def from_dict(d, book=None):
        s = Sheet(d.get('name') or 'Sheet1',
                  int(d.get('rows') or 200), int(d.get('cols') or 26))
        s.book = book
        for cd in (d.get('cells') or []):
            cl = Cell(int(cd.get('r') or 0), int(cd.get('c') or 0),
                      cd.get('raw') or '')
            cl.fmt = cd.get('fmt') or ''
            cl.note = cd.get('note') or ''
            if cd.get('style'):
                cl.style = Style.from_dict(cd['style'])
            s._classify(cl)
            s.cells[(cl.row, cl.col)] = cl
        s.col_width = {int(k): v for k, v in (d.get('col_width') or {}).items()}
        s.row_height = {int(k): v for k, v in (d.get('row_height') or {}).items()}
        s.frozen_rows = int(d.get('frozen_rows') or 0)
        s.frozen_cols = int(d.get('frozen_cols') or 0)
        s.merges = [tuple(x) for x in (d.get('merges') or [])]
        s.filter = d.get('filter')
        s.cond = [CondRule.from_dict(x) for x in (d.get('cond') or [])]
        s.tab_color = d.get('tab_color') or ''
        s.hidden = bool(d.get('hidden'))
        try:
            s.hidden_rows = set(int(x) for x in (d.get('hidden_rows') or []))
            s.hidden_cols = set(int(x) for x in (d.get('hidden_cols') or []))
        except (TypeError, ValueError):
            s.hidden_rows, s.hidden_cols = set(), set()
        s.validations = list(d.get('validations') or [])
        s.charts = list(d.get('charts') or [])
        s.invalidate()
        return s


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
    pairs = (len(args) + 1) // 2 if agg == 'sum' else len(args) // 2
    rngs, crits = [], []
    for i in range(pairs):
        rv = _crit_range(sheet, args[i * 2], r, c, depth)
        if is_err(rv):
            return rv
        rngs.append(rv)
        crits.append(_crit_matcher(sheet._eval_ast(args[i * 2 + 1], r, c, depth)))
    n = min(len(x) for x in rngs) if rngs else 0
    if agg == 'sum':
        src = _crit_range(sheet, args[-1], r, c, depth)
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


def _lazy_aggregate(sheet, r, c, args, depth):
    """AGGREGATE(功能码, 选项, 区域) —— 这里实现忽略错误的常用情形"""
    return _lazy_subtotal(sheet, r, c, args, depth)


LAZY_IMPL = {
    'IF': _lazy_if, 'IFERROR': _lazy_iferror, 'IFNA': _lazy_ifna,
    'IFS': _lazy_ifs, 'SWITCH': _lazy_switch,
    'SUMIF': _lazy_sumif, 'COUNTIF': _lazy_countif,
    'AVERAGEIF': _lazy_averageif,
    'SUMIFS': _lazy_sumifs, 'COUNTIFS': _lazy_countifs,
    'AVERAGEIFS': _lazy_averageifs, 'MAXIFS': _lazy_maxifs,
    'MINIFS': _lazy_minifs,
    'SUBTOTAL': _lazy_subtotal, 'AGGREGATE': _lazy_aggregate,
}
F.LAZY = set(LAZY_IMPL)


# ------------------------------------------------------------------ 工作簿
class Workbook(object):
    def __init__(self, name='工作簿1'):
        self.name = name
        self.sheets = []          # [Sheet]
        self.names = {}           # 命名区域：名称 -> 'Sheet1!A1:B2'
        self.active = 0

    # ---------------- 表管理 ----------------
    def add(self, name=None, rows=200, cols=26):
        name = name or self._next_name()
        s = Sheet(name, rows, cols)
        s.book = self
        self.sheets.append(s)
        return s

    def _next_name(self):
        i = len(self.sheets) + 1
        while any(s.name == 'Sheet%d' % i for s in self.sheets):
            i += 1
        return 'Sheet%d' % i

    def sheet(self, name):
        if isinstance(name, int):
            return self.sheets[name] if 0 <= name < len(self.sheets) else None
        n = str(name).strip().lower()
        for s in self.sheets:
            if s.name.strip().lower() == n:
                return s
        return None

    @property
    def act(self):
        if not self.sheets:
            self.add('Sheet1')
        if self.active >= len(self.sheets):
            self.active = 0
        return self.sheets[self.active]

    def rename(self, old, new):
        s = self.sheet(old)
        if not s or not new or self.sheet(new):
            return False
        s.name = new
        for k, v in list(self.names.items()):
            if str(v).split('!')[0].lower() == str(old).lower():
                self.names[k] = new + '!' + str(v).split('!', 1)[1]
        for sh in self.sheets:
            sh.invalidate()
        return True

    def remove(self, name):
        s = self.sheet(name)
        if not s or len(self.sheets) <= 1:
            return False
        self.sheets.remove(s)
        self.active = min(self.active, len(self.sheets) - 1)
        return True

    def order(self, names):
        """调整表顺序"""
        mp = {s.name: s for s in self.sheets}
        out = [mp[n] for n in names if n in mp]
        for s in self.sheets:
            if s not in out:
                out.append(s)
        self.sheets = out

    # ---------------- 命名区域 ----------------
    def define_name(self, name, ref):
        if not name or not ref:
            return False
        self.names[str(name).strip()] = str(ref).strip()
        for s in self.sheets:
            s.invalidate()
        return True

    def del_name(self, name):
        return self.names.pop(str(name).strip(), None) is not None

    def value_of_name(self, name):
        ref = self.names.get(name) or self.names.get(name.lower())
        if not ref:
            return '#NAME?'
        r = A.parse_ref(ref)
        if r is None:
            return '#NAME?'
        return self.act._eval_ref(r, 0, 0, 0)

    def resolve(self, ref_text):
        """把 'Sheet1!A1' / 'A1:B2' / 命名 解析成 (sheet, Ref)"""
        r = A.parse_ref(ref_text)
        if r is None:
            return None, None
        sh = self.sheet(r.sheet) if r.sheet else self.act
        return sh, r

    # ---------------- 序列化 ----------------
    def to_dict(self):
        return {'name': self.name, 'active': self.active,
                'names': self.names,
                'sheets': [s.to_dict() for s in self.sheets]}

    @staticmethod
    def from_dict(d):
        b = Workbook(d.get('name') or '工作簿1')
        b.names = dict(d.get('names') or {})
        for sd in (d.get('sheets') or []):
            b.sheets.append(Sheet.from_dict(sd, b))
        if not b.sheets:
            b.add('Sheet1')
        b.active = min(int(d.get('active') or 0), len(b.sheets) - 1)
        return b

    # ---------------- 便捷 ----------------
    def set(self, addr, text, sheet=None):
        sh = self.sheet(sheet) if sheet else self.act
        r = A.parse_ref(addr)
        if r is None or r.kind != 'cell':
            return False
        sh.set_raw(r.r1, r.c1, text)
        return True

    def val(self, addr, sheet=None):
        sh = self.sheet(sheet) if sheet else self.act
        r = A.parse_ref(addr)
        if r is None or r.kind != 'cell':
            return None
        return sh.value(r.r1, r.c1)
