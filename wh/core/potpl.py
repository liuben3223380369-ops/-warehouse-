# -*- coding: utf-8 -*-
"""采购模板（v3.36）：采购单模板与列定义。"""
import os, sqlite3, json, sys
from datetime import datetime
from .paths import (app_dir, res_dir, BASE, DB_PATH, SEED, UNITS,
                   unit_choices, _is_frozen, _writable, _user_data_dir)
from .dbconn import (conn, tx, close, q, run, runmany, _flat, _cols,
                    _run_now, _runmany_now, _write_lock, _is_write, _busy_retry)
from .tpl import head_sig

# ==================== 采购模板（v3.36） ====================
# 跟库存模板完全分开两套：库存模板改列不该牵动采购单，反过来也一样。
# 每个采购模板 = 一套采购明细列配置（系统列 + 自定义列）。
DEFAULT_PO_COLS = [
    # fid,        标签,      pos, enabled, 别名
    ('name',       '物料名称', 0, 1, '物料名称,名称,品名,物料,物品名称,货物名称,商品名称,产品名称,名称及规格,物料名'),
    ('spec',       '规格',     1, 1, '规格,型号,规格型号,物料规格,厚度,宽幅规格'),
    ('unit',       '采购单位', 2, 1, '单位,采购单位,单位名称,计量单位'),
    ('qty',        '数量',     3, 1, '数量,采购数量,订购数量,订单数量,需求数量'),
    ('price',      '单价',     4, 1, '单价,采购单价,含税单价,不含税单价,价格,单位价格'),
    ('conv',       '换算率',   5, 1, '换算率,换算,换算比,转换率,单位换算'),
    ('stock_unit', '库存单位', 6, 0, '库存单位,入库单位,库存计量单位'),
    ('batch',      '批次',     7, 0, '批次,批次号,批号,lot,批'),
    ('note',       '备注',     8, 1, '备注,说明,摘要,附注'),
]


def _fix_po_tpl_cols():
    """早先的 po_tpl_cols 排序列叫 ord，后来统一成 pos（与库存模板一致）。

    SQLite 改列名麻烦，这里直接重建：这张表只存列配置，
    且采购模板功能还没正式用过，没有值得保留的数据。
    """
    try:
        cs = _cols('po_tpl_cols')
        if 'ord' in cs and 'pos' not in cs:
            run("DROP TABLE po_tpl_cols")
    except Exception:
        pass


def _ensure_po_tpl():
    """保证至少有一个采购模板（老库升级时建）"""
    try:
        if q("SELECT COUNT(*) c FROM po_tpl")[0]['c']:
            return
        add_po_tpl('通用采购单', '默认模板：物料名称/规格/单位/数量/单价/换算率/备注')
    except Exception:
        pass


def po_tpls():
    return q("SELECT * FROM po_tpl ORDER BY ord, id")


def po_tpl(tid):
    r = q("SELECT * FROM po_tpl WHERE id=?", tid)
    return r[0] if r else None


def po_default_tpl_id():
    r = q("SELECT id FROM po_tpl ORDER BY ord, id LIMIT 1")
    return r[0]['id'] if r else None


