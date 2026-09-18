# -*- coding: utf-8 -*-
"""制表 · Web 层

职责：把制表能力挂到 HTTP 上，以及 Univer 前端集成。

    common.py   蓝图、存取辅助、引擎偏好（其余三个文件共享）
    pages.py    页面路由：列表 / 新建 / 打开 / 重命名 / 删除 / 引擎切换
    io_rt.py    导入导出：xlsx 导入、导出 xlsx / csv
    api.py      接口 /sheet/api/<bid>/<act> 分发
    edit.py     编辑类动作：网格 / 写入 / 填充 / 增删行列
    view.py     视图类动作：排序 / 筛选 / 查找 / 冻结 / 列宽
    fmt.py      格式类动作：样式 / 合并 / 分组 / 条件格式 / 批注
    book.py     工作簿级动作：工作表 / 名称 / 撤销重做 / 公式预览
    univer.py   Univer 引擎：资源检测、快照与工作簿互转、/univer 路由

这是分层的最上层，只依赖 kernel / engine / io / ops，不反向依赖业务模块。
"""

from . import (common, pages, io_rt, api, univer,          # noqa: F401
               edit, view, fmt, book)

__all__ = ['common', 'pages', 'io_rt', 'api', 'univer',
           'edit', 'view', 'fmt', 'book']

#: 电子表格路由，由调度文件注册
bp = common.bp
