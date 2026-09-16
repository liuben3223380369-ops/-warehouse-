# -*- coding: utf-8 -*-
"""库存模块"""

from ..core.router import Router
bp = Router('inv')

# -*- coding: utf-8 -*-
"""库存模块：物料档案、实时库存、台账、批量导入"""
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

# ---------- 物料台账（库存历史查询） ----------
@bp.route('/material/<int:mid>/history')
def material_history(mid):
    hit = db.q("SELECT * FROM v_mats WHERE id=?", mid)
    if not hit:
        return render_template('error.html', code=404, title='找不到这个物料',
                               detail='它可能已被删除，或链接过期了。'), 404
    row = hit[0]
    m = request.args.get('m') or ''
    rows = db.history(mid, m)
    months = [r['ym'] for r in db.q(
        "SELECT DISTINCT substr(tdate,1,7) ym FROM txns WHERE material_id=? ORDER BY ym DESC", mid)]
    # 金额合计（只统计填了单价的单据）
    amt_in = sum(float(r['amount'] or 0) for r in rows if r['kind'] == '进')
    amt_out = sum(float(r['amount'] or 0) for r in rows if r['kind'] == '出')
    return render_template('history.html', row=row, rows=rows, m=m, months=months,
                           amt_in=amt_in, amt_out=amt_out,
                           total_amount=amt_in - amt_out)


# ---------- 物料档案 ----------
def _period_stats(m=None, d=None):
    """按期间统计每个物料的「期初 / 进 / 出 / 期末」。

    d=YYYY-MM-DD  → 当天；m=YYYY-MM → 整月；都为空 → 全部时间。
    期初不是档案里的 opening，而是「截至该期间开始时的实际结存」：
      opening + 期间之前的进 - 期间之前的出
    这样选 9 月看到的期初是 8 月底的数，跟月报的口径一致，不会出现
    "每个月的期初都等于档案期初"这种对不上账的情况。
    """
    if d:
        lo, hi = d, d
    elif m:
        lo, hi = m + '-01', m + '-31'   # 2 月用 -31 也安全：字符串比较不会漏
    else:
        lo, hi = '0000-01-01', '9999-12-31'
    out = {}
    for r in db.q("SELECT material_id mid,"
                  " SUM(CASE WHEN kind='进' AND tdate<? THEN qty ELSE 0 END) pi,"
                  " SUM(CASE WHEN kind='出' AND tdate<? THEN qty ELSE 0 END) po,"
                  " SUM(CASE WHEN kind='进' AND tdate BETWEEN ? AND ? THEN qty ELSE 0 END) ci,"
                  " SUM(CASE WHEN kind='出' AND tdate BETWEEN ? AND ? THEN qty ELSE 0 END) co"
                  " FROM txns GROUP BY material_id", lo, lo, lo, hi, lo, hi):
        out[r['mid']] = r
    return out


