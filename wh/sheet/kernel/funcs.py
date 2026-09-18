# -*- coding: utf-8 -*-
"""表格模块 · 电子表格 函数库（门面）

按类别拆成十个子模块，由 registry 装配成 FUNCS 表：

    convert  类型转换与比较原语      num      数学与三角
    stat     统计                    logic    逻辑
    text     文本                    dt       日期与时间
    lookup   查找与引用              info     信息
    finance  财务                    registry 注册表与聚合

这里只做聚合导出，外部 ``from ..kernel import funcs as F`` 的用法不变。
"""
from .convert import (ERRORS, _EPOCH, _cmp_ge, _cmp_le, _same,
                      first_err, flat, is_blank, is_err, nums,
                      serial_to_dt, to_bool, to_num, to_serial, to_text)
from .registry import (FUNCS, LAZY, _CTX_FUNCS, _RAW_FUNCS, _reg,
                       all_names, is_func)

# 兼容：各类别里的 _xxx 实现仍可用 funcs._sum 这种方式取到
from . import (convert as _convert, dt as _dt_mod, finance as _finance,
               info as _info, logic as _logic, lookup as _lookup,
               num as _num, stat as _stat, text as _text)

for _mod in (_convert, _num, _stat, _logic, _text, _dt_mod, _lookup, _info,
             _finance):
    for _k, _v in vars(_mod).items():
        if _k.startswith('_') and not _k.startswith('__') and callable(_v):
            globals().setdefault(_k, _v)
del _mod, _k, _v
