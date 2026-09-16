# -*- coding: utf-8 -*-
"""表格模块 · 数据操作层

纯函数，不碰数据库也不碰 Web：
    sort_rows / filter_rows / paginate / window（虚拟滚动窗口）
    aggregate（求和、计数、极值）
    dedupe（按列名指纹去重）

业务模块（库存 / 出入库 / 采购 / 统计）要做表格相关的活，统一调这里，
不要各写一套排序和分页。
"""

# 排序三态：默认 → 升序 → 降序
SORT_NEXT = {'': 'asc', 'asc': 'desc', 'desc': ''}


def next_dir(cur):
    return SORT_NEXT.get(cur or '', '')


def _key(v):
    """排序键：数字按数字排，其余按字符串排（中文用 locale 不靠谱，直接比）"""
    if v is None or v == '':
        return (0, 0.0, '')     # 空值永远排最后
    try:
        return (1, float(v), '')
    except Exception:
        try:
            return (1, float(str(v).replace(',', '')), '')
        except Exception:
            return (1, 0.0, str(v))


def sort_rows(rows, key, dir_='asc', getter=None):
    """按某列排序。getter 用于取不到 dict 键的场景（比如 sqlite Row）"""
    if not key:
        return list(rows or [])

    def g(r):
        if getter:
            return getter(r, key)
        try:
            return r.get(key)
        except Exception:
            return getattr(r, key, None)

    out = list(rows or [])
    try:
        out.sort(key=lambda r: _key(g(r)), reverse=(dir_ == 'desc'))
    except Exception:
        pass
    return out


def filter_rows(rows, kw='', fields=None, getter=None):
    """模糊搜索：在给定字段里找关键词，不分大小写"""
    kw = (kw or '').strip().lower()
    if not kw:
        return list(rows or [])
    fields = list(fields or [])

    def g(r, k):
        if getter:
            return getter(r, k)
        try:
            return r.get(k)
        except Exception:
            return getattr(r, k, None)

    out = []
    for r in (rows or []):
        if not fields:
            hay = ' '.join(str(v or '') for v in
                           (r.values() if isinstance(r, dict) else [r]))
        else:
            hay = ' '.join(str(g(r, k) or '') for k in fields)
        if kw in hay.lower():
            out.append(r)
    return out


def paginate(rows, page=1, size=50):
    """分页。返回 (当页数据, 总页数, 总条数)"""
    rows = list(rows or [])
    n = len(rows)
    size = max(1, int(size or 50))
    pages = max(1, (n + size - 1) // size)
    page = min(max(1, int(page or 1)), pages)
    return rows[(page - 1) * size: page * size], pages, n


def window(rows, offset=0, limit=80):
    """虚拟滚动：只取出可视区那一段，避免几万行一次渲染卡死"""
    rows = list(rows or [])
    offset = max(0, int(offset or 0))
    return rows[offset: offset + max(1, int(limit or 80))], len(rows)


def aggregate(rows, field, op='sum', getter=None):
    """对某一列做聚合：sum / count / avg / max / min"""
    vals = []
    for r in (rows or []):
        if getter:
            v = getter(r, field)
        else:
            try:
                v = r.get(field)
            except Exception:
                v = getattr(r, field, None)
        try:
            vals.append(float(v))
        except Exception:
            continue
    if not vals:
        return 0.0
    op = (op or 'sum').lower()
    if op == 'count':
        return float(len(vals))
    if op == 'avg':
        return sum(vals) / len(vals)
    if op == 'max':
        return max(vals)
    if op == 'min':
        return min(vals)
    return sum(vals)


def group_by(rows, field, getter=None):
    """按某列分组：{值: [行...]}"""
    out = {}
    for r in (rows or []):
        if getter:
            v = getter(r, field)
        else:
            try:
                v = r.get(field)
            except Exception:
                v = getattr(r, field, None)
        out.setdefault(v if v not in (None, '') else '(空)', []).append(r)
    return out


def dedupe(rows, keys):
    """按若干列去重（保留第一次出现的）"""
    seen, out = set(), []
    for r in (rows or []):
        try:
            sig = tuple(str(r.get(k) or '') for k in keys)
        except Exception:
            sig = tuple(str(getattr(r, k, '') or '') for k in keys)
        if sig in seen:
            continue
        seen.add(sig)
        out.append(r)
    return out


def add_total_row(rows, headers, num_fields, label='合计'):
    """追加一行合计（只合计数值列）"""
    if not rows:
        return rows
    total = {f: aggregate(rows, f, 'sum') for f in (num_fields or [])}
    row = {}
    first = True
    for h in (headers or []):
        if h in total:
            row[h] = total[h]
        elif first:
            row[h] = label
            first = False
        else:
            row[h] = ''
    return list(rows) + [row]
