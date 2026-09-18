# -*- coding: utf-8 -*-
"""出入库模块"""

from ..core.router import Router
bp = Router('txn')

# -*- coding: utf-8 -*-
"""出入库模块：单据录入、流水查询、Excel 导入"""
from flask import request, redirect, url_for, Response, render_template
from datetime import datetime, date
import calendar, io, csv, os, time, sys, json
from urllib.parse import quote
from . import bp
from ..core import db, util
from ..core.util import *            # noqa: F401,F403, int_arg
from ..core.util import (_log_err, _last_prices, js_mats_with_price)  # noqa: F401
from .. import importer
from ..table import tbl
# 流水页 / 出入库列表要显示自定义列，这两个函数在表格模块里。
# 拆分时漏了这行，导致 /txns 直接 500 —— 名字带下划线是避免和 util 里的重名。
from ..table.helpers import all_custom_cols as _all_custom_cols, cell_val as _cell_val
from ..po.logic import (price_map, sync_txn_to_po, txn_unit_price)  # noqa: F401

# ---------- 单据流水（表格录入，含原表 A-G 全部列） ----------
def _fingerprint(vals):
    """物料唯一指纹：名称 + 规格 + 宽幅（+供应商，若三者都相同仍冲突时区分）。

    原表里同一个「0.05金」可能有 0.04 / 0.045 / 0.05 三种宽幅的批次，
    「0.05胶」甚至分属两个不同供应商。只按名称匹配会把它们并成一个物料，
    规格信息全部丢失、库存张冠李戴。带上规格和宽幅才能唯一定位。
    """
    def c(k):
        return (str(vals.get(k) or '').strip())
    parts = [c('name')]
    sp, wd = c('spec'), c('width')
    if sp: parts.append('规格' + sp)
    if wd: parts.append('宽' + wd)
    return '|'.join(parts)

def _resolve_material(vals, sqm=None, rolls=None, tpl_id=None, qty_unit=None):
    """按 料号 -> 名称+规格指纹 匹配物料；匹配到则用行内 A-G 值同步档案，否则新建。
    返回 (material_id, is_new)"""
    # 没指定模板（比如从 Excel 导入）时归入默认模板，
    # 否则这些物料会变成"无模板"，库存页和月报都统计不到
    if tpl_id is None:
        tpl_id = db.default_tpl_id()
    name = (vals.get('name') or '').strip()
    code = (vals.get('code') or '').strip()
    hit = None
    if code:
        hit = db.q("SELECT id FROM materials WHERE code=? AND code<>''", code)
    if not hit and name:
        # 先按 名称+规格+宽幅 精确匹配：同名不同规格是两种物料
        spec = (vals.get('spec') or '').strip()
        width = (vals.get('width') or '').strip()
        if spec or width:
            hit = db.q("SELECT id FROM materials WHERE name=? AND COALESCE(spec,'')=?"
                       " AND COALESCE(width,'')=?", name, spec, width)
        if not hit:
            # 没有规格信息（或表里没填）时才退回只按名称
            hit = db.q("SELECT id FROM materials WHERE name=? AND COALESCE(spec,'')=''"
                       " AND COALESCE(width,'')=''", name) if not (spec or width) else None
        if not hit and not (spec or width):
            hit = db.q("SELECT id FROM materials WHERE name=?", name)
    if hit:
        mid = hit[0]['id']
        sets, vs = [], []
        for f in ('name', 'code', 'supplier', 'category', 'spec', 'width', 'unit', 'status'):
            if f in vals and (vals[f] or '').strip():
                sets.append("%s=?" % f); vs.append((vals[f] or '').strip() or None)
        # v3.28：平米/卷料是**这一笔**的数（总面积、卷数），每笔都不一样，
        # 不能往物料档案里写 —— 否则档案被最后一笔单据覆盖，成了脏数据。
        if qty_unit in db.QTY_UNITS:
            # 记住这个物料的口径，下次联想选中时自动带出，不用每次重选
            sets.append("qty_unit=?"); vs.append(qty_unit)
        if sets:
            vs.append(mid)
            db.run("UPDATE materials SET " + ",".join(sets) + " WHERE id=?", *vs)
        return mid, False
    if not name:
        return None, False
    mid = db.run("INSERT INTO materials(supplier,category,spec,width,name,code,unit,opening,safety,status,sqm,rolls,tpl_id,qty_unit)"
                 " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 vals.get('supplier', ''), vals.get('category', ''), vals.get('spec', ''),
                 vals.get('width', ''), name, code, (vals.get('unit') or '').strip() or '',
                 float(vals.get('opening') or 0), float(vals.get('safety') or 0),
                 (vals.get('status') or '').strip() or '', None, None, tpl_id,
                 qty_unit if qty_unit in db.QTY_UNITS else '平米')
    return mid, True

