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
    issues = db.check_integrity()
    # 旧版（v3.29 之前）到货会自动生成入库单，现在采购与库存已独立，
    # 这些历史遗留单据列出来让使用者自己决定要不要清掉。
    legacy = db.q("SELECT COUNT(*) c, COALESCE(SUM(qty),0) q FROM txns"
                  " WHERE note LIKE '采购到货%'")[0]
    return render_template('sys.html', issues=issues, stats=db.stats(),
                           legacy=legacy,
                           py=platform.python_version(),
                           baks=sorted([f for f in os.listdir(BASE) if f.endswith('.bak')],
                                       reverse=True)[:5],
                           msg=request.args.get('msg', ''))

@bp.route('/sys/fix')
def sysfix():
    n = db.fix_orphans()
    return redirect(url_for('sysinfo', msg=('已清理 %d 条孤儿单据' % n) if n else '没有需要清理的数据'))

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

