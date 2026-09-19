# -*- coding: utf-8 -*-
"""表格模块 —— 制表能力的中枢

对外暴露一个门面对象 `tbl`，其它模块要用表格能力就调它，
不需要知道内部拆成了 model / formula / parse / ops / export / render。

    from ..table import tbl
    tbl.fingerprint(headers)        # 列名指纹
    tbl.evaluate('qty*price', {...})  # 公式求值
    tbl.preview(path)               # 导入预览
    tbl.xlsx_response(...)          # 导出

不提供路由：模板管理与列映射配置已下线，表格由制表模块（wh.sheet）承载。
"""

from . import model, formula, parse, ops, render, helpers          # noqa: E402
from . import form                                                  # noqa: E402
from ..sheet import kernel as K                                  # noqa: E402  制表内核
from ..sheet import batch                                         # noqa: E402
from ..sheet.engine import core as sp_engine                       # noqa: E402
from ..sheet import io as sp_io                                    # noqa: E402

from .model import (ColumnDef, TableDef, fmt_value, fmt_money, fmt_qty,   # noqa
                    fmt_int, T_TEXT, T_NUM, T_DATE, T_SELECT, T_FORMULA,
                    FMT_QTY, FMT_MONEY, FMT_INT)
from .formula import evaluate, check, used_fields                          # noqa
from .parse import (read_table, detect_header, merge_head_layers,          # noqa
                    norm_head, split_unit, fingerprint, match_columns,
                    is_total_row, find_total_rows, preview)
from .ops import (sort_rows, filter_rows, paginate, window, aggregate,     # noqa
                  group_by, dedupe, add_total_row, next_dir)
from ..core import export                                           # noqa: E402
from ..core.export import (to_xlsx, to_csv, to_json, csv_safe,             # noqa
                           xlsx_response, csv_response)
from .render import render, render_tabledef, input_grid                    # noqa
from .helpers import all_custom_cols, cell_val                            # noqa
from .form import (schema as form_schema, card_fields, label_map,          # noqa
                   PINNED_TXN, PINNED_PO, AREA_FIDS)
from ..sheet.batch import (next_no as next_batch_no, peek as peek_batch_no,        # noqa
                           fill as fill_batch, label as batch_label, DEFAULT_ROWS,
                           PREFIX as BATCH_PREFIX)


class _Facade(object):
    """门面：把子模块的常用能力聚到一个名字下"""

    def __init__(self):
        self.model = model
        self.formula = formula
        self.parse = parse
        self.ops = ops
        self.export = export
        self.render = render
        self.engine = sp_engine
        self.io = sp_io

    # --- 高频快捷方式 ---
    def fingerprint(self, headers):
        return parse.fingerprint(headers)

    def evaluate(self, expr, values=None):
        return formula.evaluate(expr, values)

    def check(self, expr):
        return formula.check(expr)

    def preview(self, path, ext=None):
        return parse.preview(path, ext)

    def sort(self, rows, key, dir_='asc'):
        return ops.sort_rows(rows, key, dir_)

    def xlsx_response(self, headers, rows, filename, **kw):
        return export.xlsx_response(headers, rows, filename, **kw)

    def csv_response(self, headers, rows, filename):
        return export.csv_response(headers, rows, filename)

    def render(self, headers, rows, **kw):
        return render.render(headers, rows, **kw)

    # --- 表单服务：录入卡片长什么样，由表格说了算 ---
    def form_schema(self, tid, scene='txn'):
        return form.schema(tid, scene)

    def card_fields(self, tid, scene='txn'):
        return form.card_fields(tid, scene)

    def label_map(self, tid, scene='txn'):
        return form.label_map(tid, scene)

    # --- 批次号：A 列的自动编号 ---
    def next_batch(self, n=1):
        return batch.next_no(n)

    def fill_batch(self, sheet, rows=batch.DEFAULT_ROWS, with_header=True):
        return batch.fill(sheet, rows, with_header)

    def batch_label(self):
        return batch.label()

    # --- 电子表格引擎 ---
    def new_book(self, name='工作簿'):
        b = sp_engine.Workbook(name)
        b.add('Sheet1')
        return b

    def book(self, data=None):
        return sp_engine.Workbook.from_dict(data) if data \
            else self.new_book()

    def calc(self, expr, sheet=None, row=0, col=0):
        """算一个 A1 风格的公式，返回 (值, 错误信息)"""
        sh = sheet or self.new_book().act
        try:
            ast = K.parse(expr)
            return sh._eval_ast(ast, row, col, 0), None
        except Exception as e:
            return None, str(e)

    def funcs(self):
        return K.all_names()


tbl = _Facade()
