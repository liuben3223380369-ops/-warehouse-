# -*- coding: utf-8 -*-
"""出入库模块 —— 门面

拆成三个文件，各管一摊：
    form.py      单据录入（入库 / 出库 / 流水）
    importer.py  流水 Excel / WPS 导入 + 模板
    browse.py    流水列表、批量操作、删除

依赖方向单向：importer / browse → form，不存在回指。
这里只造路由表 bp 并把三个子模块 import 进来（@bp.route 加载即注册）。
"""
from ..core.router import Router
bp = Router('txn')

from . import form, importer, browse      # noqa: F401  注册路由
