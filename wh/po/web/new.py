# -*- coding: utf-8 -*-
"""采购单 · new

    新建采购单与表单辅助计算
"""
from flask import redirect, render_template, request, url_for
from datetime import datetime
from .. import bp
from ...core import db
from ...core.util import (_log_err, int_arg, is_date, num, safe_date, take_nonce, today)
from .. import amount, query, status, summary

@bp.route('/po/new', methods=['GET', 'POST'])
def po_new():
    """新建采购单（含明细）"""
    if request.method == 'POST':
        # 一次性令牌：挡住连点造成的重复建单
        if not take_nonce(request.form.get('_n')):
            return redirect(url_for('purchase_home',
                msg='这个单子已经建过了，请不要重复提交'))
        supplier = (request.form.get('supplier') or '').strip()
        odate = safe_date(request.form.get('odate'))
        if not supplier:
            return render_template('po_new.html', err='供应商必填', 
                                   mats=_mat_choices(), today=today(), 
                                   sups=_sup_names(), units=db.unit_choices(), TPLS=db.tpls())
        ddate = (request.form.get('ddate') or '').strip()
        if ddate and not is_date(ddate):
            ddate = ''
        # 税率默认 13，没填或填了非法值都落回 13（页面默认也是 13）
        raw_tax = (request.form.get('tax_rate') or '').strip()
        tax = num(raw_tax, default=13, lo=0, hi=100) if raw_tax else 13
        # 单价口径：1=含税（默认） 0=不含税
        price_tax = 1 if (request.form.get('price_tax') or '1') == '1' else 0
        note = (request.form.get('note') or '').strip()
        names = request.form.getlist('item_name')
        qtys = request.form.getlist('item_qty')
        prices = request.form.getlist('item_price')
        units = request.form.getlist('item_unit')
        specs = request.form.getlist('item_spec')
        mids = request.form.getlist('item_mid')
        convs = request.form.getlist('item_conv')
        sunits = request.form.getlist('item_stock_unit')
        batches = request.form.getlist('item_batch')
        inotes = request.form.getlist('item_note')
        # 采购模板：决定明细填了哪些自定义列（与库存模板是两套，互不影响）
        try:
            _ptpl_id = int(request.form.get('ptpl') or 0)
        except (TypeError, ValueError):
            _ptpl_id = 0
        _ptpl_id = _ptpl_id or db.po_default_tpl_id()
        if not db.po_tpl(_ptpl_id):
            _ptpl_id = db.po_default_tpl_id()
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        try:
            with db.tx() as c:
                pono = status.next_pono(odate)
                # 模板：到货的物料和入库单存到选中的库存模板
                _tid = int(request.form.get('tpl') or 0) or db.default_tpl_id()
                if not db.tpl(_tid):
                    _tid = db.default_tpl_id()
                po_id = c.execute("INSERT INTO pos(pono,supplier,odate,ddate,status,"
                                  "tax_rate,price_tax,note,created_at,tpl_id,potpl_id)"
                                  " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                  (pono, supplier, odate, ddate,
                                   request.form.get('status') or '已下单',
                                   tax, price_tax, note, now, _tid, _ptpl_id)).lastrowid
                n = 0
                for i in range(len(names)):
                    nm = (names[i] or '').strip()
                    q = num(qtys[i] if i < len(qtys) else 0)
                    p = num(prices[i] if i < len(prices) else 0)
                    if not nm or q <= 0:
                        continue
                    mid = None
                    if i < len(mids) and str(mids[i]).strip().isdigit():
                        mid = int(mids[i])
                    cv = num(convs[i] if i < len(convs) else 1, default=1)
                    if cv <= 0:
                        cv = 1.0
                    _sp = (specs[i] if i < len(specs) else '').strip()
                    _un = (units[i] if i < len(units) else '').strip() or '个'
                    _bt = ((batches[i] if i < len(batches) else '') or '').strip()
                    if not _bt:
                        _bt = _bt_seq(pono, n)
                    _xv = _po_item_extra(_ptpl_id, request.form, i,
                                         qty=q, price=p, conv=cv)
                    c.execute("INSERT INTO po_items(po_id,material_id,name,spec,unit,conv,"
                              "stock_unit,qty,price,note,sig,batch,ptpl_id,extra)"
                              " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                              (po_id, mid, nm, _sp, _un, cv,
                               (sunits[i] if i < len(sunits) else '').strip(),
                               q, p,
                               (inotes[i] if i < len(inotes) else '').strip()[:200],
                               # v3.34 明细指纹：流水页靠它把采购实价映射到单据上
                               amount.item_sig(nm, _sp, _un, supplier),
                               # v3.35 批次号：入库时填它就把这批货计入本单
                               _bt, _ptpl_id, _xv))
                    n += 1
        except Exception:
            _log_err('采购单保存失败')
            return render_template('po_new.html', err='保存失败，请重试（详情见 warehouse.log）', 
                                   mats=_mat_choices(), today=today(), sups=_sup_names(), TPLS=db.tpls())
        if n == 0:
            db.run("DELETE FROM pos WHERE id=?", (po_id,))
            return render_template('po_new.html', err='至少要填一行物料（名称和数量）', 
                                   mats=_mat_choices(), today=today(), sups=_sup_names(), TPLS=db.tpls())
        query.touch_supplier(supplier)
        # 建单时若手工选了"已完成"却还没到货，状态必须拉回事实：
        # 否则看板/汇总会显示"已完成"，实际一件没到，账实不符。
        try:
            status.refresh_status(po_id)
        except Exception:
            _log_err('采购单状态推导失败')
        try:
            summary.save_summary(po_id)
        except Exception:
            _log_err('采购汇总保存失败')
        return redirect(url_for('po_detail', po_id=po_id,
                                msg='采购单 %s 已创建，%d 条明细' % (pono, n)))
    _tpls = db.po_tpls()
    # 参数可能被人手动改成 ?ptpl=abc，直接 int() 会 500，这里兜住
    try:
        _tid = int_arg(request.args, 'ptpl')
    except (TypeError, ValueError):
        _tid = 0
    _tid = _tid or db.po_default_tpl_id()
    _cols = db.po_tpl_cols(_tid) if _tid else []
    # 至少要能填名称和数量，否则这单存不下来（模板关掉了这两列也得兜住）
    _fids = {c['fid'] for c in _cols}
    for _need in ('name', 'qty'):
        if _need not in _fids:
            _cols = list(_cols) + [dict(fid=_need,
                label='物料名称' if _need == 'name' else '数量',
                unit='', xtype='text', xopt='', xform='')]
    return render_template('po_new.html', mats=_mat_choices(), today=today(),
                           sups=_sup_names(), units=db.unit_choices(), tax_default=13,
                           TPLS=db.tpls(), POTPLS=_tpls, PCOLS=_cols, ptpl=_tid or 0)

