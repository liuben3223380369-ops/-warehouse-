# -*- coding: utf-8 -*-
"""表格模块 · 电子表格 函数库

对齐 Excel / WPS 的常用函数，共 150+ 个，分九大类：
    数学三角  统计  逻辑  文本  日期时间  查找引用  信息  财务  数组

约定
    * 参数已经求值过：标量，或二维列表（区域）
    * 错误值会沿调用链向上传播（跟 Excel 一样），除非函数显式处理
      （IFERROR / IFNA / ISERROR / ISERR / ISNA / ERROR.TYPE）
    * 空单元格在数学运算里当 0，在统计函数里被忽略
    * 文本型数字在算术里自动转数值（"12" + 1 = 13），这也是 Excel 的行为
"""
import math
import re
import datetime as _dt
from functools import reduce

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


# ------------------------------------------------------------------ 数学三角
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


# ------------------------------------------------------------------ 统计
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


# ------------------------------------------------------------------ 逻辑
def _if(ctx, *a):
    """IF 需要惰性求值，由引擎特殊处理；这里兜底（参数已算好时）"""
    c = to_bool(a[0]) if a else False
    if c is None:
        return '#VALUE!'
    if c:
        return a[1] if len(a) > 1 and a[1] not in (None,) else True
    return a[2] if len(a) > 2 and a[2] not in (None,) else False


def _and(*a):
    vs = flat(a, skip_blank=True)
    if not vs:
        return '#VALUE!'
    for v in vs:
        b = to_bool(v)
        if b is None:
            return '#VALUE!'
        if not b:
            return False
    return True


def _or(*a):
    vs = flat(a, skip_blank=True)
    if not vs:
        return '#VALUE!'
    for v in vs:
        b = to_bool(v)
        if b is None:
            return '#VALUE!'
        if b:
            return True
    return False


def _xor(*a):
    vs = flat(a, skip_blank=True)
    if not vs:
        return '#VALUE!'
    c = 0
    for v in vs:
        b = to_bool(v)
        if b is None:
            return '#VALUE!'
        c += 1 if b else 0
    return c % 2 == 1


def _not(*a):
    b = to_bool(a[0]) if a else False
    if b is None:
        return '#VALUE!'
    return not b


def _true(*a):
    return True


def _false(*a):
    return False


# ------------------------------------------------------------------ 文本
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
    from . import sp_style
    try:
        return sp_style.format_value(v, fmt)
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


# ------------------------------------------------------------------ 日期时间
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


# ------------------------------------------------------------------ 查找引用
def _vlookup(ctx, *a):
    key = a[0] if a else None
    tbl = a[1] if len(a) > 1 else []
    col = int(to_num(a[2]) or 1) if len(a) > 2 else 1
    approx = to_bool(a[3]) if len(a) > 3 and a[3] not in (None, '') else True
    if not isinstance(tbl, (list, tuple)) or not tbl:
        return '#N/A'
    rows = [r if isinstance(r, (list, tuple)) else [r] for r in tbl]
    ci = col - 1
    if ci < 0 or ci >= max(len(r) for r in rows):
        return '#REF!'
    if approx is False:
        for r in rows:
            if _same(r[0] if r else None, key):
                return r[ci] if ci < len(r) else ''
        return '#N/A'
    last = '#N/A'
    for r in rows:
        v = r[0] if r else None
        if _cmp_le(v, key):
            last = r[ci] if ci < len(r) else ''
        else:
            break
    return last


def _hlookup(ctx, *a):
    key = a[0] if a else None
    tbl = a[1] if len(a) > 1 else []
    row = int(to_num(a[2]) or 1) if len(a) > 2 else 1
    approx = to_bool(a[3]) if len(a) > 3 and a[3] not in (None, '') else True
    if not isinstance(tbl, (list, tuple)) or not tbl:
        return '#N/A'
    rows = [r if isinstance(r, (list, tuple)) else [r] for r in tbl]
    ri = row - 1
    if ri < 0 or ri >= len(rows):
        return '#REF!'
    hdr = rows[0]
    for j, v in enumerate(hdr):
        if _same(v, key) if approx is False else True:
            if approx is False:
                return rows[ri][j] if j < len(rows[ri]) else ''
    if approx is False:
        return '#N/A'
    best = '#N/A'
    for j, v in enumerate(hdr):
        if _cmp_le(v, key):
            best = rows[ri][j] if j < len(rows[ri]) else ''
        else:
            break
    return best


