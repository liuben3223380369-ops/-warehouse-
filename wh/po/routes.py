# -*- coding: utf-8 -*-
"""采购模块：采购单、明细、模板"""
from flask import request, redirect, url_for, Response, render_template
from datetime import datetime, date
import calendar, io, csv, os, time, sys, json
from urllib.parse import quote
from . import bp
from ..core import db, util
from ..core.util import *            # noqa: F401,F403
from ..core.util import (_log_err, _last_prices, js_mats_with_price)  # noqa: F401
from .. import importer
from ..table import tbl
from .logic import *            # noqa: F401,F403
from . import logic as purchase  # noqa: F401  兼容 purchase.xxx 写法

# ==================== 采购台账（与仓库库存分离，靠到货单联动） ====================
@bp.route('/purchase')
def purchase_home():
    """采购首页：看板 + 采购单列表"""
    st = request.args.get('st') or ''
    sup = (request.args.get('sup') or '').strip()
    kw = clean_kw(request.args.get('kw'))
    w, a = [], []
    if st:
        w.append("p.status=?"); a.append(st)
    if sup:
        w.append("p.supplier=?"); a.append(sup)
    if kw:
        w.append("(p.pono LIKE ? OR p.supplier LIKE ? OR p.note LIKE ?)")
        a += ['%%%s%%' % kw] * 3
    sql = ("SELECT p.*, COALESCE(SUM(i.qty),0) tq, COALESCE(SUM(i.qty*i.price),0) amt,"
           " COALESCE(SUM(i.recv_qty),0) rq FROM pos p LEFT JOIN po_items i ON i.po_id=p.id")
    if w:
        sql += " WHERE " + " AND ".join(w)
    sql += " GROUP BY p.id ORDER BY p.odate DESC, p.id DESC LIMIT 300"
    rows = []
    for r in db.q(sql, *a):
        d = dict(r)
        d['tax_rate'] = float(d['tax_rate'] or 0)
        _, tax, total = purchase.line_amount(1, d['amt'], d['tax_rate'])
        d['amount'] = round(float(d['amt'] or 0), 2)
        d['tax'] = tax
        d['total'] = round(float(d['amt'] or 0) + tax, 2)
        d['paid'] = purchase.paid_amount(d['id'])
        # 欠款统一按「到货」算，跟详情页 / 汇总页口径一致；
        # 用 SUM(qty*price) 一次算出到货金额，避免逐单再查
        d['recv_amt'] = float(db.q(
            "SELECT COALESCE(SUM(r.qty*r.price),0) s FROM po_receipts r"
            " JOIN po_items i ON i.id=r.item_id WHERE i.po_id=?", d['id'])[0]['s'] or 0)
        _, _, d['recv_total'] = purchase.line_amount(1, d['recv_amt'], d['tax_rate'])
        d['owed'] = round(float(d['recv_total'] or 0) - float(d['paid'] or 0), 2)
        d['open_qty'] = round(float(d['tq'] or 0) - float(d['rq'] or 0), 2)
        today_s = today()
        d['late'] = bool(d['ddate'] and d['ddate'] < today_s
                         and d['status'] in ('已下单', '部分到货'))
        rows.append(d)
    sups = [r['name'] for r in db.q(
        "SELECT DISTINCT supplier name FROM pos ORDER BY supplier")]
    return render_template('purchase.html', rows=rows, st=st, sup=sup, kw=kw,
                           sups=sups, dash=purchase.dashboard(),
                           STATUS=purchase.STATUS)


def _po_sync(po_id):
    """采购单任何变动后重算汇总快照。失败不阻断主流程，只记日志。"""
    try:
        purchase.save_summary(po_id)
    except Exception:
        _log_err('采购汇总保存失败 po_id=%s' % po_id)


