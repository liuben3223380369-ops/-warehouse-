# -*- coding: utf-8 -*-
"""进阶动作登记表

签名统一为 `(book, st, sh, g, rect) -> dict`，
由上层 web 路由登记进动作表后即可通过接口调用。"""
from .clip import a_clip, a_autosum, a_hide
from .fill import a_series
from .valid import a_validate, check_validation, a_dedupe
from .analyze import a_chart, a_pivot, a_stat, a_subtotal
from .fmt import a_clear, a_height, a_border


EXTRA = {
    'clip': a_clip, 'autosum': a_autosum, 'hide': a_hide, 'series': a_series,
    'validate': a_validate, 'chart': a_chart, 'pivot': a_pivot,
    'clear': a_clear, 'stat': a_stat, 'height': a_height,
    'border': a_border, 'dedupe': a_dedupe, 'subtotal': a_subtotal,
}
