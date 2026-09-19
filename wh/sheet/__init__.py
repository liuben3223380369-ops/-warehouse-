# -*- coding: utf-8 -*-
"""制表（电子表格）—— 本项目的核心模块

分层约定（越往上越靠近业务，越往下越纯粹）：

    kernel/   词法 · 语法 · 地址 · 函数表 · 样式   ← 零业务耦合，可独立运行
    engine/   求值（可插拔后端：自研 / IronCalc）
    io/       xlsx · csv 读写
    ops/      分组 · 撤销 · 进阶动作
    web/      路由 · Univer 前端集成
    batch.py  批次号（A 列自动编号）

依赖方向严格自下而上：web → ops → io → engine → kernel，
下层不认识上层。kernel 零业务耦合，可整块独立运行。
"""

from . import kernel, engine, io, ops          # noqa: E402
from . import batch                             # noqa: E402
from . import web                               # noqa: E402

__all__ = ['kernel', 'engine', 'io', 'ops', 'batch', 'web']
