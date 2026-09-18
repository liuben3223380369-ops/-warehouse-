# -*- coding: utf-8 -*-
"""制表动作层 · 撤销栈

放在本层的原因：动作层负责"改表"，撤销就是"改表的反操作"，
两者天然成对 —— 快照记录改动前的内容，撤销时按快照回填。

这一组函数**只操作 sheet 对象和一个普通 dict 状态**，
不认识 HTTP、不认识数据库，因此可以和 kernel 一起独立复用。

上层（web 路由）持有 `st = {'undo': [], 'redo': []}` 并把它传进来即可。
"""
from ..kernel import style as ST

MAX_UNDO = 60


def snap(sheet, r1, c1, r2, c2):
    """记录改动前的快照，用于撤销。"""
    out = []
    for i in range(r1, r2 + 1):
        for j in range(c1, c2 + 1):
            cl = sheet.get(i, j)
            out.append([i, j,
                        cl.raw if cl else '',
                        cl.fmt if cl else '',
                        cl.style.to_dict() if (cl and cl.style) else None,
                        cl.note if cl else ''])
    return out


def restore(sheet, snap_):
    """按快照回填，撤销时调用。"""
    for i, j, raw, fmt, style, note in snap_:
        cl = sheet.cell(i, j, create=True)
        if cl is None:
            continue
        cl.raw = raw
        cl.fmt = fmt
        cl.note = note
        cl.style = ST.Style.from_dict(style) if style else None
        cl.ast = None
        cl.bad = ''
        sheet._classify(cl)
    sheet.invalidate()


def push(st, label, sheet_name, snap_):
    """把一次改动压进撤销栈。"""
    st['undo'].append({'label': label, 'sheet': sheet_name, 'snap': snap_})
    if len(st['undo']) > MAX_UNDO:
        st['undo'].pop(0)
    st['redo'] = []


def _undo_hooks():
    """给需要"先快照再压栈"的动作用的便捷取法：push, snap"""
    return push, snap


# 兼容旧名：本模块迁出前叫 _snap / _restore / _push
_snap = snap
_restore = restore
_push = push

__all__ = ['MAX_UNDO', 'snap', 'restore', 'push', '_undo_hooks',
           '_snap', '_restore', '_push']
