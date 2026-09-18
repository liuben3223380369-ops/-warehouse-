# -*- coding: utf-8 -*-
"""电子表格 Web 层的公共部分：蓝图、存取辅助、引擎偏好。

pages / io_rt / api 都从这里取 bp 与共享原语，避免各自重复导入。
"""
import os
import re
import json
import datetime
import urllib.parse

from flask import request, jsonify, render_template, redirect, url_for, Response

from ...core import db, util
from ...core.router import Router
from ...core.util import new_nonce
from ..engine import core as E
from .. import batch, ref
from ..ops import group as GR
from ..ops.undo import (MAX_UNDO, snap as _snap,
                              restore as _restore, push as _push)
from .. import io as IO
from ..kernel import style as ST
from ..kernel import funcs as F
from ..ops import EXTRA, check_validation

bp = Router('sheet')

# 打开中的工作簿：{id: {'book': Workbook, 'undo': [], 'redo': []}}
_LIVE = {}


# ------------------------------------------------------------------ 存取
def _load(bid):
    st = _LIVE.get(bid)
    if st:
        return st['book'], st
    row = db.q("SELECT * FROM wb WHERE id=?", bid)
    if not row:
        return None, None
    try:
        book = E.Workbook.from_dict(json.loads(row[0]['data'] or '{}'))
    except Exception:
        book = E.Workbook('工作簿')
        book.add('Sheet1')
    st = {'book': book, 'undo': [], 'redo': []}
    _LIVE[bid] = st
    return book, st


def _flush(bid, book):
    db.run("UPDATE wb SET data=?, updated=? WHERE id=?",
           json.dumps(book.to_dict(), ensure_ascii=False),
           datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), bid)


def _rect(args):
    g = args or {}
    def n(k, d=0):
        try:
            return int(g.get(k, d))
        except (TypeError, ValueError):
            return d
    r1, c1, r2, c2 = n('r1'), n('c1'), n('r2'), n('c2')
    return (min(r1, r2), min(c1, c2), max(r1, r2), max(c1, c2))


def _sh(book, name=None):
    return book.sheet(name) if name else book.act


# ------------------------------------------------------------------ 页面
def _uni_ok():
    """新引擎离线资源是否可用（精简包返回 False，列表页就不显示入口）"""
    try:
        from .univer import engine_available
        return engine_available()
    except Exception:
        return False


# ------------------------------------------------------- 引擎偏好（v3.77）
# 两个引擎都保留，只是决定「点工作簿名字默认进哪个」。
# 旧的那个不删：新引擎加载失败时它就是退路。
_ENGINE_DEFAULT = 'new'   # new=新引擎(Univer) / old=自带制表台


def _q(s):
    """中文提示塞进 URL 查询串"""
    return urllib.parse.quote(str(s))


def _cfg_table():
    try:
        db.run("CREATE TABLE IF NOT EXISTS cfg "
               "(k TEXT PRIMARY KEY, v TEXT)")
    except Exception:
        pass


def get_engine():
    """读取引擎偏好；没设过就返回默认。库不可用时也不崩。"""
    try:
        _cfg_table()
        rows = db.q("SELECT v FROM cfg WHERE k='sheet_engine'")
        if rows:
            v = (rows[0]['v'] or '').strip().lower()
            if v in ('new', 'old'):
                return v
    except Exception:
        pass
    return _ENGINE_DEFAULT


def set_engine(v):
    v = (v or '').strip().lower()
    if v not in ('new', 'old'):
        return False
    try:
        _cfg_table()
        db.run("INSERT INTO cfg(k,v) VALUES('sheet_engine',?) "
               "ON CONFLICT(k) DO UPDATE SET v=excluded.v", v)
        return True
    except Exception:
        return False


