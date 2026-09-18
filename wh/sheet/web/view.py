# -*- coding: utf-8 -*-
"""电子表格接口 · 视图类动作

    排序 / 筛选 / 查找 / 冻结 / 列宽 / 自适应
"""
from .common import *                                  # noqa: F401,F403
from .common import (_push, _snap)   # 星号不导入下划线名，必须显式写
from .common import F, ST

def a_sort(book, st, sh, g, rect):
    r1, c1, r2, c2 = rect
    keys = g.get('keys') or [[c1, 1]]
    _push(st, '排序', sh.name, _snap(sh, r1, c1, r2, c2))
    hdr = int(g.get('header') or 0)
    start = r1 + (1 if hdr else 0)
    order = list(range(start, r2 + 1))

    def keyf(i):
        out = []
        for col, asc in keys:
            col = int(col)
            v = sh.value(i, col)
            n = F.to_num(v)
            out.append(((n is None), (n if n is not None else 0),
                        F.to_text(v).lower(), -(1 if asc else -1)))
        return out
    order.sort(key=lambda i: [k[:-1] for k in keyf(i)])
    for col, asc in keys:
        if not asc:
            order.reverse()
            break
    data = [[sh.raw(i, j) for j in range(c1, c2 + 1)] for i in order]
    for k, i in enumerate(range(start, r2 + 1)):
        for jj, j in enumerate(range(c1, c2 + 1)):
            sh.set_raw(i, j, data[k][jj])
    sh.sort_spec = [(int(a), int(b)) for a, b in keys]
    return {}

def a_filter(book, st, sh, g, rect):
    """筛选：hide 是要隐藏的行号列表"""
    r1, c1, r2, c2 = rect
    hide = [int(x) for x in (g.get('hide') or [])]
    on = g.get('on')
    if on == '0':
        sh.filter = None
    else:
        sh.filter = {'r1': r1, 'c1': c1, 'r2': r2, 'c2': c2, 'hide': hide}
    return {}



def a_width(book, st, sh, g, rect):
    r1, c1, r2, c2 = rect
    w = g.get('w')
    if g.get('what') == 'row':
        if w:
            for i in range(r1, r2 + 1):
                sh.row_height[i] = float(w)
        else:
            for i in range(r1, r2 + 1):
                sh.row_height.pop(i, None)
    else:
        if w:
            for j in range(c1, c2 + 1):
                sh.col_width[j] = float(w)
        else:
            for j in range(c1, c2 + 1):
                sh.col_width.pop(j, None)
    return {}



def a_freeze(book, st, sh, g, rect):
    sh.frozen_rows = max(0, int(g.get('rows') or 0))
    sh.frozen_cols = max(0, int(g.get('cols') or 0))
    return {}



def a_find(book, st, sh, g, rect):
    kw = (g.get('kw') or '')
    rep = g.get('rep')
    if not kw:
        return {'hits': 0}
    r1, c1, r2, c2 = rect
    hits = []
    for i in range(r1, r2 + 1):
        for j in range(c1, c2 + 1):
            cl = sh.get(i, j)
            if not cl:
                continue
            hay = cl.raw if (g.get('inraw') == '1') else \
                (sh.value(i, j) if cl.kind == 'formula' else cl.v)
            if kw.lower() in F.to_text(hay).lower():
                hits.append([i, j])
    if rep is not None and hits:
        _push(st, '替换', sh.name,
              _snap(sh, min(h[0] for h in hits), min(h[1] for h in hits),
                    max(h[0] for h in hits), max(h[1] for h in hits)))
        for i, j in hits:
            cl = sh.get(i, j)
            old = cl.raw
            if old.startswith('='):
                new = old.replace(kw, rep)
            else:
                new = F.to_text(
                    sh.value(i, j) if cl.kind == 'formula' else cl.v
                ).replace(kw, rep)
            sh.set_raw(i, j, new)
    return {'hits': len(hits), 'list': hits[:200]}



def a_autofit(book, st, sh, g, rect):
    """按内容自动列宽"""
    r1, c1, r2, c2 = rect
    for j in range(c1, c2 + 1):
        w = 6
        for i in range(r1, r2 + 1):
            cl = sh.get(i, j)
            if not cl:
                continue
            t = cl.display() if cl.kind != 'formula' else \
                ST.format_value(sh.value(i, j), cl.fmt)
            ln = sum(2 if ord(ch) > 127 else 1 for ch in str(t))
            w = max(w, min(ln + 2, 60))
        sh.col_width[j] = w
    return {}
