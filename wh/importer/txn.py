# -*- coding: utf-8 -*-
"""流水（出入库单据）导入：表头别名、按列映射解析、整文件解析。

依赖方向：base <- wide <- txn，不存在回指。
"""
import io, csv, re

try:
    import openpyxl
except ImportError:
    openpyxl = None

from .base import (ALIASES, _as_text, _cell, _fmt_date, _is_num_col, _norm,
                   _num, unit_from_label, QTY_MAX)
from .wide import parse_wide, _head_at, _nm_sum_word, _is_sum_col

TXN_ALIASES = {
    'date':  ['日期', '单据日期', '出入库日期', '入库日期', '出库日期', '时间', '业务日期', 'date'],
    # 真实采购单 / 送货单的表头往往写得很长（如「物品/服务名称及规格型号」），
    # 精确匹配认不出就整行表头作废，等于这类单据根本导不进来，故逐个收录。
    'name':  ['物料名称', '名称', '品名', '物料', '材料名称', '货品名', 'name',
              '物品/服务名称及规格型号', '物品名称', '物品/服务名称',
              '货物名称', '商品名称', '产品名称', '名称及规格', '名称规格',
              '品名及规格', '物料名称及规格', '名称及规格型号', '材料',
              '产品名称及规格', '货物名称及规格'],
    'code':  ['料号', '物料编号', '物料编码', '编号', '编码', '型号', '规格型号', 'code',
              '物料代码', '商品编码', '商品代码', '物品编码', '物品代码',
              '产品编码', '产品代码', '货号'],
    'kind':  ['进出', '进/出', '出入库', '方向', '收发明细', '出入库类型', '类型进销',
              '进出标志', 'in/out', 'type'],
    'qty':   ['数量', '出入库数量', '数量(平米)', '数量（平米）', '重量', '平米数', '收发数量', 'qty',
              '订单数量', '应发数量', '订购数量'],
    'in_qty':  ['进', '入库', '入库数量', '进货数量', '收入数量', '入库数', '进仓数量', 'in'],
    'out_qty': ['出', '出库', '出库数量', '出货数量', '发出数量', '出库数', '出仓数量', '领用数量', 'out'],
    'note':  ['备注', '客户', '单号', '用途', '领用人', '说明', '摘要', 'note'],
    # 「卷数」已让给卷料列（v3.28）：件数功能 v3.20 就删了，而卷数现在明确指卷料，
    # 留在这里会把使用者表里的卷料列抢成已废弃的件数。
    'pieces':    ['件数', '件', '箱数', '数量(件)', '数量（件）', 'pieces'],
    'per_piece': ['每件', '每件/个', '每件数量', '单件', '每件米数', '米/件', 'per'],
    'price': ['单价', '价格', '单位价格', '售价', '进货价', '单价(元)', '单价（元）',
              '元/米', '元/卷', '元/平', 'price'],
    'amount': ['金额', '总价', '合计金额', '总金额', '金额(元)', '金额（元）', 'amount'],
    # v3.35 批次：Excel 里有批次列就带上，入库后能对上采购那一批的实价
    'batch': ['批次', '批次号', '批号', '批号/批次', '生产批号', 'lot', 'batch',
              '到货批次', '采购批次'],
}
# 物料档案列（导入流水时顺带建档/更新用）
TXN_MAT_COLS = ['supplier', 'category', 'spec', 'width', 'unit', 'status', 'opening', 'safety',
                'sqm', 'rolls']

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
    # 送货单常同时有「订单数量」和「实发数量」两列。入库要按实际收到的算，
    # 否则按订单数入账会凭空多出库存（欠交时尤其明显），故实发列优先占位。
    for i, h in enumerate(header):
        if _norm(h) in ('实发数量', '实收数量', '实发数', '实收数', '本次数量',
                        '送货数量', '实际数量', '实发'):
            out[i] = 'qty'
            break
    # 数量口径列：「数量单位」「计量口径」等，表里写了就按写的算
    for i, h in enumerate(header):
        n = _norm(h)
        if n in ('数量单位', '计量单位', '数量口径', '口径', '计量口径', '单位口径'):
            out[i] = 'qty_unit'
            break
    # 先匹配流水专用列（避免「数量」被物料列抢走）
    for i, h in enumerate(header):
        n = _norm(h)
        if not n:
            continue
        for f in ('date', 'kind', 'qty', 'in_qty', 'out_qty', 'pieces', 'per_piece',
                  'price', 'amount', 'note', 'batch'):
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
            # names（流水内置别名）和 mat（用户自定义别名）要合起来看。
            # 原来写成 names.get(f) or mat.get(f)：name/code 在 names 里本来就有，
            # 于是用户自己起的表头名永远轮不到，改名后导入就认不出来了。
            pool = list(names.get(f) or [])
            for x in (mat.get(f) or []):
                if x not in pool:
                    pool.append(x)
            if n in [_norm(x) for x in pool]:
                out[i] = f; break
            # 兜底：表头带单位（界面显示「长（米）」，使用者照抄成「长（米）」
            # 「长 (米) 」等写法）时，去掉括号内容再匹配一次，避免这一列
            # 认不出来、值静默丢失。
            n_bare = re.sub(r'\(.*?\)', '', n).strip()
            if n_bare and n_bare != n:
                pool_bare = [re.sub(r'\(.*?\)', '', _norm(x)).strip() for x in pool]
                if n_bare in pool_bare:
                    out[i] = f; break
    return out