@bp.route('/materials')
def materials():
    """物料档案。

    口径：默认看**当月**的进出存；选定日期后看**当天**的进出存；选"全部"回到累计。
    以前这页只显示档案期初和实时库存，看不出"这个月到底走了多少"，
    想看当月情况得跑去月报页。
    """
    kw = clean_kw(request.args.get('kw'))
    show_all = request.args.get('all') != '0'
    # d 优先于 m：选了具体日期就按当天算
    d = (request.args.get('d') or '').strip()
    if d and not is_date(d):
        d = ''
    scope = request.args.get('scope') or ''      # all = 全部时间
    m = '' if d else (safe_ym(request.args.get('m')) if request.args.get('m') else ym())
    if scope == 'all':
        m = d = ''

    w, a = [], []
    if kw:
        w.append("(name LIKE ? OR code LIKE ? OR supplier LIKE ? OR category LIKE ? OR spec LIKE ?)")
        a += ['%%%s%%' % kw] * 5
    if not show_all:
        w.append("active=1")
    inline = request.args.get('edit') == '1'
    sql = "SELECT * FROM v_mats" + (" WHERE " + " AND ".join(w) if w else "")
    sql += " ORDER BY active DESC, category, name"
    rows = [dict(r) for r in db.q(sql, *a)]

    # 挂上期间数据：期初/进/出/期末
    ps = _period_stats(m or None, d or None)
    sum_in = sum_out = 0.0
    for r in rows:
        p = ps.get(r['id'])
        pi = float(p['pi'] or 0) if p else 0.0
        po = float(p['po'] or 0) if p else 0.0
        ci = float(p['ci'] or 0) if p else 0.0
        co = float(p['co'] or 0) if p else 0.0
        r['p_open'] = round(float(r['opening'] or 0) + pi - po, 6)
        r['p_in'] = round(ci, 6)
        r['p_out'] = round(co, 6)
        r['p_end'] = round(r['p_open'] + ci - co, 6)
        r['moved'] = bool(ci or co)
        sum_in += ci; sum_out += co

    sort = request.args.get('sort') or ''
    dir_ = request.args.get('dir') or ''
    key_map = {'name': 'name', 'code': 'code', 'category': 'category', 'spec': 'spec',
               'width': 'width', 'supplier': 'supplier',
               'opening': 'p_open', 'stock': 'p_end',
               'in_qty': 'p_in', 'out_qty': 'p_out'}
    if sort in key_map and dir_:
        rev = (dir_ != 'asc')
        rows.sort(key=lambda x: (x[key_map[sort]] is None,
                                 x[key_map[sort]] if not isinstance(x[key_map[sort]], str)
                                 else x[key_map[sort]]), reverse=rev)
    n_off = db.q("SELECT COUNT(*) c FROM materials WHERE active=0")[0]['c']
    n_all = db.q("SELECT COUNT(*) c FROM materials")[0]['c']
    if d:
        scope_cn, scope_tip = '当天 %s' % d, '只统计这一天的进出'
    elif m:
        scope_cn, scope_tip = '%s 当月' % m, '期初为上月月末，期末为当月月末'
    else:
        scope_cn, scope_tip = '全部时间', '累计进出与当前库存'
    # 批量修改的列选项跟随模板列名（用户改过名就显示新名字，不再是写死的旧名）
    _tid = db.default_tpl_id()
    _bcols = []
    for _c in db.cols(_tid):
        _f = _c['fid']
        if _f in ('name', 'code', 'category', 'spec', 'width', 'supplier',
                  'unit', 'status', 'opening', 'safety'):
            _u = _c['unit'] if 'unit' in _c.keys() else ''
            _bcols.append({'fid': _f,
                           'label': (_c['label'] or '') + (('（%s）' % _u) if _u else '')})
    return render_template('materials.html', rows=rows, kw=kw, inline=inline,
                           bcols=_bcols,
                           show_all=show_all, n_off=n_off, n_all=n_all,
                           cur_sort=sort, cur_dir=dir_, m=m, d=d, scope=scope,
                           scope_cn=scope_cn, scope_tip=scope_tip,
                           sum_in=round(sum_in, 6), sum_out=round(sum_out, 6),
                           qs={'kw': kw, 'all': '1' if show_all else '',
                               'edit': '1' if inline else '', 'm': m, 'd': d},
                           msg=request.args.get('msg',''))

@bp.route('/material/edit/<int:mid>', methods=['GET','POST'])
@bp.route('/material/new', methods=['GET','POST'])
def material_edit(mid=None):
    row = one("SELECT * FROM materials WHERE id=?", mid) if mid else None
    if mid and not row:
        return render_template('error.html', code=404, title='找不到这个物料',
                               detail='它可能已被删除。'), 404
    if request.method == 'POST':
        f = request.form
        name = (f.get('name') or '').strip() or (f.get('code') or '').strip()
        if not name:                       # 名称和料号都空 -> 不给存，否则列表里出现空白行
            return render_template('material_edit.html', row=row,
                                   err='物料名称和料号至少要填一个')
        data = (f.get('supplier',''), f.get('category',''), f.get('spec',''), f.get('width',''),
                name, f.get('code',''), (f.get('unit','') or '').strip(),
                num(f.get('opening')), num(f.get('safety')), (f.get('status','') or '').strip())
        if mid:
            db.run("UPDATE materials SET supplier=?,category=?,spec=?,width=?,name=?,code=?,"
                   "unit=?,opening=?,safety=?,status=? WHERE id=?", (*data, mid))
        else:
            db.run("INSERT INTO materials(supplier,category,spec,width,name,code,unit,opening,safety,status)"
                   " VALUES(?,?,?,?,?,?,?,?,?,?)", *data)
        return redirect(url_for('materials'))
    return render_template('material_edit.html', row=row)

