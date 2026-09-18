# -*- coding: utf-8 -*-
"""页面聚合查询 —— 采购首页看板、供应商列表。

依赖：仅 core.db
"""
from datetime import datetime

from ..core import db



# ---------- 供应商：从采购单自动沉淀，也支持手工维护 ----------
def touch_supplier(name):
    if not (name or '').strip():
        return
    if not db.q("SELECT id FROM suppliers WHERE name=?", name.strip()):
        db.run("INSERT INTO suppliers(name) VALUES(?)", name.strip())


def supplier_list():
    rows = []
    for s in db.q("SELECT * FROM suppliers ORDER BY active DESC, name"):
        name = s['name']
        g = db.q("SELECT COUNT(*) c, COALESCE(SUM(i.qty*i.price),0) amt,"
                 " COALESCE(SUM(i.recv_qty),0) rq FROM pos p JOIN po_items i ON i.po_id=p.id"
                 " WHERE p.supplier=? AND p.status<>'已取消'", name)[0]
        paid = db.q("SELECT COALESCE(SUM(m.amount),0) s FROM po_payments m"
                    " JOIN pos p ON p.id=m.po_id WHERE p.supplier=?", name)[0]['s']
        owed_ = round(float(g['amt'] or 0) - float(paid or 0), 2)
        rows.append(dict(id=s['id'], name=name, contact=s['contact'], phone=s['phone'],
                         orders=g['c'], amount=round(float(g['amt'] or 0), 2),
                         recv_qty=g['rq'], paid=round(float(paid or 0), 2),
                         owed=owed_, active=s['active']))
    return rows


# ---------- 看板：采购首页要用的汇总 ----------
def dashboard():
    ym = datetime.now().strftime('%Y-%m') + '%'
    def sc(sql, *a):
        r = db.q(sql, *a)
        return float(r[0][0] or 0) if r and r[0][0] is not None else 0.0
    month_amt = sc("SELECT COALESCE(SUM(i.qty*i.price),0) FROM po_items i JOIN pos p"
                   " ON p.id=i.po_id WHERE p.odate LIKE ? AND p.status<>'已取消'", ym)
    open_cnt = sc("SELECT COUNT(*) FROM pos WHERE status IN ('已下单','部分到货')")
    # 逾期：过了要求交期还没完成
    today = datetime.now().strftime('%Y-%m-%d')
    overdue = sc("SELECT COUNT(*) FROM pos WHERE ddate<>'' AND ddate<?"
                 " AND status IN ('已下单','部分到货')", today)
    draft = sc("SELECT COUNT(*) FROM pos WHERE status='草稿'")
    paid_m = sc("SELECT COALESCE(SUM(amount),0) FROM po_payments WHERE pdate LIKE ?", ym)
    return {
        'month_amount': round(month_amt, 2),
        'open_orders': int(open_cnt),
        'overdue': int(overdue),
        'draft': int(draft),
        'paid_month': round(paid_m, 2),
    }
