# -*- coding: utf-8 -*-
"""统计模块（1/2）：首页洞察、月报表、库存与流水导出

这一层是"看板"——回答"今天进了多少、这个月怎么样、库存和流水明细是什么"。
统计中心那八张分析表在 center.py。
"""
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

    # 洞察榜原来只有物料名称，重名的物料（同名称不同规格）在榜上分不出来，
    # 也没法照着料号去对账。这里把料号和规格一起带出来。
    names = {}
    for r in db.q("SELECT id, name, unit, code, spec FROM materials"):
        names[r['id']] = (r['name'], r['unit'] or '',
                          r['code'] or '', r['spec'] or '')
    # 未建档的采购明细没有 material_id，只能按名称反查档案补料号/规格
    _cs = {}
    for r in db.q("SELECT name, code, spec FROM materials"):
        _cs[(r['name'] or '').strip()] = (r['code'] or '', r['spec'] or '')

    def _nm(k):
        if isinstance(k, int) and k in names:
            return names[k]
        d = cur_p.get(k) or pre_p.get(k)
        if d:
            return (d['name'], d['unit']) + _cs.get((d['name'] or '').strip(), ('', ''))
        if isinstance(k, str) and k.startswith('n:'):
            return (k[2:] or '（未建档）', '', '', '')
        return ('（未建档）', '', '', '')

    def _rank(cur, key):
        rows = []
        for k, d in cur.items():
            v = d[key] if isinstance(d, dict) else d
            if v <= 0:
                continue
            n, u, _c, _s = _nm(k)
            prev = 0.0
            if key == 'amt' or key == 'qty':
                pd = pre_p.get(k)
                prev = (pd[key] if pd else 0.0)
            else:
                prev = (pre_u.get(k) or 0.0)
            rows.append(dict(mid=k if isinstance(k, int) else None, name=n, unit=u,
                             code=_c, spec=_s,
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
        n, u, _c, _s = _nm(k)
        prev = pre_u.get(k) or 0.0
        use_rows.append(dict(mid=k, name=n, unit=u, code=_c, spec=_s, val=round(v, 2),
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
        n, u, _c, _s = _nm(k)
        trend.append(dict(mid=k if isinstance(k, int) else None, name=n, unit=u,
                          code=_c, spec=_s,
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

def _txn_where(kw, d, kind, m=None):
    """流水筛选条件（导出与计数共用，避免两处写法走偏）。

    m 为可选月份（yyyy-mm）。界面上「按日期」和「或按月份」是两个互斥的框，
    所以两者都给时以更精确的 d 为准；只给 m 时按月份前缀匹配。
    """
    w, args = [], []
    if d:
        w.append("t.tdate=?"); args.append(d)
    elif m:
        w.append("t.tdate LIKE ?"); args.append(m + '%')
    if kind:
        w.append("t.kind=?"); args.append(kind)
    if kw:
        w.append("(m.name LIKE ? OR m.code LIKE ? OR t.note LIKE ? OR m.category LIKE ?)")
        args += ['%%%s%%' % kw] * 4
    return w, args


def _txn_count(kw, d, kind, m=None):
    """符合条件的总笔数 —— 用于判断导出是否被截断。"""
    w, args = _txn_where(kw, d, kind, m)
    sql = "SELECT COUNT(*) FROM txns t JOIN materials m ON m.id=t.material_id"
    if w:
        sql += " WHERE " + " AND ".join(w)
    r = db.q(sql, *args)
    try:
        return int(r[0][0] or 0)
    except (IndexError, TypeError, ValueError):
        return 0


def _txn_rows(kw, d, kind, sort, dir_, limit=2000, m=None):
    # 导出曾经沿用页面思路写死 LIMIT 2000：5 万笔单据导出只出 2000 笔且不给提示，
    # 拿着去对账会以为拿到了全量。页面限 2000 是为了渲染速度，导出不该共用这个上限。
    sql = ("SELECT t.*, m.name, m.unit, m.code, m.category, m.spec, m.supplier,"
           " m.id AS mid, %s AS amount FROM txns t"
           " JOIN materials m ON m.id=t.material_id" % AMT)
    w, args = _txn_where(kw, d, kind, m)
    if w:
        sql += " WHERE " + " AND ".join(w)
    sql += " ORDER BY t.tdate DESC, t.id DESC"
    if limit:
        sql += " LIMIT %d" % int(limit)
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

    # 该月之前的累计净额（进 - 出），按物料汇总。
    #
    # 原写法 "WHERE tdate < ? GROUP BY material_id, kind" 会被 SQLite 选到
    # idx_txns_cover(material_id,kind,qty)：该索引不含 tdate，于是逐行回表查日期。
    # 5 万单据实测 6~9s，月报页整体 11.8s，是全系统最慢的页面。
    #
    # 改成"按月份一次覆盖扫描"（走 idx_txns_full，索引内即可完成，不回表）后 0.17s。
    # 注意：这里刻意不加 WHERE —— 实测加了 WHERE tdate<=? 反而会让 SQLite 改用
    # idx_txns_date 再次回表，耗时回到 6s。不加 WHERE 才是快的那个。
    before = {}
    for r in db.q("SELECT substr(tdate,1,7) ym, material_id, kind, SUM(qty) q"
                  " FROM txns GROUP BY ym, material_id, kind"):
        if not r['ym'] or r['ym'] >= m:
            continue
        before[r['material_id']] = before.get(r['material_id'], 0.0) + \
            (r['q'] if r['kind'] == '进' else -r['q'])

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
        ws.append(['物料 / 料号 / 规格'] + ['%d进' % d2 for d2 in range(1, days + 1)]
                  + ['%d出' % d2 for d2 in range(1, days + 1)] + ['进汇总', '出汇总', '月末'])
        for r in mats:
            _lab = r['name']
            if r['code']:
                _lab += ' / ' + r['code']
            if r.get('spec'):
                _lab += ' / ' + r['spec']
            ws.append([_lab]
                      + [c[0] or None for c in r['cells']] + [c[1] or None for c in r['cells']]
                      + [r['min'], r['mout'], r['ending']])
        fn = '进出月报' + m
    else:
        # 导出要拿全量：上限 20 万笔（Excel 单表 104 万行，留足余量）。
        # 真超了就在末尾写明，绝不静默截断。
        _TXLIMIT = 200000
        # 月份也要接：界面上有「或按月份」框，导出却只认 d，
        # 结果选了月份导出的还是全量（Excel 50002 条 vs CSV 2088 条）。
        _m = opt_ym(request.args.get('m'))
        rows = _txn_rows(kw, d, request.args.get('kind') or '', sort, dir_,
                         limit=_TXLIMIT, m=_m)
        _txtotal = _txn_count(kw, d, request.args.get('kind') or '', _m)
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
        # 截断必须写明：否则使用者以为拿到全量，拿去对账才发现少了大半。
        if _txtotal > len(rows):
            ws.append(['（本表仅导出最新 %d 笔，实际共 %d 笔。'
                       '请按日期或物料筛选后分批导出）' % (len(rows), _txtotal)])
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
    """CSV 导出。

    必须跟 /export.xlsx 取同一批数据、同一套表头：以前 CSV 只传月份、
    不看搜索词/日期/类型/排序，屏幕筛选「铜箔」后导出的却是当月全部
    （实测 Excel 50002 条 vs CSV 2088 条，差 24 倍），跟所见不一致，
    对账时极易误判。现在两边共用 _txn_rows / _stock_rows，口径天然一致。
    月份只是可选辅助：不传就导出全量，不再是「默认当月」。
    """
    kind = request.args.get('t', 'stock')
    kw = clean_kw(request.args.get('kw'))
    f = request.args.get('f') or ''
    d = request.args.get('d') or ''
    m = opt_ym(request.args.get('m'))
    sort = request.args.get('sort') or ''
    dir_ = request.args.get('dir') or ''
    _tpl = int_arg(request.args, 'tpl') or db.default_tpl_id()
    if kind == 'stock':
        _cols = [x for x in db.tpl_cols(_tpl) if x['fid'] != 'unit']
        rows = _stock_rows(kw, f, sort, dir_)
        head = [db.col_label(x) for x in _cols] + ['入库', '出库', '当前库存']
        data = [[csv_safe(cell_val(r, x)) for x in _cols]
                + [r['in_qty'], r['out_qty'], r['stock']] for r in rows]
        fn = '库存'
    else:
        _TXLIMIT = 200000
        rows = _txn_rows(kw, d, request.args.get('kind') or '', sort, dir_,
                         limit=_TXLIMIT, m=m)
        _txtotal = _txn_count(kw, d, request.args.get('kind') or '', m)
        _xcs = []
        for _t in (db.tpls() if not _tpl else [db.tpl(_tpl)]):
            if not _t:
                continue
            for _xc in db.tpl_custom_cols(_t['id']):
                if _xc['label'] not in [x['label'] for x in _xcs]:
                    _xcs.append(dict(_xc, label=db.col_label(_xc)))
        _sq_on, _rl_on = False, False
        for _t in (db.tpls() if not _tpl else [db.tpl(_tpl)]):
            if not _t:
                continue
            for _c2 in db.tpl_cols(_t['id']):
                if _c2['fid'] == 'sqm' and _c2['enabled']:
                    _sq_on = True
                elif _c2['fid'] == 'rolls' and _c2['enabled']:
                    _rl_on = True
        _calc_head = (['平米'] if _sq_on else []) + (['卷料'] if _rl_on else [])
        head = (['日期', '物料名称', '料号', '类型', '进/出', '数量']
                + _calc_head + ['单价', '金额', '备注']
                + [x['label'] for x in _xcs])
        data = []
        for r in rows:
            _ex = {}
            if r['extra']:
                try:
                    _ex = json.loads(r['extra']) or {}
                except ValueError:
                    _log_err('CSV 导出解析自定义列失败 txn extra=%r'
                             % (r['extra'],)[:200])
            _calc = []
            if _sq_on:
                _calc.append(r['sqm'] if 'sqm' in r.keys() else '')
            if _rl_on:
                _calc.append(r['rolls'] if 'rolls' in r.keys() else '')
            data.append([r['tdate'], csv_safe(r['name']), csv_safe(r['code']),
                         csv_safe(r['category']), r['kind'], r['qty']] + _calc +
                        [r['price'], (r['amount'] if r['price'] else None),
                         csv_safe(r['note'])]
                        + [csv_safe(_ex.get(x['fid'], '')) for x in _xcs])
        # 截断必须写明：否则使用者以为拿到全量，拿去对账才发现少了大半。
        if _txtotal > len(rows):
            data.append(['（本表仅导出最新 %d 笔，实际共 %d 笔。'
                         '请按日期或物料筛选后分批导出）' % (len(rows), _txtotal)])
        fn = '出入库流水' + (d or m)
    # 浮点长尾要抹掉：金额 3562.36 在 CSV 里会写成 3562.3600000000006，
    # 发给别人看、再导回来都别扭。取 6 位小数 —— 远高于金额精度，
    # 又足以吃掉 IEEE754 的噪声（数量 0.333333 这类真小数不受影响）。
    def _num(v):
        return round(v, 6) if isinstance(v, float) and v == v else v
    data = [[_num(x) for x in row] for row in data]
    out = io.StringIO(); out.write('\ufeff')
    csv.writer(out).writerow(head); csv.writer(out).writerows(data)
    from urllib.parse import quote
    return Response(out.getvalue(), content_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition':
                             "attachment; filename=export.csv; filename*=UTF-8''%s.csv" % quote(fn)})
