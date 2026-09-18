# -*- coding: utf-8 -*-
"""采购单 · settle

    到货、付款、删除
"""
from flask import redirect, request, url_for
from datetime import datetime
from .common import _po_sync
from .. import bp
from ...core import db
from ...core.util import (num, safe_date, take_nonce)
from .. import receive

@bp.route('/po/<int:po_id>/receive', methods=['POST'])
def po_receive(po_id):
    if not take_nonce(request.form.get('_n')):
        return redirect(url_for('po_detail', po_id=po_id,
                                err='这一下点重了，没有重复收货，请刷新后重试'))
    iid = request.form.get('item_id')
    if not str(iid or '').isdigit():
        return redirect(url_for('po_detail', po_id=po_id, err='请选择要收货的物料'))
    rdate = safe_date(request.form.get('rdate'))
    q = request.form.get('qty')
    p = request.form.get('price')
    ok, msg = receive.receive(int(iid), rdate, q, p,
                               (request.form.get('note') or '').strip())
    return redirect(url_for('po_detail', po_id=po_id,
                            msg=msg if ok else '', err='' if ok else msg))

@bp.route('/po/receive/del/<int:rid>')
def po_receive_del(rid):
    r = db.q("SELECT i.po_id FROM po_receipts r JOIN po_items i ON i.id=r.item_id"
             " WHERE r.id=?", rid)
    if r:
        pid = r[0]['po_id']
        ok, msg = receive.unreceive(rid)
        return redirect(url_for('po_detail', po_id=pid,
                                msg=msg if ok else '', err='' if ok else msg))
    return redirect(url_for('purchase_home'))

@bp.route('/po/<int:po_id>/pay', methods=['POST'])
def po_pay(po_id):
    if not take_nonce(request.form.get('_n')):
        return redirect(url_for('po_detail', po_id=po_id,
                                err='这一下点重了，没有重复付款，请刷新后重试'))
    amt = num(request.form.get('amount'))
    if amt <= 0:
        return redirect(url_for('po_detail', po_id=po_id, err='付款金额要大于 0'))
    db.run("INSERT INTO po_payments(po_id,pdate,amount,method,note,created_at)"
           " VALUES(?,?,?,?,?,?)", po_id, safe_date(request.form.get('pdate')),
           amt, (request.form.get('method') or '转账').strip(),
           (request.form.get('note') or '').strip(),
           datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    _po_sync(po_id)
    return redirect(url_for('po_detail', po_id=po_id, msg='已登记付款 %.2f' % amt))

@bp.route('/po/pay/del/<int:pid>')
def po_pay_del(pid):
    r = db.q("SELECT po_id FROM po_payments WHERE id=?", pid)
    if r:
        p = r[0]['po_id']
        db.run("DELETE FROM po_payments WHERE id=?", pid)
        _po_sync(p)
        return redirect(url_for('po_detail', po_id=p, msg='付款记录已删除'))
    return redirect(url_for('purchase_home'))

@bp.route('/po/del/<int:po_id>')
def po_del(po_id):
    # 采购与库存独立：直接删单即可，ON DELETE CASCADE 会带走明细和到货记录。
    # 不再去动任何入库单——采购只做状态登记，删单不该影响库存。
    n = db.q("SELECT COUNT(*) c FROM po_receipts r JOIN po_items i ON i.id=r.item_id"
             " WHERE i.po_id=?", po_id)[0]['c']
    db.run("DELETE FROM pos WHERE id=?", po_id)
    return redirect(url_for('purchase_home',
        msg='采购单已删除' + ('（含 %d 条到货记录，不影响库存）' % n if n else '')))
