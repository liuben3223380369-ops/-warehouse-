# -*- coding: utf-8 -*-
"""表格模块 · 参考模板

把已经在用的**库存模板**、**采购模板**转成电子表格，存进表格模块当参考。

为什么要有这一步：
    模板（tpl / po_tpl）本来就是"一张表长什么样"的定义——有哪些列、列叫什么、
    带什么单位。它跟电子表格描述的是同一件事，只是存在两套结构里。
    把它们摊成电子表格，用户能在表格页直接看到、照着改，不必再切回模板页。

转出来的表：第一行是表头（列名，带单位写成「长（米）」），
A 列按批次号规则预填——跟新建表保持一致。

转换是**只读快照**：改参考表不会影响原模板，改原模板也不会回头改参考表。
"""

import datetime
import json

from ..core import db
from .engine import core as E
from . import batch as BT


def _row_unit(c, default=''):
    try:
        v = c['unit']
    except (IndexError, KeyError):
        return default
    return (v or default) if v is not None else default


def _head(label, unit):
    """表头文字：有单位就写成「长（米）」"""
    label = (label or '').strip()
    unit = (unit or '').strip()
    return '%s（%s）' % (label, unit) if unit else label


def from_stock_tpl(tid, with_batch=True):
    """把一个库存模板转成工作簿，返回 wb id。

    列 = 模板里**已启用**的标准列 + 自定义列（按排序）。
    """
    t = db.tpl(tid)
    if not t:
        return None, '模板不存在'
    cols = [c for c in (db.tpl_cols(tid) or []) if c['fid'] != 'unit']
    xcols = db.tpl_custom_cols(tid) or []
    rows = [c for c in cols]
    heads = [_head(c['label'], _row_unit(c)) for c in rows]
    heads += [_head(x['label'], _row_unit(x)) for x in xcols]
    return _make_book('参考·%s' % t['name'], heads, with_batch)


def from_po_tpl(tid, with_batch=True):
    """把一个采购模板转成工作簿，返回 wb id。"""
    t = db.po_tpl(tid)
    if not t:
        return None, '模板不存在'
    cols = db.po_tpl_cols(tid) or []
    xcols = db.po_tpl_custom_cols(tid) or []
    heads = [_head(c['label'], _row_unit(c)) for c in cols]
    heads += [_head(x['label'], _row_unit(x)) for x in xcols]
    return _make_book('参考·%s' % t['name'], heads, with_batch)


def _make_book(name, heads, with_batch=True):
    """按表头建一本工作簿：第一行表头，A 列批次号。"""
    book = E.Workbook(name)
    sh = book.add('Sheet1')
    sh.book = book
    # 列数至少容得下表头，多留几列给用户自己加
    need = len(heads) + (1 if with_batch else 0) + 3
    if need > sh.cols:
        sh.cols = need
    c = 0
    if with_batch:
        sh.set_raw(0, 0, BT.label())
        c = 1
    for i, h in enumerate(heads):
        sh.set_raw(0, c + i, h)
    if with_batch:
        # A 列预填编号（跳过表头行）
        BT.fill(sh, rows=BT.DEFAULT_ROWS, with_header=True)
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    db.run("INSERT INTO wb(name, data, updated) VALUES(?,?,?)",
           name, json.dumps(book.to_dict(), ensure_ascii=False), now)
    row = db.q("SELECT last_insert_rowid() AS id")[0]
    return row['id'], None


def convert_all(with_batch=True):
    """把所有模板（库存 + 采购）一次性转成参考表。

    返回 (成功数, 跳过数, 说明列表)。同名参考表会跳过，不重复建。
    """
    ok = skip = 0
    notes = []
    exist = set(r['name'] for r in db.q("SELECT name FROM wb"))
    for t in db.tpls():
        nm = '参考·%s' % t['name']
        if nm in exist:
            skip += 1
            continue
        bid, err = from_stock_tpl(t['id'], with_batch)
        if bid:
            ok += 1
            notes.append('库存模板「%s」→ 参考表' % t['name'])
        else:
            skip += 1
            notes.append('库存模板「%s」跳过：%s' % (t['name'], err))
    for t in db.po_tpls():
        nm = '参考·%s' % t['name']
        if nm in exist:
            skip += 1
            continue
        bid, err = from_po_tpl(t['id'], with_batch)
        if bid:
            ok += 1
            notes.append('采购模板「%s」→ 参考表' % t['name'])
        else:
            skip += 1
            notes.append('采购模板「%s」跳过：%s' % (t['name'], err))
    return ok, skip, notes
