# -*- coding: utf-8 -*-
"""表格模块 · 电子表格 地址体系（A1 表示法）

跟 Excel / WPS 保持一致：
    列    A B C ... Z AA AB ... （可超过 26 列）
    行    1 2 3 ...
    单元格 A1、$A$1（$ 表示绝对引用，填充/复制时不跟随偏移）
    区域   A1:C10
    跨表   Sheet1!A1、'我的 表'!A1:B2
    名称   单价、Total（命名区域，大小写不敏感）

只做"地址怎么表示、怎么解析"，不算值。
"""
import re

MAX_ROW = 1048576        # 跟 Excel 一致
MAX_COL = 16384          # Excel 最大列 XFD

# 新建工作表的默认网格（只是“初始画多大”，不是上限；超出照常可用）
DEFAULT_ROWS = 1000
DEFAULT_COLS = 200

_LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'

# A1 或 $A$1
_RE_CELL = re.compile(r'^(\$?)([A-Za-z]{1,3})(\$?)([0-9]{1,7})$')
# 纯列 $A:A
_RE_COL = re.compile(r'^(\$?)([A-Za-z]{1,3}):(\$?)([A-Za-z]{1,3})$')
# 纯行 $1:2
_RE_ROW = re.compile(r'^(\$?)([0-9]{1,7}):(\$?)([0-9]{1,7})$')
# 名称（中文/字母/下划线开头）
_RE_NAME = re.compile(r'^[^\W\d][\w\u4e00-\u9fff\.]*$', re.UNICODE)


# ---------------------------------------------------------------- 列号 <-> 字母
def col_letter(idx):
    """列索引（0 起）→ 字母。0→A，25→Z，26→AA"""
    if idx < 0:
        raise ValueError('列号不能为负：%r' % idx)
    s = ''
    idx += 1                       # 转成 1 起的"Excel 列"
    while idx:
        idx, r = divmod(idx - 1, 26)
        s = _LETTERS[r] + s
    return s


def col_index(s):
    """字母 → 列索引（0 起）。A→0，AA→26"""
    if isinstance(s, int):
        return s
    s = (s or '').strip().upper()
    if not s or not re.match(r'^[A-Z]{1,3}$', s):
        raise ValueError('不是合法的列标：%r' % s)
    n = 0
    for ch in s:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


# ---------------------------------------------------------------- A1 构造
def a1(row, col, abs_row=False, abs_col=False):
    """(行,列) → 'A1' 字符串。row/col 均为 0 起"""
    return '%s%s%s%d' % ('$' if abs_col else '', col_letter(col),
                         '$' if abs_row else '', row + 1)


def rc_to_a1(row, col):
    return a1(row, col)


def a1_to_rc(s):
    """'A1' → (row, col)，均为 0 起"""
    m = _RE_CELL.match((s or '').strip())
    if not m:
        raise ValueError('不是合法的单元格地址：%r' % s)
    return int(m.group(4)) - 1, col_index(m.group(2))


# ---------------------------------------------------------------- 引用解析
class Ref(object):
    """一个解析好的引用。kind: cell / range / col / row / name"""

    __slots__ = ('kind', 'sheet', 'r1', 'c1', 'r2', 'c2',
                 'ar1', 'ac1', 'ar2', 'ac2', 'name')

    def __init__(self, kind, sheet='', r1=0, c1=0, r2=0, c2=0,
                 ar1=False, ac1=False, ar2=False, ac2=False, name=''):
        self.kind = kind
        self.sheet = sheet or ''
        self.r1, self.c1, self.r2, self.c2 = r1, c1, r2, c2
        self.ar1, self.ac1, self.ar2, self.ac2 = ar1, ac1, ar2, ac2
        self.name = name or ''

    def __repr__(self):
        if self.kind == 'name':
            return '<Ref %s!%s>' % (self.sheet, self.name)
        pre = (self.sheet + '!') if self.sheet else ''
        if self.kind == 'cell':
            return '<Ref %s%s>' % (pre, a1(self.r1, self.c1, self.ar1, self.ac1))
        return '<Ref %s%s:%s>' % (pre,
                                  a1(self.r1, self.c1, self.ar1, self.ac1),
                                  a1(self.r2, self.c2, self.ar2, self.ac2))

    def size(self):
        if self.kind == 'cell':
            return 1, 1
        return (abs(self.r2 - self.r1) + 1, abs(self.c2 - self.c1) + 1)

    def cells(self, limit=200000):
        """遍历区域里的所有 (row, col)。整列引用会限制在已用范围内由调用方处理"""
        n = self.size()[0] * self.size()[1]
        if n > limit:
            raise ValueError('区域太大（%d 个单元格）' % n)
        rlo, rhi = sorted((self.r1, self.r2))
        clo, chi = sorted((self.c1, self.c2))
        for r in range(rlo, rhi + 1):
            for c in range(clo, chi + 1):
                yield r, c

    def shift(self, dr, dc, allow_abs=True):
        """复制/填充时的偏移。带 $ 的方向不动"""
        o = Ref(self.kind, self.sheet, self.r1, self.c1, self.r2, self.c2,
                self.ar1, self.ac1, self.ar2, self.ac2, self.name)
        if self.kind == 'name':
            return o
        if not (allow_abs and self.ar1):
            o.r1 += dr
        if not (allow_abs and self.ar2):
            o.r2 += dr
        if not (allow_abs and self.ac1):
            o.c1 += dc
        if not (allow_abs and self.ac2):
            o.c2 += dc
        return o

    def to_str(self):
        pre = (self.sheet + '!') if self.sheet else ''
        if self.kind == 'name':
            return pre + self.name
        if self.kind == 'cell':
            return pre + a1(self.r1, self.c1, self.ar1, self.ac1)
        return '%s%s:%s' % (pre, a1(self.r1, self.c1, self.ar1, self.ac1),
                            a1(self.r2, self.c2, self.ar2, self.ac2))


