# -*- coding: utf-8 -*-
"""出入库模块（1/3）：单据录入

入库 / 出库 / 单据流水三张页面共用同一套录入逻辑，都在这里。
Excel 导入见 importer.py，列表与删除见 browse.py。
"""
from flask import request, redirect, url_for, Response, render_template, jsonify
from datetime import datetime, date
import calendar, io, csv, os, time, sys, json
from urllib.parse import quote
from . import bp
from ..core import db, util
from ..core.util import *            # noqa: F401,F403, int_arg
from ..core.util import (_log_err, _last_prices, js_mats_with_price)  # noqa: F401
from .. import importer
from ..table import tbl
# v3.104：出入库 ↔ 电子表格 桥梁（与采购同一套：映射记住、按批次号回写）
from ..sheet import bridge as BR
from ..sheet import batch as BT
from ..sheet import cols as CL
from ..sheet import colsapi

# 流水页 / 出入库列表要显示自定义列，这两个函数在表格模块里。
# 拆分时漏了这行，导致 /txns 直接 500 —— 名字带下划线是避免和 util 里的重名。
from ..table.helpers import all_custom_cols as _all_custom_cols, cell_val as _cell_val
from ..po.amount import price_map, txn_unit_price       # noqa: F401
from ..po.receive import sync_txn_to_po                 # noqa: F401


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
        _writes = []      # v3.104 待回写电子表格的行（事务提交成功后才写）
        try:
          with db.tx() as _cx:
            for i in range(nrow):
                qty = num(request.form.get(f'qty_{i}'), hi=QTY_MAX)
                # v3.106：数量列被屏蔽（不显示）时表单里根本没有这个框，
                # 直接用平米/卷料兜底，否则整笔被当成没填数量丢掉。
                if qty <= 0:
                    qty = (num(request.form.get(f'sqm_{i}'))
                           or num(request.form.get(f'rolls_{i}')) or 0)
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
                # v3.106：平米↔卷料 的换算交给表格写公式，表单不再代算。
                # 数量本身就是平米（采购按平米、入库按平米+卷），卷料是独立一列，
                # 使用者填了就记，没填就空着 —— 绝不反推，也绝不瞎填 0。
                sqm = rolls = rest = None; note_rest = ''
                rolls = num(request.form.get(f'rolls_{i}')) or None
                mid, is_new = _resolve_material(vals, sqm=sqm, rolls=rolls, tpl_id=tid,
                                                qty_unit=qu)
                if not mid:
                    continue
                # 录单页没填长宽时（出库页通常不填、联想也可能没带出），
                # 回退到物料档案里的长宽再算一次 —— 否则出库单的平米/卷料
                # 永远是空的，流水页里进有出没有，对不上账。

                newmat += is_new
                d = safe_date(request.form.get(f'tdate_{i}'), dflt)
                kind = fixed or (request.form.get(f'kind_{i}') or '进')
                note = (request.form.get(f'note_{i}') or '').strip()
                sqm = qty          # v3.106：数量 = 平米，同一个值不再摆两遍
                if 'sqm' in fids:
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
                # v3.104 回写表格用的单价/金额。录单页本来就不填单价（入库成本
                # 来自采购单），没填就留空 —— 写 0 进表格会让人以为这笔是白送的。
                _pr = (request.form.get('price_%d' % i) or '').strip()
                try:
                    _amt = round(num(_pr) * qty, 2) if _pr not in (None, '') else ''
                except Exception:
                    _amt = ''
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
                _writes.append((kind, _bt, {
                    '_tid': _tid_new,
                    'name': vals.get('name') or '',
                    'qty': qty, 'price': _pr, 'amount': _amt,
                    'spec': vals.get('spec') or '', 'width': vals.get('width') or '',
                    'sqm': sqm, 'rolls': rolls, 'unit': qu,
                    'tdate': d, 'supplier': vals.get('supplier') or '', 'note': note,
                }))
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
            # v3.104 事务已提交，这会儿才回写表格：单据存成功了才轮到表格
            _wm = _write_back(_writes)
            if _wm:
                tip += '；' + _wm
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
    # v3.106 表单字段 ≡ 可映射字段：fixed 是「进/出」，模块键是 in/out
    _mod = {'进': 'in', '出': 'out'}.get(fixed or '', 'in')
    return render_template('txn.html', cols=cols, fids=fids, mats=mats, msg=msg,
                           FLDS=CL.shown(_mod), MOD=_mod,
                           fixed=fixed, ep=ep, only_stock=only_stock,
                           allm=request.args.get('allm') == '1',
                           tdate=today(), rows=range(n), n=n, tid=tid,
                           TPLS=db.tpls(),
                           # sqlite3.Row 不能直接 tojson，前端只需要 fid/label 两列
                           col_defs=_sch['cols'],
                           xcols=_sch['xcols'],
                           js_mats=js_mats_with_price(mats),
                           # v3.35 批次候选：录单时下拉能选到采购到货登记过的批次
                           batches=_batch_choices(module=('out' if ep=='out' else 'in')),
                           # 启用了平米/卷料列才显示「数量按 平米/卷」的口径选择
                           cols_has_area=_sch['has_area'],
                           # v3.104 录入到电子表格：入库/出库各一套绑定
                           **_bind_ctx())

