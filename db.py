import os, sqlite3, json, sys
from datetime import datetime

def _is_frozen():
    return getattr(sys, 'frozen', False)

def app_dir():
    """程序目录：打包后是 exe 所在目录，源码运行时是脚本目录。
    数据库、备份、上传临时目录都放这里——它可写、且每次运行都固定。"""
    if _is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))

def res_dir():
    """资源目录：打包后是 PyInstaller 解包出来的临时目录（只读）。
    seed_materials.json 这类随程序分发的只读文件从这里取。"""
    if _is_frozen():
        return getattr(sys, '_MEIPASS', app_dir())
    return os.path.dirname(os.path.abspath(__file__))

BASE = app_dir()
DB_PATH = os.environ.get('WAREHOUSE_DB') or os.path.join(BASE, 'warehouse.db')
SEED = os.path.join(res_dir(), 'seed_materials.json')

VIEWS = """
CREATE VIEW IF NOT EXISTS v_stock AS
SELECT m.*,
  COALESCE(a.i, 0) AS in_qty,
  COALESCE(a.o, 0) AS out_qty,
  ROUND(m.opening + COALESCE(a.i, 0) - COALESCE(a.o, 0), 6) AS stock
FROM materials m
LEFT JOIN (SELECT material_id,
             SUM(CASE WHEN kind='进' THEN qty ELSE 0 END) AS i,
             SUM(CASE WHEN kind='出' THEN qty ELSE 0 END) AS o
           FROM txns GROUP BY material_id) a ON a.material_id = m.id
WHERE m.active = 1;
CREATE VIEW IF NOT EXISTS v_mats AS
SELECT m.*,
  COALESCE(a.i, 0) AS in_qty,
  COALESCE(a.o, 0) AS out_qty,
  m.opening + COALESCE(a.i, 0) - COALESCE(a.o, 0) AS stock
FROM materials m
LEFT JOIN (SELECT material_id,
             SUM(CASE WHEN kind='进' THEN qty ELSE 0 END) AS i,
             SUM(CASE WHEN kind='出' THEN qty ELSE 0 END) AS o
           FROM txns GROUP BY material_id) a ON a.material_id = m.id;
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS materials (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  supplier TEXT    DEFAULT '',
  category TEXT    DEFAULT '',
  spec     TEXT    DEFAULT '',
  width    TEXT    DEFAULT '',
  name     TEXT    NOT NULL,
  code     TEXT    DEFAULT '',
  unit     TEXT    DEFAULT '平米',
  opening  REAL    NOT NULL DEFAULT 0,
  safety   REAL    NOT NULL DEFAULT 0,
  status   TEXT    DEFAULT '常用',
  active   INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS txns (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  tdate       TEXT    NOT NULL,
  material_id INTEGER NOT NULL REFERENCES materials(id),
  kind        TEXT    NOT NULL CHECK(kind IN ('进','出')),
  qty         REAL    NOT NULL CHECK(qty > 0),
  pieces      REAL    DEFAULT NULL,
  per_piece   REAL    DEFAULT NULL,
  note        TEXT    DEFAULT '',
  created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_txns_date ON txns(tdate);
CREATE INDEX IF NOT EXISTS idx_txns_mat  ON txns(material_id);
CREATE INDEX IF NOT EXISTS idx_txns_kind ON txns(kind);
CREATE INDEX IF NOT EXISTS idx_mat_active ON materials(active);
CREATE TABLE IF NOT EXISTS colmap (
  fid     TEXT PRIMARY KEY,
  label   TEXT    NOT NULL,
  pos     INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1,
  aliases TEXT    DEFAULT ''
);
"""

import threading
_local = threading.local()

def conn():
    """按线程复用连接：PRAGMA 只需设一次，事务也能跨调用保持。

    逐项降级：WAL 开不了就退回普通模式（某些文件系统/外置存储不支持 WAL），
    绝不因为一个可选优化让整个程序起不来。
    """
    c = getattr(_local, 'conn', None)
    if c is not None:
        return c
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)) or '.', exist_ok=True)
    try:
        c = sqlite3.connect(DB_PATH, timeout=15)
    except sqlite3.Error as e:
        raise RuntimeError(
            '打不开数据库文件：%s\n原因：%s\n'
            '建议：换一个有读写权限的目录（比如手机内部存储，而不是外置 SD 卡），'
            '或用 WAREHOUSE_DB 环境变量指定路径。' % (DB_PATH, e))
    c.row_factory = sqlite3.Row
    for pragma in ("PRAGMA foreign_keys=ON",        # 外键真正生效
                   "PRAGMA busy_timeout=15000",     # 并发写不立刻报错
                   "PRAGMA journal_mode=WAL"):      # 读写不互相阻塞（可选）
        try:
            c.execute(pragma)
        except sqlite3.DatabaseError:
            if 'journal_mode' in pragma:
                try:                                # 退回默认的 DELETE 模式
                    c.execute("PRAGMA journal_mode=DELETE")
                except sqlite3.DatabaseError:
                    pass
    _local.conn = c
    return c

