import os, sqlite3, json, sys
from datetime import datetime

def _is_frozen():
    return getattr(sys, 'frozen', False)

# 常用单位字典：采购、仓库、物料三处共用同一份，避免"采购写卷、仓库写平米"对不上。
# 是"建议"不是"约束"——任何单位框都能直接手输新单位，输过一次就自动进候选。
UNITS = ['卷', '平米', '米', '张', '个', '支', '条', '片', '套', '只', '块', '根',
         'kg', 'g', '吨', '箱', '包', '桶', '袋', '台', '件', '双', '把', '罐']


def unit_choices(extra=None):
    """返回候选单位：常用字典 + 系统里实际用过的（物料档案/历史采购），去重保序。"""
    out = []
    seen = set()

    def add(u):
        u = (u or '').strip()
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    for u in UNITS:
        add(u)
    try:
        for r in q("SELECT DISTINCT unit FROM materials WHERE unit<>''"):
            add(r['unit'])
        for r in q("SELECT DISTINCT unit FROM po_items WHERE unit<>''"):
            add(r['unit'])
        for r in q("SELECT DISTINCT stock_unit FROM po_items WHERE stock_unit<>''"):
            add(r['stock_unit'])
        for r in q("SELECT DISTINCT unit FROM txns t JOIN materials m ON m.id=t.material_id"
                   " WHERE m.unit<>'' LIMIT 200"):
            add(r['unit'])
    except Exception:
        pass
    for u in (extra or []):
        add(u)
    return out


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
  price       REAL    DEFAULT NULL,
  note        TEXT    DEFAULT '',
  created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_txns_date ON txns(tdate);
CREATE INDEX IF NOT EXISTS idx_txns_mat  ON txns(material_id);
CREATE INDEX IF NOT EXISTS idx_txns_kind ON txns(kind);
CREATE INDEX IF NOT EXISTS idx_mat_active ON materials(active);

