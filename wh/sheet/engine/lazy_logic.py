# -*- coding: utf-8 -*-
"""惰性函数·逻辑分支：IF / IFERROR / IFNA / IFS / SWITCH

惰性 = 拿到的是 AST 节点而不是值，可短路不求值。"""
from ..kernel import to_bool, is_err
from .ctx import _same_val

def _lazy_if(sheet, r, c, args, depth):
    cond = sheet._eval_ast(args[0], r, c, depth) if args else False
    if is_err(cond):
        return cond
    b = to_bool(cond)
    if b is None:
        return '#VALUE!'
    if b:
        if len(args) < 2:
            return True
        return sheet._eval_ast(args[1], r, c, depth) \
            if isinstance(args[1], tuple) else args[1]
    if len(args) < 3:
        return False
    return sheet._eval_ast(args[2], r, c, depth) \
        if isinstance(args[2], tuple) else args[2]


def _lazy_iferror(sheet, r, c, args, depth):
    v = sheet._eval_ast(args[0], r, c, depth) if args else None
    if is_err(v):
        return sheet._eval_ast(args[1], r, c, depth) if len(args) > 1 else ''
    return v


def _lazy_ifna(sheet, r, c, args, depth):
    v = sheet._eval_ast(args[0], r, c, depth) if args else None
    if v == '#N/A':
        return sheet._eval_ast(args[1], r, c, depth) if len(args) > 1 else ''
    return v


def _lazy_ifs(sheet, r, c, args, depth):
    for i in range(0, len(args) - 1, 2):
        cv = sheet._eval_ast(args[i], r, c, depth)
        if is_err(cv):
            return cv
        if to_bool(cv):
            return sheet._eval_ast(args[i + 1], r, c, depth)
    return '#N/A'


def _lazy_switch(sheet, r, c, args, depth):
    if not args:
        return '#N/A'
    key = sheet._eval_ast(args[0], r, c, depth)
    for i in range(1, len(args) - 1, 2):
        if _same_val(sheet._eval_ast(args[i], r, c, depth), key):
            return sheet._eval_ast(args[i + 1], r, c, depth)
    if len(args) % 2 == 0:
        return sheet._eval_ast(args[-1], r, c, depth)
    return '#N/A'