@bp.route('/material/del/<int:mid>')
def material_del(mid):
    # 采购明细也引用物料：只看出入库单据的话，
    # "没单据但被采购单引用"的物料会走真删分支，被外键拦下直接 500。
    n = scalar("SELECT COUNT(*) FROM txns WHERE material_id=?", mid)
    p = scalar("SELECT COUNT(*) FROM po_items WHERE material_id=?", mid)
    if n or p:
        db.run("UPDATE materials SET active=0 WHERE id=?", mid)
        return redirect(url_for('materials',
            msg='已改为停用（还被 %d 条单据、%d 条采购明细引用，不能真删）' % (n, p)))
    db.run("DELETE FROM materials WHERE id=?", mid)
    return redirect(url_for('materials', msg='已删除'))

# ---------- 批量修改 / 删除 ----------
@bp.route('/materials/batch', methods=['POST'])
def batch():
    ids = ints(request.form, 'id')
    act = request.form.get('act')
    msg = ''
    if not take_nonce(request.form.get('_n')):
        return redirect(url_for('materials',
            msg='这次批量操作已经执行过了，请不要重复提交'))
    if not ids:
        msg = '未勾选任何物料'
    elif act == 'delete':
        d, s = db.batch_delete(ids)
        msg = f'已删除 {d} 项，停用 {s} 项（有历史单据的改为停用，数据可追溯）' if s else f'已删除 {d} 项'
    elif act in ('active', 'inactive'):
        n = db.batch_set_active(ids, 1 if act == 'active' else 0)
        msg = f'已启用 {n} 项' if act == 'active' else f'已停用 {n} 项'
    elif act.startswith('set:'):
        field = act[4:]
        if field not in db.SETABLE:
            msg = '不支持修改该字段'
            return redirect(url_for('materials', msg=msg))
        val = (request.form.get('value') or '').strip()
        mode = request.form.get('mode') or 'set'
        label = LABELS.get(field, field)
        if field in ('opening', 'safety'):
            if mode == 'add':              # 按增量调整，结果不为负
                delta = num(val)
                db.run(f"UPDATE materials SET {field}=MAX(0, {field}+?) WHERE id IN ({','.join('?'*len(ids))})",
                       delta, *ids)
                msg = f'已为 {len(ids)} 项{label}增减 {delta:+g}'
            else:
                n = db.batch_update(ids, field, val)
                msg = f'已把 {n} 项{label}设为 {val or 0}'
        else:
            if mode == 'replace':
                old = (request.form.get('old') or '').strip()
                n = db.batch_update(ids, field, val, mode='replace', old=old)
                msg = f'已在 {n} 项{label}中把「{old}」替换为「{val}」' if n else '未找到可替换的内容'
            else:
                n = db.batch_update(ids, field, val)
                msg = f'已把 {n} 项{label}改为「{val}」' if val else f'已清空 {n} 项{label}'
    return redirect(url_for('materials', msg=msg))

def _rows_by_ids(ids):
    ph = ','.join('?' * len(ids))
    return db.q(f"SELECT * FROM v_stock WHERE id IN ({ph})", *ids)