def _read_grid(path=None, stream=None, filename=''):
    """把 xlsx/xlsm/xls/csv 读成二维列表。

    选表：很多人第一张表是封面/说明，真正的数据在后面。
    以前直接取"第一张非空表"，但封面通常也有几个字，照样会选中它，
    结果表头认不出、导入 0 条，用户只会看到"没认出表头"，
    完全想不到是程序选错了表。
    现在分两轮：先找表头能被认出来的那张；都不认识才退回第一张非空表
    （至少让用户看到诊断网格，可以手动指定列）。
    """
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
    cand = []
    for nm in wb.sheetnames:
        rows = [[c for c in r] for r in wb[nm].iter_rows(values_only=True)]
        rows = [r for r in rows if any(_norm(c) for c in r)]
        if rows:
            cand.append(rows)
    if not cand:
        return []
    for rows in cand:                       # 第一轮：表头可识别的优先
        for i in range(min(5, len(rows))):
            try:
                if map_txn_headers(rows[i]):
                    return rows
            except Exception:
                pass
    return cand[0]                          # 第二轮：退回第一张非空表


def parse_txn_by_map(path=None, stream=None, filename='', colmap=None, start=1,
                     default_kind=None, defdate=None):
    """按用户手动指定的列来解析。colmap: {列索引: 字段名}，start: 数据起始行(0基)
    字段名取值 name/code/date/kind/qty/in_qty/out_qty/pieces/per_piece/
    price/amount/note，未列出的列忽略。
    金额(amount)可以不填，由 数量×单价 自动算出；只填金额时反推单价。"""
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
                    batch=str(val('batch') or '').strip(),
                    pieces=_num(val('pieces'), hi=QTY_MAX) or None,
                    per_piece=_num(val('per_piece'), hi=QTY_MAX) or None)
        for f in TXN_MAT_COLS:
            v = val(f)
            base[f] = '' if v is None else str(v).strip()
            if f in ('opening', 'safety'):
                base[f] = _num(v)
        ins = _num(val('in_qty'), hi=QTY_MAX); outs = _num(val('out_qty'), hi=QTY_MAX)
        q = _num(val('qty'), hi=QTY_MAX)
        pcs = _num(val('pieces'), hi=QTY_MAX); per = _num(val('per_piece'), hi=QTY_MAX)
        if not q and pcs and per:
            q = round(pcs * per, 2)
        # 单价：表里直接给"单价"最好；只给"金额"时按 金额÷数量 反推。
        # 两个都给了以单价为准（金额只是展示值，不入库，避免对不上）。
        pr = _num(val('price'))
        if not pr:
            am = _num(val('amount'))
            if am and q:
                pr = round(am / q, 4)
        pr = pr or None
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
            d = dict(base); d['kind'] = kind; d['qty'] = qty; d['price'] = pr
            out.append(d)
    return out

def sniff_grid(grid, n=6, w=14):
    """给诊断用：原始前几行前几列"""
    return [[('' if c is None else str(c))[:18] for c in (r or [])[:w]]
            for r in grid[:n]]

