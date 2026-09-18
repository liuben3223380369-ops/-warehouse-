# -*- coding: utf-8 -*-
"""电子表格接口 · 编辑类动作

    读取网格 / 写入 / 填充 / 增删行列
"""
from .common import *                                  # noqa: F401,F403
from .common import (_push, _snap)   # 星号不导入下划线名，必须显式写
from .common import F, ST, check_validation, datetime, re

def a_grid(book, st, sh, g, rect):
    r1, c1, r2, c2 = rect
    cells = []
    for i in range(r1, r2 + 1):
        row = []
        for j in range(c1, c2 + 1):
            cl = sh.get(i, j)
            if cl is None:
                row.append(None)
                continue
            v = sh.value(i, j) if cl.kind == 'formula' else cl.v
            d = {'v': _j(v), 't': cl.display() if cl.kind != 'formula'
                 else ST.format_value(v, cl.fmt),
                 'raw': cl.raw, 'k': cl.kind}
            if cl.fmt:
                d['f'] = cl.fmt
            if cl.style:
                d['s'] = cl.style.to_dict()
            if cl.note:
                d['n'] = cl.note
            if cl.bad:
                d['bad'] = cl.bad
            row.append(d)
        cells.append(row)
    return {'cells': cells,
            'rows': sh.rows, 'cols': sh.cols,
            'merges': sh.merges,
            'frozen': [sh.frozen_rows, sh.frozen_cols],
            'widths': sh.col_width,
            'heights': sh.row_height,
            'cond': [x.to_dict() for x in sh.cond],
            'sheets': [{'name': s.name, 'hidden': s.hidden,
                        'color': s.tab_color} for s in book.sheets],
            'active': book.sheets.index(sh) if sh in book.sheets else 0,
            'used': sh.used_range(),
            'names': book.names,
            'hidden_rows': sorted(sh.hidden_rows),
            'hidden_cols': sorted(sh.hidden_cols),
            'validations': sh.validations,
            'charts': sh.charts,
            'undo': len(st['undo']), 'redo': len(st['redo'])}

def col_name(i):
    from ..kernel import col_letter
    return col_letter(i)

def _j(v):
    if isinstance(v, (list, tuple)):
        return [_j(x) for x in v]
    if isinstance(v, bool) or isinstance(v, (int, float)):
        return v
    if v is None:
        return ''
    return str(v)

def a_set(book, st, sh, g, rect):
    edits = g.get('edits') or []
    if not edits:
        return {'err': '没有要写的内容'}
    rs = [int(e[0]) for e in edits]
    cs = [int(e[1]) for e in edits]
    _push(st, '写入', sh.name, _snap(sh, min(rs), min(cs), max(rs), max(cs)))
    n = 0
    warn = []
    for e in edits:
        r, c = int(e[0]), int(e[1])
        txt = e[2] if len(e) > 2 else ''
        if txt and sh.validations:
            _w = check_validation(sh, r, c, txt)
            if _w:
                warn.append('%s%d：%s' % (col_name(c), r + 1, _w))
        _, changed = sh.set_raw(r, c, txt)
        n += 1 if changed else 0
    out = {'n': n}
    if warn:
        # 拦下来，但已经写进去的部分保留 —— 提示里说清第几格不合规
        out['warn'] = warn[:6]
        out['warn_n'] = len(warn)
    return out

def _contig(sh, a, b, c1, c2, by_row=True):
    """从起始位置往下（往右）找连续有内容的行（列），返回源区下标列表。

    至少返回起始那一行 —— Excel 拖一个格子也是"复制"，不是什么都不做。
    """
    out = [a]
    for k in range(a + 1, b + 1):
        has = False
        for j in range(c1, c2 + 1):
            v = sh.raw(k, j) if by_row else sh.raw(j, k)
            if v not in ('', None):
                has = True
                break
        if not has:
            break
        out.append(k)
    return out

def _n2s(v):
    """数字转回文本，去掉浮点长尾（0.30000000000000004 → 0.3）"""
    if isinstance(v, bool):
        return str(v)
    r = round(v, 10)
    if abs(r - int(r)) < 1e-9:
        return str(int(r))
    return str(r)

_DAY = datetime.timedelta(days=1)

_RE_DATE = re.compile(r'^(\d{4})[-/](\d{1,2})[-/](\d{1,2})')

def _step_vals(sh, raws):
    """给一串源值算外推步长。返回 (kind, vals_or_step)

    kind: 'num' 等差外推 / 'date' 日期递增 / 'copy' 原样循环复制
    """
    if any(str(v).startswith('=') for v in raws if v not in ('', None)):
        return ('copy', raws)
    nums = [F.to_num(v) for v in raws]
    if len(raws) >= 2 and all(x is not None for x in nums):
        return ('num', nums)
    d0 = _RE_DATE.match(str(raws[0]).strip())
    if d0:
        try:
            d = datetime.date(int(d0.group(1)), int(d0.group(2)), int(d0.group(3)))
            if len(raws) >= 2:
                d1 = _RE_DATE.match(str(raws[1]).strip())
                if d1:
                    dd = datetime.date(int(d1.group(1)), int(d1.group(2)),
                                       int(d1.group(3)))
                    return ('date', [d, (dd - d).days or 1])
            return ('date', [d, 1])
        except Exception:
            pass
    return ('copy', raws)

