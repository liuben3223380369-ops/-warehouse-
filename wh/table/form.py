# -*- coding: utf-8 -*-
"""表格模块 · 表单服务层

把"录入卡片长什么样"这件事完全收归表格模块。

为什么单独拆一层：
    录入卡片本质上就是**一张表的一行** —— 卡片上每个输入框，对应表格里
    的一列。所以"有哪些字段、字段叫什么、什么类型、带不带单位、占多宽"
    这些全是表格的事，不该由出入库 / 采购各自拼一遍。
    业务模块只负责：读出这张表的结构、往里写一行、改一行。

对外只给一个入口 `schema()`：

    from ..table import tbl
    sch = tbl.form_schema(tid, scene='txn')
    sch['cols']     # 标准列（按启用顺序）
    sch['xcols']    # 自定义列
    sch['fids']     # 启用的列 fid
    sch['pinned']   # 常驻字段（不受表格列增减影响）
    sch['full']     # 独占一行的字段
    sch['has_area'] # 有没有平米 / 卷料列（决定显不显示「数量按」口径）

改这里就等于改所有录入页；业务模块不需要知道列是怎么存的。
"""

from ..core import db


# ---------------------------------------------------------------- 场景定义

# 常驻字段：不管表格怎么配列，这几项都得在。
#   物料名称 —— 没有它这一行就不知道录的是什么
#   批次号   —— v3.42 起常驻卡片，采购到货全靠它对账
PINNED_TXN = ('name', 'batch')
PINNED_PO = ('item_name', 'item_batch')

# 独占一行（宽度撑满）：内容长，挤在半栏里放不下
FULL_TXN = ('name', 'batch', 'note', 'supplier')
FULL_PO = ('item_name', 'item_note')

# 面积相关列：有其一才显示「数量按 平米 / 卷」的口径选择
AREA_FIDS = ('sqm', 'rolls')

# 常驻字段的默认显示名（表格里若也配了同名列，以表格的为准）
PINNED_LABEL = {
    'name': '物料名称',
    'batch': '批次号',
    'item_name': '物料名称',
    'item_batch': '批次号',
}


def _row_unit(c, default=''):
    """取列上的单位。sqlite3.Row 没有 .get()，先判键存不存在。"""
    try:
        v = c['unit']
    except (IndexError, KeyError):
        return default
    return (v or default) if v is not None else default


def _cols_of(tid, keep_unit_fid=True):
    """取某张表的标准列（已启用的）。"""
    cols = db.tpl_cols(tid) or []
    # unit 列 v3.21 起已取消（单位改为挂在每列上），表里若还有残留也不渲染
    if not keep_unit_fid:
        cols = [c for c in cols if c['fid'] != 'unit']
    return cols


def _xcols_of(tid):
    """取某张表的自定义列，补上类型/选项/公式的默认值。"""
    out = []
    for x in (db.tpl_custom_cols(tid) or []):
        out.append({
            'fid': x['fid'],
            'label': x['label'] or '',
            'xtype': (x['xtype'] if 'xtype' in x.keys() else '') or 'text',
            'xopt': (x['xopt'] if 'xopt' in x.keys() else '') or '',
            'xform': (x['xform'] if 'xform' in x.keys() else '') or '',
            'unit': _row_unit(x),
        })
    return out


# ---------------------------------------------------------------- 对外入口

def schema(tid, scene='txn'):
    """录入表单的结构定义。

    tid   表格模板 id
    scene 'txn' 出入库 / 'po' 采购明细

    返回 dict，字段含义见文件头。列增减、改名、配单位，都从这里透出去。
    """
    is_po = (scene == 'po')
    cols = _cols_of(tid, keep_unit_fid=not is_po)
    if is_po:
        cols = _cols_of(tid, keep_unit_fid=True)

    # sqlite3.Row 不能直接 tojson，前端只要 fid / label / unit 三样
    col_defs = [{'fid': c['fid'], 'label': c['label'] or '', 'unit': _row_unit(c)}
                for c in cols]

    fids = [c['fid'] for c in cols]
    xcols = _xcols_of(tid) if not is_po else _po_xcols(tid)

    return {
        'tid': tid,
        'scene': scene,
        'cols': col_defs,
        'raw_cols': cols,
        'xcols': xcols,
        'fids': fids,
        'pinned': list(PINNED_PO if is_po else PINNED_TXN),
        'full': list(FULL_PO if is_po else FULL_TXN),
        'has_area': any(f in fids for f in AREA_FIDS),
    }


def _po_xcols(tid):
    """采购模板的自定义列（存在另一套表里，这里一并归一成同样的形状）。"""
    out = []
    try:
        rows = db.po_tpl_custom_cols(tid) or []
    except Exception:
        rows = []
    for x in rows:
        out.append({
            'fid': x['fid'],
            'label': x['label'] or '',
            'xtype': (x['xtype'] if 'xtype' in x.keys() else '') or 'text',
            'xopt': (x['xopt'] if 'xopt' in x.keys() else '') or '',
            'xform': (x['xform'] if 'xform' in x.keys() else '') or '',
            'unit': _row_unit(x),
        })
    return out


def card_fields(tid, scene='txn'):
    """卡片上真正要渲染的字段（按顺序）：常驻字段 + 表格启用的列。

    表格加了列 → 这里多一项 → 卡片自动多一个输入框；
    表格删了列 / 关了列 → 这里少一项 → 卡片自动少一个。
    这就是"卡片映射表头"，业务模块不用操心。
    """
    sch = schema(tid, scene)
    label_of = label_map(tid, scene)
    seen, out = set(), []

    def add(fid, label, unit='', xtype='text', full=False, pinned=False):
        if fid in seen:
            return
        seen.add(fid)
        out.append({'fid': fid, 'label': label, 'unit': unit,
                    'xtype': xtype, 'full': full, 'pinned': pinned})

    # 1) 常驻字段打头（物料名、批次号）
    for f in sch['pinned']:
        # 表格里配了同名列就用表格的名字（用户可能改叫「品名」），否则用默认名
        add(f, label_of.get(f) or PINNED_LABEL.get(f, f),
            full=(f in sch['full']), pinned=True)
    # 2) 表格里启用的列（常驻的跳过，避免重复）
    for c in sch['cols']:
        if c['fid'] in seen:
            continue
        add(c['fid'], c['label'], c['unit'], 'text',
            full=(c['fid'] in sch['full']))
    # 3) 自定义列
    for x in sch['xcols']:
        if x['fid'] in seen:
            continue
        add(x['fid'], x['label'], x['unit'], x.get('xtype') or 'text',
            full=(x['fid'] in sch['full']))
    return out


def label_map(tid, scene='txn'):
    """fid -> 显示名（带单位），给模板里按 fid 取标签用。"""
    sch = schema(tid, scene)
    m = {}
    for c in sch['cols']:
        u = c.get('unit') or ''
        m[c['fid']] = '%s（%s）' % (c['label'], u) if u else (c['label'] or '')
    for x in sch['xcols']:
        u = x.get('unit') or ''
        m[x['fid']] = '%s（%s）' % (x['label'], u) if u else (x['label'] or '')
    return m
