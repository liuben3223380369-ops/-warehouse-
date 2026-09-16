# -*- coding: utf-8 -*-
"""统计模块"""

from ..core.router import Router
bp = Router('stat')

# -*- coding: utf-8 -*-
"""统计模块：首页洞察、月报表、数据聚合"""
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
from ..table.helpers import all_custom_cols, cell_val

@bp.route('/')
def index():
    t, m = today(), ym()
    day_in  = db.q("SELECT COALESCE(SUM(qty),0) s FROM txns WHERE tdate=? AND kind='进'", t)[0]['s']
    day_out = db.q("SELECT COALESCE(SUM(qty),0) s FROM txns WHERE tdate=? AND kind='出'", t)[0]['s']
    mon_in  = db.q("SELECT COALESCE(SUM(qty),0) s FROM txns WHERE tdate LIKE ? AND kind='进'", m+'%')[0]['s']
    mon_out = db.q("SELECT COALESCE(SUM(qty),0) s FROM txns WHERE tdate LIKE ? AND kind='出'", m+'%')[0]['s']
    n_mat   = db.q("SELECT COUNT(*) c FROM materials WHERE active=1")[0]['c']
    alerts  = db.q("SELECT * FROM v_stock WHERE stock<=safety ORDER BY stock")
    recent  = db.q("SELECT t.*, m.name, m.unit, m.code, %s AS amount FROM txns t"
                   " JOIN materials m ON m.id=t.material_id"
                   " ORDER BY t.id DESC LIMIT 8" % AMT.replace('t.', 't.'))
    # 本月进出金额（只统计填了单价的单据）
    _amt = "SELECT COALESCE(SUM(qty*COALESCE(price,0)),0) s FROM txns WHERE tdate LIKE ? AND kind=?"
    mon_amt_in  = db.q(_amt, m + '%', '进')[0]['s']
    mon_amt_out = db.q(_amt, m + '%', '出')[0]['s']
    return render_template('index.html', day_in=day_in, day_out=day_out, mon_in=mon_in,
                           mon_out=mon_out, n_mat=n_mat, alerts=alerts, recent=recent,
                           mon_amt_in=mon_amt_in, mon_amt_out=mon_amt_out)


