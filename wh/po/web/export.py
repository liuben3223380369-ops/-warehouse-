# -*- coding: utf-8 -*-
"""采购台账导出 —— orders / items / summary / recv / pay 五种表

依赖：summary
"""
import io

from flask import request, Response

from .. import bp
from ...core import db
from ...core.util import clean_kw
from .. import amount, head, summary


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
        srows, _ = summary.summary_list(st=st, sup=sup, kw=kw, m=m)
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
        # 「金额」= 数量×单价，单价可能是含税也可能是不含税（各单可不同），
        # 不标口径的话用户会拿它跟汇总表的「不含税金额」直接对，发现对不上还以为算错了。
        ws.append(['采购单号', '日期', '交期', '供应商', '物料名称', '规格', '单位',
                   '订购数', '单价', '金额', '单价口径', '已到货', '未到货', '状态',
                   '批次', '备注']
                  + [_xmap[f] for f in _xorder])
        sql = ("SELECT p.pono,p.odate,p.ddate,p.supplier,i.name,i.spec,i.unit,"
               " i.qty,i.price,i.note,p.status,i.batch,i.extra,p.price_tax,"
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
            # 口径缺省按含税处理，与 po_head() 的默认保持一致
            _ptax = r['price_tax']
            _ptax = 1 if _ptax is None else int(_ptax)
            ws.append([cv(r['pono']), cv(r['odate']), cv(r['ddate']), cv(r['supplier']),
                       cv(r['name']), cv(r['spec']), cv(r['unit']),
                       q, float(r['price'] or 0), round(q * float(r['price'] or 0), 2),
                       '含税' if _ptax else '不含税',
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
        raw = list(db.q(sql, *a))
        # v3.65 批量预取：导出也要走批量，否则 300 张单 = 600+ 次查询必然超时(502)。
        # 到货金额走 po_totals_many（精确口径），付款一次 GROUP BY 取完。
        _ids = [r['id'] for r in raw]
        _tots = head.po_totals_many(_ids)
        _paid = {}
        if _ids:
            ph = ','.join('?' * len(_ids))
            for _r in db.q("SELECT po_id, COALESCE(SUM(amount),0) s FROM po_payments"
                           " WHERE po_id IN (%s) GROUP BY po_id" % ph, *_ids):
                _paid[_r['po_id']] = round(float(_r['s'] or 0), 2)
        for r in raw:
            tr = float(r['tax_rate'] or 0)
            _t = _tots.get(r['id'])
            if _t is None:
                amt = round(float(r['amt'] or 0), 2)
                _, tax, total = amount.line_amount(1, amt, tr)
                paid = head.paid_amount(r['id'])
                _, recv_total = head.owed(r['id'])
            else:
                # 与 ?t=summary 同口径：订购金额走 po_totals（逐行折不含税再汇总），
                # 否则两处导出的「价税合计」会差 0.01，对账时对不上。
                amt = float(_t['amount'] or 0)
                tax = float(_t['tax'] or 0)
                total = float(_t['total'] or 0)
                paid = _paid.get(r['id'], 0.0)
                _, _, recv_total = amount.line_amount(
                    1, float(_t['recv_amount'] or 0), _t['tax_rate'], 0)
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
