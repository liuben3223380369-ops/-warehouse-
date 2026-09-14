"""从 Excel / CSV 批量导入物料档案。

表头自动识别（中文别名），按「料号 → 名称」优先级匹配已有物料：
  命中则更新非空白字段，未命中则新增。
"""
import io, csv, os, datetime

try:
    import openpyxl
except ImportError:
    openpyxl = None

# 规范字段 -> 可能出现的中文表头别名
ALIASES = {
    'name':     ['物料名称', '名称', '品名', '品名规格', '物料', '材料名称', 'name'],
    'code':     ['料号', '物料编号', '物料编码', '编号', '编码', '型号', '规格型号', 'code'],
    'supplier': ['供应商', '厂商', '供货商', 'supplier'],
    'category': ['类型', '类别', '分类', '大类', 'category'],
    'spec':     ['规格', '规格（米）', '规格(米)', '厚度', 'spec'],
    'width':    ['宽幅', '宽度', '幅宽', 'width'],
    'unit':     ['单位', '单位（卷）', '单位(卷)', '计量单位', 'unit'],
    'opening':  ['期初结存', '期初', '上月结存', '上期结存', '库存', '当前库存', 'opening'],
    'safety':   ['安全库存', '预警值', '库存预警', '最低库存', 'safety'],
    'status':   ['状态', '使用状态', 'status'],
}
NUM = {'opening', 'safety'}

# ---------- 流水（出入库单据）导入的表头别名 ----------
# 说明：物料的「类型」与「进出」易混淆，进出用 进出/方向/出入库 等专属词；
#      也支持 入库数量 / 出库数量 分列写法（一行可生成两笔）
TXN_ALIASES = {
    'date':  ['日期', '单据日期', '出入库日期', '入库日期', '出库日期', '时间', '业务日期', 'date'],
    'name':  ['物料名称', '名称', '品名', '物料', '材料名称', '货品名', 'name'],
    'code':  ['料号', '物料编号', '物料编码', '编号', '编码', '型号', '规格型号', 'code'],
    'kind':  ['进出', '进/出', '出入库', '方向', '收发明细', '出入库类型', '类型进销',
              '进出标志', 'in/out', 'type'],
    'qty':   ['数量', '出入库数量', '数量(平米)', '数量（平米）', '重量', '平米数', '收发数量', 'qty'],
    'in_qty':  ['进', '入库', '入库数量', '进货数量', '收入数量', '入库数', '进仓数量', 'in'],
    'out_qty': ['出', '出库', '出库数量', '出货数量', '发出数量', '出库数', '出仓数量', '领用数量', 'out'],
    'note':  ['备注', '客户', '单号', '用途', '领用人', '说明', '摘要', 'note'],
    'pieces':    ['件数', '件', '卷数', '箱数', '数量(件)', '数量（件）', 'pieces'],
    'per_piece': ['每件', '每件/个', '每件数量', '单件', '每件米数', '米/件', 'per'],
}
# 物料档案列（导入流水时顺带建档/更新用）
TXN_MAT_COLS = ['supplier', 'category', 'spec', 'width', 'unit', 'status', 'opening', 'safety']

def _norm(s):
    return str(s or '').strip().lower().replace(' ', '').replace('（', '(').replace('）', ')')

def map_headers(header, aliases=None):
    """表头行 -> {列索引: 规范字段}。aliases 来自列映射配置，优先级高于内置别名"""
    names = {f: list(v) for f, v in ALIASES.items()}
    for f, v in (aliases or {}).items():
        if f in names:
            names[f] = list(v) + [x for x in ALIASES.get(f, []) if x not in v]
    out = {}
    for i, h in enumerate(header):
        n = _norm(h)
        if not n:
            continue
        for f, ns in names.items():
            if f in out.values():
                continue
            if n in [_norm(x) for x in ns]:
                out[i] = f
                break
    return out

