# -*- coding: utf-8 -*-
"""制表动作层 —— 行列分组与进阶编辑动作

这一层建立在 kernel/ + engine/ 之上：

    kernel/   地址 · 词法 · 语法 · 函数表 · 样式（零业务耦合）
    engine/   数据模型（Sheet / Workbook）+ 求值
    io/       xlsx · csv 读写
    ops/      ★ 本层
        group.py    行分组与折叠/展开（折叠状态存 sheet.hidden_rows）
        undo.py     撤销栈：快照 / 回填 / 压栈（不依赖 HTTP 与数据库）
        clip.py     剪贴板 · 自动求和 · 隐藏行列
        fill.py     序列填充（等差 / 等比 / 日期）
        valid.py    数据有效性 · 删除重复项
        analyze.py  图表取数 · 透视表 · 区域统计 · 分类汇总
        fmt.py      按类型清除 · 行高列宽 · 细致边框
        extra.py    ★ 进阶动作登记表（上面对外只需要这一张表）

**共同点**：只操作 sheet 对象本身，不引入任何业务含义。
本层不认识「物料」「供应商」，只认识列和值 —— 因此可随 kernel 一起独立复用。

动作签名统一为 `(book, st, sh, g, rect) -> dict`，
由上层（web 路由）登记进动作表后即可通过接口调用。

对外只通过本文件导出，外部不要直接 import 具体子模块。
"""

from .group import group_by, build_groups, collapse
from .undo import MAX_UNDO, snap, restore, push
from .extra import EXTRA, check_validation
from .valid import check_validation as _cv  # noqa: F401

__all__ = [
    # 行分组与折叠
    'group_by', 'build_groups', 'collapse',
    # 撤销栈
    'MAX_UNDO', 'snap', 'restore', 'push',
    # 进阶动作
    'EXTRA', 'check_validation',
]