# ---------- 月报表（复刻原模板布局） ----------
def _report_insights(m, top=5):
    """月报洞察：采购最多 / 使用最多 / 采购金额最多 / 每种物料环比趋势。

    采购数据取自 po_receipts（到货登记）——只有真正收到货才算采购，
    下单不算，跟"到货才入库"的口径一致。
    数量用**采购单位**（po_items.unit），金额用到货实价（po_receipts.price），
    因为供应商调价是常态，用实价才对得上实际付的钱。
    """
    m = safe_ym(m)
    pm = prev_ym(m)
    lo, hi = m + '-01', m + '-31'
    plo, phi = pm + '-01', pm + '-31'

    # 采购明细可能没建档（material_id 为空），这时按名称反查物料档案，
    # 让"采购的铜箔"和"出入库的铜箔"归到同一行，否则趋势表会拆成两行。
    _by_name = {}
    for r in db.q("SELECT id, name FROM materials"):
        _by_name[(r['name'] or '').strip()] = r['id']

    def _mid(mid, name):
        if mid:
            return int(mid)
        return _by_name.get((name or '').strip()) or ('n:' + (name or '').strip())

    def _span(a, b):
        """这个月的到货按物料汇总：数量、金额、单位"""
        out = {}
        for r in db.q("SELECT pi.material_id mid, pi.name, pi.unit,"
                      " SUM(pr.qty) q, SUM(pr.qty*pr.price) amt"
                      " FROM po_receipts pr JOIN po_items pi ON pi.id=pr.item_id"
                      " WHERE pr.rdate BETWEEN ? AND ? GROUP BY pi.material_id, pi.name, pi.unit",
                      a, b):
            k = _mid(r['mid'], r['name'])
            d = out.setdefault(k, dict(mid=r['mid'], name=(r['name'] or '').strip(),
                                       unit=r['unit'] or '', qty=0.0, amt=0.0))
            d['qty'] += float(r['q'] or 0)
            d['amt'] += float(r['amt'] or 0)
        return out

    cur_p = _span(lo, hi)
    pre_p = _span(plo, phi)

    # 出库（使用）
    def _use(a, b):
        out = {}
        for r in db.q("SELECT material_id mid, SUM(qty) q FROM txns"
                      " WHERE kind='出' AND tdate BETWEEN ? AND ? GROUP BY material_id", a, b):
            out[r['mid']] = float(r['q'] or 0)
        return out

    cur_u, pre_u = _use(lo, hi), _use(plo, phi)

    # 入库（便于看趋势）
    def _in(a, b):
        out = {}
        for r in db.q("SELECT material_id mid, SUM(qty) q FROM txns"
                      " WHERE kind='进' AND tdate BETWEEN ? AND ? GROUP BY material_id", a, b):
            out[r['mid']] = float(r['q'] or 0)
        return out

    cur_i, pre_i = _in(lo, hi), _in(plo, phi)

    names = {}
    for r in db.q("SELECT id, name, unit FROM materials"):
        names[r['id']] = (r['name'], r['unit'] or '')

    def _nm(k):
        if isinstance(k, int) and k in names:
            return names[k]
        d = cur_p.get(k) or pre_p.get(k)
        if d:
            return (d['name'], d['unit'])
        if isinstance(k, str) and k.startswith('n:'):
            return (k[2:] or '（未建档）', '')
        return ('（未建档）', '')

    def _rank(cur, key):
        rows = []
        for k, d in cur.items():
            v = d[key] if isinstance(d, dict) else d
            if v <= 0:
                continue
            n, u = _nm(k)
            prev = 0.0
            if key == 'amt' or key == 'qty':
                pd = pre_p.get(k)
                prev = (pd[key] if pd else 0.0)
            else:
                prev = (pre_u.get(k) or 0.0)
            rows.append(dict(mid=k if isinstance(k, int) else None, name=n, unit=u,
                             val=round(v, 2), prev=round(prev, 2),
                             delta=round(v - prev, 2),
                             pct=(round((v - prev) / prev * 100, 1) if prev else None)))
        rows.sort(key=lambda x: -x['val'])
        return rows[:top]

    top_pur = _rank(cur_p, 'qty')
    top_amt = _rank(cur_p, 'amt')
    # 使用排行（cur_u 是 {mid: qty}）
    use_rows = []
    for k, v in cur_u.items():
        if v <= 0:
            continue
        n, u = _nm(k)
        prev = pre_u.get(k) or 0.0
        use_rows.append(dict(mid=k, name=n, unit=u, val=round(v, 2),
                             prev=round(prev, 2), delta=round(v - prev, 2),
                             pct=(round((v - prev) / prev * 100, 1) if prev else None)))
    use_rows.sort(key=lambda x: -x['val'])
    top_use = use_rows[:top]

    # 每种物料的环比趋势：进 / 出 / 采购金额
    trend = []
    keys = set(cur_i) | set(pre_i) | set(cur_u) | set(pre_u) | set(cur_p) | set(pre_p)
    for k in keys:
        ci, pi_ = cur_i.get(k, 0.0), pre_i.get(k, 0.0)
        cu, pu = cur_u.get(k, 0.0), pre_u.get(k, 0.0)
        cp = cur_p.get(k)
        pp = pre_p.get(k)
        ca = cp['amt'] if cp else 0.0
        pa = pp['amt'] if pp else 0.0
        if not (ci or pi_ or cu or pu or ca or pa):
            continue
        n, u = _nm(k)
        trend.append(dict(mid=k if isinstance(k, int) else None, name=n, unit=u,
                          i_cur=round(ci, 2), i_pre=round(pi_, 2),
                          o_cur=round(cu, 2), o_pre=round(pu, 2),
                          a_cur=round(ca, 2), a_pre=round(pa, 2)))
    for t in trend:
        t['i_up'] = t['i_cur'] > t['i_pre'] + 1e-9
        t['i_dn'] = t['i_cur'] < t['i_pre'] - 1e-9
        t['o_up'] = t['o_cur'] > t['o_pre'] + 1e-9
        t['o_dn'] = t['o_cur'] < t['o_pre'] - 1e-9
        t['a_up'] = t['a_cur'] > t['a_pre'] + 1e-9
        t['a_dn'] = t['a_cur'] < t['a_pre'] - 1e-9
    # 按"本月动静"排序：出库多的排前面，其次入库
    trend.sort(key=lambda x: (-(x['o_cur'] + x['i_cur']), x['name']))

    tot = dict(
        pur_qty=round(sum(d['qty'] for d in cur_p.values()), 2),
        pur_amt=round(sum(d['amt'] for d in cur_p.values()), 2),
        pur_amt_pre=round(sum(d['amt'] for d in pre_p.values()), 2),
        use_qty=round(sum(cur_u.values()), 2),
        use_pre=round(sum(pre_u.values()), 2),
    )
    return dict(m=m, pm=pm, top_pur=top_pur, top_use=top_use, top_amt=top_amt,
                trend=trend, tot=tot)


@bp.route('/report')
def report():
    m = safe_ym(request.args.get('m'))
    mats, days = _report_data(m)
    tot_in = sum(mt['min'] for mt in mats)
    tot_out = sum(mt['mout'] for mt in mats)
    try:
        ins = _report_insights(m)
    except Exception:
        _log_err('月报洞察统计失败')
        ins = None
    # 按模板分组：每个模板单独统计，所有模板都保留（不合并、不丢弃）
    groups = []
    tid_of = {}
    for r in db.q("SELECT id, COALESCE(tpl_id,0) t FROM materials"):
        tid_of[r['id']] = r['t']
    names = {t['id']: t['name'] for t in db.tpls()}
    names[0] = '未归类'
    bucket = {}
    for mt in mats:
        t = tid_of.get(mt['id'], 0)
        bucket.setdefault(t, []).append(mt)
    # 遍历全部模板（不是只遍历有物料的）：空模板也要显示，
    # 否则使用者会以为某个模板丢了
    for t in [x['id'] for x in db.tpls()] + ([0] if 0 in bucket else []):
        g = bucket.get(t, [])
        gin = sum(x['min'] for x in g)
        gout = sum(x['mout'] for x in g)
        groups.append(dict(tid=t, name=names.get(t, '模板%d' % t), mats=g,
                           n=len(g), tin=gin, tout=gout,
                           tend=sum(x['ending'] for x in g)))
    # 总汇分析：所有表（模板）放一起看——哪家占比最大、进出最活跃
    total_all = tot_in + tot_out
    summary = []
    for g in groups:
        share = (g['tin'] + g['tout']) / total_all * 100 if total_all else 0
        summary.append(dict(name=g['name'], n=g['n'], tin=g['tin'], tout=g['tout'],
                            tend=g['tend'], share=round(share, 1),
                            act=round(g['tin'] + g['tout'], 2)))
    summary.sort(key=lambda x: -x['act'])
    top = summary[0] if summary else None
    return render_template('report.html', m=m, days=days, mats=mats,
                           tot_in=tot_in, tot_out=tot_out, ins=ins,
                           groups=groups, TPLS=db.tpls(),
                           summary=summary, top=top, total_all=round(total_all, 2))