def parse_file(path=None, stream=None, filename='', aliases=None):
    """返回 (行列表[dict], 识别到的字段集合)"""
    if path and path.lower().endswith('.csv') or (filename or '').lower().endswith('.csv'):
        data = open(path, 'rb').read() if path else stream.read()
        for enc in ('utf-8-sig', 'gbk', 'utf-8'):
            try:
                text = data.decode(enc); break
            except UnicodeDecodeError:
                continue
        else:
            text = data.decode('utf-8', 'replace')
        rows = list(csv.reader(io.StringIO(text)))
    else:
        if openpyxl is None:
            raise RuntimeError('未安装 openpyxl，请先执行: pip install openpyxl')
        wb = openpyxl.load_workbook(path or io.BytesIO(stream.read()), data_only=True)
        ws = wb[wb.sheetnames[0]]
        rows = [[c for c in r] for r in ws.iter_rows(values_only=True)]

    rows = [r for r in rows if any(_norm(c) for c in r)]
    if not rows:
        return [], set()

    hi, hmap = 0, {}
    for i in range(min(5, len(rows))):
        m = map_headers(rows[i], aliases)
        if 'name' in m.values() and len(m) > len(hmap):
            hi, hmap = i, m
    if 'name' not in hmap.values():          # 无表头：A=名称 B=料号 C=类型 D=单位 E=期初
        hmap = {0: 'name', 1: 'code', 2: 'category', 3: 'unit', 4: 'opening'}
        # 首行若含任何已知字段名，仍当作表头跳过，避免把表头当数据
        known = set()
        for v in (aliases or {}).values():
            known |= {_norm(x) for x in v}
        for v in ALIASES.values():
            known |= {_norm(x) for x in v}
        hi = 0 if any(_norm(c) in known for c in rows[0]) else -1

    out = []
    for r in rows[hi + 1:]:
        d = {f: '' for f in ALIASES}
        for i, f in hmap.items():
            if i < len(r):
                v = r[i]
                d[f] = '' if v is None else (str(v).strip() if f != 'width' else str(v).split('.')[0] if isinstance(v, float) else str(v).strip())
        d['name'] = d['name'] or d['code']
        if not d['name']:
            continue
        for f in NUM:
            d[f] = _num(d[f])
        out.append(d)
    return out, set(hmap.values())

def _num(v):
    s = str(v or '').strip().replace(',', '')
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0

def _read_grid(path=None, stream=None, filename=''):
    """把 xlsx/xlsm/xls/csv 读成二维列表（只取第一张表）"""
    fn = ((path or '') + (filename or '')).lower()
    if fn.endswith('.csv'):
        data = open(path, 'rb').read() if path else stream.read()
        for enc in ('utf-8-sig', 'gbk', 'utf-8', 'gb18030'):
            try:
                text = data.decode(enc); break
            except UnicodeDecodeError:
                continue
        else:
            text = data.decode('utf-8', 'replace')
        return list(csv.reader(io.StringIO(text)))
    if fn.endswith(('.xls', '.et')):
        try:
            import xlrd
        except ImportError:
            raise RuntimeError('读取 .xls/.et 需要 xlrd，请先执行 pip install xlrd，'
                               '或在 WPS 里另存为 .xlsx / .csv 再导入')
        bk = xlrd.open_workbook(path or (io.BytesIO(stream.read()) if stream else None))
        sh = bk.sheet_by_index(0)
        return [[_cell(sh, r, c) for c in range(sh.ncols)] for r in range(sh.nrows)]
    if openpyxl is None:
        raise RuntimeError('未安装 openpyxl，请先执行: pip install openpyxl')
    wb = openpyxl.load_workbook(path or io.BytesIO(stream.read()), data_only=True)
    # 只取第一张"有内容的"表：很多人第一张表是说明/封面，数据在后面
    for nm in wb.sheetnames:
        ws = wb[nm]
        rows = [[c for c in r] for r in ws.iter_rows(values_only=True)]
        if any(any(_norm(c) for c in r) for r in rows):
            return rows
    return []

def _cell(sh, r, c):
    v = sh.cell_value(r, c)
    if sh.cell_type(r, c) == 3:            # XL_CELL_DATE
        import xlrd, datetime as _dt
        try:
            t = xlrd.xldate_as_tuple(v, sh.book.datemode)
            return _dt.datetime(*t).strftime('%Y-%m-%d')
        except Exception:
            return str(v)
    return v

