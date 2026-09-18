# -*- coding: utf-8 -*-
"""电子表格接口 · 格式类动作

    样式 / 合并 / 分组 / 条件格式 / 批注
"""
from .common import *                                  # noqa: F401,F403
from .common import (_push, _snap)   # 星号不导入下划线名，必须显式写
from .common import GR, ST

def a_style(book, st, sh, g, rect):
    r1, c1, r2, c2 = rect
    _push(st, '格式', sh.name, _snap(sh, r1, c1, r2, c2))
    patch = g.get('style') or {}
    clear = g.get('clear') == '1'
    for i in range(r1, r2 + 1):
        for j in range(c1, c2 + 1):
            cl = sh.cell(i, j, create=True)
            if cl is None:
                continue
            if clear:
                cl.style = None
                if 'fmt' in patch:
                    cl.fmt = ''
                continue
            if 'fmt' in patch:
                cl.fmt = patch['fmt'] or ''
            # 只要 patch 里带了任意样式字段就合并（下划线/字体/缩进/细致边框等）
            if any(k in patch for k in ST.Style.__slots__ if k != 'fmt'):
                base = cl.style or ST.Style()
                cl.style = base.merge(ST.Style.from_dict(patch))
    return {}



def a_group(book, st, sh, g, rect):
    """按列分组 / 折叠展开（开源表格的通用能力）。"""
    r1, c1, r2, c2 = rect
    col = int(g.get('col') if g.get('col') is not None else c1)
    op = g.get('op') or 'group'
    hd = int(g.get('header') or 0)
    if op == 'collapse' or op == 'expand':
        gs = GR.build_groups(sh, col, r1, r2, hd)
        n = GR.collapse(sh, gs, hide=(op == 'collapse'))
        return {'n': len(gs), 'hidden': n}
    _push(st, '分组', sh.name, _snap(sh, r1, 0, r2, sh.cols - 1))
    gs, ok = GR.group_by(sh, col, r1, r2, hd, insert=(op != 'sortonly'))
    return {'groups': gs, 'ok': ok}

def a_merge(book, st, sh, g, rect):
    r1, c1, r2, c2 = rect
    op = g.get('op') or 'merge'
    _push(st, '合并', sh.name, _snap(sh, r1, c1, r2, c2))
    sh.merges = [m for m in sh.merges
                 if not (m[0] <= r2 and m[2] >= r1 and m[1] <= c2 and m[3] >= c1)]
    if op == 'merge':
        sh.merges.append((r1, c1, r2, c2))
    return {}



def a_note(book, st, sh, g, rect):
    r1, c1, r2, c2 = rect
    cl = sh.cell(r1, c1, create=True)
    if cl is None:
        return {}
    _push(st, '批注', sh.name, _snap(sh, r1, c1, r1, c1))
    cl.note = g.get('note') or ''
    return {}

def a_cond(book, st, sh, g, rect):
    """条件格式：add / del / list"""
    op = g.get('op') or 'add'
    if op == 'del':
        sh.cond = []
        return {}
    rule = ST.CondRule.from_dict(g.get('rule') or {})
    rule.range = list(rect)
    sh.cond = [r for r in sh.cond if r.to_dict().get('ctype') != rule.ctype
               or r.op != rule.op]
    sh.cond.append(rule)
    return {'cond': [x.to_dict() for x in sh.cond]}
