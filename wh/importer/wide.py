# -*- coding: utf-8 -*-
"""原 Excel「宽表」布局：物料一行 × 每日(进/出)两列。

依赖 base 的读取原语；被 txn.parse_txn_file 在识别不出表头时回退调用。
"""
import datetime, re

from .base import _norm, _num, _fmt_date, unit_from_label, QTY_MAX

# ---------- 原 Excel「宽表」布局：物料一行 × 每日(进/出)两列 ----------
WIDE_KINDS = {'进', '出'}
MAT_HEADS = {
    'supplier': ['供应商', '厂商', '供货商'],
    'category': ['类型', '类别', '分类', '大类'],
    'spec': ['规格', '规格(米)', '规格（米）', '厚度'],
    'width': ['宽幅', '宽度', '幅宽'],
    'name': ['物料名称', '名称', '品名', '材料名称', '物料'],
    'code': ['料号', '物料编号', '物料编码', '编号', '编码', '型号'],
    'unit': ['单位', '单位(卷)', '单位（卷）', '计量单位'],
    'opening': ['上月结存', '期初结存', '期初', '上期结存'],
}

def _is_dt(v):
    return hasattr(v, 'strftime') or isinstance(v, datetime.datetime)

def _wide_kind_row(grid):
    """找形如 [进,出,进,出...] 的那一行（原表第2行）"""
    best, bestn = None, 0
    for r in range(min(8, len(grid))):
        n = sum(1 for v in grid[r] if str(v or '').strip() in WIDE_KINDS)
        if n > bestn:
            best, bestn = r, n
    return best if bestn >= 4 else None

def _head_at(grid, rows, c):
    """取第 c 列在若干表头行里的文字（优先非空的那行）"""
    for hr in rows:
        if hr is not None and hr < len(grid) and c < len(grid[hr]):
            v = str(grid[hr][c] or '').strip()
            if v:
                return v
    return ''

# 「汇总/合计」这类列长在日期区后面，表头不是日期、进出标记却同样是「进/出」。
# 当作日期列读进来会把当月的合计值再导一遍 —— 库存直接翻倍。必须整段跳过。
SUM_WORDS = ('汇总', '合计', '总计', '小计', '累计', '合計')

def _nm_sum_word(nm):
    """物料名是不是「总计/合计」这类汇总行（去掉括号内容后整名比对）"""
    import re as _r
    t = _r.sub(r'[（(].*?[)）]', '', str(nm or '')).strip()
    return t in SUM_WORDS or str(nm or '').strip() in SUM_WORDS

def _is_sum_col(v):
    s = str(v or '').strip()
    return bool(s) and any(w in s for w in SUM_WORDS)

def _wide_dates(grid, drow, defmonth):
    """从日期行取每列日期；合并单元格造成的 None 沿用前一个日期。

    遇到「汇总/合计」列立刻中断日期延续（last=None），
    否则它右边的列会继续沿用汇总列之前那个日期，同样被误当成单据列。
    """
    row = grid[drow] if (drow is not None and drow < len(grid)) else []
    y, mo = (int(defmonth[:4]), int(defmonth[5:7])) if defmonth and len(defmonth) >= 7 \
        else (datetime.date.today().year, datetime.date.today().month)
    out, last = {}, None
    for c, v in enumerate(row):
        if _is_sum_col(v):
            last = None
            continue
        d = None
        if _is_dt(v):
            d = v.strftime('%Y-%m-%d')
        elif isinstance(v, (int, float)) and 1 <= float(v) <= 31:
            d = '%04d-%02d-%02d' % (y, mo, int(v))
        else:
            s = str(v or '').strip()
            if s and (('月' in s) or ('-' in s) or ('/' in s)):
                d = _fmt_date(v) or None
        if d:
            last = d
        if last:
            out[c] = last
    return out