def _fmt_date(v):
    """把 Excel 日期/字符串统一成 YYYY-MM-DD"""
    if v is None or str(v).strip() == '':
        return ''
    if isinstance(v, (int, float)):        # Excel 序列号
        from datetime import date as _d, timedelta
        try:
            return (_d(1899, 12, 30) + timedelta(days=float(v))).strftime('%Y-%m-%d')
        except Exception:
            return str(v)
    s = str(v).strip()
    for attr in ('date', 'datetime'):
        pass
    if hasattr(v, 'strftime'):
        return v.strftime('%Y-%m-%d')
    s = s.replace('年', '-').replace('月', '-').replace('日', '').replace('/', '-').replace('.', '-')
    parts = [p for p in s.split('-') if p]
    if len(parts) >= 3:
        y, m, d = parts[0], parts[1].zfill(2), parts[2][:2].zfill(2)
        if len(y) == 2:
            y = '20' + y
        return f'{y}-{m}-{d}'
    return s[:10]

def map_txn_headers(header, aliases=None, mat_aliases=None):
    """流水表头 -> {列索引: 字段}。先看进出/数量专用别名，再补物料 A-G 列"""
    names = {f: list(v) for f, v in TXN_ALIASES.items()}
    for f, v in (aliases or {}).items():          # 用户在列映射里自定义的
        if f in names:
            names[f] = list(v) + [x for x in TXN_ALIASES.get(f, []) if x not in v]
    mat = dict(ALIASES)
    for f, v in (mat_aliases or {}).items():
        if f in mat:
            mat[f] = list(v) + [x for x in ALIASES.get(f, []) if x not in v]
    out = {}
    # 先匹配流水专用列（避免「数量」被物料列抢走）
    for i, h in enumerate(header):
        n = _norm(h)
        if not n:
            continue
        for f in ('date', 'kind', 'qty', 'in_qty', 'out_qty', 'pieces', 'per_piece', 'note'):
            if f in out.values():
                continue
            if n in [_norm(x) for x in names[f]]:
                out[i] = f; break
    # 再匹配 name/code 与物料 A-G 列
    for i, h in enumerate(header):
        if i in out:
            continue
        n = _norm(h)
        if not n:
            continue
        for f in ['name', 'code'] + TXN_MAT_COLS:
            if f in out.values():
                continue
            if n in [_norm(x) for x in (names.get(f) or mat.get(f) or [])]:
                out[i] = f; break
    return out

# ---------- 原 Excel「宽表」布局：物料一行 × 每日(进/出)两列 ----------
WIDE_KINDS = {'进', '出'}
MAT_HEADS = {
    'supplier': ['供应商', '厂商', '供货商'],
    'category': ['类型', '类别', '分类', '大类'],
    'spec': ['规格', '规格(米)', '规格（米）', '厚度'],
    'width': ['宽幅', '宽度', '幅宽'],
    'name': ['物料名称', '名称', '品名', '材料名称', '物料'],
    'code': ['料号', '物料编号', '物料编码', '编号', '编码', '型号'],
    'unit': ['单位', '单位(卷)', '单位（卷）', '计量单位'],
    'opening': ['上月结存', '期初结存', '期初', '上期结存'],
}

def _is_dt(v):
    return hasattr(v, 'strftime') or isinstance(v, datetime.datetime)

def _wide_kind_row(grid):
    """找形如 [进,出,进,出...] 的那一行（原表第2行）"""
    best, bestn = None, 0
    for r in range(min(8, len(grid))):
        n = sum(1 for v in grid[r] if str(v or '').strip() in WIDE_KINDS)
        if n > bestn:
            best, bestn = r, n
    return best if bestn >= 4 else None

def _wide_dates(grid, drow, defmonth):
    """从日期行取每列日期；合并单元格造成的 None 沿用前一个日期"""
    row = grid[drow] if (drow is not None and drow < len(grid)) else []
    y, mo = (int(defmonth[:4]), int(defmonth[5:7])) if defmonth and len(defmonth) >= 7 \
        else (datetime.date.today().year, datetime.date.today().month)
    out, last = {}, None
    for c, v in enumerate(row):
        d = None
        if _is_dt(v):
            d = v.strftime('%Y-%m-%d')
        elif isinstance(v, (int, float)) and 1 <= float(v) <= 31:
            d = '%04d-%02d-%02d' % (y, mo, int(v))
        else:
            s = str(v or '').strip()
            if s and (('月' in s) or ('-' in s) or ('/' in s)):
                d = _fmt_date(v) or None
        if d:
            last = d
        if last:
            out[c] = last
    return out