# ---------- 行内表格批量编辑（A-G 列全部可改） ----------
@bp.route('/materials/table', methods=['POST'])
def table_save():
    ids = ints(request.form, 'id')
    if not take_nonce(request.form.get('_n')):
        return redirect(url_for('materials',
            msg='这批修改已经保存过了，请不要重复提交'))
    changed = 0
    # 表单里的字段名带行 id 后缀（name_12、code_12），
    # 原来判断 "if f in request.form" 找的是不带后缀的键，永远为假，
    # 整段保存逻辑被跳过 —— 改完点保存，一格都没写进去。
    for i in ids:
        vals = {}
        for f in ('name', 'code', 'supplier', 'category', 'spec', 'width', 'unit', 'status'):
            key = f'{f}_{i}'
            if key in request.form:
                vals[f] = (request.form.get(key) or '').strip()
        for f in ('opening', 'safety'):
            key = f'{f}_{i}'
            if key in request.form:
                vals[f] = num(request.form.get(key))
        name = vals.get('name')
        if name is not None and not name:
            continue                      # 名称清空的行不处理，避免出现空白物料
        if not vals:
            continue
        sets = ','.join(f'{k}=?' for k in vals)
        db.run(f"UPDATE materials SET {sets} WHERE id=?", *vals.values(), i)
        changed += 1
    return redirect(url_for('materials', msg=f'已保存 {changed} 项物料的修改',
                            kw=request.args.get('kw', ''), all=request.args.get('all', '')))

# ---------- Excel / CSV 批量导入 ----------

