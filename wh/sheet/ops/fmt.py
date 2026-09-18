# -*- coding: utf-8 -*-
"""进阶动作·格式：按类型清除（内容/格式/批注/全部）、行高列宽、细致边框"""
from ..kernel import funcs as F
from ..kernel import style as ST
from .undo import push as _push, snap as _snap, _undo_hooks

def _push_snap(st, label, sh, r1, c1, r2, c2):
    _push(st, label, sh.name, _snap(sh, r1, c1, r2, c2))


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
    """撤销钩子现在就在本层（undo.py），不再反向依赖上层路由。

    这里保留函数的形式，是为了让调用方写法不必改动。
    """
    from .undo import push as _p, snap as _s
    return _p, _s


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
