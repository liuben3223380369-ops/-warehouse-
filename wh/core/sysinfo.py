# -*- coding: utf-8 -*-
"""系统自检与备份"""

from .router import Router
bp = Router('sys')
from flask import request, redirect, url_for, Response, render_template
from datetime import datetime, date
import calendar, io, csv, os, time, sys, json
from urllib.parse import quote
from ..core import db, util
from ..core.util import *            # noqa: F401,F403
from ..core.util import (_log_err, _last_prices, js_mats_with_price)  # noqa: F401
from .. import importer
from ..table import tbl

# ---------- 系统自检 / 备份 ----------
@bp.route('/sys')
def sysinfo():
    import platform
    # 默认快速体检（0.2 秒）；?deep=1 才跑逐页校验（12 秒，手动点才做）
    deep = (request.args.get('deep') or '') == '1'
    issues = db.check_integrity(deep=deep)
    # 旧版（v3.29 之前）到货会自动生成入库单，现在采购与库存已独立，
    # 这些历史遗留单据列出来让使用者自己决定要不要清掉。
    legacy = db.q("SELECT COUNT(*) c, COALESCE(SUM(qty),0) q FROM txns"
                  " WHERE note LIKE '采购到货%'")[0]
    # 到货数有两处：po_receipts 是流水源头，po_items.recv_qty 是冗余缓存（便于列表汇总）。
    # 正常登记时两者在同一事务里一起写，不会漂；但外部导入、老库迁移、异常中断
    # 可能只写了一个 —— 那样页面会一直显示「已到货 0 / 已下单」，且不报错。
    drift = db.q("SELECT COUNT(*) c FROM po_items i"
                 " WHERE ABS(COALESCE(i.recv_qty,0) - COALESCE("
                 "   (SELECT SUM(r.qty) FROM po_receipts r WHERE r.item_id=i.id),0)) > 1e-6")[0]
    return render_template('sys.html', issues=issues, stats=db.stats(),
                           deep=deep,
                           legacy=legacy,
                           drift=drift,
                           py=platform.python_version(),
                           baks=sorted([f for f in os.listdir(BASE) if f.endswith('.bak')],
                                       reverse=True)[:5],
                           msg=request.args.get('msg', ''))

@bp.route('/sys/fix')
def sysfix():
    n = db.fix_orphans()
    return redirect(url_for('sysinfo', msg=('已清理 %d 条孤儿单据' % n) if n else '没有需要清理的数据'))

@bp.route('/sys/fix_recv')
def sys_fix_recv():
    """按到货流水重算 po_items.recv_qty。

    po_receipts 是源头，recv_qty 只是冗余缓存。正常登记两者一起写，
    但外部导入/老库迁移/异常中断可能只写了一个，页面就会一直显示
    「已到货 0、已下单」还不报错。这里以流水为准把缓存拉回来。
    """
    n = db.q("SELECT COUNT(*) c FROM po_items i"
             " WHERE ABS(COALESCE(i.recv_qty,0) - COALESCE("
             "   (SELECT SUM(r.qty) FROM po_receipts r WHERE r.item_id=i.id),0)) > 1e-6")[0]['c']
    if not n:
        return redirect(url_for('sysinfo', msg='到货数没有不一致，无需修复'))
    try:
        bk = db.backup()
    except Exception:
        bk = ''
    db.run("UPDATE po_items SET recv_qty=COALESCE("
           "  (SELECT SUM(r.qty) FROM po_receipts r WHERE r.item_id=po_items.id),0)")
    # 到货数变了，单头状态（已下单/部分到货/已完成）要跟着重算
    try:
        from ..po import logic as _pl          # 延迟导入：避免 core 反向依赖 po
        for r in db.q("SELECT DISTINCT po_id FROM po_items"):
            _pl.refresh_status(r['po_id'])
    except Exception as e:
        _log_err('修复到货数后重算状态失败', 'refresh_status: %s' % e)
    tip = '已按到货流水重算 %d 条明细的到货数' % n
    if bk:
        tip += '；修复前已自动备份 %s' % os.path.basename(bk)
    return redirect(url_for('sysinfo', msg=tip))

@bp.route('/sys/purge_po_txns')
def sys_purge_po_txns():
    """清掉旧版采购到货自动生成的入库单。

    采购与库存独立之后，这些单据本不该存在（货要进仓库请到入库页另录）。
    但删了会让库存减少，所以只在使用者手动点的时候才删，并先自动备份。
    """
    n = db.q("SELECT COUNT(*) c FROM txns WHERE note LIKE '采购到货%'")[0]['c']
    if not n:
        return redirect(url_for('sysinfo', msg='没有旧版采购入库单，无需清理'))
    try:
        bk = db.backup()
    except Exception:
        bk = ''
    db.run("DELETE FROM txns WHERE note LIKE '采购到货%'")
    db.run("UPDATE po_receipts SET txn_id=NULL")
    tip = '已清理 %d 条旧版采购入库单（库存相应减少）' % n
    if bk:
        tip += '；清理前已自动备份 %s' % os.path.basename(bk)
    return redirect(url_for('sysinfo', msg=tip))


@bp.route('/sys/backup')
def sysbackup():
    p = db.backup()
    return redirect(url_for('sysinfo', msg='已备份到 %s' % os.path.basename(p)))

