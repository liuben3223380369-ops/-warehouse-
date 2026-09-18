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


def _cell(v):
    """写进单元格前的安全化：数字原样，其余走 csv_safe 防公式注入"""
    if v is None:
        return ''
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v
    return csv_safe(v)


def to_xlsx(headers, rows, sheet='Sheet1', freeze='A2', title=None, chart=None):
    """生成 xlsx 二进制内容。

    :param headers: ['日期', '物料名称', ...]
    :param rows:    二维列表
    :param freeze:  冻结位置，'A2' = 冻结首行
    :param chart:   可选的原生 Excel 图表，见 _add_chart 的说明。
                    传了就用 xlsxwriter 画（openpyxl 画的图 Excel 兼容性差），
                    没传也优先走 xlsxwriter —— 它比 openpyxl 快约一倍，
                    且内存占用低，2 万行导出差别明显。

    两种引擎产出的文件在 Excel / WPS 里打开效果一致，
    xlsxwriter 装不上时自动回退 openpyxl，调用方无需关心。
    """
    try:
        return _to_xlsx_xlsxwriter(headers, rows, sheet=sheet,
                                   freeze=freeze, title=title, chart=chart)
    except ImportError:
        return _to_xlsx_openpyxl(headers, rows, sheet=sheet,
                                 freeze=freeze, title=title)


def _to_xlsx_xlsxwriter(headers, rows, sheet='Sheet1', freeze='A2',
                        title=None, chart=None):
    """xlsxwriter 版：快、省内存、支持原生图表"""
    import xlsxwriter
    import io as _io

    headers = list(headers or [])
    rows = [list(r) for r in (rows or [])]

    buf = _io.BytesIO()
    # strings_to_formulas=False：单元格以 = 开头时不当公式写，
    # 避免导入的外部数据被 Excel 当成公式执行（跟 csv_safe 是同一层防护）
    wb = xlsxwriter.Workbook(buf, {
        'in_memory': True,
        'strings_to_formulas': False,
        'strings_to_urls': False,
        'constant_memory': False,
    })
    ws = wb.add_worksheet((sheet or 'Sheet1')[:31])

    fmt_title = wb.add_format({'bold': True, 'font_size': 13})
    fmt_head = wb.add_format({'bold': True, 'bg_color': '#DDEBF7',
                              'align': 'center', 'valign': 'vcenter',
                              'border': 1, 'text_wrap': True})
    fmt_txt = wb.add_format({'valign': 'vcenter'})
    fmt_num = wb.add_format({'num_format': '#,##0.00', 'align': 'right'})
    fmt_int = wb.add_format({'num_format': '#,##0', 'align': 'right'})

    r0 = 0                      # xlsxwriter 行号从 0 开始
    if title:
        ws.write(0, 0, str(title), fmt_title)
        r0 = 2                  # 标题下空一行再放表头

    widths = _col_widths([headers] + rows) or [10] * len(headers)
    for j, h in enumerate(headers):
        ws.write(r0, j, str(h or ''), fmt_head)
        if j < len(widths):
            ws.set_column(j, j, widths[j])

    for i, row in enumerate(rows, start=r0 + 1):
        for j, v in enumerate(row):
            if v is None or v == '':
                continue                      # 空值不写，省内存也更好看
            if isinstance(v, bool):
                ws.write(i, j, bool(v), fmt_txt)
            elif isinstance(v, (int, float)):
                # 整数用整数格式，避免数量列显示成 10.00
                ws.write_number(i, j, v,
                                fmt_int if float(v).is_integer() and abs(v) < 1e15
                                else fmt_num)
            else:
                ws.write_string(i, j, csv_safe(v), fmt_txt)

    if freeze:
        ws.freeze_panes(r0 + 1, 0)            # 冻结表头行
    if rows:
        ws.autofilter(r0, 0, r0 + len(rows), max(len(headers) - 1, 0))

    if chart:
        _add_chart(wb, ws, headers, rows, r0, chart)

    wb.close()
    return buf.getvalue()


