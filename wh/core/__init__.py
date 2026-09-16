# -*- coding: utf-8 -*-
"""核心层 —— 所有模块共用的底座

    db       数据库（建表、迁移、查询、备份）
    util     工具（校验、日期、一次性令牌）
    errors   错误呈现
    router   路由收集器
    sysinfo  系统自检
"""
from . import db        # 必须最先：util 依赖 db.app_dir()
from . import util      # noqa: E402,F401
from . import errors    # noqa: E402,F401