# --------------------------------------------------- 回写电子表格（v3.104）
def _cur_bind(module):
    """该模块当前要写入的表：优先上次绑的，没绑过就取最新一本工作簿的第一张表。

    与采购同一个道理 —— 出入库总得有个去处，每次都让用户先选一遍，
    是把「记住」该做的事推给了人。没建过任何工作簿时才返回 None。
    """
    try:
        b = BR.bind_get(module)
        if b:
            return b
        for bk in BR.books():
            shs = BR.sheets(bk['id'])
            if shs:
                return {'wb_id': bk['id'], 'sh_name': shs[0], 'mapping': {},
                        'module': module}
    except Exception:
        _log_err('取默认表格绑定失败 %s' % module)
    return None


def _bind_from_form(module):
    """表单里选的表；没传（页面上没这几项）就退回当前默认绑定。"""
    try:
        _bw = int(request.form.get('bind_wb_' + module) or 0)
    except (TypeError, ValueError):
        _bw = 0
    _bsh = (request.form.get('bind_sh_' + module) or '').strip()
    if _bw and _bsh:
        return _bw, _bsh
    b = _cur_bind(module)
    return (b['wb_id'], b['sh_name']) if b else (0, '')


def _write_back(rows):
    """把刚存下的出入库写进电子表格。

    rows 是 [(kind, batch, vals)]。入库写 'in' 那张表，出库写 'out' 那张表
    —— 混录时（/txn 通用页）一行也按自己的方向各归各表，不会串成一锅。

    为什么不塞进出入库的事务里：wb 表是另一份数据，绑在一起会让
    「单据回滚了表格却写上了」和「单据存了表格没写」两种错乱纠缠不清。
    表格写失败不该让单据跟着丢，所以放事务外、单独记日志。
    """
    mods = {}
    for kind, bt, vals in (rows or []):
        mods.setdefault('out' if kind == '出' else 'in', []).append([bt, vals])
    ok = 0
    for m, items in mods.items():
        _bw, _bsh = _bind_from_form(m)
        if not _bw or not _bsh:
            continue
        _mp = {}
        for _f, _lb, _d in BR.FIELDS.get(m, []):
            _v = request.form.get('map_%s_%s' % (m, _f))
            try:
                _mp[_f] = int(_v) if _v not in (None, '') else BR.SKIP
            except (TypeError, ValueError):
                _mp[_f] = BR.SKIP
        # 页面没展开映射卡片时一个 map_* 都没提交，这时按表头自动猜一套
        if all(v == BR.SKIP for v in _mp.values()):
            _mp = BR.auto_map(m, BR.heads(_bw, _bsh))
        if not BR.bind_set(m, _bw, _bsh, _mp):
            continue
        for _it in items:
            _bt, _vals = _it[0], dict(_it[1])
            _tid = _vals.pop('_tid', None)
            # 没填批次号的行自动补一个：表格按批次号定位行，没有号无从下笔。
            # 补完还要回写 txns，否则流水页是空的、表格里却有号，两边对不上账。
            if not (_bt or '').strip():
                try:
                    _bt = (BT.next_no(1) or [''])[0]
                except Exception:
                    _log_err('自动生成批次号失败')
                    continue
                if not _bt:
                    continue
                _it[0] = _bt
                if _tid:
                    try:
                        db.run("UPDATE txns SET batch=? WHERE id=?", _bt, _tid)
                    except Exception:
                        _log_err('批次号回写流水失败 id=%s' % _tid)
            try:
                _done, _why = BR.write(m, _bt, _vals)
                if _done:
                    ok += 1
            except Exception:
                _log_err('回写电子表格失败 %s' % _bt)
    return ('%d 行已录入表格' % ok) if ok else ''