def _strip_paren(n):
    """去掉表头末尾的括号单位：「宽幅(米)」->「宽幅」、「单位(卷)」->「单位」。

    真实表格里大量写成「规格（米）」「宽幅（米）」「单位（卷）」，
    只按全名精确匹配会整列丢失（宽幅直接变空）。
    """
    import re as _re
    return _re.sub(r'[（(][^）)]*[)）]\s*$', '', n).strip()

def _col_by_head(grid, hrows, names):
    """在若干候选表头行里按名字找列号。

    先精确匹配，再退一步去掉括号后缀匹配（「宽幅(米)」按「宽幅」命中）。
    """
    exact = [_norm(x) for x in names]
    loose = [_strip_paren(x) for x in exact]
    for hr in hrows:
        if hr is None or hr >= len(grid):
            continue
        # 先跑一遍精确，保证「规格」不会被「规格(米)」抢走
        for c, v in enumerate(grid[hr]):
            n = _norm(v)
            if n and n in exact:
                return c
    for hr in hrows:
        if hr is None or hr >= len(grid):
            continue
        for c, v in enumerate(grid[hr]):
            n = _strip_paren(_norm(v))
            if n and n in loose:
                return c
    return None

def _combo_date_kind(v, y, mo):
    """识别「日期+进出」写在同一个单元格里的表头。

    真实表格里这种写法很常见：09-01进 / 09-01出 / 9/1 进 / 2026-09-01进。
    以前只认"一行日期 + 一行进出"的双层表头，遇到这种单层写法
    parse_wide 直接返回空，用户又没法手动指定列（宽表列太多，
    手动映射只适合一行一笔的表），数据就彻底导不进来了。
    """
    s = str(v or '').strip()
    if not s:
        return None
    kind = None
    for k in WIDE_KINDS:
        if s.endswith(k):
            kind = k
            s = s[:-1].strip()
            break
    if kind is None:
        return None
    s = s.rstrip('/-—– 　')
    if not s:
        return None
    d = _fmt_date(s)                       # 完整日期：2026-09-01 / 2026/9/1
    if d:
        return (d, kind)
    m = re.match(r'^(\d{1,2})\s*[\-/月]\s*(\d{1,2})\s*日?$', s)   # 09-01 / 9-1 / 9月1
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        mm, dd = (a, b) if a <= 12 else (b, a)
        if 1 <= mm <= 12 and 1 <= dd <= 31:
            try:
                return ('%04d-%02d-%02d' % (y, mo, dd) if mm == mo
                        else '%04d-%02d-%02d' % (y, mm, dd), kind)
            except ValueError:
                return None
    return None


def _combo_cols(grid, defmonth=None):
    """扫描表头区，找出"日期+进出"合并写法的列。

    返回 (dates, kind_map, 最后一条表头行号)；找不到返回 None。
    """
    y, mo = (int(defmonth[:4]), int(defmonth[5:7])) if defmonth and len(defmonth) >= 7 \
        else (datetime.date.today().year, datetime.date.today().month)
    dates, kinds, last = {}, {}, None
    for r in range(min(6, len(grid))):
        for c, v in enumerate(grid[r] if r < len(grid) else []):
            if c in dates:
                continue
            got = _combo_date_kind(v, y, mo)
            if got:
                dates[c], kinds[c] = got
                last = r if last is None else max(last, r)
    if len(dates) >= 4 and kinds:          # 至少 4 个日期列才认定是宽表
        return dates, kinds, last
    return None