def _stock_rows(kw, f, sort, dir_):
    w, args = [], []
    if f == 'alert':
        w.append("stock<=safety")
    elif f == 'zero':
        w.append("stock<=0")
    elif f == 'dead':
        w.append("status='呆滞'")
    if kw:
        w.append("(name LIKE ? OR code LIKE ? OR supplier LIKE ? OR category LIKE ? OR spec LIKE ?)")
        args += ['%%%s%%' % kw] * 5
    sql = "SELECT * FROM v_stock" + (" WHERE " + " AND ".join(w) if w else "")
    allowed = {'name': 'name', 'stock': 'stock', 'opening': 'opening',
               'in_qty': 'in_qty', 'out_qty': 'out_qty', 'category': 'category', 'code': 'code'}
    if sort in allowed and dir_:
        sql += " ORDER BY %s %s, name" % (allowed[sort], 'ASC' if dir_ == 'asc' else 'DESC')
    else:
        sql += " ORDER BY category, name"
    return db.q(sql, *args)

def _txn_rows(kw, d, kind, sort, dir_):
    sql = ("SELECT t.*, m.name, m.unit, m.code, m.category, m.spec, m.supplier,"
           " m.id AS mid, %s AS amount FROM txns t"
           " JOIN materials m ON m.id=t.material_id" % AMT)
    w, args = [], []
    if d:
        w.append("t.tdate=?"); args.append(d)
    if kind:
        w.append("t.kind=?"); args.append(kind)
    if kw:
        w.append("(m.name LIKE ? OR m.code LIKE ? OR t.note LIKE ? OR m.category LIKE ?)")
        args += ['%%%s%%' % kw] * 4
    if w:
        sql += " WHERE " + " AND ".join(w)
    sql += " ORDER BY t.tdate DESC, t.id DESC LIMIT 2000"
    return db.q(sql, *args)

def _report_data(m):
    """月报数据。（调用前应用 safe_ym 清洗月份）
    月初结存 = 物料期初 + 该月之前所有单据的净额（不是固定的 materials.opening），
    这样才能保证：上月月末 == 本月月初，且当月月末 == 实时库存。
    """
    m = safe_ym(m)
    y, mo = int(m[:4]), int(m[5:7])
    days = calendar.monthrange(y, mo)[1]
    first, last = f'{m}-01', f'{m}-{days:02d}'
    # 用 v_mats（含停用物料）而不是 v_stock（只含启用）：
    # "停用"只是让它不再出现在录单和实时库存里，历史月份既然有单据，
    # 月报就必须照实反映，否则停用某物料后查旧月报会凭空少掉一批数据。
    mats = [dict(r) for r in db.q("SELECT * FROM v_mats ORDER BY category, name")]

    # 该月之前的累计净额（进 - 出），按物料汇总
    before = {}
    for r in db.q("SELECT material_id, kind, SUM(qty) q FROM txns WHERE tdate < ?"
                  " GROUP BY material_id, kind", first):
        before[r['material_id']] = before.get(r['material_id'], 0.0) +             (r['q'] if r['kind'] == '进' else -r['q'])

    raw = db.q("SELECT tdate,material_id,kind,SUM(qty) q FROM txns WHERE tdate BETWEEN ? AND ?"
               " GROUP BY tdate,material_id,kind", first, last)
    cell = {}
    for r in raw:
        cell[(r['material_id'], int(r['tdate'][8:10]), r['kind'])] = r['q']
    for mt in mats:
        mi = mo_ = 0.0
        mt['cells'] = []
        for dd in range(1, days + 1):
            a = cell.get((mt['id'], dd, '进'), 0); b = cell.get((mt['id'], dd, '出'), 0)
            mt['cells'].append((a, b)); mi += a; mo_ += b
        mt['min'], mt['mout'] = mi, mo_
        # 月初 = 档案期初 + 历史累计；月末 = 月初 + 本月进 - 本月出
        mt['opening'] = round(float(mt['opening'] or 0) + before.get(mt['id'], 0.0), 2)
        mt['ending'] = round(mt['opening'] + mi - mo_, 2)
    return mats, days

def csv_safe(v):
    """防 CSV 公式注入。

    单元格以 = + - @ 或制表符开头时，Excel / WPS 打开会当成公式执行
    （=HYPERLINK("http://evil.com","点我")、=cmd|'/c calc'!A1 都能触发）。
    这里给文本前面加一个单引号，Excel 当纯文本显示，肉眼看不出差别。
    数字原样返回，避免破坏数值。
    """
    if v is None:
        return ''
    if isinstance(v, (int, float)):
        return v
    t = str(v)
    if t[:1] in ('=', '+', '-', '@', '\t', '\r'):
        return "'" + t
    return t


