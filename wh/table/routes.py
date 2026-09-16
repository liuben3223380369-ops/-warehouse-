# -*- coding: utf-8 -*-
"""表格模块的路由层：模板制表、列映射、导出"""
from flask import request, redirect, url_for, Response, render_template
from datetime import datetime, date
import calendar, io, csv, os, time, sys, json
from urllib.parse import quote
from . import bp
from ..core import db, util
from ..core.util import *            # noqa: F401,F403
from ..core.util import (_log_err, _last_prices, js_mats_with_price)  # noqa: F401
from .. import importer
from ..table import tbl

@bp.route('/tpl/<int:tid>/col/add', methods=['POST'])
def tpl_col_add(tid):
    """加一个系统里没有的自定义列（只属于这个模板）"""
    if not take_nonce(request.form.get('_n')):
        return redirect(url_for('tpl_cols_set', tid=tid, msg=''))
    if not db.tpl(tid):
        return redirect(url_for('tpls'))
    label = (request.form.get('label') or '').strip()
    if not label:
        return redirect(url_for('tpl_cols_set', tid=tid, msg='列名不能为空'))
    xtype = (request.form.get('xtype') or 'text').strip()
    xform = (request.form.get('xform') or '').strip()
    # 公式可以先建后补：在改表头页随时填，没公式时该列显示为空，不报错
    for r in db.tpl_cols(tid, False):
        if r['label'] == label:
            return redirect(url_for('tpl_cols_set', tid=tid,
                                    msg='已经有叫「%s」的列了' % label))
    db.add_custom_col(tid, label, xtype,
                      (request.form.get('xopt') or '').strip(), xform)
    tip = {'text': '文本', 'number': '数字', 'date': '日期',
           'select': '单选', 'calc': '公式'}.get(xtype, xtype)
    msg = '已添加「%s」（%s列）' % (label, tip)
    if xtype == 'calc' and not xform:
        msg += '，记得在下面给它填公式（如 qty*price）'
    return redirect(url_for('tpl_cols_set', tid=tid, msg=msg))

@bp.route('/tpl/<int:tid>/col/del/<fid>')
def tpl_col_del(tid, fid):
    if db.del_custom_col(tid, fid):
        return redirect(url_for('tpl_cols_set', tid=tid, msg='自定义列已删除'))
    return redirect(url_for('tpl_cols_set', tid=tid, msg='只能删除自定义列'))

@bp.route('/tpl/from_head', methods=['POST'])
def tpl_from_head():
    """用刚上传那张表的表头一键建模板（列名完全相同的表以后自动归到这里）"""
    if not take_nonce(request.form.get('_n')):
        return redirect(url_for('tpls', msg='这个模板已经建过了，请不要重复提交'))
    name = (request.form.get('name') or '').strip()
    if not name:
        return redirect(url_for('tpls', msg='模板名字不能为空'))
    for t in db.tpls():
        if t['name'] == name:
            return redirect(url_for('tpls', msg='已经有叫「%s」的模板了' % name))
    raw = (request.form.get('hdr') or '')
    heads = [x for x in raw.split('\t') if x.strip()]
    if not heads:
        return redirect(url_for('tpls', msg='没拿到表头，请重新上传文件'))
    tid = db.tpl_from_headers(heads, name=name, keep=1)
    return redirect(url_for('tpl_cols_set', tid=tid,
                    msg='已按表头建好「%s」，共 %d 列；以后列名一样的表会自动归到这里'
                        % (name, len(heads))))

# ---------- 库存模板（一套列配置 = 一个模板） ----------
@bp.route('/tpls')
def tpls():
    """模板列表：每个模板显示自己的列、物料数、单据数"""
    rows = []
    for t in db.tpls():
        nm, nt = db.tpl_stat(t['id'])
        rows.append(dict(id=t['id'], name=t['name'], note=t['note'],
                         mats=nm, txns=nt,
                         cols=[c['label'] for c in db.tpl_cols(t['id'])]))
    return render_template('tpls.html', rows=rows, msg=request.args.get('msg', ''))

@bp.route('/tpl/add', methods=['POST'])
def tpl_add():
    if not take_nonce(request.form.get('_n')):
        return redirect(url_for('tpls', msg='这个模板已经建过了，请不要重复提交'))
    name = (request.form.get('name') or '').strip()
    if not name:
        return redirect(url_for('tpls', msg='模板名字不能为空'))
    for t in db.tpls():                     # 不许重名，否则选的时候分不清
        if t['name'] == name:
            return redirect(url_for('tpls', msg='已经有叫「%s」的模板了' % name))
    copy_from = int_arg(request.form, 'copy_from') or None
    tid = db.add_tpl(name, (request.form.get('note') or '').strip(), copy_from=copy_from)
    return redirect(url_for('tpls', msg='已新建模板「%s」，去「改表头」配置它的列' % name))

@bp.route('/tpl/rename/<int:tid>', methods=['POST'])
def tpl_rename(tid):
    if not take_nonce(request.form.get('_n')):
        return redirect(url_for('tpls', msg='已经改过了，请不要重复提交'))
    name = (request.form.get('name') or '').strip()
    if not name:
        return redirect(url_for('tpls', msg='模板名字不能为空'))
    db.rename_tpl(tid, name, (request.form.get('note') or '').strip())
    return redirect(url_for('tpls', msg='已改名为「%s」' % name))

