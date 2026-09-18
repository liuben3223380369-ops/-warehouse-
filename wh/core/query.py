# -*- coding: utf-8 -*-
"""常用业务查询：物料流水、库存行、批量改删。"""
import os, sqlite3, json, sys
from datetime import datetime
from .paths import (app_dir, res_dir, BASE, DB_PATH, SEED, UNITS,
                   unit_choices, _is_frozen, _writable, _user_data_dir)
from .dbconn import (conn, tx, close, q, run, runmany, _flat, _cols,
                    _run_now, _runmany_now, _write_lock, _is_write, _busy_retry)

def history(mid, m=''):
    """物料台账：每笔单据 + 滚动结存"""
    sql = """SELECT t.*, m.name, m.unit, m.code, m.opening,
      ROUND(t.qty * COALESCE(t.price,0), 2) AS amount,
      ROUND(m.opening + SUM(CASE WHEN t.kind='进' THEN t.qty ELSE -t.qty END) OVER (
        PARTITION BY t.material_id ORDER BY t.tdate, t.id
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW), 6) AS balance
      FROM txns t JOIN materials m ON m.id=t.material_id WHERE t.material_id=?"""
    args = [mid]
    if m:
        sql += " AND t.tdate LIKE ?"; args.append(m + '%')
    sql += " ORDER BY t.tdate DESC, t.id DESC"
    return q(sql, *args)

def stock_rows(where='', args=()):
    return q("SELECT * FROM v_stock " + where + " ORDER BY category, name", *args)

# A-G 列 + 期初/安全库存/状态，全部可自由修改
SETABLE = {
    'name': str, 'code': str, 'supplier': str, 'category': str,
    'spec': str, 'width': str, 'unit': str, 'status': str,
    'opening': float, 'safety': float,
}
FIELDS = ['name', 'code', 'supplier', 'category', 'spec', 'width', 'unit',
          'status', 'opening', 'safety']

def batch_update(ids, field, value, mode='set', old=''):
    """mode: set(设为该值) | replace(把字段里的 old 替换成 value)"""
    if field not in SETABLE or not ids:
        return 0
    ph = ','.join('?' * len(ids))
    if mode == 'replace':
        old = str(old)
        if not old:
            return 0
        run(f"UPDATE materials SET {field}=REPLACE({field},?,?) WHERE id IN ({ph})",
            old, str(value), *ids)
        return len(ids)
    v = float(value or 0) if field in ('opening', 'safety') else str(value)
    run(f"UPDATE materials SET {field}=? WHERE id IN ({ph})", v, *ids)
    return len(ids)

def batch_delete(ids):
    """有单据的停用，无单据的真删。返回 (删除数, 停用数)。
    整批在一个事务里完成，中途出错全回滚。"""
    if not ids:
        return 0, 0
    ph = ','.join('?' * len(ids))
    with tx() as c:
        used = {r['material_id'] for r in
                c.execute(f"SELECT DISTINCT material_id FROM txns WHERE material_id IN ({ph})", ids)}
        # 采购明细同样引用物料，漏掉就会真删失败、整批回滚
        used |= {r['material_id'] for r in
                 c.execute(f"SELECT DISTINCT material_id FROM po_items"
                           f" WHERE material_id IS NOT NULL AND material_id IN ({ph})", ids)}
        dead = [i for i in ids if i not in used]
        keep = [i for i in ids if i in used]
        if dead:
            c.execute(f"DELETE FROM materials WHERE id IN ({','.join('?' * len(dead))})", dead)
        if keep:
            c.execute(f"UPDATE materials SET active=0 WHERE id IN ({','.join('?' * len(keep))})", keep)
    return len(dead), len(keep)

def batch_set_active(ids, active):
    if not ids:
        return 0
    ph = ','.join('?' * len(ids))
    run(f"UPDATE materials SET active=? WHERE id IN ({ph})", active, *ids)
    return len(ids)
