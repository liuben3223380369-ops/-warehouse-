# -*- coding: utf-8 -*-
"""采购单 · new

    新建采购单。明细固定六列（物料/规格/数量/单位/单价/小计），
    列不再由「采购模板」决定 —— 需要什么列，去制表模块把表头做成什么样，
    下单时映射到那张表即可。

    保存后按批次号回写到绑定的电子表格（v3.100 起为默认动作）。
"""
from flask import redirect, render_template, request, url_for, jsonify
from datetime import datetime
from .. import bp
from ...core import db
from ...core.util import (_log_err, is_date, num, safe_date, take_nonce, today)
from ...core.tpl import rest_note
from .. import amount, query, status, summary
from ...sheet import bridge as BR
from ...sheet import cols as CL
from ...sheet import colsapi


@bp.route('/po/new', methods=['GET', 'POST'])
def po_new():
    """新建采购单（含明细）"""
    if request.method == 'POST':
        return _po_new_post()
    return _po_new_get()


def _po_new_post():
    # 一次性令牌：挡住连点造成的重复建单
    if not take_nonce(request.form.get('_n')):
        return redirect(url_for('purchase_home',
            msg='这个单子已经建过了，请不要重复提交'))
    supplier = (request.form.get('supplier') or '').strip()
    odate = safe_date(request.form.get('odate'))
    if not supplier:
        return _form(err='供应商必填')

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
    # v3.103 卷料按「列」记：长 / 宽 / 平米 / 卷料。
    # v3.106 起不再双向推算，填什么写什么。
    widths = request.form.getlist('item_width')
    rollss = request.form.getlist('item_rolls')
    amounts = request.form.getlist('item_amount')
    # v3.106：明细字段统一按 fid 取。某一列被屏蔽（不显示）时列表会是空的，
    # 取不到就当空值，不能因为少了可选列就把整行丢掉。
    def _fv(fid, i):
        _l = request.form.getlist('item_' + fid)
        return (_l[i] if i < len(_l) else '') or ''

    def _nblank(lst, i, conv=num):
        """取第 i 项的数字；**没填就是 None，不是 0**。

        v3.108：没填和填 0 是两回事。`num('')` 返回 0，于是「卷料」栏留空时
        会往表格写 0 —— 使用者在那一列写了 `=平米/(长*宽)` 的公式，被 0 冲成
        死数字，表格从此不再计算，而且看不出是谁干的。
        返回 None 时 bridge 会判断目标格是不是公式，是就跳过。
        """
        raw = (lst[i] if i < len(lst) else '') or ''
        if isinstance(raw, str) and not raw.strip():
            return None
        try:
            return conv(raw)
        except Exception:
            return None
    _tid = db.default_tpl_id()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        with db.tx() as c:
            pono = status.next_pono(odate)
            po_id = c.execute("INSERT INTO pos(pono,supplier,odate,ddate,status,"
                              "tax_rate,price_tax,note,created_at,tpl_id,potpl_id)"
                              " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                              (pono, supplier, odate, ddate,
                               request.form.get('status') or '已下单',
                               tax, price_tax, note, now, _tid, 0)).lastrowid
            n = 0
            rows = []          # 回写电子表格用：(批次号, 字段值)
            for i in range(len(names)):
                nm = (names[i] or '').strip()
                q = num(qtys[i] if i < len(qtys) else 0)
                p = num(prices[i] if i < len(prices) else 0)
                if not nm:
                    continue
                mid = None
                if i < len(mids) and str(mids[i]).strip().isdigit():
                    mid = int(mids[i])
                _sp = (specs[i] if i < len(specs) else '').strip()
                _un = (units[i] if i < len(units) else '').strip() or '个'
                _cv = num(convs[i] if i < len(convs) else 1, default=1)
                if _cv <= 0:
                    _cv = 1.0
                _su = (sunits[i] if i < len(sunits) else '').strip()
                _bt = ((batches[i] if i < len(batches) else '') or '').strip()
                if not _bt:
                    _bt = _bt_seq(pono, n)
                # 卷料四列：长=_sp（沿用规格列）、宽、平米、卷料
                _wd = (widths[i] if i < len(widths) else '').strip()
                # v3.106：采购按平米 —— 数量本身就是平米，不再两个字段来回倒。
                # 平米↔卷料 的换算交给表格写公式，表单只原样搬运使用者填的值：
                # 表单代算会让「表格里的公式」和「单据里的数」变成两套口径，
                # 回写时还把公式冲成死数字。
                _sq = q
                # v3.108：没填 → None（不是 0）。0 会把使用者写在卷料列的公式冲掉。
                _ro = _nblank(rollss, i)
                _qu = '平米'
                if q <= 0:
                    continue
                c.execute("INSERT INTO po_items(po_id,material_id,name,spec,width,sqm,rolls,"
                          "qty_unit,unit,conv,stock_unit,qty,price,note,sig,batch,ptpl_id,extra)"
                          " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (po_id, mid, nm, _sp, _wd, _sq or None, _ro or None, _qu,
                           _un, _cv, _su, q, p, '',
                           # v3.34 明细指纹：流水页靠它把采购实价映射到单据上
                           amount.item_sig(nm, _sp, _un, supplier),
                           # v3.35 批次号：入库时填它就把这批货计入本单
                           _bt, 0, ''))
                rows.append((_bt, {
                    'name': nm, 'spec': _sp, 'width': _wd,
                    'sqm': _sq, 'rolls': _ro, 'qty_unit': _qu,
                    # v3.108：单价 / 换算率 / 单位 同样「没填就不写」。
                    # 这三个都有默认值（num('')→0、conv 默认 1、unit 默认「个」），
                    # 直接搬运会把表格里对应列的公式盖成默认值。
                    'qty': q, 'price': _nblank(prices, i),
                    'unit': (_un if (units[i] if i < len(units) else '').strip() else None),
                    'conv': _nblank(convs, i, lambda x: num(x, default=1) or 1.0),
                    'stock_unit': _su,
                    # v3.106：金额不再由表单代算 —— 要算就在表格里写公式。
                    # 这里只原样搬运使用者填的值，没填就不写这一列。
                    'amount': (num(_fv('amount', i)) or None),
                    'supplier': supplier, 'pono': pono, 'odate': odate,
                    'note': '',
                }))
                n += 1
    except Exception:
        _log_err('采购单保存失败')
        return _form(err='保存失败，请重试（详情见 warehouse.log）')
    if n == 0:
        db.run("DELETE FROM pos WHERE id=?", (po_id,))
        return _form(err='至少要填一行物料（名称和数量）')

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
    # 回写到电子表格：先存住列映射，再按批次号写行
    _wb_msg = ''
    _bw, _bsh = _bind_from_form()
    if _bw and _bsh:
        _mp = {}
        for _f, _lb, _d in BR.FIELDS.get('po', []):
            _v = request.form.get('map_' + _f)
            try:
                _mp[_f] = int(_v) if _v not in (None, '') else BR.SKIP
            except (TypeError, ValueError):
                _mp[_f] = BR.SKIP
        # 页面没展开映射卡片时一个 map_* 都没提交，这时按表头自动猜一套
        if all(v == BR.SKIP for v in _mp.values()):
            _mp = BR.auto_map('po', BR.heads(_bw, _bsh))
        if BR.bind_set('po', _bw, _bsh, _mp):
            _ok = 0
            for _bt, _vals in rows:
                try:
                    _done, _why = BR.write('po', _bt, _vals)
                    if _done:
                        _ok += 1
                except Exception:
                    _log_err('回写电子表格失败 %s' % _bt)
            _wb_msg = '，%d 行已录入表格' % _ok if _ok else '，表格未写入'
    return redirect(url_for('po_detail', po_id=po_id,
                            msg='采购单 %s 已创建，%d 条明细%s'
                                % (pono, n, _wb_msg)))