def _lookup(ctx, *a):
    """LOOKUP(查找值, 查找向量, [结果向量]) / LOOKUP(查找值, 数组)

    向量形式：在查找向量里找「不大于查找值的最大项」，返回【结果向量】同位置的值；
    省略结果向量时返回查找向量里找到的那个值。查找向量需按升序排列。
    """
    key = a[0] if a else None
    if len(a) < 2:
        return '#N/A'
    if len(a) > 2:
        look = flat([a[1]])
        res = flat([a[2]])
        n = min(len(look), len(res))
        pos = -1
        for i in range(n):
            if _cmp_le(look[i], key):
                pos = i
            else:
                break
        return res[pos] if pos >= 0 else '#N/A'
    arr = a[1]
    rows = arr if isinstance(arr, (list, tuple)) else [[arr]]
    rows = [r if isinstance(r, (list, tuple)) else [r] for r in rows]
    if not rows or not rows[0]:
        return '#N/A'
    nr, nc = len(rows), max(len(r) for r in rows)
    if nc >= nr:                       # 宽数组：搜第一行，返回最后一行
        look = [rows[0][j] for j in range(nc)]
        res = [rows[nr - 1][j] for j in range(nc)]
    else:                              # 高数组：搜第一列，返回最后一列
        look = [rows[i][0] for i in range(nr)]
        res = [rows[i][nc - 1] for i in range(nr)]
    pos = -1
    for i, v in enumerate(look):
        if _cmp_le(v, key):
            pos = i
        else:
            break
    return res[pos] if pos >= 0 else '#N/A'


def _xlookup(ctx, *a):
    key = a[0] if a else None
    lookup = flat([a[1]]) if len(a) > 1 else []
    ret = flat([a[2]]) if len(a) > 2 else []
    miss = a[3] if len(a) > 3 and a[3] not in (None,) else '#N/A'
    for i, v in enumerate(lookup):
        if _same(v, key):
            return ret[i] if i < len(ret) else '#N/A'
    return miss


def _index(ctx, *a):
    arr = a[0] if a else []
    r = a[1] if len(a) > 1 else None
    c = a[2] if len(a) > 2 else None
    if not isinstance(arr, (list, tuple)) or not arr:
        return '#REF!'
    rows = [x if isinstance(x, (list, tuple)) else [x] for x in arr]
    ri = (int(to_num(r)) - 1) if r not in (None, '') else 0
    ci = (int(to_num(c)) - 1) if c not in (None, '') else 0
    if isinstance(ri, int) and isinstance(ci, int):
        if ri < 0 or ri >= len(rows):
            return '#REF!'
        row = rows[ri]
        if ci < 0 or ci >= len(row):
            return '#REF!'
        return row[ci]
    if ri is not None and c in (None, ''):
        return [rows[ri]] if 0 <= ri < len(rows) else '#REF!'
    return [[row[ci]] for row in rows] if 0 <= ci else '#REF!'


def _match(ctx, *a):
    key = a[0] if a else None
    vec = flat([a[1]]) if len(a) > 1 else []
    # 注意：0 是精确匹配，不能用 `to_num(...) or 1` —— 0 是 falsy，会被换成 1
    _m = to_num(a[2]) if len(a) > 2 and a[2] not in (None, '') else None
    mode = int(_m) if _m is not None else 1
    if mode == 0:
        for i, v in enumerate(vec):
            if _same(v, key):
                return float(i + 1)
        return '#N/A'
    if mode == 1:
        best = '#N/A'
        for i, v in enumerate(vec):
            if _cmp_le(v, key):
                best = float(i + 1)
            else:
                break
        return best
    best = '#N/A'
    for i, v in enumerate(vec):
        if _cmp_ge(v, key):
            best = float(i + 1)
        else:
            break
    return best