# ---------- 导出（制表能力由表格模块提供） ----------
@bp.route('/export.xlsx')
def export_xlsx():
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    kind = request.args.get('t', 'stock')
    kw = clean_kw(request.args.get('kw'))
    f = request.args.get('f') or ''
    d = request.args.get('d') or ''
    m = safe_ym(request.args.get('m'))
    sort = request.args.get('sort') or ''
    dir_ = request.args.get('dir') or ''
    wb = openpyxl.Workbook(); ws = wb.active
    head_font = Font(bold=True, color='FFFFFF')
    fill = PatternFill('solid', start_color='1F6FEB')
    if kind == 'stock':
        rows = _stock_rows(kw, f, sort, dir_)
        ws.title = '库存'
        # 表头跟随列配置：使用者把「规格」改名「长」、给它配了单位「米」，
        # 界面上显示「长（米）」，导出也必须一致 —— 否则导出的表跟屏幕上
        # 看到的对不上，照着导出文件填再导回来就认不出列（值静默丢失）。
        # 另外 v3.21 已取消独立的「单位」列，这里不能再导出它。
        _tpl = int_arg(request.args, 'tpl') or db.default_tpl_id()
        _cols = [x for x in db.tpl_cols(_tpl) if x['fid'] != 'unit']
        _head = [db.col_label(x) for x in _cols] + ['入库', '出库', '当前库存']
        ws.append(_head)
        for r in rows:
            _line = [cell_val(r, x) for x in _cols]
            _line += [r['in_qty'], r['out_qty'], r['stock']]
            ws.append(_line)
        fn = '库存'
    elif kind == 'report':
        mats, days = _report_data(m)
        ws.title = m
        ws.append(['物料 / 料号'] + ['%d进' % d2 for d2 in range(1, days + 1)]
                  + ['%d出' % d2 for d2 in range(1, days + 1)] + ['进汇总', '出汇总', '月末'])
        for r in mats:
            ws.append([r['name'] + (' / ' + r['code'] if r['code'] else '')]
                      + [c[0] or None for c in r['cells']] + [c[1] or None for c in r['cells']]
                      + [r['min'], r['mout'], r['ending']])
        fn = '进出月报' + m
    else:
        rows = _txn_rows(kw, d, request.args.get('kind') or '', sort, dir_)
        ws.title = '流水'
        # 自定义列（客户订单号/单价/金额等）也一并导出
        _xtid = int_arg(request.args, 'tpl') or 0
        _xcs = []
        for _t in (db.tpls() if not _xtid else [db.tpl(_xtid)]):
            if not _t:
                continue
            for _xc in db.tpl_custom_cols(_t['id']):
                if _xc['label'] not in [x['label'] for x in _xcs]:
                    _xcs.append(dict(_xc, label=db.col_label(_xc)))
        # 平米 / 卷料：录单时按长宽自动算出来的，导出去却看不到就说不过去
        # （使用者要对账、要发给别人看，这两列是核心数据）。
        # 只有启用过这两列的模板才导出，没启用就不添乱。
        _sq_on, _rl_on = False, False
        for _t in (db.tpls() if not _xtid else [db.tpl(_xtid)]):
            if not _t:
                continue
            for _c2 in db.tpl_cols(_t['id']):
                if _c2['fid'] == 'sqm' and _c2['enabled']:
                    _sq_on = True
                elif _c2['fid'] == 'rolls' and _c2['enabled']:
                    _rl_on = True
        _calc_head = (['平米'] if _sq_on else []) + (['卷料'] if _rl_on else [])
        _head = ['日期', '物料名称', '料号', '类型', '进/出', '数量'] \
                + _calc_head + ['单价', '金额', '备注'] + [x['label'] for x in _xcs]
        ws.append(_head)
        for r in rows:
            _ex = {}
            if r['extra']:
                try:
                    _ex = json.loads(r['extra']) or {}
                except ValueError:
                    # 不能静默吞掉：之前就是静默 except 把 NameError 藏了三天
                    _log_err('导出解析自定义列失败 txn extra=%r' % (r['extra'],)[:200])
            _calc = []
            if _sq_on:
                _calc.append(r['sqm'] if 'sqm' in r.keys() else '')
            if _rl_on:
                _calc.append(r['rolls'] if 'rolls' in r.keys() else '')
            ws.append([r['tdate'], r['name'], r['code'], r['category'], r['kind'],
                       r['qty']] + _calc +
                      [r['price'], (r['amount'] if r['price'] else None), r['note']]
                      + [csv_safe(_ex.get(x['fid'], '')) for x in _xcs])
        fn = ('出入库流水' + (d or m))
    for c in ws[1]:
        c.font = head_font; c.fill = fill; c.alignment = Alignment(horizontal='center')
    ws.freeze_panes = 'A2'
    for i, w in enumerate([14, 14, 12, 10, 26, 18, 10, 10, 10, 10, 12, 10, 10, 20, 20], 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
    bio = io.BytesIO(); wb.save(bio)
    from urllib.parse import quote
    return Response(bio.getvalue(),
                    mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition':
                             "attachment; filename=export.xlsx; filename*=UTF-8''%s.xlsx" % quote(fn)})


