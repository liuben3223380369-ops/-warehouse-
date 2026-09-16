# -*- coding: utf-8 -*-
"""表格模块 · 批次号

批次号是 A 列，是整张表的行标识：入库、出库、采购对账都靠它串起来。
所以把它做成表格模块的内建能力——建表时 A 列默认就是批次号，并自动编号。

编号规则：PC + 年月日 + 当日流水（3 位）
    PC20260916-001、PC20260916-002 …

流水号存在 batch_seq 表里按天计数，重启、并发都不会重号。
"""

import datetime

from ..core import db

#: 批次号前缀（批次拼音首字母）
PREFIX = 'PC'

#: 建表时默认预填几行的批次号（表刚建时还没有数据，先备着，用多少算多少）
DEFAULT_ROWS = 30


def _ensure_table():
    db.run("CREATE TABLE IF NOT EXISTS batch_seq ("
           "  d TEXT PRIMARY KEY, seq INTEGER NOT NULL DEFAULT 0)")


def next_no(n=1, _day=None):
    """取 n 个新批次号，返回 list。

    用 UPDATE ... RETURNING 拿不到（旧 SQLite 不支持），
    这里靠事务 + 先读后写保证同一天内不重号。
    """
    if n <= 0:
        return []
    _ensure_table()
    d = _day or datetime.date.today().strftime('%Y%m%d')
    out = []
    with db.tx() as c:
        for _ in range(n):
            # 注意：日期是 8 位字符串，直接传会被当成 8 个绑定参数，必须包成元组
            r = c.execute("SELECT seq FROM batch_seq WHERE d=?", (d,)).fetchone()
            cur = r[0] if r else 0
            cur += 1
            c.execute("INSERT INTO batch_seq(d,seq) VALUES(?,?) "
                      "ON CONFLICT(d) DO UPDATE SET seq=?", (d, cur, cur))
            out.append('%s%s-%03d' % (PREFIX, d, cur))
    return out


def peek(n=1, _day=None):
    """看看接下来 n 个号长什么样（不占用号）。"""
    d = _day or datetime.date.today().strftime('%Y%m%d')
    _ensure_table()
    r = db.q("SELECT seq FROM batch_seq WHERE d=?", d)
    cur = r[0]['seq'] if r else 0
    return ['%s%s-%03d' % (PREFIX, d, cur + i + 1) for i in range(n)]


# ---------------------------------------------------------------- A 列

def label():
    """A 列的表头文字。"""
    return '批次号'


def fill(sheet, rows=DEFAULT_ROWS, with_header=True):
    """把 A 列填成批次号：A1 是表头，A2 往下是自动编号。

    sheet        电子表格的 Sheet 对象
    rows         预填几行编号
    with_header  第一行写不写「批次号」表头

    已经填过的行不覆盖（用户自己改过的号要保住）。
    """
    start = 1 if with_header else 0
    if with_header:
        if not (sheet.raw(0, 0) or '').strip():
            sheet.set_raw(0, 0, label())
    need = []
    for r in range(start, start + rows):
        if not (sheet.raw(r, 0) or '').strip():
            need.append(r)
    if not need:
        return 0
    nos = next_no(len(need))
    for r, no in zip(need, nos):
        sheet.set_raw(r, 0, no)
    return len(need)