def _po_new_get():
    _bind = _cur_bind()
    _shs, _heads = [], []
    if _bind:
        try:
            _shs = BR.sheets(_bind['wb_id'])
            _heads = BR.heads(_bind['wb_id'], _bind['sh_name'])
        except Exception:
            _log_err('读取表格绑定失败')
    return render_template('po_new.html', mats=_mat_choices(), today=today(),
                           sups=_sup_names(), units=db.unit_choices(), tax_default=13,
                           BOOKS=BR.books(), BIND=_bind, SHS=_shs, HEADS=_heads,
                           **_view_ctx())


def _cur_bind():
    """当前要写入的表：优先上次绑的，没绑过就取最新一本工作簿的第一张表。

    为什么默认就选一张：采购明细总得有个去处，每次都让用户先选一遍
    是把「记住」该做的事推给了人。没建过任何工作簿时才返回 None。
    """
    try:
        b = BR.bind_get('po')
        if b:
            return b
        bks = BR.books()
        for bk in bks:
            shs = BR.sheets(bk['id'])
            if shs:
                return {'wb_id': bk['id'], 'sh_name': shs[0], 'mapping': {},
                        'module': 'po'}
    except Exception:
        _log_err('取默认表格绑定失败')
    return None


def _bind_from_form():
    """表单里选的表；没传（比如页面上一本工作簿都没有）就退回当前默认绑定。"""
    try:
        _bw = int(request.form.get('bind_wb') or 0)
    except (TypeError, ValueError):
        _bw = 0
    _bsh = (request.form.get('bind_sh') or '').strip()
    if _bw and _bsh:
        return _bw, _bsh
    b = _cur_bind()
    return (b['wb_id'], b['sh_name']) if b else (0, '')