@bp.route('/import', methods=['GET', 'POST'])
def imp():
    if request.method == 'POST':
        mode = request.form.get('mode', 'merge')
        if request.form.get('confirm') == '1':
            if not take_nonce(request.form.get('_n')):
                return redirect(url_for('materials',
                    msg='这批物料已经导入过了，请不要重复提交'))
            p = os.path.join(TMP, os.path.basename(request.form.get('f', '')))
            if not os.path.exists(p):
                return render_template('import.html', err='预览已过期，请重新选择文件')
            rows, _ = importer.parse_file(path=p, aliases=db.aliases_map())
            added = updated = skipped = 0
            # 整批放进事务：中途异常全回滚，不会只导入一半
            try:
              with db.tx():
                for d in rows:
                  exist = None
                  if d['code']:
                      exist = db.q("SELECT id FROM materials WHERE code=? AND code<>''", d['code'])
                  if not exist:
                      exist = db.q("SELECT id FROM materials WHERE name=?", d['name'])
                  if exist:
                      if mode == 'skip':
                          skipped += 1; continue
                      mid = exist[0]['id']
                      sets, vals = [], []
                      for f2 in ('supplier', 'category', 'spec', 'width', 'unit', 'status', 'code'):
                          if d[f2] and (mode == 'overwrite' or f2 == 'code'):
                              sets.append("%s=?" % f2); vals.append(d[f2])
                      if mode == 'overwrite' and d['name']:
                          sets.append("name=?"); vals.append(d['name'])
                      for f2 in ('opening', 'safety'):
                          if d[f2] or mode == 'overwrite':
                              sets.append("%s=?" % f2); vals.append(d[f2])
                      if sets:
                          vals.append(mid)
                          db.run("UPDATE materials SET " + ",".join(sets) + " WHERE id=?", *vals)
                      updated += 1
                  else:
                      db.run("INSERT INTO materials(supplier,category,spec,width,name,code,unit,opening,safety,status)"
                             " VALUES(?,?,?,?,?,?,?,?,?,?)",
                             d['supplier'], d['category'], d['spec'], d['width'], d['name'], d['code'],
                             (d['unit'] or '').strip(), d['opening'], d['safety'], (d['status'] or '').strip())
                      added += 1
            except Exception as e:
                try: os.remove(p)
                except OSError: pass
                return render_template('import.html',
                    err='导入失败，已全部回滚（数据未改动）：%s' % e)
            try: os.remove(p)
            except OSError: pass
            return redirect(url_for('materials',
                msg='导入完成：新增 %d 项，更新 %d 项，跳过 %d 项' % (added, updated, skipped)))

        f = request.files.get('file')
        if not f or not f.filename:
            return render_template('import.html', err='请选择文件')
        fn = safe_name(f.filename, 'x.xlsx').lower()
        if not fn.endswith(('.xlsx', '.xlsm', '.csv')):
            return render_template('import.html', err='只支持 .xlsx / .xlsm / .csv')
        f.seek(0, os.SEEK_END); size = f.tell(); f.seek(0)
        if size > MAX_UPLOAD:
            return render_template('import.html',
                                   err='文件太大（%.1f MB），上限 %d MB'
                                       % (size / 1048576.0, MAX_UPLOAD // 1048576))
        tmp = os.path.join(TMP, '%d_%s' % (int(time.time() * 1000), fn))
        try:
            f.save(tmp)
        except (ValueError, OSError):
            return render_template('import.html', err='文件名不合法，请改成普通中文/英数字再试')
        try:
            rows, fields = importer.parse_file(path=tmp, aliases=db.aliases_map())
        except Exception as e:
            return render_template('import.html', err='解析失败：%s' % e)
        if not rows:
            return render_template('import.html', err='没读到有效数据行（需含「物料名称」列）')
        return render_template('import.html', preview=rows[:50], total=len(rows),
                               fields=sorted(fields), mode=mode, f=os.path.basename(tmp))
    return render_template('import.html')

@bp.route('/import/tpl')
def import_tpl():
    import openpyxl
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = '物料导入'
    ws.append(['物料名称','料号','类型','规格','宽幅','供应商','单位','期初结存','安全库存','状态'])
    for r in db.q("SELECT name,code,category,spec,width,supplier,unit,opening,safety,status"
                  " FROM materials WHERE active=1 ORDER BY category,name LIMIT 3"):
        ws.append([r['name'], r['code'], r['category'], r['spec'], r['width'],
                   r['supplier'], r['unit'], r['opening'], r['safety'], r['status']])
    ws.append(['（示例）半对半黑化压延','CPDR131218KAB1','无胶压延','12/20','250','松杨电子','平米',100,30,'常用'])
    for i, w in enumerate([24,20,14,12,8,14,8,10,10,10], 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
    bio = io.BytesIO(); wb.save(bio)
    from urllib.parse import quote
    return Response(bio.getvalue(),
                    mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition': "attachment; filename=tpl.xlsx; filename*=UTF-8''%s.xlsx" % quote('物料导入模板')})


# ---------- 实时库存 ----------
@bp.route('/stock')
def stock():
    f = request.args.get('f', '')
    kw = clean_kw(request.args.get('kw'))
    sort = request.args.get('sort') or ''
    dir_ = request.args.get('dir') or ''
    tid = int_arg(request.args, 'tpl')          # 0 = 全部模板
    w, args = [], []
    if tid:
        w.append("COALESCE(tpl_id,0)=?")
        args.append(tid)
    if f == 'alert':
        w.append("stock<=safety")
    elif f == 'zero':
        w.append("stock<=0")
    elif f == 'dead':
        w.append("status='呆滞'")
    if kw:
        w.append("(name LIKE ? OR code LIKE ? OR supplier LIKE ? OR category LIKE ? OR spec LIKE ?)")
        args += ['%%%s%%' % kw] * 5
    sql = "SELECT * FROM v_stock" + (" WHERE " + " AND ".join(w) if w else "")
    allowed = {'name': 'name', 'stock': 'stock', 'opening': 'opening',
               'in_qty': 'in_qty', 'out_qty': 'out_qty', 'category': 'category', 'code': 'code'}
    sql += " ORDER BY " + (allowed.get(sort, '') + (' ASC' if dir_ == 'asc' else ' DESC')
                           if sort in allowed and dir_ else
                           (allowed.get(sort, 'category') + ' ASC' if sort in allowed else 'category, name'))
    if sort in allowed and dir_:
        sql += ", name"
    rows = db.q(sql, *args)
    return render_template('stock.html', rows=rows, f=f, kw=kw, cur_sort=sort, cur_dir=dir_,
                           qs={'f': f, 'kw': kw, 'tpl': tid or ''},
                           TPLS=db.tpls(), tid=tid)