class tx:
    """真正的事务：批量写要么全成功要么全回滚。

    用法：
        with db.tx() as c:
            c.execute(...)
    嵌套时复用外层事务（用 SAVEPOINT）。
    """
    def __enter__(self):
        c = conn()
        self.depth = getattr(_local, 'depth', 0)
        if self.depth == 0:
            c.execute("BEGIN")
        else:
            c.execute("SAVEPOINT sp%s" % self.depth)
        _local.depth = self.depth + 1
        return c

    def __exit__(self, exc_type, exc, tb):
        c = conn()
        _local.depth = self.depth
        try:
            if exc_type is None:
                if self.depth == 0:
                    c.commit()
                else:
                    c.execute("RELEASE sp%s" % self.depth)
            else:
                if self.depth == 0:
                    c.rollback()
                else:
                    c.execute("ROLLBACK TO sp%s" % self.depth)
                    c.execute("RELEASE sp%s" % self.depth)
        except sqlite3.Error:
            pass
        return False

def close():
    c = getattr(_local, 'conn', None)
    if c is not None:
        try:
            c.close()
        except sqlite3.Error:
            pass
        _local.conn = None

def _cols(table):
    return [r['name'] for r in q("PRAGMA table_info(%s)" % table)]

def migrate():
    """老库平滑升级：补齐后加的列，并按需重建视图/索引。"""
    for col, ddl in (('pieces', 'REAL'), ('per_piece', 'REAL')):
        if col not in _cols('txns'):
            run("ALTER TABLE txns ADD COLUMN %s %s" % (col, ddl))
    # 视图改成聚合 JOIN 后，老库里的旧视图不会自动更新，这里重建
    old = q("SELECT sql FROM sqlite_master WHERE type='view' AND name='v_stock'")
    if old and ('COALESCE(a.i' not in (old[0]['sql'] or '')
                or 'ROUND(' not in (old[0]['sql'] or '')):
        with tx() as c:
            c.execute("DROP VIEW IF EXISTS v_stock")
            c.execute("DROP VIEW IF EXISTS v_mats")
            c.executescript(VIEWS)
    for idx, ddl in (('idx_txns_kind', "CREATE INDEX IF NOT EXISTS idx_txns_kind ON txns(kind)"),
                     ('idx_mat_active', "CREATE INDEX IF NOT EXISTS idx_mat_active ON materials(active)")):
        run(ddl)

def init():
    fresh = not os.path.exists(DB_PATH)
    with tx() as c:
        c.executescript(SCHEMA)
        c.executescript(VIEWS)
        if fresh and os.path.exists(SEED):
            for m in json.load(open(SEED, encoding='utf-8')):
                name = (m.get('name') or '').strip() or (m.get('code') or '').strip()
                if not name:          # 名称和料号都空 -> 原表里的空行，跳过
                    continue
                c.execute("INSERT INTO materials(supplier,category,spec,width,name,code,unit)"
                          " VALUES(?,?,?,?,?,?,?)",
                          (m['supplier'], m['category'], m['spec'], str(m['width']),
                           name, m['code'], m['unit'] or '平米'))
        for fid, label, pos, en, al in DEFAULT_COLS:
            c.execute("INSERT OR IGNORE INTO colmap(fid,label,pos,enabled,aliases)"
                      " VALUES(?,?,?,?,?)", (fid, label, pos, en, al))
    migrate()

def q(sql, *a):
    return conn().execute(sql, _flat(a)).fetchall()

def _flat(a):
    """run(sql, v, *ids) 与 run(sql, (v,)+ids) 两种写法都能吃"""
    if len(a) == 1 and isinstance(a[0], (tuple, list)):
        return tuple(a[0])
    return a

