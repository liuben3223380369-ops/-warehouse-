# -*- coding: utf-8 -*-
"""填写模块的「显示哪些列」（v3.106）

采购 / 入库 / 出库的表单只做两件事：**填值** 和 **把值写进表格的哪一列**。
所以表单上的字段和可以映射的字段必须是同一套 —— 这里就以

    sheet/bridge.py 的 FIELDS

为唯一来源。在这个前提下，「屏蔽一列」只需要表达一次：

    屏蔽 → 表单上不出现 → 映射卡片里也不出现 → 不写进表格

反过来，只显示出来的字段才会被映射、才会落表。

为什么要能屏蔽：
    每个库房记的字段不一样。采购可能只关心「批次号 / 物料 / 平米 / 单价」，
    长宽卷料另有本账；出库的「领用人」有的库根本不用。
    与其把十几列全摆出来让人每单都跳过一遍，不如让它只显示自己要的那几列。

存储：ui_field(module, fid, hide, ord)。没配过 = 全部显示（默认全开，
不会因为升级就让人发现自己常用的列不见了）。
"""

import datetime

from ..core import db
from . import bridge as BR


#: 没配过时默认显示哪些列（v3.106）
#: 全部摆出来会有十几列，手机上要横滑半天，多数库房根本用不上。
#: 默认只给「录一单必须填」的那几列，其余按需自己开。
DEFAULT_SHOWN = {
    'po':  ['batch', 'name', 'spec', 'width', 'rolls', 'qty', 'price'],
    'in':  ['batch', 'name', 'spec', 'width', 'rolls', 'qty', 'price'],
    'out': ['batch', 'name', 'spec', 'width', 'rolls', 'qty'],
}


def all_fields(module):
    """该模块全部字段 [(fid, 标签)]，顺序按 FIELDS 定义"""
    return [(f[0], f[1]) for f in BR.FIELDS.get(module, [])]


def _label(module, fid):
    for f in BR.FIELDS.get(module, []):
        if f[0] == fid:
            return f[1]
    return fid


def shown(module):
    """要显示的字段 [(fid, 标签)]，按 ord 排

    没配过 → 全部显示。配过 → 只显示 hide=0 的，按 ord 顺序；
    FIELDS 里新增了字段而库里没有记录的，同样按「默认显示」处理。
    """
    fs = all_fields(module)
    try:
        rows = db.q("SELECT fid, hide, ord FROM ui_field WHERE module=?",
                    (module,))
    except Exception:
        return fs
    if not rows:
        # 没配过：用默认集（不是全开 —— 十几列全摆出来反而没法用）
        d = DEFAULT_SHOWN.get(module)
        if not d:
            return fs
        out = []
        for i, (fid, lb) in enumerate(fs):
            if fid in d:
                out.append((i, 0, fid, lb))
        return [(fid, lb) for _, _, fid, lb in out]
    conf = {r['fid']: (r['hide'], r['ord']) for r in rows}
    out = []
    for i, (fid, lb) in enumerate(fs):
        hide, od = conf.get(fid, (0, i))
        if not hide:
            out.append((od, i, fid, lb))
    out.sort()
    return [(fid, lb) for _, _, fid, lb in out]


def hidden(module):
    s = {f for f, _ in shown(module)}
    return [(f, l) for f, l in all_fields(module) if f not in s]


def set_shown(module, fids):
    """保存：fids 里的是显示，其余全部屏蔽"""
    fs = all_fields(module)
    ok = set(fids or [])
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with db.tx() as c:
        for i, (fid, _lb) in enumerate(fs):
            c.execute("INSERT INTO ui_field(module,fid,hide,ord) VALUES(?,?,?,?) "
                      "ON CONFLICT(module,fid) DO UPDATE SET hide=?, ord=?",
                      (module, fid, 0 if fid in ok else 1, i,
                       0 if fid in ok else 1, i))
    return len(fs)


def visible_fids(module):
    return [f for f, _ in shown(module)]