@bp.route('/tpl/del/<int:tid>')
def tpl_del(tid):
    ok, msg = db.del_tpl(tid)
    return redirect(url_for('tpls', msg=msg))

@bp.route('/tpl/cols/<int:tid>', methods=['GET', 'POST'])
def tpl_cols_set(tid):
    """某个模板的列配置（改表头）"""
    t = db.tpl(tid)
    if not t:
        return redirect(url_for('tpls', msg='模板不存在'))
    if request.method == 'POST':
        if request.form.get('reset'):
            db.reset_tpl_cols(tid)
            return redirect(url_for('tpl_cols_set', tid=tid, msg='已恢复默认表头'))
        rows = []
        for r in db.q("SELECT fid,aliases FROM tpl_cols WHERE tpl_id=?", tid):
            fid = r['fid']
            label = (request.form.get(f'label_{fid}') or '').strip() or fid
            aliases = (request.form.get(f'al_{fid}') or '').strip()
            # 改名后必须把新名字并进别名，否则导入认不出使用者自己起的名字
            parts = [x.strip() for x in aliases.split(',') if x.strip()]
            if label and label not in parts:
                parts.insert(0, label)
            # 配了单位后，界面上表头显示成「长（米）」。使用者照着界面做 Excel 时
            # 表头就会写成「长（米）」，所以这个带单位的写法也必须能认 —— 否则
            # 这一列导入时匹配不上，单据建了但值静默丢失（不报错，最难查）。
            _u = (request.form.get(f'un_{fid}') or '').strip()
            if label and _u:
                for w in ('%s（%s）' % (label, _u), '%s(%s)' % (label, _u)):
                    if w not in parts:
                        parts.append(w)
            rows.append(dict(fid=fid, label=label,
                             pos=int(request.form.get(f'pos_{fid}') or 0),
                             enabled=1 if request.form.get(f'en_{fid}') else 0,
                             aliases=','.join(parts)))
        # 字段类型 / 选项 / 公式
        for r in rows:
            fid = r['fid']
            r['xtype'] = (request.form.get(f'xt_{fid}') or 'text').strip()
            r['xopt'] = (request.form.get(f'xo_{fid}') or '').strip()
            r['xform'] = (request.form.get(f'xf_{fid}') or '').strip()
            # 列级单位：留空表示这一列不带单位（表头就只显示列名）
            r['unit'] = (request.form.get(f'un_{fid}') or '').strip()[:20]
        db.save_tpl_cols(tid, rows)
        return redirect(url_for('tpl_cols_set', tid=tid, msg='表头映射已保存'))
    # 旧模板残留检测：只开 供应商/类型/状态/物料名称 之外的列，说明还是老配置
    _keep = {'supplier', 'category', 'status', 'name'}
    _extra = [r['label'] for r in db.tpl_cols(tid)
              if r['enabled'] and r['fid'] not in _keep]
    # 列名还是旧的（规格（米）/宽幅/单位（卷））也算残留
    _old = {'规格（米）', '宽幅', '单位（卷）', '料号'}
    for r in db.tpl_cols(tid):
        if r['enabled'] and r['label'] in _old and r['label'] not in _extra:
            _extra.append(r['label'])
    return render_template('columns.html', cols=db.tpl_cols(tid, False),
                           msg=request.args.get('msg', ''), CALC=db.CALC_COLS,
                           tpl=t, TID=tid, EXTRA=_extra, UNITS=db.unit_choices(),
                           SAMPLE=['物料名称', '料号', '类型', '长', '宽', '供应商', '单位'])

# ---------- 列（表头）映射设置 ----------
@bp.route('/columns', methods=['GET', 'POST'])
def columns():
    if request.method == 'POST':
        if request.form.get('reset'):
            db.reset_cols()
            return redirect(url_for('columns', msg='已恢复默认表头'))
        rows = []
        for r in db.q("SELECT fid,aliases FROM colmap"):
            fid = r['fid']
            label = (request.form.get(f'label_{fid}') or '').strip() or fid
            aliases = (request.form.get(f'al_{fid}') or '').strip()
            # 改名后必须把新名字并进别名：否则导入时按旧别名找列，
            # 使用者自己起的名字（比如把"规格"改成"长度"）认不出来，数据落错字段。
            parts = [x.strip() for x in aliases.split(',') if x.strip()]
            if label and label not in parts:
                parts.insert(0, label)
            _u = (request.form.get(f'un_{fid}') or '').strip()
            if label and _u:
                for w in ('%s（%s）' % (label, _u), '%s(%s)' % (label, _u)):
                    if w not in parts:
                        parts.append(w)
            rows.append(dict(fid=fid, label=label,
                             pos=int(request.form.get(f'pos_{fid}') or 0),
                             enabled=1 if request.form.get(f'en_{fid}') else 0,
                             aliases=','.join(parts)))
        db.save_cols(rows)
        return redirect(url_for('columns', msg='表头映射已保存'))
    cols = db.cols(False)
    return render_template('columns.html', cols=cols, msg=request.args.get('msg', ''),
                           CALC=db.CALC_COLS, UNITS=db.unit_choices(),
                           SAMPLE=['物料名称', '料号', '类型', '长', '宽', '供应商', '期初结存', '安全库存', '状态'])

