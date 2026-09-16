# -*- coding: utf-8 -*-
"""错误呈现层 —— 把异常翻译成一句人话，而不是空白页或 traceback。

原则：
  * 404 / 405 这类"常规状态"原样返回，不要伪装成 500
    （否则用户看到"出错了"却根本不知道真实原因）
  * 真正的异常：落盘 warehouse.log + 给一句可执行的提示
"""
import traceback

from flask import render_template, request
from werkzeug.exceptions import HTTPException

from . import util


def register(app):
    """由调度文件调用，把错误处理器挂到 app 上。"""

    @app.teardown_appcontext
    def _close_db(exc):
        try:
            from . import db
            db.close()
        except Exception:
            pass

    @app.errorhandler(404)
    def e404(e):
        return render_template('error.html', code=404,
                               msg='页面不存在',
                               detail=request.path), 404

    @app.errorhandler(500)
    def e500(e):
        util._log_err('500', traceback.format_exc())
        return render_template('error.html', code=500,
                               msg='出错了，本次操作没有生效',
                               detail='错误已写入 warehouse.log'), 500

    @app.errorhandler(Exception)
    def eall(e):
        # HTTPException 里 404/405/重定向这些都是正常流程，原样抛回去；
        # 只有真正的意外才落到 500 那一档。
        if isinstance(e, HTTPException):
            return e
        util._log_err('uncaught', traceback.format_exc())
        return render_template('error.html', code=500,
                               msg='出错了，本次操作没有生效',
                               detail='错误已写入 warehouse.log'), 500
