# -*- coding: utf-8 -*-
"""单元格 —— 一格 = 原文(raw)/类型/值/样式/数字格式/批注/有效性

这是数据模型的最小单位，不认识"表"，也不认识"求值器"。
"""
from ..kernel import addr as A
from ..kernel import format_value, is_err

MAX_CELLS = 400000          # 单表单元格上限，防止被恶意公式撑爆


class Cell(object):
    __slots__ = ('row', 'col', 'raw', 'kind', 'ast', 'v', 'fmt', 'style',
                 'note', 'valid', 'bad')

    def __init__(self, row=0, col=0, raw=''):
        self.row = row
        self.col = col
        self.raw = raw or ''
        self.kind = 'blank'
        self.ast = None
        self.v = None
        self.fmt = ''
        self.style = None
        self.note = ''
        self.valid = None
        self.bad = ''          # 公式解析错误信息

    @property
    def addr(self):
        return A.a1(self.row, self.col)

    def display(self):
        """按数字格式渲染显示文本"""
        if is_err(self.v):
            return str(self.v)
        return format_value(self.v, self.fmt)

    def to_dict(self, with_value=True):
        d = {'r': self.row, 'c': self.col, 'raw': self.raw,
             'kind': self.kind, 'fmt': self.fmt,
             'note': self.note}
        if self.style:
            d['style'] = self.style.to_dict()
        if with_value:
            d['v'] = _jsonable(self.v)
            d['text'] = self.display()
        if self.bad:
            d['bad'] = self.bad
        return d


def _jsonable(v):
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v
    if v is None:
        return ''
    return str(v)