/* ============ 采购台账（与仓库单据分离，通过到货单联动） ============ */
CREATE TABLE IF NOT EXISTS suppliers (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  name     TEXT NOT NULL UNIQUE,
  contact  TEXT DEFAULT '',
  phone    TEXT DEFAULT '',
  address  TEXT DEFAULT '',
  note     TEXT DEFAULT '',
  active   INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS pos (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  pono       TEXT NOT NULL UNIQUE,        /* 采购单号 CG20260901-001 */
  supplier   TEXT NOT NULL,
  odate      TEXT NOT NULL,               /* 下单日期 */
  ddate      TEXT DEFAULT '',             /* 要求交期 */
  status     TEXT NOT NULL DEFAULT '草稿', /* 草稿/已下单/部分到货/已完成/已取消 */
  tax_rate   REAL NOT NULL DEFAULT 0,     /* 税率 % */
  price_tax  INTEGER NOT NULL DEFAULT 1,  /* 单价是否含税：1=含税 0=不含税 */
  tpl_id     INTEGER,                     /* 到货存到哪个库存模板 */
  note       TEXT DEFAULT '',
  created_at TEXT NOT NULL
);
/* 采购单汇总快照：金额原来全是现算的，查一次算一次。
   但月底对账、翻历史单时用户想直接看到"当时这单到底多少钱"，
   光靠现算还有个问题——税率后来被改了，历史金额也跟着变，对不上当时的凭证。
   所以每次变动后落一份快照，单独查看用。 */
CREATE TABLE IF NOT EXISTS po_summary (
  po_id        INTEGER PRIMARY KEY REFERENCES pos(id) ON DELETE CASCADE,
  qty          REAL NOT NULL DEFAULT 0,   /* 订购数量（采购单位） */
  amount       REAL NOT NULL DEFAULT 0,   /* 不含税金额 */
  tax          REAL NOT NULL DEFAULT 0,   /* 税额 */
  total        REAL NOT NULL DEFAULT 0,   /* 价税合计 */
  recv_qty     REAL NOT NULL DEFAULT 0,   /* 已到货数量（库存单位） */
  recv_amount  REAL NOT NULL DEFAULT 0,   /* 已到货不含税金额 */
  recv_total   REAL NOT NULL DEFAULT 0,   /* 已到货价税合计 */
  paid         REAL NOT NULL DEFAULT 0,   /* 已付 */
  owed         REAL NOT NULL DEFAULT 0,   /* 欠款 = 已到货价税合计 - 已付 */
  open_qty     REAL NOT NULL DEFAULT 0,   /* 未交数量（采购单位） */
  updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_posum_upd ON po_summary(updated_at);
CREATE TABLE IF NOT EXISTS po_items (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  po_id      INTEGER NOT NULL REFERENCES pos(id) ON DELETE CASCADE,
  material_id INTEGER REFERENCES materials(id),
  name       TEXT NOT NULL,               /* 采购时的名称（可能尚未建档） */
  spec       TEXT DEFAULT '',
  unit       TEXT DEFAULT '个',          /* 采购单位：可随意改，不影响已入库存量 */
  conv       REAL NOT NULL DEFAULT 1,    /* 换算率：1 采购单位 = conv 库存单位 */
  stock_unit TEXT DEFAULT '',            /* 库存单位，空=与采购单位相同 */
  qty        REAL NOT NULL,              /* 订购数量（采购单位） */
  price      REAL NOT NULL DEFAULT 0,     /* 含税/不含税单价（按单头税率） */
  recv_qty   REAL NOT NULL DEFAULT 0,     /* 累计到货数量（冗余，便于列表汇总） */
  note       TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS po_receipts (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id    INTEGER NOT NULL REFERENCES po_items(id) ON DELETE CASCADE,
  txn_id     INTEGER REFERENCES txns(id),  /* 生成的入库单，取消到货时可追溯 */
  rdate      TEXT NOT NULL,
  qty        REAL NOT NULL,
  price      REAL NOT NULL,               /* 本次到货的实际单价（可能与订购价不同） */
  note       TEXT DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS po_payments (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  po_id      INTEGER NOT NULL REFERENCES pos(id) ON DELETE CASCADE,
  pdate      TEXT NOT NULL,
  amount     REAL NOT NULL,
  method     TEXT DEFAULT '转账',
  note       TEXT DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pos_sup  ON pos(supplier);
CREATE INDEX IF NOT EXISTS idx_pos_st   ON pos(status);
CREATE INDEX IF NOT EXISTS idx_poi_po   ON po_items(po_id);
CREATE INDEX IF NOT EXISTS idx_por_item ON po_receipts(item_id);
CREATE INDEX IF NOT EXISTS idx_pop_po   ON po_payments(po_id);

CREATE TABLE IF NOT EXISTS colmap (
  fid     TEXT PRIMARY KEY,
  label   TEXT    NOT NULL,
  pos     INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1,
  aliases TEXT    DEFAULT ''
);

/* ============ 库存模板（v3.13）：一套列配置 = 一个模板 ============ */
CREATE TABLE IF NOT EXISTS tpl (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  name    TEXT    NOT NULL,
  note    TEXT    DEFAULT '',
  pos     INTEGER NOT NULL DEFAULT 0,
  created TEXT    DEFAULT '',
  sig     TEXT    DEFAULT ''   /* 列名指纹：表头列名规范化后排序拼接，用于自动归类 */
);
-- 每个模板自己的列配置（colmap 是老表，仅为兼容保留）
CREATE TABLE IF NOT EXISTS tpl_cols (
  tpl_id  INTEGER NOT NULL,
  fid     TEXT    NOT NULL,
  label   TEXT    NOT NULL,
  pos     INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1,
  aliases TEXT    DEFAULT '',
  xtype   TEXT    DEFAULT 'text',   /* text 文本 | number 数字 | date 日期 | select 单选 | calc 公式 */
  xopt    TEXT    DEFAULT '',       /* select 的选项，逗号分隔 */
  xform   TEXT    DEFAULT '',       /* calc 的公式，如 qty*price */
  PRIMARY KEY(tpl_id, fid)
);
"""

import threading
_local = threading.local()

# 进程内写锁：SQLite 同一时刻只允许一个写者。
# 多个线程（Web 并发请求）同时写时，在显式事务里 SQLite 不会等
# busy_timeout，而是立刻抛 "database is locked" —— 用户看到的就是"点保存突然 500"。
# 所以写操作先在进程内排队，从根上避免撞锁；跨进程（多开程序）再靠重试兜底。
_write_lock = threading.RLock()
_READ_PREFIX = ('select', 'pragma', 'explain', 'with')


def _is_write(sql):
    s = (sql or '').lstrip().lower()
    return bool(s) and not s.startswith(_READ_PREFIX)


def _busy_retry(fn, tries=60, sleep=0.05):
    """遇到 locked/busy 自动重试（跨进程场景，比如同时开了两个程序）"""
    import time as _t
    last = None
    for _ in range(tries):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            last = e
            msg = str(e).lower()
            if 'locked' in msg or 'busy' in msg:
                _t.sleep(sleep)
                continue
            raise
    raise last

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
        _write_lock.acquire()
        try:
            c = conn()
            self.depth = getattr(_local, 'depth', 0)
            if self.depth == 0:
                # IMMEDIATE 是关键：普通 BEGIN 属于"读事务起步"，
                # 中途要写才升级锁，这时若别人持有写锁，SQLite 不等
                # busy_timeout 而直接报 locked。IMMEDIATE 起步就取写锁，
                # 取不到就按 busy_timeout 等，于是能扛住真正的并发。
                _busy_retry(lambda: c.execute("BEGIN IMMEDIATE"))
            else:
                c.execute("SAVEPOINT sp%s" % self.depth)
            _local.depth = self.depth + 1
        except Exception:
            _write_lock.release()
            raise
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
        finally:
            # 锁必须放：一次异常没放锁，后面所有写操作会永久卡死
            try:
                _write_lock.release()
            except RuntimeError:
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
    # 采购表对老库是全新的，CREATE TABLE IF NOT EXISTS 会自动补上；
    # 但 conv/stock_unit 是后加的，老采购库要单独 ALTER
    if 'po_items' in [r['name'] for r in q("SELECT name FROM sqlite_master WHERE type='table'")]:
        for col, ddl in (('conv', 'REAL NOT NULL DEFAULT 1'),
                         ('stock_unit', "TEXT DEFAULT ''")):
            if col not in _cols('po_items'):
                run("ALTER TABLE po_items ADD COLUMN %s %s" % (col, ddl))
    # price_tax 是后加的：老采购单按"单价含税"处理（与原逻辑一致，不翻旧账）
    if 'pos' in [r['name'] for r in q("SELECT name FROM sqlite_master WHERE type='table'")]:
        if 'price_tax' not in _cols('pos'):
            run("ALTER TABLE pos ADD COLUMN price_tax INTEGER NOT NULL DEFAULT 1")

    for col, ddl in (('pieces', 'REAL'), ('per_piece', 'REAL'), ('price', 'REAL')):
        if col not in _cols('txns'):
            run("ALTER TABLE txns ADD COLUMN %s %s" % (col, ddl))
    # 长宽→平米→卷料：老库要补这两个数值列（materials 存档、txns 记每笔）
    for tb in ('materials', 'txns'):
        for col in NUM_EXTRA_COLS:
            if col not in _cols(tb):
                run("ALTER TABLE %s ADD COLUMN %s REAL" % (tb, col))
    # v3.13 模板制：老库没有 tpl_id，要补列并给一个默认模板
    for tb in ('materials', 'txns'):
        if 'tpl_id' not in _cols(tb):
            run("ALTER TABLE %s ADD COLUMN tpl_id INTEGER" % tb)
    if 'tpl_id' not in _cols('pos'):
        run("ALTER TABLE pos ADD COLUMN tpl_id INTEGER")
    if 'sig' not in _cols('tpl'):
        run("ALTER TABLE tpl ADD COLUMN sig TEXT DEFAULT ''")
    if 'xtype' not in _cols('tpl_cols'):
        run("ALTER TABLE tpl_cols ADD COLUMN xtype TEXT DEFAULT 'text'")
    if 'xopt' not in _cols('tpl_cols'):
        run("ALTER TABLE tpl_cols ADD COLUMN xopt TEXT DEFAULT ''")
    if 'xform' not in _cols('tpl_cols'):
        run("ALTER TABLE tpl_cols ADD COLUMN xform TEXT DEFAULT ''")
    # 自定义列的值存在 extra(JSON)，不动表结构
    if 'extra' not in _cols('materials'):
        run("ALTER TABLE materials ADD COLUMN extra TEXT DEFAULT ''")
    if 'extra' not in _cols('txns'):
        run("ALTER TABLE txns ADD COLUMN extra TEXT DEFAULT ''")
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
    _migrate_tpl()


def _migrate_tpl():
    """老库 → 模板制：
    1) 没有模板就建一个「通用库存」，把老 colmap 配置原样搬进去（名字一字不改）
    2) materials / txns 里 tpl_id 为空的老数据，全部归入这个默认模板
    """
    if not q("SELECT COUNT(*) c FROM tpl")[0]['c']:
        tid = run("INSERT INTO tpl(name,note,pos,created) VALUES(?,?,?,?)",
                  '通用库存', '老数据自动归入', 1,
                  datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    else:
        tid = q("SELECT id FROM tpl ORDER BY pos, id LIMIT 1")[0]['id']
    # 老 colmap → 默认模板的列配置（只搬一次）
    if not q("SELECT COUNT(*) c FROM tpl_cols WHERE tpl_id=?", tid)[0]['c']:
        old = q("SELECT fid,label,pos,enabled,aliases FROM colmap ORDER BY pos")
        if old:
            for r in old:
                run("INSERT OR IGNORE INTO tpl_cols(tpl_id,fid,label,pos,enabled,aliases)"
                    " VALUES(?,?,?,?,?,?)", tid, r['fid'], r['label'], r['pos'],
                    r['enabled'], r['aliases'])
            # 老表里没有的新列（长/宽/平米/卷料）补齐，默认关闭
            for fid, label, pos, en, al in DEFAULT_COLS:
                if not q("SELECT 1 FROM tpl_cols WHERE tpl_id=? AND fid=?", tid, fid):
                    run("INSERT INTO tpl_cols(tpl_id,fid,label,pos,enabled,aliases)"
                        " VALUES(?,?,?,?,?,?)", tid, fid, label, pos, en, al)
        else:
            reset_tpl_cols(tid)
    # 老数据归入默认模板
    run("UPDATE materials SET tpl_id=? WHERE tpl_id IS NULL", tid)
    run("UPDATE txns SET tpl_id=? WHERE tpl_id IS NULL", tid)
    run("UPDATE pos SET tpl_id=? WHERE tpl_id IS NULL", tid)
    return tid

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

    并发：写 SQL 先拿进程内写锁，被别的进程锁住则自动重试。
    """
    if _is_write(sql):
        with _write_lock:
            return _busy_retry(lambda: _run_now(sql, a))
    return _run_now(sql, a)


def _run_now(sql, a):
    c = conn()
    cur = c.execute(sql, _flat(a))
    if getattr(_local, 'depth', 0) == 0:
        c.commit()
    return cur.lastrowid

def runmany(sql, seq):
    """批量执行。返回影响行数"""
    with _write_lock:
        return _busy_retry(lambda: _runmany_now(sql, seq))


def _runmany_now(sql, seq):
    c = conn()
    cur = c.executemany(sql, seq)
    if getattr(_local, 'depth', 0) == 0:
        c.commit()
    return cur.rowcount

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
    # 默认只保留 供应商 / 类型 / 状态（外加录单必需的物料名称），
    # 其余全部默认关闭 —— 使用者在「改表头」里按需启用，不再绑定原 Excel 的 A-G 七列。
    ('supplier', '供应商', 1, 1, '供应商,厂商,供货商,供方'),
    ('category', '类型', 2, 1, '类型,类别,分类,大类,品种'),
    ('status',   '状态', 3, 1, '状态,使用状态'),
    ('name',     '物料名称', 4, 1, '物料名称,名称,品名,品名规格,物料,材料名称'),
    # ↓ 使用者按需启用：长/宽 是输入项，平米/卷料 是自动算出来的
    ('spec',  '长',   5, 0, '长,长度,长(米),长（米）,规格,规格（米）,规格(米),厚度,米数'),
    ('width', '宽',   6, 0, '宽,宽幅,宽度,幅宽,宽(米),宽（米）'),
    ('sqm',   '平米', 7, 0, '平米,平方米,面积,平方,m2,m²'),
    ('rolls', '卷料', 8, 0, '卷料,卷,卷数,米数'),
    ('code',  '料号', 9, 0, '料号,物料编号,物料编码,编号,编码,型号,规格型号'),
    ('unit',  '单位', 10, 0, '单位,计量单位,单位（卷）,单位(卷)'),
    ('opening', '期初结存', 11, 0, '期初结存,期初,上月结存,上期结存,库存,当前库存'),
    ('safety',  '安全库存', 12, 0, '安全库存,预警值,库存预警,最低库存'),
]

# 计算列：值由别的列算出来，不手填。
# sqm   = 长 × 宽
# rolls = 平米 ÷ 宽   （向下取整；除不尽的余料自动写进备注）
CALC_COLS = {'sqm': ('spec', 'width'), 'rolls': ('sqm', 'width')}

# 需要存进数据库的数值列（老库升级时要补）
NUM_EXTRA_COLS = ('sqm', 'rolls')

def calc_area(vals):
    """长 × 宽 = 平米；平米 ÷ 宽 = 卷料（取整，余料写进备注）。

    只在启用且填了长、宽时才算；算不出来就保持原值，绝不瞎填 0。
    返回 (sqm, rolls, rest)，任一算不出就是 None。
    """
    def f(v):
        try:
            x = float(str(v or '').strip())
        except (TypeError, ValueError):
            return None
        return x if x > 0 else None
    L = f(vals.get('spec'))      # 长
    W = f(vals.get('width'))     # 宽
    sqm = rolls = rest = None
    if L is not None and W is not None:
        sqm = round(L * W, 6)
        # 卷料 = 平米 ÷ 宽，向下取整；除不尽的部分是余料
        raw = sqm / W
        rolls = int(raw + 1e-9)          # 1e-9 抵消浮点误差，避免 3.0 算成 2
        rest = round(sqm - rolls * W, 6)
        if rest < 1e-6:
            rest = None
    return sqm, rolls, rest


def rest_note(rest, unit='平米'):
    """余料备注文案"""
    if not rest:
        return ''
    return '余料 %g %s' % (round(rest, 4), unit)

# ---------- 列名指纹：列名完全相同的表自动归为一类 ----------
import re as _re
def head_sig(headers):
    """把表头列名变成一个指纹：去空格/括号/全角，排序后拼接。
    两张表只要列名一样（顺序、写法不同也算），指纹就相同。"""
    out = []
    for h in (headers or []):
        t = str(h or '').strip()
        t = _re.sub(r'[（(].*?[)）]', '', t)          # 去掉括号及其内容
        t = _re.sub(r'[\s\u3000]+', '', t)           # 去空格/全角空格
        t = t.replace('：', ':').replace('，', ',')
        if t:
            out.append(t)
    return '|'.join(sorted(set(out)))

def tpl_by_sig(sig):
    """按列名指纹找模板；找不到返回 None"""
    if not sig:
        return None
    r = q("SELECT * FROM tpl WHERE sig=? ORDER BY pos, id LIMIT 1", sig)
    return r[0] if r else None

def set_tpl_sig(tid, sig):
    run("UPDATE tpl SET sig=? WHERE id=?", sig or '', tid)

def tpl_from_headers(headers, name=None, keep=0):
    """用一张表的表头直接建模板：每个列名成为该模板的一个启用列。
    keep=1 时同时保留系统默认列（长/宽/平米/卷料等），便于勾选启用。"""
    seen, cols = set(), []
    for i, h in enumerate(headers or []):
        t = str(h or '').strip()
        if not t or t in seen:
            continue
        seen.add(t)
        cols.append((t, i))
    tid = add_tpl(name or '新表', '由表头自动生成', copy_from=None)
    run("DELETE FROM tpl_cols WHERE tpl_id=?", tid)
    pos = 0
    for label, _ in cols:
        run("INSERT INTO tpl_cols(tpl_id,fid,label,pos,enabled,aliases)"
            " VALUES(?,?,?,?,?,?)", tid, 'x%d' % pos, label, pos, 1, label)
        pos += 1
    if keep:
        for fid, label, _p, _en, al in DEFAULT_COLS:
            if label in seen:
                continue
            run("INSERT INTO tpl_cols(tpl_id,fid,label,pos,enabled,aliases)"
                " VALUES(?,?,?,?,?,?)", tid, fid, label, pos, 0, al)
            pos += 1
    set_tpl_sig(tid, head_sig(headers))
    return tid

# ---------- 自定义列 / 字段类型 / 公式 ----------
import ast as _ast
# 公式里能用的：引用别的列（写 fid 或列名的拼音/英文标识）、四则运算、几个常用函数
# 例：qty*price   (qty*price)*1.13   round(qty*price,2)
_FORM_FUNCS = {
    'round': round, 'abs': abs, 'min': min, 'max': max,
    'int': int, 'float': float, 'len': len, 'sum': sum,
}
_ALLOWED = (_ast.Expression, _ast.BinOp, _ast.UnaryOp, _ast.Num, _ast.Constant,
            _ast.Name, _ast.Load, _ast.Add, _ast.Sub, _ast.Mult, _ast.Div,
            _ast.Pow, _ast.Mod, _ast.USub, _ast.UAdd, _ast.Call, _ast.keyword,
            _ast.Tuple, _ast.List)

def calc_formula(expr, values):
    """算公式。只放行四则运算和几个函数，**不用 eval**，防注入。
    expr  : 公式字符串，如 'qty*price'
    values: {fid: 数值/文本}
    算不出来返回 None（不抛异常）。"""
    if not (expr or '').strip():
        return None
    try:
        tree = _ast.parse(expr.strip(), mode='eval')
    except (SyntaxError, ValueError):
        return None
    for n in _ast.walk(tree):
        if not isinstance(n, _ALLOWED):
            return None                      # 出现属性访问、下标、赋值等一律拒绝
        if isinstance(n, _ast.Call):
            f = n.func
            if not (isinstance(f, _ast.Name) and f.id in _FORM_FUNCS):
                return None
    env = {}
    for k, v in (values or {}).items():
        try:
            env[str(k)] = float(v) if v not in (None, '') else 0.0
        except (TypeError, ValueError):
            env[str(k)] = 0.0
    env.update(_FORM_FUNCS)
    try:
        out = eval(compile(tree, '<formula>', 'eval'),
                   {'__builtins__': {}}, env)
    except ZeroDivisionError:
        return None
    except Exception:
        return None
    try:
        f = float(out)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float('inf'), float('-inf')):
        return None
    return round(f, 6)

def get_extra(row, key, default=''):
    """从 extra(JSON) 里取自定义列的值"""
    import json as _json
    raw = None
    try:
        raw = row['extra'] if 'extra' in row.keys() else None
    except (IndexError, TypeError, KeyError):
        raw = None
    if not raw:
        return default
    try:
        d = _json.loads(raw)
    except Exception:
        return default
    return d.get(key, default)

def set_extra(tbl, rid, key, val):
    """写自定义列的值到 extra(JSON)"""
    import json as _json
    row = q("SELECT extra FROM %s WHERE id=?" % tbl, rid)
    d = {}
    if row:
        try:
            d = _json.loads(row[0]['extra'] or '{}') or {}
        except Exception:
            d = {}
    d[str(key)] = val
    run("UPDATE %s SET extra=? WHERE id=?" % tbl, _json.dumps(d, ensure_ascii=False), rid)

def tpl_custom_cols(tpl_id, enabled_only=True):
    """该模板的自定义列（fid 以 x_ 开头）"""
    sql = "SELECT * FROM tpl_cols WHERE tpl_id=? AND fid LIKE 'x_%'"
    if enabled_only:
        sql += " AND enabled=1"
    return q(sql + " ORDER BY pos, fid", tpl_id)

def add_custom_col(tpl_id, label, xtype='text', xopt='', xform=''):
    """新增自定义列。fid 用 x_<时间戳> 保证唯一"""
    import time as _t
    fid = 'x_%d' % int(_t.time() * 1000)
    mx = q("SELECT COALESCE(MAX(pos),0) p FROM tpl_cols WHERE tpl_id=?", tpl_id)[0]['p']
    run("INSERT INTO tpl_cols(tpl_id,fid,label,pos,enabled,aliases,xtype,xopt,xform)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        tpl_id, fid, (label or '').strip() or '新列', mx + 1, 1,
        (label or '').strip(), xtype or 'text', xopt or '', xform or '')
    return fid

def del_custom_col(tpl_id, fid):
    """删自定义列（连带清掉已存的值）"""
    if not str(fid or '').startswith('x_'):
        return False
    run("DELETE FROM tpl_cols WHERE tpl_id=? AND fid=?", tpl_id, fid)
    import json as _json
    for tbl in ('materials', 'txns'):
        for r in q("SELECT id, extra FROM %s WHERE extra IS NOT NULL AND extra<>''" % tbl):
            try:
                d = _json.loads(r['extra'] or '{}') or {}
            except Exception:
                continue
            if fid in d:
                d.pop(fid, None)
                run("UPDATE %s SET extra=? WHERE id=?" % tbl,
                    _json.dumps(d, ensure_ascii=False), r['id'])
    return True

# ---------- 库存模板 ----------
def tpls():
    """全部模板（按排序）"""
    return q("SELECT * FROM tpl ORDER BY pos, id")

def tpl(tid):
    r = q("SELECT * FROM tpl WHERE id=?", tid)
    return r[0] if r else None

def default_tpl_id():
    """第一个模板；没有就建一个（老库升级时用）"""
    r = q("SELECT id FROM tpl ORDER BY pos, id LIMIT 1")
    return r[0]['id'] if r else None

def add_tpl(name, note='', copy_from=None):
    """新建模板。copy_from 给定时复制该模板的列配置"""
    name = (name or '').strip() or '未命名模板'
    mx = q("SELECT COALESCE(MAX(pos),0) p FROM tpl")[0]['p']
    tid = run("INSERT INTO tpl(name,note,pos,created) VALUES(?,?,?,?)",
              name, note, mx + 1,
              datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    if copy_from:
        for r in q("SELECT fid,label,pos,enabled,aliases FROM tpl_cols WHERE tpl_id=?", copy_from):
            run("INSERT INTO tpl_cols(tpl_id,fid,label,pos,enabled,aliases)"
                " VALUES(?,?,?,?,?,?)", tid, r['fid'], r['label'], r['pos'],
                r['enabled'], r['aliases'])
    else:
        for fid, label, pos, en, al in DEFAULT_COLS:
            run("INSERT INTO tpl_cols(tpl_id,fid,label,pos,enabled,aliases)"
                " VALUES(?,?,?,?,?,?)", tid, fid, label, pos, en, al)
    return tid

def rename_tpl(tid, name, note=None):
    name = (name or '').strip()
    if not name:
        return False
    if note is None:
        run("UPDATE tpl SET name=? WHERE id=?", name, tid)
    else:
        run("UPDATE tpl SET name=?,note=? WHERE id=?", name, note, tid)
    return True

def del_tpl(tid):
    """删模板。有物料/单据的拒绝删（避免数据变成孤儿）"""
    nm = q("SELECT COUNT(*) c FROM materials WHERE COALESCE(tpl_id,0)=?", tid)[0]['c']
    nt = q("SELECT COUNT(*) c FROM txns WHERE COALESCE(tpl_id,0)=?", tid)[0]['c']
    if nm or nt:
        return False, '这个模板下还有 %d 种物料、%d 条单据，不能删。可以先改用别的模板。' % (nm, nt)
    if q("SELECT COUNT(*) c FROM tpl")[0]['c'] <= 1:
        return False, '至少要留一个模板'
    run("DELETE FROM tpl_cols WHERE tpl_id=?", tid)
    run("DELETE FROM tpl WHERE id=?", tid)
    return True, '已删除'

def tpl_cols(tid, only_enabled=True):
    sql = "SELECT * FROM tpl_cols WHERE tpl_id=?"
    if only_enabled:
        sql += " AND enabled=1"
    return q(sql + " ORDER BY pos", tid)

def save_tpl_cols(tid, rows):
    with tx() as c:
        c.executemany("UPDATE tpl_cols SET label=?,pos=?,enabled=?,aliases=?,"
                      " xtype=?,xopt=?,xform=?"
                      " WHERE tpl_id=? AND fid=?",
                      [(r['label'], r['pos'], r['enabled'], r['aliases'],
                        r.get('xtype') or 'text', r.get('xopt') or '',
                        r.get('xform') or '', tid, r['fid'])
                       for r in rows])
    return True

def reset_tpl_cols(tid):
    run("DELETE FROM tpl_cols WHERE tpl_id=?", tid)
    for fid, label, pos, en, al in DEFAULT_COLS:
        run("INSERT INTO tpl_cols(tpl_id,fid,label,pos,enabled,aliases)"
            " VALUES(?,?,?,?,?,?)", tid, fid, label, pos, en, al)

def tpl_stat(tid):
    """模板下的物料数、单据数"""
    nm = q("SELECT COUNT(*) c FROM materials WHERE COALESCE(tpl_id,0)=?", tid)[0]['c']
    nt = q("SELECT COUNT(*) c FROM txns WHERE COALESCE(tpl_id,0)=?", tid)[0]['c']
    return nm, nt

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
    # 只按名称判重会误报：原表里「0.05金」本来就有 0.04 / 0.045 两种宽幅，
    # 它们是不同物料。真正有问题的是「名称+规格+宽幅」完全一样却建了两条。
    dup = q("SELECT name, COALESCE(spec,'') sp, COALESCE(width,'') wd, COUNT(*) c"
            " FROM materials WHERE active=1"
            " GROUP BY name, COALESCE(spec,''), COALESCE(width,'') HAVING c > 1")
    for r in dup:
        tag = r['name'] + ((' 规格' + r['sp']) if r['sp'] else '') \
                        + ((' 宽' + r['wd']) if r['wd'] else '')
        issues.append('物料重复：%s（%d 条，规格完全一样，建议合并）' % (tag, r['c']))
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

def aliases_map(tpl_id=None):
    """{系统字段: [别名...]}：自定义别名 + 自定义表头名，供 Excel 导入识别表头。

    模板制之后列配置存在 tpl_cols，colmap 是老表（v3.13 起不再更新）。
    这里必须优先读模板，否则使用者在模板里自己起的表头名，导入时认不出来。"""
    rows = []
    if tpl_id:
        rows = q("SELECT fid, label, aliases FROM tpl_cols WHERE tpl_id=?", tpl_id)
    if not rows:
        rows = [r for r in q("SELECT fid, label, aliases FROM tpl_cols")]
    if not rows:
        rows = q("SELECT fid, label, aliases FROM colmap")
    out = {}
    for r in rows:
        al = [x.strip() for x in (r['aliases'] or '').split(',') if x.strip()]
        if r['label'] and r['label'] not in al:
            al.append(r['label'])
        # 同名不同列都收进来，别互相覆盖
        old = out.get(r['fid']) or []
        for x in al:
            if x not in old:
                old.append(x)
        out[r['fid']] = old or [r['fid']]
    return out
