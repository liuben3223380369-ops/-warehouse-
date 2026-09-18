# -*- coding: utf-8 -*-
"""状态与单号 —— 状态由到货数据推导，不手工维护。

为什么推导而不存：存了就会出现"明细都到齐了，单头还写着部分到货"这种脏数据。

依赖：仅 core.db
"""
from datetime import datetime

from ..core import db

STATUS = ['草稿', '已下单', '部分到货', '已完成', '已取消']


def derive_status(po_id):
    head = db.q("SELECT status FROM pos WHERE id=?", po_id)
    if not head:
        return None
    cur = head[0]['status']
    if cur in ('草稿', '已取消'):
        return cur                      # 人工状态，不自动改
    items = db.q("SELECT qty, recv_qty FROM po_items WHERE po_id=?", po_id)
    if not items:
        return cur
    tot = sum(float(i['qty'] or 0) for i in items)
    got = sum(float(i['recv_qty'] or 0) for i in items)
    if got <= 0:
        return '已下单'
    if got >= tot - 1e-9:
        return '已完成'
    return '部分到货'


def refresh_status(po_id):
    """到货/取消到货后调用，把推导出的状态写回单头"""
    st = derive_status(po_id)
    if st:
        db.run("UPDATE pos SET status=? WHERE id=?", st, po_id)
    return st


# ---------- 单号：CG + 日期 + 当日流水，保证唯一 ----------
def next_pono(odate=None):
    d = (odate or datetime.now().strftime('%Y-%m-%d')).replace('-', '')
    pre = 'CG' + d + '-'
    last = db.q("SELECT pono FROM pos WHERE pono LIKE ? ORDER BY pono DESC LIMIT 1", pre + '%')
    n = 1
    if last:
        try:
            n = int(last[0]['pono'].rsplit('-', 1)[-1]) + 1
        except (ValueError, IndexError):
            n = 1
    while db.q("SELECT id FROM pos WHERE pono=?", pre + '%03d' % n):
        n += 1
    return pre + '%03d' % n