def _add_chart(wb, ws, headers, rows, r0, spec):
    """往工作表里插一张原生 Excel 图表。

    spec 的写法（跟表格页 ECharts 用的是同一份数据，不用重新整理）：

        {
          'type': 'column',            # column / bar / line / pie / area
          'title': '进出趋势',
          'cats':  ['1月','2月'],       # 横轴类目
          'series':[{'name':'入库','values':[10,20]}],
          'anchor':'A20',              # 图插在哪，默认数据区下方
          'x_title':'月份', 'y_title':'数量'
        }

    图表数据写在一块隐藏区域（而不是引用已有单元格），
    这样调用方传什么数据就画什么，不用去对齐列索引。
    """
    try:
        import xlsxwriter
    except ImportError:
        return
    st = (spec or {}).get('type') or 'column'
    cats = list((spec or {}).get('cats') or [])
    series = list((spec or {}).get('series') or [])
    if not series:
        return

    _MAP = {'column': 'column', 'bar': 'bar', 'line': 'line',
            'pie': 'pie', 'area': 'area', 'scatter': 'scatter'}

    # 图表数据写进一张单独的隐藏表：
    # 放正文右侧会污染表格，隐藏行列又会让图表画不出来（Excel 默认不取隐藏行列），
    # 隐藏整张工作表则不影响取数，是唯一两边都干净的做法。
    dname = '图表数据'
    try:
        dws = wb.add_worksheet(dname)
    except Exception:
        dname = '图表数据2'
        dws = wb.add_worksheet(dname)
    dws.hide()

    c0 = 0
    r_top = 0
    dws.write(r_top, c0, (spec or {}).get('x_title') or '类目')
    for i, c in enumerate(cats, start=1):
        dws.write(r_top + i, c0, csv_safe(c))
    for k, s in enumerate(series):
        col = c0 + 1 + k
        dws.write(r_top, col, csv_safe(s.get('name') or ('系列%d' % (k + 1))))
        for i, v in enumerate(s.get('values') or [], start=1):
            if isinstance(v, (int, float)):
                dws.write_number(r_top + i, col, v)
            else:
                dws.write(r_top + i, col, '')

    ch = wb.add_chart({'type': _MAP.get(st, 'column')})
    n = len(cats)
    for k, s in enumerate(series):
        col = c0 + 1 + k
        cfg = {
            'name':       [dname, r_top, col],
            'categories': [dname, r_top + 1, c0, r_top + n, c0],
            'values':     [dname, r_top + 1, col, r_top + n, col],
        }
        if st == 'pie':
            # 饼图只画第一个系列，多个系列叠上去没有意义
            ch.add_series({'name': cfg['name'],
                           'categories': cfg['categories'],
                           'values': cfg['values']})
            break
        if st == 'scatter':
            ch.add_series({'name': cfg['name'],
                           'categories': cfg['categories'],
                           'values': cfg['values']})
        else:
            ch.add_series(cfg)

    ch.set_title({'name': (spec or {}).get('title') or ''})
    if st != 'pie':
        ch.set_x_axis({'name': (spec or {}).get('x_title') or ''})
        ch.set_y_axis({'name': (spec or {}).get('y_title') or ''})
    ch.set_size({'width': 720, 'height': 400})
    ch.set_legend({'position': 'bottom'})
    anchor = (spec or {}).get('anchor')
    if not anchor:
        anchor = 'A%d' % (r0 + len(rows) + 3)
    ws.insert_chart(anchor, ch)


def _to_xlsx_openpyxl(headers, rows, sheet='Sheet1', freeze='A2', title=None):
    """openpyxl 版（回退用）：xlsxwriter 装不上时走这里"""
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


def xlsx_response(headers, rows, filename, sheet='Sheet1', title=None,
                  chart=None):
    """打包成 Flask 的下载响应"""
    from flask import Response
    from urllib.parse import quote

    data = to_xlsx(headers, rows, sheet=sheet, title=title, chart=chart)
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