def _split_sheet(s):
    """拆出 'Sheet1!A1' 的表名部分。支持单引号包裹的表名"""
    if '!' not in s:
        return '', s
    if s.startswith("'"):
        m = re.match(r"^'((?:[^']|'')*)'!(.*)$", s)
        if m:
            return m.group(1).replace("''", "'"), m.group(2)
    i = s.index('!')
    return s[:i], s[i + 1:]


def parse_ref(text):
    """把一个 token 解析成 Ref。解析不了返回 None"""
    s = (text or '').strip()
    if not s:
        return None
    sheet, body = _split_sheet(s)
    body = body.strip()
    if not body:
        return None

    # 区域 A1:B2
    if ':' in body:
        a, _, b = body.partition(':')
        ca, cb = _RE_CELL.match(a.strip()), _RE_CELL.match(b.strip())
        if ca and cb:
            return Ref('range', sheet,
                       int(ca.group(4)) - 1, col_index(ca.group(2)),
                       int(cb.group(4)) - 1, col_index(cb.group(2)),
                       ca.group(3) == '$', ca.group(1) == '$',
                       cb.group(3) == '$', cb.group(1) == '$')
        ma, mb = _RE_COL.match(body), _RE_ROW.match(body)
        if ma:                                   # A:C 整列
            return Ref('col', sheet, 0, col_index(ma.group(2)),
                       MAX_ROW - 1, col_index(ma.group(4)))
        if mb:                                   # 1:5 整行
            return Ref('row', sheet, int(mb.group(2)) - 1, 0,
                       int(mb.group(4)) - 1, MAX_COL - 1)
        return None

    m = _RE_CELL.match(body)
    if m:
        r, c = int(m.group(4)) - 1, col_index(m.group(2))
        return Ref('cell', sheet, r, c, r, c,
                   m.group(3) == '$', m.group(1) == '$',
                   m.group(3) == '$', m.group(1) == '$')
    ma = re.match(r'^(\$?)([A-Za-z]{1,3})$', body)
    if ma:                                       # 单个列标 A
        c = col_index(ma.group(2))
        return Ref('col', sheet, 0, c, MAX_ROW - 1, c)
    if re.match(r'^[0-9]{1,7}$', body):          # 单个行号 3
        r = int(body) - 1
        return Ref('row', sheet, r, 0, r, MAX_COL - 1)
    if _RE_NAME.match(body):
        return Ref('name', sheet, name=body)
    return None


def looks_like_ref(s):
    """给解析器判断：这个标识符是不是一个引用（而不是函数名/名称）"""
    try:
        return parse_ref(s) is not None
    except Exception:
        return False


def range_of(r1, c1, r2, c2, sheet=''):
    lo_r, hi_r = sorted((r1, r2))
    lo_c, hi_c = sorted((c1, c2))
    return Ref('range', sheet, lo_r, lo_c, hi_r, hi_c)


def norm_rect(a, b):
    """把两个对角单元格规范成左上角→右下角"""
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))


# ------------------------------------------------------------------ 引用偏移
import re as _re

# A1 引用：可带 $ 绝对标记，可带 表名! 前缀
_REF_RE = _re.compile(
    r"(?:(?:'([^']+)'|([A-Za-z_\u4e00-\u9fa5][A-Za-z0-9_\u4e00-\u9fa5.]*))!)?"
    r"(\$?)([A-Za-z]{1,3})(\$?)([0-9]{1,7})")


def shift_formula(text, dr, dc, max_row=1048576, max_col=16384):
    """把公式里的相对引用整体平移 (dr, dc)，用于复制/填充。

    带 $ 的行或列不跟着动；双引号里的字符串原样保留；
    函数名（LOG10、SUM 之类）不会被误当成单元格地址。"""
    s = text or ''
    if not s or (dr == 0 and dc == 0) or '=' not in s and not _REF_RE.search(s):
        return s
    out = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == '"':                       # 字符串常量：整段照抄
            j = i + 1
            while j < n:
                if s[j] == '"':
                    if j + 1 < n and s[j + 1] == '"':
                        j += 2
                        continue
                    break
                j += 1
            out.append(s[i:min(j + 1, n)])
            i = j + 1
            continue
        m = _REF_RE.match(s, i)
        if m:
            # 函数名保护：后面紧跟 ( 说明是函数不是地址（LOG10( 里的 LOG10）
            nxt = s[m.end():m.end() + 1]
            if nxt == '(':
                out.append(ch)
                i += 1
                continue
            ar, ac = m.group(3), m.group(5)
            col = col_index(m.group(4))
            row = int(m.group(6)) - 1
            nc = col if ac else max(0, min(max_col - 1, col + dc))
            nr = row if ar else max(0, min(max_row - 1, row + dr))
            out.append((m.group(1) or m.group(2) or '') and
                       (("'%s'!" % m.group(1)) if m.group(1)
                        else ('%s!' % m.group(2))) or '')
            out.append('%s%s%s%d' % (ar, col_letter(nc), ac, nr + 1))
            i = m.end()
            continue
        out.append(ch)
        i += 1
    return ''.join(out)
