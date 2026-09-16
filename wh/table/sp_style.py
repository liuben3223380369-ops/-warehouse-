# -*- coding: utf-8 -*-
"""表格模块 · 数字格式与单元格样式

数字格式（兼容 Excel 格式代码的常用子集）：
    General / 常规          原样显示
    0                       整数（四舍五入）
    0.00 / #,##0.00         固定小数、千分位
    0% / 0.00%              百分比
    ¥#,##0.00 / $#,##0.00   货币
    yyyy-mm-dd / yyyy年m月d日 / mm-dd / hh:mm:ss   日期时间
    @                       文本
    0.00;[红色]-0.00        正数;负数;零;文本 四段

样式：字体（粗/斜/下划线/大小/颜色）、填充、对齐、边框、换行、冻结
"""
import re
import math
import datetime as _dt

from .sp_funcs import to_num, is_err, to_text, to_bool

# Excel 日期起点
_EPOCH = _dt.date(1899, 12, 30)

# 预置格式（界面下拉里直接选）
PRESET_FORMATS = [
    ('', '常规'),
    ('0', '整数'),
    ('0.00', '两位小数'),
    ('#,##0.00', '千分位'),
    ('0%', '百分比'),
    ('0.00%', '百分比两位'),
    ('¥#,##0.00', '货币'),
    ('$#,##0.00', '美元'),
    ('yyyy-mm-dd', '日期'),
    ('yyyy-mm-dd hh:mm', '日期时间'),
    ('hh:mm:ss', '时间'),
    ('@', '文本'),
]

_COLOR_WORDS = {'红色': 'red', '黑色': 'black', '蓝色': 'blue',
                '绿色': 'green', '黄色': 'yellow', '白色': 'white',
                'RED': 'red', 'BLACK': 'black', 'BLUE': 'blue',
                'GREEN': 'green', 'YELLOW': 'yellow', 'WHITE': 'white'}


# ------------------------------------------------------------------ 数字格式
def _split_sections(fmt):
    """'正;负;零;文本' → 四段"""
    parts = []
    buf = []
    i, n = 0, len(fmt)
    while i < n:
        ch = fmt[i]
        if ch == '"':
            j = fmt.find('"', i + 1)
            if j < 0:
                buf.append(ch); i += 1; continue
            buf.append(fmt[i:j + 1]); i = j + 1; continue
        if ch == '[':
            j = fmt.find(']', i + 1)
            if j < 0:
                buf.append(ch); i += 1; continue
            buf.append(fmt[i:j + 1]); i = j + 1; continue
        if ch == ';':
            parts.append(''.join(buf)); buf = []; i += 1; continue
        buf.append(ch); i += 1
    parts.append(''.join(buf))
    while len(parts) < 4:
        parts.append('')
    return parts[:4]


def _fmt_number(v, code):
    """按一段格式代码渲染一个数字"""
    if code == '' or code.upper() in ('GENERAL', '常规'):
        return _general(v)

    # 取出颜色标记
    color = None
    m = re.search(r'\[(红色|黑色|蓝色|绿色|黄色|白色|RED|BLACK|BLUE|GREEN|'
                  r'YELLOW|WHITE)\]', code, re.I)
    if m:
        color = _COLOR_WORDS.get(m.group(1).upper(), _COLOR_WORDS.get(m.group(1)))
        code = code.replace(m.group(0), '')

    # 文字字面量（"元"）
    lits = []
    def _stash(mm):
        lits.append(mm.group(1))
        return '\x01%d\x01' % (len(lits) - 1)
    code = re.sub(r'"([^"]*)"', _stash, code)

    pct = '%' in code
    if pct:
        v = v * 100.0
        code = code.replace('%', '')

    code = code.replace('¥', '').replace('$', '').strip()

    # 小数位数
    dec = 0
    if '.' in code:
        frac = code.split('.', 1)[1]
        dec = len(re.findall(r'[0#?]', frac))
        int_part = code.split('.', 1)[0]
    else:
        int_part = code

    comma = ',' in int_part
    m0 = re.search(r'[0#?]+', int_part)
    min_int = 0
    if m0:
        body = m0.group(0)
        min_int = body.count('0')

    neg = v < 0
    av = abs(v)
    try:
        s = ('%.' + str(dec) + 'f') % av
    except (OverflowError, ValueError):
        s = str(av)
    if dec > 0:
        ip, fp = s.split('.')
    else:
        ip, fp = s, ''

    if comma:
        ip = _group(ip)
    if min_int and len(ip.lstrip('-')) < min_int:
        ip = '0' * (min_int - len(ip)) + ip

    out = ip + ('.' + fp if dec > 0 else '')
    if neg:
        out = '-' + out
    if pct:
        out += '%'

    for i, l in enumerate(lits):
        out = out.replace('\x01%d\x01' % i, l)

    return (out, color) if color else out