@bp.route('/export.csv')
def export_csv():
    kind = request.args.get('t', 'stock')
    if kind == 'stock':
        # 跟 xlsx 导出保持一致：表头跟随列配置（含自定义名与单位），
        # 且不再导出 v3.21 已取消的「单位」列
        _tpl = int_arg(request.args, 'tpl') or db.default_tpl_id()
        _cols = [x for x in db.tpl_cols(_tpl) if x['fid'] != 'unit']
        rows, head = db.stock_rows(), [db.col_label(x) for x in _cols] + ['入库','出库','当前库存']
        data = [[csv_safe(cell_val(r, x)) for x in _cols]
                + [r['in_qty'], r['out_qty'], r['stock']] for r in rows]
        fn = '库存'
    else:
        m = safe_ym(request.args.get('m'))
        rows = db.q("SELECT t.tdate,m.name,m.code,m.unit,t.kind,t.qty,t.sqm,t.rolls,t.price,"
                    " %s AS amount, t.note FROM txns t"
                    " JOIN materials m ON m.id=t.material_id WHERE t.tdate LIKE ?"
                    " ORDER BY t.tdate,t.id"
                    % AMT.replace('t.', 't.'), m + '%')
        # 平米/卷料跟 xlsx 导出保持一致（录单算了就得导得出来）
        _sq_on, _rl_on = False, False
        for _t in db.tpls():
            for _c2 in db.tpl_cols(_t['id']):
                if _c2['fid'] == 'sqm' and _c2['enabled']:
                    _sq_on = True
                elif _c2['fid'] == 'rolls' and _c2['enabled']:
                    _rl_on = True
        _ch = (['平米'] if _sq_on else []) + (['卷料'] if _rl_on else [])
        head, data = ['日期','物料名称','料号','类型','数量'] + _ch + ['单价','金额','备注'], \
            [[r['tdate'],csv_safe(r['name']),csv_safe(r['code']),
              r['kind'],r['qty']]
             + ([r['sqm']] if _sq_on else []) + ([r['rolls']] if _rl_on else [])
             + [r['price'],(r['amount'] if r['price'] else None),
              csv_safe(r['note'])] for r in rows]
        fn = f'流水{m}'
    out = io.StringIO(); out.write('\ufeff')
    csv.writer(out).writerow(head); csv.writer(out).writerows(data)
    from urllib.parse import quote
    return Response(out.getvalue(), mimetype='text/csv; charset=utf-8',
                    headers={'Content-Disposition':
                             "attachment; filename=export.csv; filename*=UTF-8''%s.csv" % quote(fn)})


# ==================================================================
#  统计中心（按通用仓储统计报表模板重做）
#
#  八张表，覆盖收发存、周转、呆滞、ABC、预警、趋势、维度、盘点差异：
#    1 收发存汇总  期初 / 本期入库 / 本期出库 / 期末 / 单位成本 / 期末金额
#    2 周转分析    平均库存 / 本期出库 / 周转率(次) / 周转天数
#    3 呆滞库存    期末库存 / 库龄(天) / 库龄区间 / 金额 / 处理建议
#    4 ABC 分类    期末金额 / 累计占比 / A·B·C / 管理重点
#    5 安全库存预警 当前 / 安全库存 / 状态 / 建议措施
#    6 进出趋势    近 12 个月 进 / 出 / 净额
#    7 维度统计    按供应商 / 按类型 / 按物料 汇总
#    8 盘点差异    每次盘点的盘盈盘亏种类、数量、金额
#
#  口径统一说明（写死在这里，避免各处各算一套）：
#    期初 = 档案期初 + 期间开始前的进 − 期间开始前的出
#    期末 = 期初 + 本期入库 − 本期出库
#    平均库存 = (期初 + 期末) / 2
#    周转率 = 本期出库 / 平均库存      周转天数 = 期间天数 / 周转率
#    库龄   = 今天 − 最后一次出入库日期
#    金额   = 数量 × 最近一次填过的单价（没填过单价的按 0 计，不瞎估）
# ==================================================================

TABS = [('recv', '收发存汇总'), ('turn', '周转分析'), ('idle', '呆滞库存'),
        ('abc', 'ABC 分类'), ('warn', '安全库存预警'), ('trend', '进出趋势'),
        ('dim', '维度统计'), ('diff', '盘点差异')]


def _span(m, d1, d2):
    """把月份或自定义起止日期统一成 (lo, hi, 天数, 标题)"""
    if d1 and d2:
        lo, hi = d1, d2
        label = '%s ~ %s' % (d1, d2)
        try:
            days = (datetime.strptime(hi, '%Y-%m-%d')
                    - datetime.strptime(lo, '%Y-%m-%d')).days + 1
        except Exception:
            days = 30
        return lo, hi, max(days, 1), label
    m = safe_ym(m) or ym()
    return m + '-01', m + '-31', calendar.monthrange(int(m[:4]), int(m[5:7]))[1], m


def _period_rows(lo, hi):
    """每个物料在一个期间内的收发存（含单价与最后流动日期）"""
    out = {}
    for r in db.q("SELECT material_id mid,"
                  " SUM(CASE WHEN kind='进' AND tdate<? THEN qty ELSE 0 END) bin,"
                  " SUM(CASE WHEN kind='出' AND tdate<? THEN qty ELSE 0 END) bout,"
                  " SUM(CASE WHEN kind='进' AND tdate BETWEEN ? AND ? THEN qty ELSE 0 END) tin,"
                  " SUM(CASE WHEN kind='出' AND tdate BETWEEN ? AND ? THEN qty ELSE 0 END) tout,"
                  " SUM(CASE WHEN kind='进' AND tdate BETWEEN ? AND ?"
                  "     THEN qty*COALESCE(price,0) ELSE 0 END) tin_amt,"
                  " SUM(CASE WHEN kind='出' AND tdate BETWEEN ? AND ?"
                  "     THEN qty*COALESCE(price,0) ELSE 0 END) tout_amt,"
                  " MAX(tdate) last_date"
                  " FROM txns WHERE tdate<=? GROUP BY material_id",
                  lo, lo, lo, hi, lo, hi, lo, hi, lo, hi, hi):
        out[r['mid']] = dict(r)
    return out


