# -*- coding: utf-8 -*-
"""表格模块 · 导出层

导出是"制表"的另一半，所有需要出 Excel / CSV 的地方都走这里：

    to_xlsx()  真正的 .xlsx（冻结首行、表头配色、列宽自适应）
    to_csv()   CSV（带 BOM，Excel 打开不乱码；防公式注入）
    to_json()

CSV 公式注入防护：单元格以 = + - @ 开头时，前面补一个单引号。
否则导出的 CSV 在 Excel 里打开会执行公式（=cmd|'/c calc'!A1 这类）。
"""

# 需要加单引号防注入的开头字符
_INJ = ('=', '+', '-', '@', '\t', '\r')


def csv_safe(v):
    """CSV / Excel 单元格安全化"""
    if v is None:
        return ''
    s = str(v)
    if s[:1] in _INJ:
        # 负数是正常数据，只有后面跟非数字时才当公式处理
        if s.startswith('-') and s[1:2].isdigit():
            return s
        return "'" + s
    return s


def _col_widths(rows, maxw=40, minw=6):
    """按内容算列宽（中文按 2 个字符宽算）"""
    if not rows:
        return []
    n = max(len(r) for r in rows)
    out = []
    for i in range(n):
        w = minw
        for r in rows[:200]:          # 只看前 200 行，够用且快
            if i < len(r):
                s = str(r[i] or '')
                ln = sum(2 if ord(c) > 127 else 1 for c in s)
                w = max(w, ln + 2)
        out.append(min(w, maxw))
    return out


def to_xlsx(headers, rows, sheet='Sheet1', freeze='A2', title=None):
    """生成 xlsx 二进制内容。

    :param headers: ['日期', '物料名称', ...]
    :param rows:    二维列表
    :param freeze:  冻结位置，'A2' = 冻结首行
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = (sheet or 'Sheet1')[:31]

    r0 = 1
    if title:
        ws.cell(row=1, column=1, value=title)
        ws.cell(row=1, column=1).font = Font(bold=True, size=13)
        r0 = 3   # 标题下空一行再放表头

    # 表头
    head_fill = PatternFill('solid', fgColor='DDEBF7')
    head_font = Font(bold=True)
    for j, h in enumerate(headers or [], start=1):
        c = ws.cell(row=r0, column=j, value=str(h or ''))
        c.fill = head_fill
        c.font = head_font
        c.alignment = Alignment(horizontal='center', vertical='center')

    # 数据
    for i, row in enumerate(rows or [], start=r0 + 1):
        for j, v in enumerate(row, start=1):
            if isinstance(v, (int, float)):
                ws.cell(row=i, column=j, value=v)
            else:
                ws.cell(row=i, column=j, value=csv_safe(v))

    # 列宽
    for j, w in enumerate(_col_widths([list(headers or [])] + list(rows or [])),
                          start=1):
        ws.column_dimensions[get_column_letter(j)].width = w

    if freeze:
        ws.freeze_panes = (freeze if not title
                           else 'A%d' % (r0 + 1))

    import io
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def xlsx_response(headers, rows, filename, sheet='Sheet1', title=None):
    """打包成 Flask 的下载响应"""
    from flask import Response
    from urllib.parse import quote

    data = to_xlsx(headers, rows, sheet=sheet, title=title)
    fn = quote(str(filename or 'export.xlsx').encode('utf-8'))
    return Response(
        data,
        mimetype='application/vnd.openxmlformats-officedocument.'
                 'spreadsheetml.sheet',
        headers={'Content-Disposition':
                 "attachment; filename*=UTF-8''%s" % fn})


def to_csv(headers, rows, bom=True):
    """生成 CSV 文本（带 BOM，Excel 直接打开不乱码）"""
    import io
    import csv as _csv
    buf = io.StringIO()
    w = _csv.writer(buf, quoting=_csv.QUOTE_MINIMAL)
    if headers:
        w.writerow([csv_safe(h) for h in headers])
    for r in (rows or []):
        w.writerow([csv_safe(v) for v in r])
    s = buf.getvalue()
    return ('\ufeff' + s) if bom else s


def csv_response(headers, rows, filename):
    from flask import Response
    from urllib.parse import quote
    data = to_csv(headers, rows).encode('utf-8')
    fn = quote(str(filename or 'export.csv').encode('utf-8'))
    return Response(
        data, mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition':
                 "attachment; filename*=UTF-8''%s" % fn})


def to_json(headers, rows):
    import json
    out = []
    for r in (rows or []):
        d = {}
        for i, h in enumerate(headers or []):
            d[str(h)] = r[i] if i < len(r) else ''
        out.append(d)
    return json.dumps(out, ensure_ascii=False, indent=2)
