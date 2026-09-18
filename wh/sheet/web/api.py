# -*- coding: utf-8 -*-
"""电子表格接口：/sheet/api/<bid>/<act> 分发。

全部 a_* 动作按主题拆到同级四个模块，这里只做登记与分发：

    edit.py   读取网格 / 写入 / 填充 / 增删行列
    view.py   排序 / 筛选 / 查找 / 冻结 / 列宽 / 自适应
    fmt.py    样式 / 合并 / 分组 / 条件格式 / 批注
    book.py   工作表管理 / 名称 / 撤销重做 / 保存 / 公式预览
"""
from .common import *                                  # noqa: F401,F403
from .common import (_flush, _load, _rect, _sh)        # noqa: F401
from . import edit, view, fmt, book                    # noqa: F401

# ------------------------------------------------------------------ 各操作
_ACTIONS = {
    # 编辑
    'grid': edit.a_grid, 'set': edit.a_set, 'fill': edit.a_fill,
    'rows': edit.a_rows, 'cols': edit.a_cols,
    # 视图
    'sort': view.a_sort, 'filter': view.a_filter, 'find': view.a_find,
    'freeze': view.a_freeze, 'width': view.a_width,
    'autofit': view.a_autofit,
    # 格式
    'style': fmt.a_style, 'merge': fmt.a_merge, 'group': fmt.a_group,
    'cond': fmt.a_cond, 'note': fmt.a_note,
    # 工作簿
    'sheets': book.a_sheets, 'undo': book.a_undo, 'redo': book.a_redo,
    'names': book.a_names, 'save': book.a_save, 'eval': book.a_eval,
    # 进阶：剪贴板 / 自动求和 / 序列 / 隐藏 / 有效性 / 图表 / 透视 / 清除
    **EXTRA,
}


# ------------------------------------------------------------------ 接口
def _api(bid, act):
    book_, st = _load(bid)
    if not book_:
        return {'err': '工作簿不存在'}
    g = request.get_json(silent=True) or request.form or {}
    sname = g.get('sheet') or ''
    sh = _sh(book_, sname) or book_.act
    r1, c1, r2, c2 = _rect(g)
    fn = _ACTIONS.get(act)
    if not fn:
        return {'err': '没有这个操作：%s' % act}
    try:
        out = fn(book_, st, sh, g, (r1, c1, r2, c2))
    except Exception as e:
        return {'err': '%s' % e}
    if act not in ('grid', 'find'):
        _flush(bid, book_)
    out = out or {}
    out['ok'] = 1
    return out


@bp.route('/sheet/api/<int:bid>/<act>', methods=['GET', 'POST'])
def sheet_api(bid, act):
    return jsonify(_api(bid, act))


# ------------------------------------------------------------------ Univer 引擎
# 工业级表格内核（Apache-2.0）。资源在 static/univer，全部离线，不联网。
try:
    from . import univer as _UNI
    _UNI.register(bp)
except Exception as _e:          # 引擎挂了也要保证旧表格能用
    import sys
    print('[univer] 未启用：%s' % _e, file=sys.stderr)