@bp.route('/po/summary')
def po_summary():
    """采购单汇总 —— 单独一页，看每单的数量/金额/税额/价税合计/已付/欠款/未交。

    数据来自 po_summary 快照表（每次变动后重算落库），不是现场聚合，
    所以翻历史单看到的就是当时的金额，改了税率也不会让旧单金额跟着变。
    """
    st = request.args.get('st') or ''
    sup = (request.args.get('sup') or '').strip()
    kw = clean_kw(request.args.get('kw'))
    m = safe_ym(request.args.get('m') or '', default='')
    rows, tot = purchase.summary_list(st=st, sup=sup, kw=kw, m=m)
    # 老库（升级上来的）可能还没生成快照，这里补一次
    if not rows and db.q("SELECT COUNT(*) c FROM pos")[0]['c']:
        try:
            purchase.sync_all()
            rows, tot = purchase.summary_list(st=st, sup=sup, kw=kw, m=m)
        except Exception:
            _log_err('汇总补算失败')
    sups = [r['name'] for r in db.q(
        "SELECT DISTINCT supplier name FROM pos ORDER BY supplier")]
    return render_template('po_summary.html', rows=rows, tot=tot, st=st, sup=sup,
                           kw=kw, m=m, sups=sups, STATUS=purchase.STATUS,
                           today=today())


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
                pono = purchase.next_pono(odate)
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
                               purchase.item_sig(nm, _sp, _un, supplier),
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
        purchase.touch_supplier(supplier)
        # 建单时若手工选了"已完成"却还没到货，状态必须拉回事实：
        # 否则看板/汇总会显示"已完成"，实际一件没到，账实不符。
        try:
            purchase.refresh_status(po_id)
        except Exception:
            _log_err('采购单状态推导失败')
        try:
            purchase.save_summary(po_id)
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
    """建单时按 单号-序号 生成批次号（与 purchase.new_item_batch 同一套规则）。

    为什么不在循环里查库生成：建单是在一个事务里批量插的，此时新明细还没落库，
    查 COUNT 拿不到刚插的行，会重号。用循环序号最稳。
    """
    return '%s-%d' % (pono, idx + 1)


# ---------- 采购模板 ----------
# 采购单要填的列跟仓库入库完全不同（客户订单号、交货方式、税率…），
# 所以单独一套模板，跟库存模板互不影响：改库存的列不会牵连采购单。
@bp.route('/po/tpls')
def po_tpls():
    rows = []
    for t in db.po_tpls():
        n, npo = db.po_tpl_stat(t['id'])
        rows.append(dict(id=t['id'], name=t['name'], note=t['note'],
                         items=n, pos_=npo,
                         cols=[c['label'] for c in db.po_tpl_cols(t['id'])]))
    return render_template('po_tpls.html', rows=rows,
                           msg=request.args.get('msg', ''),
                           err=request.args.get('err', ''),
                           units=db.unit_choices())


@bp.route('/po/tpl/add', methods=['POST'])
def po_tpl_add():
    if not take_nonce(request.form.get('_n')):
        return redirect(url_for('po_tpls', err='这个模板已经建过了，请不要重复提交'))
    name = (request.form.get('name') or '').strip()
    if not name:
        return redirect(url_for('po_tpls', err='模板名字不能为空'))
    for t in db.po_tpls():
        if t['name'] == name:
            return redirect(url_for('po_tpls', err='已经有叫「%s」的模板了' % name))
    copy_from = int_arg(request.form, 'copy_from') or None
    tid = db.add_po_tpl(name, (request.form.get('note') or '').strip(),
                        copy_from=copy_from)
    return redirect(url_for('po_tpls',
        msg='已新建「%s」，去「⚙ 配列」决定它有哪些列' % name))


@bp.route('/po/tpl/rename/<int:tid>', methods=['POST'])
def po_tpl_rename(tid):
    if not take_nonce(request.form.get('_n')):
        return redirect(url_for('po_tpls', err='已经改过了，请不要重复提交'))
    name = (request.form.get('name') or '').strip()
    if not name:
        return redirect(url_for('po_tpls', err='模板名字不能为空'))
    db.rename_po_tpl(tid, name, (request.form.get('note') or '').strip())
    return redirect(url_for('po_tpls', msg='已改名为「%s」' % name))


@bp.route('/po/tpl/del/<int:tid>')
def po_tpl_del(tid):
    ok, msg = db.del_po_tpl(tid)
    return redirect(url_for('po_tpls', msg=msg if ok else '', err='' if ok else msg))


