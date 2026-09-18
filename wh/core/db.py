# -*- coding: utf-8 -*-
"""数据库门面：把下面几个子模块聚合成一个 db 命名空间。

历史写法是 `from ..core import db` 然后 `db.q(...)`，为免改动几十个调用点，
这里保持全部公开符号不变，实际实现已按职责拆到同级子模块：
    paths / dbconn / schema / query / tpl / maintain / potpl
"""
import os, sqlite3, json, sys
from datetime import datetime
import threading

from .paths import *
from .dbconn import *
from .schema import *
from .query import *
from .tpl import *
from .maintain import *
from .potpl import *
