# -*- coding: utf-8 -*-
"""业务模块 ↔ 电子表格 桥梁（v3.100）

把「制表模块里做好的一张表」和「采购 / 入库 / 出库」连起来：

    1. 用户在制表页做好表格（表头自己定），存成一本工作簿
    2. 采购下单时选「录入到哪本簿的哪张表」
    3. 表头映射成一张卡片：采购字段 ↔ 表格的哪一列，可勾选要不要写
    4. 映射按「模块 + 工作簿 + 工作表」记住 —— 换表再换回来，配置还在
    5. 下单后按批次号回写：A 列有这个批次号就更新那行，没有才追加新行

为什么按批次号定位行而不是永远追加：
    改单、分批到货都会产生同一批次的多条记录。永远追加会留下多行同批次，
    表格越涨越乱；按批次号匹配才能让「这一批」始终只有一行。
"""

import json
import datetime

from ..core import db
from . import batch as BT

#: 不写入任何列
SKIP = -1

#: 三个模块各自的字段。（fid, 标签, 默认的批次号列）
#: v3.103：卷料业务按「列」记 —— 长 / 宽 / 平米 / 卷料 四列是核心口径，
#: 与 wh/core/tpl.py 的 calc_area 同一套命名（spec=长, width=宽, sqm=平米, rolls=卷料），
#: 这样制表页、导入、回写表格三处列名完全一致，不用再换算。
#: 卷料业务按「列」记 —— 长 / 宽 / 卷料 三列是核心口径，
#: 与 wh/core/tpl.py 的 calc_area 同一套命名（spec=长, width=宽, rolls=卷料），
#: 这样制表页、导入、回写表格三处列名完全一致，不用再换算。
#:
#: v3.106 去掉了 sqm：采购按平米，数量本身就是平米，「数量」和「平米」
#: 是同一个值摆了两遍。使用者同时打开两列时，其中一列永远写不进去
#: （两列抢同一个表头，先到先得），还以为是自己没配对。
#: 现在只剩 qty，标签就叫「平米」，数量 = 平米。
_ROLL_COLS = [
    ('spec',  '长（米）', SKIP),
    ('width', '宽（米）', SKIP),
    ('rolls', '卷料',    SKIP),
]
FIELDS = {
    'po': [
        ('batch',      '批次号',   0),
        ('name',       '物料名称', 1),
        ('qty',        '平米',     2),
        ('price',      '单价',     3),
        ('amount',     '金额',     4),
    ] + _ROLL_COLS + [
        ('unit',       '采购单位', SKIP),
        ('conv',       '换算率',   SKIP),
        ('stock_unit', '库存单位', SKIP),
        ('supplier',   '供应商',   SKIP),
        ('pono',       '采购单号', SKIP),
        ('odate',      '下单日期', SKIP),
        ('note',       '备注',     SKIP),
    ],
    'in': [
        ('batch',    '批次号',   0),
        ('name',     '物料名称', 1),
        ('qty',      '平米',     2),
        ('price',    '单价',     3),
        ('amount',   '金额',     4),
    ] + _ROLL_COLS + [
        ('unit',     '单位',     SKIP),
        ('tdate',    '日期',     SKIP),
        ('supplier', '供应商',   SKIP),
        ('note',     '备注',     SKIP),
    ],
    'out': [
        ('batch',    '批次号',   0),
        ('name',     '物料名称', 1),
        ('qty',      '平米',     2),
        ('price',    '单价',     3),
        ('amount',   '金额',     4),
    ] + _ROLL_COLS + [
        ('unit',     '单位',     SKIP),
        ('tdate',    '日期',     SKIP),
        ('receiver', '领用人',   SKIP),
        ('note',     '备注',     SKIP),
    ],
}

#: 模块中文名，页面上显示用
MODULE_NAME = {'po': '采购', 'in': '入库', 'out': '出库'}


# ------------------------------------------------------------------ 读取表
def books():
    """所有工作簿，供下拉选择。"""
    try:
        return db.q("SELECT id, name, updated FROM wb ORDER BY id DESC")
    except Exception:
        return []


def sheets(wb_id):
    """一本工作簿里有哪些工作表。"""
    bk = _load(wb_id)
    if bk is None:
        return []
    return [s.name for s in bk.sheets]


