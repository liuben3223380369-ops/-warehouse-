# -*- coding: utf-8 -*-
"""导入通用层：文件格式读取 + 原语 + 物料档案表头映射。

只负责「把表读成网格、把表头认成字段」，不含流水/宽表等业务解析。
"""
import io, csv, datetime, re

try:
    import openpyxl
except ImportError:
    openpyxl = None

ALIASES = {
    'name':     ['物料名称', '名称', '品名', '品名规格', '物料', '材料名称', 'name'],
    'code':     ['料号', '物料编号', '物料编码', '编号', '编码', '型号', '规格型号', 'code'],
    'supplier': ['供应商', '厂商', '供货商', 'supplier'],
    'category': ['类型', '类别', '分类', '大类', 'category'],
    'spec':     ['规格', '规格（米）', '规格(米)', '厚度', 'spec'],
    'width':    ['宽幅', '宽度', '幅宽', 'width'],
    'unit':     ['单位', '单位（卷）', '单位(卷)', '计量单位', 'unit'],
    'opening':  ['期初结存', '期初', '上月结存', '上期结存', '库存', '当前库存', 'opening'],
    'safety':   ['安全库存', '预警值', '库存预警', '最低库存', 'safety'],
    'status':   ['状态', '使用状态', 'status'],
    # 平米 / 卷料：使用者表里往往自带这两列（原 Excel 就有「平米（㎡）」），
    # 少了它们导入时认不出，表里的数会被丢掉，只能靠长宽重新算一遍。
    'sqm':      ['平米', '平方米', '面积', '平方', '平米数', 'm2', 'm²', 'sqm'],
    'rolls':    ['卷料', '卷', '卷数', '米数', 'rolls'],
}
NUM = {'opening', 'safety', 'sqm', 'rolls'}

# 单笔数量上限：挡住表里多输几个零（1e15）把库存冲爆的情况。
# 真有超大批量请拆单，或换个更大的单位（比如用吨而不是克）。
QTY_MAX = 1e9

def _norm(s):
    return str(s or '').strip().lower().replace(' ', '').replace('（', '(').replace('）', ')')

def _is_num_col(vals):
    """整列都是数字（且至少有一个非空）-> 这一列不是单位列。"""
    n = 0
    for v in vals:
        if v is None or str(v).strip() == '':
            continue
        try:
            float(str(v)); n += 1
        except (TypeError, ValueError):
            return False
    return n > 0


def unit_from_label(label, sample_vals):
    """「单位（卷）」这类表头，列里填的其实是卷数（50、10），不是单位名。

    直接把它当单位会把 unit 存成 '50'、'9.999999999999998' 这种数字，
    库存显示就变成"50 50"这种鬼东西。这里判断：整列都是数字 -> 不是单位列，
    真实单位从表头括号里取，「单位（卷）」的"卷"才是单位。
    """
    import re as _re
    has_num = False
    for v in sample_vals:
        if v is None or str(v).strip() == '':
            continue
        try:
            float(str(v)); has_num = True; break
        except (TypeError, ValueError):
            return None                      # 有文本 -> 确实就是单位列，原样返回
    if not has_num:
        return None
    m = _re.search(r'[（(]([^）)]+)[)）]', str(label or ''))
    if m:
        u = m.group(1).strip()
        # 括号里可能是"卷""平米""KG"等单位名，不该是"米""㎡"这种量纲
        if u and len(u) <= 6 and not _re.match(r'^[\d.]+$', u):
            return u
    return None

def map_headers(header, aliases=None):
    """表头行 -> {列索引: 规范字段}。aliases 来自列映射配置，优先级高于内置别名"""
    names = {f: list(v) for f, v in ALIASES.items()}
    for f, v in (aliases or {}).items():
        if f in names:
            names[f] = list(v) + [x for x in ALIASES.get(f, []) if x not in v]
    out = {}
    for i, h in enumerate(header):
        n = _norm(h)
        if not n:
            continue
        for f, ns in names.items():
            if f in out.values():
                continue
            if n in [_norm(x) for x in ns]:
                out[i] = f
                break
    return out

def _pick_sheet(cand, aliases=None):
    """从多张表候选里挑出真正有数据的那张。

    两轮：先找表头能被识别的（最可靠），
    都不认识就退回第一张非空表（至少让用户看到诊断网格去手选列）。
    """
    for rows in cand:
        for i in range(min(5, len(rows))):
            try:
                if map_headers(rows[i], aliases):
                    return rows
            except Exception:
                pass
    return cand[0]


