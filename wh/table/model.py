# -*- coding: utf-8 -*-
"""表格模块 · 数据模型层

参考开源表格项目的通行设计（OpenTable 的 表/列/行 三件套、
AG Grid 的列定义、Univer 的单元格格式），做成本项目够用的轻量版本：

    TableDef  一张表 = 若干列 + 若干行
    ColumnDef 一列 = 键 + 显示名 + 类型 + 宽度 + 对齐 + 单位 + 公式 + 格式

行数据是 dict（键 = ColumnDef.key），新增列不需要改表结构——
这是"用户能自己加列"的技术前提。
"""

# 列的数据类型
T_TEXT = 'text'
T_NUM = 'num'
T_DATE = 'date'
T_SELECT = 'select'
T_FORMULA = 'formula'

TYPES = (T_TEXT, T_NUM, T_DATE, T_SELECT, T_FORMULA)

# 数字格式
FMT_PLAIN = ''          # 原样
FMT_QTY = 'qty'         # 数量：去掉无意义的 .0，保留 2 位
FMT_MONEY = 'money'     # 金额：千分位 + 2 位
FMT_INT = 'int'         # 整数（卷数）


class ColumnDef(object):
    """一列的定义。

    key      内部键，写入数据库的字段名
    label    显示名（用户可改）
    unit     单位（可为空；有值时表头显示成「长（米）」）
    ctype    类型：text/num/date/select/formula
    width    建议列宽（字符数）
    align    对齐：left/right/center
    frozen   是否冻结（显示在左侧不随横向滚动）
    sortable / filterable
    fmt      数字格式
    options  单选类型的候选值（逗号分隔）
    formula  公式表达式（ctype=formula 时生效）
    aliases  导入时认这个列的别名
    """

    __slots__ = ('key', 'label', 'unit', 'ctype', 'width', 'align', 'frozen',
                 'sortable', 'filterable', 'fmt', 'options', 'formula',
                 'aliases', 'enabled', 'ord')

    def __init__(self, key, label=None, unit='', ctype=T_TEXT, width=10,
                 align=None, frozen=False, sortable=True, filterable=True,
                 fmt=FMT_PLAIN, options='', formula='', aliases=None,
                 enabled=True, ord=0):
        self.key = key
        self.label = label or key
        self.unit = unit or ''
        self.ctype = ctype if ctype in TYPES else T_TEXT
        self.width = int(width or 10)
        if align is None:
            align = 'right' if self.ctype in (T_NUM, T_FORMULA) else 'left'
        self.align = align
        self.frozen = bool(frozen)
        self.sortable = bool(sortable)
        self.filterable = bool(filterable)
        self.fmt = fmt or FMT_PLAIN
        self.options = options or ''
        self.formula = formula or ''
        self.aliases = list(aliases or [])
        self.enabled = bool(enabled)
        self.ord = int(ord or 0)

    # ---- 显示 ----
    @property
    def header(self):
        """表头文字：有单位就带括号"""
        return '%s（%s）' % (self.label, self.unit) if self.unit else self.label

    def fmt_val(self, v):
        """按列的格式渲染一个值"""
        return fmt_value(v, self.fmt)

    # ---- 校验 ----
    def coerce(self, v):
        """把输入变成这一列该有的类型；变不了就返回 None（= 留白）"""
        if v is None:
            return None
        s = str(v).strip()
        if s == '':
            return None
        if self.ctype in (T_NUM, T_FORMULA):
            try:
                return float(s)
            except Exception:
                try:
                    return float(s.replace(',', ''))
                except Exception:
                    return None
        if self.ctype == T_DATE:
            from ..core.util import safe_date
            return safe_date(s)
        return s

    def to_dict(self):
        return {k: getattr(self, k) for k in self.__slots__}

    @classmethod
    def from_dict(cls, d):
        d = dict(d or {})
        key = d.pop('key', '')
        return cls(key, **d)

    def __repr__(self):
        return '<Col %s:%s>' % (self.key, self.label)


class TableDef(object):
    """一张表 = 列定义 + 行数据。

    不绑定具体存储：可以是界面上的录入表、导入预览表、
    也可以是导出或月报用的临时表。
    """

    def __init__(self, name='', columns=None, rows=None, meta=None):
        self.name = name
        self.columns = list(columns or [])
        self.rows = list(rows or [])
        self.meta = dict(meta or {})

    # ---- 列操作 ----
    def col(self, key):
        for c in self.columns:
            if c.key == key:
                return c
        return None

    def add_col(self, col, at=None):
        if at is None:
            self.columns.append(col)
        else:
            self.columns.insert(at, col)
        return col

    def del_col(self, key):
        self.columns = [c for c in self.columns if c.key != key]
        for r in self.rows:
            r.pop(key, None)
        return self

    def enabled_cols(self):
        return [c for c in self.columns if c.enabled]

    def sort_cols(self):
        self.columns.sort(key=lambda c: (c.ord, c.key))
        return self

    # ---- 行操作 ----
    def add_row(self, **kw):
        row = dict((c.key, None) for c in self.columns)
        row.update(kw)
        self.rows.append(row)
        return row

    def set(self, r, key, val):
        c = self.col(key)
        self.rows[r][key] = c.coerce(val) if c else val

    def get(self, r, key, default=None):
        v = self.rows[r].get(key, default)
        return default if v is None else v

    def to_rows(self):
        """导出用：[[表头...], [值...], ...]"""
        cols = self.enabled_cols()
        out = [[c.header for c in cols]]
        for r in self.rows:
            out.append([c.fmt_val(r.get(c.key)) for c in cols])
        return out

    def __len__(self):
        return len(self.rows)

    def __repr__(self):
        return '<Table %s cols=%d rows=%d>' % (
            self.name, len(self.columns), len(self.rows))


# ---------------------------------------------------------------- 数值格式化
def fmt_value(v, fmt=FMT_PLAIN):
    """统一的数值格式化。空值一律返回空串——"没填就留白"。"""
    if v is None or v == '':
        return ''
    try:
        f = float(v)
    except Exception:
        return str(v)
    if fmt == FMT_QTY or fmt == FMT_PLAIN:
        if abs(f - round(f)) < 1e-9:
            return str(int(round(f)))
        s = ('%.6f' % f).rstrip('0').rstrip('.')
        return s
    if fmt == FMT_INT:
        return str(int(f))
    if fmt == FMT_MONEY:
        return '%s' % ('{:,.2f}'.format(f))
    return str(v)


def fmt_money(v):
    return fmt_value(v, FMT_MONEY)


def fmt_qty(v):
    return fmt_value(v, FMT_QTY)


def fmt_int(v):
    return fmt_value(v, FMT_INT)