@bp.route('/txn', methods=['GET', 'POST'], endpoint='txn')
@bp.route('/out', methods=['GET', 'POST'], endpoint='out')
@bp.route('/in', methods=['GET', 'POST'], endpoint='in')
def txn():
    """表格录单。/in 默认入库、/out 默认出库（且锁定为出库）、/txn 通用"""
    ep = request.endpoint
    fixed = '出' if ep == 'out' else ('进' if ep == 'in' else None)
    # 模板：入库/出库/录单都按选中的模板加载列；没指定就用第一个模板
    # tpl 也常来自网址参数（?tpl=2），手改坏成 ?tpl=abc 不能让页面 500
    try:
        tid = int_arg(request.values, 'tpl')
    except (TypeError, ValueError):
        tid = 0
    tid = tid or db.default_tpl_id()
    if not db.tpl(tid):
        tid = db.default_tpl_id()
    # v3.43：录入卡片长什么样，全部问表格模块要 —— 卡片字段 = 这张表的列。
    # 换表格 / 加列 / 减列 / 改名，卡片跟着变，出入库这边不做任何列判断。
    _sch = tbl.form_schema(tid, scene='txn')
    cols = _sch['raw_cols']
    fids = _sch['fids']
    only_stock = fixed == '出' and request.args.get('allm') != '1'
    mats = [dict(r) for r in db.q(
        "SELECT * FROM v_stock WHERE active=1" + (" AND stock>0" if only_stock else "")
        + " ORDER BY name")]
    msg = request.args.get('msg', '')

    if request.method == 'POST':
        _back = 'out' if ep == 'out' else ('in' if ep == 'in' else 'txn')
        # 一次性令牌：挡住"网络卡顿时连点两下"造成的重复记账
        if not take_nonce(request.form.get('_n')):
            return redirect(url_for(_back,
                msg='这一批已经保存过了，请不要重复提交（可去流水页核对）'))
        nrow = min(int_arg(request.form, 'nrow', lo=0), MAX_ROWS)
        saved = newmat = blocked = 0
        names = []
        po_tips = []      # v3.35 入库同步到采购流水的回执（批次对上了才可能有）
        dflt = request.form.get('tdate') or today()
        # 一次提交多行时要么全成功要么全回滚：
        # 否则中途出错（比如某一行的物料档案更新失败）会留下"录了一半"的单据，
        # 用户看到报错后重录，那几行就重复了。
        try:
          with db.tx() as _cx:
            for i in range(nrow):
                qty = num(request.form.get(f'qty_{i}'), hi=QTY_MAX)
                vals = {f: (request.form.get(f'{f}_{i}') or '').strip() for f in
                        ('name', 'code', 'supplier', 'category', 'spec', 'width', 'unit', 'status')}
                # v3.28：平米/卷料也收进来 —— 使用者可以直接填这两个数，
                # 填了哪个就按哪个推另一个。以前只从数量推，手填的值会被丢掉。
                for _af in ('sqm', 'rolls'):
                    if _af in fids:
                        vals[_af] = (request.form.get(f'{_af}_{i}') or '').strip()
                vals['opening'] = num(request.form.get(f'opening_{i}'))
                vals['safety'] = num(request.form.get(f'safety_{i}'))
                # 件数 / 每件已移除（v3.20），不再从表单读取
                pieces = per = None
                if qty <= 0 or (not vals['name'] and not vals['code']):
                    continue
                # 长 × 宽 = 平米；再由数量口径算卷料（取整，余料自动写备注）
                # 使用者没启用这两列时算不出来，保持空，不瞎填 0
                sqm = rolls = rest = None; note_rest = ''
                # 物料档案里记的默认口径：联想选中、或按名称匹配到已有物料时用
                _mqu = ''
                _mq = None
                if vals.get('code'):
                    _mq = db.q("SELECT qty_unit FROM materials WHERE code=? AND tpl_id=? LIMIT 1",
                               vals['code'], tid)
                if not _mq and vals.get('name'):
                    _mq = db.q("SELECT qty_unit FROM materials WHERE name=? AND tpl_id=? LIMIT 1",
                               vals['name'], tid)
                if _mq:
                    _mqu = (_mq[0]['qty_unit'] or '').strip()
                # 数量口径：本行选了就用选的，没选（联想/导入）就跟随物料档案
                qu = (request.form.get('qty_unit_%d' % i) or '').strip()
                if qu not in db.QTY_UNITS:
                    qu = (request.form.get('qty_unit') or '').strip()
                if qu not in db.QTY_UNITS:
                    qu = _mqu or '平米'
                if 'sqm' in fids or 'rolls' in fids:
                    # 卷数 = 总量 ÷ 一卷平米，所以要带上这一笔的数量
                    sqm, rolls, rest = db.calc_area(dict(vals, qty=qty, qty_unit=qu))
                    if rest:
                        note_rest = db.rest_note(rest)
                mid, is_new = _resolve_material(vals, sqm=sqm, rolls=rolls, tpl_id=tid,
                                                qty_unit=qu)
                if not mid:
                    continue
                # 录单页没填长宽时（出库页通常不填、联想也可能没带出），
                # 回退到物料档案里的长宽再算一次 —— 否则出库单的平米/卷料
                # 永远是空的，流水页里进有出没有，对不上账。
                if sqm is None and rolls is None and ('sqm' in fids or 'rolls' in fids):
                    _mrow = db.q("SELECT spec,width FROM materials WHERE id=?", mid)
                    if _mrow and (_mrow[0]['spec'] or _mrow[0]['width']):
                        # 注意用 or 而不是 setdefault：表单里这两列往往存在
                        # 但值是空串，setdefault 不会覆盖空串，照样算不出来
                        _v2 = dict(vals)
                        _v2['spec'] = (vals.get('spec') or '').strip() or _mrow[0]['spec']
                        _v2['width'] = (vals.get('width') or '').strip() or _mrow[0]['width']
                        _v2['qty'] = qty
                        _v2['qty_unit'] = qu
                        sqm, rolls, rest = db.calc_area(_v2)
                        if rest:
                            note_rest = db.rest_note(rest)
                newmat += is_new
                d = safe_date(request.form.get(f'tdate_{i}'), dflt)
                kind = fixed or (request.form.get(f'kind_{i}') or '进')
                note = (request.form.get(f'note_{i}') or '').strip()
                if sqm is not None and 'sqm' in fids:
                    vals['sqm'] = sqm
                if rolls is not None and 'rolls' in fids:
                    vals['rolls'] = rolls
                if rest and note_rest and note_rest not in note:
                    note = (note + ' ' if note else '') + note_rest
                if kind == '出':                      # 出库不允许超出现有库存
                    stock = scalar("SELECT stock FROM v_stock WHERE id=?", mid, default=0)
                    if qty > stock + 1e-9:
                        blocked += 1
                        names.append('%s(可用%g)' % (vals['name'] or vals['code'], stock))
                        continue
                # 单价不在录单页填 —— 入库成本来自采购单，采购到货时写入
                # 批次：使用者填了就用（去空格），没填留空，流水页按物料价回退
                _bt = (request.form.get('batch_%d' % i) or '').strip()[:40]
                _cx.execute("INSERT INTO txns(tdate,material_id,kind,qty,pieces,per_piece,"
                       "note,created_at,sqm,rolls,tpl_id,qty_unit,batch) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (d, mid, kind, qty, pieces, per, note,
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        sqm, rolls, tid, qu, _bt))
                _tid_new = _cx.execute("SELECT last_insert_rowid()").fetchone()[0]
                # v3.35 一份数据两处落：库存记 txns（上面），采购记 po_receipts（这里）。
                # 只按批次号桥接，两边不做外键绑定，删哪边都不牵连另一边。
                # v3.39：只有「进」才计入采购到货。领料出库时仓管同样会在批次框里
                # 选批次号（领料单必须注明从哪批领的，这是批次追溯的基础），
                # v3.35 漏了方向判断，出库越记到货数越高 —— 供应商还欠着货，
                # 采购单却已被推成「已完成」，财务照着付全款就是真金白银的损失。
                if kind == '进' and (request.form.get(f'to_po_{i}') or '1') == '1':
                    _ok, _pt = sync_txn_to_po(
                        _bt, vals.get('name') or '', mid, qty,
                        request.form.get('price_%d' % i), d, note, _tid_new,
                        # v3.42：带上这一笔的数量口径（按平米还是按卷），
                        # 采购侧才知道该不该除换算率，否则「按卷」录入会少记 conv 倍
                        qty_unit=qu)
                else:
                    _ok, _pt = False, ''
                if _pt:
                    po_tips.append(_pt)
                # 自定义列：文本/数字/日期/单选 直接存；
                # 公式列**不信前端**，后端按公式重算一遍，防手改表单提交假数
                _xv = {}
                for _xc in db.tpl_custom_cols(tid):
                    _k = _xc['fid']
                    _raw = (request.form.get(f'x_{i}_{_k}') or '').strip()
                    if (_xc['xtype'] or 'text') == 'calc':
                        _env = {'qty': qty, 'pieces': pieces or 0, 'per': per or 0,
                                'spec': num(request.form.get(f'spec_{i}')),
                                'width': num(request.form.get(f'width_{i}')),
                                'sqm': sqm or 0, 'rolls': rolls or 0,
                                'opening': num(request.form.get(f'opening_{i}')),
                                'safety': num(request.form.get(f'safety_{i}'))}
                        for _xc2 in db.tpl_custom_cols(tid):
                            if (_xc2['xtype'] or 'text') == 'calc':
                                continue
                            _r2 = (request.form.get(f'x_{i}_{_xc2["fid"]}') or '').strip()
                            try:
                                _v2 = float(_r2) if _r2 else 0.0
                            except ValueError:
                                _v2 = 0.0
                            _env[_xc2['fid']] = _v2
                            # 公式里也可以直接写列名（如 qty*单价），更好记
                            if _xc2['label']:
                                _env[_xc2['label']] = _v2
                        _got = db.calc_formula(_xc['xform'], _env)
                        _xv[_k] = '' if _got is None else _got
                    else:
                        _xv[_k] = _raw
                if _xv:
                    import json as _json
                    _cx.execute("UPDATE txns SET extra=? WHERE id=?",
                                (_json.dumps(_xv, ensure_ascii=False), _tid_new))
                saved += 1
        except Exception:
            # 不能静默吞掉：窗口模式没有控制台，不落盘就永远查不到原因
            _log_err('录单保存失败')
            return render_template('error.html', code=500, title='保存失败',
                detail='这批单据一条都没保存（已回滚），请返回重试。'
                       '错误详情已写入 warehouse.log。'), 500
        back = 'out' if ep == 'out' else ('in' if ep == 'in' else 'txn')
        if saved:
            tip = f'已保存 {saved} 条{"出库" if fixed=="出" else ("入库" if fixed=="进" else "")}单' \
                  + (f'，新建物料 {newmat} 种' if newmat else '')
        elif blocked:
            tip = f'一条都没保存：{blocked} 行超出库存（{"、".join(names[:3])}）'
        else:
            # 一条没存又不是超库存 —— 说明用户没填必填项，得说清楚，别只报 0
            tip = '一条都没保存：每行都要填「物料名称」和「数量」，数量要大于 0'
        if saved and blocked:
            tip += '；%d 行因超出库存未保存：%s' % (blocked, '、'.join(names[:3]))
        if po_tips:
            tip += '；' + '；'.join(po_tips[:3])
        # 连续录入是主场景：默认留在录入页，省得反复点回来
        if request.form.get('goto_txn'):
            return redirect(url_for('txns', msg=tip, kind=fixed or '', tpl=tid))
        # 保存成功 → 回到默认 1 张（这一批已经录完了，下一笔是新的）；
        # 一条没存 → 保留行数，用户填的内容还得修，打回 1 张等于白填。
        _n_back = None if saved else request.form.get('nrow')
        return redirect(url_for(back, msg=tip, n=_n_back, tpl=tid))

    # 默认只给一张卡片：多数时候就是录一笔，需要多行时自己点「＋ 加一行」。
    n = min(int_arg(request.args, 'n', default=1, lo=1), MAX_ROWS)
    return render_template('txn.html', cols=cols, fids=fids, mats=mats, msg=msg,
                           fixed=fixed, ep=ep, only_stock=only_stock,
                           allm=request.args.get('allm') == '1',
                           tdate=today(), rows=range(n), n=n, tid=tid,
                           TPLS=db.tpls(),
                           # sqlite3.Row 不能直接 tojson，前端只需要 fid/label 两列
                           col_defs=_sch['cols'],
                           xcols=_sch['xcols'],
                           js_mats=js_mats_with_price(mats),
                           # v3.35 批次候选：录单时下拉能选到采购到货登记过的批次
                           batches=_batch_choices(),
                           # 启用了平米/卷料列才显示「数量按 平米/卷」的口径选择
                           cols_has_area=_sch['has_area'])