def parse_wide(grid, defmonth=None):
    """解析原表那种『物料 × 每日进/出』宽表。返回 (单据列表, 字段集合)

    支持两种表头写法：
      A 双层：一行写日期、下一行写 进/出（原表就是这种）
      B 单层：日期和进出写在一个格子里，如「09-01进」「09-01出」
    """
    kr = _wide_kind_row(grid)
    combo = None
    if kr is None:
        combo = _combo_cols(grid, defmonth)     # 试试单层合并写法
        if combo is None:
            return [], set()
        dates, kind_map_pre, last = combo
        start = (last or 0) + 1
        heads = [r for r in range((last or 0) + 1)]
        kr = None
    else:
        drow = kr - 1 if kr > 0 else None
        dates = _wide_dates(grid, drow, defmonth)
        if not dates:
            return [], set()
        # 数据起点：进出行之后，且该行有物料名称
        start = kr + 1
        heads = [hr for hr in (drow, kr) if hr is not None]
        kind_map_pre = None
    name_c = _col_by_head(grid, heads, MAT_HEADS['name'])
    if name_c is None:                      # 表头对不上：挑"文字最多"的那一列当物料名
        best, bestn = None, 0
        for c in range(min(12, max(len(r) for r in grid[:20]))):
            n = 0
            for r in grid[start:start + 15]:
                if r and c < len(r):
                    v = str(r[c] or '').strip()
                    if v and not _num(v) and len(v) >= 2:
                        n += 1
            if n > bestn:
                best, bestn = c, n
        name_c = best
    if name_c is None:
        return [], set()
    cols = {k: _col_by_head(grid, heads, v) for k, v in MAT_HEADS.items()}
    cols['name'] = name_c
    # 日期列 -> (列号, 进/出)
    if kind_map_pre is not None:
        kind_map = dict(kind_map_pre)        # 单层写法：方向直接来自表头单元格
    else:
        kind_map = {}
        for c, v in enumerate(grid[kr] if (kr is not None and kr < len(grid)) else []):
            s = str(v or '').strip()
            if s in WIDE_KINDS and c in dates:
                kind_map[c] = s
    out = []
    for r in grid[start:]:
        if not r or name_c >= len(r):
            continue
        nm = str(r[name_c] or '').strip()
        if not nm or nm in ('/', '-'):
            continue
        # 表末尾常有「总计/合计/小计」这种汇总行。它是上面各行的和，
        # 当成普通行导进来会凭空多出一笔，库存和金额全部虚增。
        # 用 SUM_WORDS 统一判断（去掉括号内容后比对，如「总计（卷）」）。
        if _nm_sum_word(nm):
            continue
        base = {'name': nm, 'code': '', 'date': '', 'note': '', 'batch': '',
                'pieces': None, 'per_piece': None, 'price': None}
        for f in ('supplier', 'category', 'spec', 'width', 'unit', 'code'):
            c = cols.get(f)
            base[f] = '' if (c is None or c >= len(r)) else str(r[c] or '').strip()
            if base[f] in ('/', '-'):
                base[f] = ''
        # 「单位（卷）」列里填的是卷数，不是单位名；把真实单位从表头括号里取出来
        uc = cols.get('unit')
        if uc is not None:
            fixed = unit_from_label(_head_at(grid, heads, uc),
                                    [r[uc] if uc < len(r) else None for r in grid[start:start + 30]])
            if fixed:
                base['unit'] = fixed
        oc = cols.get('opening')
        base['opening'] = _num(r[oc], hi=QTY_MAX) if (oc is not None and oc < len(r)) else 0.0
        base['safety'] = 0.0
        got = False
        for c, kind in sorted(kind_map.items()):
            if c >= len(r):
                continue
            q = _num(r[c], hi=QTY_MAX)
            if q > 0:
                d = dict(base); d['kind'] = kind; d['qty'] = q
                d['date'] = dates[c]
                out.append(d); got = True
        if not got:                          # 该行没有进出，但档案信息仍要保留（期初）
            d = dict(base); d['kind'] = None; d['qty'] = 0.0
            out.append(d)
        # 数量取整到合理精度：原表里 9.999999999999998 这种浮点尾巴
        # 会一路带进数据库，显示成"9.999999999999998 卷"
    for d in out:
        if d.get('qty'):
            d['qty'] = round(float(d['qty']), 6)
    return out, set(list(cols.keys()) + ['date', 'qty', 'kind'])

