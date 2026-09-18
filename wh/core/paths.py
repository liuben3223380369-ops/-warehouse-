# -*- coding: utf-8 -*-
"""路径与环境：安装目录、数据目录、只读资源目录、单位字典。
不依赖数据库，可独立 import。"""
import os, sqlite3, json, sys
from datetime import datetime


def _is_frozen():
    return getattr(sys, 'frozen', False)

# 常用单位字典：采购、仓库、物料三处共用同一份，避免"采购写卷、仓库写平米"对不上。
# 是"建议"不是"约束"——任何单位框都能直接手输新单位，输过一次就自动进候选。
UNITS = ['卷', '平米', '米', '张', '个', '支', '条', '片', '套', '只', '块', '根',
         'kg', 'g', '吨', '箱', '包', '桶', '袋', '台', '件', '双', '把', '罐']


def unit_choices(extra=None):
    """返回候选单位：常用字典 + 系统里实际用过的（物料档案/历史采购），去重保序。"""
    from .dbconn import q        # 延迟导入：dbconn 依赖本模块，不能放文件头
    out = []
    seen = set()

    def add(u):
        u = (u or '').strip()
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    for u in UNITS:
        add(u)
    try:
        for r in q("SELECT DISTINCT unit FROM materials WHERE unit<>''"):
            add(r['unit'])
        for r in q("SELECT DISTINCT unit FROM po_items WHERE unit<>''"):
            add(r['unit'])
        for r in q("SELECT DISTINCT stock_unit FROM po_items WHERE stock_unit<>''"):
            add(r['stock_unit'])
        for r in q("SELECT DISTINCT unit FROM txns t JOIN materials m ON m.id=t.material_id"
                   " WHERE m.unit<>'' LIMIT 200"):
            add(r['unit'])
    except Exception:
        pass
    for u in (extra or []):
        add(u)
    return out


def _writable(d):
    """这个目录能写吗？装到 Program Files 时普通用户是没权限的，
    不检测的话数据库会建不出来，程序直接起不来。"""
    try:
        if not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        t = os.path.join(d, '.wtest.tmp')
        with open(t, 'w') as f:
            f.write('x')
        os.remove(t)
        return True
    except Exception:
        return False


def _user_data_dir():
    """程序目录不可写时的落点（安装到 Program Files 的标准做法）"""
    if os.name == 'nt':
        base = os.environ.get('APPDATA') or os.path.expanduser('~')
        return os.path.join(base, '仓库管理系统')
    return os.path.join(os.path.expanduser('~'), '.warehouse')


def app_dir():
    """程序目录：打包后是 exe 所在目录，源码运行时是项目根目录。
    数据库、备份、上传临时目录都放这里——它可写、且每次运行都固定。

    注意：源码运行时必须回到**项目根目录**（run.py 所在的那层），
    不能停在 wh/core，否则升级改目录结构后数据文件会跟着跑丢。

    打包后若 exe 所在目录不可写（装进 Program Files 的典型情况），
    自动改用用户目录（%APPDATA%\\仓库管理系统），避免数据库建不出来。
    """
    # Android / 自定义数据目录：环境变量优先，行为不变（未设置时走原逻辑）
    _home = os.environ.get('WAREHOUSE_HOME')
    if _home:
        try:
            os.makedirs(_home, exist_ok=True)
        except Exception:
            pass
        return _home
    if _is_frozen():
        d = os.path.dirname(os.path.abspath(sys.executable))
        if _writable(d):
            return d
        # 程序目录只读（Program Files）→ 数据放用户目录
        u = _user_data_dir()
        try:
            os.makedirs(u, exist_ok=True)
        except Exception:
            pass
        return u
    # wh/core/db.py -> core -> wh -> 项目根
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def res_dir():
    """资源目录：打包后是 PyInstaller 解包出来的临时目录（只读）。
    seed_materials.json 这类随程序分发的只读文件从这里取。"""
    if _is_frozen():
        return getattr(sys, '_MEIPASS', app_dir())
    return os.path.dirname(os.path.abspath(__file__))

BASE = app_dir()
DB_PATH = os.environ.get('WAREHOUSE_DB') or os.path.join(BASE, 'warehouse.db')
SEED = os.path.join(res_dir(), 'seed_materials.json')