def add_po_tpl(name, note='', copy_from=None):
    name = (name or '').strip() or '未命名采购模板'
    mx = q("SELECT COALESCE(MAX(ord),0) p FROM po_tpl")[0]['p']
    tid = run("INSERT INTO po_tpl(name,note,ord,created_at) VALUES(?,?,?,?)",
              name, note, mx + 1,
              datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    if copy_from:
        for r in q("SELECT fid,label,pos,enabled,aliases,unit,xtype,xopt,xform"
                   " FROM po_tpl_cols WHERE tpl_id=?", copy_from):
            run("INSERT INTO po_tpl_cols(tpl_id,fid,label,pos,enabled,aliases,"
                "unit,xtype,xopt,xform) VALUES(?,?,?,?,?,?,?,?,?,?)",
                tid, r['fid'], r['label'], r['pos'], r['enabled'], r['aliases'],
                r['unit'] or '', r['xtype'] or 'text', r['xopt'] or '', r['xform'] or '')
    else:
        for fid, label, pos, en, al in DEFAULT_PO_COLS:
            run("INSERT INTO po_tpl_cols(tpl_id,fid,label,pos,enabled,aliases)"
                " VALUES(?,?,?,?,?,?)", tid, fid, label, pos, en, al)
    return tid


def rename_po_tpl(tid, name, note=None):
    name = (name or '').strip()
    if not name:
        return False
    if note is None:
        run("UPDATE po_tpl SET name=? WHERE id=?", name, tid)
    else:
        run("UPDATE po_tpl SET name=?,note=? WHERE id=?", name, note, tid)
    return True


def del_po_tpl(tid):
    n = q("SELECT COUNT(*) c FROM po_items WHERE COALESCE(ptpl_id,0)=?", tid)[0]['c']
    if n:
        return False, '还有 %d 条明细在用这个模板，不能删。' % n
    if q("SELECT COUNT(*) c FROM po_tpl")[0]['c'] <= 1:
        return False, '至少要留一个采购模板'
    run("DELETE FROM po_tpl_cols WHERE tpl_id=?", tid)
    run("DELETE FROM po_tpl WHERE id=?", tid)
    return True, '已删除'


def po_tpl_cols(tid, only_enabled=True):
    sql = "SELECT * FROM po_tpl_cols WHERE tpl_id=?"
    if only_enabled:
        sql += " AND enabled=1"
    return q(sql + " ORDER BY pos", tid)


def save_po_tpl_cols(tid, rows):
    with tx() as c:
        c.executemany("UPDATE po_tpl_cols SET label=?,pos=?,enabled=?,aliases=?,"
                      "xtype=?,xopt=?,xform=?,unit=? WHERE tpl_id=? AND fid=?",
                      [(r['label'], r['pos'], r['enabled'], r['aliases'],
                        r.get('xtype') or 'text', r.get('xopt') or '',
                        r.get('xform') or '', (r.get('unit') or '').strip()[:20],
                        tid, r['fid']) for r in rows])
    return True


def reset_po_tpl_cols(tid):
    run("DELETE FROM po_tpl_cols WHERE tpl_id=?", tid)
    for fid, label, pos, en, al in DEFAULT_PO_COLS:
        run("INSERT INTO po_tpl_cols(tpl_id,fid,label,pos,enabled,aliases)"
            " VALUES(?,?,?,?,?,?)", tid, fid, label, pos, en, al)


def po_tpl_custom_cols(tid, enabled_only=True):
    sql = "SELECT * FROM po_tpl_cols WHERE tpl_id=? AND fid LIKE 'x_%'"
    if enabled_only:
        sql += " AND enabled=1"
    return q(sql + " ORDER BY pos, fid", tid)


def po_add_custom_col(tid, label, xtype='text', xopt='', xform=''):
    import time as _t
    fid = 'x_%d' % int(_t.time() * 1000)
    mx = q("SELECT COALESCE(MAX(pos),0) p FROM po_tpl_cols WHERE tpl_id=?", tid)[0]['p']
    run("INSERT INTO po_tpl_cols(tpl_id,fid,label,pos,enabled,aliases,xtype,xopt,xform)"
        " VALUES(?,?,?,?,?,?,?,?,?)", tid, fid, (label or '').strip() or '新列',
        mx + 1, 1, (label or '').strip(), xtype or 'text', xopt or '', xform or '')
    return fid


def po_del_custom_col(tid, fid):
    if not str(fid or '').startswith('x_'):
        return False
    run("DELETE FROM po_tpl_cols WHERE tpl_id=? AND fid=?", tid, fid)
    import json as _json
    for r in q("SELECT id, extra FROM po_items WHERE extra IS NOT NULL AND extra<>''"):
        try:
            d = _json.loads(r['extra'] or '{}') or {}
        except Exception:
            continue
        if fid in d:
            d.pop(fid, None)
            run("UPDATE po_items SET extra=? WHERE id=?",
                _json.dumps(d, ensure_ascii=False), r['id'])
    return True


def po_tpl_from_headers(headers, name=None):
    """用一张采购表的表头建模板：每个列名成为模板的一个启用列"""
    seen, cols = set(), []
    for h in (headers or []):
        t = str(h or '').strip()
        if not t or t in seen:
            continue
        seen.add(t)
        cols.append(t)
    tid = add_po_tpl(name or '新采购表', '由表头自动生成')
    run("DELETE FROM po_tpl_cols WHERE tpl_id=?", tid)
    for i, label in enumerate(cols):
        run("INSERT INTO po_tpl_cols(tpl_id,fid,label,pos,enabled,aliases)"
            " VALUES(?,?,?,?,?,?)", tid, 'x%d' % i, label, i, 1, label)
    run("UPDATE po_tpl SET sig=? WHERE id=?", head_sig(headers), tid)
    return tid


def po_tpl_by_sig(sig):
    if not sig:
        return None
    r = q("SELECT id FROM po_tpl WHERE sig=? AND sig<>'' LIMIT 1", sig)
    return r[0]['id'] if r else None


def po_tpl_stat(tid):
    n = q("SELECT COUNT(*) c FROM po_items WHERE COALESCE(ptpl_id,0)=?", tid)[0]['c']
    np_ = q("SELECT COUNT(DISTINCT po_id) c FROM po_items WHERE COALESCE(ptpl_id,0)=?", tid)[0]['c']
    return n, np_
