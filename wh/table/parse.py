# -*- coding: utf-8 -*-
"""表格模块 · 解析层

负责把"别人的表格"读进来，并尽量看懂它：

    read_table()      读 xlsx / xlsm / xls / et / csv → 二维列表
    detect_header()   找到表头在第几行（支持多层表头、带标题行的报表）
    split_unit()      「长（米）」→ (长, 米)
    norm_head()       列名规整化（去空格/括号/全角，用于比对）
    fingerprint()     列名指纹：只要列名集合相同就算同一类表
    match_columns()   表头 → 系统字段的映射
    find_total_rows() 找出表尾的「合计/总计/小计」行（不能当数据导入）

设计原则：宁可少认，不可乱认。认不出来就交给用户手动指定列。
"""
import os
import re as _re

from .. import importer
from ..core import db


# ------------------------------------------------------------------ 读文件
def read_table(path, ext=None, sheet=None):
    """读成一个二维列表（不解析、不映射，只负责把格子搬进来）。

    失败返回 ([], 错误信息)。表格模块自带读取能力，
    不依赖业务侧的 importer —— 那是导入物料档案用的。
    """
    if not path or not os.path.exists(path):
        return [], '文件不存在'
    ext = (ext or os.path.splitext(path)[1] or '').lower()
    rows = []
    try:
        if ext in ('.csv', '.txt'):
            import csv as _csv
            # 先试 UTF-8（带 BOM 也认），不行再退 GBK
            for enc in ('utf-8-sig', 'gbk', 'utf-8'):
                try:
                    with open(path, 'r', encoding=enc,
                              errors='replace', newline='') as f:
                        rows = [list(r) for r in _csv.reader(f)]
                    break
                except UnicodeDecodeError:
                    continue
        else:
            from openpyxl import load_workbook
            wb = load_workbook(path, data_only=True)
            ws = wb[sheet] if (sheet and sheet in wb.sheetnames) else wb.worksheets[0]
            for r in ws.iter_rows(values_only=True):
                rows.append(list(r))
    except Exception as e:
        return [], '读不了这个文件：%s' % e
    rows = [[('' if c is None else c) for c in (r or [])] for r in (rows or [])]
    # 去掉尾部空行（Excel 常有虚高空行）
    while rows and not any(str(c or '').strip() for c in rows[-1]):
        rows.pop()
    return rows, ''


# ------------------------------------------------------------------ 表头识别
# 表头里出现这些词，基本可以断定"这一行是列名而不是数据"
HEAD_WORDS = ('日期', '物料', '名称', '品名', '规格', '数量', '进', '出', '备注',
              '供应商', '单位', '单价', '金额', '类型', '批号', '批次', '单号',
              'date', 'name', 'qty', 'price', 'amount', 'item', 'spec')


def _row_score(row):
    """这一行有多像表头"""
    if not row:
        return 0
    hit = 0
    non_empty = 0
    for c in row:
        s = str(c or '').strip()
        if not s:
            continue
        non_empty += 1
        low = s.lower()
        if any(w in low for w in HEAD_WORDS):
            hit += 1
    if not non_empty:
        return 0
    return hit * 1.0 / non_empty


def detect_header(rows, scan=6):
    """在头几行里找表头行。返回行索引（找不到返回 0）。

    多层表头（比如第一行是「9月份收发存」、第二行才是列名）也能找对：
    取"最像表头"的那一行的**最后一层**。
    """
    if not rows:
        return 0
    best_i, best_s = 0, -1
    for i, r in enumerate(rows[:max(1, scan)]):
        s = _row_score(r)
        if s > best_s:
            best_i, best_s = i, s
    return best_i if best_s > 0 else 0


def merge_head_layers(rows, hi, max_layers=2):
    """多层表头合并：['规格',''] + ['','米'] → '规格米'？

    实际上更常见的是第一行大类、第二行子类。这里做保守处理：
    只有当上层格非空且下层格为空时才拼接，避免把「9月」拼进列名。
    """
    if hi <= 0 or hi >= len(rows):
        return list(rows[hi] if rows else [])
    top = rows[hi - 1]
    cur = list(rows[hi])
    out = []
    for i, c in enumerate(cur):
        s = str(c or '').strip()
        t = str(top[i] if i < len(top) else '' or '').strip()
        if s and t and t not in HEAD_WORDS and not _re.search(r'\d', t):
            # 上层是「规格」下层是「米」这种，拼成「规格（米）」交给单位解析
            out.append('%s（%s）' % (t, s))
        else:
            out.append(s)
    # 用合并后的列名更像表头才采用
    return out if _row_score(out) >= _row_score(cur) else cur


# ------------------------------------------------------------------ 列名规整
_PUNCT = _re.compile(r'[\s\u3000（）()\[\]【】:：/\\\-_.*#]+')


def norm_head(s):
    """列名规整化：去空格、括号内容、标点、统一大小写。用于比对。"""
    s = str(s or '').strip()
    s = _re.sub(r'（[^）]*）|\([^)]*\)', '', s)   # 去掉括号内容（单位）
    s = _PUNCT.sub('', s)
    return s.lower()