def _bind_ctx():
    """给录入页准备的绑定上下文：入库 / 出库各一套。

    一本工作簿都没有时整套返回空，页面连卡片都不显示 —— 满页空下拉
    比没有这个入口更让人困惑。
    """
    ctx = {'BOOKS': [], 'BINDS': {}, 'SHS': {}, 'HEADS': {}, 'MAPF': {},
           'SHOWN': {}, 'SKIP': BR.SKIP}
    # v3.106：表单字段 ≡ 可映射的字段，屏蔽一列两处同时消失
    for _m in ('in', 'out'):
        ctx['SHOWN'][_m] = CL.shown(_m)
        _ss = {f for f, _ in ctx['SHOWN'][_m]}
        ctx['MAPF'][_m] = [f for f in BR.FIELDS.get(_m, []) if f[0] in _ss]
    try:
        ctx['BOOKS'] = BR.books()
    except Exception:
        _log_err('读取工作簿列表失败')
    for m in ('in', 'out'):
        ctx['BINDS'][m] = None
        ctx['SHS'][m] = []
        ctx['HEADS'][m] = []
        if not ctx['BOOKS']:
            continue
        b = _cur_bind(m)
        if not b:
            continue
        ctx['BINDS'][m] = b
        try:
            ctx['SHS'][m] = BR.sheets(b['wb_id'])
            ctx['HEADS'][m] = BR.heads(b['wb_id'], b['sh_name'])
        except Exception:
            _log_err('读取表格表头失败 %s' % m)
    return ctx


@bp.route('/txn/bind/sheets')
def txn_bind_sheets():
    """选了工作簿后，取它里面的工作表列表。"""
    try:
        wb_id = int(request.args.get('wb') or 0)
    except (TypeError, ValueError):
        wb_id = 0
    return jsonify({'sheets': BR.sheets(wb_id) if wb_id else []})


@bp.route('/txn/bind/heads')
def txn_bind_heads():
    """选了工作表后，取表头 + 自动猜的映射 + 这套表以前存过的映射。

    以前配过就用以前那套 —— 换表再换回来，映射还在，不用重配一遍。
    """
    m = (request.args.get('m') or 'in').strip()
    if m not in ('in', 'out'):
        m = 'in'
    try:
        wb_id = int(request.args.get('wb') or 0)
    except (TypeError, ValueError):
        wb_id = 0
    sh = (request.args.get('sh') or '').strip()
    heads = BR.heads(wb_id, sh) if (wb_id and sh) else []
    auto = BR.auto_map(m, heads)
    saved = {}
    if wb_id and sh:
        try:
            r = db.q("SELECT mapping FROM sheet_bind WHERE module=? AND wb_id=?"
                     " AND sh_name=?", m, wb_id, sh)
            if r:
                saved = BR._json(r[0]['mapping'])
        except Exception:
            pass
    return jsonify({'heads': heads, 'auto': auto, 'saved': saved})


def _batch_choices(limit=300, module='in'):
    """录入页的批次候选（v3.109）。

    **表格优先**：表格是主体，采购下单 / 入库 / 出库都往同一张表写，表里那
    一行就是这批货的档案。手填批次号单独入库的那些批次采购单里根本没有，
    只查采购单的话，最需要联想的时候反而一个候选都看不到。
    采购单只作为补充（表格里还没有的批次），两者按批次号去重。

    采购单那一路只要「还没交齐」的：订 10 已到 10 的批次再选就没意义了，
    留着只会让人挑花眼、还可能重复入库。
    """
    out = []
    seen = set()
    # 表格优先
    try:
        for r in BR.batch_rows(module, limit=limit):
            bn = (r.get('b') or '').strip()
            if not bn or bn in seen:
                continue
            seen.add(bn)
            out.append(r)
    except Exception:
        pass
    if len(out) >= limit:
        return out
    # 采购单补充
    for r in _po_batch_choices(limit=limit):
        bn = (r.get('b') or '').strip()
        if not bn or bn in seen:
            continue
        seen.add(bn)
        out.append(r)
    return out


def _po_batch_choices(limit=300):
    """**未结单**的采购明细批次号。"""
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
                'src': 'po',
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


# 显示哪些列：入库 / 出库共用一个端点，用 ?m=in|out 区分
# （v3.106 与采购共用同一套实现）
_cols_view = colsapi.register(bp, '/txn/cols', 'in', 'txn_cols_cfg')
