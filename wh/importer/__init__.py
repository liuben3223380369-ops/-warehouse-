# -*- coding: utf-8 -*-
"""Excel / CSV 导入：物料档案 + 流水 + 原表宽表。

分层（依赖方向单向，不存在回指）：
    base   读取原语、物料档案表头映射、parse_file
    wide   原 Excel「宽表」（物料一行 × 每日进/出两列）
    txn    流水导入（表头别名、按列映射、整文件解析）

门面只做聚合，调用方继续用 importer.parse_file(...) 等旧入口。
"""
from .base import *                                    # noqa: F401,F403
from .base import (_as_text, _cell, _fmt_date, _is_num_col, _norm, _num,   # noqa: F401
                   _pick_sheet, unit_from_label)
from .wide import *                                    # noqa: F401,F403
from .wide import (_col_by_head, _combo_cols, _combo_date_kind, _head_at,  # noqa: F401
                   _is_dt, _is_sum_col, _nm_sum_word, _strip_paren,
                   _wide_dates, _wide_kind_row)
from .txn import *                                     # noqa: F401,F403
from .txn import _read_grid                            # noqa: F401
