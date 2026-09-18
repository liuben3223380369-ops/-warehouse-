# -*- coding: utf-8 -*-
"""进阶动作·分析：图表取数、透视表、区域统计、分类汇总"""
from ..kernel import funcs as F
from ..kernel import style as ST
from ..kernel import col_letter
from .undo import push as _push, snap as _snap, _undo_hooks


def _disp(sh, r, c):
    cl = sh.get(r, c)
    if not cl:
        return ''
    try:
        v = sh.value(r, c)
    except Exception:
        v = cl.raw
    if v is None:
        return ''
    # 整数别显示成 10.0 —— 表头/分类轴上出现这种最扎眼
    if isinstance(v, float) and v == int(v) and abs(v) < 1e15:
        return str(int(v))
    return str(v)


def _num(sh, r, c):
    v = None
    try:
        v = sh.value(r, c)
    except Exception:
        pass
    n = F.to_num(v)
    return n if n is not None else 0


# ------------------------------------------------------------------ 图表取数
def a_chart(book, st, sh, g, rect):
    """按区域取图表数据。首行/首列可当分类轴（跟 Excel 一个习惯）。

    返回 categories + series，前端直接画 SVG，不引第三方图表库。
    """
    r1, c1, r2, c2 = rect
    ctype = g.get('type') or 'bar'
    by = g.get('by') or 'col'          # col=每列一条系列；row=每行一条
    header = str(g.get('header', '1')) in ('1', 'true', 'True')
    save = str(g.get('save', '0')) in ('1', 'true', 'True')
    if save:
        sh.charts.append({'type': ctype, 'rect': [r1, c1, r2, c2],
                          'by': by, 'header': header,
                          'title': g.get('title') or ''})
    cats, series = [], []
    if by == 'col':
        start = r1 + 1 if header else r1
        cats = [_disp(sh, i, c1) for i in range(start, r2 + 1)]
        for j in range(c1 + 1, c2 + 1):
            name = _disp(sh, r1, j) if header else col_letter(j)
            series.append({'name': name or col_letter(j),
                           'data': [_num(sh, i, j) for i in range(start, r2 + 1)]})
    else:
        start = c1 + 1 if header else c1
        cats = [_disp(sh, r1, j) for j in range(start, c2 + 1)]
        for i in range(r1 + 1, r2 + 1):
            name = _disp(sh, i, c1) if header else str(i + 1)
            series.append({'name': name or str(i + 1),
                           'data': [_num(sh, i, j) for j in range(start, c2 + 1)]})
    return {'type': ctype, 'categories': cats, 'series': series,
            'title': g.get('title') or '', 'charts': sh.charts}


# ------------------------------------------------------------------ 透视表
def a_pivot(book, st, sh, g, rect):
    """透视表：行字段 + 列字段 + 值字段（求和/计数/平均/最大/最小）。

    典型用法：一堆流水，按「物料」分组、按「月份」拆列，看每个月进了多少。
    """
    r1, c1, r2, c2 = rect
    header = str(g.get('header', '1')) in ('1', 'true', 'True')
    rows_f = [int(x) for x in (g.get('rows') or [])]
    cols_f = [int(x) for x in (g.get('cols') or [])]
    vals_f = g.get('vals') or []          # [[列号, 聚合方式], ...]
    start = r1 + 1 if header else r1
    data = {}
    rkeys, ckeys = [], []
    for i in range(start, r2 + 1):
        rk = tuple(_disp(sh, i, x) for x in rows_f) or ('',)
        ck = tuple(_disp(sh, i, x) for x in cols_f) or ('',)
        if rk not in rkeys:
            rkeys.append(rk)
        if ck not in ckeys:
            ckeys.append(ck)
        data.setdefault((rk, ck), []).append(i)
    ckeys.sort()
    rkeys.sort()
    heads = []
    if rows_f:
        heads += [''] * len(rows_f)
    for ck in ckeys:
        for vi, (_c, _agg) in enumerate(vals_f):
            heads.append(' / '.join([x for x in ck if x] +
                                    ([_agg] if len(vals_f) > 1 else [])))
    out = []
    for rk in rkeys:
        line = list(rk)
        for ck in ckeys:
            for ci, agg in vals_f:
                idxs = data.get((rk, ck), [])
                vs = [F.to_num(sh.value(i, int(ci))) for i in idxs]
                vs = [v for v in vs if v is not None]
                if agg == '计数':
                    line.append(len(idxs))
                elif agg == '计数(非空)':
                    line.append(len(vs))
                elif not vs:
                    line.append(0)
                elif agg == '平均':
                    line.append(round(sum(vs) / len(vs), 6))
                elif agg == '最大':
                    line.append(max(vs))
                elif agg == '最小':
                    line.append(min(vs))
                else:
                    line.append(round(sum(vs), 6))
        out.append(line)
    # 合计行
    if str(g.get('total', '1')) in ('1', 'true', 'True') and out:
        tot = ['合计'] + [''] * (len(rows_f) - 1 if rows_f else 0)
        for k in range(len(rows_f) if rows_f else 1, len(out[0])):
            vs = [r[k] for r in out if isinstance(r[k], (int, float))]
            tot.append(round(sum(vs), 6) if vs else '')
        out.append(tot)
    return {'headers': heads, 'rows': out}