def parse_file(path=None, stream=None, filename='', aliases=None):
    """返回 (行列表[dict], 识别到的字段集合)"""
    if path and path.lower().endswith('.csv') or (filename or '').lower().endswith('.csv'):
        data = open(path, 'rb').read() if path else stream.read()
        for enc in ('utf-8-sig', 'gbk', 'utf-8'):
            try:
                text = data.decode(enc); break
            except UnicodeDecodeError:
                continue
        else:
            text = data.decode('utf-8', 'replace')
        rows = list(csv.reader(io.StringIO(text)))
    else:
        if openpyxl is None:
            raise RuntimeError('未安装 openpyxl，请先执行: pip install openpyxl')
        wb = openpyxl.load_workbook(path or io.BytesIO(stream.read()), data_only=True)
        # 选表：很多人第一张表是封面/说明，真正的数据在后面。
        # 直接取 sheetnames[0] 会停在封面上，表头认不出、导入 0 条，
        # 用户只会看到"没认出表头"，完全想不到是选错了表。
        # 策略：先找表头能认出来的那张；都不认识才退回第一张非空表。
        _cand = []
        for _nm in wb.sheetnames:
            _rows = [[c for c in r] for r in wb[_nm].iter_rows(values_only=True)]
            _rows = [r for r in _rows if any(_norm(c) for c in r)]
            if _rows:
                _cand.append(_rows)
        rows = _pick_sheet(_cand, aliases) if _cand else []

    rows = [r for r in rows if any(_norm(c) for c in r)]
    if not rows:
        return [], set()

    hi, hmap = 0, {}
    for i in range(min(5, len(rows))):
        m = map_headers(rows[i], aliases)
        if 'name' in m.values() and len(m) > len(hmap):
            hi, hmap = i, m
    if 'name' not in hmap.values():          # 无表头：A=名称 B=料号 C=类型 D=单位 E=期初
        hmap = {0: 'name', 1: 'code', 2: 'category', 3: 'unit', 4: 'opening'}
        # 首行若含任何已知字段名，仍当作表头跳过，避免把表头当数据
        known = set()
        for v in (aliases or {}).values():
            known |= {_norm(x) for x in v}
        for v in ALIASES.values():
            known |= {_norm(x) for x in v}
        hi = 0 if any(_norm(c) in known for c in rows[0]) else -1

    out = []
    for r in rows[hi + 1:]:
        d = {f: '' for f in ALIASES}
        for i, f in hmap.items():
            if i < len(r):
                v = r[i]
                d[f] = '' if v is None else _as_text(v)
        d['name'] = d['name'] or d['code']
        if not d['name']:
            continue
        for f in NUM:
            d[f] = _num(d[f], hi=QTY_MAX)
        out.append(d)
    return out, set(hmap.values())

def _as_text(v):
    """单元格值转文本。

    坑：宽幅常见值是 0.035/0.045/0.05 这类真小数，以前用
    str(v).split('.')[0] 想去掉 250.0 的尾巴，结果把 0.035 也砍成了 "0"
    —— 宽幅信息全丢，连带"名称+规格+宽幅"的物料指纹失效，
    不同宽幅的物料被合并成一条，库存张冠李戴。
    正确做法：只有整数值（250.0）才去掉小数部分，真小数原样保留。
    """
    if v is None:
        return ''
    if isinstance(v, float):
        if v == int(v) and abs(v) < 1e15:      # 250.0 -> "250"
            return str(int(v))
        # 去掉浮点噪音：0.035000000000000003 -> 0.035
        return ('%g' % v)
    return str(v).strip()


def _num(v, lo=None, hi=None):
    """转数字。lo/hi 为边界，超出则截断；非法值一律 0。

    数量上限很必要：表里填错多几个零（1e15）会把库存冲到天文数字，
    后面所有报表、预警全都失真，而且很难一眼看出是哪笔错了。
    """
    try:
        f = float(str(v).strip().replace(',', ''))
    except (TypeError, ValueError):
        return 0.0
    import math
    if math.isnan(f) or math.isinf(f):
        return 0.0
    if lo is not None and f < lo:
        f = lo
    if hi is not None and f > hi:
        f = hi
    return f

def _cell(sh, r, c):
    v = sh.cell_value(r, c)
    if sh.cell_type(r, c) == 3:            # XL_CELL_DATE
        import xlrd, datetime as _dt
        try:
            t = xlrd.xldate_as_tuple(v, sh.book.datemode)
            return _dt.datetime(*t).strftime('%Y-%m-%d')
        except Exception:
            return str(v)
    return v

def _fmt_date(v):
    """把 Excel 日期/字符串统一成 YYYY-MM-DD"""
    if v is None or str(v).strip() == '':
        return ''
    if isinstance(v, (int, float)):        # Excel 序列号
        from datetime import date as _d, timedelta
        try:
            return (_d(1899, 12, 30) + timedelta(days=float(v))).strftime('%Y-%m-%d')
        except Exception:
            return str(v)
    s = str(v).strip()
    for attr in ('date', 'datetime'):
        pass
    if hasattr(v, 'strftime'):
        return v.strftime('%Y-%m-%d')
    # 只做"整体像日期"的替换：先确认格式，再动手，避免把文本里的"日"字吃掉
    import re as _re
    m = _re.match(r'^\s*(\d{4})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})\s*日?\s*$', s)
    if m:
        y, mo, d = m.group(1), m.group(2).zfill(2), m.group(3).zfill(2)
        try:
            datetime.date(int(y), int(mo), int(d))   # 顺带校验合法性（排除 2026-13-45）
        except ValueError:
            return ''
        return f'{y}-{mo}-{d}'
    m2 = _re.match(r'^\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})\s*日?\s*$', s)   # 只有月日
    if m2:
        return ''
    # 认不出来就返回空，由上层用默认日期，绝不把垃圾塞进数据库
    return ''