def _base_rows(lo, hi):
    """物料档案 × 期间收发存 → 每行补齐期初/进/出/期末/金额/库龄"""
    per = _period_rows(lo, hi)
    lp = _last_prices()
    today_s = today()
    rows = []
    for m in db.q("SELECT id, name, code, spec, width, unit, category, supplier,"
                  " opening, safety, status FROM materials WHERE active=1 ORDER BY name"):
        d = dict(m)
        p = per.get(m['id']) or {}
        begin = float(m['opening'] or 0) + float(p.get('bin') or 0) - float(p.get('bout') or 0)
        tin = float(p.get('tin') or 0)
        tout = float(p.get('tout') or 0)
        d['begin'] = round(begin, 6)
        d['tin'] = round(tin, 6)
        d['tout'] = round(tout, 6)
        d['end'] = round(begin + tin - tout, 6)
        d['avg'] = round((begin + d['end']) / 2, 6)
        d['price'] = float(lp.get(m['id']) or 0)
        d['end_amt'] = round(d['end'] * d['price'], 2)
        d['tin_amt'] = round(float(p.get('tin_amt') or 0), 2)
        d['tout_amt'] = round(float(p.get('tout_amt') or 0), 2)
        # 库龄：最后一次流动的日期到今天；从没流动过但有库存的，按"从未流动"处理
        ld = p.get('last_date')
        if ld and is_date(ld):
            try:
                d['age'] = (datetime.strptime(today_s, '%Y-%m-%d')
                            - datetime.strptime(ld, '%Y-%m-%d')).days
            except Exception:
                d['age'] = None
        else:
            d['age'] = None
        d['last_date'] = ld or ''
        _t = tout / d['avg'] if d['avg'] > 1e-9 else None
        d['turn_raw'] = _t          # 周转天数用未舍入的值算，否则 0.27 会被放大成 111 天
        d['turn'] = round(_t, 2) if _t is not None else None
        rows.append(d)
    return rows


def _age_band(age):
    if age is None:
        return '从未流动'
    if age <= 30:
        return '30天内'
    if age <= 90:
        return '31-90天'
    if age <= 180:
        return '91-180天'
    return '180天以上'


