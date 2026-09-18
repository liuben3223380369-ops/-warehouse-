# -*- coding: utf-8 -*-
"""进阶动作·编辑：剪贴板（复制剪切粘贴，公式相对引用跟随）、自动求和、隐藏行列"""
from ..kernel import funcs as F
from ..kernel import style as ST
from ..kernel import col_letter
from ..kernel import shift_formula
from .undo import push as _push, snap as _snap

def _push_snap(st, label, sh, r1, c1, r2, c2):
    _push(st, label, sh.name, _snap(sh, r1, c1, r2, c2))


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
