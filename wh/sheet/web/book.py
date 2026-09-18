# -*- coding: utf-8 -*-
"""电子表格接口 · 工作簿级动作

    工作表管理 / 名称 / 撤销重做 / 保存 / 公式预览
"""
from .common import *                                  # noqa: F401,F403
from .common import (_restore, _snap)   # 星号不导入下划线名，必须显式写
from .common import E, ST
from .edit import _j                                    # 值 → JSON 安全值

def a_sheets(book, st, sh, g, rect):
    op = g.get('op') or 'add'
    if op == 'add':
        s = book.add(g.get('name') or '')
        book.active = book.sheets.index(s)
    elif op == 'del':
        if len(book.sheets) <= 1:
            return {'err': '至少保留一张表'}
        book.remove(g.get('name') or sh.name)
        book.active = min(book.active, len(book.sheets) - 1)
    elif op == 'rename':
        old = g.get('name') or sh.name
        nm = (g.get('new') or '').strip()
        if not nm:
            return {'err': '表名不能为空'}
        if book.sheet(nm) and nm.lower() != old.lower():
            return {'err': '已经有同名的工作表了'}
        book.rename(old, nm)
    elif op == 'copy':
        src = sh
        nm = (g.get('new') or (src.name + ' 副本')).strip()
        i = 2
        while book.sheet(nm):
            nm = '%s%d' % (src.name + ' 副本', i)
            i += 1
        ns = E.Sheet.from_dict(src.to_dict(), book)
        ns.name = nm
        book.sheets.insert(book.sheets.index(src) + 1, ns)
        book.active = book.sheets.index(ns)
    elif op == 'order':
        names = g.get('names') or []
        book.order(names)
    elif op == 'active':
        s = book.sheet(g.get('name'))
        if s:
            book.active = book.sheets.index(s)
    elif op == 'color':
        s = book.sheet(g.get('name')) or sh
        s.tab_color = (g.get('color') or '')
    elif op == 'hide':
        s = book.sheet(g.get('name')) or sh
        s.hidden = bool(g.get('v'))
    return {}

def a_undo(book, st, sh, g, rect):
    if not st['undo']:
        return {'err': '没有可撤销的操作'}
    item = st['undo'].pop()
    cur = book.sheet(item['sheet']) or sh
    snap = _snap(cur,
                 min(x[0] for x in item['snap']), min(x[1] for x in item['snap']),
                 max(x[0] for x in item['snap']), max(x[1] for x in item['snap']))
    st['redo'].append({'label': item['label'], 'sheet': item['sheet'],
                       'snap': snap})
    _restore(cur, item['snap'])
    return {'label': item['label'], 'sheet': item['sheet']}

def a_redo(book, st, sh, g, rect):
    if not st['redo']:
        return {'err': '没有可重做的操作'}
    item = st['redo'].pop()
    cur = book.sheet(item['sheet']) or sh
    snap = _snap(cur,
                 min(x[0] for x in item['snap']), min(x[1] for x in item['snap']),
                 max(x[0] for x in item['snap']), max(x[1] for x in item['snap']))
    st['undo'].append({'label': item['label'], 'sheet': item['sheet'],
                       'snap': snap})
    _restore(cur, item['snap'])
    return {'label': item['label'], 'sheet': item['sheet']}



def a_names(book, st, sh, g, rect):
    op = g.get('op') or 'define'
    if op == 'define':
        book.define_name(g.get('name'), g.get('ref'))
    elif op == 'del':
        book.del_name(g.get('name'))
    return {'names': book.names}



def a_save(book, st, sh, g, rect):
    return {}

def a_eval(book, st, sh, g, rect):
    """给界面实时预览一个公式（不写入）"""
    expr = g.get('expr') or ''
    try:
        from ..kernel import parser as P
        ast = P.parse(expr)
        v = sh._eval_ast(ast, rect[0], rect[1], 0)
        return {'v': _j(v), 't': ST.format_value(v, g.get('fmt') or '')}
    except Exception as e:
        return {'err': str(e)}
