# -*- coding: utf-8 -*-
"""采购模板 Web 路由 —— 增删改、列定义、从单头另存、套用

依赖：仅 core.db
"""
from flask import request, redirect, url_for, render_template
from .. import bp
from ...core import db
from ...core.util import int_arg, take_nonce


# ---------- 采购模板 ----------
# 采购单要填的列跟仓库入库完全不同（客户订单号、交货方式、税率…），
# 所以单独一套模板，跟库存模板互不影响：改库存的列不会牵连采购单。
@bp.route('/po/tpls')
def po_tpls():
    rows = []
    for t in db.po_tpls():
        n, npo = db.po_tpl_stat(t['id'])
        rows.append(dict(id=t['id'], name=t['name'], note=t['note'],
                         items=n, pos_=npo,
                         cols=[c['label'] for c in db.po_tpl_cols(t['id'])]))
    return render_template('po_tpls.html', rows=rows,
                           msg=request.args.get('msg', ''),
                           err=request.args.get('err', ''),
                           units=db.unit_choices())


@bp.route('/po/tpl/add', methods=['POST'])
def po_tpl_add():
    if not take_nonce(request.form.get('_n')):
        return redirect(url_for('po_tpls', err='这个模板已经建过了，请不要重复提交'))
    name = (request.form.get('name') or '').strip()
    if not name:
        return redirect(url_for('po_tpls', err='模板名字不能为空'))
    for t in db.po_tpls():
        if t['name'] == name:
            return redirect(url_for('po_tpls', err='已经有叫「%s」的模板了' % name))
    copy_from = int_arg(request.form, 'copy_from') or None
    tid = db.add_po_tpl(name, (request.form.get('note') or '').strip(),
                        copy_from=copy_from)
    return redirect(url_for('po_tpls',
        msg='已新建「%s」，去「⚙ 配列」决定它有哪些列' % name))


@bp.route('/po/tpl/rename/<int:tid>', methods=['POST'])
def po_tpl_rename(tid):
    if not take_nonce(request.form.get('_n')):
        return redirect(url_for('po_tpls', err='已经改过了，请不要重复提交'))
    name = (request.form.get('name') or '').strip()
    if not name:
        return redirect(url_for('po_tpls', err='模板名字不能为空'))
    db.rename_po_tpl(tid, name, (request.form.get('note') or '').strip())
    return redirect(url_for('po_tpls', msg='已改名为「%s」' % name))


@bp.route('/po/tpl/del/<int:tid>')
def po_tpl_del(tid):
    ok, msg = db.del_po_tpl(tid)
    return redirect(url_for('po_tpls', msg=msg if ok else '', err='' if ok else msg))


@bp.route('/po/tpl/cols/<int:tid>', methods=['GET', 'POST'])
def po_tpl_cols_set(tid):
    """某个采购模板的列配置：改名 / 排序 / 开关 / 单位 / 别名 / 类型"""
    t = db.po_tpl(tid)
    if not t:
        return redirect(url_for('po_tpls', err='模板不存在'))
    if request.method == 'POST':
        if request.form.get('reset'):
            db.reset_po_tpl_cols(tid)
            return redirect(url_for('po_tpl_cols_set', tid=tid, msg='已恢复默认列'))
        rows = []
        for r in db.q("SELECT fid,aliases FROM po_tpl_cols WHERE tpl_id=?", tid):
            fid = r['fid']
            label = (request.form.get('label_%s' % fid) or '').strip() or fid
            aliases = (request.form.get('al_%s' % fid) or '').strip()
            parts = [x.strip() for x in aliases.split(',') if x.strip()]
            # 改名后必须把新名字并进别名：否则导入时认不出使用者自己起的名字，
            # 数据会落到别的字段去（v3.12 出过这个 bug）。
            if label and label not in parts:
                parts.insert(0, label)
            _u = (request.form.get('un_%s' % fid) or '').strip()
            if label and _u:
                # 界面表头显示成「长（米）」，使用者照着做 Excel 时也会这么写，
                # 所以带单位的写法必须也能认
                for w in ('%s（%s）' % (label, _u), '%s(%s)' % (label, _u)):
                    if w not in parts:
                        parts.append(w)
            rows.append(dict(fid=fid, label=label,
                             pos=int(request.form.get('pos_%s' % fid) or 0),
                             enabled=1 if request.form.get('en_%s' % fid) else 0,
                             aliases=','.join(parts),
                             xtype=(request.form.get('xt_%s' % fid) or 'text').strip(),
                             xopt=(request.form.get('xo_%s' % fid) or '').strip(),
                             xform=(request.form.get('xf_%s' % fid) or '').strip(),
                             unit=_u[:20]))
        db.save_po_tpl_cols(tid, rows)
        return redirect(url_for('po_tpl_cols_set', tid=tid, msg='列配置已保存'))
    return render_template('po_tpl_cols.html', t=t,
                           cols=db.q("SELECT * FROM po_tpl_cols WHERE tpl_id=? ORDER BY pos", tid),
                           n_item=db.po_tpl_stat(tid)[0],
                           msg=request.args.get('msg', ''), err=request.args.get('err', ''))


@bp.route('/po/tpl/col/add/<int:tid>', methods=['POST'])
def po_tpl_col_add(tid):
    """给采购模板加自定义列（系统列之外的任意列）"""
    if not take_nonce(request.form.get('_n')):
        return redirect(url_for('po_tpl_cols_set', tid=tid, err='加过了，请不要重复提交'))
    label = (request.form.get('label') or '').strip()
    if not label:
        return redirect(url_for('po_tpl_cols_set', tid=tid, err='列名不能为空'))
    xtype = (request.form.get('xtype') or 'text').strip()
    if xtype not in ('text', 'number', 'date', 'select', 'calc'):
        xtype = 'text'
    db.po_add_custom_col(tid, label, xtype,
                         (request.form.get('xopt') or '').strip(),
                         (request.form.get('xform') or '').strip())
    return redirect(url_for('po_tpl_cols_set', tid=tid, msg='已加列「%s」' % label))


@bp.route('/po/tpl/col/del/<int:tid>/<fid>')
def po_tpl_col_del(tid, fid):
    if db.po_del_custom_col(tid, fid):
        return redirect(url_for('po_tpl_cols_set', tid=tid, msg='列已删除（已存的值一并清掉）'))
    return redirect(url_for('po_tpl_cols_set', tid=tid, err='系统列不能删，只能关掉'))


@bp.route('/po/tpl/from_head', methods=['POST'])
def po_tpl_from_head():
    """拿一张真实采购表的表头建模板：每个列名成为模板的一列"""
    if not take_nonce(request.form.get('_n')):
        return redirect(url_for('po_tpls', err='建过了，请不要重复提交'))
    heads = [x.strip() for x in (request.form.get('heads') or '').replace('\n', ',').split(',')]
    heads = [h for h in heads if h]
    if not heads:
        return redirect(url_for('po_tpls', err='没读到表头'))
    name = (request.form.get('name') or '').strip() or '新采购表'
    tid = db.po_tpl_from_headers(heads, name)
    return redirect(url_for('po_tpl_cols_set', tid=tid,
        msg='已按表头建好「%s」，共 %d 列' % (name, len(heads))))


@bp.route('/po/tpl/use/<int:tid>')
def po_tpl_use(tid):
    """从模板直接开新采购单"""
    t = db.po_tpl(tid)
    if not t:
        return redirect(url_for('po_tpls', err='模板不存在'))
    return redirect(url_for('po_new', ptpl=tid))
