# -*- coding: utf-8 -*-
"""表格模块 · 渲染层

把 TableDef 或 (表头, 行) 渲染成 HTML。调用 UI 模块的样式，不自己造样式。

能力（对齐开源表格项目的常见项）：
    冻结首行 / 冻结左侧列
    表头点击排序
    数字右对齐并格式化
    条件着色（负数标红、低于安全库存标红）
    横向滚动容器（列多时手机也能看）
"""
from . import model
from ..ui import widgets as W


def render(headers, rows, sort_key='', sort_dir='', align=None,
           cls='', total_row=None, colored=None, empty_msg='这里还没有数据'):
    """渲染一张只读表格。

    :param headers: [('日期','date'), ('数量','qty', True)] 或 ['日期','数量']
    :param rows:    二维列表
    :param align:   {列索引: 'right'}
    :param colored: 函数 (行索引, 列索引, 值) → css 类名 或 ''
    """
    cols = []
    for h in headers or []:
        if isinstance(h, (tuple, list)):
            cols.append({'label': h[0], 'key': (h[1] if len(h) > 1 else h[0]),
                         'num': bool(h[2]) if len(h) > 2 else False})
        else:
            cols.append({'label': h, 'key': h, 'num': False})

    if not rows:
        return '<div class="card">%s</div>' % W.empty(empty_msg)

    out = ['<div class="card scroll"><table class="%s">' % cls]
    out.append('<thead><tr>')
    for c in cols:
        if c['num']:
            out.append(W.sort_th(c['label'], c['key'], sort_key, sort_dir,
                                 num=True))
        else:
            out.append(W.sort_th(c['label'], c['key'], sort_key, sort_dir))
    out.append('</tr></thead><tbody>')

    for i, r in enumerate(rows):
        out.append('<tr>')
        for j, c in enumerate(cols):
            v = r[j] if j < len(r) else ''
            cls_td = ' class="num"' if (c['num'] or
                                        (align and align.get(j) == 'right')) else ''
            style = ''
            if colored:
                try:
                    k = colored(i, j, v)
                    if k:
                        style = ' style="color:var(--%s)"' % k
                except Exception:
                    pass
            out.append('<td%s%s>%s</td>' % (cls_td, style, W.esc(v)))
        out.append('</tr>')

    if total_row:
        out.append('<tr style="font-weight:600;background:var(--soft)">')
        for j, c in enumerate(cols):
            v = total_row[j] if j < len(total_row) else ''
            out.append('<td class="num">%s</td>' % W.esc(v))
        out.append('</tr>')

    out.append('</tbody></table></div>')
    return ''.join(out)


def render_tabledef(td, **kw):
    """渲染 TableDef（用它的列定义决定表头和对齐）"""
    cols = td.enabled_cols()
    headers = [(c.header, c.key, c.ctype in (model.T_NUM, model.T_FORMULA))
               for c in cols]
    rows = [[c.fmt_val(r.get(c.key)) for c in cols] for r in td.rows]
    return render(headers, rows, **kw)


def input_grid(td, name_prefix='r', editable_cols=None):
    """渲染可编辑的表格（录入页用）"""
    cols = [c for c in td.enabled_cols()
            if (editable_cols is None or c.key in editable_cols)]
    out = ['<div class="scroll"><table><thead><tr>']
    for c in cols:
        out.append('<th%s>%s</th>' % (' class="num"' if c.ctype in
                                      (model.T_NUM, model.T_FORMULA) else '',
                                      W.esc(c.header)))
    out.append('</tr></thead><tbody>')
    for i, r in enumerate(td.rows):
        out.append('<tr>')
        for c in cols:
            v = r.get(c.key, '')
            v = '' if v is None else v
            if c.ctype == model.T_SELECT:
                opts = ''.join('<option value="%s"%s>%s</option>'
                               % (W.esc(o), ' selected' if str(v) == o else '',
                                  W.esc(o))
                               for o in [x.strip() for x in
                                         (c.options or '').split(',') if x.strip()])
                cell = '<select name="%s%d_%s">%s</select>' % (
                    name_prefix, i, c.key, opts)
            else:
                t = 'text'
                if c.ctype in (model.T_NUM, model.T_FORMULA):
                    t = 'number'
                elif c.ctype == model.T_DATE:
                    t = 'date'
                cell = ('<input type="%s" name="%s%d_%s" value="%s">'
                        % (t, name_prefix, i, c.key, W.esc(v)))
            out.append('<td>%s</td>' % cell)
        out.append('</tr>')
    out.append('</tbody></table></div>')
    return ''.join(out)