def _group(s):
    neg = s.startswith('-')
    if neg:
        s = s[1:]
    out = []
    for i, ch in enumerate(reversed(s)):
        if i and i % 3 == 0:
            out.append(',')
        out.append(ch)
    r = ''.join(reversed(out))
    return ('-' + r) if neg else r


def _general(v):
    if v is None or v == '':
        return ''
    if isinstance(v, bool):
        return 'TRUE' if v else 'FALSE'
    if isinstance(v, (int, float)):
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return str(v)
        if abs(v - round(v)) < 1e-9 and abs(v) < 1e15:
            return str(int(round(v)))
        s = '%.10f' % v
        s = s.rstrip('0').rstrip('.')
        return s if s not in ('', '-') else '0'
    return str(v)


def _fmt_date(v, code):
    """日期时间格式"""
    sn = to_num(v)
    if sn is None:
        return to_text(v)
    days = int(sn)
    frac = sn - days
    try:
        d = _EPOCH + _dt.timedelta(days=days)
        t = _dt.datetime.combine(d, _dt.time()) + \
            _dt.timedelta(seconds=frac * 86400)
    except (OverflowError, ValueError):
        return to_text(v)

    def rep(mm):
        tok = mm.group(0)
        n = len(tok)
        low = tok[0] in 'ymdhs'
        c = tok[0].lower()
        if c == 'y':
            return str(t.year)[-min(n, 4):].rjust(min(n, 4), '0') \
                if n <= 3 else str(t.year)
        if c == 'm':
            if n >= 3:
                names = ['一月', '二月', '三月', '四月', '五月', '六月', '七月',
                         '八月', '九月', '十月', '十一月', '十二月']
                return names[t.month - 1]
            return str(t.month).rjust(min(n, 2), '0') if n == 2 else str(t.month)
        if c == 'd':
            if n >= 4:
                wd = ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五',
                      '星期六']
                return wd[t.weekday() if t.weekday() < 6 else 6]
            return str(t.day).rjust(min(n, 2), '0') if n == 2 else str(t.day)
        if c == 'h':
            return str(t.hour).rjust(min(n, 2), '0')
        if c == 's':
            return str(t.second).rjust(min(n, 2), '0')
        if tok[0] == 'm' and n >= 3:
            return str(t.minute)
        return tok

    # 分钟要跟月区分：h 或 s 后面的 m 是分钟
    code2 = re.sub(r'(?<=[hH])[^a-zA-Z]*(m+)', lambda mm: mm.group(1), code)
    out = re.sub(r'(yyyy|yy|y|mmmm|mmm|mm|m|dddd|ddd|dd|d|HH|hh|h|ss|s)',
                 rep, code2)
    return out


def is_date_code(code):
    return bool(re.search(r'[ymdhs]', code or '', re.I)) and \
        not re.search(r'[0#]', code or '')


def format_value(v, fmt=''):
    """把任意值按格式代码渲染成显示文本。返回 str（可能带颜色元组）"""
    if is_err(v):
        return str(v)
    if v is None or v == '':
        return ''
    if isinstance(v, bool):
        return 'TRUE' if v else 'FALSE'
    if isinstance(v, str) and fmt and not is_date_code(fmt):
        n = to_num(v)
        if n is not None and re.match(r'^[\d\.\-+%,¥$ ]+$', v):
            v = n
        else:
            return v
    if isinstance(v, str):
        return v

    fmt = (fmt or '').strip()
    if not fmt or fmt.upper() in ('GENERAL', '常规'):
        return _general(v)
    if fmt == '@':
        return to_text(v)
    if is_date_code(fmt):
        return _fmt_date(v, fmt)

    secs = _split_sections(fmt)
    if v > 0:
        code = secs[0] or secs[1] or ''
    elif v < 0:
        code = secs[1] or ('-' + secs[0] if secs[0] else '')
        if not code and secs[0]:
            code = '-' + secs[0]
    else:
        code = secs[2] or secs[0] or ''
    if not code:
        return _general(v)
    r = _fmt_number(v, code)
    # [红色] 这类标记会让 _fmt_number 返回 (文本, 颜色)；
    # 给界面用的显示文本只要文本本身，颜色由单元格样式单独走
    return r[0] if isinstance(r, tuple) else r


