# -*- coding: utf-8 -*-
"""数据库连接与查询原语：按线程复用连接、写锁排队、busy 重试、
表结构探测 _cols、以及 q/run/runmany 三个查询入口。"""
import os, sqlite3, json, sys
from datetime import datetime
from .paths import (app_dir, res_dir, BASE, DB_PATH, SEED, UNITS,
                   unit_choices, _is_frozen, _writable, _user_data_dir)
import threading

_local = threading.local()

# 进程内写锁：SQLite 同一时刻只允许一个写者。
# 多个线程（Web 并发请求）同时写时，在显式事务里 SQLite 不会等
# busy_timeout，而是立刻抛 "database is locked" —— 用户看到的就是"点保存突然 500"。
# 所以写操作先在进程内排队，从根上避免撞锁；跨进程（多开程序）再靠重试兜底。
_write_lock = threading.RLock()
_READ_PREFIX = ('select', 'pragma', 'explain', 'with')


def _is_write(sql):
    s = (sql or '').lstrip().lower()
    return bool(s) and not s.startswith(_READ_PREFIX)


def _busy_retry(fn, tries=60, sleep=0.05):
    """遇到 locked/busy 自动重试（跨进程场景，比如同时开了两个程序）"""
    import time as _t
    last = None
    for _ in range(tries):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            last = e
            msg = str(e).lower()
            if 'locked' in msg or 'busy' in msg:
                _t.sleep(sleep)
                continue
            raise
    raise last

def conn():
    """按线程复用连接：PRAGMA 只需设一次，事务也能跨调用保持。

    逐项降级：WAL 开不了就退回普通模式（某些文件系统/外置存储不支持 WAL），
    绝不因为一个可选优化让整个程序起不来。
    """
    c = getattr(_local, 'conn', None)
    if c is not None:
        return c
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)) or '.', exist_ok=True)
    try:
        c = sqlite3.connect(DB_PATH, timeout=15)
    except sqlite3.Error as e:
        raise RuntimeError(
            '打不开数据库文件：%s\n原因：%s\n'
            '建议：换一个有读写权限的目录（比如手机内部存储，而不是外置 SD 卡），'
            '或用 WAREHOUSE_DB 环境变量指定路径。' % (DB_PATH, e))
    c.row_factory = sqlite3.Row
    # WAL 在某些文件系统上会**静默损坏**数据（不抛异常，只是写坏）：
    # 网络共享盘、U 盘、exFAT、部分容器/overlay 文件系统。
    # 所以提供 WAREHOUSE_NO_WAL=1 逃生阀：数据放这类盘上时设它。
    _no_wal = os.environ.get('WAREHOUSE_NO_WAL', '').strip() in ('1', 'true', 'yes')
    _pragma_wal = ("PRAGMA journal_mode=DELETE" if _no_wal
                   else "PRAGMA journal_mode=WAL")
    for pragma in ("PRAGMA foreign_keys=ON",        # 外键真正生效
                   "PRAGMA busy_timeout=15000",     # 并发写不立刻报错
                   _pragma_wal):                    # 读写不互相阻塞（可选）
        try:
            c.execute(pragma)
        except sqlite3.DatabaseError:
            if 'journal_mode' in pragma:
                try:                                # 退回默认的 DELETE 模式
                    c.execute("PRAGMA journal_mode=DELETE")
                except sqlite3.DatabaseError:
                    pass
    # 校验 WAL 是否真生效：有些文件系统请求 WAL 不报错但实际没生效，
    # 此时同样退回 DELETE，避免"以为开了其实没开"的隐患。
    if not _no_wal:
        try:
            if str(c.execute("PRAGMA journal_mode").fetchone()[0]).lower() != 'wal':
                c.execute("PRAGMA journal_mode=DELETE")
        except sqlite3.DatabaseError:
            pass
    _local.conn = c
    return c

class tx:
    """真正的事务：批量写要么全成功要么全回滚。

    用法：
        with db.tx() as c:
            c.execute(...)
    嵌套时复用外层事务（用 SAVEPOINT）。
    """
    def __enter__(self):
        _write_lock.acquire()
        try:
            c = conn()
            self.depth = getattr(_local, 'depth', 0)
            if self.depth == 0:
                # IMMEDIATE 是关键：普通 BEGIN 属于"读事务起步"，
                # 中途要写才升级锁，这时若别人持有写锁，SQLite 不等
                # busy_timeout 而直接报 locked。IMMEDIATE 起步就取写锁，
                # 取不到就按 busy_timeout 等，于是能扛住真正的并发。
                _busy_retry(lambda: c.execute("BEGIN IMMEDIATE"))
            else:
                c.execute("SAVEPOINT sp%s" % self.depth)
            _local.depth = self.depth + 1
        except Exception:
            _write_lock.release()
            raise
        return c

    def __exit__(self, exc_type, exc, tb):
        c = conn()
        _local.depth = self.depth
        try:
            if exc_type is None:
                if self.depth == 0:
                    c.commit()
                else:
                    c.execute("RELEASE sp%s" % self.depth)
            else:
                if self.depth == 0:
                    c.rollback()
                else:
                    c.execute("ROLLBACK TO sp%s" % self.depth)
                    c.execute("RELEASE sp%s" % self.depth)
        except sqlite3.Error:
            pass
        finally:
            # 锁必须放：一次异常没放锁，后面所有写操作会永久卡死
            try:
                _write_lock.release()
            except RuntimeError:
                pass
        return False

def close():
    c = getattr(_local, 'conn', None)
    if c is not None:
        try:
            c.close()
        except sqlite3.Error:
            pass
        _local.conn = None


def _cols(table):
    return [r['name'] for r in q("PRAGMA table_info(%s)" % table)]


def q(sql, *a):
    return conn().execute(sql, _flat(a)).fetchall()

def _flat(a):
    """run(sql, v, *ids) 与 run(sql, (v,)+ids) 两种写法都能吃"""
    if len(a) == 1 and isinstance(a[0], (tuple, list)):
        return tuple(a[0])
    return a

def run(sql, *a):
    """执行写操作。不在 tx() 内则立即提交。

    注意：不能用 c.in_transaction 判断——sqlite3 执行 INSERT 会自动开隐式事务，
    那个标志恒为 True，会导致永远不提交、进程退出后数据全丢。

    并发：写 SQL 先拿进程内写锁，被别的进程锁住则自动重试。
    """
    if _is_write(sql):
        with _write_lock:
            return _busy_retry(lambda: _run_now(sql, a))
    return _run_now(sql, a)


def _run_now(sql, a):
    c = conn()
    cur = c.execute(sql, _flat(a))
    if getattr(_local, 'depth', 0) == 0:
        c.commit()
    return cur.lastrowid

def runmany(sql, seq):
    """批量执行。返回影响行数"""
    with _write_lock:
        return _busy_retry(lambda: _runmany_now(sql, seq))


def _runmany_now(sql, seq):
    c = conn()
    cur = c.executemany(sql, seq)
    if getattr(_local, 'depth', 0) == 0:
        c.commit()
    return cur.rowcount
