# -*- coding: utf-8 -*-
"""表格模块 · 电子表格的进阶动作

这里放的是"让它更像 Excel / WPS"的那批操作：
剪贴板（复制/剪切/粘贴，公式相对引用跟着走）、自动求和、序列填充、
隐藏行列、数据有效性、图表取数、透视表、按类型清除。

每个动作签名统一为 (book, st, sh, g, rect) -> dict，由 sp_routes 的
_ACTION 表登记后即可通过 /sheet/api/<bid>/<act> 调用。
"""
import datetime
import calendar

from . import sp_style as ST
from . import sp_funcs as F
from .sp_addr import shift_formula, col_letter


# ------------------------------------------------------------------ 剪贴板
def a_clip(book, st, sh, g, rect):
    """复制 / 剪切 / 粘贴。

    粘贴时公式的相对引用会按偏移量平移 —— 这是"复制公式"的灵魂：
    把 C1 的 =A1+B1 粘到 C2，应该变成 =A2+B2，而不是原封不动。
    """
    op = g.get('op') or 'copy'
    if op in ('copy', 'cut'):
        r1, c1, r2, c2 = rect
        cells = []
        for i in range(r1, r2 + 1):
            row = []
            for j in range(c1, c2 + 1):
                cl = sh.get(i, j)
                row.append([cl.raw if cl else '',
                            cl.fmt if cl else '',
                            cl.style.to_dict() if (cl and cl.style) else None,
                            cl.note if cl else ''])
            cells.append(row)
        st['clip'] = {'sheet': sh.name, 'r1': r1, 'c1': c1,
                      'r2': r2, 'c2': c2, 'cells': cells, 'cut': op == 'cut'}
        if op == 'cut':
            _push_snap(st, '剪切', sh, r1, c1, r2, c2)
            for i in range(r1, r2 + 1):
                for j in range(c1, c2 + 1):
                    sh.set_raw(i, j, '')
            sh.invalidate()
        return {'n': (r2 - r1 + 1) * (c2 - c1 + 1), 'clip': 1}

    if op != 'paste':
        return {'err': '不认识的剪贴板操作：%s' % op}

    cb = st.get('clip')
    if not cb:
        return {'err': '剪贴板是空的，先复制或剪切'}
    mode = g.get('mode') or 'all'          # all/formula/value/format
    tr = bool(g.get('transpose'))
    tr1, tc1 = int(g.get('r1') or 0), int(g.get('c1') or 0)
    src = cb['cells']
    nr, nc = len(src), len(src[0]) if src else 0
    if tr:
        if not src:
            return {'err': '剪贴板是空的'}
        nr, nc = len(src[0]), len(src)

    _push_snap(st, '粘贴', sh, tr1, tc1, tr1 + nr - 1, tc1 + nc - 1)
    dr = tr1 - cb['r1']
    dc = tc1 - cb['c1']
    n = 0
    for i in range(nr):
        for j in range(nc):
            si, sj = (j, i) if tr else (i, j)      # 转置：源按列读
            if si >= len(src) or sj >= len(src[si]):
                continue
            raw, fmt, style, note = src[si][sj]
            ti, tj = tr1 + i, tc1 + j
            cl = sh.cell(ti, tj, create=True)
            if cl is None:
                continue
            if mode in ('all', 'value'):
                if mode == 'value':
                    # 只粘计算后的值：公式变死数，引用不再跟着走
                    try:
                        v = sh.value(cb['r1'] + si, cb['c1'] + sj)
                    except Exception:
                        v = ''
                    if v is None:
                        v = ''
                    txt = '' if v == '' else (
                        F.to_text(v) if not isinstance(v, (int, float)) else str(v))
                    cl.raw = txt
                else:
                    txt = shift_formula(raw, dr, dc) if raw.startswith('=') else raw
                    cl.raw = txt
                cl.ast = None
                cl.bad = ''
                sh._classify(cl)
            if mode in ('all', 'format') and style:
                cl.style = ST.Style.from_dict(style)
            if mode == 'all':
                cl.fmt = fmt or ''
                cl.note = note or ''
            n += 1
    sh.invalidate()
    if cb.get('cut'):
        st['clip'] = None                  # 剪切是一次性的
    return {'n': n}