def _offset(ctx, *a):
    """OFFSET(基点, 行偏移, 列偏移, 高, 宽) —— 需要引擎提供取区域能力"""
    return ctx.offset(a) if ctx and hasattr(ctx, 'offset') else '#REF!'


def _indirect(ctx, *a):
    return ctx.indirect(a[0] if a else '') if ctx and hasattr(ctx, 'indirect') \
        else '#REF!'


def _row(ctx, *a):
    if a and a[0] not in (None, ''):
        return ctx.ref_row(a[0]) if ctx else '#REF!'
    return float((ctx.row if ctx else 0) + 1)


def _column(ctx, *a):
    if a and a[0] not in (None, ''):
        return ctx.ref_col(a[0]) if ctx else '#REF!'
    return float((ctx.col if ctx else 0) + 1)


def _rows(ctx, *a):
    v = a[0] if a else None
    if isinstance(v, (list, tuple)):
        return float(len(v))
    return 1.0


def _columns(ctx, *a):
    v = a[0] if a else None
    if isinstance(v, (list, tuple)) and v:
        return float(max(len(x) if isinstance(x, (list, tuple)) else 1 for x in v))
    return 1.0


def _choose(ctx, *a):
    i = int(to_num(a[0]) or 1) if a else 1
    if i < 1 or i > len(a) - 1:
        return '#VALUE!'
    return a[i]


def _transpose(ctx, *a):
    v = a[0] if a else []
    if not isinstance(v, (list, tuple)):
        return [[v]]
    rows = [x if isinstance(x, (list, tuple)) else [x] for x in v]
    w = max(len(r) for r in rows) if rows else 0
    return [[(rows[i][j] if j < len(rows[i]) else '') for i in range(len(rows))]
            for j in range(w)]


def _unique(ctx, *a):
    seen, out = [], []
    for v in flat([a[0]] if a else []):
        k = to_text(v)
        if k not in seen:
            seen.append(k)
            out.append(v)
    return [[x] for x in out]


def _sort_range(ctx, *a):
    vs = flat([a[0]] if a else [])
    desc = (int(to_num(a[1]) or 1) if len(a) > 1 and a[1] not in (None, '') else 1) < 0
    vs = sorted(vs, key=lambda x: (to_num(x) is None, to_num(x) or 0, to_text(x)),
                reverse=desc)
    return [[x] for x in vs]


def _filter_range(ctx, *a):
    arr = flat([a[0]] if a else [])
    conds = flat([a[1]] if len(a) > 1 else [])
    out = [v for i, v in enumerate(arr)
           if to_bool(conds[i] if i < len(conds) else False)]
    return [[x] for x in out]


# ------------------------------------------------------------------ 信息
def _isnumber(*a):
    return isinstance(a[0] if a else None, (int, float)) \
        and not isinstance(a[0], bool)


def _istext(*a):
    return isinstance(a[0] if a else None, str) and not is_err(a[0])


def _isblank(*a):
    return is_blank(a[0] if a else None)


def _iserror(*a):
    return is_err(a[0] if a else None)


def _iserr(*a):
    v = a[0] if a else None
    return is_err(v) and v != '#N/A'


def _isna(*a):
    return (a[0] if a else None) == '#N/A'


def _islogical(*a):
    return isinstance(a[0] if a else None, bool)


def _isnontext(*a):
    return not _istext(*a)


def _isref(*a):
    return False


def _type(*a):
    v = a[0] if a else None
    if is_err(v):
        return 16.0
    if isinstance(v, bool):
        return 4.0
    if isinstance(v, (int, float)):
        return 1.0
    if isinstance(v, str):
        return 2.0
    if isinstance(v, (list, tuple)):
        return 64.0
    return 16.0


def _na(*a):
    return '#N/A'


def _error_type(*a):
    v = a[0] if a else None
    return float({'#NULL!': 1, '#DIV/0!': 2, '#VALUE!': 3, '#REF!': 4,
                  '#NAME?': 5, '#NUM!': 6, '#N/A': 7,
                  '#GETTING_DATA': 8}.get(v, 0)) if is_err(v) else '#N/A'