@bp.route('/po/tpl/cols/<int:tid>', methods=['GET', 'POST'])
def po_tpl_cols_set(tid):
    """某个采购模板的列配置：改名 / 排序 / 开关 / 单位 / 别名 / 类型"""
    t = db.po_tpl(tid)
    if not t:
        return redirect(url_for('po_tpls', err='模板不存在'))
    if request.method == 'POST':
        if request.form.get('reset'):
            db.reset_po_tpl_cols(tid)
            return redirect(url_for('po_tpl_cols_set', tid=tid, msg='已恢复默认列'))
        rows = []
        for r in db.q("SELECT fid,aliases FROM po_tpl_cols WHERE tpl_id=?", tid):
            fid = r['fid']
            label = (request.form.get('label_%s' % fid) or '').strip() or fid
            aliases = (request.form.get('al_%s' % fid) or '').strip()
            parts = [x.strip() for x in aliases.split(',') if x.strip()]
            # 改名后必须把新名字并进别名：否则导入时认不出使用者自己起的名字，
            # 数据会落到别的字段去（v3.12 出过这个 bug）。
            if label and label not in parts:
                parts.insert(0, label)
            _u = (request.form.get('un_%s' % fid) or '').strip()
            if label and _u:
                # 界面表头显示成「长（米）」，使用者照着做 Excel 时也会这么写，
                # 所以带单位的写法必须也能认
                for w in ('%s（%s）' % (label, _u), '%s(%s)' % (label, _u)):
                    if w not in parts:
                        parts.append(w)
            rows.append(dict(fid=fid, label=label,
                             pos=int(request.form.get('pos_%s' % fid) or 0),
                             enabled=1 if request.form.get('en_%s' % fid) else 0,
                             aliases=','.join(parts),
                             xtype=(request.form.get('xt_%s' % fid) or 'text').strip(),
                             xopt=(request.form.get('xo_%s' % fid) or '').strip(),
                             xform=(request.form.get('xf_%s' % fid) or '').strip(),
                             unit=_u[:20]))
        db.save_po_tpl_cols(tid, rows)
        return redirect(url_for('po_tpl_cols_set', tid=tid, msg='列配置已保存'))
    return render_template('po_tpl_cols.html', t=t,
                           cols=db.q("SELECT * FROM po_tpl_cols WHERE tpl_id=? ORDER BY pos", tid),
                           n_item=db.po_tpl_stat(tid)[0],
                           msg=request.args.get('msg', ''), err=request.args.get('err', ''))


@bp.route('/po/tpl/col/add/<int:tid>', methods=['POST'])
def po_tpl_col_add(tid):
    """给采购模板加自定义列（系统列之外的任意列）"""
    if not take_nonce(request.form.get('_n')):
        return redirect(url_for('po_tpl_cols_set', tid=tid, err='加过了，请不要重复提交'))
    label = (request.form.get('label') or '').strip()
    if not label:
        return redirect(url_for('po_tpl_cols_set', tid=tid, err='列名不能为空'))
    xtype = (request.form.get('xtype') or 'text').strip()
    if xtype not in ('text', 'number', 'date', 'select', 'calc'):
        xtype = 'text'
    db.po_add_custom_col(tid, label, xtype,
                         (request.form.get('xopt') or '').strip(),
                         (request.form.get('xform') or '').strip())
    return redirect(url_for('po_tpl_cols_set', tid=tid, msg='已加列「%s」' % label))


@bp.route('/po/tpl/col/del/<int:tid>/<fid>')
def po_tpl_col_del(tid, fid):
    if db.po_del_custom_col(tid, fid):
        return redirect(url_for('po_tpl_cols_set', tid=tid, msg='列已删除（已存的值一并清掉）'))
    return redirect(url_for('po_tpl_cols_set', tid=tid, err='系统列不能删，只能关掉'))


@bp.route('/po/tpl/from_head', methods=['POST'])
def po_tpl_from_head():
    """拿一张真实采购表的表头建模板：每个列名成为模板的一列"""
    if not take_nonce(request.form.get('_n')):
        return redirect(url_for('po_tpls', err='建过了，请不要重复提交'))
    heads = [x.strip() for x in (request.form.get('heads') or '').replace('\n', ',').split(',')]
    heads = [h for h in heads if h]
    if not heads:
        return redirect(url_for('po_tpls', err='没读到表头'))
    name = (request.form.get('name') or '').strip() or '新采购表'
    tid = db.po_tpl_from_headers(heads, name)
    return redirect(url_for('po_tpl_cols_set', tid=tid,
        msg='已按表头建好「%s」，共 %d 列' % (name, len(heads))))


@bp.route('/po/tpl/use/<int:tid>')
def po_tpl_use(tid):
    """从模板直接开新采购单"""
    t = db.po_tpl(tid)
    if not t:
        return redirect(url_for('po_tpls', err='模板不存在'))
    return redirect(url_for('po_new', ptpl=tid))


def _mat_choices():
    return [dict(id=m['id'], name=m['name'], spec=m['spec'] or '',
                 unit=m['unit'] or '', code=m['code'] or '')
            for m in db.q("SELECT id,name,spec,unit,code FROM materials"
                          " WHERE active=1 ORDER BY name")]


