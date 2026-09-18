# -*- coding: utf-8 -*-
"""进阶动作·序列填充：等差/等比/日期（按日、工作日、月、年）"""
import calendar
import datetime

from ..kernel import funcs as F
from .undo import push as _push, snap as _snap


def _push_snap(st, label, sh, r1, c1, r2, c2):
    _push(st, label, sh.name, _snap(sh, r1, c1, r2, c2))

def _to_date(v):
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    if isinstance(v, (int, float)):
        try:
            return (datetime.date(1899, 12, 30) +
                    datetime.timedelta(days=float(v)))
        except (ValueError, OverflowError):
            return None
    s = str(v or '').strip()
    for f in ('%Y-%m-%d', '%Y/%m/%d', '%Y年%m月%d日', '%m/%d/%Y'):
        try:
            return datetime.datetime.strptime(s, f).date()
        except ValueError:
            continue
    return None


def _add_month(d, months):
    m = d.month - 1 + int(months)
    y = d.year + m // 12
    m = m % 12 + 1
    day = min(d.day, calendar.monthrange(y, m)[1])
    return datetime.date(y, m, day)


# ------------------------------------------------------------------ 序列填充
def a_series(book, st, sh, g, rect):
    """序列填充：等比/等差/日期/工作日/月/年，还能给终止值。

    Excel 的「序列」对话框就这几样；拖填充柄走的是简化版（见 a_fill）。
    """
    r1, c1, r2, c2 = rect
    stype = g.get('type') or 'linear'
    step = F.to_num(g.get('step'))
    step = 1 if step is None else step
    stop = F.to_num(g.get('stop'))
    down = (r2 - r1) >= (c2 - c1)
    _push_snap(st, '序列填充', sh, r1, c1, r2, c2)

    cur = sh.value(r1, c1)
    if isinstance(cur, str) and cur.strip() == '':
        return {'err': '起始格是空的，先填个起始值'}
    if stype in ('date', 'weekday', 'month', 'year'):
        cur = _to_date(cur)
        if cur is None:
            return {'err': '起始格不是日期'}
    else:
        cur = F.to_num(cur)
        if cur is None:
            return {'err': '起始格不是数字'}

    n = (r2 - r1 + 1) if down else (c2 - c1 + 1)
    k = 0
    for t in range(1, n):
        k = t
        if stype == 'linear':
            v = cur + step * t
        elif stype == 'growth':
            v = cur * (step ** t)
        elif stype == 'date':
            v = cur + datetime.timedelta(days=step * t)
        elif stype == 'weekday':
            v = cur + datetime.timedelta(days=step * t)
            while v.weekday() >= 5:              # 跳过周六周日
                v += datetime.timedelta(days=1 if step >= 0 else -1)
        elif stype == 'month':
            v = _add_month(cur, step * t)
        else:                                    # year
            v = _add_month(cur, 12 * step * t)
        if stop is not None:
            nv = v if not isinstance(v, datetime.date) else v.toordinal()
            if (step >= 0 and nv > stop) or (step < 0 and nv < stop):
                break
        i, j = (r1 + t, c1) if down else (r1, c1 + t)
        if isinstance(v, datetime.date):
            sh.set_raw(i, j, v.strftime('%Y-%m-%d'))
        else:
            v = round(v, 10)
            sh.set_raw(i, j, str(int(v)) if float(v).is_integer() else str(v))
    sh.invalidate()
    return {'n': k}
