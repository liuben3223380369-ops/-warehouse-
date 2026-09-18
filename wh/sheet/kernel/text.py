# -*- coding: utf-8 -*-
"""表格模块 · 电子表格 函数库 · 文本函数

参数已经求值过：标量或二维列表（区域）；错误值沿调用链传播。
"""
import re
from .convert import flat, to_bool, to_num, to_text

def _concat(*a):
    return ''.join(to_text(v) for v in flat(a, skip_blank=False))


def _textjoin(*a):
    """TEXTJOIN(分隔符, 是否忽略空, 文本1, ...)"""
    sep = to_text(a[0]) if a else ''
    skip = to_bool(a[1]) if len(a) > 1 else True
    parts = [to_text(v) for v in flat(a[2:], skip_blank=bool(skip))]
    return sep.join(parts)


def _left(*a):
    s = to_text(a[0]) if a else ''
    n = int(to_num(a[1]) or 1) if len(a) > 1 else 1
    return s[:max(0, n)]


def _right(*a):
    s = to_text(a[0]) if a else ''
    n = int(to_num(a[1]) or 1) if len(a) > 1 else 1
    return s[len(s) - n:] if n > 0 else ''


def _mid(*a):
    s = to_text(a[0]) if a else ''
    start = int(to_num(a[1]) or 1) if len(a) > 1 else 1
    n = int(to_num(a[2]) or 0) if len(a) > 2 else 0
    if start < 1 or n < 0:
        return '#VALUE!'
    return s[start - 1:start - 1 + n]


def _len(*a):
    return float(len(to_text(a[0]) if a else ''))


def _lenb(*a):
    s = to_text(a[0]) if a else ''
    return float(len(s.encode('gbk', 'ignore')))


def _upper(*a):
    return to_text(a[0] if a else '').upper()


def _lower(*a):
    return to_text(a[0] if a else '').lower()


def _proper(*a):
    return re.sub(r"[A-Za-z\u4e00-\u9fff]+",
                  lambda m: m.group(0)[:1].upper() + m.group(0)[1:].lower(),
                  to_text(a[0] if a else ''))


def _trim(*a):
    return re.sub(r'\s+', ' ', to_text(a[0] if a else '').strip())


def _clean(*a):
    # Excel 的 CLEAN 删除全部 0~31 号非打印字符（含制表符与换行）
    return ''.join(ch for ch in to_text(a[0] if a else '') if ord(ch) >= 32)


def _substitute(*a):
    s = to_text(a[0]) if a else ''
    old = to_text(a[1]) if len(a) > 1 else ''
    new = to_text(a[2]) if len(a) > 2 else ''
    n = to_num(a[3]) if len(a) > 3 and a[3] not in (None, '') else None
    if not old:
        return s
    if n is None:
        return s.replace(old, new)
    out, cnt, i = [], 0, 0
    while True:
        j = s.find(old, i)
        if j < 0:
            out.append(s[i:])
            break
        cnt += 1
        out.append(s[i:j])
        out.append(new if cnt == int(n) else old)
        i = j + len(old)
    return ''.join(out)


def _replace(*a):
    s = to_text(a[0]) if a else ''
    start = int(to_num(a[1]) or 1) if len(a) > 1 else 1
    n = int(to_num(a[2]) or 0) if len(a) > 2 else 0
    new = to_text(a[3]) if len(a) > 3 else ''
    if start < 1 or n < 0:
        return '#VALUE!'
    return s[:start - 1] + new + s[start - 1 + n:]


def _find(*a):
    what = to_text(a[0]) if a else ''
    s = to_text(a[1]) if len(a) > 1 else ''
    start = int(to_num(a[2]) or 1) if len(a) > 2 and a[2] not in (None, '') else 1
    i = s.find(what, max(0, start - 1))
    return float(i + 1) if i >= 0 else '#VALUE!'


def _search(*a):
    what = to_text(a[0]) if a else ''
    s = to_text(a[1]) if len(a) > 1 else ''
    start = int(to_num(a[2]) or 1) if len(a) > 2 and a[2] not in (None, '') else 1
    i = s.lower().find(what.lower(), max(0, start - 1))
    return float(i + 1) if i >= 0 else '#VALUE!'


def _text(*a):
    """TEXT(值, 格式)：支持 0.00、#,##0.00、0%、yyyy-mm-dd 等常用写法"""
    v = a[0] if a else ''
    fmt = to_text(a[1]) if len(a) > 1 else ''
    return _fmt_text(v, fmt)


def _fmt_text(v, fmt):
    from . import style
    try:
        return style.format_value(v, fmt)
    except Exception:
        return to_text(v)


def _value(*a):
    n = to_num(a[0]) if a else None
    return n if n is not None else '#VALUE!'


def _numbervalue(*a):
    s = to_text(a[0]) if a else ''
    dec = to_text(a[1]) if len(a) > 1 and a[1] not in (None, '') else '.'
    grp = to_text(a[2]) if len(a) > 2 and a[2] not in (None, '') else ','
    s = s.replace(grp, '').replace(dec, '.').replace('%', '')
    pct = 0.01 if '%' in to_text(a[0] if a else '') else 1.0
    try:
        return float(s) * pct
    except ValueError:
        return '#VALUE!'


def _rept(*a):
    s = to_text(a[0]) if a else ''
    n = int(to_num(a[1]) or 0) if len(a) > 1 else 0
    if n < 0 or n * len(s) > 100000:
        return '#VALUE!'
    return s * n


def _exact(*a):
    return to_text(a[0] if a else '') == to_text(a[1] if len(a) > 1 else '')


def _char(*a):
    n = int(to_num(a[0]) or 0)
    if n < 1 or n > 255:
        return '#VALUE!'
    return chr(n)


def _code(*a):
    s = to_text(a[0]) if a else ''
    return float(ord(s[0])) if s else '#VALUE!'


def _t(*a):
    v = a[0] if a else ''
    return to_text(v) if isinstance(v, str) else ''


def _split_text(*a):
    s = to_text(a[0]) if a else ''
    sep = to_text(a[1]) if len(a) > 1 else ','
    return [[p] for p in s.split(sep)]


def _str_reverse(*a):
    return to_text(a[0] if a else '')[::-1]