def split_unit(head):
    """「长（米）」→ ('长', '米')；没有括号 → ('长', '')"""
    s = str(head or '').strip()
    m = _re.search(r'（([^）]*)）|\(([^)]*)\)', s)
    if not m:
        return s, ''
    unit = (m.group(1) or m.group(2) or '').strip()
    name = (s[:m.start()] or s).strip()
    return name, unit


def fingerprint(headers):
    """列名指纹：只要列名集合一样就算同一类表，与顺序、括号写法无关。"""
    return db.head_sig([str(h or '') for h in (headers or [])])


# ------------------------------------------------------------------ 列映射
def match_columns(headers, aliases):
    """把表头映射成系统字段。

    :param headers: ['长（米）', '数量', ...]
    :param aliases: {系统字段: [别名...]}
    :return: (映射 {列索引: 系统字段}, 没认出来的表头列表)
    """
    al = dict(aliases or {})
    # 内置兜底：常见中文列名
    builtin = {
        'date': ['日期', '单据日期', '时间', 'date'],
        'name': ['物料名称', '名称', '品名', '物料', 'name'],
        'code': ['料号', '编号', '物料编码', 'code'],
        'supplier': ['供应商', '厂商', 'supplier'],
        'category': ['类型', '类别', 'category'],
        'spec': ['规格', '长', '长度', 'spec'],
        'width': ['宽幅', '宽', '宽度', 'width'],
        'unit': ['单位', 'unit'],
        'qty': ['数量', 'qty', 'quantity'],
        'in': ['进', '入库', '入库数量', 'in'],
        'out': ['出', '出库', '出库数量', 'out'],
        'note': ['备注', '摘要', 'note', 'remark'],
        'price': ['单价', 'price'],
        'amount': ['金额', 'amount'],
        'batch': ['批次', '批号', 'batch', 'lot'],
    }
    for k, v in builtin.items():
        al.setdefault(k, list(v))

    mapping, unknown = {}, []
    used = set()
    for idx, h in enumerate(headers or []):
        raw = str(h or '').strip()
        if not raw:
            continue
        nm, _u = split_unit(raw)
        cands = [raw, nm, norm_head(raw)]
        hit = None
        for f, names in al.items():
            if f in used:
                continue
            for n in names:
                if not n:
                    continue
                if (str(n).strip().lower() in [c.lower() for c in cands]
                        or norm_head(n) == norm_head(raw)):
                    hit = f
                    break
            if hit:
                break
        if hit:
            mapping[idx] = hit
            used.add(hit)
        else:
            unknown.append(raw)
    return mapping, unknown


# ------------------------------------------------------------------ 汇总行
TOTAL_WORDS = ('合计', '总计', '小计', '累计', '总和', 'total', 'sum', 'subtotal')


def is_total_row(row, name_idx=None):
    """这一行是不是表尾的汇总行？

    判定：名称列（或任一文本列）里出现「合计/总计」等字样。
    只按"文本里写了合计"来判，不按"等于上面各行之和"来判——
    后者容易误伤（比如某天刚好只有一个数）。
    """
    if not row:
        return False
    cells = list(row)
    if name_idx is not None and name_idx < len(cells):
        cells = [cells[name_idx]] + cells
    for c in cells:
        s = str(c or '').strip().lower()
        if not s:
            continue
        for w in TOTAL_WORDS:
            if w in s:
                return True
    return False


def find_total_rows(rows, header=None, name_idx=None):
    """返回所有汇总行的索引集合"""
    out = set()
    for i, r in enumerate(rows or []):
        if is_total_row(r, name_idx):
            out.add(i)
    return out


# ------------------------------------------------------------------ 预览
def preview(path, ext=None, scan=6):
    """导入预览：读文件 → 定位表头 → 识别列 → 标出汇总行。

    返回 dict：
        rows      原始数据（二维列表）
        hi        表头行索引
        headers   列名（可能多层合并）
        mapping   列映射
        unknown   没认出来的列名
        total_rows 汇总行索引集合
        sig       列名指纹
        err       错误信息
    """
    rows, err = read_table(path, ext)
    if err:
        return {'rows': [], 'hi': 0, 'headers': [], 'mapping': {},
                'unknown': [], 'total_rows': set(), 'sig': '', 'err': err}
    if not rows:
        return {'rows': [], 'hi': 0, 'headers': [], 'mapping': {},
                'unknown': [], 'total_rows': set(), 'sig': '',
                'err': '这个表是空的'}
    hi = detect_header(rows, scan)
    headers = merge_head_layers(rows, hi)
    if not any(str(h or '').strip() for h in headers):
        headers = [str(c or '').strip() for c in rows[hi]]
    mapping, unknown = match_columns(headers, db.aliases_map())
    name_idx = None
    for i, f in mapping.items():
        if f == 'name':
            name_idx = i
            break
    totals = find_total_rows(rows, headers, name_idx)
    return {'rows': rows, 'hi': hi, 'headers': headers, 'mapping': mapping,
            'unknown': unknown, 'total_rows': totals,
            'sig': fingerprint(headers), 'err': ''}