def heads(wb_id, sh_name, nrow=1):
    """取表头行（默认第一行），返回 ['', '物料名称', ...]。"""
    bk = _load(wb_id)
    if bk is None:
        return []
    sh = bk.sheet(sh_name)
    if sh is None:
        return []
    out = []
    c = 0
    while True:
        try:
            v = sh.value(0, c)
        except Exception:
            v = ''
        out.append('' if v is None else str(v))
        c += 1
        # 连续 5 个空列就认为到头了，避免扫满 200 列
        if c >= sh.cols or (c > len(out) and all(not x for x in out[-5:]) and c > 5):
            break
        if c > 500:
            break
    while out and not out[-1]:
        out.pop()
    return out


# ------------------------------------------------------------------ 绑定
def bind_get(module):
    """取该模块当前绑定。没绑过返回 None。"""
    try:
        rows = db.q("SELECT * FROM sheet_bind WHERE module=? AND is_cur=1 "
                    "ORDER BY updated DESC LIMIT 1", module)
        if not rows:
            return None
        r = rows[0]
        return {'wb_id': r['wb_id'], 'sh_name': r['sh_name'],
                'mapping': _json(r['mapping']), 'module': module}
    except Exception:
        return None


def bind_set(module, wb_id, sh_name, mapping):
    """保存绑定。同一模块只保留一条 is_cur=1，其余置 0（但记录留着，切回来还能用）。"""
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        db.run("UPDATE sheet_bind SET is_cur=0 WHERE module=?", module)
        db.run("INSERT INTO sheet_bind(module,wb_id,sh_name,mapping,is_cur,updated)"
               " VALUES(?,?,?,?,1,?)"
               " ON CONFLICT(module,wb_id,sh_name) DO UPDATE SET"
               " mapping=excluded.mapping, is_cur=1, updated=excluded.updated",
               module, int(wb_id), str(sh_name),
               json.dumps(mapping or {}, ensure_ascii=False), now)
        return True
    except Exception:
        return False


def bind_off(module):
    """取消绑定（不再自动回写），但保留映射记录。"""
    try:
        db.run("UPDATE sheet_bind SET is_cur=0 WHERE module=?", module)
        return True
    except Exception:
        return False


#: 字段别名：表头怎么写都能认（v3.103）
#: 卷料业务的列名各厂写法不一 —— 长/长度/米数、宽/幅宽、平米/平方米/面积、
#: 卷/卷数/卷料。只靠标签互相包含会漏（「平米」认不出「平方米」）。
ALIASES = {
    # v3.104 去掉 no / 编号：太泛，「物料编号」「供应商编号」会被当成批次号列，
    # 写错列比认不出更糟（认不出还能在卡片上手动选）。
    'batch': '批次号,批号,批次,lot,batch',
    'name':  '物料名称,物料,名称,品名,材料',
    'spec':  '长,长度,长(米),长（米）,规格,规格（米）,规格(米),厚度,米数',
    'width': '宽,宽幅,宽度,幅宽,宽(米),宽（米）',
    'rolls': '卷料,卷,卷数',
    'qty':   '平米,平方米,面积,平方,m2,m²,数量,件数,总数,平米数',
    'price': '单价,价格,含税单价,不含税单价,成本价',
    'amount': '金额,小计,总价,合计,总金额',
    'unit':  '单位,采购单位',
    'conv':  '换算率,换算,倍数',
    'stock_unit': '库存单位',
    'supplier': '供应商,厂商,供货商,供方',
    'pono':  '采购单号,单号,订单号,采购订单',
    'odate': '下单日期,日期,订单日期',
    'tdate': '日期,入库日期,出库日期,单据日期',
    'receiver': '领用人,领料人,领料,使用人',
    'note':  '备注,说明,注释',
}


def _hits(fid, label, norm, used):
    """按 别名整词 → 标签整词 → 别名包含 → 标签包含 的顺序找列。"""
    al = [a.strip() for a in (ALIASES.get(fid) or '').split(',') if a.strip()]
    for cand in (al + [label]):            # 整词相等优先，别名在前
        for i, h in enumerate(norm):
            if i in used or not h:
                continue
            if h == cand:
                return i
    for cand in (al + [label]):            # 再退到互相包含
        for i, h in enumerate(norm):
            if i in used or not h:
                continue
            if cand in h or h in cand:
                return i
    return SKIP


