# -*- coding: utf-8 -*-
"""采购单 · home

    采购首页看板、汇总台账
"""
from flask import render_template, request
from .. import bp
from .common import _po_sync
from ...core import db
from ...core.util import (_log_err, clean_kw, safe_ym, today)
from .. import amount, head, query, status, summary

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
    raw = list(db.q(sql, *a))
    # v3.65 批量预取：以前在循环里逐单查付款与到货金额（N+1），
    # 300 张单就是 600 次查询、5 万单据下首页 30 秒。改成 2 次批量查完。
    ids = [r['id'] for r in raw]
    _paid = {}
    # 到货金额必须走 po_totals_many（精确口径：实价折不含税）。
    # 不能简化成 SUM(qty*price)——那会把含税价当不含税用，
    # 实测列表页比详情页多出约 13%（正好一个税率），欠款也跟着错。
    _tots = head.po_totals_many(ids)
    if ids:
        ph = ','.join('?' * len(ids))
        for r in db.q("SELECT po_id, COALESCE(SUM(amount),0) s FROM po_payments"
                      " WHERE po_id IN (%s) GROUP BY po_id" % ph, *ids):
            _paid[r['po_id']] = round(float(r['s'] or 0), 2)
    rows = []
    for r in raw:
        d = dict(r)
        d['tax_rate'] = float(d['tax_rate'] or 0)
        _, tax, total = amount.line_amount(1, d['amt'], d['tax_rate'])
        d['amount'] = round(float(d['amt'] or 0), 2)
        d['tax'] = tax
        d['total'] = round(float(d['amt'] or 0) + tax, 2)
        d['paid'] = _paid.get(d['id'], 0.0)
        # 欠款统一按「到货」算，跟详情页 / 汇总页口径一致；
        # 取 po_totals_many 的精确值（实价折不含税），与详情页完全同源
        d['recv_amt'] = float((_tots.get(d['id']) or {}).get('recv_amount', 0) or 0)
        _, _, d['recv_total'] = amount.line_amount(1, d['recv_amt'], d['tax_rate'])
        d['owed'] = round(float(d['recv_total'] or 0) - float(d['paid'] or 0), 2)
        d['open_qty'] = round(float(d['tq'] or 0) - float(d['rq'] or 0), 2)
        today_s = today()
        d['late'] = bool(d['ddate'] and d['ddate'] < today_s
                         and d['status'] in ('已下单', '部分到货'))
        rows.append(d)
    sups = [r['name'] for r in db.q(
        "SELECT DISTINCT supplier name FROM pos ORDER BY supplier")]
    return render_template('purchase.html', rows=rows, st=st, sup=sup, kw=kw,
                           sups=sups, dash=query.dashboard(),
                           STATUS=status.STATUS)

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
    rows, tot = summary.summary_list(st=st, sup=sup, kw=kw, m=m)
    # 老库（升级上来的）可能还没生成快照，这里补一次。
    # 只有"快照表整张是空的"才算老库；不能只看 rows 为空——
    # 那样每次筛选无结果都会触发全量重算，页面直接卡死。
    if (not rows
            and not db.q("SELECT 1 FROM po_summary LIMIT 1")
            and db.q("SELECT 1 FROM pos LIMIT 1")):
        try:
            summary.sync_all()
            rows, tot = summary.summary_list(st=st, sup=sup, kw=kw, m=m)
        except Exception:
            _log_err('汇总补算失败')
    sups = [r['name'] for r in db.q(
        "SELECT DISTINCT supplier name FROM pos ORDER BY supplier")]
    return render_template('po_summary.html', rows=rows, tot=tot, st=st, sup=sup,
                           kw=kw, m=m, sups=sups, STATUS=status.STATUS,
                           today=today())
