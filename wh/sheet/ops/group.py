# -*- coding: utf-8 -*-
"""表格模块 · 行分组与折叠（电子表格侧）

参照主流开源表格（AG Grid、Handsontable、RevoGrid、Univer）的通用能力补齐：

- **行分组**：按某一列的值把行归拢到一起，每组前插一行组标题
- **折叠 / 展开**：把一组里的明细行收起来，只留组标题行

共同点：**只动数据本身，不引入任何业务含义**。
表格模块不该知道「物料」「供应商」是什么，它只认识列和值。

折叠状态存在 sheet.hidden_rows 里，跟合并、筛选一样随表一起保存。
"""

from ..kernel import funcs as F


def _gkey(v):
    """分组用的比较键：数字按数字比，其余按小写文本比。"""
    n = F.to_num(v)
    if n is not None:
        return (0, n, '')
    return (1, 0.0, F.to_text(v).strip().lower())


def _row_of(sh, i):
    return [sh.raw(i, j) for j in range(sh.cols)]


def _write_rows(sh, r1, rows):
    for k, row in enumerate(rows):
        for j, v in enumerate(row):
            sh.set_raw(r1 + k, j, v)


def group_by(sh, col, r1, r2, header=0, insert=True):
    """按 col 列的值分组并重排，返回 (分组摘要 list, 是否改动)。

    header=1 时第一行为表头，不参与分组。
    insert=True 会在每组前插一行组标题，写「列名：值（N 行）」，
    这样折叠后也知道这组是什么。
    """
    start = r1 + (1 if header else 0)
    if start > r2:
        return [], False
    buckets, idx = [], {}
    for i in range(start, r2 + 1):
        row = _row_of(sh, i)
        v = row[col] if col < len(row) else ''
        k = _gkey(v)
        if k not in idx:
            idx[k] = len(buckets)
            buckets.append({'key': k, 'val': v, 'rows': []})
        buckets[idx[k]]['rows'].append(row)
    buckets.sort(key=lambda b: b['key'])

    out = []
    if header:
        out.append(_row_of(sh, r1))
    for b in buckets:
        if insert:
            t = [''] * sh.cols
            cname = F.to_text(sh.raw(r1, col)).strip() if header else '分组'
            t[col] = '%s：%s（%d 行）' % (cname, F.to_text(b['val']).strip(),
                                         len(b['rows']))
            out.append(t)
        out.extend(b['rows'])
    _write_rows(sh, r1, out)
    # 清掉尾部可能残留的旧内容
    old_n = r2 - r1 + 1
    for i in range(r1 + len(out), r1 + max(old_n, len(out))):
        for j in range(sh.cols):
            sh.set_raw(i, j, '')
    return [{'val': F.to_text(b['val']), 'n': len(b['rows'])} for b in buckets], True


def build_groups(sh, col, r1, r2, header=0):
    """扫描当前数据算出每组的起止行（不移动数据），用于折叠。"""
    start = r1 + (1 if header else 0)
    gs, cur = [], None
    for i in range(start, r2 + 1):
        v = F.to_text(sh.value(i, col)).strip()
        if cur is None or v != cur['val']:
            if cur:
                cur['r2'] = i - 1
            cur = {'val': v, 'r1': i, 'r2': i, 'col': col}
            gs.append(cur)
        else:
            cur['r2'] = i
    return gs


def collapse(sh, groups, hide=True):
    """折叠（hide=True）或展开：把组内明细藏起来 / 放出来。"""
    hid = set(getattr(sh, 'hidden_rows', None) or set())
    for g in groups:
        for i in range(g['r1'] + 1, g['r2'] + 1):
            (hid.add(i) if hide else hid.discard(i))
    sh.hidden_rows = hid
    return len(hid)
