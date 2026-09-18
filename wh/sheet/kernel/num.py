# -*- coding: utf-8 -*-
"""表格模块 · 电子表格 函数库 · 数学与三角函数

参数已经求值过：标量或二维列表（区域）；错误值沿调用链传播。
"""
import math
from functools import reduce
from .convert import first_err, nums, to_num

def _sum(*a):
    e = first_err(*a)
    if e:
        return e
    return float(sum(nums(a)))


def _product(*a):
    e = first_err(*a)
    if e:
        return e
    vs = nums(a)
    return reduce(lambda x, y: x * y, vs, 1.0)


def _round(*a):
    v = to_num(a[0]) if a else 0.0
    d = int(to_num(a[1]) or 0) if len(a) > 1 else 0
    if v is None:
        return '#VALUE!'
    # Excel 的 ROUND 是"四舍五入远离零"
    try:
        f = 10.0 ** d
        return math.floor(abs(v) * f + 0.5) / f * (1 if v >= 0 else -1)
    except OverflowError:
        return '#NUM!'


def _roundup(*a):
    v = to_num(a[0]) if a else 0.0
    d = int(to_num(a[1]) or 0) if len(a) > 1 else 0
    if v is None:
        return '#VALUE!'
    f = 10.0 ** d
    return math.ceil(v * f - 1e-12) / f


def _rounddown(*a):
    v = to_num(a[0]) if a else 0.0
    d = int(to_num(a[1]) or 0) if len(a) > 1 else 0
    if v is None:
        return '#VALUE!'
    f = 10.0 ** d
    return math.floor(v * f + 1e-12) / f


def _trunc(*a):
    v = to_num(a[0]) if a else 0.0
    d = int(to_num(a[1]) or 0) if len(a) > 1 else 0
    if v is None:
        return '#VALUE!'
    f = 10.0 ** d
    return math.trunc(v * f) / f


def _int(*a):
    v = to_num(a[0]) if a else 0.0
    return 0.0 if v is None else math.floor(v)


def _ceiling(*a):
    v = to_num(a[0]) if a else 0.0
    s = to_num(a[1]) if len(a) > 1 else 1.0
    if v is None:
        return '#VALUE!'
    if not s:
        return 0.0
    return math.ceil(v / s - 1e-12) * s


def _floor(*a):
    v = to_num(a[0]) if a else 0.0
    s = to_num(a[1]) if len(a) > 1 else 1.0
    if v is None:
        return '#VALUE!'
    if not s:
        return 0.0
    return math.floor(v / s + 1e-12) * s


def _mod(*a):
    x, y = (to_num(a[0]) if a else None), (to_num(a[1]) if len(a) > 1 else None)
    if x is None or y is None:
        return '#VALUE!'
    if y == 0:
        return '#DIV/0!'
    return x - y * math.floor(x / y)          # 跟 Excel 一致：结果符号跟除数


def _power(*a):
    x, y = (to_num(a[0]) if a else None), (to_num(a[1]) if len(a) > 1 else None)
    if x is None or y is None:
        return '#VALUE!'
    try:
        r = x ** y
    except (OverflowError, ValueError):
        return '#NUM!'
    if isinstance(r, complex):
        return '#NUM!'
    return float(r)


def _sqrt(*a):
    v = to_num(a[0]) if a else None
    if v is None:
        return '#VALUE!'
    if v < 0:
        return '#NUM!'
    return math.sqrt(v)


def _abs_(*a):
    v = to_num(a[0]) if a else 0.0
    return 0.0 if v is None else abs(v)


def _sign(*a):
    v = to_num(a[0]) if a else 0.0
    if v is None:
        return '#VALUE!'
    return 0.0 if v == 0 else (1.0 if v > 0 else -1.0)


def _exp(*a):
    v = to_num(a[0]) if a else 0.0
    if v is None:
        return '#VALUE!'
    try:
        return math.exp(v)
    except OverflowError:
        return '#NUM!'


def _ln(*a):
    v = to_num(a[0]) if a else None
    if v is None or v <= 0:
        return '#NUM!'
    return math.log(v)


def _log(*a):
    v = to_num(a[0]) if a else None
    b = to_num(a[1]) if len(a) > 1 and a[1] not in (None, '') else 10.0
    if v is None or v <= 0 or not b or b <= 0:
        return '#NUM!'
    return math.log(v, b)


