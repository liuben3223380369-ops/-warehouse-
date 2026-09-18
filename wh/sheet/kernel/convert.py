# -*- coding: utf-8 -*-
"""表格模块 · 电子表格 函数库 · 类型转换与比较原语

Excel 日期起点 1899-12-30（兼容 Excel 把 1900 当闰年的历史 bug）。
"""
import math
import re
import datetime as _dt
from functools import reduce

_EPOCH = _dt.date(1899, 12, 30)
# Excel 的日期起点：1899-12-30（为了兼容 Excel 把 1900 当闰年的历史 bug）
_EPOCH = _dt.date(1899, 12, 30)

ERRORS = ('#NULL!', '#DIV/0!', '#VALUE!', '#REF!', '#NAME?', '#NUM!',
          '#N/A', '#CIRC!', '#SPILL!', '#GETTING_DATA')


def is_err(v):
    return isinstance(v, str) and v in ERRORS


def is_blank(v):
    return v is None or v == ''


# ------------------------------------------------------------------ 取值工具
def flat(args, skip_blank=True, skip_err=False):
    """把参数摊平成一维列表（区域 → 逐格）"""
    out = []

    def walk(x):
        if isinstance(x, (list, tuple)):
            for y in x:
                walk(y)
        else:
            if skip_err and is_err(x):
                return
            if skip_blank and is_blank(x):
                return
            out.append(x)
    for a in args:
        walk(a)
    return out


def nums(args, skip_text=True):
    """摊平后只留数字"""
    out = []
    for v in flat(args):
        n = to_num(v)
        if n is None:
            if skip_text:
                continue
            out.append(0.0)
        else:
            out.append(n)
    return out


def to_num(v):
    """尽量转成数字；转不了返回 None（而不是 0 —— 统计函数要区分）"""
    if v is None or v == '':
        return None
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return None
        return float(v)
    s = str(v).strip().replace(',', '').replace('，', '')
    if s.endswith('%'):
        try:
            return float(s[:-1]) / 100.0
        except ValueError:
            return None
    s = s.replace('¥', '').replace('$', '').replace('元', '')
    try:
        return float(s)
    except ValueError:
        return None


def to_text(v):
    if v is None:
        return ''
    if isinstance(v, bool):
        return 'TRUE' if v else 'FALSE'
    if isinstance(v, float) and v == int(v) and abs(v) < 1e15:
        return str(int(v))
    return str(v)


def to_bool(v):
    if isinstance(v, bool):
        return v
    if v is None or v == '':
        return False
    if isinstance(v, (int, float)):
        return v != 0
    s = str(v).strip().upper()
    if s in ('TRUE', 'T', '真', '是', 'Y', 'YES'):
        return True
    if s in ('FALSE', 'F', '假', '否', 'N', 'NO'):
        return False
    return None


def first_err(*vals):
    for v in vals:
        if is_err(v):
            return v
    return None


# ------------------------------------------------------------------ 日期工具
_TIME_RE = re.compile(
    r'^\s*(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?\s*(?:([AaPp])\.?[Mm]\.?)?\s*$')


def _time_frac(s):
    """纯时间文本 "13:45:30" / "1:00 PM" -> 当日小数部分；不是时间文本返回 None"""
    m = _TIME_RE.match(s)
    if not m:
        return None
    h = int(m.group(1))
    mi = int(m.group(2))
    se = int(m.group(3) or 0)
    ap = (m.group(4) or '').lower()
    if ap == 'a' and h == 12:
        h = 0
    elif ap == 'p' and h != 12:
        h += 12
    if h > 23 or mi > 59 or se > 59:
        return None
    return (h * 3600 + mi * 60 + se) / 86400.0


def to_serial(v):
    """转成 Excel 序列号。接受 date/datetime/序列号/日期文本/纯时间文本"""
    if v is None or v == '':
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, _dt.datetime):
        return (v.date() - _EPOCH).days + (v.hour * 3600 + v.minute * 60
                                           + v.second) / 86400.0
    if isinstance(v, _dt.date):
        return float((v - _EPOCH).days)
    s = str(v).strip()
    if not s:
        return None
    m = re.match(r'^(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})', s)
    if m:
        try:
            d = _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
        frac = 0.0
        tm = re.search(r'(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?', s)
        if tm:
            frac = (int(tm.group(1)) * 3600 + int(tm.group(2)) * 60
                    + int(tm.group(3) or 0)) / 86400.0
        return (d - _EPOCH).days + frac
    # 纯时间文本（"13:45:30"）：Excel 里其序列号就是当日的小数部分
    tf = _time_frac(s)
    if tf is not None:
        return tf
    n = to_num(s)
    return n


def serial_to_dt(sn):
    try:
        sn = float(sn)
    except (TypeError, ValueError):
        return None
    if sn < 0 or sn > 2958465:
        return None
    days = int(sn)
    frac = sn - days
    return _dt.datetime.combine(_EPOCH + _dt.timedelta(days=days),
                                _dt.time()) + _dt.timedelta(seconds=frac * 86400)

# ------------------------------------------------------------------ 比较辅助
def _same(a, b):
    """精确匹配（VLOOKUP/MATCH 的 0 模式）"""
    if isinstance(a, str) and isinstance(b, str):
        return a.strip().lower() == b.strip().lower()
    na, nb = to_num(a), to_num(b)
    if na is not None and nb is not None:
        return abs(na - nb) < 1e-9
    return to_text(a).strip().lower() == to_text(b).strip().lower()


def _cmp_le(a, b):
    na, nb = to_num(a), to_num(b)
    if na is not None and nb is not None:
        return na <= nb + 1e-9
    return to_text(a).strip().lower() <= to_text(b).strip().lower()


def _cmp_ge(a, b):
    na, nb = to_num(a), to_num(b)
    if na is not None and nb is not None:
        return na >= nb - 1e-9
    return to_text(a).strip().lower() >= to_text(b).strip().lower()
