# -*- coding: utf-8 -*-
"""制表 IO · csv 读写

导出带 BOM（Excel 打开中文不乱码）；
导入自动探测编码：utf-8-sig / gbk / utf-8 / latin-1 依次尝试。
"""
import io
import csv

from ..engine import core as E

def to_csv(sheet, r1=None, c1=None, r2=None, c2=None):
    if r1 is None:
        r1, c1, r2, c2 = sheet.used_range()
    out = io.StringIO()
    out.write('\ufeff')
    w = csv.writer(out)
    for i in range(r1, (r2 if r2 is not None else sheet.max_used_row()) + 1):
        row = []
        for j in range(c1, (c2 if c2 is not None else sheet.max_used_col()) + 1):
            cl = sheet.cells.get((i, j))
            if cl is None:
                row.append('')
            elif cl.kind == 'formula':
                row.append(sheet.value(i, j) if i is not None else '')
            else:
                row.append(cl.display())
        w.writerow(row)
    return out.getvalue()


# ------------------------------------------------------------------ 导入


def from_csv(path=None, stream=None, name='Sheet1', enc=None):
    """csv → Workbook（单表）"""
    data = stream.read() if stream is not None else open(path, 'rb').read()
    for e in ([enc] if enc else ['utf-8-sig', 'gbk', 'utf-8', 'latin-1']):
        try:
            txt = data.decode(e)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    else:
        txt = data.decode('utf-8', 'ignore')
    book = E.Workbook(name)
    sh = book.add(name, 200, 20)
    rdr = csv.reader(io.StringIO(txt))
    for i, row in enumerate(rdr):
        for j, v in enumerate(row):
            if v != '':
                cl = E.Cell(i, j, v)
                sh._classify(cl)
                sh.cells[(i, j)] = cl
    sh.rows = max(200, sh.max_used_row() + 2)
    sh.cols = max(20, sh.max_used_col() + 2)
    return book
