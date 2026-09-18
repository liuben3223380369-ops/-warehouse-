# -*- coding: utf-8 -*-
"""表格模块 · 电子表格 函数库 · 日期与时间函数

参数已经求值过：标量或二维列表（区域）；错误值沿调用链传播。
"""
import re
import datetime as _dt
from .convert import _EPOCH, serial_to_dt, to_num, to_serial, to_text

def _today(*a):
    return float((_dt.date.today() - _EPOCH).days)


def _now(*a):
    d = _dt.datetime.now()
    return float((d.date() - _EPOCH).days) + (d.hour * 3600 + d.minute * 60
                                              + d.second) / 86400.0


def _date(*a):
    y, m, d = (int(to_num(a[0]) or 0), int(to_num(a[1]) or 0),
               int(to_num(a[2]) or 0))
    try:
        if y < 100:
            y += 2000
        return float((_dt.date(y, m, d) - _EPOCH).days)
    except ValueError:
        return '#NUM!'


def _year(*a):
    d = serial_to_dt(to_serial(a[0] if a else None))
    return float(d.year) if d else '#NUM!'


def _month(*a):
    d = serial_to_dt(to_serial(a[0] if a else None))
    return float(d.month) if d else '#NUM!'


def _day(*a):
    d = serial_to_dt(to_serial(a[0] if a else None))
    return float(d.day) if d else '#NUM!'


def _hour(*a):
    d = serial_to_dt(to_serial(a[0] if a else None))
    return float(d.hour) if d else '#NUM!'


def _minute(*a):
    d = serial_to_dt(to_serial(a[0] if a else None))
    return float(d.minute) if d else '#NUM!'


def _second(*a):
    d = serial_to_dt(to_serial(a[0] if a else None))
    return float(d.second) if d else '#NUM!'


def _weekday(*a):
    d = serial_to_dt(to_serial(a[0] if a else None))
    t = int(to_num(a[1]) or 1) if len(a) > 1 else 1
    if not d:
        return '#NUM!'
    w = d.weekday()          # 0=周一 … 6=周日
    if t == 1:               # 周日=1 … 周六=7
        return float((w + 1) % 7 + 1)
    if t == 2:               # 周一=1 … 周日=7
        return float(w + 1)
    if t == 3:               # 周一=0 … 周日=6
        return float(w % 7)
    return '#NUM!'


def _weeknum(*a):
    d = serial_to_dt(to_serial(a[0] if a else None))
    if not d:
        return '#NUM!'
    return float(d.isocalendar()[1])


def _days(*a):
    e, s = to_serial(a[0] if a else None), to_serial(a[1] if len(a) > 1 else None)
    if e is None or s is None:
        return '#VALUE!'
    return float(int(e) - int(s))


def _datedif(*a):
    s, e = serial_to_dt(to_serial(a[0] if a else None)), \
        serial_to_dt(to_serial(a[1] if len(a) > 1 else None))
    unit = to_text(a[2] if len(a) > 2 else 'D').upper()
    if not s or not e or e < s:
        return '#NUM!'
    y = e.year - s.year
    m = e.month - s.month
    d = e.day - s.day
    if unit == 'Y':
        if (e.month, e.day) < (s.month, s.day):
            y -= 1
        return float(y)
    if unit == 'M':
        tot = y * 12 + m
        if d < 0:
            tot -= 1
        return float(tot)
    if unit == 'D':
        return float((e.date() - s.date()).days)
    if unit == 'MD':
        if d < 0:
            pm = e.replace(day=1) - _dt.timedelta(days=1)
            d = (e.date() - pm.date()).days + d
        return float(d)
    if unit == 'YM':
        tot = y * 12 + m
        if d < 0:
            tot -= 1
        return float(tot % 12)
    if unit == 'YD':
        base = s.replace(year=e.year)
        if base > e:
            base = s.replace(year=e.year - 1)
        return float((e.date() - base.date()).days)
    return '#NUM!'


def _edate(*a):
    d = serial_to_dt(to_serial(a[0] if a else None))
    n = int(to_num(a[1]) or 0) if len(a) > 1 else 0
    if not d:
        return '#NUM!'
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    try:
        return float((_dt.date(y, m, min(d.day, _days_in_month(y, m)))
                      - _EPOCH).days)
    except ValueError:
        return '#NUM!'


def _days_in_month(y, m):
    import calendar
    return calendar.monthrange(y, m)[1]


def _eomonth(*a):
    d = serial_to_dt(to_serial(a[0] if a else None))
    n = int(to_num(a[1]) or 0) if len(a) > 1 else 0
    if not d:
        return '#NUM!'
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    return float((_dt.date(y, m, _days_in_month(y, m)) - _EPOCH).days)


def _datevalue(*a):
    v = to_serial(a[0] if a else None)
    return float(int(v)) if v is not None else '#VALUE!'


def _time(*a):
    h, m, s = (int(to_num(a[0]) or 0), int(to_num(a[1]) or 0),
               int(to_num(a[2]) or 0))
    if h > 32767 or m > 32767 or s > 32767:
        return '#NUM!'
    return ((h % 24) * 3600 + m * 60 + s) / 86400.0


def _timevalue(*a):
    v = a[0] if a else None
    if isinstance(v, str):
        # to_serial 只认「日期」或「日期+时间」，纯时间文本（"13:45:30"）要先单独解析
        m = re.match(r'^\s*(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?\s*(?:([AaPp])\.?[Mm]\.?)?\s*$',
                     v)
        if m:
            h = int(m.group(1)); mi = int(m.group(2)); se = int(m.group(3) or 0)
            ap = (m.group(4) or '').lower()
            if ap == 'a' and h == 12:
                h = 0
            elif ap == 'p' and h != 12:
                h += 12
            if h > 23 or mi > 59 or se > 59:
                return '#VALUE!'
            return (h * 3600 + mi * 60 + se) / 86400.0
        n = to_num(v)
        if n is not None:
            return n - int(n)
        return '#VALUE!'
    sv = to_serial(v)
    return (sv - int(sv)) if sv is not None else '#VALUE!'


def _workday(*a):
    d = serial_to_dt(to_serial(a[0] if a else None))
    n = int(to_num(a[1]) or 0) if len(a) > 1 else 0
    if not d:
        return '#NUM!'
    cur = d.date()
    step = 1 if n >= 0 else -1
    left = abs(n)
    while left:
        cur += _dt.timedelta(days=step)
        if cur.weekday() < 5:
            left -= 1
    return float((cur - _EPOCH).days)


def _networkdays(*a):
    s = serial_to_dt(to_serial(a[0] if a else None))
    e = serial_to_dt(to_serial(a[1] if len(a) > 1 else None))
    if not s or not e:
        return '#NUM!'
    if s > e:
        s, e = e, s
    n, cur = 0, s.date()
    while cur <= e.date():
        if cur.weekday() < 5:
            n += 1
        cur += _dt.timedelta(days=1)
    return float(n)
