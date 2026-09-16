# -*- coding: utf-8 -*-
"""表格模块 · 取值辅助

一行数据里"某一列的值怎么取"—— 系统列直接取字段，自定义列从 extra JSON 取。
导出、流水页、库存页都要用，所以放在表格模块统一提供。
"""
from ..core import db


def all_custom_cols():
    """所有模板的自定义列（按列名去重），供流水页 / 导出显示"""
    out = []
    for t in db.tpls():
        for x in db.tpl_custom_cols(t['id']):
            if x['label'] not in [y['label'] for y in out]:
                out.append(dict(x, label=db.col_label(x)))
    return out


def cell_val(row, col):
    '''取一行数据里某列的值：系统列直接取字段，自定义列从 extra JSON 取。

    库存导出按列配置生成表头时靠它取值，保证表头和数据一一对应。
    '''
    fid = col['fid']
    try:
        v = row[fid] if fid in row.keys() else None
    except (IndexError, TypeError, KeyError):
        v = None
    if v is not None:
        return v
    raw = None
    try:
        raw = row['extra'] if 'extra' in row.keys() else None
    except (IndexError, TypeError, KeyError):
        raw = None
    if not raw:
        return ''
    try:
        import json as _json
        return (_json.loads(raw) or {}).get(fid, '')
    except ValueError:
        return ''