def _log10(*a):
    v = to_num(a[0]) if a else None
    if v is None or v <= 0:
        return '#NUM!'
    return math.log10(v)


def _gcd(*a):
    vs = [int(abs(x)) for x in nums(a)]
    if not vs:
        return 0.0
    return float(reduce(math.gcd, vs))


def _lcm(*a):
    vs = [int(abs(x)) for x in nums(a) if x]
    if not vs:
        return 0.0
    return float(reduce(lambda x, y: x * y // math.gcd(x, y), vs))


def _fact(*a):
    v = int(to_num(a[0]) or 0)
    if v < 0:
        return '#NUM!'
    if v > 170:
        return '#NUM!'
    return float(math.factorial(v))


def _factdouble(*a):
    v = int(to_num(a[0]) or 0)
    if v < -1:
        return '#NUM!'
    r = 1
    while v > 1:
        r *= v
        v -= 2
    return float(r)


def _combin(*a):
    n, k = int(to_num(a[0]) or 0), int(to_num(a[1]) or 0)
    if n < 0 or k < 0 or k > n:
        return '#NUM!'
    return float(math.comb(n, k))


def _permut(*a):
    n, k = int(to_num(a[0]) or 0), int(to_num(a[1]) or 0)
    if n < 0 or k < 0 or k > n:
        return '#NUM!'
    return float(math.factorial(n) // math.factorial(n - k))


def _pi(*a):
    return math.pi


def _rand(*a):
    import random
    return random.random()


def _randbetween(*a):
    import random
    lo, hi = int(to_num(a[0]) or 0), int(to_num(a[1]) or 0)
    if lo > hi:
        lo, hi = hi, lo
    return float(random.randint(lo, hi))


def _sumsq(*a):
    return float(sum(x * x for x in nums(a)))


def _sumxmy2(*a):
    x = nums([a[0]]) if a else []
    y = nums([a[1]]) if len(a) > 1 else []
    n = min(len(x), len(y))
    return float(sum((x[i] - y[i]) ** 2 for i in range(n)))


def _seriessum(*a):
    """SERIESSUM(x, n, m, 系数)"""
    x = to_num(a[0]) if a else None
    n = to_num(a[1]) if len(a) > 1 else 0
    m = to_num(a[2]) if len(a) > 2 else 0
    co = nums([a[3]]) if len(a) > 3 else []
    if x is None:
        return '#VALUE!'
    return float(sum(c * (x ** (n + i * m)) for i, c in enumerate(co)))


def _mround(*a):
    v = to_num(a[0]) if a else None
    m = to_num(a[1]) if len(a) > 1 else None
    if v is None or not m:
        return '#NUM!'
    return round(v / m) * m


def _quotient(*a):
    x = to_num(a[0]) if a else None
    y = to_num(a[1]) if len(a) > 1 else None
    if x is None or not y:
        return '#DIV/0!'
    return float(int(x / y))


def _deg_rad(*a):
    v = to_num(a[0]) if a else 0.0
    return math.radians(v) if v is not None else '#VALUE!'


def _rad_deg(*a):
    v = to_num(a[0]) if a else 0.0
    return math.degrees(v) if v is not None else '#VALUE!'


def _mk_trig(fn):
    """三角函数：与 Excel / LibreOffice 一致 —— 入参为弧度，返回比值。

    早期实现误把入参当角度（math.radians(v)），导致 SIN(PI()/6)=0.0091 而非 0.5。
    因 SIN(0)=0、COS(0)=1 恰好正确，该问题长期未被回归发现。
    """
    def f(*a):
        v = to_num(a[0]) if a else 0.0
        if v is None:
            return '#VALUE!'
        try:
            return fn(v)
        except (ValueError, OverflowError):
            return '#NUM!'
    return f


def _mk_trig_inv(fn):
    """反三角函数：与 Excel / LibreOffice 一致 —— 入参为比值，返回弧度。

    早期实现误把结果转成角度（math.degrees(...)），导致 ASIN(0.5)=30 而非 0.5236。
    """
    def f(*a):
        v = to_num(a[0]) if a else 0.0
        if v is None:
            return '#VALUE!'
        try:
            return fn(v)
        except (ValueError, OverflowError):
            return '#NUM!'
    return f