def _batch_choices(limit=300):
    """录入页的批次候选：**未结单**的采购明细批次号。

    为什么改查 po_items（而不是到货登记表 po_receipts）：
    v3.30 起采购与库存解耦、v3.35 起到货按入库录入算，批次号在建单时就
    生成在 po_items 上了。第一次入库时 po_receipts 还是空的 —— 照旧查它，
    使用者在最需要联想的时候（刚建完单、还没到货）反而一个候选都看不到。

    只要「还没交齐」的单据：订 10 已到 10 的批次再选就没意义了，
    留着只会让人挑花眼、还可能重复入库。
    """
    try:
        rows = db.q(
            "SELECT i.batch, i.name, i.spec, i.unit, i.conv,"
            "       i.stock_unit, i.qty, i.recv_qty, i.material_id,"
            "       p.supplier, p.pono, p.status"
            " FROM po_items i JOIN pos p ON p.id=i.po_id"
            " WHERE COALESCE(i.batch,'')<>''"
            "   AND COALESCE(i.recv_qty,0) < i.qty - 1e-9"      # 未交齐
            "   AND p.status NOT IN ('已取消','草稿')"
            " ORDER BY p.odate DESC, i.id DESC LIMIT ?", limit)
        out = []
        for r in rows:
            left = (r['qty'] or 0) - (r['recv_qty'] or 0)
            if left <= 1e-9:
                continue
            out.append({
                'b': r['batch'],
                'n': r['name'],
                'sp': r['spec'] or '',
                'un': r['unit'] or '',
                'su': r['stock_unit'] or '',
                'cv': r['conv'] or 1,
                'mid': r['material_id'] or 0,
                'sup': r['supplier'] or '',
                'po': r['pono'] or '',
                'st': r['status'] or '',
                'left': round(left, 4),
                'ord': r['qty'] or 0,
            })
        return out
    except Exception:
        return []