def run(sql, *a):
    """执行写操作。不在 tx() 内则立即提交。

    注意：不能用 c.in_transaction 判断——sqlite3 执行 INSERT 会自动开隐式事务，
    那个标志恒为 True，会导致永远不提交、进程退出后数据全丢。
    """
    c = conn()
    cur = c.execute(sql, _flat(a))
    if getattr(_local, 'depth', 0) == 0:
        c.commit()
    return cur.lastrowid

def runmany(sql, seq):
    """批量执行。返回影响行数"""
    c = conn()
    cur = c.executemany(sql, seq)
    if getattr(_local, 'depth', 0) == 0:
        c.commit()
    return cur.rowcount

def history(mid, m=''):
    """物料台账：每笔单据 + 滚动结存"""
    sql = """SELECT t.*, m.name, m.unit, m.code, m.opening,
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

# ---------- 列（表头）映射配置 ----------
# fid=系统字段, label=入库界面显示的表头, pos=列顺序, enabled=是否显示, aliases=导入时识别的表头别名
DEFAULT_COLS = [
    ('supplier', '供应商', 1, 1, '供应商,厂商,供货商'),
    ('category', '类型', 2, 1, '类型,类别,分类,大类'),
    ('spec', '规格（米）', 3, 1, '规格,规格（米）,规格(米),厚度'),
    ('width', '宽幅', 4, 1, '宽幅,宽度,幅宽'),
    ('name', '物料名称', 5, 1, '物料名称,名称,品名,品名规格,物料,材料名称'),
    ('code', '料号', 6, 1, '料号,物料编号,物料编码,编号,编码,型号,规格型号'),
    ('unit', '单位（卷）', 7, 1, '单位,单位（卷）,单位(卷),计量单位'),
    ('status', '状态', 8, 1, '状态,使用状态'),
    ('opening', '期初结存', 9, 1, '期初结存,期初,上月结存,上期结存,库存,当前库存'),
    ('safety', '安全库存', 10, 1, '安全库存,预警值,库存预警,最低库存'),
]

def init_cols():
    # 注意：conn() 是按线程复用的共享连接，这里绝对不能 close()，
    # 否则 _local.conn 会指向一个已关闭的连接，之后所有查询全部报
    # "Cannot operate on a closed database"——整个程序直接废掉。
    c = conn()
    for fid, label, pos, en, al in DEFAULT_COLS:
        c.execute("INSERT OR IGNORE INTO colmap(fid,label,pos,enabled,aliases)"
                  " VALUES(?,?,?,?,?)", (fid, label, pos, en, al))
    c.commit()

def cols(only_enabled=True):
    sql = "SELECT * FROM colmap"
    if only_enabled:
        sql += " WHERE enabled=1"
    return q(sql + " ORDER BY pos")

def save_cols(rows):
    """整批保存列映射，一个事务内完成（老写法 BEGIN 会被立即提交，是假事务）"""
    with tx() as c:
        c.executemany("UPDATE colmap SET label=?,pos=?,enabled=?,aliases=? WHERE fid=?",
                      [(r['label'], r['pos'], r['enabled'], r['aliases'], r['fid']) for r in rows])
    return True

def reset_cols():
    run("DELETE FROM colmap")
    init_cols()

# ---------- 数据完整性 / 备份 ----------
def check_integrity():
    """体检：返回问题列表（空列表=健康）"""
    issues = []
    try:
        if q("PRAGMA integrity_check")[0][0] != 'ok':
            issues.append('数据库文件结构异常（integrity_check 未通过）')
    except sqlite3.DatabaseError as e:
        issues.append('数据库文件损坏，读不了了：%s' % e)
        # 只说"坏了"没用，得告诉用户手上有哪些备份可以救
        try:
            d = os.path.dirname(os.path.abspath(DB_PATH)) or '.'
            baks = sorted((f for f in os.listdir(d)
                           if f.startswith(os.path.basename(DB_PATH) + '.')
                           and f.endswith('.bak')), reverse=True)
        except OSError:
            baks = []
        if baks:
            issues.append('别慌，有 %d 份自动备份可以还原，最新的是 %s' % (len(baks), baks[0]))
            issues.append('还原方法：退出程序 → 把 %s 改名成 %s → 重新打开'
                          % (baks[0], os.path.basename(DB_PATH)))
        else:
            issues.append('没有找到自动备份，数据库只能重建，此前的单据将无法恢复')
        return issues
    for r in q("SELECT t.id, t.material_id FROM txns t"
               " LEFT JOIN materials m ON m.id=t.material_id WHERE m.id IS NULL"):
        issues.append('单据 #%s 指向不存在的物料（%s）' % (r['id'], r['material_id']))
    for r in q("SELECT id, name FROM materials WHERE name IS NULL OR trim(name)=''"):
        issues.append('物料 #%s 没有名称' % r['id'])
    for r in q("SELECT id, tdate FROM txns WHERE tdate IS NULL OR tdate NOT LIKE '____-__-__'"):
        issues.append('单据 #%s 日期格式异常（%s）' % (r['id'], r['tdate']))
    for r in q("SELECT id, qty FROM txns WHERE qty IS NULL OR qty <= 0"):
        issues.append('单据 #%s 数量不合法（%s）' % (r['id'], r['qty']))
    for r in q("SELECT id, kind FROM txns WHERE kind NOT IN ('进','出')"):
        issues.append('单据 #%s 进出类型异常（%s）' % (r['id'], r['kind']))
    for r in q("SELECT id, name, stock FROM v_stock WHERE stock < 0"):
        issues.append('【%s】库存为负（%g）——出库可能超过了入库' % (r['name'], r['stock']))
    dup = q("SELECT name, COUNT(*) c FROM materials WHERE active=1"
            " GROUP BY name HAVING c > 1")
    for r in dup:
        issues.append('物料名称重复：%s（%d 条）' % (r['name'], r['c']))
    dup2 = q("SELECT code, COUNT(*) c FROM materials WHERE active=1 AND code<>''"
             " GROUP BY code HAVING c > 1")
    for r in dup2:
        issues.append('料号重复：%s（%d 条）' % (r['code'], r['c']))
    return issues

def fix_orphans():
    """清掉指向已不存在物料的孤儿单据。返回清理条数"""
    n = q("SELECT COUNT(*) c FROM txns t LEFT JOIN materials m ON m.id=t.material_id"
          " WHERE m.id IS NULL")[0]['c']
    if n:
        run("DELETE FROM txns WHERE material_id NOT IN (SELECT id FROM materials)")
    return n

def backup(to_path=None, keep=10):
    """在线备份（用 SQLite 官方 backup API，拷出来的库一定是一致的）。
    同一秒重复备份会覆盖；只保留最近 keep 个，避免备份把磁盘塞满。"""
    if not to_path:
        base = DB_PATH + '.' + datetime.now().strftime('%Y%m%d_%H%M%S')
        to_path = base + '.bak'
        # 同一秒内重复备份会互相覆盖（比如程序反复重启），加序号保证每份都在，
        # 否则最后一份好备份可能被同名覆盖掉，真出事就没得还原了
        n = 1
        while os.path.exists(to_path):
            n += 1
            to_path = '%s_%d.bak' % (base, n)
    to_path = to_path
    src = conn()
    dst = sqlite3.connect(to_path)
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
    try:
        d = os.path.dirname(os.path.abspath(DB_PATH)) or '.'
        baks = sorted((f for f in os.listdir(d)
                       if f.startswith(os.path.basename(DB_PATH) + '.') and f.endswith('.bak')),
                      reverse=True)
        for old in baks[keep:]:
            try:
                os.remove(os.path.join(d, old))
            except OSError:
                pass
    except OSError:
        pass
    return to_path

def stats():
    """库概况"""
    g = lambda s, *a: q(s, *a)[0][0]
    return dict(
        materials=g("SELECT COUNT(*) FROM materials WHERE active=1"),
        all_materials=g("SELECT COUNT(*) FROM materials"),
        txns=g("SELECT COUNT(*) FROM txns"),
        first=g("SELECT MIN(tdate) FROM txns") or '—',
        last=g("SELECT MAX(tdate) FROM txns") or '—',
        alerts=g("SELECT COUNT(*) FROM v_stock WHERE stock<=safety"),
        size=os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0,
        path=DB_PATH,
    )

def aliases_map():
    """{系统字段: [别名...]}：自定义别名 + 自定义表头名，供 Excel 导入识别表头"""
    out = {}
    for r in q("SELECT fid, label, aliases FROM colmap"):
        al = [x.strip() for x in (r['aliases'] or '').split(',') if x.strip()]
        if r['label'] and r['label'] not in al:
            al.append(r['label'])
        out[r['fid']] = al or [r['fid']]
    return out
