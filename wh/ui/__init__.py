# -*- coding: utf-8 -*-
"""UI 模块 —— 简练直观的界面层

只提供：配色主题、页面骨架、一批小组件。
不含业务逻辑，业务模块想显示什么就调组件拼。

模板里可以直接用：
    {{ ui_css|safe }}      全局样式
    {{ nav(active)|safe }} 底部导航
    {{ btn('保存', kind='p')|safe }}
"""
from . import theme, widgets, nav   # noqa: F401

css = theme.css
NAV = theme.NAV
MORE = theme.MORE
COLORS = theme.COLORS

main_items = nav.main_items
sub_items = nav.sub_items
current_module = nav.current_module


def context():
    """调度文件注入给所有模板的公共变量"""
    from ..core.util import new_nonce, today, ym
    return {
        'nav_main': nav.main_items(),
        'nav_sub': nav.sub_items(),
        'nav_mod': nav.current_module(),
        'ui_css': theme.css(),
        'ui_theme': theme,
        'ui': widgets,
        'nav': widgets.nav,
        'more_links': widgets.more_links,
        'btn': widgets.btn,
        'badge': widgets.badge,
        'alert': widgets.alert,
        'empty': widgets.empty,
        'page': widgets.page,
        'stat_cards': widgets.stat_cards,
        'sort_th': widgets.sort_th,
        'new_nonce': new_nonce,
        'today': today(),
        'ym': ym(),
    }