def _cell_info(ctx, *a):
    what = to_text(a[0] if a else '').lower()
    if what == 'row':
        return float((ctx.row if ctx else 0) + 1)
    if what == 'col':
        return float((ctx.col if ctx else 0) + 1)
    if what == 'address':
        return ctx.addr if ctx else ''
    return '#VALUE!'


# ------------------------------------------------------------------ 财务
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


# ------------------------------------------------------------------ 注册表
def _plain(f):
    """普通函数：参数全部先求值"""
    def g(ctx, *args):
        e = first_err(*args)
        if e:
            return e
        return f(*args)
    return g


def _with_ctx(f):
    def g(ctx, *args):
        e = first_err(*args)
        if e:
            return e
        return f(ctx, *args)
    return g


def _raw(f):
    """错误检查类函数：错误值不能短路，必须原样交给函数看。

    ISERROR(1/0) 若走 _plain，1/0 的 #DIV/0! 会被 first_err 直接抛出，
    函数永远收不到错误值，只能返回 #DIV/0! 而不是 TRUE。
    """
    def g(ctx, *args):
        return f(*args)
    return g


#: 函数表。ctx 版用于需要知道"我在哪个单元格"的函数（ROW/OFFSET/INDIRECT）
FUNCS = {}
_CTX_FUNCS = {}

#: 错误检查类：允许错误值作为参数传入（与 Excel 一致）
_RAW_FUNCS = {'ISERROR', 'ISERR', 'ISNA', 'ERROR.TYPE', 'TYPE'}


def _reg(name, fn, ctx=False, raw=False):
    if raw or name in _RAW_FUNCS:
        FUNCS[name] = _raw(fn)
    else:
        FUNCS[name] = _with_ctx(fn) if ctx else _plain(fn)


# 数学
for _n, _f in [
    ('SUM', _sum), ('PRODUCT', _product), ('ABS', _abs_), ('ROUND', _round),
    ('ROUNDUP', _roundup), ('ROUNDDOWN', _rounddown), ('TRUNC', _trunc),
    ('INT', _int), ('CEILING', _ceiling), ('FLOOR', _floor), ('MOD', _mod),
    ('POWER', _power), ('SQRT', _sqrt), ('SIGN', _sign), ('EXP', _exp),
    ('LN', _ln), ('LOG', _log), ('LOG10', _log10), ('GCD', _gcd),
    ('LCM', _lcm), ('FACT', _fact), ('FACTDOUBLE', _factdouble),
    ('COMBIN', _combin), ('PERMUT', _permut), ('PI', _pi), ('RAND', _rand),
    ('RANDBETWEEN', _randbetween), ('SUMSQ', _sumsq), ('SUMXMY2', _sumxmy2),
    ('SERIESSUM', _seriessum), ('MROUND', _mround), ('QUOTIENT', _quotient),
    ('RADIANS', _deg_rad), ('DEGREES', _rad_deg),
    ('SIN', _mk_trig(math.sin)), ('COS', _mk_trig(math.cos)),
    ('TAN', _mk_trig(math.tan)), ('ASIN', _mk_trig_inv(math.asin)),
    ('ACOS', _mk_trig_inv(math.acos)), ('ATAN', _mk_trig_inv(math.atan)),
    ('SINH', lambda *a: math.sinh(to_num(a[0]) or 0)),
    ('COSH', lambda *a: math.cosh(to_num(a[0]) or 0)),
    ('TANH', lambda *a: math.tanh(to_num(a[0]) or 0)),
]:
    _reg(_n, _f)

# 统计
for _n, _f in [
    ('AVERAGE', _average), ('AVG', _average), ('MEDIAN', _median),
    ('MODE', _mode), ('STDEV', _stdev), ('STDEVP', _stdevp), ('VAR', _var),
    ('VARP', _varp), ('MIN', _min), ('MAX', _max), ('COUNT', _count),
    ('COUNTA', _counta), ('COUNTBLANK', _countblank), ('LARGE', _large),
    ('SMALL', _small), ('RANK', _rank), ('PERCENTILE', _percentile),
    ('QUARTILE', _quartile), ('AVEDEV', _avedev), ('TRIMMEAN', _trimmean),
    ('GEOMEAN', _geomean), ('HARMEAN', _harmean), ('CORREL', _correl),
    ('SLOPE', _slope), ('INTERCEPT', _intercept), ('FREQUENCY', _frequency),
]:
    _reg(_n, _f)