def a_fill(book, st, sh, g, rect):
    """填充（拖填充柄 / Ctrl+D / Ctrl+R）

    跟 Excel 一个脾气：源区只有一格就复制；有规律（等差、日期）就延续规律；
    源是公式就按相对引用平移着复制。
    """
    r1, c1, r2, c2 = rect
    dir_ = (g.get('dir') or 'down')
    _push(st, '填充', sh.name, _snap(sh, r1, c1, r2, c2))

    if dir_ in ('down', 'up'):
        srcs = _contig(sh, r1, r2, c1, c2, True) if dir_ == 'down' else \
               _contig(sh, r2, r1, c1, c2, True)
        if dir_ == 'up':
            srcs = list(reversed(srcs))
        n = len(srcs)
        if r1 + n > r2:                      # 没有目标行，Excel 也是什么都不做
            return {}
        for j in range(c1, c2 + 1):
            raws = [sh.raw(i, j) for i in srcs]
            kind, info = _step_vals(sh, raws)
            for k, i in enumerate(range(r1 + n, r2 + 1)):
                if kind == 'num':
                    step = (info[-1] - info[0]) / (len(info) - 1)
                    sh.set_raw(i, j, _n2s(info[-1] + step * (k + 1)))
                elif kind == 'date':
                    d, sd = info
                    nd = d + datetime.timedelta(days=sd * (k + 1 + len(srcs) - 1))
                    sh.set_raw(i, j, nd.strftime('%Y-%m-%d'))
                else:
                    s = srcs[k % n]
                    sh.set_raw(i, j, _shift_formula(sh.raw(s, j), i - s, 0))
    else:
        srcs = _contig(sh, c1, c2, r1, r2, False) if dir_ == 'right' else \
               _contig(sh, c2, c1, r1, r2, False)
        n = len(srcs)
        if c1 + n > c2:
            return {}
        for i in range(r1, r2 + 1):
            raws = [sh.raw(i, j) for j in srcs]
            kind, info = _step_vals(sh, raws)
            for k, j in enumerate(range(c1 + n, c2 + 1)):
                if kind == 'num':
                    step = (info[-1] - info[0]) / (len(info) - 1)
                    sh.set_raw(i, j, _n2s(info[-1] + step * (k + 1)))
                elif kind == 'date':
                    d, sd = info
                    nd = d + datetime.timedelta(days=sd * (k + 1 + len(srcs) - 1))
                    sh.set_raw(i, j, nd.strftime('%Y-%m-%d'))
                else:
                    s = srcs[k % n]
                    sh.set_raw(i, j, _shift_formula(sh.raw(i, s), 0, j - s))
    return {}

def _shift_formula(raw, dr, dc):
    """复制/填充时把相对引用按偏移平移（跟 Excel 一致）"""
    if not raw or not raw.startswith('='):
        return raw
    try:
        from ..kernel import parser as P
        import re
        ast = P.parse(raw)
        refs = P.refs_of(ast)
        if not refs:
            return raw
        # 逐个替换文本里的引用（按长度从长到短，避免 A1 命中 A10 的前缀）
        out = raw
        pairs = sorted(((r.to_str(), r.shift(dr, dc).to_str()) for r in refs),
                       key=lambda x: -len(x[0]))
        for old, new in pairs:
            if old == new:
                continue
            out = re.sub(r'(?<![A-Za-z0-9_$!])' + re.escape(old) + r'(?![0-9])',
                         new.replace('\\', '\\\\'), out)
        return out
    except Exception:
        return raw

def a_rows(book, st, sh, g, rect):
    op = g.get('op') or 'insert'
    r1, c1, r2, c2 = rect
    n = max(1, int(g.get('n') or 1))
    _push(st, '行', sh.name, _snap(sh, 0, 0, sh.rows - 1, sh.cols - 1))
    if op == 'insert':
        sh.insert_rows(r1, n)
    else:
        sh.delete_rows(r1, n)
    return {}

def a_cols(book, st, sh, g, rect):
    op = g.get('op') or 'insert'
    r1, c1, r2, c2 = rect
    n = max(1, int(g.get('n') or 1))
    _push(st, '列', sh.name, _snap(sh, 0, 0, sh.rows - 1, sh.cols - 1))
    if op == 'insert':
        sh.insert_cols(c1, n)
    else:
        sh.delete_cols(c1, n)
    return {}
