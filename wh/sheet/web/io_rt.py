# -*- coding: utf-8 -*-
"""电子表格导入导出路由：xlsx 导入、导出 xlsx / csv。"""
from .common import *                                  # noqa: F401,F403
from .common import _flush, _load                      # noqa: F401
# ------------------------------------------------------------------ 导入导出
@bp.route('/sheet/<int:bid>/import', methods=['POST'])
def sheet_import(bid):
    book, st = _load(bid)
    if not book:
        return redirect(url_for('sheet_index'))
    f = request.files.get('file')
    if not f or not f.filename:
        return redirect(url_for('sheet_open', bid=bid, msg='没选文件'))
    ext = os.path.splitext(f.filename or '')[1].lower()
    tmp = os.path.join(util.TMP, '%d_%s' % (bid, os.path.basename(f.filename)))
    try:
        os.makedirs(util.TMP, exist_ok=True)
        f.save(tmp)
        if ext in ('.csv', '.txt'):
            nb = IO.from_csv(path=tmp, name='导入')
        else:
            nb = IO.from_xlsx(path=tmp)
    except Exception as e:
        return redirect(url_for('sheet_open', bid=bid,
                                msg='导入失败：%s' % e))
    # 追加到当前工作簿（保留已有的表）
    for s in nb.sheets:
        nm = s.name
        i = 2
        while book.sheet(nm):
            nm = '%s%d' % (s.name, i)
            i += 1
        s.name = nm
        s.book = book
        book.sheets.append(s)
    _flush(bid, book)
    return redirect(url_for('sheet_open', bid=bid, msg='已导入 %d 张表'
                            % len(nb.sheets)))


@bp.route('/sheet/<int:bid>/export.xlsx')
def sheet_export_xlsx(bid):
    book, _ = _load(bid)
    if not book:
        return redirect(url_for('sheet_index'))
    data = IO.to_xlsx(book)
    from urllib.parse import quote
    return Response(data, mimetype='application/vnd.openxmlformats-'
                    'officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition':
                             "attachment; filename=wb.xlsx; filename*=UTF-8''%s.xlsx"
                             % quote(book.name)})


@bp.route('/sheet/<int:bid>/export.csv')
def sheet_export_csv(bid):
    book, _ = _load(bid)
    if not book:
        return redirect(url_for('sheet_index'))
    txt = IO.to_csv(book.act)          # to_csv 自己已带 BOM，这里不能再加一次
    from urllib.parse import quote
    return Response(txt, content_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition':
                             "attachment; filename=wb.csv; filename*=UTF-8''%s.csv"
                             % quote(book.act.name)})


