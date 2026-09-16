# -*- coding: utf-8 -*-
"""表格模块 · 电子表格 导入导出（xlsx / csv）

导入
    单元格原文（公式保留 '=' 开头）、数字格式、粗体/底色/对齐/边框、
    合并单元格、列宽、冻结窗格、表名、命名区域。
    openpyxl 读不到缓存值时（data_only=True 且文件没存过计算结果），
    会自动退回公式原文，不让整表变空。

导出
    值 + 公式原文都写进去，Excel/WPS 打开会自动重算；
    数字格式、样式、列宽、冻结、合并一并带出。
"""
import io
import csv
import datetime as _dt

from . import sp_addr as A
from . import sp_engine as E
from .sp_style import Style

try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    _HAVE = True
except Exception:                                   # pragma: no cover
    _HAVE = False


# ------------------------------------------------------------------ 导出
#: 我们的线型名 → openpyxl Side.style
_XL_LINE = {'thin': 'thin', 'hair': 'hair', 'dotted': 'dotted',
            'dashed': 'dashed', 'dashdot': 'dashDot', 'medium': 'medium',
            'thick': 'thick', 'double': 'double'}


def _xl_color(c):
    """#rgb / #rrggbb / 颜色词 → openpyxl 要的 AARRGGBB"""
    c = (c or '').strip()
    if not c:
        return None
    if c.startswith('#'):
        h = c[1:]
        if len(h) == 3:
            h = ''.join(ch * 2 for ch in h)
        if len(h) != 6:
            return None
        try:
            int(h, 16)
        except ValueError:
            return None
        return 'FF' + h.upper()
    words = {'red': 'FF0000', 'black': '000000', 'blue': '0000FF',
             'green': '008000', 'yellow': 'FFFF00', 'white': 'FFFFFF'}
    return 'FF' + words.get(c.lower(), '000000')


#: 我们存的是 CSS 写法，Excel 的取值不一样，导出前要翻译
_XL_VALIGN = {'middle': 'center', 'center': 'center', 'top': 'top',
              'bottom': 'bottom'}
_XL_HALIGN = {'left': 'left', 'center': 'center', 'right': 'right',
              'justify': 'justify'}


def _xl_valign(v):
    """CSS 的 middle 在 Excel 里叫 center，不翻译会直接抛 ValueError"""
    return _XL_VALIGN.get((v or '').strip().lower())


def _xl_halign(v):
    return _XL_HALIGN.get((v or '').strip().lower())


def _xl_rot(deg):
    """CSS 角度（顺时针为正）→ Excel textRotation

    Excel：1-90 是逆时针，91-180 是顺时针（91 = 顺时针 1 度）。
    """
    try:
        d = int(deg)
    except Exception:
        return None
    if not d:
        return None
    d = max(-90, min(90, d))
    return (90 + d) if d > 0 else (-d)


def _xl_border(sty):
    """细致边框 bd 优先，退回 border 简写"""
    bd = getattr(sty, 'bd', None)
    if bd:
        sides = {}
        for k, key in (('l', 'left'), ('r', 'right'), ('t', 'top'),
                       ('b', 'bottom')):
            v = bd.get(k)
            if not v:
                continue
            s = v[0] if isinstance(v, (list, tuple)) else v
            c = v[1] if isinstance(v, (list, tuple)) and len(v) > 1 else ''
            if str(s).lower() == 'none':
                continue
            sides[key] = Side(style=_XL_LINE.get(s, 'thin'),
                              color=_xl_color(c) or 'FF000000')
        if sides:
            return Border(**sides)
        return None
    if getattr(sty, 'border', '') and sty.border != 'none':
        c = _xl_color(sty.border if sty.border.startswith('#') else '') \
            or 'FFC8CDD4'
        sd = Side(style='thin', color=c)
        return Border(left=sd, right=sd, top=sd, bottom=sd)
    return None