def display(v, fmt='', colored=False):
    """渲染 + 可选返回颜色"""
    r = format_value(v, fmt)
    if isinstance(r, tuple):
        return r if colored else r[0]
    return (r, None) if colored else r


# ------------------------------------------------------------------ 样式
#: 边框线型 → CSS。与 Excel / WPS 的边框下拉一致
BORDER_STYLES = [
    ('none', '无'), ('thin', '细线'), ('hair', '发丝线'), ('dotted', '点线'),
    ('dashed', '虚线'), ('dashdot', '点划线'), ('medium', '中粗线'),
    ('thick', '粗线'), ('double', '双线'),
]

_BD_CSS = {
    'thin': '1px solid', 'hair': '1px dotted', 'dotted': '1px dotted',
    'dashed': '1px dashed', 'dashdot': '1px dashed', 'medium': '2px solid',
    'thick': '3px solid', 'double': '3px double',
}

#: 字体下拉。中文放前面——这个程序主要记中文物料
FONTS = ['', '宋体', '微软雅黑', '黑体', '楷体', '仿宋',
         'Arial', 'Calibri', 'Times New Roman', 'Consolas', 'Verdana']

#: 字号下拉
SIZES = [0, 8, 9, 10, 11, 12, 14, 16, 18, 20, 22, 24, 28, 36, 48, 72]


def _css_font(name):
    """字体名 → CSS。含空格或中文的要加引号，否则 CSS 解析会断"""
    n = (name or '').strip()
    if not n:
        return ''
    if ' ' in n or any('\u4e00' <= ch <= '\u9fff' for ch in n):
        return '"%s"' % n.replace('"', '')
    return n


def _bd_pair(v):
    """单边边框值 → (线型, 颜色)。兼容 ['thin','#000'] 和 'thin' 两种写法"""
    if isinstance(v, (list, tuple)):
        s = v[0] if v else 'thin'
        c = v[1] if len(v) > 1 else ''
        return (s or 'thin', c or '#000000')
    return (str(v or 'thin'), '#000000')


def _bd_css(sty):
    """生成边框 CSS。细致边框 bd 优先，没有再退回 border 简写"""
    out = []
    bd = getattr(sty, 'bd', None)
    if bd:
        for k, prop in (('t', 'border-top'), ('b', 'border-bottom'),
                        ('l', 'border-left'), ('r', 'border-right')):
            if not bd.get(k):
                continue
            s, c = _bd_pair(bd[k])
            if s and s != 'none':
                out.append('%s:%s %s' % (prop, _BD_CSS.get(s, '1px solid'), c))
        if out:
            return out
    b = getattr(sty, 'border', '')
    if b and b != 'none':
        if b.startswith('#'):        # 旧版：border 存的是颜色
            out.append('border:1px solid %s' % b)
        else:
            out.append('border:1px solid #c8cdd4')
    return out