# 逻辑
for _n, _f in [('AND', _and), ('OR', _or), ('NOT', _not), ('XOR', _xor),
               ('TRUE', _true), ('FALSE', _false)]:
    _reg(_n, _f)

# 文本
for _n, _f in [
    ('CONCAT', _concat), ('CONCATENATE', _concat), ('TEXTJOIN', _textjoin),
    ('LEFT', _left), ('RIGHT', _right), ('MID', _mid), ('LEN', _len),
    ('LENB', _lenb), ('UPPER', _upper), ('LOWER', _lower), ('PROPER', _proper),
    ('TRIM', _trim), ('CLEAN', _clean), ('SUBSTITUTE', _substitute),
    ('REPLACE', _replace), ('FIND', _find), ('SEARCH', _search),
    ('TEXT', _text), ('VALUE', _value), ('NUMBERVALUE', _numbervalue),
    ('REPT', _rept), ('EXACT', _exact), ('CHAR', _char), ('CODE', _code),
    ('T', _t), ('SPLIT', _split_text), ('REVERSE', _str_reverse),
]:
    _reg(_n, _f)

# 日期时间
for _n, _f in [
    ('TODAY', _today), ('NOW', _now), ('DATE', _date), ('YEAR', _year),
    ('MONTH', _month), ('DAY', _day), ('HOUR', _hour), ('MINUTE', _minute),
    ('SECOND', _second), ('WEEKDAY', _weekday), ('WEEKNUM', _weeknum),
    ('DAYS', _days), ('DATEDIF', _datedif), ('EDATE', _edate),
    ('EOMONTH', _eomonth), ('DATEVALUE', _datevalue), ('TIME', _time),
    ('TIMEVALUE', _timevalue), ('WORKDAY', _workday),
    ('NETWORKDAYS', _networkdays),
]:
    _reg(_n, _f)

# 信息
for _n, _f in [
    ('ISNUMBER', _isnumber), ('ISTEXT', _istext), ('ISBLANK', _isblank),
    ('ISERROR', _iserror), ('ISERR', _iserr), ('ISNA', _isna),
    ('ISLOGICAL', _islogical), ('ISNONTEXT', _isnontext), ('ISREF', _isref),
    ('TYPE', _type), ('NA', _na), ('ERROR.TYPE', _error_type),
]:
    _reg(_n, _f)
_reg('CELL', _cell_info, ctx=True)

# 财务
for _n, _f in [('PMT', _pmt), ('PV', _pv), ('FV', _fv), ('NPV', _npv),
               ('IRR', _irr), ('RATE', _rate), ('SLN', _sln), ('DB', _db)]:
    _reg(_n, _f)

# 查找引用（需要 ctx）
for _n, _f in [
    ('VLOOKUP', _vlookup), ('HLOOKUP', _hlookup), ('LOOKUP', _lookup),
    ('XLOOKUP', _xlookup), ('INDEX', _index), ('MATCH', _match),
    ('OFFSET', _offset), ('INDIRECT', _indirect), ('ROW', _row),
    ('COLUMN', _column), ('ROWS', _rows), ('COLUMNS', _columns),
    ('CHOOSE', _choose), ('TRANSPOSE', _transpose), ('UNIQUE', _unique),
    ('SORT', _sort_range), ('FILTER', _filter_range),
]:
    _reg(_n, _f, ctx=True)

# IF 系列：必须惰性求值，交给引擎特殊处理
LAZY = {'IF', 'IFERROR', 'IFNA', 'IFS', 'SWITCH', 'SUMIF', 'SUMIFS',
        'COUNTIF', 'COUNTIFS', 'AVERAGEIF', 'AVERAGEIFS', 'MAXIFS', 'MINIFS',
        'SUBTOTAL', 'AGGREGATE',
        'ROW', 'COLUMN', 'ROWS', 'COLUMNS'}


def is_func(name):
    return name in FUNCS or name in LAZY


def all_names():
    return sorted(set(list(FUNCS) + list(LAZY)))
