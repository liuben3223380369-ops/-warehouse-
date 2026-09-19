# -*- coding: utf-8 -*-
"""采购单 · detail

    采购单详情、状态流转、明细增删
"""
from flask import redirect, render_template, request, url_for
from .common import _po_sync
from .new import _mat_choices
from .. import bp
from ...core import db
from ...core.util import (num, take_nonce, today)
from .. import amount, head, receive, status

@bp.route('/po/<int:po_id>')
def po_detail(po_id):
    po = db.q("SELECT * FROM pos WHERE id=?", po_id)
    if not po:
        return render_template('error.html', code=404, title='找不到采购单',
                               detail='它可能已被删除。'), 404
    po = po[0]
    items = []
    for it in db.q("SELECT * FROM po_items WHERE po_id=? ORDER BY id", po_id):
        d = dict(it)
        a, t, tt = amount.line_amount(d['qty'], d['price'], po['tax_rate'])
        d['amount'], d['tax'], d['total'] = a, t, tt
        d['remain'] = round(float(d['qty']) - float(d['recv_qty']), 2)
        d['recv_amount'] = float(db.q(
            "SELECT COALESCE(SUM(qty*price),0) s FROM po_receipts WHERE item_id=?",
            d['id'])[0]['s'] or 0)
        d['done'] = d['remain'] <= 1e-9
        # 自定义列的值（JSON）：模板换了列名也能按当前模板显示
        d['xv'] = {}
        try:
            import json as _json
            d['xv'] = _json.loads(d.get('extra') or '{}') or {}
        except Exception:
            d['xv'] = {}
        items.append(d)
    recs = db.q("SELECT r.*, i.name, i.unit FROM po_receipts r JOIN po_items i"
                " ON i.id=r.item_id WHERE i.po_id=? ORDER BY r.rdate DESC, r.id DESC", po_id)
    pays = db.q("SELECT * FROM po_payments WHERE po_id=? ORDER BY pdate DESC, id DESC", po_id)
    t = head.po_totals(po_id)
    owed_amt, recv_total = head.owed(po_id)
    return render_template('po.html', po=po, items=items, recs=recs, pays=pays,
                           t=t, paid=head.paid_amount(po_id),
                           owed=owed_amt, recv_total=recv_total,
                           STATUS=status.STATUS, today=today(), mats=_mat_choices(),
                           units=db.unit_choices(),
                           msg=request.args.get('msg', ''), err=request.args.get('err', ''))

@bp.route('/po/<int:po_id>/status', methods=['POST'])
def po_status(po_id):
    if not take_nonce(request.form.get('_n')):
        return redirect(url_for('po_detail', po_id=po_id,
                                err='这一下点重了，状态没改，请刷新后重试'))
    st = (request.form.get('status') or '').strip()
    if st in status.STATUS:
        db.run("UPDATE pos SET status=? WHERE id=?", st, po_id)
        # 手工选了「已下单/部分到货/已完成」这三种业务状态时，必须按到货
        # 事实再核定一次：没到齐的货不能标成已完成，否则采购台和汇总页
        # 全显示已完成、实物却没到，这种失真的数字比报错更难查。
        # 「草稿/已取消」是人工终态，derive_status 会原样保留，不受影响。
        if st not in ('草稿', '已取消'):
            status.refresh_status(po_id)
            got = db.q("SELECT status FROM pos WHERE id=?", po_id)
            real = got[0]['status'] if got else st
            if real != st:
                return redirect(url_for('po_detail', po_id=po_id,
                    msg='状态按到货事实定为「%s」（%s不成立）' % (real, st)))
    return redirect(url_for('po_detail', po_id=po_id, msg='状态已改为「%s」' % st))

@bp.route('/po/<int:po_id>/item/add', methods=['POST'])
def po_item_add(po_id):
    if not take_nonce(request.form.get('_n')):
        return redirect(url_for('po_detail', po_id=po_id,
                                err='这一下点重了，明细没加，请刷新后重试'))
    nm = (request.form.get('name') or '').strip()
    q = num(request.form.get('qty'))
    p = num(request.form.get('price'))
    if not nm or q <= 0:
        return redirect(url_for('po_detail', po_id=po_id, err='物料名称和数量都要填'))
    mid = request.form.get('material_id')
    mid = int(mid) if str(mid or '').isdigit() else None
    cv = num(request.form.get('conv'), default=1)
    if cv <= 0:
        cv = 1.0
    _sp = (request.form.get('spec') or '').strip()
    _un = (request.form.get('unit') or '').strip() or '个'
    # 指纹要带上这张单的供应商，同名不同供应商的货不能共用一个价
    _sup = ''
    try:
        _r = db.q("SELECT supplier FROM pos WHERE id=?", po_id)
        _sup = (_r[0]['supplier'] or '') if _r else ''
    except Exception:
        _sup = ''
    _bt = ((request.form.get('batch') or '').strip()
           or receive.new_item_batch(po_id))
    db.run("INSERT INTO po_items(po_id,material_id,name,spec,unit,conv,stock_unit,"
           "qty,price,note,sig,batch,ptpl_id,extra) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
           po_id, mid, nm, _sp, _un, cv,
           (request.form.get('stock_unit') or '').strip(), q, p,
           (request.form.get('note') or '').strip(),
           amount.item_sig(nm, _sp, _un, _sup), _bt, 0, '')
    status.refresh_status(po_id)
    _po_sync(po_id)
    return redirect(url_for('po_detail', po_id=po_id,
        msg='已加入 %s，批次号 %s（入库时填它就能计入本单）' % (nm, _bt) if _bt
        else '已加入 %s' % nm))

@bp.route('/po/item/unit', methods=['POST'])
def po_item_unit():
    """随时改采购单位 / 换算率 / 库存单位 / 单价。
    改单位只影响之后的到货，已入库存量不动（历史记的是当时实际入库数）。"""
    if not take_nonce(request.form.get('_n')):
        rid = request.form.get('item_id')
        hit = db.q("SELECT po_id FROM po_items WHERE id=?", rid) if str(rid or '').isdigit() else None
        if hit:
            return redirect(url_for('po_detail', po_id=hit[0]['po_id'],
                                    err='这一下点重了，单位没改，请刷新后重试'))
        return redirect(url_for('purchase_home'))
    iid = request.form.get('item_id')
    if not str(iid or '').isdigit():
        return redirect(url_for('purchase_home'))
    it = db.q("SELECT po_id FROM po_items WHERE id=?", int(iid))
    if not it:
        return redirect(url_for('purchase_home'))
    pid = it[0]['po_id']
    ok, msg = receive.set_item_unit(
        int(iid),
        unit=request.form.get('unit'),
        conv=request.form.get('conv'),
        stock_unit=request.form.get('stock_unit'),
        price=request.form.get('price'))
    return redirect(url_for('po_detail', po_id=pid,
                            msg=msg if ok else '', err='' if ok else msg))

@bp.route('/po/item/del/<int:iid>')
def po_item_del(iid):
    it = db.q("SELECT po_id FROM po_items WHERE id=?", iid)
    if it:
        pid = it[0]['po_id']
        ok, msg = receive.delete_item(iid)
        return redirect(url_for('po_detail', po_id=pid,
                                msg=msg if ok else '', err='' if ok else msg))
    return redirect(url_for('purchase_home'))
