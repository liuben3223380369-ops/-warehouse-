# -*- coding: utf-8 -*-
"""表结构：视图与建表 SQL、字段迁移、初始化。
初始化需要默认列定义与采购模板，故依赖 tpl / potpl。"""
import os, sqlite3, json, sys
from datetime import datetime
from .paths import (app_dir, res_dir, BASE, DB_PATH, SEED, UNITS,
                   unit_choices, _is_frozen, _writable, _user_data_dir)
from .dbconn import (conn, tx, close, q, run, runmany, _flat, _cols,
                    _run_now, _runmany_now, _write_lock, _is_write, _busy_retry)
from .tpl import (DEFAULT_COLS, NUM_EXTRA_COLS, preset_col_units, reset_tpl_cols)
from .potpl import _ensure_po_tpl, _fix_po_tpl_cols

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
  created_at  TEXT    NOT NULL,
  batch       TEXT    DEFAULT ''           /* v3.35 批次号，用于匹配采购实价 */
);
CREATE INDEX IF NOT EXISTS idx_txns_date ON txns(tdate);
CREATE INDEX IF NOT EXISTS idx_txns_mat  ON txns(material_id);
CREATE INDEX IF NOT EXISTS idx_txns_kind ON txns(kind);
CREATE INDEX IF NOT EXISTS idx_mat_active ON materials(active);
-- v3.65 覆盖索引：库存聚合与期间统计只扫索引、不回表读数据。
-- 没有它时 v_stock 与 _period_stats 每次都要全表扫描（5 万单据下 3 秒级），
-- 且 LIMIT 分页完全失效（LIMIT 50 比取全量还慢）。加上后降到 60~90ms。
CREATE INDEX IF NOT EXISTS idx_txns_cover ON txns(material_id, tdate, kind, qty);

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
  potpl_id   INTEGER,                     /* v3.35 用的采购模板（决定明细填哪些列） */
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
  note       TEXT DEFAULT '',
  sig        TEXT DEFAULT '',             /* v3.34 明细指纹：供应商+名称+规格+单位 */
  batch      TEXT DEFAULT ''              /* v3.35 批次号：入库时填它就能对上这条明细 */
);
CREATE TABLE IF NOT EXISTS po_receipts (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id    INTEGER NOT NULL REFERENCES po_items(id) ON DELETE CASCADE,
  txn_id     INTEGER REFERENCES txns(id),  /* 生成的入库单，取消到货时可追溯 */
  rdate      TEXT NOT NULL,
  qty        REAL NOT NULL,
  price      REAL NOT NULL,               /* 本次到货的实际单价（可能与订购价不同） */
  note       TEXT DEFAULT '',
  created_at TEXT NOT NULL,
  batch      TEXT DEFAULT '',             /* v3.35 批次号，入库时填同一个号就能对上价 */
  src        TEXT DEFAULT 'manual'         /* manual=采购页手登, txn=入库页录入同步过来 */
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
/* v3.35 采购模板：采购页自己建表（列配置），跟库存模板完全分开。
   为什么分开：库存模板改列（比如关掉"长宽"）不该牵连采购单，两边用途不同。 */
CREATE TABLE IF NOT EXISTS po_tpl (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  name      TEXT NOT NULL,
  note      TEXT DEFAULT '',
  sig       TEXT DEFAULT '',          /* 列名指纹：采购单导入时自动归到同列名的模板 */
  ord       INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS po_tpl_cols (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  tpl_id   INTEGER NOT NULL REFERENCES po_tpl(id) ON DELETE CASCADE,
  fid      TEXT NOT NULL,
  label    TEXT NOT NULL,
  unit     TEXT DEFAULT '',
  enabled  INTEGER NOT NULL DEFAULT 1,
  pos      INTEGER NOT NULL DEFAULT 0,
  aliases  TEXT DEFAULT '',
  xtype    TEXT DEFAULT 'text',
  xopt     TEXT DEFAULT '',
  xform    TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_potplc_t ON po_tpl_cols(tpl_id);

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
  unit    TEXT    DEFAULT '',       /* 这一列的单位，如 米/平米/卷；空=不带单位 */
  PRIMARY KEY(tpl_id, fid)
);

/* 电子表格模块：自建的工作簿（表格模块 · 类 Excel 的制表页） */
CREATE TABLE IF NOT EXISTS wb (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  name     TEXT    NOT NULL,
  data     TEXT    NOT NULL,        /* 整本工作簿的 JSON */
  updated  TEXT
);

/* ============ 业务模块 ↔ 电子表格 绑定（v3.100） ============
   采购/入库/出库下单后，把数据回写到用户在制表模块做好的那张表。
   绑定记到「模块 + 工作簿 + 工作表」这一层：换表再换回来，
   上次配好的列映射还在，不会退回默认表头。 */
CREATE TABLE IF NOT EXISTS sheet_bind (
  module   TEXT    NOT NULL,        /* po 采购 | in 入库 | out 出库 */
  wb_id    INTEGER NOT NULL,
  sh_name  TEXT    NOT NULL,
  mapping  TEXT    NOT NULL DEFAULT '{}',  /* JSON {字段: 列号}，-1 = 不写入 */
  is_cur   INTEGER NOT NULL DEFAULT 0,     /* 1 = 该模块当前正在用这一本 */
  updated  TEXT,
  PRIMARY KEY(module, wb_id, sh_name)
);

/* ============ 填写模块「显示哪些列」（v3.106） ============
   采购 / 入库 / 出库的表单只做两件事：填值、把值写进表格。
   所以表单上的字段 ≡ 可以映射到表格的字段（同一套 fid）。
   这里记每个模块哪些字段出现在表单上：屏蔽掉的既不显示，也不参与映射，
   更不会写进表格 —— 一次屏蔽，两处同时生效。 */
CREATE TABLE IF NOT EXISTS ui_field (
  module  TEXT    NOT NULL,        /* po 采购 | in 入库 | out 出库 */
  fid     TEXT    NOT NULL,        /* 字段，与 sheet/bridge.py 的 FIELDS 同一套 */
  hide    INTEGER NOT NULL DEFAULT 0,   /* 1 = 屏蔽 */
  ord     INTEGER NOT NULL DEFAULT 0,   /* 显示顺序 */
  PRIMARY KEY(module, fid)
);

/* ============ 盘点（与出入库单据分离，调整时才生成单据） ============
   账实核对的标准流程：先按账面数生成盘点表 → 现场盲盘填实盘数 →
   算差异 → 确认后按差异生成盘盈入库 / 盘亏出库单据。
   盘点单本身不直接改库存，只有「调整」这一步才动账，
   这样盘点期间可以继续出入库，也留得下完整的审计痕迹。 */
CREATE TABLE IF NOT EXISTS stk (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  sno        TEXT    NOT NULL UNIQUE,   /* 盘点单号 PD20260916-001 */
  sdate      TEXT    NOT NULL,          /* 盘点日期（基准日） */
  tpl_id     INTEGER,                   /* 盘点范围：按模板筛物料 */
  scope      TEXT    DEFAULT '',        /* 范围说明：A区 / 全部 / 卷料类 */
  counter    TEXT    DEFAULT '',        /* 盘点人 */
  checker    TEXT    DEFAULT '',        /* 监盘人（复核） */
  status     TEXT    DEFAULT '草稿',    /* 草稿 / 已盘点 / 已调整 / 已取消 */
  note       TEXT    DEFAULT '',
  created_at TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS stk_items (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  stk_id      INTEGER NOT NULL REFERENCES stk(id) ON DELETE CASCADE,
  material_id INTEGER NOT NULL REFERENCES materials(id),
  book_qty    REAL    NOT NULL DEFAULT 0,   /* 账面数（生成盘点表那一刻的库存） */
  real_qty    REAL    DEFAULT NULL,         /* 实盘数（NULL=还没盘） */
  price       REAL    DEFAULT 0,            /* 单位成本，算差异金额用 */
  reason      TEXT    DEFAULT '',           /* 差异原因：出库漏录 / 自然损耗 / 供应商多送… */
  handle      TEXT    DEFAULT '',           /* 处理意见：补录单据 / 调整账面 / 报损 */
  note        TEXT    DEFAULT '',
  txn_id      INTEGER,                      /* 调整时生成的那笔单据，撤销时按它删 */
  sort        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_stk_items ON stk_items(stk_id);
"""


def migrate():
    """老库平滑升级：补齐后加的列，并按需重建视图/索引。"""
    # 采购表对老库是全新的，CREATE TABLE IF NOT EXISTS 会自动补上；
    # 但 conv/stock_unit 是后加的，老采购库要单独 ALTER
    if 'po_items' in [r['name'] for r in q("SELECT name FROM sqlite_master WHERE type='table'")]:
        for col, ddl in (('conv', 'REAL NOT NULL DEFAULT 1'),
                         ('stock_unit', "TEXT DEFAULT ''"),
                         # v3.34 采购明细指纹：供应商+物料名+规格+单位 归一化后的签名，
                         # 流水页靠它把采购价映射到对应的出入库单据上。
                         ('sig', "TEXT DEFAULT ''")):
            if col not in _cols('po_items'):
                run("ALTER TABLE po_items ADD COLUMN %s %s" % (col, ddl))
    # price_tax 是后加的：老采购单按"单价含税"处理（与原逻辑一致，不翻旧账）
    if 'pos' in [r['name'] for r in q("SELECT name FROM sqlite_master WHERE type='table'")]:
        if 'price_tax' not in _cols('pos'):
            run("ALTER TABLE pos ADD COLUMN price_tax INTEGER NOT NULL DEFAULT 1")

    for col, ddl in (('pieces', 'REAL'), ('per_piece', 'REAL'), ('price', 'REAL')):
        if col not in _cols('txns'):
            run("ALTER TABLE txns ADD COLUMN %s %s" % (col, ddl))
    # 列级单位（v3.21）：老库的 tpl_cols 没有 unit，补上才能给每列配单位
    if 'tpl_cols' in [r['name'] for r in q("SELECT name FROM sqlite_master WHERE type='table'")]:
        if 'unit' not in _cols('tpl_cols'):
            run("ALTER TABLE tpl_cols ADD COLUMN unit TEXT DEFAULT ''")
        # 单位不再单独占一列（v3.21）：老模板里的「单位」列删掉，
        # 改用每一列自己配单位。老物料档案里的 unit 字段保留（历史数据不动）。
        run("DELETE FROM tpl_cols WHERE fid='unit'")
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
    # 数量口径（按平米 / 按卷）：记在物料上当默认值，单据上也留一份，
    # 保证改了物料口径后，历史单据还按当时口径显示。
    if 'qty_unit' not in _cols('materials'):
        run("ALTER TABLE materials ADD COLUMN qty_unit TEXT DEFAULT '平米'")
    if 'qty_unit' not in _cols('txns'):
        run("ALTER TABLE txns ADD COLUMN qty_unit TEXT DEFAULT ''")
    # v3.35 批次：采购到货登记批次号，出入库单据填同一批次，
    # 就能精确匹配到"这一批"的实价，而不是笼统用该物料的最新采购价。
    if 'batch' not in _cols('txns'):
        run("ALTER TABLE txns ADD COLUMN batch TEXT DEFAULT ''")
    if 'batch' not in _cols('po_receipts'):
        run("ALTER TABLE po_receipts ADD COLUMN batch TEXT DEFAULT ''")
    if 'batch' not in _cols('po_items'):
        run("ALTER TABLE po_items ADD COLUMN batch TEXT DEFAULT ''")
    # 采购明细的自定义列值（JSON）与它用的采购模板
    if 'extra' not in _cols('po_items'):
        run("ALTER TABLE po_items ADD COLUMN extra TEXT DEFAULT ''")
    _fix_po_tpl_cols()
    if 'potpl_id' not in _cols('pos'):
        run("ALTER TABLE pos ADD COLUMN potpl_id INTEGER")
    if 'ptpl_id' not in _cols('po_items'):
        run("ALTER TABLE po_items ADD COLUMN ptpl_id INTEGER")
    # 来源：manual=采购页手工登记, txn=入库页按批次录入同步过来的
    if 'src' not in _cols('po_receipts'):
        run("ALTER TABLE po_receipts ADD COLUMN src TEXT DEFAULT 'manual'")
    # v3.103 采购明细按「列」记长宽平米卷料：
    # 卷料是「采购按平米、库存按平米+卷」这条业务线的核心口径，
    # 之前只有 spec(长)/unit/conv，宽和卷数没处存，只能靠换算率反推，对不上账。
    # 与 materials / txns 用同一套列名，导入导出与回写表格都能直接对齐。
    for _col, _ddl in (('width', "TEXT DEFAULT ''"), ('sqm', 'REAL'),
                       ('rolls', 'REAL'), ('qty_unit', "TEXT DEFAULT '平米'")):
        if _col not in _cols('po_items'):
            run("ALTER TABLE po_items ADD COLUMN %s %s" % (_col, _ddl))
    # 视图改成聚合 JOIN 后，老库里的旧视图不会自动更新，这里重建
    old = q("SELECT sql FROM sqlite_master WHERE type='view' AND name='v_stock'")
    if old and ('COALESCE(a.i' not in (old[0]['sql'] or '')
                or 'ROUND(' not in (old[0]['sql'] or '')):
        with tx() as c:
            c.execute("DROP VIEW IF EXISTS v_stock")
            c.execute("DROP VIEW IF EXISTS v_mats")
            c.executescript(VIEWS)
    # v3.65 覆盖索引：老库升级也要有，否则 5 万单据下物料页/首页仍是秒级。
    # 新库由建表脚本创建，这里只补老库；IF NOT EXISTS 保证重复执行安全。
    for idx, ddl in (('idx_txns_kind', "CREATE INDEX IF NOT EXISTS idx_txns_kind ON txns(kind)"),
                     ('idx_mat_active', "CREATE INDEX IF NOT EXISTS idx_mat_active ON materials(active)"),
                     ('idx_txns_cover', "CREATE INDEX IF NOT EXISTS idx_txns_cover ON txns(material_id, tdate, kind, qty)")):
        run(ddl)
    _migrate_tpl()


def _patch_unit_aliases(tid):
    """给配了单位的列补「列名（单位）」别名。

    界面上表头显示成「长（米）」，使用者照着做 Excel 时表头就会这么写；
    导入必须能认，否则这一列匹配不上、值静默丢失（单据建了但字段空）。
    """
    for r in q("SELECT fid,label,unit,aliases FROM tpl_cols WHERE tpl_id=? AND unit<>''", tid):
        lb = (r['label'] or '').strip()
        u = (r['unit'] or '').strip()
        if not lb or not u:
            continue
        parts = [x.strip() for x in (r['aliases'] or '').split(',') if x.strip()]
        changed = False
        for w in ('%s（%s）' % (lb, u), '%s(%s)' % (lb, u)):
            if w not in parts:
                parts.append(w); changed = True
        if changed:
            run("UPDATE tpl_cols SET aliases=? WHERE tpl_id=? AND fid=?",
                ','.join(parts), tid, r['fid'])


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
    # 列级单位是 v3.21 才有的，老模板补上预设值（长=米 / 平米=平米 / 卷料=卷）
    if not q("SELECT COUNT(*) c FROM tpl_cols WHERE tpl_id=? AND unit<>''", tid)[0]['c']:
        preset_col_units(tid)
    # v3.21b：配了单位的列，界面上表头显示成「长（米）」，导入也必须能认这个写法。
    # 老配置的别名里没有带单位的形式，这里统一补上，否则导入时该列匹配不上、
    # 单据建了但值静默丢失。
    _patch_unit_aliases(tid)
    # 老数据归入默认模板
    run("UPDATE materials SET tpl_id=? WHERE tpl_id IS NULL", tid)
    run("UPDATE txns SET tpl_id=? WHERE tpl_id IS NULL", tid)
    run("UPDATE pos SET tpl_id=? WHERE tpl_id IS NULL", tid)
    _upgrade_enable_area_cols()
    return tid

def _upgrade_enable_area_cols():
    """v3.28：长 / 宽 / 平米 / 卷料 改为默认开启。

    老库里这四列是关着的（v3.16 摒弃旧模板时设的），使用者在入库页根本
    看不到它们，也没有任何提示说要去哪儿开。这里在升级时统一打开一次。

    只做一次（用 user_version 记标记）：之后使用者自己手动关掉的列不会
    被反复打开 —— 那是他的选择，得尊重。
    """
    try:
        if int(q("PRAGMA user_version")[0][0] or 0) >= 1:
            return
        for fid in ('spec', 'width', 'sqm', 'rolls'):
            run("UPDATE tpl_cols SET enabled=1 WHERE fid=?", fid)
            run("UPDATE colmap SET enabled=1 WHERE fid=?", fid)
        run("PRAGMA user_version=1")
    except Exception:
        pass      # 升级失败不该拖垮启动，列还能手动开

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
                           name, m['code'], (m.get('unit') or '').strip()))
        for fid, label, pos, en, al in DEFAULT_COLS:
            c.execute("INSERT OR IGNORE INTO colmap(fid,label,pos,enabled,aliases)"
                      " VALUES(?,?,?,?,?)", (fid, label, pos, en, al))
    migrate()
    _ensure_po_tpl()