def _form(err=''):
    return render_template('po_new.html', err=err, mats=_mat_choices(),
                           today=today(), sups=_sup_names(),
                           units=db.unit_choices(), tax_default=13,
                           BOOKS=BR.books(), BIND=_cur_bind(), SHS=[],
                           HEADS=[], **_view_ctx())


def _view_ctx():
    """填写页共用的「字段」上下文（v3.106）

    SHOWN  表单上要出现哪些列（屏蔽掉的不出现）
    MAPF   映射卡片里可映射的列 —— 与 SHOWN 同一套，
           所以屏蔽一列，表单和表格两处同时消失。
    """
    _shown = CL.shown('po')
    _fids = {f for f, _ in _shown}
    return {
        'SHOWN': _shown,
        'MAPF': [f for f in BR.FIELDS.get('po', []) if f[0] in _fids],
        'SKIP': BR.SKIP,
    }


# 显示列配置的读 / 写（与入库出库共用同一套实现）
_cols_view = colsapi.register(bp, '/po/cols', 'po', 'po_cols_cfg')


@bp.route('/po/bind/sheets')
def po_bind_sheets():
    """选了工作簿后，取它里面的工作表列表。"""
    try:
        wb_id = int(request.args.get('wb') or 0)
    except (TypeError, ValueError):
        wb_id = 0
    return jsonify({'sheets': BR.sheets(wb_id) if wb_id else []})


@bp.route('/po/bind/heads')
def po_bind_heads():
    """选了工作表后，取表头 + 自动猜的映射 + 这套表以前存过的映射。"""
    try:
        wb_id = int(request.args.get('wb') or 0)
    except (TypeError, ValueError):
        wb_id = 0
    sh = (request.args.get('sh') or '').strip()
    heads = BR.heads(wb_id, sh) if (wb_id and sh) else []
    auto = BR.auto_map('po', heads)
    # 这套表以前配过就用以前的，没配过才用猜的
    saved = {}
    if wb_id and sh:
        try:
            r = db.q("SELECT mapping FROM sheet_bind WHERE module='po' AND wb_id=?"
                     " AND sh_name=?", wb_id, sh)
            if r:
                saved = BR._json(r[0]['mapping'])
        except Exception:
            pass
    return jsonify({'heads': heads, 'auto': auto, 'saved': saved})


@bp.route('/po/bind/off', methods=['POST'])
def po_bind_off():
    """取消绑定：不再自动回写，但映射记录留着。"""
    BR.bind_off('po')
    return jsonify({'ok': True})


def _bt_seq(pono, idx):
    """建单时按 单号-序号 生成批次号（与 receive.new_item_batch 同一套规则）。

    为什么不在循环里查库生成：建单是在一个事务里批量插的，此时新明细还没落库，
    查 COUNT 拿不到刚插的行，会重号。用循环序号最稳。
    """
    return '%s-%d' % (pono, idx + 1)


def _mat_choices():
    """物料候选。带出长/宽是为了选中后自动填进明细，少打两遍。"""
    return [dict(id=m['id'], name=m['name'], spec=m['spec'] or '',
                 width=m['width'] or '', unit=m['unit'] or '',
                 code=m['code'] or '', qty_unit=m['qty_unit'] or '平米')
            for m in db.q("SELECT id,name,spec,width,unit,code,qty_unit FROM materials"
                          " WHERE active=1 ORDER BY name")]


def _sup_names():
    return [r['name'] for r in db.q("SELECT name FROM suppliers WHERE active=1 ORDER BY name")]