def _col_by_head(grid, hrows, names):
    """在若干候选表头行里按名字找列号"""
    for hr in hrows:
        if hr is None or hr >= len(grid):
            continue
        for c, v in enumerate(grid[hr]):
            n = _norm(v)
            if n and n in [_norm(x) for x in names]:
                return c
    return None

def parse_wide(grid, defmonth=None):
    """解析原表那种『物料 × 每日进/出』宽表。返回 (单据列表, 字段集合)"""
    kr = _wide_kind_row(grid)
    if kr is None:
        return [], set()
    drow = kr - 1 if kr > 0 else None
    dates = _wide_dates(grid, drow, defmonth)
    if not dates:
        return [], set()
    # 数据起点：进出行之后，且该行有物料名称
    start = kr + 1
    heads = [hr for hr in (drow, kr) if hr is not None]
    name_c = _col_by_head(grid, heads, MAT_HEADS['name'])
    if name_c is None:                      # 表头对不上：挑"文字最多"的那一列当物料名
        best, bestn = None, 0
        for c in range(min(12, max(len(r) for r in grid[:20]))):
            n = 0
            for r in grid[start:start + 15]:
                if r and c < len(r):
                    v = str(r[c] or '').strip()
                    if v and not _num(v) and len(v) >= 2:
                        n += 1
            if n > bestn:
                best, bestn = c, n
        name_c = best
    if name_c is None:
        return [], set()
    cols = {k: _col_by_head(grid, heads, v) for k, v in MAT_HEADS.items()}
    cols['name'] = name_c
    # 日期列 -> (列号, 进/出)
    kind_map = {}
    for c, v in enumerate(grid[kr] if kr < len(grid) else []):
        s = str(v or '').strip()
        if s in WIDE_KINDS and c in dates:
            kind_map[c] = s
    out = []
    for r in grid[start:]:
        if not r or name_c >= len(r):
            continue
        nm = str(r[name_c] or '').strip()
        if not nm or nm in ('/', '-', '合计', '总计'):
            continue
        base = {'name': nm, 'code': '', 'date': '', 'note': '',
                'pieces': None, 'per_piece': None}
        for f in ('supplier', 'category', 'spec', 'width', 'unit', 'code'):
            c = cols.get(f)
            base[f] = '' if (c is None or c >= len(r)) else str(r[c] or '').strip()
            if base[f] in ('/', '-'):
                base[f] = ''
        oc = cols.get('opening')
        base['opening'] = _num(r[oc]) if (oc is not None and oc < len(r)) else 0.0
        base['safety'] = 0.0
        got = False
        for c, kind in sorted(kind_map.items()):
            if c >= len(r):
                continue
            q = _num(r[c])
            if q > 0:
                d = dict(base); d['kind'] = kind; d['qty'] = q
                d['date'] = dates[c]
                out.append(d); got = True
        if not got:                          # 该行没有进出，但档案信息仍要保留（期初）
            d = dict(base); d['kind'] = None; d['qty'] = 0.0
            out.append(d)
    return out, set(list(cols.keys()) + ['date', 'qty', 'kind'])

def parse_txn_by_map(path=None, stream=None, filename='', colmap=None, start=1,
                     default_kind=None, defdate=None):
    """按用户手动指定的列来解析。colmap: {列索引: 字段名}，start: 数据起始行(0基)
    字段名取值 name/code/date/kind/qty/in_qty/out_qty/pieces/per_piece/note，未列出的列忽略。"""
    grid = _read_grid(path=path, stream=stream, filename=filename)
    colmap = {int(k): v for k, v in (colmap or {}).items() if v}
    out = []
    for r in grid[start:]:
        if not any(_norm(c) for c in r):
            continue
        def val(f):
            for k, v in colmap.items():
                if v == f:
                    return r[k] if k < len(r) else None
            return None
        name = str(val('name') or '').strip()
        code = str(val('code') or '').strip()
        if not name and not code:
            continue
        base = dict(name=name or code, code=code,
                    date=_fmt_date(val('date')) or (defdate or ''),
                    note=str(val('note') or '').strip(),
                    pieces=_num(val('pieces')) or None,
                    per_piece=_num(val('per_piece')) or None)
        for f in TXN_MAT_COLS:
            v = val(f)
            base[f] = '' if v is None else str(v).strip()
            if f in ('opening', 'safety'):
                base[f] = _num(v)
        ins = _num(val('in_qty')); outs = _num(val('out_qty'))
        q = _num(val('qty')); pcs = _num(val('pieces')); per = _num(val('per_piece'))
        if not q and pcs and per:
            q = round(pcs * per, 2)
        kd = str(val('kind') or '').strip()
        items = []
        if ins or outs:
            if ins: items.append(('进', ins))
            if outs: items.append(('出', outs))
        elif q:
            k = default_kind or ''
            if kd:
                k = '出' if ('出' in kd or 'out' in kd.lower()) else '进'
            items.append((k or '进', q))
        elif per and pcs:
            pass
        for kind, qty in items:
            if qty <= 0:
                continue
            d = dict(base); d['kind'] = kind; d['qty'] = qty
            out.append(d)
    return out

