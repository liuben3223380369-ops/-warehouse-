# -*- coding: utf-8 -*-
"""出入库模块（3/3）：流水列表、批量操作、删除

删除时要顺带撤掉挂在采购单上的到货记录（_unlink_po_receipts）。
"""
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
from ..po.amount import price_map, txn_unit_price       # noqa: F401
from ..po.receive import sync_txn_to_po                 # noqa: F401
from .form import _apply_po_price, _extra_map


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
    from ..po import status as po_status
    for pid in po_ids:
        try:
            st = po_status.derive_status(pid)
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