def _push_snap(st, label, sh, r1, c1, r2, c2):
    from . import sp_routes as R
    R._push(st, label, sh.name, R._snap(sh, r1, c1, r2, c2))


# ------------------------------------------------------------------ 自动求和
def a_autosum(book, st, sh, g, rect):
    """Σ：在目标格插 =SUM(上方或左方的连续数字区)。

    上方没数字就看左边，两边都没有就给一个空括号让用户自己选区域。
    """
    r, c = int(g.get('r1') or 0), int(g.get('c1') or 0)
    fn = (g.get('fn') or 'SUM').upper()

    def isnum(i, j):
        cl = sh.get(i, j)
        if not cl or cl.kind == 'formula' or cl.raw == '':
            return False
        v = sh.value(i, j)
        return isinstance(v, (int, float)) and not isinstance(v, bool)

    # 往上找连续数字
    top = None
    i = r - 1
    while i >= 0 and isnum(i, c):
        top = i
        i -= 1
    if top is not None:
        expr = '=%s(%s%d:%s%d)' % (fn, col_letter(c), top + 1, col_letter(c), r)
    else:
        left = None
        j = c - 1
        while j >= 0 and isnum(r, j):
            left = j
            j -= 1
        if left is not None:
            expr = '=%s(%s%d:%s%d)' % (fn, col_letter(left), r + 1,
                                       col_letter(c - 1), r + 1)
        else:
            expr = '=%s()' % fn
    _push_snap(st, '自动求和', sh, r, c, r, c)
    sh.set_raw(r, c, expr)
    return {'expr': expr}


# ------------------------------------------------------------------ 隐藏行列
def a_hide(book, st, sh, g, rect):
    """隐藏 / 取消隐藏行或列。Excel 里右键菜单最常用的两个。"""
    what = g.get('what') or 'rows'
    on = str(g.get('on', '1')) in ('1', 'true', 'True', 'yes')
    idx = g.get('idx')
    if idx is None:
        r1, c1, r2, c2 = rect
        idx = list(range(r1, r2 + 1)) if what == 'rows' else list(range(c1, c2 + 1))
    if not isinstance(idx, (list, tuple)):
        idx = [idx]
    idx = [int(x) for x in idx]
    tgt = sh.hidden_rows if what == 'rows' else sh.hidden_cols
    for x in idx:
        if on:
            tgt.add(x)
        else:
            tgt.discard(x)
    return {'hidden_rows': sorted(sh.hidden_rows),
            'hidden_cols': sorted(sh.hidden_cols)}


# ------------------------------------------------------------------ 序列填充
def a_series(book, st, sh, g, rect):
    """序列填充：等比/等差/日期/工作日/月/年，还能给终止值。

    Excel 的「序列」对话框就这几样；拖填充柄走的是简化版（见 a_fill）。
    """
    r1, c1, r2, c2 = rect
    stype = g.get('type') or 'linear'
    step = F.to_num(g.get('step'))
    step = 1 if step is None else step
    stop = F.to_num(g.get('stop'))
    down = (r2 - r1) >= (c2 - c1)
    _push_snap(st, '序列填充', sh, r1, c1, r2, c2)

    cur = sh.value(r1, c1)
    if isinstance(cur, str) and cur.strip() == '':
        return {'err': '起始格是空的，先填个起始值'}
    if stype in ('date', 'weekday', 'month', 'year'):
        cur = _to_date(cur)
        if cur is None:
            return {'err': '起始格不是日期'}
    else:
        cur = F.to_num(cur)
        if cur is None:
            return {'err': '起始格不是数字'}

    n = (r2 - r1 + 1) if down else (c2 - c1 + 1)
    k = 0
    for t in range(1, n):
        k = t
        if stype == 'linear':
            v = cur + step * t
        elif stype == 'growth':
            v = cur * (step ** t)
        elif stype == 'date':
            v = cur + datetime.timedelta(days=step * t)
        elif stype == 'weekday':
            v = cur + datetime.timedelta(days=step * t)
            while v.weekday() >= 5:              # 跳过周六周日
                v += datetime.timedelta(days=1 if step >= 0 else -1)
        elif stype == 'month':
            v = _add_month(cur, step * t)
        else:                                    # year
            v = _add_month(cur, 12 * step * t)
        if stop is not None:
            nv = v if not isinstance(v, datetime.date) else v.toordinal()
            if (step >= 0 and nv > stop) or (step < 0 and nv < stop):
                break
        i, j = (r1 + t, c1) if down else (r1, c1 + t)
        if isinstance(v, datetime.date):
            sh.set_raw(i, j, v.strftime('%Y-%m-%d'))
        else:
            v = round(v, 10)
            sh.set_raw(i, j, str(int(v)) if float(v).is_integer() else str(v))
    sh.invalidate()
    return {'n': k}