def _shown_fids(module):
    """该模块当前显示的字段。延迟导入 —— cols 要用这里的 FIELDS，
    模块级互相 import 会成环。"""
    try:
        from . import cols as CL
        return set(CL.visible_fids(module))
    except Exception:
        return {f[0] for f in FIELDS.get(module, [])}


def auto_map(module, heads_list):
    """按表头文字猜一套映射。

    先整词相等，再退到「互相包含」。猜不中的字段置 SKIP（不写入），
    让用户自己在卡片上选，宁可少写也别写错列。
    """
    # v3.106：屏蔽掉的字段不参与映射 —— 表单上不显示，表格里也不写。
    # 表单字段 ≡ 映射字段（同一套 fid），屏蔽只需表达一次。
    fields = [f for f in FIELDS.get(module, []) if f[0] in _shown_fids(module)]
    norm = [(h or '').strip() for h in (heads_list or [])]
    out = {}
    used = set()
    # v3.104 批次号先按表头文字找 —— 用户自己排的表，批次号不一定在 A 列。
    # 以前无条件认 A 列，碰到「物料 / 平方米 / 批号」这种顺序就把批次号写进了
    # 物料那一列，物料名反而丢了。认不出才退回 A 列（制表模块建的表 A 列默认
    # 就是批次号）。
    _b = _hits('batch', '批次号', norm, set())
    if _b == SKIP:
        _b = 0 if norm else SKIP
    out['batch'] = _b
    if _b != SKIP:
        used.add(_b)
    for fid, label, dft in fields:
        if fid == 'batch':
            continue
        hit = _hits(fid, label, norm, used)
        out[fid] = hit
        if hit != SKIP:
            used.add(hit)
    return out


# ------------------------------------------------------------------ 回写
def write(module, batch_no, values):
    """按批次号回写一行。

    batch_no 为空就不写 —— 没有批次号无从定位行，写进去只会变成孤儿行。
    返回 (是否写入, '追加'/'更新'/原因)。
    """
    b = bind_get(module)
    if not b:
        return False, '未绑定表格'
    batch_no = (batch_no or '').strip()
    if not batch_no:
        return False, '无批次号'
    bk = _load(b['wb_id'])
    if bk is None:
        return False, '工作簿不存在'
    sh = bk.sheet(b['sh_name'])
    if sh is None:
        return False, '工作表不存在'

    mapping = b['mapping'] or {}
    bcol = mapping.get('batch', 0)
    bcol = 0 if bcol is None or bcol < 0 else int(bcol)

    # 找批次号所在行：从表头下一行开始扫 A 列
    row = None
    for r in range(1, sh.rows):
        try:
            v = sh.value(r, bcol)
        except Exception:
            v = ''
        if str(v or '').strip() == batch_no:
            row = r
            break
    added = False
    if row is None:
        row = _next_empty_row(sh, bcol)
        added = True

    sh.set_raw(row, bcol, batch_no)
    _shown = _shown_fids(module)
    for fid, col in (mapping or {}).items():
        if fid == 'batch':
            continue
        if fid not in _shown:
            continue      # v3.106 已屏蔽的字段不写，哪怕旧映射里还留着
        try:
            col = int(col)
        except (TypeError, ValueError):
            continue
        if col < 0 or fid not in values:
            continue
        v = values.get(fid)
        # v3.106：空值不许盖掉表格里的公式。
        # 使用者在「卷料」列写了 =E2/(C2*D2)，表单这一栏没填 → 以前会写个空串
        # 把公式抹掉，表格从此不会再算，而且看不出是谁干的。
        # 空格子只在目标格不是公式时才清；要改数就填数，填了就一定写。
        if v is None or (isinstance(v, str) and not v.strip()):
            _cur = sh.raw(row, col)
            if _cur is not None and str(_cur).strip().startswith('='):
                continue
            v = ''
        sh.set_raw(row, col, v)

    _flush(b['wb_id'], bk)
    return True, ('追加' if added else '更新')