def _po_item_extra(ptpl_id, form, idx, qty=0, price=0, conv=1):
    """取这一行明细的自定义列值，存成 JSON。

    公式列（calc）在服务端重算：表单里填的值不可信，
    前端改了输入框就能骗过去，金额类的列必须后端算。
    """
    import json as _json
    if not ptpl_id:
        return ''
    d = {}
    for c in db.po_tpl_custom_cols(ptpl_id):
        fid = c['fid']
        vals = form.getlist('xc_%s' % fid)
        v = (vals[idx] if idx < len(vals) else '')
        xt = (c['xtype'] or 'text').strip()
        if xt == 'calc':
            v = _po_calc(c['xform'], qty=qty, price=price, conv=conv)
        elif xt == 'number':
            try:
                v = float(v) if str(v).strip() != '' else ''
            except (TypeError, ValueError):
                v = ''
        d[fid] = v
    return _json.dumps(d, ensure_ascii=False) if d else ''

def _po_calc(formula, qty=0, price=0, conv=1):
    """算自定义公式列。只放行给定变量和四则运算，别的字符一律不算。"""
    import ast as _ast, operator as _op
    f = (formula or '').strip()
    if not f:
        return ''
    if len(f) > 120:
        return ''
    _vars = {'qty': float(qty or 0), 'price': float(price or 0),
             'conv': float(conv or 1) or 1.0}
    _ops = {_ast.Add: _op.add, _ast.Sub: _op.sub, _ast.Mult: _op.mul,
            _ast.Div: _op.truediv}
    try:
        tree = _ast.parse(f, mode='eval')

        def ev(n_):
            if isinstance(n_, _ast.Expression):
                return ev(n_.body)
            if isinstance(n_, _ast.Constant) and isinstance(n_.value, (int, float)):
                return float(n_.value)
            if isinstance(n_, _ast.Name):
                if n_.id in _vars:
                    return _vars[n_.id]
                raise ValueError(n_.id)
            if isinstance(n_, _ast.BinOp) and type(n_.op) in _ops:
                b = ev(n_.right)
                if _ops[type(n_.op)] is _op.truediv and abs(b) < 1e-12:
                    return ''
                return _ops[type(n_.op)](ev(n_.left), b)
            if isinstance(n_, _ast.UnaryOp) and isinstance(n_.op, _ast.USub):
                return -ev(n_.operand)
            raise ValueError('bad')
        r = ev(tree)
        if r is None or r != r or r in (float('inf'), float('-inf')):
            return ''
        return round(r, 6)
    except Exception:
        return ''

def _bt_seq(pono, idx):
    """建单时按 单号-序号 生成批次号（与 receive.new_item_batch 同一套规则）。

    为什么不在循环里查库生成：建单是在一个事务里批量插的，此时新明细还没落库，
    查 COUNT 拿不到刚插的行，会重号。用循环序号最稳。
    """
    return '%s-%d' % (pono, idx + 1)

def _mat_choices():
    return [dict(id=m['id'], name=m['name'], spec=m['spec'] or '',
                 unit=m['unit'] or '', code=m['code'] or '')
            for m in db.q("SELECT id,name,spec,unit,code FROM materials"
                          " WHERE active=1 ORDER BY name")]

def _sup_names():
    return [r['name'] for r in db.q("SELECT name FROM suppliers WHERE active=1 ORDER BY name")]