def to_xlsx(book):
    """Workbook → xlsx 二进制"""
    if not _HAVE:
        raise RuntimeError('缺少 openpyxl，导不出 xlsx')
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for sh in book.sheets:
        ws = wb.create_sheet(title=_safe_name(sh.name))
        r1, c1, r2, c2 = sh.used_range()
        if not sh.cells:
            r1, c1, r2, c2 = 0, 0, 0, 0

        for j in range(c1, c2 + 1):
            w = sh.col_width.get(j)
            if w:
                ws.column_dimensions[get_column_letter(j + 1)].width = \
                    float(w) * 1.15
        for i in range(r1, r2 + 1):
            h = sh.row_height.get(i)
            if h:
                ws.row_dimensions[i + 1].height = float(h)

        for i in range(r1, r2 + 1):
            for j in range(c1, c2 + 1):
                cl = sh.cells.get((i, j))
                if not cl or (not cl.raw and not cl.style):
                    continue
                cell = ws.cell(row=i + 1, column=j + 1)
                raw = cl.raw or ''
                if cl.kind == 'formula':
                    cell.value = raw               # 保留公式，让 Excel 自己算
                elif cl.kind == 'num':
                    cell.value = cl.v
                elif cl.kind == 'bool':
                    cell.value = bool(cl.v)
                elif cl.kind == 'err':
                    cell.value = cl.v
                else:
                    cell.value = cl.v if cl.v is not None else raw
                if cl.fmt:
                    cell.number_format = _xl_fmt(cl.fmt)
                st = cl.style
                if st:
                    cell.font = Font(bold=st.bold or None,
                                     italic=st.italic or None,
                                     underline='single' if st.underline else None,
                                     strike=st.strike or None,
                                     size=st.size or None,
                                     name=getattr(st, 'font', '') or None,
                                     color=_xl_color(st.color))
                    if st.bg:
                        cell.fill = PatternFill('solid', start_color=_xl_color(st.bg))
                    _akw = {'horizontal': _xl_halign(st.align),
                            'vertical': _xl_valign(st.valign),
                            'wrap_text': st.wrap or None,
                            'text_rotation': _xl_rot(getattr(st, 'rotate', 0) or 0)}
                    # openpyxl 的 indent 是 Float 描述符，传 None 会抛
                    # TypeError: expected float —— 所以 0 时干脆不传
                    _ind = int(getattr(st, 'indent', 0) or 0)
                    if _ind > 0:
                        _akw['indent'] = float(_ind)
                    cell.alignment = Alignment(**_akw)
                    bd = _xl_border(st)
                    if bd:
                        cell.border = bd
        for m in sh.merges:
            try:
                ws.merge_cells(start_row=m[0] + 1, start_column=m[1] + 1,
                               end_row=m[2] + 1, end_column=m[3] + 1)
            except Exception:
                pass
        if sh.frozen_rows or sh.frozen_cols:
            ws.freeze_panes = A.a1(sh.frozen_rows, sh.frozen_cols)
        if sh.tab_color:
            ws.sheet_properties.tabColor = 'FF' + sh.tab_color.lstrip('#')
        if sh.hidden:
            ws.sheet_state = 'hidden'

    for nm, ref in (book.names or {}).items():
        try:
            wb.defined_names.add(openpyxl.workbook.defined_name.DefinedName(
                nm, attr_text=ref))
        except Exception:
            pass
    try:
        wb.active = min(book.active, len(wb.sheetnames) - 1)
    except Exception:
        pass
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


def _safe_name(s, used=None):
    s = (s or 'Sheet').strip() or 'Sheet'
    for ch in '[]:*?/\\':
        s = s.replace(ch, '_')
    s = s[:31]
    while used and s in used:
        s = (s[:28] + '_%d' % (len(used)))[:31]
    return s


