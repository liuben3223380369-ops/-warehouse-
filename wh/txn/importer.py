# -*- coding: utf-8 -*-
"""出入库模块（2/3）：流水 Excel / WPS 导入

识别表头、匹配物料、写入单据，以及导入模板下载。
"""
from flask import request, redirect, url_for, Response, render_template
from datetime import datetime, date
import calendar, io, csv, os, time, sys, json
from urllib.parse import quote
from . import bp
from ..core import db, util
from ..core.util import *            # noqa: F401,F403, int_arg
from ..core.util import (_log_err, _last_prices, js_mats_with_price)  # noqa: F401
from .. import importer
from ..table import tbl
# 流水页 / 出入库列表要显示自定义列，这两个函数在表格模块里。
# 拆分时漏了这行，导致 /txns 直接 500 —— 名字带下划线是避免和 util 里的重名。
from ..table.helpers import all_custom_cols as _all_custom_cols, cell_val as _cell_val
from ..po.amount import price_map, txn_unit_price       # noqa: F401
from ..po.receive import sync_txn_to_po                 # noqa: F401
from .form import _resolve_material      # 导入时复用录入层的物料解析


# ---------- 流水（出入库单据）Excel / WPS 导入 ----------
@bp.route('/txn/import', methods=['GET', 'POST'])
@bp.route('/in/import', methods=['GET', 'POST'], endpoint='in_import')
@bp.route('/out/import', methods=['GET', 'POST'], endpoint='out_import')
def txn_import():
    ep = request.endpoint
    fixed = '出' if ep == 'out_import' else ('进' if ep == 'in_import' else None)
    back = 'out' if fixed == '出' else ('in' if fixed == '进' else 'txn')
    # 导入也能选模板：导进来的物料/单据归到选中的模板
    tid = int_arg(request.values, 'tpl') or db.default_tpl_id()
    if not db.tpl(tid):
        tid = db.default_tpl_id()

    if request.method == 'POST':
        mode = request.form.get('mode', 'merge')
        if request.form.get('confirm') == '1':
            if not take_nonce(request.form.get('_n')):
                return redirect(url_for(back,
                    msg='这批单据已经导入过了，请不要重复提交'))
            p = os.path.join(TMP, os.path.basename(request.form.get('f', '')))
            if not os.path.exists(p):
                return render_template('txn_import.html', fixed=fixed, TPLS=db.tpls(), tid=tid, ep=ep,
                                       err='预览已过期，请重新选择文件')
            # v3.53：导入的「进」是否计入采购到货。
            # 退料/调拨/盘盈同样带批次号，照记会把供应商到货数越滚越高，
            # 采购单提前结单、财务按虚高的到货数付全款。默认「否」——
            # 导入来源复杂（一张 Excel 常混着入库和退料），宁可不记也不能错记；
            # 确属供应商到货的，导入时显式选「是」即可。
            _imp_to_po = (request.form.get('to_po') or '0') == '1'
            if request.form.get('manual') == '1':
                colmap = {}
                for k in request.form.keys():
                    if k.startswith('cm_'):
                        v = request.form.get(k)
                        if v:
                            colmap[k[3:]] = v
                try:
                    start = int_arg(request.form, 'startrow', default=1, lo=1)
                except ValueError:
                    start = 1
                rows = importer.parse_txn_by_map(
                    path=p, colmap=colmap, start=start, default_kind=fixed,
                    defdate=(request.form.get('defdate') or '').strip() or today())
            else:
                rows, _, _, _ = importer.parse_txn_file(
                    path=p, default_kind=fixed or (request.form.get('defkind') or None),
                    defmonth=(request.form.get('defdate') or '')[:7],
                    mat_aliases=db.aliases_map(tid), xcols=db.tpl_custom_cols(tid))
            dflt_date = (request.form.get('defdate') or '').strip() or today()
            allow_over = request.form.get('allow_over') == '1'
            fields = set((request.form.get('fields') or '').split(','))
            has_split = 'in_qty' in fields or 'out_qty' in fields
            # 宽表（物料×每日进出）：方向由表里的"进/出"行决定，不能跟着页面走
            is_wide = request.form.get('wide') == '1'
            # 表里有 进/出 分列时以表格方向为准；入库页只留"进"、出库页只留"出"
            filter_kind = None
            if not is_wide:
                if fixed == '进' and 'in_qty' in fields:
                    filter_kind = '进'
                elif fixed == '出' and 'out_qty' in fields:
                    filter_kind = '出'
            saved = newmat = blocked = skipped = archived = new_miss = 0
            po_tips = set()   # v3.35 导入里同步到采购流水的回执（去重）
            n_in = n_out = 0
            msgs = []
            # 按 日期→进先出后 排序后再写入。不排的话，同一物料"先出后进"
            # 的历史行会在写入当时因库存为 0 被当成超库存拦掉，
            # 导入整月流水时莫名其妙少几笔。
            def _ord(d):
                return (str(d.get('date') or '9999-99-99'),
                        0 if d.get('kind') == '进' else 1)
            try:
                rows = sorted(rows, key=_ord)
            except Exception:
                pass
            # 整批导入放进一个事务：中途任何异常全回滚，绝不留下"导了一半"的数据
            try:
              with db.tx():
                for d in rows:
                  vals = {k: d.get(k, '') for k in
                          ('name', 'code', 'supplier', 'category', 'spec', 'width', 'unit', 'status')}
                  vals['opening'] = d.get('opening', 0)
                  vals['safety'] = d.get('safety', 0)
                  mid, is_new = _resolve_material(vals, tpl_id=tid)
                  if not mid:
                      continue
                  newmat += is_new
                  _fids = [x['fid'] for x in db.tpl_cols(tid)]
                  if is_new and not (vals.get('code') or '').strip() \
                          and not (vals.get('category') or '').strip():
                      new_miss += 1
                  if not d.get('kind'):          # 宽表里没进出的行：只建档/更新期初
                      if mode == 'overwrite' or is_new:
                          db.run("UPDATE materials SET opening=? WHERE id=?",
                                 float(d.get('opening') or 0), mid)
                      archived += 1
                      continue
                  if is_wide or has_split:      # 表格自带方向，以表格为准
                      kind = d.get('kind') or '进'
                  else:
                      kind = fixed or d.get('kind') or '进'
                  qty = float(d.get('qty') or 0)
                  if filter_kind and kind != filter_kind:
                      skipped += 1
                      continue
                  if kind == '出' and not allow_over:
                      stock = scalar("SELECT stock FROM v_stock WHERE id=?", mid, default=0)
                      if qty > stock + 1e-9:
                          blocked += 1
                          msgs.append('%s(可用%g)' % (d.get('name'), stock))
                          continue
                  qty, pc, per = calc_qty(qty, d.get('pieces'), d.get('per_piece'))
                  if qty <= 0:
                      continue
                  # 平米 / 卷料：导入也得起作用，否则 Excel 导进来的单子这两列
                  # 永远空着，跟手工录入的对不上（进有出没有、手工有导入没有）。
                  # 优先采信表里直接给的值；只给了长宽就按长宽算；都没有就回退
                  # 物料档案的长宽。
                  _sq = num(d.get('sqm')) or None
                  _rl = num(d.get('rolls')) or None
                  # 先初始化，别等到下面的 if 里才定义 —— INSERT 时要读它，
                  # 若那分支没进（表里直接给了平米/卷料，或没启用这两列），
                  # 就会 NameError，整批导入静默回滚。
                  _vv = dict(d); _vv['qty'] = qty
                  _qu = str(d.get('qty_unit') or '').strip()
                  if _qu not in db.QTY_UNITS:
                      _mr = db.q("SELECT qty_unit FROM materials WHERE id=?", mid)
                      _qu = ((_mr[0]['qty_unit'] or '').strip() if _mr else '') or '平米'
                  _vv['qty_unit'] = _qu
                  if (_sq is None or _rl is None) and ('sqm' in _fids or 'rolls' in _fids):
                      # v3.28：只给了平米就补算卷料，只给了卷料就补算平米
                      if _sq is not None: _vv['sqm'] = _sq
                      if _rl is not None: _vv['rolls'] = _rl
                      _vv['qty'] = qty
                      _vv['qty_unit'] = _qu
                      if not (str(_vv.get('spec') or '').strip()
                              and str(_vv.get('width') or '').strip()):
                          _mr = db.q("SELECT spec,width FROM materials WHERE id=?", mid)
                          if _mr:
                              _vv['spec'] = _mr[0]['spec']
                              _vv['width'] = _mr[0]['width']
                      _sq2, _rl2, _rest = db.calc_area(_vv)
                      # 只补表里没给的那个：表里写了的以表为准，
                      # 不能因为算不出来（比如表里没长宽）就把已有值清成 None
                      if _sq is None and _sq2 is not None: _sq = _sq2
                      if _rl is None and _rl2 is not None: _rl = _rl2
                      if _rest:
                          _nt = db.rest_note(_rest)
                          if _nt and _nt not in (d.get('note') or ''):
                              d['note'] = ((d.get('note') or '') + ' ' + _nt).strip()
                  db.run("INSERT INTO txns(tdate,material_id,kind,qty,pieces,per_piece,price,"
                         "note,created_at,tpl_id,sqm,rolls,qty_unit,batch) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         d.get('date') or dflt_date, mid, kind, qty, pc, per,
                         (num(d.get('price')) or None), d.get('note', ''),
                         datetime.now().strftime('%Y-%m-%d %H:%M:%S'), tid, _sq, _rl,
                         _vv.get('qty_unit') or '',
                         # 导入的批次：表里写了就带上，这样也能对上采购批次价
                         str(d.get('batch') or '').strip()[:40])
                  _new_id = scalar("SELECT last_insert_rowid()")
                  # v3.35 导入同样一分为二：库存 + 采购流水（按批次号桥接）
                  # v3.53 加开关：退料/调拨/盘盈默认不计入，避免虚增到货
                  if kind == '进' and _imp_to_po and str(d.get('batch') or '').strip():
                      _ok2, _pt2 = sync_txn_to_po(
                          str(d.get('batch') or '').strip(),
                          d.get('name') or '', mid, qty, d.get('price'),
                          d.get('date') or dflt_date, d.get('note', ''), _new_id,
                          # v3.42：导入同样要带口径，理由同上
                          qty_unit=(_vv.get('qty_unit') or d.get('qty_unit') or ''))
                      if _pt2:
                          po_tips.add(_pt2)
                  # Excel 里列名能对上自定义列的，一并存进 extra
                  _xv = {}
                  for _xc in db.tpl_custom_cols(tid):
                      if (_xc['xtype'] or 'text') == 'calc':
                          continue      # 公式列后端算，不认表格里的值
                      # 自定义列也可能配了单位，界面上显示成「膜厚（丝）」，
                      # 使用者照抄过来就是这个写法 —— 得认，否则这一列静默丢失。
                      _lb = _xc['label'] or ''
                      # sqlite3.Row 没有 .get()，只能用 keys() 判断（踩过一次：
                      # 写成 _xc.get('unit') 直接抛异常，整批导入回滚）
                      _u = ((_xc['unit'] if 'unit' in _xc.keys() else '') or '').strip()
                      _cands = [_xc['fid'], _lb]
                      if _lb and _u:
                          _cands += ['%s（%s）' % (_lb, _u), '%s(%s)' % (_lb, _u)]
                      _v = ''
                      for _c in _cands:
                          if d.get(_c) not in ('', None):
                              _v = d.get(_c); break
                      if _v in ('', None):
                          # 兜底：表头带括号（含手写空格变体）时去括号再比一次
                          import re as _re2
                          _want = _re2.sub(r'\(.*?\)', '', _lb).strip()
                          for _k, _val in d.items():
                              if _val in ('', None):
                                  continue
                              if _re2.sub(r'\(.*?\)', '', str(_k)).strip() == _want:
                                  _v = _val; break
                      if _v not in ('', None):
                          _xv[_xc['fid']] = _v
                  if _xv:
                      import json as _json
                      db.run("UPDATE txns SET extra=? WHERE id=?",
                             _json.dumps(_xv, ensure_ascii=False), _new_id)
                  saved += 1
                  if kind == '进': n_in += 1
                  else: n_out += 1
            except Exception as e:
                try: os.remove(p)
                except OSError: pass
                return render_template('txn_import.html', fixed=fixed, TPLS=db.tpls(), tid=tid, ep=ep,
                    err='导入失败，已全部回滚（数据未改动）：%s' % e)
            try: os.remove(p)
            except OSError: pass
            # 宽表会把"进""出"一起导进来，提示必须写明构成，
            # 否则用户看到"已导入 8 条入库单据"，实际却是 4 进 4 出。
            if n_in and n_out:
                tip = f'已导入 {saved} 条单据（进 {n_in} / 出 {n_out}）'
            elif n_out:
                tip = f'已导入 {saved} 条出库单据'
            else:
                tip = f'已导入 {saved} 条入库单据'
            if newmat:
                tip += f'，新建物料 {newmat} 种'
                # 只统计"本次新建"里字段不全的，别拿全库说事
                if new_miss:
                    tip += f'（其中 {new_miss} 种没认出料号/类型：'
                    tip += '宽表前几列若没写表头就识别不出来' if is_wide else '表里缺这几列'
                    tip += '，可在「物料」页补全或改用「手动指定列」）'
            if po_tips:
                tip += '；' + '；'.join(list(po_tips)[:3])
            if blocked:
                tip += f'；{blocked} 行超出库存被跳过：' + '、'.join(msgs[:3])
                # 导入历史流水时，期初没填会让早期的出库全部被拦。
                # 光报"超出库存"用户不知道怎么办，必须给出路。
                tip += '。这些行的出库时间早于入库（或期初未填）——' \
                       '请先补期初结存，或勾选「允许超出库存」重导'
            if skipped:
                tip += f'（忽略 {skipped} 笔{"出库" if filter_kind=="进" else "入库"}行）'
            if archived:
                tip += f'；{archived} 行没填数量，只更新了物料档案'
            return redirect(url_for(back, msg=tip))

        # 手动指定列：用户自己挑哪列是什么，绕过表头识别
        if request.form.get('manual') == '1':
            p = os.path.join(TMP, os.path.basename(request.form.get('f', '')))
            if not os.path.exists(p):
                return render_template('txn_import.html', fixed=fixed, TPLS=db.tpls(), tid=tid, ep=ep,
                                       err='预览已过期，请重新选择文件')
            colmap = {}
            for k in request.form.keys():
                if k.startswith('cm_'):
                    v = request.form.get(k)
                    if v:
                        colmap[k[3:]] = v
            try:
                start = int_arg(request.form, 'startrow', default=1, lo=1)
            except ValueError:
                start = 1
            if 'name' not in colmap.values() and 'code' not in colmap.values():
                return render_template('txn_import.html', fixed=fixed, TPLS=db.tpls(), tid=tid, ep=ep,
                                       err='至少要指定一列是「物料名称」或「料号」')
            try:
                rows = importer.parse_txn_by_map(
                    path=p, colmap=colmap, start=start, default_kind=fixed,
                    defdate=request.form.get('defdate') or today())
            except Exception as e:
                return render_template('txn_import.html', fixed=fixed, TPLS=db.tpls(), tid=tid, ep=ep, err='解析失败：%s' % e)
            if not rows:
                return render_template('txn_import.html', fixed=fixed, TPLS=db.tpls(), tid=tid, ep=ep,
                                       err='按你指定的列没读到数据，检查一下起始行是不是选错了')
            fields = sorted(set(colmap.values()))
            return render_template('txn_import.html', fixed=fixed, TPLS=db.tpls(), tid=tid, ep=ep, preview=rows[:60],
                                   total=len(rows), fields=fields,
                                   fields_str=','.join(fields),
                                   f=os.path.basename(p),
                                   manual=1, startrow=start,
                                   colmap=colmap,
                                   defkind=fixed or '',
                                   defdate=request.form.get('defdate') or today())

        f = request.files.get('file')
        if not f or not f.filename:
            return render_template('txn_import.html', fixed=fixed, TPLS=db.tpls(), tid=tid, ep=ep, err='请选择文件')
        fn = safe_name(f.filename, 'x.xlsx').lower()
        if not fn.endswith(('.xlsx', '.xlsm', '.xls', '.et', '.csv')):
            return render_template('txn_import.html', fixed=fixed, TPLS=db.tpls(), tid=tid, ep=ep,
                                   err='只支持 .xlsx / .xlsm / .xls / .et / .csv')
        # 先看大小再落盘：超大文件直接拒，别把磁盘写满
        f.seek(0, os.SEEK_END); size = f.tell(); f.seek(0)
        if size > MAX_UPLOAD:
            return render_template('txn_import.html', fixed=fixed, TPLS=db.tpls(), tid=tid, ep=ep,
                                   err='文件太大（%.1f MB），上限 %d MB。'
                                       '请拆分后再导入。' % (size / 1048576.0, MAX_UPLOAD // 1048576))
        tmp = os.path.join(TMP, '%d_%s' % (int(time.time() * 1000), fn))
        try:
            f.save(tmp)
        except (ValueError, OSError):
            return render_template('txn_import.html', fixed=fixed, TPLS=db.tpls(), tid=tid, ep=ep,
                                   err='文件名不合法，请改成普通中文/英数字再试')
        try:
            rows, fields, hi, diag = importer.parse_txn_file(
                path=tmp, default_kind=fixed or (request.form.get('defkind') or None),
                defmonth=(request.form.get('defdate') or '')[:7],
                mat_aliases=db.aliases_map(tid), xcols=db.tpl_custom_cols(tid))
        except Exception as e:
            return render_template('txn_import.html', fixed=fixed, TPLS=db.tpls(), tid=tid, ep=ep, err='解析失败：%s' % e)
        # 列名指纹：拿原始表头算，列名一样就自动归到同一个模板
        try:
            _grid = importer._read_grid(path=tmp)
            _hdr = _grid[0] if _grid else []
        except Exception:
            _hdr = []
        _sig = db.head_sig(_hdr)
        _hit = db.tpl_by_sig(_sig)
        if _hit and (request.values.get('tpl') or 0) in ('', '0', None, 0):
            tid = _hit['id']          # 认出是同一张表，自动选中
        if not rows:
            why = (diag or {}).get('why')
            if why == 'no-header':
                err = ('没认出表头。表格第一行要写列名，'
                       '至少需要「物料名称（或料号）」，'
                       '以及「数量」/「进」「出」之一。')
            elif why == 'no-data':
                err = '表头认出来了，但下面没有有效数据行（物料名和数量都要填）。'
            else:
                err = '没读到有效单据。'
            grid = (diag or {}).get('grid') or []
            ncol = max([len(r) for r in grid] or [0])
            return render_template('txn_import.html', fixed=fixed, TPLS=db.tpls(), tid=tid, ep=ep, err=err, diag=diag,
                                   grid=grid, ncol=range(ncol),
                                   f=os.path.basename(tmp),
                                   defkind=request.form.get('defkind') or '',
                                   defdate=request.form.get('defdate') or today())
        return render_template('txn_import.html', fixed=fixed, TPLS=db.tpls(), tid=tid, ep=ep, preview=rows[:60],
                               total=len(rows), fields=sorted(fields),
                               fields_str=','.join(sorted(fields)),
                               f=os.path.basename(tmp),
                               yms=(diag or {}).get('yms') or {},
                               skipped_sum=(diag or {}).get('skipped_sum') or 0,
                               defkind=request.form.get('defkind') or '',
                               defdate=request.form.get('defdate') or today(),
                               wide=(hi == -2), sig=_sig, hdr=_hdr,
                               sig_hit=(_hit['id'] if _hit else 0))
    return render_template('txn_import.html', fixed=fixed, TPLS=db.tpls(), tid=tid, ep=ep, defdate=today())

@bp.route('/txn/import/tpl')
def txn_import_tpl():
    import openpyxl
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = '出入库导入'
    head = ['日期', '物料名称', '料号', '类型', '规格', '宽幅', '供应商', '单位', '进', '出', '备注']
    ws.append(head)
    for r in db.q("SELECT id,name,code,category,spec,width,supplier,unit,opening FROM v_stock"
                  " WHERE active=1 ORDER BY category,name LIMIT 3"):
        ws.append([today(), r['name'], r['code'], r['category'], r['spec'], r['width'],
                   r['supplier'], r['unit'], '', '', ''])
    ws.append([today(), '（示例）半对半黑化压延', 'CPDR131218KAB1', '无胶压延', '12/20', '250',
               '松杨电子', '平米', 100, 30, '出柏威'])
    for i, w in enumerate([12, 24, 20, 14, 12, 8, 14, 8, 9, 9, 18], 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
    bio = io.BytesIO(); wb.save(bio)
    from urllib.parse import quote
    return Response(bio.getvalue(),
                    mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition':
                             "attachment; filename=tpl.xlsx; filename*=UTF-8''%s.xlsx" % quote('出入库导入模板')})