def _build(tab, lo, hi, days):
    """生成某张报表的数据：返回 (表头, 行, 合计行)"""
    rows = _base_rows(lo, hi)
    if tab == 'recv':
        head = ['物料名称', '料号', '规格', '单位', '供应商', '类型',
                '期初数量', '本期入库', '本期出库', '期末数量',
                '单位成本', '期末金额']
        body = [[r['name'], r['code'] or '', r['spec'] or '', r['unit'] or '',
                 r['supplier'] or '', r['category'] or '',
                 r['begin'], r['tin'], r['tout'], r['end'],
                 r['price'], r['end_amt']] for r in rows]
        total = ['合计', '', '', '', '', '',
                 round(sum(r['begin'] for r in rows), 2),
                 round(sum(r['tin'] for r in rows), 2),
                 round(sum(r['tout'] for r in rows), 2),
                 round(sum(r['end'] for r in rows), 2), '',
                 round(sum(r['end_amt'] for r in rows), 2)]
        return head, body, total, rows

    if tab == 'turn':
        out = []
        for r in rows:
            if abs(r['tout']) < 1e-9 and abs(r['tin']) < 1e-9 and abs(r['end']) < 1e-9:
                continue
            tv = r['turn']
            out.append([r['name'], r['code'] or '', r['unit'] or '',
                        r['begin'], r['end'], r['avg'], r['tout'],
                        tv if tv is not None else '',
                        round(days / r['turn_raw'], 1) if r['turn_raw'] and r['turn_raw'] > 1e-9 else ''])
        out.sort(key=lambda x: -(x[7] or 0))
        head = ['物料名称', '料号', '单位', '期初', '期末', '平均库存',
                '本期出库', '周转率(次)', '周转天数']
        total = ['合计', '', '',
                 round(sum(r[3] for r in out), 2), round(sum(r[4] for r in out), 2),
                 round(sum(r[5] for r in out), 2), round(sum(r[6] for r in out), 2), '', '']
        return head, out, total, rows

    if tab == 'idle':
        out = []
        for r in rows:
            if r['end'] <= 1e-9:
                continue
            age = r['age']
            if age is not None and age < 90:
                continue
            band = _age_band(age)
            if age is None:
                sug = '长期无流动，确认是否还能用'
            elif age > 180:
                sug = '折价处理或报废'
            else:
                sug = '优先使用 / 协调其他订单'
            out.append([r['name'], r['code'] or '', r['spec'] or '', r['unit'] or '',
                        r['end'], r['price'], r['end_amt'],
                        age if age is not None else '—', band,
                        r['last_date'], sug])
        out.sort(key=lambda x: -(x[7] if isinstance(x[7], int) else 99999))
        head = ['物料名称', '料号', '规格', '单位', '呆滞数量', '单位成本',
                '金额', '库龄(天)', '库龄区间', '最后流动', '处理建议']
        total = ['合计 %d 种' % len(out), '', '', '',
                 round(sum(r[4] for r in out), 2), '',
                 round(sum(r[6] for r in out), 2), '', '', '', '']
        return head, out, total, rows

    if tab == 'abc':
        live = [r for r in rows if r['end_amt'] > 0]
        live.sort(key=lambda r: -r['end_amt'])
        s = sum(r['end_amt'] for r in live) or 1.0
        out, acc = [], 0.0
        for r in live:
            pct = acc / s * 100          # 这一项之前累计了多少
            acc += r['end_amt']
            # 用「加入这一项之前」的累计占比划档：
            # 单个物料金额就超过 70% 时，它是第一批，理应归 A 而不是被挤到 C
            if pct < 70:
                cls, focus = 'A', '重点控制，严格盘点'
            elif pct < 90:
                cls, focus = 'B', '次重点，定期盘点'
            else:
                cls, focus = 'C', '简化管理，抽查'
            out.append([r['name'], r['code'] or '', r['unit'] or '', r['end'],
                        r['price'], r['end_amt'], round(pct, 1), cls, focus])
        head = ['物料名称', '料号', '单位', '期末数量', '单位成本',
                '期末金额', '累计占比(%)', '分类', '管理重点']
        total = ['合计', '', '', round(sum(r[3] for r in out), 2), '',
                 round(sum(r[5] for r in out), 2), '', '', '']
        return head, out, total, rows

    if tab == 'warn':
        out = []
        for r in rows:
            st, sug, cls = '正常', '', ''
            if r['end'] <= 1e-9 and r['safety'] > 0:
                st, sug, cls = '缺料', '立即采购', 'low'
            elif r['safety'] > 0 and r['end'] < r['safety']:
                st, sug, cls = '低于安全库存', '安排补货（差 %.2f）' % (r['safety'] - r['end']), 'low'
            elif r['age'] is not None and r['age'] > 180 and r['end'] > 0:
                st, sug, cls = '呆滞', '启动处置流程', 'warn'
            elif r['safety'] > 0 and r['end'] > r['safety'] * 5 and r['safety'] > 0:
                st, sug, cls = '超储', '控制采购', 'warn'
            else:
                continue
            out.append([r['name'], r['code'] or '', r['unit'] or '', r['end'],
                        r['safety'], r['end'] - r['safety'], st, sug, cls])
        out.sort(key=lambda x: x[5])
        head = ['物料名称', '料号', '单位', '当前库存', '安全库存', '差额', '状态', '建议措施']
        total = ['合计 %d 种' % len(out), '', '', '', '', '', '', '']
        return head, out, total, (out, rows)

    if tab == 'trend':
        out = []
        for r in db.q("SELECT substr(tdate,1,7) ym,"
                      " SUM(CASE WHEN kind='进' THEN qty ELSE 0 END) i,"
                      " SUM(CASE WHEN kind='出' THEN qty ELSE 0 END) o,"
                      " SUM(CASE WHEN kind='进' THEN qty*COALESCE(price,0) ELSE 0 END) ia,"
                      " SUM(CASE WHEN kind='出' THEN qty*COALESCE(price,0) ELSE 0 END) oa,"
                      " COUNT(*) c FROM txns WHERE tdate<>'' GROUP BY ym"
                      " ORDER BY ym DESC LIMIT 24"):
            out.append([r['ym'], r['i'], r['o'], round(float(r['i'] or 0) - float(r['o'] or 0), 2),
                        round(float(r['ia'] or 0), 2), round(float(r['oa'] or 0), 2), r['c']])
        out.reverse()
        head = ['月份', '入库数量', '出库数量', '净额', '入库金额', '出库金额', '单据数']
        total = ['合计', round(sum(r[1] for r in out), 2), round(sum(r[2] for r in out), 2),
                 round(sum(r[3] for r in out), 2), round(sum(r[4] for r in out), 2),
                 round(sum(r[5] for r in out), 2), sum(r[6] for r in out)]
        return head, out, total, out

    if tab == 'dim':
        out = []
        for r in db.q("SELECT COALESCE(NULLIF(m.category,''),'（未分类）') k,"
                      " SUM(CASE WHEN t.kind='进' THEN t.qty ELSE 0 END) i,"
                      " SUM(CASE WHEN t.kind='出' THEN t.qty ELSE 0 END) o,"
                      " SUM(CASE WHEN t.kind='进' THEN t.qty*COALESCE(t.price,0) ELSE 0 END) ia,"
                      " SUM(CASE WHEN t.kind='出' THEN t.qty*COALESCE(t.price,0) ELSE 0 END) oa"
                      " FROM txns t JOIN materials m ON m.id=t.material_id"
                      " WHERE t.tdate BETWEEN ? AND ? GROUP BY k ORDER BY o DESC", lo, hi):
            out.append(['类型', r['k'], round(float(r['i'] or 0), 2), round(float(r['o'] or 0), 2),
                        round(float(r['ia'] or 0), 2), round(float(r['oa'] or 0), 2)])
        for r in db.q("SELECT COALESCE(NULLIF(m.supplier,''),'（未填）') k,"
                      " SUM(CASE WHEN t.kind='进' THEN t.qty ELSE 0 END) i,"
                      " SUM(CASE WHEN t.kind='出' THEN t.qty ELSE 0 END) o,"
                      " SUM(CASE WHEN t.kind='进' THEN t.qty*COALESCE(t.price,0) ELSE 0 END) ia,"
                      " SUM(CASE WHEN t.kind='出' THEN t.qty*COALESCE(t.price,0) ELSE 0 END) oa"
                      " FROM txns t JOIN materials m ON m.id=t.material_id"
                      " WHERE t.tdate BETWEEN ? AND ? GROUP BY k ORDER BY i DESC", lo, hi):
            out.append(['供应商', r['k'], round(float(r['i'] or 0), 2), round(float(r['o'] or 0), 2),
                        round(float(r['ia'] or 0), 2), round(float(r['oa'] or 0), 2)])
        head = ['维度', '名称', '入库数量', '出库数量', '入库金额', '出库金额']
        total = ['合计', '', round(sum(r[2] for r in out), 2), round(sum(r[3] for r in out), 2),
                 round(sum(r[4] for r in out), 2), round(sum(r[5] for r in out), 2)]
        return head, out, total, out

    # 盘点差异
    out = []
    try:
        stks = db.q("SELECT * FROM stk ORDER BY sdate DESC, id DESC LIMIT 100")
    except Exception:
        stks = []
    for s in stks:
        it = db.q("SELECT book_qty, real_qty, price FROM stk_items WHERE stk_id=?", s['id'])
        over = short = 0
        oq = sq = 0.0
        amt = 0.0
        for r in it:
            if r['real_qty'] is None:
                continue
            d = float(r['real_qty']) - float(r['book_qty'] or 0)
            if abs(d) < 1e-9:
                continue
            if d > 0:
                over += 1
                oq += d
            else:
                short += 1
                sq += -d
            amt += d * float(r['price'] or 0)
        out.append([s['sno'], s['sdate'], s['scope'] or '', len(it), over, short,
                    round(oq, 2), round(sq, 2), round(amt, 2), s['status']])
    head = ['盘点单号', '盘点日期', '范围', '物料种类', '盘盈种类', '盘亏种类',
            '盘盈数量', '盘亏数量', '差异金额', '状态']
    total = ['合计 %d 次' % len(out), '', '', sum(r[3] for r in out), sum(r[4] for r in out),
             sum(r[5] for r in out), round(sum(r[6] for r in out), 2),
             round(sum(r[7] for r in out), 2), round(sum(r[8] for r in out), 2), '']
    return head, out, total, out