def batch_rows(module='in', limit=300):
    """从该模块绑定的表格里读批次号候选（v3.109）。

    表格是主体：采购下单、入库、出库都往同一张表写，表里那一行就是这批货的
    完整档案（批次号 / 物料 / 规格 / 平米 / 卷料……）。所以联想直接问表格要，
    不再只问采购单 —— 手填批次号单独入库的那些批次，采购单里压根没有，
    照旧只查采购单的话，使用者在最需要联想的时候反而一个候选都看不到。

    返回 [{'b':批次号,'n':物料,'sp':规格,'un':单位,'sq':平米,'src':'sheet', ...}]
    """
    b = bind_get(module)
    if not b:
        return []
    bk = _load(b['wb_id'])
    if bk is None:
        return []
    sh = bk.sheet(b['sh_name'])
    if sh is None:
        return []
    mapping = b['mapping'] or {}
    if not mapping:
        return []

    def _col(fid, dflt=SKIP):
        try:
            c = int(mapping.get(fid, dflt))
        except (TypeError, ValueError):
            return SKIP
        return c

    bcol = _col('batch', 0)
    if bcol < 0:
        return []
    cmap = {f: _col(f) for f in
            ('name', 'spec', 'width', 'qty', 'rolls', 'price', 'amount',
             'unit', 'supplier', 'note')}

    def _sv(r, c):
        if c is None or c < 0:
            return ''
        try:
            v = sh.value(r, c)
        except Exception:
            return ''
        return '' if v is None else str(v).strip()

    out = []
    blank = 0
    r = 1
    while r < sh.rows and len(out) < limit:
        bno = _sv(r, bcol)
        if not bno:
            blank += 1
            # 中间偶尔空一行很正常（分组空行）；连续 30 行全空就认为到头了
            if blank >= 30:
                break
            r += 1
            continue
        blank = 0
        rec = {
            'b': bno[:40],
            'n': _sv(r, cmap.get('name', SKIP)),
            'sp': _sv(r, cmap.get('spec', SKIP)),
            'un': _sv(r, cmap.get('unit', SKIP)),
            'su': _sv(r, cmap.get('unit', SKIP)),
            'cv': 1,
            'mid': 0,
            'sup': _sv(r, cmap.get('supplier', SKIP)),
            'po': '',
            'st': '表格',
            'sq': _sv(r, cmap.get('qty', SKIP)),
            'rk': _sv(r, cmap.get('rolls', SKIP)),
            'left': 0,
            'ord': 0,
            'src': 'sheet',
        }
        # 物料档案里有同名物料就带上 id —— 选中时能连库存、长宽、口径一起带出
        try:
            if rec['n']:
                m = db.q("SELECT id,qty_unit FROM materials WHERE name=? AND active=1"
                         " ORDER BY id DESC LIMIT 1", rec['n'])
                if m:
                    rec['mid'] = m[0]['id'] or 0
        except Exception:
            pass
        if not rec['un']:
            rec['un'] = '平米'
        out.append(rec)
        r += 1
    return out


def _next_empty_row(sh, bcol, start=1):
    """第一个「批次号列为空」的行。"""
    r = start
    while r < sh.rows:
        try:
            v = sh.value(r, bcol)
        except Exception:
            v = ''
        if not str(v or '').strip():
            return r
        r += 1
    return sh.rows


# ------------------------------------------------------------------ 内部
def _load(wb_id):
    """读工作簿。优先走制表页的在线缓存，避免覆盖掉用户正在编辑的内容。"""
    try:
        from .web.common import _load as _l, _LIVE
        st = _LIVE.get(wb_id)
        if st:
            return st['book']
        bk, _ = _l(wb_id)
        return bk
    except Exception:
        pass
    # 兜底：直连数据库
    try:
        from .engine import core as E
        row = db.q("SELECT data FROM wb WHERE id=?", wb_id)
        if not row:
            return None
        return E.Workbook.from_dict(json.loads(row[0]['data'] or '{}'))
    except Exception:
        return None


def _flush(wb_id, bk):
    """写回数据库。在线缓存存在时一并更新，两边保持一致。"""
    try:
        from .web.common import _LIVE
        st = _LIVE.get(wb_id)
        if st is not None:
            st['book'] = bk
    except Exception:
        pass
    try:
        db.run("UPDATE wb SET data=?, updated=? WHERE id=?",
               json.dumps(bk.to_dict(), ensure_ascii=False),
               datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), wb_id)
        return True
    except Exception:
        return False


def _json(s):
    try:
        return json.loads(s or '{}')
    except Exception:
        return {}