class Style(object):
    """单元格样式。所有字段都可空（= 继承默认）"""
    __slots__ = ('bold', 'italic', 'underline', 'strike', 'size', 'color',
                 'bg', 'align', 'valign', 'wrap', 'border', 'fmt',
                 'font', 'indent', 'bd', 'rotate')

    def __init__(self, bold=False, italic=False, underline=False, strike=False,
                 size=0, color='', bg='', align='', valign='', wrap=False,
                 border='', fmt='', font='', indent=0, bd=None, rotate=0):
        self.bold = bold
        self.italic = italic
        self.underline = underline
        self.strike = strike
        self.size = int(size or 0)
        self.color = color or ''
        self.bg = bg or ''
        self.align = align or ''      # left/center/right
        self.valign = valign or ''    # top/middle/bottom
        self.wrap = bool(wrap)
        self.border = border or ''    # 简写：all/box/top/bottom/left/right/none
        self.fmt = fmt or ''
        self.font = font or ''        # 字体名，如 宋体 / 微软雅黑
        self.indent = int(indent or 0)        # 缩进级别 0-15
        self.bd = dict(bd) if bd else None    # 细致边框 {'t':[线型,色],...}
        self.rotate = int(rotate or 0)        # 文字角度（度），负数为逆时针

    def merge(self, other):
        """把 other 里非空的字段盖到自己身上（用于区域刷格式）"""
        if not other:
            return self
        s = Style(**{k: getattr(self, k) for k in self.__slots__})
        for k in self.__slots__:
            v = getattr(other, k)
            if v not in (None, '', False, 0):
                setattr(s, k, v)
        return s

    def css(self, is_num=False):
        out = []
        if self.bold:
            out.append('font-weight:600')
        if self.italic:
            out.append('font-style:italic')
        if self.underline:
            out.append('text-decoration:underline')
        if self.strike:
            out.append('text-decoration:line-through')
        if self.font:
            out.append('font-family:%s' % _css_font(self.font))
        if self.size:
            out.append('font-size:%dpx' % self.size)
        if self.color:
            out.append('color:%s' % self.color)
        if self.bg:
            out.append('background:%s' % self.bg)
        al = self.align or ('right' if is_num else 'left')
        out.append('text-align:%s' % al)
        if self.valign:
            out.append('vertical-align:%s' % self.valign)
        if self.wrap:
            out.append('white-space:normal')
        if self.indent:
            out.append('padding-left:%dpx' % (int(self.indent) * 9))
        out += _bd_css(self)
        if self.rotate:
            out.append('transform:rotate(%ddeg)' % int(self.rotate))
        return ';'.join(out)

    def to_dict(self):
        return {k: getattr(self, k) for k in self.__slots__}

    @staticmethod
    def from_dict(d):
        return Style(**{k: v for k, v in (d or {}).items()
                        if k in Style.__slots__})


DEFAULT_STYLE = Style()


# ------------------------------------------------------------------ 条件格式
class CondRule(object):
    """条件格式规则

    ctype:  cellIs(比较) / contains / between / top / bottom /
            above / below / duplicate / unique / formula / dataBar / colorScale
    """
    __slots__ = ('ctype', 'op', 'v1', 'v2', 'style', 'range', 'color', 'rank')

    def __init__(self, ctype='cellIs', op='gt', v1=None, v2=None,
                 style=None, range=None, color='', rank=10):
        self.ctype = ctype
        self.op = op
        self.v1 = v1
        self.v2 = v2
        self.style = style or Style()
        self.range = range
        self.color = color or ''
        self.rank = int(rank or 10)

    def to_dict(self):
        return {'ctype': self.ctype, 'op': self.op, 'v1': self.v1,
                'v2': self.v2, 'style': self.style.to_dict(),
                'range': self.range, 'color': self.color, 'rank': self.rank}

    @staticmethod
    def from_dict(d):
        d = dict(d or {})
        st = Style.from_dict(d.pop('style', None))
        return CondRule(style=st, **{k: v for k, v in d.items()
                                     if k in CondRule.__slots__
                                     and k != 'style'})


_OPS = {
    'gt': lambda a, b: a > b,
    'lt': lambda a, b: a < b,
    'ge': lambda a, b: a >= b,
    'le': lambda a, b: a <= b,
    'eq': lambda a, b: abs(a - b) < 1e-9,
    'ne': lambda a, b: abs(a - b) >= 1e-9,
}


def cond_match(rule, v, ctx=None):
    """判断一个值是否命中条件格式"""
    n = to_num(v)
    if rule.ctype == 'cellIs':
        b = to_num(rule.v1)
        if n is None or b is None:
            return False
        f = _OPS.get(rule.op)
        return bool(f(n, b)) if f else False
    if rule.ctype == 'between':
        a, b = to_num(rule.v1), to_num(rule.v2)
        return n is not None and a is not None and b is not None \
            and min(a, b) <= n <= max(a, b)
    if rule.ctype == 'contains':
        return to_text(rule.v1).lower() in to_text(v).lower()
    if rule.ctype in ('above', 'below'):
        return n is not None and (n > 0 if rule.ctype == 'above' else n < 0)
    if rule.ctype == 'formula':
        return bool(to_bool(rule.v1))
    return False
