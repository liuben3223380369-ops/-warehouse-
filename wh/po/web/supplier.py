# -*- coding: utf-8 -*-
"""供应商 Web 路由 —— 列表与手工维护（也会由采购单自动沉淀）

依赖：query
"""
from flask import request, redirect, url_for, render_template

from .. import bp
from ...core import db
from .. import query


@bp.route('/suppliers')
def suppliers():
    return render_template('suppliers.html', rows=query.supplier_list(),
                           msg=request.args.get('msg', ''))


@bp.route('/suppliers/add', methods=['POST'])
def supplier_add():
    nm = (request.form.get('name') or '').strip()
    if not nm:
        return redirect(url_for('suppliers', msg='供应商名称必填'))
    query.touch_supplier(nm)
    db.run("UPDATE suppliers SET contact=?, phone=?, note=? WHERE name=?",
           (request.form.get('contact') or '').strip(),
           (request.form.get('phone') or '').strip(),
           (request.form.get('note') or '').strip(), nm)
    return redirect(url_for('suppliers', msg='已保存 %s' % nm))
