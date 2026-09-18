# -*- coding: utf-8 -*-
"""桌面外壳：单实例锁、端口、日志、原生窗口。

    shell  通用外壳（锁、端口、托盘提示、webview 窗口）
    qt     Qt WebEngine 窗口（可选后端，未安装时自动跳过）

门面只做聚合，run.py / dispatch.py 继续用 desktop.log(...) 等旧入口。
"""
from .shell import *                                   # noqa: F401,F403
from .shell import (_LOCK_NAME, _backends, _have_webview2,                 # noqa: F401
                    _lock_path, _pid_alive, _wait_up)