def _apply_po_price(rows, mapped_only=0):
    """把采购到货实价映射到流水单据上，重算单价和金额。

    为什么是"采购价优先"：入库时随手填的单价（甚至空着）不能代表真实成本，
    真正付出去的钱记在采购到货上。映射上了就用采购价，
    没采购记录的（比如自产、调拨）才回落到单据自己填的价。

    返回 (新行列表, 命中条数)。行转成 dict 是为了加 price_src 标记，
    模板里按属性取值不受影响。
    """
    try:
        pmap = price_map()
    except Exception:
        _log_err('流水页采购价映射失败')
        return rows, 0
    if not pmap or len(pmap) <= 1:     # 只有 __mid__ 说明一张采购单都没有
        return rows, 0
    out = []
    hit = 0
    for r in rows:
        d = dict(r)
        p, src = txn_unit_price(
            pmap, d.get('mid') or d.get('material_id'),
            d.get('name'), d.get('spec'), d.get('unit'), d.get('supplier'),
            d.get('batch'))
        d['self_price'] = d.get('price')      # 单据自己填的，留作对照
        if p and p > 0:
            d['price'] = round(p, 6)
            d['amount'] = round(float(d.get('qty') or 0) * p, 2)
            d['price_src'] = src              # batch=批次命中, mat=物料最新价
            hit += 1
        else:
            d['price_src'] = '' if d.get('price') else 'none'
        out.append(d)
    return out, hit


def _extra_map(rows):
    """{单据id: {自定义列fid: 值}} —— 流水页显示自定义列用"""
    import json as _json
    m = {}
    for r in rows or []:
        raw = None
        try:
            raw = r['extra'] if 'extra' in r.keys() else None
        except (IndexError, TypeError, KeyError):
            raw = None
        if not raw:
            continue
        try:
            m[r['id']] = _json.loads(raw) or {}
        except ValueError:
            _log_err('流水页解析自定义列失败 txn id=%s' % r['id'])
    return m


@bp.route('/out/list')
def out_list():
    """历史出库流水"""
    return _kind_list('出')

@bp.route('/in/list')
def in_list():
    return _kind_list('进')

def _kind_list(kind):
    d = request.args.get('d') or ''
    m = request.args.get('m') or ''
    sql = ("SELECT t.*, m.name, m.unit, m.code, m.spec, m.supplier, m.id AS mid,"
           " %s AS amount FROM txns t"
           " JOIN materials m ON m.id=t.material_id WHERE t.kind=?" % AMT)
    args = [kind]
    if d:
        sql += " AND t.tdate=?"; args.append(d)
    elif m:
        sql += " AND t.tdate LIKE ?"; args.append(m + '%')
    sql += " ORDER BY t.tdate DESC, t.id DESC LIMIT 300"
    tot = db.q("SELECT COALESCE(SUM(qty),0) s, COUNT(*) c FROM txns WHERE kind=?" +
               (" AND tdate LIKE ?" if m else ""), *([kind, m + '%'] if m else [kind]))[0]
    rows = db.q(sql, *args)
    rows, mapped_n = _apply_po_price(rows)
    return render_template('txns.html', rows=rows, d=d, m=m, kind=kind,
                           total=tot['s'], count=tot['c'], url_kind='out_list' if kind == '出' else 'in_list',
                           mapped_n=mapped_n,
                           XC=_all_custom_cols(), XVAL=_extra_map(rows))