# ------------------------------------------------------------------ 区域统计
def a_stat(book, st, sh, g, rect):
    """状态栏那几个数：求和 / 平均 / 计数 / 最大 / 最小"""
    r1, c1, r2, c2 = rect
    vs = []
    for i in range(r1, r2 + 1):
        for j in range(c1, c2 + 1):
            try:
                v = sh.value(i, j)
            except Exception:
                v = None
            n = F.to_num(v)
            if n is not None:
                vs.append(n)
    if not vs:
        return {'count': 0}
    return {'count': len(vs), 'sum': round(sum(vs), 6),
            'avg': round(sum(vs) / len(vs), 6),
            'max': max(vs), 'min': min(vs)}


# ------------------------------------------------------------------ 分类汇总
_FN = {'sum': 'SUM', 'count': 'COUNT', 'avg': 'AVERAGE',
       'max': 'MAX', 'min': 'MIN'}


def a_subtotal(book, st, sh, g, rect):
    """按分组列分段，每段末尾插一行小计（写成公式，改数据自动重算）"""
    push, snap = _undo_hooks()
    gc = int(g.get('gc') or 0)
    sc = int(g.get('sc') or 0)
    how = g.get('how') or 'sum'
    fn = _FN.get(how, 'SUM')
    r1 = int(g.get('r1') or 0)
    r2 = int(g.get('r2') if g.get('r2') is not None else rect[2])
    c2 = int(g.get('c2') if g.get('c2') is not None else rect[3])
    if r2 <= r1:
        return {'err': '数据行范围为空'}

    # 先扫出每一段（分组列值相同的连续行算一段）
    segs = []
    start = r1
    prev = ('%s' % (sh.value(r1, gc) if sh.value(r1, gc) is not None
                    else '')).strip()
    for i in range(r1 + 1, r2 + 1):
        cur = ('%s' % (sh.value(i, gc) if sh.value(i, gc) is not None
                       else '')).strip()
        if cur != prev:
            segs.append((start, i - 1, prev))
            start, prev = i, cur
    segs.append((start, r2, prev))
    if len(segs) <= 1 and not any('%s' % (sh.value(a, gc) or '').strip()
                                  for a, b, _ in segs):
        return {'err': '分组列没有内容，先选对列'}

    push(st, '分类汇总', sh.name, snap(sh, r1, 0, r2, c2))
    cl = col_letter(sc)
    # 从下往上插，避免插一行把下面的段号顶乱
    for a, b, name in reversed(segs):
        at = b + 1
        sh.insert_rows(at, 1)
        tgt = sh.cell(at, gc, create=True)
        if tgt is not None:
            tgt.style = (tgt.style or ST.Style())
            tgt.style.bold = True
        cell = sh.cell(at, sc, create=True)
        if cell is None:
            continue
        cell.style = (cell.style or ST.Style())
        cell.style.bold = True
        sh.set_raw(at, sc, '=%s(%s%d:%s%d)' % (fn, cl, a + 1, cl, b + 1))
        lab = sh.cell(at, gc, create=True)
        if lab is not None and not ('%s' % (lab.v or '')).strip():
            sh.set_raw(at, gc, '%s 小计' % (name or ''))
    return {'n': len(segs)}