def _sup_names():
    return [r['name'] for r in db.q("SELECT name FROM suppliers WHERE active=1 ORDER BY name")]


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
        a, t, tt = purchase.line_amount(d['qty'], d['price'], po['tax_rate'])
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
        d['xcols'] = []
        _pt = int(d.get('ptpl_id') or 0)
        if _pt:
            for _c in db.po_tpl_custom_cols(_pt):
                _v = d['xv'].get(_c['fid'], '')
                if _v not in ('', None):
                    d['xcols'].append((_c['label'], _v))
        items.append(d)
    recs = db.q("SELECT r.*, i.name, i.unit FROM po_receipts r JOIN po_items i"
                " ON i.id=r.item_id WHERE i.po_id=? ORDER BY r.rdate DESC, r.id DESC", po_id)
    pays = db.q("SELECT * FROM po_payments WHERE po_id=? ORDER BY pdate DESC, id DESC", po_id)
    t = purchase.po_totals(po_id)
    owed_amt, recv_total = purchase.owed(po_id)
    # 这张单用到的采购模板（明细可能来自不同模板，取第一条的做显示）
    # 模板优先取单头记的（建单时选的那套），没有再退到明细上带的
    # sqlite3.Row 没有 .get()，取值前先确认这列存在（老库可能还没迁移）
    _ptid = 0
    try:
        _ptid = int(po['potpl_id'] or 0)
    except (IndexError, KeyError, TypeError, ValueError):
        _ptid = 0
    _ptpl = db.po_tpl(_ptid) if _ptid else None
    if not _ptpl:
        _pused = {int(i.get('ptpl_id') or 0) for i in items if i.get('ptpl_id')}
        if _pused:
            _ptpl = db.po_tpl(sorted(_pused)[0])
    return render_template('po.html', po=po, items=items, recs=recs, pays=pays,
                           t=t, paid=purchase.paid_amount(po_id),
                           owed=owed_amt, recv_total=recv_total,
                           STATUS=purchase.STATUS, today=today(), mats=_mat_choices(),
                           units=db.unit_choices(), ptpl=_ptpl,
                           xcols=(db.po_tpl_custom_cols(_ptpl['id'])
                                  if _ptpl else []),
                           msg=request.args.get('msg', ''), err=request.args.get('err', ''))


