# -*- coding: utf-8 -*-
"""进阶动作·数据质量：数据有效性（下拉/整数/日期/文本长度）、删除重复项"""
from ..kernel import funcs as F
from .undo import push as _push, snap as _snap, _undo_hooks

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
