# -*- coding: utf-8 -*-
"""单头金额 —— 订购额、已付、欠款。

欠款口径：欠款 = 到货价税合计 − 已付。付给供应商的钱本来就含税，
且只按实际到货算（没到货的不该算欠款）。

依赖：amount
"""
from ..core import db
from .amount import net_price, line_amount



def po_head(po_id):
    """取单头的税率与"单价是否含税"。所有金额计算都从这里取口径，避免各处写法不一"""
    r = db.q("SELECT tax_rate, price_tax FROM pos WHERE id=?", po_id)
    if not r:
        return 0.0, 1
    try:
        tax = float(r[0]['tax_rate'] or 0)
    except (TypeError, ValueError):
        tax = 0.0
    try:
        pt = int(r[0]['price_tax']) if r[0]['price_tax'] is not None else 1
    except (TypeError, ValueError):
        pt = 1
    return tax, (1 if pt else 0)


def po_totals(po_id):
    """整单汇总：订购/到货/未到 的数量与金额（金额一律不含税，税额单列）"""
    items = db.q("SELECT * FROM po_items WHERE po_id=? ORDER BY id", po_id)
    tax, pt = po_head(po_id)
    tot_qty = tot_amt = tot_tax = 0.0
    recv_qty = recv_amt = 0.0
    real_amt = 0.0          # 按到货实价计的金额（可能与订购价不同）
    for it in items:
        a, t, _ = line_amount(it['qty'], it['price'], tax, pt)
        tot_qty += float(it['qty'] or 0)
        tot_amt += a
        tot_tax += t
        rq = float(it['recv_qty'] or 0)
        recv_qty += rq
        # 到货金额按"实收"算：先取到货记录的实价，没有就退回订购价
        got = db.q("SELECT COALESCE(SUM(qty),0) q, COALESCE(SUM(qty*price),0) s"
                   " FROM po_receipts WHERE item_id=?", it['id'])
        rqty = float(got[0]['q'] or 0)
        real = float(got[0]['s'] or 0)
        if rqty:
            # 到货实价与订购价同一口径，先折成不含税再汇总
            real_amt += round(rqty * net_price(real / rqty, tax, pt), 2)
            recv_amt += round(rqty * net_price(real / rqty, tax, pt), 2)
        else:
            recv_amt += round(rq * net_price(it['price'], tax, pt), 2)
    return {
        'qty': tot_qty, 'amount': round(tot_amt, 2), 'tax': round(tot_tax, 2),
        'total': round(tot_amt + tot_tax, 2),
        'recv_qty': recv_qty, 'recv_amount': round(recv_amt, 2),
        'open_qty': round(tot_qty - recv_qty, 2),
        'real_amount': round(real_amt, 2),
        'tax_rate': tax,
        'price_tax': pt,
    }


def po_totals_many(po_ids):
    """批量版 po_totals：一次算多张单，口径与 po_totals 逐字段一致。

    为什么不能简化成 SUM(qty*price)：到货金额要按「实价折不含税」，
    没有到货记录的还要退回订购价，简化算法会跟详情页/汇总页对不上。
    列表页 v3.65 的批量用的是简化口径，导出必须走这个精确版。
    """
    po_ids = [int(i) for i in (po_ids or []) if i]
    if not po_ids:
        return {}
    ph = ','.join('?' * len(po_ids))
    heads = {}
    for r in db.q("SELECT id, tax_rate, price_tax FROM pos WHERE id IN (%s)" % ph, *po_ids):
        try:
            tax = float(r['tax_rate'] or 0)
        except (TypeError, ValueError):
            tax = 0.0
        try:
            pt = int(r['price_tax']) if r['price_tax'] is not None else 1
        except (TypeError, ValueError):
            pt = 1
        heads[r['id']] = (tax, pt)
    items_by_po, item_ids = {}, []
    for r in db.q("SELECT * FROM po_items WHERE po_id IN (%s) ORDER BY id" % ph, *po_ids):
        items_by_po.setdefault(r['po_id'], []).append(dict(r))
        item_ids.append(r['id'])
    rec_by_item = {}
    if item_ids:
        ph2 = ','.join('?' * len(item_ids))
        for r in db.q("SELECT item_id, COALESCE(SUM(qty),0) q,"
                      " COALESCE(SUM(qty*price),0) s"
                      " FROM po_receipts WHERE item_id IN (%s) GROUP BY item_id" % ph2,
                      *item_ids):
            rec_by_item[r['item_id']] = (float(r['q'] or 0), float(r['s'] or 0))
    out = {}
    for pid, items in items_by_po.items():
        tax, pt = heads.get(pid, (0.0, 1))
        tot_qty = tot_amt = tot_tax = 0.0
        recv_qty = recv_amt = 0.0
        real_amt = 0.0
        for it in items:
            a, t, _ = line_amount(it['qty'], it['price'], tax, pt)
            tot_qty += float(it['qty'] or 0)
            tot_amt += a
            tot_tax += t
            rq = float(it['recv_qty'] or 0)
            recv_qty += rq
            rqty, real = rec_by_item.get(it['id'], (0.0, 0.0))
            if rqty:
                v = round(rqty * net_price(real / rqty, tax, pt), 2)
                real_amt += v
                recv_amt += v
            else:
                recv_amt += round(rq * net_price(it['price'], tax, pt), 2)
        out[pid] = {
            'qty': tot_qty, 'amount': round(tot_amt, 2), 'tax': round(tot_tax, 2),
            'total': round(tot_amt + tot_tax, 2),
            'recv_qty': recv_qty, 'recv_amount': round(recv_amt, 2),
            'open_qty': round(tot_qty - recv_qty, 2),
            'real_amount': round(real_amt, 2),
            'tax_rate': tax, 'price_tax': pt,
        }
    return out


def paid_amount(po_id):
    got = db.q("SELECT COALESCE(SUM(amount),0) s FROM po_payments WHERE po_id=?", po_id)
    return round(float(got[0]['s'] or 0), 2)


def owed(po_id):
    """欠款 = 到货价税合计 − 已付。只按实际到货算，没到货的不该付钱"""
    t = po_totals(po_id)
    _, tax_amt, recv_total = line_amount(1, t['recv_amount'], t['tax_rate'], 0)
    return round(recv_total - paid_amount(po_id), 2), recv_total
