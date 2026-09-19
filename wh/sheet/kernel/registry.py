# -*- coding: utf-8 -*-
"""表格模块 · 电子表格 函数库 · 函数注册表与聚合导出

把各类别的函数装配成 FUNCS 表，并统一对外导出。
换实现只需改这里的注册，数据模型与求值器不受影响。
"""

import math

from .convert import (
    first_err,
    to_num,
)

from .dt import (
    _date,
    _datedif,
    _datevalue,
    _day,
    _days,
    _edate,
    _eomonth,
    _hour,
    _minute,
    _month,
    _networkdays,
    _now,
    _second,
    _time,
    _timevalue,
    _today,
    _weekday,
    _weeknum,
    _workday,
    _year,
)

from .finance import (
    _db,
    _fv,
    _irr,
    _npv,
    _pmt,
    _pv,
    _rate,
    _sln,
)

from .info import (
    _cell_info,
    _error_type,
    _isblank,
    _iserr,
    _iserror,
    _islogical,
    _isna,
    _isnontext,
    _isnumber,
    _isref,
    _istext,
    _na,
    _type,
)

from .logic import (
    _and,
    _false,
    _not,
    _or,
    _true,
    _xor,
)

from .lookup import (
    _choose,
    _column,
    _columns,
    _filter_range,
    _hlookup,
    _index,
    _indirect,
    _lookup,
    _match,
    _offset,
    _row,
    _rows,
    _sort_range,
    _transpose,
    _unique,
    _vlookup,
    _xlookup,
)

from .num import (
    _abs_,
    _ceiling,
    _combin,
    _deg_rad,
    _exp,
    _fact,
    _factdouble,
    _floor,
    _gcd,
    _int,
    _lcm,
    _ln,
    _log,
    _log10,
    _mk_trig,
    _mk_trig_inv,
    _mod,
    _mround,
    _permut,
    _pi,
    _power,
    _product,
    _quotient,
    _rad_deg,
    _rand,
    _randbetween,
    _round,
    _rounddown,
    _roundup,
    _seriessum,
    _sign,
    _sqrt,
    _sum,
    _sumsq,
    _sumxmy2,
    _trunc,
)

from .stat import (
    _avedev,
    _average,
    _correl,
    _count,
    _counta,
    _countblank,
    _frequency,
    _geomean,
    _harmean,
    _intercept,
    _large,
    _max,
    _median,
    _min,
    _mode,
    _percentile,
    _quartile,
    _rank,
    _slope,
    _small,
    _stdev,
    _stdevp,
    _trimmean,
    _var,
    _varp,
)

from .text import (
    _char,
    _clean,
    _code,
    _concat,
    _exact,
    _find,
    _left,
    _len,
    _lenb,
    _lower,
    _mid,
    _numbervalue,
    _proper,
    _replace,
    _rept,
    _right,
    _search,
    _split_text,
    _str_reverse,
    _substitute,
    _t,
    _text,
    _textjoin,
    _trim,
    _upper,
    _value,
)

def _guard(v):
    """溢出保护（v3.106）：inf / nan 一律转成 #NUM!，不许往外传。

    为什么放在包装层而不是逐个函数里补：
        算术层（engine/ctx.py）已经挡住 9e300*9e300 这类，但函数层没有 ——
        =SUM(B4:B5) 装两个 1e308 照样算出 inf。inf 一旦落进格子，
        后面 =C1*2、=SUM(C:C) 全跟着变成 inf，越算越离谱且毫无提示。
        逐函数补会漏（175 个函数），统一在出口挡一次才拦得住。
    """
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return '#NUM!'
    return v


def _plain(f):
    """普通函数：参数全部先求值"""
    def g(ctx, *args):
        e = first_err(*args)
        if e:
            return e
        return _guard(f(*args))
    return g


def _with_ctx(f):
    def g(ctx, *args):
        e = first_err(*args)
        if e:
            return e
        return _guard(f(ctx, *args))
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
