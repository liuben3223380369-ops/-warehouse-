# -*- coding: utf-8 -*-
"""表格模块 · 电子表格 函数库 · 统计函数

参数已经求值过：标量或二维列表（区域）；错误值沿调用链传播。
"""
import math
from .convert import first_err, flat, is_blank, is_err, nums, to_num

def _average(*a):
    e = first_err(*a)
    if e:
        return e
    vs = nums(a)
    return (sum(vs) / len(vs)) if vs else '#DIV/0!'


def _median(*a):
    vs = sorted(nums(a))
    if not vs:
        return '#NUM!'
    n = len(vs)
    return vs[n // 2] if n % 2 else (vs[n // 2 - 1] + vs[n // 2]) / 2.0


def _mode(*a):
    vs = nums(a)
    if not vs:
        return '#NUM!'
    cnt = {}
    for v in vs:
        cnt[v] = cnt.get(v, 0) + 1
    top = max(cnt.values())
    # Excel：没有任何值重复出现时返回 #N/A，而不是返回第一个数
    if top < 2:
        return '#N/A'
    for v in vs:
        if cnt[v] == top:
            return v
    return '#N/A'


def _stdev(*a):
    vs = nums(a)
    if len(vs) < 2:
        return '#DIV/0!'
    m = sum(vs) / len(vs)
    return math.sqrt(sum((x - m) ** 2 for x in vs) / (len(vs) - 1))


def _stdevp(*a):
    vs = nums(a)
    if not vs:
        return '#DIV/0!'
    m = sum(vs) / len(vs)
    return math.sqrt(sum((x - m) ** 2 for x in vs) / len(vs))


def _var(*a):
    v = _stdev(*a)
    return v if is_err(v) else v ** 2


def _varp(*a):
    v = _stdevp(*a)
    return v if is_err(v) else v ** 2


def _min(*a):
    e = first_err(*a)
    if e:
        return e
    vs = nums(a)
    return min(vs) if vs else 0.0


def _max(*a):
    e = first_err(*a)
    if e:
        return e
    vs = nums(a)
    return max(vs) if vs else 0.0


def _count(*a):
    return float(len(nums(a)))


def _counta(*a):
    return float(len(flat(a)))


def _countblank(*a):
    tot = nblank = 0

    def walk(x):
        nonlocal tot, nblank
        if isinstance(x, (list, tuple)):
            for y in x:
                walk(y)
        else:
            tot += 1
            if is_blank(x):
                nblank += 1
    for x in a:
        walk(x)
    return float(nblank if tot else 1)


def _large(*a):
    vs = sorted(nums([a[0]]) if a else [], reverse=True)
    k = int(to_num(a[1]) or 1) if len(a) > 1 else 1
    if k < 1 or k > len(vs):
        return '#NUM!'
    return vs[k - 1]


def _small(*a):
    vs = sorted(nums([a[0]]) if a else [])
    k = int(to_num(a[1]) or 1) if len(a) > 1 else 1
    if k < 1 or k > len(vs):
        return '#NUM!'
    return vs[k - 1]


def _rank(*a):
    v = to_num(a[0]) if a else None
    arr = sorted(nums([a[1]]) if len(a) > 1 else [], reverse=True)
    order = int(to_num(a[2]) or 0) if len(a) > 2 else 0
    if v is None or not arr:
        return '#NUM!'
    if order:
        arr = sorted(arr)
    for i, x in enumerate(arr):
        if x == v:
            return float(i + 1)
    return '#NA' if False else '#N/A'


def _percentile(*a):
    arr = sorted(nums([a[0]]) if a else [])
    k = to_num(a[1]) if len(a) > 1 else None
    if not arr or k is None or k < 0 or k > 1:
        return '#NUM!'
    if len(arr) == 1:
        return arr[0]
    pos = k * (len(arr) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(arr) - 1)
    return arr[lo] + (arr[hi] - arr[lo]) * (pos - lo)


def _quartile(*a):
    arr = nums([a[0]]) if a else []
    q = int(to_num(a[1]) or 0) if len(a) > 1 else 0
    if q < 0 or q > 4 or not arr:
        return '#NUM!'
    return _percentile((a[0] if a else []), q / 4.0)


def _avedev(*a):
    vs = nums(a)
    if not vs:
        return '#NUM!'
    m = sum(vs) / len(vs)
    return sum(abs(x - m) for x in vs) / len(vs)


def _trimmean(*a):
    arr = sorted(nums([a[0]]) if a else [])
    p = to_num(a[1]) if len(a) > 1 else 0
    if not arr or p is None or p < 0 or p >= 1:
        return '#NUM!'
    cut = int(len(arr) * p) // 2
    keep = arr[cut:len(arr) - cut] if cut else arr
    return sum(keep) / len(keep) if keep else '#NUM!'


def _geomean(*a):
    vs = [x for x in nums(a) if x > 0]
    if not vs:
        return '#NUM!'
    return math.exp(sum(math.log(x) for x in vs) / len(vs))


def _harmean(*a):
    vs = [x for x in nums(a) if x]
    if not vs:
        return '#NUM!'
    return len(vs) / sum(1.0 / x for x in vs)


def _correl(*a):
    x = nums([a[0]]) if a else []
    y = nums([a[1]]) if len(a) > 1 else []
    n = min(len(x), len(y))
    if n < 2:
        return '#DIV/0!'
    x, y = x[:n], y[:n]
    mx, my = sum(x) / n, sum(y) / n
    num = sum((x[i] - mx) * (y[i] - my) for i in range(n))
    dx = math.sqrt(sum((v - mx) ** 2 for v in x))
    dy = math.sqrt(sum((v - my) ** 2 for v in y))
    if not dx or not dy:
        return '#DIV/0!'
    return num / (dx * dy)


def _slope(*a):
    # Excel 语义：SLOPE(known_y's, known_x's) —— y 在前，x 在后
    y = nums([a[0]]) if a else []
    x = nums([a[1]]) if len(a) > 1 else []
    n = min(len(x), len(y))
    if n < 2:
        return '#DIV/0!'
    mx, my = sum(x[:n]) / n, sum(y[:n]) / n
    num = sum((x[i] - mx) * (y[i] - my) for i in range(n))
    den = sum((x[i] - mx) ** 2 for i in range(n))
    return num / den if den else '#DIV/0!'


def _intercept(*a):
    y = nums([a[0]]) if a else []
    x = nums([a[1]]) if len(a) > 1 else []
    n = min(len(x), len(y))
    if n < 1:
        return '#DIV/0!'
    # 注意保持 (y, x) 顺序，与 SLOPE 一致
    s = _slope((a[0] if a else []), (a[1] if len(a) > 1 else []))
    if is_err(s):
        return s
    return sum(y[:n]) / n - s * sum(x[:n]) / n


def _frequency(*a):
    data = nums([a[0]]) if a else []
    bins = nums([a[1]]) if len(a) > 1 else []
    out = [0.0] * (len(bins) + 1)
    for v in data:
        placed = False
        for i, b in enumerate(bins):
            if v <= b:
                out[i] += 1
                placed = True
                break
        if not placed:
            out[-1] += 1
    return [[x] for x in out]