def _to_date(v):
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    if isinstance(v, (int, float)):
        try:
            return (datetime.date(1899, 12, 30) +
                    datetime.timedelta(days=float(v)))
        except (ValueError, OverflowError):
            return None
    s = str(v or '').strip()
    for f in ('%Y-%m-%d', '%Y/%m/%d', '%Y年%m月%d日', '%m/%d/%Y'):
        try:
            return datetime.datetime.strptime(s, f).date()
        except ValueError:
            continue
    return None


def _add_month(d, months):
    m = d.month - 1 + int(months)
    y = d.year + m // 12
    m = m % 12 + 1
    day = min(d.day, calendar.monthrange(y, m)[1])
    return datetime.date(y, m, day)


# ------------------------------------------------------------------ 数据有效性
def a_validate(book, st, sh, g, rect):
    """数据有效性：下拉列表 / 整数小数区间 / 日期区间 / 文本长度。"""
    r1, c1, r2, c2 = rect
    vtype = g.get('type') or 'any'
    rule = {'rect': [r1, c1, r2, c2], 'type': vtype,
            'op': g.get('op') or '',
            'v1': g.get('v1') or '', 'v2': g.get('v2') or '',
            'items': [x for x in (g.get('items') or '').replace('，', ',').split(',')
                      if str(x).strip()],
            'msg': g.get('msg') or ''}
    if g.get('clear'):
        sh.validations = [v for v in sh.validations
                          if v.get('rect') != [r1, c1, r2, c2]]
    else:
        sh.validations = [v for v in sh.validations
                          if v.get('rect') != [r1, c1, r2, c2]]
        if vtype != 'any' or rule['items']:
            sh.validations.append(rule)
    return {'validations': sh.validations}


def check_validation(sh, r, c, text):
    """写入时校验一格是否合规，返回提示（合规返回 ''）"""
    for v in sh.validations or []:
        rc = v.get('rect') or [0, 0, 0, 0]
        if not (rc[0] <= r <= rc[2] and rc[1] <= c <= rc[3]):
            continue
        t = v.get('type')
        if t == 'list':
            items = v.get('items') or []
            if text not in items:
                return '只能填：%s' % '、'.join(items[:8])
        elif t in ('int', 'dec'):
            n = F.to_num(text)
            if n is None:
                return '要填数字'
            if t == 'int' and abs(n - int(n)) > 1e-9:
                return '要填整数'
            lo, hi = F.to_num(v.get('v1')), F.to_num(v.get('v2'))
            if lo is not None and n < lo:
                return '不能小于 %s' % v.get('v1')
            if hi is not None and n > hi:
                return '不能大于 %s' % v.get('v2')
        elif t == 'len':
            lo, hi = F.to_num(v.get('v1')), F.to_num(v.get('v2'))
            n = len(str(text or ''))
            if lo is not None and n < lo:
                return '至少 %d 个字' % lo
            if hi is not None and n > hi:
                return '最多 %d 个字' % hi
    return ''


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


# ------------------------------------------------------------------ 按类型清除
def a_clear(book, st, sh, g, rect):
    """清除内容 / 格式 / 批注 / 全部。Excel 的「清除」是个小菜单。"""
    r1, c1, r2, c2 = rect
    what = g.get('what') or 'content'
    _push_snap(st, '清除', sh, r1, c1, r2, c2)
    n = 0
    for i in range(r1, r2 + 1):
        for j in range(c1, c2 + 1):
            cl = sh.get(i, j)
            if not cl:
                continue
            if what in ('content', 'all'):
                cl.raw = ''
                cl.ast = None
                cl.bad = ''
                sh._classify(cl)
            if what in ('format', 'all'):
                cl.style = None
                cl.fmt = ''
            if what in ('note', 'all'):
                cl.note = ''
            n += 1
    sh.invalidate()
    return {'n': n}


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


