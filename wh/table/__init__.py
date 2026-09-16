# -*- coding: utf-8 -*-
"""表格模块 —— 制表能力的中枢

对外暴露一个门面对象 `tbl`，其它模块要用表格能力就调它，
不需要知道内部拆成了 model / formula / parse / ops / export / render。

    from ..table import tbl
    tbl.fingerprint(headers)        # 列名指纹
    tbl.evaluate('qty*price', {...})  # 公式求值
    tbl.preview(path)               # 导入预览
    tbl.xlsx_response(...)          # 导出

同时提供路由（模板制表 / 列映射 / 导出），由调度文件注册。
"""

from ..core.router import Router
bp = Router('table')

from . import model, formula, parse, ops, export, render, helpers  # noqa: E402
from . import sp_addr, sp_lexer, sp_parser, sp_funcs, sp_style    # noqa: E402
from . import sp_engine, sp_io                                     # noqa: E402

from .model import (ColumnDef, TableDef, fmt_value, fmt_money, fmt_qty,   # noqa
                    fmt_int, T_TEXT, T_NUM, T_DATE, T_SELECT, T_FORMULA,
                    FMT_QTY, FMT_MONEY, FMT_INT)
from .formula import evaluate, check, used_fields                          # noqa
from .parse import (read_table, detect_header, merge_head_layers,          # noqa
                    norm_head, split_unit, fingerprint, match_columns,
                    is_total_row, find_total_rows, preview)
from .ops import (sort_rows, filter_rows, paginate, window, aggregate,     # noqa
                  group_by, dedupe, add_total_row, next_dir)
from .export import (to_xlsx, to_csv, to_json, csv_safe,                   # noqa
                     xlsx_response, csv_response)
from .render import render, render_tabledef, input_grid                    # noqa
from .helpers import all_custom_cols, cell_val                            # noqa


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
            ast = sp_parser.parse(expr)
            return sh._eval_ast(ast, row, col, 0), None
        except Exception as e:
            return None, str(e)

    def funcs(self):
        return sp_funcs.all_names()


tbl = _Facade()

from . import routes   # noqa: E402  路由挂在 bp 上，必须在 bp 之后导入
from . import sp_routes                                            # noqa: E402

#: 电子表格（类 Excel 制表台）的路由，由调度文件单独注册
sp_bp = sp_routes.bp
