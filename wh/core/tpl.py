# -*- coding: utf-8 -*-
"""模板体系：列（表头）映射、列名指纹、自定义列、库存模板增删改。
_DEFAULT_COLS 等默认列定义在这里，被 schema.init 引用。"""
import os, sqlite3, json, sys
from datetime import datetime
from .paths import (app_dir, res_dir, BASE, DB_PATH, SEED, UNITS,
                   unit_choices, _is_frozen, _writable, _user_data_dir)
from .dbconn import (conn, tx, close, q, run, runmany, _flat, _cols,
                    _run_now, _runmany_now, _write_lock, _is_write, _busy_retry)

# ---------- 列（表头）映射配置 ----------
# fid=系统字段, label=入库界面显示的表头, pos=列顺序, enabled=是否显示, aliases=导入时识别的表头别名
DEFAULT_COLS = [
    # 默认只保留 供应商 / 类型 / 状态（外加录单必需的物料名称），
    # 其余全部默认关闭 —— 使用者在「改表头」里按需启用，不再绑定原 Excel 的 A-G 七列。
    ('supplier', '供应商', 1, 1, '供应商,厂商,供货商,供方'),
    ('category', '类型', 2, 1, '类型,类别,分类,大类,品种'),
    ('status',   '状态', 3, 1, '状态,使用状态'),
    ('name',     '物料名称', 4, 1, '物料名称,名称,品名,品名规格,物料,材料名称'),
    # ↓ 长/宽/平米/卷料 默认开启：填长宽后，平米与卷料互相推算（v3.28）
    ('spec',  '长',   5, 1, '长,长度,长(米),长（米）,规格,规格（米）,规格(米),厚度,米数'),
    ('width', '宽',   6, 1, '宽,宽幅,宽度,幅宽,宽(米),宽（米）'),
    ('sqm',   '平米', 7, 1, '平米,平方米,面积,平方,m2,m²'),
    ('rolls', '卷料', 8, 1, '卷料,卷,卷数,米数'),
    ('code',  '料号', 9, 0, '料号,物料编号,物料编码,编号,编码,型号,规格型号'),
    ('opening', '期初结存', 10, 0, '期初结存,期初,上月结存,上期结存,库存,当前库存'),
    ('safety',  '安全库存', 11, 0, '安全库存,预警值,库存预警,最低库存'),
]

# 计算列：值由别的列算出来（也可手填，填了就算另一个）。
# sqm（总面积）  = 卷料 × 长 × 宽
# rolls（卷数）  = 平米 ÷ (长 × 宽)   向下取整；除不尽的余料自动写进备注
CALC_COLS = {'sqm': ('rolls', 'spec', 'width'), 'rolls': ('sqm', 'spec', 'width')}

# 需要存进数据库的数值列（老库升级时要补）
NUM_EXTRA_COLS = ('sqm', 'rolls')

def col_label(c, with_unit=True):
    """列显示名：配了单位就显示成「长（米）」，没配就是「长」。

    使用者在「改表头」里给每列单独选单位，也可以选「不用」——留空即不显示。
    """
    lb = (c.get('label') if hasattr(c, 'get') else c['label']) or ''
    if not with_unit:
        return lb
    try:
        u = (c.get('unit') or '').strip() if hasattr(c, 'get') else ((c['unit'] if 'unit' in c.keys() else '') or '').strip()
    except Exception:
        u = ''
    return '%s（%s）' % (lb, u) if u else lb


QTY_UNITS = ('平米', '卷')      # 数量列的两种口径