# ------------------------------------------------------------------ 行列高宽
def a_height(book, st, sh, g, rect):
    """行高 / 列宽（字符数）"""
    what = g.get('what') or 'row'
    v = F.to_num(g.get('v'))
    r1, c1, r2, c2 = rect
    if what == 'row':
        for i in range(r1, r2 + 1):
            if v is None or v <= 0:
                sh.row_height.pop(i, None)
            else:
                sh.row_height[i] = int(v)
    else:
        for j in range(c1, c2 + 1):
            if v is None or v <= 0:
                sh.col_width.pop(j, None)
            else:
                sh.col_width[j] = float(v)
    return {'widths': sh.col_width, 'heights': sh.row_height}


# ------------------------------------------------------------------ 细致边框
def _undo_hooks():
    """延迟取撤销钩子。

    sp_routes 导入本模块时 _push/_snap 还没定义完，所以放到调用时再取。
    """
    from . import sp_routes
    return sp_routes._push, sp_routes._snap


def a_border(book, st, sh, g, rect):
    """细致边框：四边独立 + 线型 + 颜色，另支持"内部"线。

    bd 形如 {'t':[线型,色], 'b':..., 'l':..., 'r':..., 'cross':[线型,色]}。
    值为 'none' 表示去掉这条边。
    """
    push, snap = _undo_hooks()
    r1, c1, r2, c2 = rect
    bd = g.get('bd') or {}
    if not bd:
        return {'err': '没有指定要画哪些边'}
    push(st, '边框', sh.name, snap(sh, r1, c1, r2, c2))
    cross = bd.get('cross')

    def edge(cl, k, val):
        d = dict(cl.style.bd or {}) if cl.style else {}
        s = val[0] if isinstance(val, (list, tuple)) else val
        if str(s).lower() == 'none':
            d.pop(k, None)
        else:
            d[k] = [val[0], val[1]] if isinstance(val, (list, tuple)) \
                else [val, '#000000']
        base = cl.style or ST.Style()
        base.bd = d or None
        cl.style = base

    n = 0
    for i in range(r1, r2 + 1):
        for j in range(c1, c2 + 1):
            cl = sh.cell(i, j, create=True)
            if cl is None:
                continue
            for k, cond in (('t', i == r1), ('b', i == r2),
                            ('l', j == c1), ('r', j == c2)):
                if bd.get(k) and cond:
                    edge(cl, k, bd[k])
                    n += 1
            if cross:
                if j > c1:
                    edge(cl, 'l', cross)
                if i > r1:
                    edge(cl, 't', cross)
                n += 1
    return {'n': n}


# ------------------------------------------------------------------ 删除重复项
def a_dedupe(book, st, sh, g, rect):
    """按某一列判重，保留每组第一条，重复的整行删掉"""
    push, snap = _undo_hooks()
    c = int(g.get('c1') or 0)
    r1 = int(g.get('r1') or 0)
    r2 = int(g.get('r2') if g.get('r2') is not None else rect[2])
    c2 = int(g.get('c2') if g.get('c2') is not None else rect[3])
    if r2 <= r1:
        return {'err': '要判断的行范围为空'}
    push(st, '删除重复项', sh.name, snap(sh, r1, 0, r2, c2))
    seen = set()
    dels = []
    for i in range(r1, r2 + 1):
        v = sh.value(i, c)
        key = ('%s' % (v if v is not None else '')).strip().lower()
        if key in seen:
            dels.append(i)
        else:
            seen.add(key)
    # 从下往上删，删上面的行不会让下面的行号失效
    for i in reversed(dels):
        sh.delete_rows(i, 1)
    return {'n': len(dels), 'left': (r2 - r1 + 1) - len(dels)}


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


EXTRA = {
    'clip': a_clip, 'autosum': a_autosum, 'hide': a_hide, 'series': a_series,
    'validate': a_validate, 'chart': a_chart, 'pivot': a_pivot,
    'clear': a_clear, 'stat': a_stat, 'height': a_height,
    'border': a_border, 'dedupe': a_dedupe, 'subtotal': a_subtotal,
}
