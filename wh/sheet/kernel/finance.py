# -*- coding: utf-8 -*-
"""表格模块 · 电子表格 函数库 · 财务函数

参数已经求值过：标量或二维列表（区域）；错误值沿调用链传播。
"""
from .convert import nums, to_num

def _pmt(*a):
    rate = to_num(a[0]) if a else 0
    nper = to_num(a[1]) if len(a) > 1 else 0
    pv = to_num(a[2]) if len(a) > 2 else 0
    if not nper:
        return '#DIV/0!'
    if not rate:
        return -pv / nper
    return -pv * rate / (1 - (1 + rate) ** (-nper))


def _pv(*a):
    rate = to_num(a[0]) if a else 0
    nper = to_num(a[1]) if len(a) > 1 else 0
    pmt = to_num(a[2]) if len(a) > 2 else 0
    if not rate:
        return -pmt * nper
    return -pmt * (1 - (1 + rate) ** (-nper)) / rate


def _fv(*a):
    rate = to_num(a[0]) if a else 0
    nper = to_num(a[1]) if len(a) > 1 else 0
    pmt = to_num(a[2]) if len(a) > 2 else 0
    pv = to_num(a[3]) if len(a) > 3 else 0
    if not rate:
        return -(pv + pmt * nper)
    return -(pv * (1 + rate) ** nper + pmt * ((1 + rate) ** nper - 1) / rate)


def _npv(*a):
    rate = to_num(a[0]) if a else 0
    if rate is None:
        return '#VALUE!'
    vals = nums(a[1:])
    return sum(v / (1 + rate) ** (i + 1) for i, v in enumerate(vals))


def _irr(*a):
    vals = nums(a)
    guess = to_num(a[1]) if len(a) > 1 and a[1] not in (None, '') else 0.1
    if len(vals) < 2:
        return '#NUM!'
    r = guess
    for _ in range(100):
        f = sum(v / (1 + r) ** i for i, v in enumerate(vals))
        df = sum(-i * v / (1 + r) ** (i + 1) for i, v in enumerate(vals) if i)
        if abs(df) < 1e-12:
            break
        nr = r - f / df
        if abs(nr - r) < 1e-9:
            return nr
        r = nr
    return r if abs(r) < 1e6 else '#NUM!'


def _rate(*a):
    nper = to_num(a[0]) if a else 0
    pmt = to_num(a[1]) if len(a) > 1 else 0
    pv = to_num(a[2]) if len(a) > 2 else 0
    if not nper:
        return '#DIV/0!'
    lo, hi = -0.9999, 10.0
    for _ in range(200):
        mid = (lo + hi) / 2
        v = -pv * mid / (1 - (1 + mid) ** (-nper)) if mid else -pv / nper
        if v > pmt:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _sln(*a):
    cost = to_num(a[0]) if a else 0
    salv = to_num(a[1]) if len(a) > 1 else 0
    life = to_num(a[2]) if len(a) > 2 else 0
    if not life:
        return '#DIV/0!'
    return (cost - salv) / life


def _db(*a):
    cost = to_num(a[0]) if a else 0
    salv = to_num(a[1]) if len(a) > 1 else 0
    life = to_num(a[2]) if len(a) > 2 else 0
    per = to_num(a[3]) if len(a) > 3 else 1
    if life <= 0 or per < 1 or cost <= 0:
        return '#NUM!'
    rate = 1 - (salv / cost) ** (1 / life)
    rate = round(rate, 3)
    book = cost
    out = 0.0
    for m in range(1, int(life) + 1):
        d = book * rate
        if m == int(per):
            out = d
        book -= d
    return out
