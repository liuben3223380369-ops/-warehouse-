# -*- coding: utf-8 -*-
"""统计模块（2/2）：统计中心八张分析表

收发存 / 周转 / 呆滞 / ABC / 预警 / 趋势 / 维度 / 盘点差异。
所有口径写死在这里，避免各处各算一套。
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
from .report import csv_safe      # 导出防 CSV 公式注入，两边共用


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
            out.append([r['name'], r['code'] or '', r['spec'] or '', r['unit'] or '',
                        r['begin'], r['end'], r['avg'], r['tout'],
                        tv if tv is not None else '',
                        round(days / r['turn_raw'], 1) if r['turn_raw'] and r['turn_raw'] > 1e-9 else ''])
        out.sort(key=lambda x: -(x[8] or 0))
        head = ['物料名称', '料号', '规格', '单位', '期初', '期末', '平均库存',
                '本期出库', '周转率(次)', '周转天数']
        total = ['合计', '', '', '',
                 round(sum(r[4] for r in out), 2), round(sum(r[5] for r in out), 2),
                 round(sum(r[6] for r in out), 2), round(sum(r[7] for r in out), 2), '', '']
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
            out.append([r['name'], r['code'] or '', r['spec'] or '', r['unit'] or '', r['end'],
                        r['price'], r['end_amt'], round(pct, 1), cls, focus])
        head = ['物料名称', '料号', '规格', '单位', '期末数量', '单位成本',
                '期末金额', '累计占比(%)', '分类', '管理重点']
        total = ['合计', '', '', '', round(sum(r[4] for r in out), 2), '',
                 round(sum(r[6] for r in out), 2), '', '', '']
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
            out.append([r['name'], r['code'] or '', r['spec'] or '', r['unit'] or '', r['end'],
                        r['safety'], r['end'] - r['safety'], st, sug, cls])
        out.sort(key=lambda x: x[6])
        head = ['物料名称', '料号', '规格', '单位', '当前库存', '安全库存', '差额', '状态', '建议措施']
        total = ['合计 %d 种' % len(out), '', '', '', '', '', '', '', '']
        return head, out, total, (out, rows)

    if tab == 'trend':
        # 以前这张表完全不看统计区间，恒按整张表分组再 LIMIT 24：
        # 选了区间也照样显示全库的月份，跟其余七张表口径不一致；
        # 单据跨过 24 个月时老数据被静默丢掉，页面上看不出少了。
        # 现在先按区间过滤再分组，并在真截断时于合计行写明。
        _w, _a = "tdate<>''", []
        if lo and hi:
            _w += " AND tdate BETWEEN ? AND ?"
            _a += [lo, hi]
        _nmon = db.q("SELECT COUNT(*) c FROM (SELECT DISTINCT substr(tdate,1,7) ym"
                     " FROM txns WHERE %s)" % _w, *_a)[0]['c']
        out = []
        for r in db.q("SELECT substr(tdate,1,7) ym,"
                      " SUM(CASE WHEN kind='进' THEN qty ELSE 0 END) i,"
                      " SUM(CASE WHEN kind='出' THEN qty ELSE 0 END) o,"
                      " SUM(CASE WHEN kind='进' THEN qty*COALESCE(price,0) ELSE 0 END) ia,"
                      " SUM(CASE WHEN kind='出' THEN qty*COALESCE(price,0) ELSE 0 END) oa,"
                      " COUNT(*) c FROM txns WHERE %s GROUP BY ym"
                      " ORDER BY ym DESC LIMIT 24" % _w, *_a):
            out.append([r['ym'], r['i'], r['o'], round(float(r['i'] or 0) - float(r['o'] or 0), 2),
                        round(float(r['ia'] or 0), 2), round(float(r['oa'] or 0), 2), r['c']])
        out.reverse()
        head = ['月份', '入库数量', '出库数量', '净额', '入库金额', '出库金额', '单据数']
        # 提示写在合计行而不是数据行：_chart_spec 取 body[-24:] 画图，
        # 数据行里塞提示会变成图表上一个值为 0 的假类目。
        _lab = '合计'
        if _nmon > 24:
            _lab = ('合计·注意：仅含最新 %d 个月，实际共 %d 个月，'
                    '请缩短统计区间分次查看' % (len(out), _nmon))
        total = [_lab, round(sum(r[1] for r in out), 2), round(sum(r[2] for r in out), 2),
                 round(sum(r[3] for r in out), 2), round(sum(r[4] for r in out), 2),
                 round(sum(r[5] for r in out), 2), sum(r[6] for r in out)]
        return head, out, total, out

    if tab == 'dim':
        cat, sup = [], []
        for r in db.q("SELECT COALESCE(NULLIF(m.category,''),'（未分类）') k,"
                      " SUM(CASE WHEN t.kind='进' THEN t.qty ELSE 0 END) i,"
                      " SUM(CASE WHEN t.kind='出' THEN t.qty ELSE 0 END) o,"
                      " SUM(CASE WHEN t.kind='进' THEN t.qty*COALESCE(t.price,0) ELSE 0 END) ia,"
                      " SUM(CASE WHEN t.kind='出' THEN t.qty*COALESCE(t.price,0) ELSE 0 END) oa"
                      " FROM txns t JOIN materials m ON m.id=t.material_id"
                      " WHERE t.tdate BETWEEN ? AND ? GROUP BY k ORDER BY o DESC", lo, hi):
            cat.append(['类型', r['k'], round(float(r['i'] or 0), 2), round(float(r['o'] or 0), 2),
                        round(float(r['ia'] or 0), 2), round(float(r['oa'] or 0), 2)])
        for r in db.q("SELECT COALESCE(NULLIF(m.supplier,''),'（未填）') k,"
                      " SUM(CASE WHEN t.kind='进' THEN t.qty ELSE 0 END) i,"
                      " SUM(CASE WHEN t.kind='出' THEN t.qty ELSE 0 END) o,"
                      " SUM(CASE WHEN t.kind='进' THEN t.qty*COALESCE(t.price,0) ELSE 0 END) ia,"
                      " SUM(CASE WHEN t.kind='出' THEN t.qty*COALESCE(t.price,0) ELSE 0 END) oa"
                      " FROM txns t JOIN materials m ON m.id=t.material_id"
                      " WHERE t.tdate BETWEEN ? AND ? GROUP BY k ORDER BY i DESC", lo, hi):
            sup.append(['供应商', r['k'], round(float(r['i'] or 0), 2), round(float(r['o'] or 0), 2),
                        round(float(r['ia'] or 0), 2), round(float(r['oa'] or 0), 2)])
        out = cat + sup
        head = ['维度', '名称', '入库数量', '出库数量', '入库金额', '出库金额']
        # 「类型」和「供应商」是两条独立分组，覆盖的是同一批单据。之前合计行
        # 把两组直接相加，得出真实值的 2 倍（实测 5524631.72 vs 2762315.86）。
        # 任一维度各自的合计就等于真实值，所以取类型维，并在标签里写明
        # 不要把各行加起来 —— 否则使用者照着页面加总仍会得到翻倍的数。
        total = ['合计（各维分别统计，勿将各行相加）', '',
                 round(sum(r[2] for r in cat), 2), round(sum(r[3] for r in cat), 2),
                 round(sum(r[4] for r in cat), 2), round(sum(r[5] for r in cat), 2)]
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


def _fit(head, body):
    """把 _build 的 body 裁成与 head 等长，并把行样式类单独摘出来。

    预警表每行末尾多带一个 cls（'low'/'warn'），本意是给行上色，但既没有
    对应表头、模板也没当样式用，结果被直接渲染成多出来的一列 ——
    实测表头 9 列、数据 10 列，最后一格直接显示 'low'。
    这里把它从数据里摘出来交给 <tr>，单元格严格按表头数量输出，
    页面和导出的列数才对得上。其余报表行长度本就等于表头，原样返回。
    """
    n = len(head)
    cls, out = [], []
    for r in body:
        cls.append(r[n] if len(r) > n else '')
        out.append(list(r[:n]))
    return out, cls


def _num(v):
    """图表用：把单元格值转成数字，空值和 '-' 都算 0"""
    try:
        if v is None or v == '' or v == '—':
            return 0.0
        return float(v)
    except Exception:
        return 0.0


def _chart_spec(tab, body):
    """把报表数据整理成图表规格。

    同一份 spec 既喂页面上的 ECharts，也喂导出的 Excel 原生图表，
    所以页面上看到的图和导出的图永远一致，不用维护两份。
    取数用列索引，跟 _build 里 head 的顺序一一对应。
    """
    try:
        if not body:
            return None
        if tab == 'recv':
            rs = sorted(body, key=lambda r: -_num(r[9]))[:12]
            return dict(type='column', title='收发存 Top12（按期末数量）',
                        x_title='物料', y_title='数量',
                        cats=[r[0] for r in rs],
                        series=[{'name': '期初', 'values': [_num(r[6]) for r in rs]},
                                {'name': '入库', 'values': [_num(r[7]) for r in rs]},
                                {'name': '出库', 'values': [_num(r[8]) for r in rs]},
                                {'name': '期末', 'values': [_num(r[9]) for r in rs]}])
        if tab == 'turn':
            rs = body[:12]
            return dict(type='column', title='周转率 Top12',
                        x_title='物料', y_title='次数',
                        cats=[r[0] for r in rs],
                        series=[{'name': '周转率(次)',
                                 'values': [_num(r[8]) for r in rs]}])
        if tab == 'idle':
            agg = {}
            for r in body:
                agg[r[8]] = agg.get(r[8], 0.0) + _num(r[4])
            return dict(type='pie', title='呆滞库存按库龄区间', x_title='区间',
                        cats=list(agg.keys()),
                        series=[{'name': '呆滞数量',
                                 'values': [round(v, 2) for v in agg.values()]}])
        if tab == 'abc':
            agg = {}
            for r in body:
                agg[r[8]] = agg.get(r[8], 0.0) + _num(r[6])
            order = [k for k in ('A', 'B', 'C') if k in agg]
            return dict(type='pie', title='ABC 金额占比', x_title='分类',
                        cats=order,
                        series=[{'name': '期末金额',
                                 'values': [round(agg[k], 2) for k in order]}])
        if tab == 'warn':
            rs = body[:15]
            return dict(type='column', title='预警物料：当前库存 vs 安全库存',
                        x_title='物料', y_title='数量',
                        cats=[r[0] for r in rs],
                        series=[{'name': '当前库存', 'values': [_num(r[4]) for r in rs]},
                                {'name': '安全库存', 'values': [_num(r[5]) for r in rs]}])
        if tab == 'trend':
            rs = body[-24:]
            return dict(type='line', title='进出趋势',
                        x_title='月份', y_title='数量',
                        cats=[r[0] for r in rs],
                        series=[{'name': '入库', 'values': [_num(r[1]) for r in rs]},
                                {'name': '出库', 'values': [_num(r[2]) for r in rs]},
                                {'name': '净额', 'values': [_num(r[3]) for r in rs]}])
        if tab == 'dim':
            rs = body[:15]
            return dict(type='column', title='维度统计 Top15',
                        x_title='维度 / 名称', y_title='数量',
                        cats=['%s·%s' % (r[0], r[1]) for r in rs],
                        series=[{'name': '入库数量', 'values': [_num(r[2]) for r in rs]},
                                {'name': '出库数量', 'values': [_num(r[3]) for r in rs]}])
        if tab == 'diff':
            rs = body[-20:]
            return dict(type='column', title='盘点差异',
                        x_title='盘点单号', y_title='数量',
                        cats=[r[0] for r in rs],
                        series=[{'name': '盘盈', 'values': [_num(r[6]) for r in rs]},
                                {'name': '盘亏', 'values': [_num(r[7]) for r in rs]}])
    except Exception:
        return None
    return None


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
    chart = _chart_spec(tab, body)
    body, rowcls = _fit(head, body)

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
                           chart=chart, rowcls=rowcls,
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
    # 裁剪到表头长度：否则预警表会多导出一列没有表头的 'low'
    body, _ = _fit(head, body)
    rows = body + [total]
    fmt = (request.args.get('fmt') or 'xlsx').lower()
    if fmt == 'csv':
        bio = io.StringIO()
        w = csv.writer(bio)
        w.writerow(head)
        for r in rows:
            w.writerow([csv_safe(v) for v in r])
        fn = '%s_%s.csv' % (name, label.replace(' ', ''))
        return Response('\ufeff' + bio.getvalue(), content_type='text/csv; charset=utf-8',
                        headers={'Content-Disposition':
                                 "attachment; filename*=UTF-8''%s" % quote(fn)})
    # 走表格模块的导出层：xlsxwriter 优先（快、省内存），
    # 并把页面上的那张图原样画进 Excel —— 导出的表和看到的图永远一致。
    fn = '%s_%s.xlsx' % (name, label.replace(' ', ''))
    try:
        return tbl.xlsx_response(head, rows, fn, sheet=name,
                                 title='%s %s' % (name, label),
                                 chart=_chart_spec(tab, body))
    except Exception:
        _log_err('stat_export')
        # 两种引擎都装不上时退回 CSV，别让用户点了个没反应的按钮
        return redirect(url_for('stat_export', tab=tab, m=m, d1=d1, d2=d2,
                                fmt='csv'))
