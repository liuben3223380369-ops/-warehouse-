# -*- coding: utf-8 -*-
"""UI 模块 · 导航结构

底部导航只放 7 个一级入口，一行排开，不再堆成两三行。

三个"并入"：
    模板  → 制表（二级）
    流水  → 统计（二级）
    盘点  → 库存（二级）

二级菜单只在进入对应模块时出现，用完即收，不常驻占地方。

数据结构：
    MAIN: (endpoint, 图标, 名称, 归属该一级的所有 endpoint 集合)
    SUB : 一级 endpoint -> [(显示名, 跳转 endpoint, 算作同一项的 endpoint 集合)]
"""
import sys

# 一级：首页 / 入库 / 出库 / 制表 / 采购 / 库存 / 统计
MAIN = [
    ('index', '📊', '首页',
     ('index', 'sysinfo', 'sysfix', 'sysbackup', 'sys_purge_po_txns')),

    ('in', '📥', '入库',
     ('in', 'in_list', 'in_import', 'imp', 'import_tpl')),

    ('out', '📤', '出库',
     ('out', 'out_list', 'out_import')),

    ('sheet_index', '📗', '制表',
     ('sheet_index', 'sheet_open', 'sheet_new', 'sheet_rename', 'sheet_del',
      'sheet_api', 'sheet_import', 'sheet_export_xlsx', 'sheet_export_csv',
      'sheet_ref',
      'tpls', 'tpl_add', 'tpl_rename', 'tpl_del', 'tpl_cols_set',
      'tpl_col_add', 'tpl_col_del', 'tpl_from_head', 'columns')),

    ('purchase_home', '🛒', '采购',
     ('purchase_home', 'po_new', 'po_detail', 'po_summary',
      'po_tpls', 'po_tpl_add', 'po_tpl_rename', 'po_tpl_del',
      'po_tpl_cols_set', 'po_tpl_col_add', 'po_tpl_col_del',
      'po_tpl_from_head', 'po_tpl_use',
      'po_status', 'po_item_add', 'po_item_del', 'po_item_unit',
      'po_receive', 'po_receive_del', 'po_pay', 'po_pay_del', 'po_del',
      'suppliers', 'supplier_add', 'export_po_xlsx')),

    ('materials', '📦', '库存',
     ('materials', 'material_edit', 'material_history', 'material_del',
      'batch', 'table_save',
      'stock', 'export_xlsx', 'export_csv',
      'stk_list', 'stk_new', 'stk_detail', 'stk_save', 'stk_adjust',
      'stk_unadjust', 'stk_status', 'stk_del', 'stk_export', 'stk_print')),

    ('stat_center', '📈', '统计',
     ('stat_center', 'stat_export',
      'txns', 'txns_batch', 'txn', 'txn_del', 'txn_import', 'txn_import_tpl',
      'report')),
]

# 二级菜单
SUB = {
    'in': [
        ('录入', 'in', ('in', 'in_import', 'imp', 'import_tpl')),
        ('单据', 'in_list', ('in_list',)),
    ],
    'out': [
        ('录入', 'out', ('out', 'out_import')),
        ('单据', 'out_list', ('out_list',)),
    ],
    'sheet_index': [
        ('制表台', 'sheet_index',
         ('sheet_index', 'sheet_open', 'sheet_new', 'sheet_rename',
          'sheet_del', 'sheet_api', 'sheet_import', 'sheet_export_xlsx',
          'sheet_export_csv', 'sheet_ref')),
        ('模板', 'tpls',
         ('tpls', 'tpl_add', 'tpl_rename', 'tpl_del', 'tpl_cols_set',
          'tpl_col_add', 'tpl_col_del', 'tpl_from_head', 'columns')),
    ],
    'purchase_home': [
        ('台账', 'purchase_home', ('purchase_home', 'po_new', 'po_detail',
                                   'po_status', 'po_item_add', 'po_item_del',
                                   'po_item_unit', 'po_receive',
                                   'po_receive_del', 'po_pay', 'po_pay_del',
                                   'po_del', 'export_po_xlsx')),
        ('汇总', 'po_summary', ('po_summary',)),
        ('模板', 'po_tpls',
         ('po_tpls', 'po_tpl_add', 'po_tpl_rename', 'po_tpl_del',
          'po_tpl_cols_set', 'po_tpl_col_add', 'po_tpl_col_del',
          'po_tpl_from_head', 'po_tpl_use')),
        ('供应商', 'suppliers', ('suppliers', 'supplier_add')),
    ],
    'materials': [
        ('档案', 'materials', ('materials', 'material_edit',
                               'material_history', 'material_del',
                               'batch', 'table_save')),
        ('库存', 'stock', ('stock', 'export_xlsx', 'export_csv')),
        ('盘点', 'stk_list',
         ('stk_list', 'stk_new', 'stk_detail', 'stk_save', 'stk_adjust',
          'stk_unadjust', 'stk_status', 'stk_del', 'stk_export',
          'stk_print')),
    ],
    'stat_center': [
        ('统计', 'stat_center', ('stat_center', 'stat_export')),
        ('流水', 'txns', ('txns', 'txns_batch', 'txn', 'txn_del',
                          'txn_import', 'txn_import_tpl')),
        ('月报', 'report', ('report',)),
    ],
}


def _ep():
    try:
        from flask import request
        return request.endpoint or ''
    except Exception:
        return ''


def _href(ep):
    """生成链接。万一某个入口需要参数（模板里漏传会 500），退化成 # 而不是崩掉"""
    try:
        from flask import url_for
        return url_for(ep)
    except Exception:
        return '#'


def main_items(ep=None):
    """底部一级项：[{ep, href, icon, label, on}]"""
    ep = _ep() if ep is None else ep
    out = []
    for e, icon, label, eps in MAIN:
        out.append({'ep': e, 'href': _href(e), 'icon': icon, 'label': label,
                    'on': 1 if ep in eps else 0})
    return out


def sub_items(ep=None):
    """当前模块的二级项；不属于任何模块或该模块没有二级时返回 []"""
    ep = _ep() if ep is None else ep
    for e, _icon, _label, eps in MAIN:
        if ep in eps:
            items = SUB.get(e) or []
            return [{'ep': it[1], 'href': _href(it[1]), 'label': it[0],
                     'on': 1 if ep in it[2] else 0} for it in items]
    return []


def current_module(ep=None):
    """当前所在的一级模块名称，没有则返回空串"""
    ep = _ep() if ep is None else ep
    for e, _icon, label, eps in MAIN:
        if ep in eps:
            return label
    return ''


def report():
    """自检用：列出导航结构，确认没有 endpoint 漏配"""
    lines = []
    known = set()
    for e, _i, label, eps in MAIN:
        known |= set(eps)
        lines.append('%-14s %-4s 二级 %d 项' % (e, label, len(SUB.get(e) or [])))
    lines.append('已覆盖 endpoint：%d' % len(known))

    # 找出登记了路由却没进导航的（漏配会让页面"不高亮任何一项"）
    try:
        root = __name__.split('.')[0]
        from ..dispatch import create_app
        app = create_app(init_db=False, verbose=False)
        missing = sorted(r.endpoint for r in app.url_map.iter_rules()
                         if r.endpoint != 'static' and r.endpoint not in known)
        lines.append('未纳入导航：%s' % (', '.join(missing) or '无'))
    except Exception as e:
        lines.append('未纳入导航：检查失败 %s' % e)
    lines.append('Python %s' % sys.version.split()[0])
    return '\n'.join(lines)
