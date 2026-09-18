# -*- coding: utf-8 -*-
"""调度文件 —— 把各个模块装配成一个能跑的应用

各模块（表格 / UI / 库存 / 出入库 / 采购 / 统计）只管自己那摊事，
彼此不互相 import 业务代码；谁先谁后、怎么拼在一起，由这里决定。

    create_app()  →  造 Flask 应用、初始化数据库、注册所有路由

好处：
  * 单个模块可以独立看、独立改，不用在三千行里翻
  * 换 UI、换存储、加新模块都是改这一个文件
"""
import os
import sys

from flask import Flask

from .core import db, util, errors, sysinfo
from .core.util import say, new_nonce, LABELS
from . import ui
from .table import bp as table_bp
from .sheet.web import bp as sp_bp
from .inv import bp as inv_bp
from .txn import bp as txn_bp
from .po import bp as po_bp
from .stat import bp as stat_bp
from .count import bp as count_bp

# 装配顺序：底层能力在前，业务模块在后
MODULES = [
    ('表格', table_bp),
    ('电子表格', sp_bp),
    ('库存', inv_bp),
    ('出入库', txn_bp),
    ('采购', po_bp),
    ('统计', stat_bp),
    ('盘点', count_bp),
    ('系统', sysinfo.bp),
]


def _template_folder():
    """模板目录。

    Android / 自定义部署可用环境变量 WAREHOUSE_TEMPLATES 覆盖。

    源码运行：项目根下的 templates（wh/ 的上一级）
    打包成 exe：PyInstaller 解包出来的临时目录
    用 Flask(__name__) 的默认行为会找成 wh/templates，所以对路径必须给全。
    """
    _env = os.environ.get('WAREHOUSE_TEMPLATES')
    if _env:
        return _env
    if getattr(sys, 'frozen', False):
        return os.path.join(db.res_dir(), 'templates')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, 'templates')


def _static_folder():
    """静态资源目录（和模板同样的道理，必须给绝对路径）。

    表格引擎的离线资源（static/univer，约 12MB）就放在这里，
    不指定的话 Flask 会去找 wh/static，结果全 404。

    Android / 自定义部署可用环境变量 WAREHOUSE_STATIC 覆盖。
    """
    _env = os.environ.get('WAREHOUSE_STATIC')
    if _env:
        return _env
    if getattr(sys, 'frozen', False):
        return os.path.join(db.res_dir(), 'static')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, 'static')


def create_app(init_db=True, verbose=True):
    """造一个装配好的 Flask 应用"""
    app = Flask(__name__, template_folder=_template_folder(),
                static_folder=_static_folder())

    # ---------- 1. 数据库（损坏要在这里就接住，否则窗口模式一闪而过） ----------
    if init_db:
        from . import desktop
        try:
            db.init()
        except Exception as e:
            _lines = ['数据库打不开：%s' % e]
            try:
                _lines += db.check_integrity()
            except Exception:
                pass
            _lines.append('也可以把 %s 改名（比如加 .old），程序会自动新建一个空库，'
                          '再用之前的备份还原。' % os.path.basename(db.DB_PATH))
            desktop.log('启动失败：\n  ' + '\n  '.join(_lines))
            for _m in ['', '  !! 启动失败 !!'] + ['  ' + x for x in _lines] + ['']:
                say(_m)
            if getattr(sys, 'frozen', False):
                import time
                time.sleep(30)
            raise SystemExit(1)

        # 老采购明细补指纹：升级前建的明细没有 sig，不补就配不上采购价
        try:
            from .po.amount import backfill_sigs
            _n = backfill_sigs()
            if _n and verbose:
                say('  已为 %d 条老采购明细补上指纹' % _n)
        except Exception:
            pass

    # ---------- 2. 错误呈现 ----------
    errors.register(app)

    # ---------- 3. UI 注入（所有模板都能用） ----------
    @app.context_processor
    def _ui_ctx():
        return ui.context()

    app.jinja_env.globals.update(LABELS=LABELS, new_nonce=new_nonce)

    # ---------- 4. 注册各模块路由 ----------
    total = 0
    for name, router in MODULES:
        n = router.register(app)
        total += n
        if verbose:
            say('  · %-4s 模块  %2d 条路由' % (name, n))
    if verbose:
        say('  合计 %d 条路由' % total)

    return app


def route_report():
    """列出各模块登记了哪些路由（自检用）"""
    out = []
    for name, router in MODULES:
        out.append('%s：%d 条' % (name, len(router.routes)))
        for rule, func, opt in router.routes:
            out.append('    %-28s %s' % (rule, func.__name__))
    return '\n'.join(out)
