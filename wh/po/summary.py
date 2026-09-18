# -*- coding: utf-8 -*-
"""汇总快照 —— 把整单金额落成一张表，便于单独查看与历史对账。

依赖：head
"""
from datetime import datetime

from ..core import db
from .amount import _log_err                 # noqa: F401
from .head import po_totals, paid_amount, owed



# ---------- 汇总快照：把整单金额落成一张表，便于单独查看与历史对账 ----------
def save_summary(po_id):
    """重算并保存采购单汇总快照。

    为什么要落库：金额原本全是现算的，改了税率/单价后历史单的金额也跟着变，
    跟当时打印出来给供应商的凭证对不上。落一份快照，翻旧单看到的就是当时的数。
    建单、改明细、到货、撤销到货、付款、删付款、改税率 都要调一次。
    """
    t = po_totals(po_id)
    paid = paid_amount(po_id)
    _, recv_total = owed(po_id)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    db.run("INSERT INTO po_summary(po_id,qty,amount,tax,total,recv_qty,recv_amount,"
           "recv_total,paid,owed,open_qty,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)"
           " ON CONFLICT(po_id) DO UPDATE SET qty=excluded.qty, amount=excluded.amount,"
           " tax=excluded.tax, total=excluded.total, recv_qty=excluded.recv_qty,"
           " recv_amount=excluded.recv_amount, recv_total=excluded.recv_total,"
           " paid=excluded.paid, owed=excluded.owed, open_qty=excluded.open_qty,"
           " updated_at=excluded.updated_at",
           po_id, t['qty'], t['amount'], t['tax'], t['total'],
           t['recv_qty'], t['recv_amount'], recv_total,
           paid, round(recv_total - paid, 2), t['open_qty'], now)
    return get_summary(po_id)


def get_summary(po_id):
    r = db.q("SELECT * FROM po_summary WHERE po_id=?", po_id)
    return dict(r[0]) if r else None


def summary_list(st='', sup='', kw='', m=''):
    """汇总清单：可直接看，也可导出"""
    w, a = [], []
    if st:
        w.append("p.status=?"); a.append(st)
    if sup:
        w.append("p.supplier=?"); a.append(sup)
    if kw:
        w.append("(p.pono LIKE ? OR p.supplier LIKE ?)"); a += ['%%%s%%' % kw] * 2
    if m:
        w.append("p.odate LIKE ?"); a.append(m + '%')
    where = (" WHERE " + " AND ".join(w)) if w else ""
    _sql = ("SELECT p.id,p.pono,p.odate,p.ddate,p.supplier,p.status,p.tax_rate,"
            " p.price_tax, s.* FROM pos p"
            " LEFT JOIN po_summary s ON s.po_id=p.id"
            + where + " ORDER BY p.odate DESC, p.id DESC LIMIT 500")
    raw = list(db.q(_sql, *a))
    # 快照缺失时 LEFT JOIN 右半边全是 NULL，金额会被当成 0 显示。
    # 这在付款已登记却没走到 save_summary 的场景下（老库升级、异常中断、
    # 直接往 po_payments 插数据）会让汇总页把"已付"显示成 0，看着像没付过。
    # 这里补齐后再查一次，宁可多算一遍也不能显示错金额。
    miss = [r['id'] for r in raw if r['po_id'] is None]
    if miss:
        try:
            with db.tx():
                for _pid in miss[:500]:
                    save_summary(_pid)
        except Exception as _e:
            _log_err('采购汇总快照补齐失败（共 %d 张）' % len(miss),
                     'save_summary: %s' % _e)
        raw = list(db.q(_sql, *a))
    rows = []
    for r in raw:
        d = dict(r)
        for k in ('qty', 'amount', 'tax', 'total', 'recv_qty', 'recv_amount',
                  'recv_total', 'paid', 'owed', 'open_qty'):
            d[k] = round(float(d.get(k) or 0), 2)
        rows.append(d)
    tot = {k: round(sum(r[k] for r in rows), 2)
           for k in ('amount', 'tax', 'total', 'recv_amount', 'recv_total',
                     'paid', 'owed')}
    return rows, tot


def sync_all():
    """把没有快照或已过期的采购单全部重算一遍（升级老库、或发现数据对不上时用）

    必须放进单个事务：save_summary 每张单要写 po_summary 一次，
    逐条提交时 300 张单就是 300 次 fsync，实测 40 秒以上打不开页面；
    合成一个事务后只有收尾一次提交，量级下降两个数量级。
    """
    n = 0
    with db.tx():
        for r in db.q("SELECT id FROM pos ORDER BY id"):
            save_summary(r['id'])
            n += 1
    return n