def _xl_fmt(code):
    """把我们的格式代码转成 Excel 能认的"""
    if not code:
        return 'General'
    c = code.replace('¥', '"¥"').replace('$', '"$"')
    return c


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
def from_xlsx(path=None, stream=None, keep_formula=True):
    """xlsx → Workbook"""
    if not _HAVE:
        raise RuntimeError('缺少 openpyxl，读不了 xlsx')
    wb = openpyxl.load_workbook(stream or path, data_only=False)
    book = E.Workbook((wb.properties.title if wb.properties else None)
                      or '工作簿1')
    book.sheets = []
    for ws in wb.worksheets:
        sh = E.Sheet(ws.title, max(ws.max_row, 50), max(ws.max_column, 20))
        sh.book = book
        book.sheets.append(sh)

        for row in ws.iter_rows():
            for cell in row:
                i, j = cell.row - 1, cell.column - 1
                v = cell.value
                if v is None:
                    continue
                if isinstance(v, str) and v.startswith('=') and keep_formula:
                    raw = v
                elif isinstance(v, (_dt.datetime, _dt.date)):
                    raw = _from_dt(v)
                elif isinstance(v, bool):
                    raw = 'TRUE' if v else 'FALSE'
                else:
                    raw = '' if v is None else str(v)
                cl = E.Cell(i, j, raw)
                sh._classify(cl)
                nf = cell.number_format
                if nf and nf != 'General':
                    cl.fmt = _our_fmt(nf)
                f = cell.font
                st = Style()
                dirty = False
                if f:
                    if f.bold:
                        st.bold = True; dirty = True
                    if f.italic:
                        st.italic = True; dirty = True
                    if f.underline and str(f.underline) not in ('none', 'None'):
                        st.underline = True; dirty = True
                    if f.strike:
                        st.strike = True; dirty = True
                    if f.sz:
                        st.size = int(f.sz); dirty = True
                    if f.color and getattr(f.color, 'rgb', None):
                        rgb = str(f.color.rgb)
                        if len(rgb) >= 6:
                            st.color = '#' + rgb[-6:]; dirty = True
                fill = cell.fill
                if fill and getattr(fill, 'fgColor', None) and \
                        fill.patternType and fill.fgColor.rgb:
                    rgb = str(fill.fgColor.rgb)
                    if len(rgb) >= 6 and rgb[-6:] not in ('000000', 'FFFFFF'):
                        st.bg = '#' + rgb[-6:]; dirty = True
                al = cell.alignment
                if al:
                    if al.horizontal:
                        st.align = al.horizontal; dirty = True
                    if al.vertical:
                        st.valign = al.vertical; dirty = True
                    if al.wrap_text:
                        st.wrap = True; dirty = True
                bd = cell.border
                if bd and any([bd.left and bd.left.style, bd.right
                               and bd.right.style, bd.top and bd.top.style,
                               bd.bottom and bd.bottom.style]):
                    st.border = 'box'; dirty = True
                if dirty:
                    cl.style = st
                sh.cells[(i, j)] = cl

        for k, v in (ws.column_dimensions or {}).items():
            try:
                sh.col_width[A.col_index(k) if isinstance(k, str)
                             else int(k) - 1] = round(v.width or 10, 1)
            except Exception:
                pass
        for k, v in (ws.row_dimensions or {}).items():
            try:
                sh.row_height[int(k) - 1] = round(v.height or 18)
            except Exception:
                pass
        fp = ws.freeze_panes
        if fp:
            try:
                r, c = A.a1_to_rc(str(fp).replace('$', ''))
                sh.frozen_rows, sh.frozen_cols = r, c
            except Exception:
                pass
        for mg in ws.merged_cells.ranges:
            try:
                sh.merges.append((mg.min_row - 1, mg.min_col - 1,
                                  mg.max_row - 1, mg.max_col - 1))
            except Exception:
                pass
        if ws.sheet_state == 'hidden':
            sh.hidden = True
        if ws.sheet_properties.tabColor:
            try:
                sh.tab_color = '#' + str(ws.sheet_properties.tabColor.rgb)[-6:]
            except Exception:
                pass
    try:
        for nm, dn in wb.defined_names.items():
            book.names[nm] = dn.attr_text
    except Exception:
        pass
    if not book.sheets:
        book.add('Sheet1')
    book.active = 0
    for s in book.sheets:
        s.invalidate()
    return book


def _from_dt(v):
    if isinstance(v, _dt.datetime):
        return v.strftime('%Y-%m-%d %H:%M:%S')
    return v.strftime('%Y-%m-%d')


def _our_fmt(nf):
    """Excel 格式代码 → 我们的格式代码（只做常见映射）"""
    if not nf:
        return ''
    s = str(nf)
    s = s.replace('"¥"', '¥').replace('"$"', '$')
    s = s.replace('"元"', '元')
    s = re_multi(r'\[[^\]]*\]', '', s)
    return s


def re_multi(pat, rep, s):
    import re
    return re.sub(pat, rep, s)


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