def parse_txn_file(path=None, stream=None, filename='', aliases=None, mat_aliases=None,
                   default_kind=None, defmonth=None, xcols=None):
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
        skipped = ([c for c, v in enumerate(raw[0]) if _is_sum_col(v)]
                   if raw else [])
        if wrows:
            # 原表常把下月 1 号也画进来（"9月表"里含 10-01 列），
            # 不提示的话用户会以为只导了本月，回头对不上账
            yms = {}
            for r in wrows:
                ym = str(r.get('date') or '')[:7]
                if ym: yms[ym] = yms.get(ym, 0) + 1
            return wrows, wfields, -2, {'why': 'wide', 'grid': sniff_grid(raw),
                                        'yms': yms, 'skipped_sum': len(skipped)}
        return [], set(), -1, {
            'why': 'no-header',
            'grid': sniff_grid(raw),
            'heads': [str(v or '') for v in (raw[0] if raw else [])][:14],
        }

    # 自定义列（客户订单号之类）：表头里能按列名对上的，一并读进来
    if xcols:
        for i, h in enumerate(raw[hi]):
            if i in hmap:
                continue
            n = _norm(h)
            if not n:
                continue
            for x in xcols:
                # 自定义列也可能配了单位，界面显示「膜厚（丝）」，使用者照抄
                # 过来就是这个写法；表头还可能带空格。精确匹配不上时，
                # 去掉括号内容再比一次，否则这一列静默丢失。
                _lb = x['label'] or ''
                _u = (x['unit'] if 'unit' in x.keys() else '') or ''
                _hit = False
                for _w in ([_lb]
                           + (['%s（%s）' % (_lb, _u), '%s(%s)' % (_lb, _u)] if _lb and _u else [])):
                    if n == _norm(_w):
                        _hit = True; break
                if not _hit and _lb:
                    if re.sub(r'\(.*?\)', '', n).strip() == re.sub(r'\(.*?\)', '', _norm(_lb)).strip():
                        _hit = True
                if _hit:
                    hmap[i] = x['fid']
                    break

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
        # 「总计/合计」这类汇总行是上面各行的和，导进来会凭空多一笔
        if _nm_sum_word(name):
            continue
        base = dict(name=name or code, code=code,
                    date=_fmt_date(val('date')),
                    # 表里写了「数量单位」列就采信（按平米 / 按卷），
                    # 决定卷料列是「数量÷一卷平米」还是直接等于数量
                    qty_unit=str(val('qty_unit') or '').strip(),
                    batch=str(val('batch') or '').strip(),
                    note=str(val('note') or '').strip(),
                    pieces=_num(val('pieces'), hi=QTY_MAX) or None,
                    per_piece=_num(val('per_piece'), hi=QTY_MAX) or None)
        for f in TXN_MAT_COLS:
            v = val(f)
            base[f] = '' if v is None else str(v).strip()
            if f in ('opening', 'safety'):
                base[f] = _num(v, hi=QTY_MAX)
        # 「单位（卷）」这类表头，列里填的其实是卷数（50、9.999999999999998），
        # 不是单位名 —— 直接存会把 unit 存成数字（v2.1 已在宽表路径修过，
        # 这是普通表路径，同一处陷阱）。真实单位从表头括号里取。
        _uc = [k for k, f in hmap.items() if f == 'unit']
        if _uc:
            _u0 = _uc[0]
            _fixed = unit_from_label(_head_at(raw, [hi], _u0),
                                     [r[_u0] if _u0 < len(r) else None for r in raw[hi + 1:hi + 31]])
            if _fixed:
                base['unit'] = _fixed
            elif _is_num_col([r[_u0] if _u0 < len(r) else None for r in raw[hi + 1:hi + 31]]):
                base['unit'] = ''        # 整列是数字又不认得单位 -> 留白，别塞数字
        # 自定义列的值：hmap 里已按列名对上，这里原样带出去
        for k, f in hmap.items():
            if str(f).startswith('x_') and k < len(r):
                base[f] = '' if r[k] is None else str(r[k]).strip()
        # 数量：优先 进/出 分列，其次 数量 + 进出方向
        # 都加 QTY_MAX 上限：表里多输几个零不该把库存冲到天文数字
        ins = _num(val('in_qty'), hi=QTY_MAX)
        outs = _num(val('out_qty'), hi=QTY_MAX)
        q = _num(val('qty'), hi=QTY_MAX)
        pcs = _num(val('pieces'), hi=QTY_MAX)
        per = _num(val('per_piece'))
        if not q and pcs and per:      # 只有件数×每件 -> 总数量
            q = round(pcs * per, 2)
        # 单价：优先表里的"单价"列；只给"金额"时按 金额÷数量 反推
        pr = _num(val('price'))
        if not pr:
            am = _num(val('amount'))
            if am and q:
                pr = round(am / q, 4)
        pr = pr or None
        base['qty_computed'] = bool(pcs and per and not _num(val('qty')))
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
            d = dict(base); d['kind'] = kind; d['qty'] = qty; d['price'] = pr
            out.append(d)
    if not out:
        return out, set(hmap.values()), hi, {
            'why': 'no-data', 'grid': sniff_grid(raw),
            'heads': [str(v or '') for v in raw[hi]][:14],
            'hi': hi,
        }
    return out, set(hmap.values()), hi, {'why': 'long', 'hi': hi}