def sniff_grid(grid, n=6, w=14):
    """给诊断用：原始前几行前几列"""
    return [[('' if c is None else str(c))[:18] for c in (r or [])[:w]]
            for r in grid[:n]]

def parse_txn_file(path=None, stream=None, filename='', aliases=None, mat_aliases=None,
                   default_kind=None, defmonth=None):
    """解析流水表。先按"一行一笔"解析；失败则按原表宽表解析。
    返回 (单据列表, 识别到的字段集合, 表头行号, 诊断信息)"""
    grid = _read_grid(path=path, stream=stream, filename=filename)
    raw = [r for r in grid if any(_norm(c) for c in r)]
    if not raw:
        return [], set(), -1, {'why': '空文件', 'grid': []}

    hi, hmap = 0, {}
    for i in range(min(5, len(raw))):
        m = map_txn_headers(raw[i], aliases, mat_aliases)
        has_q = ('qty' in m.values() or 'in_qty' in m.values() or 'out_qty' in m.values()
                 or ('pieces' in m.values() and 'per_piece' in m.values()))
        if 'name' in m.values() and has_q and len(m) > len(hmap):
            hi, hmap = i, m

    if 'name' not in hmap.values():
        # 不是"一行一笔"，试试原表那种"物料 × 每日进/出"宽表
        wrows, wfields = parse_wide(raw, defmonth)
        if wrows:
            return wrows, wfields, -2, {'why': 'wide', 'grid': sniff_grid(raw)}
        return [], set(), -1, {
            'why': 'no-header',
            'grid': sniff_grid(raw),
            'heads': [str(v or '') for v in (raw[0] if raw else [])][:14],
        }

    out = []
    for r in raw[hi + 1:]:
        def val(f):
            for k, v in hmap.items():
                if v == f:
                    return r[k] if k < len(r) else None
            return None
        name = str(val('name') or '').strip()
        code = str(val('code') or '').strip()
        if not name and not code:
            continue
        base = dict(name=name or code, code=code,
                    date=_fmt_date(val('date')),
                    note=str(val('note') or '').strip(),
                    pieces=_num(val('pieces')) or None,
                    per_piece=_num(val('per_piece')) or None)
        for f in TXN_MAT_COLS:
            v = val(f)
            base[f] = '' if v is None else str(v).strip()
            if f in ('opening', 'safety'):
                base[f] = _num(v)
        # 数量：优先 进/出 分列，其次 数量 + 进出方向
        ins = _num(val('in_qty'))
        outs = _num(val('out_qty'))
        q = _num(val('qty'))
        pcs = _num(val('pieces'))
        per = _num(val('per_piece'))
        if not q and pcs and per:      # 只有件数×每件 -> 总数量
            q = round(pcs * per, 2)
            base['qty_computed'] = True
        kd = str(val('kind') or '').strip()
        items = []
        if ins or outs:
            if ins:
                items.append(('进', ins))
            if outs:
                items.append(('出', outs))
        elif q:
            k = default_kind or ''
            if kd:
                k = '出' if ('出' in kd or 'out' in kd.lower() or kd in ('-', 'OUT')) else '进'
            items.append((k or '进', q))
        for kind, qty in items:
            if qty <= 0:
                continue
            d = dict(base); d['kind'] = kind; d['qty'] = qty
            out.append(d)
    if not out:
        return out, set(hmap.values()), hi, {
            'why': 'no-data', 'grid': sniff_grid(raw),
            'heads': [str(v or '') for v in raw[hi]][:14],
            'hi': hi,
        }
    return out, set(hmap.values()), hi, {'why': 'long', 'hi': hi}