def calc_area(vals):
    """长 × 宽 = 一卷的平米；平米（总面积）与卷料（卷数）**双向推算**。

    v3.28：使用者两种填法都有，先填哪个就算另一个：

      先填平米 → 卷料 = 平米 ÷ 一卷平米（向下取整）
                 余料 = 平米 − 卷料 × 一卷平米（不足一卷的零头）
      先填卷料 → 平米 = 卷料 × 一卷平米
                 卷料带小数时，零头折算成平米写进余料

    两个都没填、只填了数量时，按「数量口径」推算（v3.27 的双口径，
    兼容以前只填数量不填平米/卷料的表）：

      按平米：数量就是总面积 → 卷料 = 数量 ÷ 一卷平米
      按卷  ：数量就是卷数   → 平米 = 数量 × 一卷平米

    sqm 记的是**总面积**（这一笔一共多少平米），不是一卷的平米 ——
    一卷多少平米 = 长 × 宽，是物料本身的属性。

    只在填了长、宽时才算；算不出来就保持 None，绝不瞎填 0。
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
    if L is None or W is None:
        return None, None, None
    per_roll = L * W                      # 一卷多少平米
    if per_roll <= 0:
        return None, None, None
    sqm_in = f(vals.get('sqm'))           # 使用者手填的平米（总面积）
    rolls_in = f(vals.get('rolls'))       # 使用者手填的卷料（卷数）
    qu = str(vals.get('qty_unit') or '平米').strip()
    rest = None
    if sqm_in is not None:
        # 先填平米：卷数取整，零头是余料
        sqm = round(sqm_in, 6)
        rolls = int(sqm_in / per_roll + 1e-9)   # 1e-9 抵消浮点误差，避免 3.0 算成 2
        rest = round(sqm_in - rolls * per_roll, 6)
    elif rolls_in is not None:
        # 先填卷料：总面积 = 卷数 × 一卷平米
        sqm = round(rolls_in * per_roll, 6)
        rolls = rolls_in
        _frac = round(rolls_in - int(rolls_in + 1e-9), 6)
        rest = round(_frac * per_roll, 6) if _frac > 1e-6 else None
    else:
        total = f(vals.get('qty'))
        if total is None:
            # 只填了长宽：只能算出一卷多少平米，平米（总面积）无从得知
            return round(per_roll, 6), None, None
        if qu == '卷':
            rolls = total
            sqm = round(total * per_roll, 6)
            _f2 = round(total - int(total + 1e-9), 6)
            rest = round(_f2 * per_roll, 6) if _f2 > 1e-6 else None
        else:
            sqm = round(total, 6)
            rolls = int(total / per_roll + 1e-9)
            rest = round(total - rolls * per_roll, 6)
    if rest is not None and rest < 1e-6:
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
# 注意：_ast.Num / _ast.Str / _ast.NameConstant 在 Python 3.12 起已被彻底移除，
# 直接引用会在**导入时**就 AttributeError（整个程序起不来）。
# 这里用 getattr 兜底：新版本统一由 Constant 表示，老版本挂上各自的节点。
_ALLOWED = tuple(x for x in (
    _ast.Expression, _ast.BinOp, _ast.UnaryOp, _ast.Constant,
    _ast.Name, _ast.Load, _ast.Add, _ast.Sub, _ast.Mult, _ast.Div,
    _ast.Pow, _ast.Mod, _ast.USub, _ast.UAdd, _ast.Call, _ast.keyword,
    _ast.Tuple, _ast.List,
    getattr(_ast, 'Num', None), getattr(_ast, 'Str', None),
    getattr(_ast, 'NameConstant', None), getattr(_ast, 'Bytes', None),
    getattr(_ast, 'Index', None),
) if x is not None)

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

def preset_col_units(tid):
    """新建模板时的预设单位：长=米、宽=米、平米=平米、卷料=卷。

    只是省事用的默认值 —— 使用者在「改表头」里可以改，也可以清空表示不带单位。
    """
    for fid, u in (('spec', '米'), ('width', '米'), ('sqm', '平米'), ('rolls', '卷')):
        run("UPDATE tpl_cols SET unit=? WHERE tpl_id=? AND fid=?", u, tid, fid)


def add_tpl(name, note='', copy_from=None):
    """新建模板。copy_from 给定时复制该模板的列配置"""
    name = (name or '').strip() or '未命名模板'
    mx = q("SELECT COALESCE(MAX(pos),0) p FROM tpl")[0]['p']
    tid = run("INSERT INTO tpl(name,note,pos,created) VALUES(?,?,?,?)",
              name, note, mx + 1,
              datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    if copy_from:
        for r in q("SELECT fid,label,pos,enabled,aliases,unit FROM tpl_cols WHERE tpl_id=?", copy_from):
            run("INSERT INTO tpl_cols(tpl_id,fid,label,pos,enabled,aliases,unit)"
                " VALUES(?,?,?,?,?,?,?)", tid, r['fid'], r['label'], r['pos'],
                r['enabled'], r['aliases'], r['unit'] or '')
    else:
        for fid, label, pos, en, al in DEFAULT_COLS:
            run("INSERT INTO tpl_cols(tpl_id,fid,label,pos,enabled,aliases)"
                " VALUES(?,?,?,?,?,?)", tid, fid, label, pos, en, al)
        preset_col_units(tid)
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
                      " xtype=?,xopt=?,xform=?,unit=?"
                      " WHERE tpl_id=? AND fid=?",
                      [(r['label'], r['pos'], r['enabled'], r['aliases'],
                        r.get('xtype') or 'text', r.get('xopt') or '',
                        r.get('xform') or '', (r.get('unit') or '').strip(),
                        tid, r['fid'])
                       for r in rows])
    return True

def reset_tpl_cols(tid):
    run("DELETE FROM tpl_cols WHERE tpl_id=?", tid)
    for fid, label, pos, en, al in DEFAULT_COLS:
        run("INSERT INTO tpl_cols(tpl_id,fid,label,pos,enabled,aliases)"
            " VALUES(?,?,?,?,?,?)", tid, fid, label, pos, en, al)
    preset_col_units(tid)

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