@bp.route('/stat')
def stat_center():
    tab = request.args.get('tab') or 'recv'
    if tab not in [t[0] for t in TABS]:
        tab = 'recv'
    m = request.args.get('m') or ''
    # 注意：safe_date(None) 会返回今天，不能直接用在选填参数上，
    # 否则不填起止日期时统计区间会缩水成"只有今天"，页面看起来像没数据。
    d1 = (request.args.get('d1') or '').strip()
    d2 = (request.args.get('d2') or '').strip()
    d1 = d1 if is_date(d1) else ''
    d2 = d2 if is_date(d2) else ''
    lo, hi, days, label = _span(m, d1, d2)
    head, body, total, _ = _build(tab, lo, hi, days)

    # 顶部四张概览卡：期末库存金额 / 本期出入库 / 呆滞种类 / 预警种类
    rows = _base_rows(lo, hi)
    end_amt = round(sum(r['end_amt'] for r in rows), 2)
    tin = round(sum(r['tin'] for r in rows), 2)
    tout = round(sum(r['tout'] for r in rows), 2)
    idle_n = sum(1 for r in rows if r['end'] > 0 and (r['age'] is None or r['age'] >= 90))
    warn_n = sum(1 for r in rows if r['safety'] > 0 and r['end'] < r['safety'])
    months = [r['ym'] for r in db.q(
        "SELECT DISTINCT substr(tdate,1,7) ym FROM txns WHERE tdate<>''"
        " ORDER BY ym DESC LIMIT 24")]
    return render_template('stat_center.html', TABS=TABS, tab=tab, head=head, body=body,
                           total=total, label=label, m=m if not d1 else '',
                           d1=d1, d2=d2, months=months,
                           cards=[('期末库存金额', '%.2f' % end_amt, ''),
                                  ('本期入库', '%g' % tin, 'in'),
                                  ('本期出库', '%g' % tout, 'out'),
                                  ('呆滞 / 预警', '%d / %d' % (idle_n, warn_n),
                                   'warn' if warn_n else '')])


@bp.route('/stat/export')
def stat_export():
    tab = request.args.get('tab') or 'recv'
    if tab not in [t[0] for t in TABS]:
        tab = 'recv'
    m = request.args.get('m') or ''
    # 注意：safe_date(None) 会返回今天，不能直接用在选填参数上，
    # 否则不填起止日期时统计区间会缩水成"只有今天"，页面看起来像没数据。
    d1 = (request.args.get('d1') or '').strip()
    d2 = (request.args.get('d2') or '').strip()
    d1 = d1 if is_date(d1) else ''
    d2 = d2 if is_date(d2) else ''
    lo, hi, days, label = _span(m, d1, d2)
    head, body, total, _ = _build(tab, lo, hi, days)
    name = dict(TABS).get(tab, '统计')
    rows = body + [total]
    fmt = (request.args.get('fmt') or 'xlsx').lower()
    if fmt == 'csv':
        bio = io.StringIO()
        w = csv.writer(bio)
        w.writerow(head)
        for r in rows:
            w.writerow([csv_safe(v) for v in r])
        fn = '%s_%s.csv' % (name, label.replace(' ', ''))
        return Response('\ufeff' + bio.getvalue(), mimetype='text/csv; charset=utf-8',
                        headers={'Content-Disposition':
                                 "attachment; filename*=UTF-8''%s" % quote(fn)})
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.utils import get_column_letter
    except Exception:
        return redirect(url_for('stat_export', tab=tab, m=m, fmt='csv'))
    wb = Workbook()
    ws = wb.active
    ws.title = name[:31]
    ws.append(head)
    for c in ws[1]:
        c.font = Font(bold=True, color='FFFFFF')
        c.fill = PatternFill('solid', fgColor='4F6B8A')
        c.alignment = Alignment(horizontal='center')
    for r in rows:
        ws.append([csv_safe(v) for v in r])
    for c in ws[ws.max_row]:
        c.font = Font(bold=True)
    ws.freeze_panes = 'A2'
    for i in range(1, len(head) + 1):
        ws.column_dimensions[get_column_letter(i)].width = 14
    ws.column_dimensions['A'].width = 20
    # 数字列右对齐
    for row in ws.iter_rows(min_row=2):
        for c in row:
            if isinstance(c.value, (int, float)):
                c.alignment = Alignment(horizontal='right')
    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    fn = '%s_%s.xlsx' % (name, label.replace(' ', ''))
    return Response(bio.getvalue(),
                    mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition':
                             "attachment; filename*=UTF-8''%s" % quote(fn)})
