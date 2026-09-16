# -*- coding: utf-8 -*-
"""UI 模块 · 组件与骨架

提供一批返回 HTML 片段的小函数，模板里直接用：
    btn() badge() alert() empty() sort_th() nav()

原则：简练直观。组件只做一件事，不嵌套复杂结构。
"""
from . import theme


def esc(s):
    from markupsafe import escape
    return escape('' if s is None else str(s))


def btn(text, href=None, kind='', onclick=None, **attrs):
    """按钮 / 链接按钮"""
    cls = 'btn' + (' btn-%s' % kind if kind else '')
    a = ' '.join('%s="%s"' % (k.replace('_', '-'), esc(v))
                 for k, v in attrs.items() if v is not None)
    if href:
        return '<a class="%s" href="%s" %s>%s</a>' % (cls, esc(href), a, esc(text))
    if onclick:
        return ('<button type="button" class="%s" onclick="%s" %s>%s</button>'
                % (cls, esc(onclick), a, esc(text)))
    return '<button type="submit" class="%s" %s>%s</button>' % (cls, a, esc(text))


def badge(text, kind=''):
    """状态徽标"""
    cls = 'badge' + (' b-%s' % kind if kind else '')
    return '<span class="%s">%s</span>' % (cls, esc(text))


def alert(msg, kind=''):
    """提示条 kind: ok / info / ''（默认警告色）"""
    cls = 'alert' + (' alert-%s' % kind if kind else '')
    return '<div class="%s">%s</div>' % (cls, msg)


def empty(msg='这里还没有数据', hint=''):
    """空态"""
    h = '<div class="muted" style="margin-top:6px">%s</div>' % hint if hint else ''
    return '<div class="empty">%s%s</div>' % (esc(msg), h)


def sort_th(label, key, sort='', dir_='', num=False, width=None):
    """可点排序的表头。三态：默认 → 升 → 降"""
    arrow = ' ▲' if (sort == key and dir_ == 'asc') else (
        ' ▼' if (sort == key and dir_ == 'desc') else '')
    nxt = '' if sort != key else ('desc' if dir_ == 'asc' else '')
    from flask import request
    q = dict(request.args)
    q['sort'] = key
    if nxt:
        q['dir'] = nxt
    else:
        q.pop('dir', None)
    qs = '&'.join('%s=%s' % (k, v) for k, v in q.items() if v not in (None, ''))
    cls = 'num' if num else ''
    w = ' style="width:%s"' % width if width else ''
    return ('<th class="%s"%s><a href="?%s">%s%s</a></th>'
            % (cls, w, esc(qs), esc(label), arrow))


def nav(active=''):
    """底部导航栏"""
    from flask import url_for
    items = []
    for ep, icon, name in theme.NAV:
        try:
            href = url_for(ep)
        except Exception:
            href = '#'
        on = ' on' if ep == active else ''
        items.append('<a class="%s" href="%s"><span>%s</span>%s</a>'
                     % (on.strip(), esc(href), icon, esc(name)))
    return '<nav class="nav">%s</nav>' % ''.join(items)


def more_links(active=''):
    """更多页面入口（首页 / 各页底部展示）"""
    from flask import url_for
    out = []
    for ep, icon, name in theme.MORE:
        try:
            href = url_for(ep)
        except Exception:
            continue
        out.append('<a class="btn" href="%s">%s %s</a>'
                   % (esc(href), icon, esc(name)))
    return '<div class="row">%s</div>' % ''.join(out)


def page(title, sub=''):
    """页头"""
    s = '<div class="muted">%s</div>' % esc(sub) if sub else ''
    return '<h1>%s</h1>%s' % (esc(title), s)


def stat_cards(items):
    """首页那种 数字 + 说明 的小卡片组

    items: [('本月进', 120, ''), ('本月出', 80, 'warn'), ...]
    """
    out = []
    for name, val, kind in items:
        out.append(
            '<div class="card" style="flex:1;min-width:110px;text-align:center">'
            '<div class="muted">%s</div>'
            '<div style="font-size:20px;font-weight:600;margin-top:4px">%s</div>'
            '</div>' % (esc(name), esc(val)))
    return '<div class="row">%s</div>' % ''.join(out)