# ---------- 流水（出入库单据）Excel / WPS 导入 ----------
@bp.route('/txn/import', methods=['GET', 'POST'])
@bp.route('/in/import', methods=['GET', 'POST'], endpoint='in_import')
@bp.route('/out/import', methods=['GET', 'POST'], endpoint='out_import')
def txn_import():
    ep = request.endpoint
    fixed = '出' if ep == 'out_import' else ('进' if ep == 'in_import' else None)
    back = 'out' if fixed == '出' else ('in' if fixed == '进' else 'txn')
    # 导入也能选模板：导进来的物料/单据归到选中的模板
    tid = int_arg(request.values, 'tpl') or db.default_tpl_id()
    if not db.tpl(tid):
        tid = db.default_tpl_id()

    if request.method == 'POST':
        mode = request.form.get('mode', 'merge')
        if request.form.get('confirm') == '1':
            if not take_nonce(request.form.get('_n')):
                return redirect(url_for(back,
                    msg='这批单据已经导入过了，请不要重复提交'))
            p = os.path.join(TMP, os.path.basename(request.form.get('f', '')))
            if not os.path.exists(p):
                return render_template('txn_import.html', fixed=fixed, TPLS=db.tpls(), tid=tid, ep=ep,
                                       err='预览已过期，请重新选择文件')
            # v3.53：导入的「进」是否计入采购到货。
            # 退料/调拨/盘盈同样带批次号，照记会把供应商到货数越滚越高，
            # 采购单提前结单、财务按虚高的到货数付全款。默认「否」——
            # 导入来源复杂（一张 Excel 常混着入库和退料），宁可不记也不能错记；
            # 确属供应商到货的，导入时显式选「是」即可。
            _imp_to_po = (request.form.get('to_po') or '0') == '1'
            if request.form.get('manual') == '1':
                colmap = {}
                for k in request.form.keys():
                    if k.startswith('cm_'):
                        v = request.form.get(k)
                        if v:
                            colmap[k[3:]] = v
                try:
                    start = int_arg(request.form, 'startrow', default=1, lo=1)
                except ValueError:
                    start = 1
                rows = importer.parse_txn_by_map(
                    path=p, colmap=colmap, start=start, default_kind=fixed,
                    defdate=(request.form.get('defdate') or '').strip() or today())
            else:
                rows, _, _, _ = importer.parse_txn_file(
                    path=p, default_kind=fixed or (request.form.get('defkind') or None),
                    defmonth=(request.form.get('defdate') or '')[:7],
                    mat_aliases=db.aliases_map(tid), xcols=db.tpl_custom_cols(tid))
            dflt_date = (request.form.get('defdate') or '').strip() or today()
            allow_over = request.form.get('allow_over') == '1'
            fields = set((request.form.get('fields') or '').split(','))
            has_split = 'in_qty' in fields or 'out_qty' in fields
            # 宽表（物料×每日进出）：方向由表里的"进/出"行决定，不能跟着页面走
            is_wide = request.form.get('wide') == '1'
            # 表里有 进/出 分列时以表格方向为准；入库页只留"进"、出库页只留"出"
            filter_kind = None
            if not is_wide:
                if fixed == '进' and 'in_qty' in fields:
                    filter_kind = '进'
                elif fixed == '出' and 'out_qty' in fields:
                    filter_kind = '出'
            saved = newmat = blocked = skipped = archived = new_miss = 0
            po_tips = set()   # v3.35 导入里同步到采购流水的回执（去重）
            n_in = n_out = 0
            msgs = []
            # 按 日期→进先出后 排序后再写入。不排的话，同一物料"先出后进"
            # 的历史行会在写入当时因库存为 0 被当成超库存拦掉，
            # 导入整月流水时莫名其妙少几笔。
            def _ord(d):
                return (str(d.get('date') or '9999-99-99'),
                        0 if d.get('kind') == '进' else 1)
            try:
                rows = sorted(rows, key=_ord)
            except Exception:
                pass
            # 整批导入放进一个事务：中途任何异常全回滚，绝不留下"导了一半"的数据
            try:
              with db.tx():
                for d in rows:
                  vals = {k: d.get(k, '') for k in
                          ('name', 'code', 'supplier', 'category', 'spec', 'width', 'unit', 'status')}
                  vals['opening'] = d.get('opening', 0)
                  vals['safety'] = d.get('safety', 0)
                  mid, is_new = _resolve_material(vals, tpl_id=tid)
                  if not mid:
                      continue
                  newmat += is_new
                  _fids = [x['fid'] for x in db.tpl_cols(tid)]
                  if is_new and not (vals.get('code') or '').strip() \
                          and not (vals.get('category') or '').strip():
                      new_miss += 1
                  if not d.get('kind'):          # 宽表里没进出的行：只建档/更新期初
                      if mode == 'overwrite' or is_new:
                          db.run("UPDATE materials SET opening=? WHERE id=?",
                                 float(d.get('opening') or 0), mid)
                      archived += 1
                      continue
                  if is_wide or has_split:      # 表格自带方向，以表格为准
                      kind = d.get('kind') or '进'
                  else:
                      kind = fixed or d.get('kind') or '进'
                  qty = float(d.get('qty') or 0)
                  if filter_kind and kind != filter_kind:
                      skipped += 1
                      continue
                  if kind == '出' and not allow_over:
                      stock = scalar("SELECT stock FROM v_stock WHERE id=?", mid, default=0)
                      if qty > stock + 1e-9:
                          blocked += 1
                          msgs.append('%s(可用%g)' % (d.get('name'), stock))
                          continue
                  qty, pc, per = calc_qty(qty, d.get('pieces'), d.get('per_piece'))
                  if qty <= 0:
                      continue
                  # 平米 / 卷料：导入也得起作用，否则 Excel 导进来的单子这两列
                  # 永远空着，跟手工录入的对不上（进有出没有、手工有导入没有）。
                  # 优先采信表里直接给的值；只给了长宽就按长宽算；都没有就回退
                  # 物料档案的长宽。
                  _sq = num(d.get('sqm')) or None
                  _rl = num(d.get('rolls')) or None
                  # 先初始化，别等到下面的 if 里才定义 —— INSERT 时要读它，
                  # 若那分支没进（表里直接给了平米/卷料，或没启用这两列），
                  # 就会 NameError，整批导入静默回滚。
                  _vv = dict(d); _vv['qty'] = qty
                  _qu = str(d.get('qty_unit') or '').strip()
                  if _qu not in db.QTY_UNITS:
                      _mr = db.q("SELECT qty_unit FROM materials WHERE id=?", mid)
                      _qu = ((_mr[0]['qty_unit'] or '').strip() if _mr else '') or '平米'
                  _vv['qty_unit'] = _qu
                  if (_sq is None or _rl is None) and ('sqm' in _fids or 'rolls' in _fids):
                      # v3.28：只给了平米就补算卷料，只给了卷料就补算平米
                      if _sq is not None: _vv['sqm'] = _sq
                      if _rl is not None: _vv['rolls'] = _rl
                      _vv['qty'] = qty
                      _vv['qty_unit'] = _qu
                      if not (str(_vv.get('spec') or '').strip()
                              and str(_vv.get('width') or '').strip()):
                          _mr = db.q("SELECT spec,width FROM materials WHERE id=?", mid)
                          if _mr:
                              _vv['spec'] = _mr[0]['spec']
                              _vv['width'] = _mr[0]['width']
                      _sq2, _rl2, _rest = db.calc_area(_vv)
                      # 只补表里没给的那个：表里写了的以表为准，
                      # 不能因为算不出来（比如表里没长宽）就把已有值清成 None
                      if _sq is None and _sq2 is not None: _sq = _sq2
                      if _rl is None and _rl2 is not None: _rl = _rl2
                      if _rest:
                          _nt = db.rest_note(_rest)
                          if _nt and _nt not in (d.get('note') or ''):
                              d['note'] = ((d.get('note') or '') + ' ' + _nt).strip()
                  db.run("INSERT INTO txns(tdate,material_id,kind,qty,pieces,per_piece,price,"
                         "note,created_at,tpl_id,sqm,rolls,qty_unit,batch) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         d.get('date') or dflt_date, mid, kind, qty, pc, per,
                         (num(d.get('price')) or None), d.get('note', ''),
                         datetime.now().strftime('%Y-%m-%d %H:%M:%S'), tid, _sq, _rl,
                         _vv.get('qty_unit') or '',
                         # 导入的批次：表里写了就带上，这样也能对上采购批次价
                         str(d.get('batch') or '').strip()[:40])
                  _new_id = scalar("SELECT last_insert_rowid()")
                  # v3.35 导入同样一分为二：库存 + 采购流水（按批次号桥接）
                  # v3.53 加开关：退料/调拨/盘盈默认不计入，避免虚增到货
                  if kind == '进' and _imp_to_po and str(d.get('batch') or '').strip():
                      _ok2, _pt2 = sync_txn_to_po(
                          str(d.get('batch') or '').strip(),
                          d.get('name') or '', mid, qty, d.get('price'),
                          d.get('date') or dflt_date, d.get('note', ''), _new_id,
                          # v3.42：导入同样要带口径，理由同上
                          qty_unit=(_vv.get('qty_unit') or d.get('qty_unit') or ''))
                      if _pt2:
                          po_tips.add(_pt2)
                  # Excel 里列名能对上自定义列的，一并存进 extra
                  _xv = {}
                  for _xc in db.tpl_custom_cols(tid):
                      if (_xc['xtype'] or 'text') == 'calc':
                          continue      # 公式列后端算，不认表格里的值
                      # 自定义列也可能配了单位，界面上显示成「膜厚（丝）」，
                      # 使用者照抄过来就是这个写法 —— 得认，否则这一列静默丢失。
                      _lb = _xc['label'] or ''
                      # sqlite3.Row 没有 .get()，只能用 keys() 判断（踩过一次：
                      # 写成 _xc.get('unit') 直接抛异常，整批导入回滚）
                      _u = ((_xc['unit'] if 'unit' in _xc.keys() else '') or '').strip()
                      _cands = [_xc['fid'], _lb]
                      if _lb and _u:
                          _cands += ['%s（%s）' % (_lb, _u), '%s(%s)' % (_lb, _u)]
                      _v = ''
                      for _c in _cands:
                          if d.get(_c) not in ('', None):
                              _v = d.get(_c); break
                      if _v in ('', None):
                          # 兜底：表头带括号（含手写空格变体）时去括号再比一次
                          import re as _re2
                          _want = _re2.sub(r'\(.*?\)', '', _lb).strip()
                          for _k, _val in d.items():
                              if _val in ('', None):
                                  continue
                              if _re2.sub(r'\(.*?\)', '', str(_k)).strip() == _want:
                                  _v = _val; break
                      if _v not in ('', None):
                          _xv[_xc['fid']] = _v
                  if _xv:
                      import json as _json
                      db.run("UPDATE txns SET extra=? WHERE id=?",
                             _json.dumps(_xv, ensure_ascii=False), _new_id)
                  saved += 1
                  if kind == '进': n_in += 1
                  else: n_out += 1
            except Exception as e:
                try: os.remove(p)
                except OSError: pass
                return render_template('txn_import.html', fixed=fixed, TPLS=db.tpls(), tid=tid, ep=ep,
                    err='导入失败，已全部回滚（数据未改动）：%s' % e)
            try: os.remove(p)
            except OSError: pass
            # 宽表会把"进""出"一起导进来，提示必须写明构成，
            # 否则用户看到"已导入 8 条入库单据"，实际却是 4 进 4 出。
            if n_in and n_out:
                tip = f'已导入 {saved} 条单据（进 {n_in} / 出 {n_out}）'
            elif n_out:
                tip = f'已导入 {saved} 条出库单据'
            else:
                tip = f'已导入 {saved} 条入库单据'
            if newmat:
                tip += f'，新建物料 {newmat} 种'
                # 只统计"本次新建"里字段不全的，别拿全库说事
                if new_miss:
                    tip += f'（其中 {new_miss} 种没认出料号/类型：'
                    tip += '宽表前几列若没写表头就识别不出来' if is_wide else '表里缺这几列'
                    tip += '，可在「物料」页补全或改用「手动指定列」）'
            if po_tips:
                tip += '；' + '；'.join(list(po_tips)[:3])
            if blocked:
                tip += f'；{blocked} 行超出库存被跳过：' + '、'.join(msgs[:3])
                # 导入历史流水时，期初没填会让早期的出库全部被拦。
                # 光报"超出库存"用户不知道怎么办，必须给出路。
                tip += '。这些行的出库时间早于入库（或期初未填）——' \
                       '请先补期初结存，或勾选「允许超出库存」重导'
            if skipped:
                tip += f'（忽略 {skipped} 笔{"出库" if filter_kind=="进" else "入库"}行）'
            if archived:
                tip += f'；{archived} 行没填数量，只更新了物料档案'
            return redirect(url_for(back, msg=tip))

        # 手动指定列：用户自己挑哪列是什么，绕过表头识别
        if request.form.get('manual') == '1':
            p = os.path.join(TMP, os.path.basename(request.form.get('f', '')))
            if not os.path.exists(p):
                return render_template('txn_import.html', fixed=fixed, TPLS=db.tpls(), tid=tid, ep=ep,
                                       err='预览已过期，请重新选择文件')
            colmap = {}
            for k in request.form.keys():
                if k.startswith('cm_'):
                    v = request.form.get(k)
                    if v:
                        colmap[k[3:]] = v
            try:
                start = int_arg(request.form, 'startrow', default=1, lo=1)
            except ValueError:
                start = 1
            if 'name' not in colmap.values() and 'code' not in colmap.values():
                return render_template('txn_import.html', fixed=fixed, TPLS=db.tpls(), tid=tid, ep=ep,
                                       err='至少要指定一列是「物料名称」或「料号」')
            try:
                rows = importer.parse_txn_by_map(
                    path=p, colmap=colmap, start=start, default_kind=fixed,
                    defdate=request.form.get('defdate') or today())
            except Exception as e:
                return render_template('txn_import.html', fixed=fixed, TPLS=db.tpls(), tid=tid, ep=ep, err='解析失败：%s' % e)
            if not rows:
                return render_template('txn_import.html', fixed=fixed, TPLS=db.tpls(), tid=tid, ep=ep,
                                       err='按你指定的列没读到数据，检查一下起始行是不是选错了')
            fields = sorted(set(colmap.values()))
            return render_template('txn_import.html', fixed=fixed, TPLS=db.tpls(), tid=tid, ep=ep, preview=rows[:60],
                                   total=len(rows), fields=fields,
                                   fields_str=','.join(fields),
                                   f=os.path.basename(p),
                                   manual=1, startrow=start,
                                   colmap=colmap,
                                   defkind=fixed or '',
                                   defdate=request.form.get('defdate') or today())

        f = request.files.get('file')
        if not f or not f.filename:
            return render_template('txn_import.html', fixed=fixed, TPLS=db.tpls(), tid=tid, ep=ep, err='请选择文件')
        fn = safe_name(f.filename, 'x.xlsx').lower()
        if not fn.endswith(('.xlsx', '.xlsm', '.xls', '.et', '.csv')):
            return render_template('txn_import.html', fixed=fixed, TPLS=db.tpls(), tid=tid, ep=ep,
                                   err='只支持 .xlsx / .xlsm / .xls / .et / .csv')
        # 先看大小再落盘：超大文件直接拒，别把磁盘写满
        f.seek(0, os.SEEK_END); size = f.tell(); f.seek(0)
        if size > MAX_UPLOAD:
            return render_template('txn_import.html', fixed=fixed, TPLS=db.tpls(), tid=tid, ep=ep,
                                   err='文件太大（%.1f MB），上限 %d MB。'
                                       '请拆分后再导入。' % (size / 1048576.0, MAX_UPLOAD // 1048576))
        tmp = os.path.join(TMP, '%d_%s' % (int(time.time() * 1000), fn))
        try:
            f.save(tmp)
        except (ValueError, OSError):
            return render_template('txn_import.html', fixed=fixed, TPLS=db.tpls(), tid=tid, ep=ep,
                                   err='文件名不合法，请改成普通中文/英数字再试')
        try:
            rows, fields, hi, diag = importer.parse_txn_file(
                path=tmp, default_kind=fixed or (request.form.get('defkind') or None),
                defmonth=(request.form.get('defdate') or '')[:7],
                mat_aliases=db.aliases_map(tid), xcols=db.tpl_custom_cols(tid))
        except Exception as e:
            return render_template('txn_import.html', fixed=fixed, TPLS=db.tpls(), tid=tid, ep=ep, err='解析失败：%s' % e)
        # 列名指纹：拿原始表头算，列名一样就自动归到同一个模板
        try:
            _grid = importer._read_grid(path=tmp)
            _hdr = _grid[0] if _grid else []
        except Exception:
            _hdr = []
        _sig = db.head_sig(_hdr)
        _hit = db.tpl_by_sig(_sig)
        if _hit and (request.values.get('tpl') or 0) in ('', '0', None, 0):
            tid = _hit['id']          # 认出是同一张表，自动选中
        if not rows:
            why = (diag or {}).get('why')
            if why == 'no-header':
                err = ('没认出表头。表格第一行要写列名，'
                       '至少需要「物料名称（或料号）」，'
                       '以及「数量」/「进」「出」之一。')
            elif why == 'no-data':
                err = '表头认出来了，但下面没有有效数据行（物料名和数量都要填）。'
            else:
                err = '没读到有效单据。'
            grid = (diag or {}).get('grid') or []
            ncol = max([len(r) for r in grid] or [0])
            return render_template('txn_import.html', fixed=fixed, TPLS=db.tpls(), tid=tid, ep=ep, err=err, diag=diag,
                                   grid=grid, ncol=range(ncol),
                                   f=os.path.basename(tmp),
                                   defkind=request.form.get('defkind') or '',
                                   defdate=request.form.get('defdate') or today())
        return render_template('txn_import.html', fixed=fixed, TPLS=db.tpls(), tid=tid, ep=ep, preview=rows[:60],
                               total=len(rows), fields=sorted(fields),
                               fields_str=','.join(sorted(fields)),
                               f=os.path.basename(tmp),
                               yms=(diag or {}).get('yms') or {},
                               skipped_sum=(diag or {}).get('skipped_sum') or 0,
                               defkind=request.form.get('defkind') or '',
                               defdate=request.form.get('defdate') or today(),
                               wide=(hi == -2), sig=_sig, hdr=_hdr,
                               sig_hit=(_hit['id'] if _hit else 0))
    return render_template('txn_import.html', fixed=fixed, TPLS=db.tpls(), tid=tid, ep=ep, defdate=today())

@bp.route('/txn/import/tpl')
def txn_import_tpl():
    import openpyxl
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = '出入库导入'
    head = ['日期', '物料名称', '料号', '类型', '规格', '宽幅', '供应商', '单位', '进', '出', '备注']
    ws.append(head)
    for r in db.q("SELECT id,name,code,category,spec,width,supplier,unit,opening FROM v_stock"
                  " WHERE active=1 ORDER BY category,name LIMIT 3"):
        ws.append([today(), r['name'], r['code'], r['category'], r['spec'], r['width'],
                   r['supplier'], r['unit'], '', '', ''])
    ws.append([today(), '（示例）半对半黑化压延', 'CPDR131218KAB1', '无胶压延', '12/20', '250',
               '松杨电子', '平米', 100, 30, '出柏威'])
    for i, w in enumerate([12, 24, 20, 14, 12, 8, 14, 8, 9, 9, 18], 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
    bio = io.BytesIO(); wb.save(bio)
    from urllib.parse import quote
    return Response(bio.getvalue(),
                    mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition':
                             "attachment; filename=tpl.xlsx; filename*=UTF-8''%s.xlsx" % quote('出入库导入模板')})


def _unlink_po_receipts(ids, c):
    """删出入库单前，先把这批单据挂在采购上的到货记录撤掉。

    v3.35 起「到货按入库录入算」：入库单上填了批次号，就会在 po_receipts
    生成一条带 txn_id 的到货记录。外键 txn_id -> txns(id) 于是把这张入库单
    锁住了 —— 直接 DELETE 会抛 IntegrityError，页面上就是 500，删不掉。

    录错了想重录是每天都在做的事，必须能删。
    这里连同到货数一起回滚（recv_qty 减回、单头状态重算），
    否则会出现「入库单没了、采购账还记着到货」，后面重新入库时到货数翻倍，
    供应商欠着货却显示已完成、货款多付 —— 这正是 v3.39 修的那类问题。

    必须用调用方传进来的事务连接 c，别在循环里另开连接：
    嵌套写会提前提交，事务就散了。
    """
    ids = [int(x) for x in ids if str(x).strip().lstrip('-').isdigit()]
    if not ids:
        return 0
    ph = ','.join('?' * len(ids))
    # 列名必须带表别名：两张表都有 id，写裸 id 会 ambiguous column name
    rows = c.execute("SELECT r.id, r.item_id, r.qty, i.po_id FROM po_receipts r"
                     " JOIN po_items i ON i.id=r.item_id"
                     " WHERE r.txn_id IN (%s)" % ph, tuple(ids)).fetchall()
    if not rows:
        return 0
    po_ids = set()
    for r in rows:
        c.execute("UPDATE po_items SET recv_qty=MAX(0, COALESCE(recv_qty,0)-?)"
                  " WHERE id=?", (r['qty'] or 0, r['item_id']))
        po_ids.add(r['po_id'])
    c.execute("DELETE FROM po_receipts WHERE txn_id IN (%s)" % ph, tuple(ids))
    # 状态回到「已下单 / 部分到货」，不能还挂着「已完成」
    from ..po import logic as po_logic
    for pid in po_ids:
        try:
            st = po_logic.derive_status(pid)
            if st:
                c.execute("UPDATE pos SET status=? WHERE id=?", (st, pid))
        except Exception:
            pass
    return len(rows)


@bp.route('/txn/del/<int:tid>')
def txn_del(tid):
    """删一张单据。

    不能裸 DELETE：v3.35 起带批次的入库单会生成采购到货记录并外键引用它，
    直接删会 IntegrityError → 页面 500。必须先解掉采购侧的引用。
    """
    try:
        with db.tx() as c:
            _unlink_po_receipts([tid], c)
            c.execute("DELETE FROM txns WHERE id=?", (tid,))
    except Exception:
        return redirect((request.referrer or url_for('txns'))
                        + '?msg=' + quote('删除失败，已回滚，请重试'))
    return redirect(request.referrer or url_for('txns'))

@bp.route('/txns/batch', methods=['POST'])
def txns_batch():
    """流水批量删除：勾谁删谁，也可按当前筛选条件一键清空。
    整批放在一个事务里，中途出错全部回滚，不会删一半。"""
    ids = ints(request.form, 'id')
    clear = request.form.get('clear') == '1'
    back = request.form.get('back') or ''
    try:
        with db.tx() as c:
            if clear:
                # 按当前页面筛选条件删（日期 / 月份 / 类型 / 搜索词），
                # 与列表页看到的结果保持一致，避免"看到的和删掉的不是一批"
                d = (request.form.get('d') or '').strip()
                m = safe_ym(request.form.get('m')) if request.form.get('m') else ''
                kind = (request.form.get('kind') or '').strip()
                kw = clean_kw(request.form.get('kw'))
                w, args = [], []
                if d:
                    w.append("t.tdate=?"); args.append(d)
                elif m:
                    w.append("t.tdate LIKE ?"); args.append(m + '%')
                if kind in ('进', '出'):
                    w.append("t.kind=?"); args.append(kind)
                if kw:
                    w.append("(m.name LIKE ? OR m.code LIKE ? OR t.note LIKE ? OR m.category LIKE ?)")
                    args += ['%%%s%%' % kw] * 4
                if w:
                    sql = ("DELETE FROM txns WHERE id IN (SELECT t.id FROM txns t"
                           " JOIN materials m ON m.id=t.material_id WHERE " + " AND ".join(w) + ")")
                else:
                    sql = "DELETE FROM txns"
                # 安全闸门：列表只显示前 TXN_PAGE 条，但条件删除会删掉全部。
                # 必须显式传 real_total 授权，否则最多只删一页，杜绝"以为删500实际删2万"。
                try:
                    declared = int_arg(request.form, 'real_total')
                except ValueError:
                    declared = 0
                cur = c.execute("SELECT COUNT(*) FROM txns t JOIN materials m"
                                " ON m.id=t.material_id"
                                + (" WHERE " + " AND ".join(w) if w else ""),
                                tuple(args)).fetchone()[0]
                if declared != cur:
                    # 条件实际命中数与页面声明的不一致（数据已变化/参数被改），拒绝执行
                    raise ValueError('count-mismatch')
                if cur > TXN_PAGE:
                    raise ValueError('too-many')
                # 条件删除：先把命中这批单据的采购到货引用解掉（否则外键拦下 → 500）
                hit = [r[0] for r in c.execute(
                    "SELECT t.id FROM txns t JOIN materials m ON m.id=t.material_id"
                    + (" WHERE " + " AND ".join(w) if w else ""), tuple(args)).fetchall()]
                _unlink_po_receipts(hit, c)
                cur = c.execute(sql, tuple(args))
                n = cur.rowcount if cur.rowcount and cur.rowcount > 0 else declared
            else:
                if not ids:
                    return redirect((back or url_for('txns')) + '?msg=' + quote('未勾选任何单据'))
                _unlink_po_receipts(ids, c)
                ph = ','.join('?' * len(ids))
                c.execute("DELETE FROM txns WHERE id IN (%s)" % ph, tuple(ids))
                n = len(ids)
    except ValueError as ex:
        msg = ('删除已取消：要删的数量超过一页上限 %d 条。'
               '请先按日期或月份缩小范围，再清空。' % TXN_PAGE) if str(ex) == 'too-many' \
            else '删除已取消：数据量与页面不符，请刷新页面后重试。'
        return redirect((back or url_for('txns')) + '?msg=' + quote(msg))
    except Exception:
        return redirect((back or url_for('txns')) + '?msg=' + quote('删除失败，已回滚，请重试'))
    return redirect((back or url_for('txns')) + '?msg=' + quote('已删除 %d 条单据' % n))

@bp.route('/txns')
def txns():
    d = request.args.get('d') or ''
    # 月份筛选曾经是死的：模板上有「或按月份」输入框，路由却根本不读 m，
    # 选了月份照样显示全量、还不给任何提示。既然给了框就得生效。
    m = util.opt_ym(request.args.get('m'))
    kind = request.args.get('kind') or ''
    kw = clean_kw(request.args.get('kw'))
    sort = request.args.get('sort') or ''
    dir_ = request.args.get('dir') or ''
    # t.* 已含 txns.extra（自定义列的值）
    sql = ("SELECT t.*, m.name, m.unit, m.code, m.category, %s AS amount FROM txns t"
           " JOIN materials m ON m.id=t.material_id" % AMT)
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
    if w:
        sql += " WHERE " + " AND ".join(w)
    allowed = {'tdate': 't.tdate', 'name': 'm.name', 'qty': 't.qty', 'kind': 't.kind',
               'pieces': 'COALESCE(t.pieces,0)', 'note': 't.note',
               'price': 'COALESCE(t.price,0)', 'amount': AMT}
    if sort in allowed and dir_:
        sql += " ORDER BY %s %s, t.id DESC" % (allowed[sort], 'ASC' if dir_ == 'asc' else 'DESC')
    else:
        sql += " ORDER BY t.tdate DESC, t.id DESC"
    # 真实总数必须单独查：列表 LIMIT 500，用户只看到 500 条，
    # 但"清空当前筛选"删的是筛选条件的全部。若把 500 当成总数，
    # 用户以为删 500 条，实际可能删掉几万条 —— 这是灾难性误删。
    csql = "SELECT COUNT(*) FROM txns t JOIN materials m ON m.id=t.material_id"
    if w:
        csql += " WHERE " + " AND ".join(w)
    real_total = db.q(csql, *args)[0][0]

    sql += " LIMIT %d" % TXN_PAGE
    rows = db.q(sql, *args)
    rows, mapped_n = _apply_po_price(rows)
    return render_template('txns.html', rows=rows, d=d, kind=kind or None, m=m, kw=kw,
                           total=0, count=len(rows), real_total=real_total,
                           capped=(real_total > len(rows)),
                           url_kind='txns',
                           cur_sort=sort, cur_dir=dir_, qs={'d': d, 'kind': kind, 'kw': kw},
                           msg=request.args.get('msg', ''),
                           mapped_n=mapped_n,
                           XC=_all_custom_cols(), XVAL=_extra_map(rows))

