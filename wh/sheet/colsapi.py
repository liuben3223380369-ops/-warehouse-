# -*- coding: utf-8 -*-
"""「显示哪些列」的接口（v3.106）

采购 / 入库 / 出库三处填写页都要用到同一套「屏蔽列」功能，
逻辑在 sheet/cols.py，这里只负责挂到各自的蓝图上，避免三处各写一遍::

    from ..sheet import colsapi
    colsapi.register(bp, '/po/cols')

前端拿到的是该模块全部字段 + 各自的 hide 标记，勾选后整份提交回来。
"""

from flask import request, jsonify

from . import cols as CL


def register(bp, url, default_module='po', endpoint='cols_cfg'):
    """给蓝图挂上显示列配置的读 / 写接口。

    endpoint 必须各自不同 —— Router 用函数名当端点名（全局、不带蓝图前缀），
    采购 / 入库 / 出库三处用同一个实现，名字撞了后者会注册失败。
    """

    @bp.route(url, methods=['GET', 'POST'], endpoint=endpoint)
    def _cols_cfg():
        m = (request.values.get('m') or default_module).strip()
        if m not in CL.BR.FIELDS:
            return jsonify({'ok': False, 'err': '没有这个模块'}), 400
        if request.method == 'POST':
            fids = request.form.getlist('fid')
            n = CL.set_shown(m, fids)
            return jsonify({'ok': True, 'total': n,
                            'shown': CL.visible_fids(m)})
        shown = set(CL.visible_fids(m))
        return jsonify({
            'ok': True, 'module': m,
            'name': CL.BR.MODULE_NAME.get(m, m),
            'fields': [{'fid': f, 'label': l, 'hide': 0 if f in shown else 1}
                       for f, l in CL.all_fields(m)],
        })

    return _cols_cfg