@bp.route('/po/<int:po_id>/status', methods=['POST'])
def po_status(po_id):
    if not take_nonce(request.form.get('_n')):
        return redirect(url_for('po_detail', po_id=po_id,
                                err='这一下点重了，状态没改，请刷新后重试'))
    st = (request.form.get('status') or '').strip()
    if st in purchase.STATUS:
        db.run("UPDATE pos SET status=? WHERE id=?", st, po_id)
        # 手工选了「已下单/部分到货/已完成」这三种业务状态时，必须按到货
        # 事实再核定一次：没到齐的货不能标成已完成，否则采购台和汇总页
        # 全显示已完成、实物却没到，这种失真的数字比报错更难查。
        # 「草稿/已取消」是人工终态，derive_status 会原样保留，不受影响。
        if st not in ('草稿', '已取消'):
            purchase.refresh_status(po_id)
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
           or purchase.new_item_batch(po_id))
    _ptpl = int(request.form.get('ptpl') or 0) or None
    _xv = _po_item_extra(_ptpl, request.form, 0, qty=q, price=p, conv=cv)
    db.run("INSERT INTO po_items(po_id,material_id,name,spec,unit,conv,stock_unit,"
           "qty,price,note,sig,batch,ptpl_id,extra) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
           po_id, mid, nm, _sp, _un, cv,
           (request.form.get('stock_unit') or '').strip(), q, p,
           (request.form.get('note') or '').strip(),
           purchase.item_sig(nm, _sp, _un, _sup), _bt, _ptpl, _xv)
    purchase.refresh_status(po_id)
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
    ok, msg = purchase.set_item_unit(
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
        ok, msg = purchase.delete_item(iid)
        return redirect(url_for('po_detail', po_id=pid,
                                msg=msg if ok else '', err='' if ok else msg))
    return redirect(url_for('purchase_home'))


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
    ok, msg = purchase.receive(int(iid), rdate, q, p,
                               (request.form.get('note') or '').strip())
    return redirect(url_for('po_detail', po_id=po_id,
                            msg=msg if ok else '', err='' if ok else msg))


@bp.route('/po/receive/del/<int:rid>')
def po_receive_del(rid):
    r = db.q("SELECT i.po_id FROM po_receipts r JOIN po_items i ON i.id=r.item_id"
             " WHERE r.id=?", rid)
    if r:
        pid = r[0]['po_id']
        ok, msg = purchase.unreceive(rid)
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


@bp.route('/suppliers')
def suppliers():
    return render_template('suppliers.html', rows=purchase.supplier_list(),
                           msg=request.args.get('msg', ''))


@bp.route('/suppliers/add', methods=['POST'])
def supplier_add():
    nm = (request.form.get('name') or '').strip()
    if not nm:
        return redirect(url_for('suppliers', msg='供应商名称必填'))
    purchase.touch_supplier(nm)
    db.run("UPDATE suppliers SET contact=?, phone=?, note=? WHERE name=?",
           (request.form.get('contact') or '').strip(),
           (request.form.get('phone') or '').strip(),
           (request.form.get('note') or '').strip(), nm)
    return redirect(url_for('suppliers', msg='已保存 %s' % nm))


# ---------- 采购台账导出 ----------
@bp.route('/export/po.xlsx')
def export_po_xlsx():
    """采购台账导出。

    三种表：orders=采购单汇总 / items=明细（含未到货量）/ recv=到货流水 / pay=付款流水
    之前只有库存/流水/月报能导出，采购数据导不出来，
    月底对账、发给供应商核对都得手工抄，这里补齐。
    """
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    kind = request.args.get('t', 'items')
    st = request.args.get('st') or ''
    sup = (request.args.get('sup') or '').strip()
    kw = clean_kw(request.args.get('kw'))
    m = request.args.get('m') or ''

    w, a = [], []
    if st:
        w.append("p.status=?"); a.append(st)
    if sup:
        w.append("p.supplier=?"); a.append(sup)
    if kw:
        w.append("(p.pono LIKE ? OR p.supplier LIKE ? OR p.note LIKE ?)")
        a += ['%%%s%%' % kw] * 3
    if m:
        w.append("p.odate LIKE ?"); a.append(m + '%')
    where = (" WHERE " + " AND ".join(w)) if w else ""

    wb = openpyxl.Workbook(); ws = wb.active
    hf = Font(bold=True, color='FFFFFF')
    fill = PatternFill('solid', start_color='1F6FEB')
    # 公式注入防护：= + - @ 开头的文本会被 Excel 当公式执行
    def cv(v):
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return v
        s = str(v)
        return ("'" + s) if s[:1] in ('=', '+', '-', '@') else s

    if kind == 'summary':
        ws.title = '采购单汇总'
        ws.append(['采购单号', '日期', '交期', '供应商', '状态', '税率%', '单价口径',
                   '订购数量', '不含税金额', '税额', '价税合计',
                   '已到货数量', '已到货金额', '已付', '欠款', '未交数量', '更新时间'])
        srows, _ = purchase.summary_list(st=st, sup=sup, kw=kw, m=m)
        for r in srows:
            ws.append([cv(r['pono']), cv(r['odate']), cv(r['ddate']), cv(r['supplier']),
                       cv(r['status']), float(r['tax_rate'] or 0),
                       '含税' if r['price_tax'] else '不含税',
                       float(r['qty']), float(r['amount']), float(r['tax']),
                       float(r['total']), float(r['recv_qty']), float(r['recv_total']),
                       float(r['paid']), float(r['owed']), float(r['open_qty']),
                       cv(r['updated_at'])])
        fn = '采购单汇总'
    elif kind == 'items':
        ws.title = '采购明细'
        # 自定义列：这批明细可能来自不同模板，先把各模板的自定义列并起来。
        # 同名列（不同模板都叫"客户订单号"）合并成一列，避免导出一堆重复表头。
        import json as _xj
        _xmap = {}          # fid -> 表头名
        _xorder = []        # 表头顺序
        for _r in db.q("SELECT DISTINCT i.ptpl_id t FROM po_items i"
                       " JOIN pos p ON p.id=i.po_id" + where +
                       " AND i.ptpl_id IS NOT NULL"):
            for _c in db.po_tpl_custom_cols(int(_r['t'])):
                if _c['fid'] not in _xmap:
                    _xmap[_c['fid']] = _c['label']
                    _xorder.append(_c['fid'])
        ws.append(['采购单号', '日期', '交期', '供应商', '物料名称', '规格', '单位',
                   '订购数', '单价', '金额', '已到货', '未到货', '状态', '批次', '备注']
                  + [_xmap[f] for f in _xorder])
        sql = ("SELECT p.pono,p.odate,p.ddate,p.supplier,i.name,i.spec,i.unit,"
               " i.qty,i.price,i.note,p.status,i.batch,i.extra,"
               " COALESCE(SUM(r.qty),0) rq FROM po_items i"
               " JOIN pos p ON p.id=i.po_id"
               " LEFT JOIN po_receipts r ON r.item_id=i.id"
               + where + " GROUP BY i.id ORDER BY p.odate DESC, i.id")
        for r in db.q(sql, *a):
            q = float(r['qty'] or 0); rq = float(r['rq'] or 0)
            try:
                _xv = _xj.loads(r['extra'] or '{}') or {}
            except Exception:
                _xv = {}
            ws.append([cv(r['pono']), cv(r['odate']), cv(r['ddate']), cv(r['supplier']),
                       cv(r['name']), cv(r['spec']), cv(r['unit']),
                       q, float(r['price'] or 0), round(q * float(r['price'] or 0), 2),
                       rq, round(q - rq, 2), cv(r['status']), cv(r['batch']), cv(r['note'])]
                      + [cv(_xv.get(f, '')) for f in _xorder])
        fn = '采购明细'
    elif kind == 'recv':
        ws.title = '到货流水'
        ws.append(['到货日期', '采购单号', '供应商', '物料名称', '规格', '单位',
                   '到货数', '单价', '金额', '备注'])
        sql = ("SELECT r.rdate,p.pono,p.supplier,i.name,i.spec,i.unit,"
               " r.qty,r.price,r.note FROM po_receipts r"
               " JOIN po_items i ON i.id=r.item_id JOIN pos p ON p.id=i.po_id"
               + where + " ORDER BY r.rdate DESC, r.id DESC")
        for r in db.q(sql, *a):
            q = float(r['qty'] or 0); pr = float(r['price'] or 0)
            ws.append([cv(r['rdate']), cv(r['pono']), cv(r['supplier']), cv(r['name']),
                       cv(r['spec']), cv(r['unit']), q, pr, round(q * pr, 2), cv(r['note'])])
        fn = '到货流水'
    elif kind == 'pay':
        ws.title = '付款流水'
        ws.append(['付款日期', '采购单号', '供应商', '金额', '方式', '备注'])
        sql = ("SELECT y.pdate,p.pono,p.supplier,y.amount,y.method,y.note"
               " FROM po_payments y JOIN pos p ON p.id=y.po_id"
               + where + " ORDER BY y.pdate DESC, y.id DESC")
        for r in db.q(sql, *a):
            ws.append([cv(r['pdate']), cv(r['pono']), cv(r['supplier']),
                       float(r['amount'] or 0), cv(r['method']), cv(r['note'])])
        fn = '付款流水'
    else:
        ws.title = '采购单'
        ws.append(['采购单号', '日期', '交期', '供应商', '状态', '税率%',
                   '订购金额', '税额', '价税合计', '已付', '欠款', '备注'])
        sql = ("SELECT p.*, COALESCE(SUM(i.qty*i.price),0) amt FROM pos p"
               " LEFT JOIN po_items i ON i.po_id=p.id"
               + where + " GROUP BY p.id ORDER BY p.odate DESC, p.id DESC")
        for r in db.q(sql, *a):
            tr = float(r['tax_rate'] or 0)
            amt = round(float(r['amt'] or 0), 2)
            _, tax, total = purchase.line_amount(1, amt, tr)
            paid = purchase.paid_amount(r['id'])
            _, recv_total = purchase.owed(r['id'])
            ws.append([cv(r['pono']), cv(r['odate']), cv(r['ddate']), cv(r['supplier']),
                       cv(r['status']), tr, amt, round(tax, 2), round(total, 2),
                       paid, round(recv_total - paid, 2), cv(r['note'])])
        fn = '采购单汇总'

    for cc in ws[1]:
        cc.font = hf; cc.fill = fill; cc.alignment = Alignment(horizontal='center')
    ws.freeze_panes = 'A2'
    for i, wd in enumerate([16, 12, 12, 14, 20, 12, 8, 10, 12, 10, 10, 10, 10, 18], 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = wd
    bio = io.BytesIO(); wb.save(bio)
    from urllib.parse import quote
    if m:
        fn += m
    return Response(bio.getvalue(),
                    mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition':
                             "attachment; filename=export.xlsx; filename*=UTF-8''%s.xlsx" % quote(fn)})


