# Windows 中文版控制台是 GBK，print 一个 emoji 就会 UnicodeEncodeError 崩溃。
# 必须在任何输出之前把 stdout/stderr 改成容错模式。
import sys as _sys
def _safe_stdio():
    for name in ('stdout', 'stderr'):
        st = getattr(_sys, name, None)
        if st is None:
            continue
        try:                       # Python 3.7+ 支持重新配置
            st.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            try:                   # 老版本 / 打包环境
                import io as _io
                setattr(_sys, name, _io.TextIOWrapper(
                    st.buffer, encoding='utf-8', errors='replace'))
            except Exception:
                pass
_safe_stdio()

from flask import Flask, render_template, request, redirect, url_for
from datetime import datetime, date
import calendar, io, csv, os, time, sys
from flask import Response
import db, importer

def say(msg=''):
    """容错输出：编码问题、控制台不存在都不会让程序崩"""
    try:
        print(msg)
    except Exception:
        try:
            print(str(msg).encode('ascii', 'replace').decode('ascii'))
        except Exception:
            pass


BASE = db.app_dir()
TMP = os.path.join(BASE, '.uploads')
os.makedirs(TMP, exist_ok=True)

# 打包成 exe 后，模板在 PyInstaller 解包的临时目录里，必须显式指过去
if getattr(sys, 'frozen', False):
    app = Flask(__name__, template_folder=os.path.join(db.res_dir(), 'templates'))
else:
    app = Flask(__name__)
# 数据库损坏时 init() 就会抛异常，而这是在 import 阶段——
# main() 里的兜底根本轮不到执行。必须在这里就接住，否则
# 窗口模式（无控制台）下程序一闪而过，用户完全不知道出了什么事。
try:
    db.init()
except Exception as _e:
    import desktop as _d
    _lines = ['数据库打不开：%s' % _e]
    try:
        _lines += db.check_integrity()
    except Exception:
        pass
    _lines.append('也可以把 %s 改名（比如加 .old），程序会自动新建一个空库，'
                  '再用之前的备份还原。' % os.path.basename(db.DB_PATH))
    _d.log('启动失败：\n  ' + '\n  '.join(_lines))
    say('')
    say('  !! 启动失败 !!')
    for _m in _lines:
        say('  ' + _m)
    say('')
    say('  以上信息已写入 %s' % _d.LOG)
    if getattr(sys, 'frozen', False):
        time.sleep(30)
    raise SystemExit(1)

LABELS = {'name': '物料名称', 'code': '料号', 'supplier': '供应商', 'category': '类型',
          'spec': '规格', 'width': '宽幅', 'unit': '单位', 'status': '状态',
          'opening': '期初结存', 'safety': '安全库存'}
app.jinja_env.globals.update(LABELS=LABELS)

# ---------- 全局错误处理：任何异常都给一句人话，而不是空白页 ----------
@app.teardown_appcontext
def _close_db(exc):
    pass                      # 连接按线程复用，进程退出时由解释器回收

@app.errorhandler(404)
def e404(e):
    return render_template('error.html', code=404, title='页面不存在',
                           detail='地址可能输错了。'), 404

@app.errorhandler(500)
def e500(e):
    import traceback
    traceback.print_exc()
    return render_template('error.html', code=500, title='出错了',
                           detail='已记录错误。数据未写入，可以返回上一步重试。'), 500

@app.errorhandler(Exception)
def eall(e):
    """兜底：业务异常转成友好提示，不暴露堆栈"""
    import traceback
    traceback.print_exc()
    code = 500
    return render_template('error.html', code=code, title='操作未完成',
                           detail='%s' % e), code

def today():
    return date.today().strftime('%Y-%m-%d')

MAX_UPLOAD = 20 * 1024 * 1024      # 单个上传文件上限 20MB，防止被大文件刷爆磁盘
MAX_KW = 100                       # 搜索词上限：SQLite 对超长 LIKE 模式会报
                                   # "LIKE or GLOB pattern too complex" 直接 500
MAX_ROWS = 500                     # 录单页一次最多提交/渲染多少行

def safe_name(name, default='upload'):
    """把上传文件名压成安全的：去掉路径、空字节、控制字符，限制长度。

    不处理的话，文件名里带 \\x00 会在 open() 时抛
    ValueError: embedded null byte，直接变 HTTP 500。
    """
    import re as _re
    name = os.path.basename((name or '').replace('\\', '/'))
    # 控制字符直接剔除（而不是截断），这样 a\x00b.xlsx 还能保留成 ab.xlsx
    name = _re.sub(r'[\x00-\x1f\x7f]', '', name)
    name = _re.sub(r'\s+', ' ', name)                     # 连续空白压成一个
    name = name.strip().strip('.') or default
    if len(name) > 80:                                 # 防止超长文件名
        stem, dot, ext = name.rpartition('.')
        name = (stem[:60] or stem) + dot + (ext[:10] if dot else '')
    return name

def clean_kw(s, limit=MAX_KW):
    """搜索词清洗：去空白、限长。超长会让 SQLite 的 LIKE 直接报错。"""
    s = (s or '').strip()
    if len(s) > limit:
        s = s[:limit]
    # LIKE 里的 % 和 _ 是通配符，用户搜 "50%" 时应该匹配字面量而不是任意串
    return s.replace('%', '').replace('_', '')

def cleanup_tmp(max_age=3600):
    """清掉上传后没确认导入的临时文件，避免 .uploads 越积越多"""
    try:
        now = time.time()
        for f in os.listdir(TMP):
            p = os.path.join(TMP, f)
            try:
                if os.path.isfile(p) and now - os.path.getmtime(p) > max_age:
                    os.remove(p)
            except OSError:
                pass
    except OSError:
        pass

# ---------- 输入校验 ----------
def num(v, default=0.0, lo=None, hi=None):
    """安全转数字：非法/NaN/Inf 一律返回默认值"""
    import math
    try:
        f = float(str(v).strip().replace(',', ''))
    except (TypeError, ValueError):
        return default
    if math.isnan(f) or math.isinf(f):
        return default
    if lo is not None and f < lo:
        f = lo
    if hi is not None and f > hi:
        f = hi
    return f

def one(sql, *a, default=None):
    """取第一行，没有就返回 default，杜绝 IndexError"""
    r = db.q(sql, *a)
    return r[0] if r else default

def scalar(sql, *a, default=0):
    """取第一格，没有就返回 default"""
    r = db.q(sql, *a)
    return r[0][0] if r else default

def is_date(s):
    """只接受 YYYY-MM-DD"""
    try:
        datetime.strptime((s or '').strip(), '%Y-%m-%d')
        return True
    except (ValueError, TypeError):
        return False

def safe_date(s, default=None):
    s = (s or '').strip()
    return s if is_date(s) else (default or today())

def ints(form, key):
    """从表单取 id 列表，过滤非法值"""
    out = []
    for i in form.getlist(key):
        try:
            out.append(int(str(i).strip()))
        except (TypeError, ValueError):
            continue
    return out

# ---------- 系统自检 / 备份 ----------
@app.route('/sys')
def sysinfo():
    import platform
    issues = db.check_integrity()
    return render_template('sys.html', issues=issues, stats=db.stats(),
                           py=platform.python_version(),
                           baks=sorted([f for f in os.listdir(BASE) if f.endswith('.bak')],
                                       reverse=True)[:5],
                           msg=request.args.get('msg', ''))

@app.route('/sys/fix')
def sysfix():
    n = db.fix_orphans()
    return redirect(url_for('sysinfo', msg=('已清理 %d 条孤儿单据' % n) if n else '没有需要清理的数据'))

@app.route('/sys/backup')
def sysbackup():
    p = db.backup()
    return redirect(url_for('sysinfo', msg='已备份到 %s' % os.path.basename(p)))

def calc_qty(qty, pieces, per):
    """总数量 / 件数 / 每件数量，填任意两个算第三个。
    件数与每件不可整除时保留两位小数；只填总数量也可以。"""
    def f(v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return 0.0
        return v if v > 0 else 0.0
    q, p, e = f(qty), f(pieces), f(per)
    if q and p and not e:
        e = round(q / p, 2)
    elif q and e and not p:
        p = round(q / e, 2)
    elif p and e and not q:
        q = round(p * e, 2)
    return (q or 0.0), (p or None), (e or None)

def sort_args(sort, default):
    """三态排序：默认 -> 升序 -> 降序 -> 默认"""
    if not sort:
        return default, ''
    f, d = (sort.rsplit(':', 1) + [''])[:2] if ':' in sort else (sort, '')
    return f, d

def ym(d=None):
    d = d or today()
    return d[:7]

def safe_ym(m, default=None):
    """把用户传来的月份参数规范成 YYYY-MM。

    不校验就直接 int(m[5:7]) 会在 m='abc' 时抛 ValueError，
    calendar.monthrange 也会对 13 月、0 月抛 IllegalMonthError，
    两种情况都是 HTTP 500。这里统一兜住，非法就回退到默认月份。
    """
    import re as _re
    if not m or not isinstance(m, str):
        return default or ym()
    m = m.strip()
    if not _re.match(r'^\d{4}-\d{2}$', m):
        # 容错：2026-9 补成 2026-09
        m2 = _re.match(r'^(\d{4})-(\d{1,2})$', m)
        if m2:
            m = '%s-%02d' % (m2.group(1), int(m2.group(2)))
        else:
            return default or ym()
    try:
        y, mo = int(m[:4]), int(m[5:7])
        if not (1 <= mo <= 12) or not (1970 <= y <= 9999):
            return default or ym()
    except (ValueError, TypeError):
        return default or ym()
    return m

@app.context_processor
def inject():
    return dict(today=today(), ym=ym())

@app.route('/')
def index():
    t, m = today(), ym()
    day_in  = db.q("SELECT COALESCE(SUM(qty),0) s FROM txns WHERE tdate=? AND kind='进'", t)[0]['s']
    day_out = db.q("SELECT COALESCE(SUM(qty),0) s FROM txns WHERE tdate=? AND kind='出'", t)[0]['s']
    mon_in  = db.q("SELECT COALESCE(SUM(qty),0) s FROM txns WHERE tdate LIKE ? AND kind='进'", m+'%')[0]['s']
    mon_out = db.q("SELECT COALESCE(SUM(qty),0) s FROM txns WHERE tdate LIKE ? AND kind='出'", m+'%')[0]['s']
    n_mat   = db.q("SELECT COUNT(*) c FROM materials WHERE active=1")[0]['c']
    alerts  = db.q("SELECT * FROM v_stock WHERE stock<=safety ORDER BY stock")
    recent  = db.q("SELECT t.*, m.name, m.unit, m.code FROM txns t JOIN materials m ON m.id=t.material_id"
                   " ORDER BY t.id DESC LIMIT 8")
    return render_template('index.html', day_in=day_in, day_out=day_out, mon_in=mon_in,
                           mon_out=mon_out, n_mat=n_mat, alerts=alerts, recent=recent)

# ---------- 单据流水（表格录入，含原表 A-G 全部列） ----------
def _resolve_material(vals):
    """按 料号 -> 名称 匹配物料；匹配到则用行内 A-G 值同步档案，否则新建。
    返回 (material_id, is_new)"""
    name = (vals.get('name') or '').strip()
    code = (vals.get('code') or '').strip()
    hit = None
    if code:
        hit = db.q("SELECT id FROM materials WHERE code=? AND code<>''", code)
    if not hit and name:
        hit = db.q("SELECT id FROM materials WHERE name=?", name)
    if hit:
        mid = hit[0]['id']
        sets, vs = [], []
        for f in ('name', 'code', 'supplier', 'category', 'spec', 'width', 'unit', 'status'):
            if f in vals and (vals[f] or '').strip():
                sets.append("%s=?" % f); vs.append((vals[f] or '').strip() or None)
        if sets:
            vs.append(mid)
            db.run("UPDATE materials SET " + ",".join(sets) + " WHERE id=?", *vs)
        return mid, False
    if not name:
        return None, False
    mid = db.run("INSERT INTO materials(supplier,category,spec,width,name,code,unit,opening,safety,status)"
                 " VALUES(?,?,?,?,?,?,?,?,?,?)",
                 vals.get('supplier', ''), vals.get('category', ''), vals.get('spec', ''),
                 vals.get('width', ''), name, code, (vals.get('unit') or '').strip() or '平米',
                 float(vals.get('opening') or 0), float(vals.get('safety') or 0),
                 (vals.get('status') or '').strip() or '常用')
    return mid, True

@app.route('/txn', methods=['GET', 'POST'], endpoint='txn')
@app.route('/out', methods=['GET', 'POST'], endpoint='out')
@app.route('/in', methods=['GET', 'POST'], endpoint='in')
def txn():
    """表格录单。/in 默认入库、/out 默认出库（且锁定为出库）、/txn 通用"""
    ep = request.endpoint
    fixed = '出' if ep == 'out' else ('进' if ep == 'in' else None)
    cols = db.cols()
    fids = [c['fid'] for c in cols]
    only_stock = fixed == '出' and request.args.get('allm') != '1'
    mats = [dict(r) for r in db.q(
        "SELECT * FROM v_stock WHERE active=1" + (" AND stock>0" if only_stock else "")
        + " ORDER BY name")]
    msg = request.args.get('msg', '')

    if request.method == 'POST':
        nrow = min(int(request.form.get('nrow') or 0), MAX_ROWS)
        saved = newmat = blocked = 0
        names = []
        dflt = request.form.get('tdate') or today()
        # 一次提交多行时要么全成功要么全回滚：
        # 否则中途出错（比如某一行的物料档案更新失败）会留下"录了一半"的单据，
        # 用户看到报错后重录，那几行就重复了。
        try:
          with db.tx() as _cx:
            for i in range(nrow):
                qty = num(request.form.get(f'qty_{i}'))
                vals = {f: (request.form.get(f'{f}_{i}') or '').strip() for f in
                        ('name', 'code', 'supplier', 'category', 'spec', 'width', 'unit', 'status')}
                vals['opening'] = num(request.form.get(f'opening_{i}'))
                vals['safety'] = num(request.form.get(f'safety_{i}'))
                qty, pieces, per = calc_qty(qty, request.form.get(f'pieces_{i}'),
                                            request.form.get(f'per_{i}'))
                if qty <= 0 or (not vals['name'] and not vals['code']):
                    continue
                mid, is_new = _resolve_material(vals)
                if not mid:
                    continue
                newmat += is_new
                d = safe_date(request.form.get(f'tdate_{i}'), dflt)
                kind = fixed or (request.form.get(f'kind_{i}') or '进')
                note = (request.form.get(f'note_{i}') or '').strip()
                if kind == '出':                      # 出库不允许超出现有库存
                    stock = scalar("SELECT stock FROM v_stock WHERE id=?", mid, default=0)
                    if qty > stock + 1e-9:
                        blocked += 1
                        names.append('%s(可用%g)' % (vals['name'] or vals['code'], stock))
                        continue
                _cx.execute("INSERT INTO txns(tdate,material_id,kind,qty,pieces,per_piece,note,created_at)"
                       " VALUES(?,?,?,?,?,?,?,?)",
                       (d, mid, kind, qty, pieces, per, note,
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
                saved += 1
        except Exception:
            return render_template('error.html', code=500, title='保存失败',
                detail='这批单据一条都没保存（已回滚），请返回重试。'), 500
        back = 'out' if ep == 'out' else ('in' if ep == 'in' else 'txn')
        tip = f'已保存 {saved} 条{"出库" if fixed=="出" else ("入库" if fixed=="进" else "")}单' \
              + (f'，新建物料 {newmat} 种' if newmat else '')
        if blocked:
            tip += '；%d 行因超出库存未保存：%s' % (blocked, '、'.join(names[:3]))
        if request.form.get('stay'):
            return redirect(url_for(back, msg=tip, n=request.form.get('nrow')))
        return redirect(url_for('txns', msg=tip, kind=fixed or ''))

    n = min(int(request.args.get('n') or 5), MAX_ROWS)
    return render_template('txn.html', cols=cols, fids=fids, mats=mats, msg=msg,
                           fixed=fixed, ep=ep, only_stock=only_stock,
                           allm=request.args.get('allm') == '1',
                           tdate=today(), rows=range(n), n=n,
                           js_mats=[{k: m[k] for k in ('id','name','code','supplier','category',
                                                       'spec','width','unit','stock')} for m in mats])

@app.route('/out/list')
def out_list():
    """历史出库流水"""
    return _kind_list('出')

@app.route('/in/list')
def in_list():
    return _kind_list('进')

def _kind_list(kind):
    d = request.args.get('d') or ''
    m = request.args.get('m') or ''
    sql = "SELECT t.*, m.name, m.unit, m.code FROM txns t JOIN materials m ON m.id=t.material_id WHERE t.kind=?"
    args = [kind]
    if d:
        sql += " AND t.tdate=?"; args.append(d)
    elif m:
        sql += " AND t.tdate LIKE ?"; args.append(m + '%')
    sql += " ORDER BY t.tdate DESC, t.id DESC LIMIT 300"
    tot = db.q("SELECT COALESCE(SUM(qty),0) s, COUNT(*) c FROM txns WHERE kind=?" +
               (" AND tdate LIKE ?" if m else ""), *([kind, m + '%'] if m else [kind]))[0]
    return render_template('txns.html', rows=db.q(sql, *args), d=d, m=m, kind=kind,
                           total=tot['s'], count=tot['c'], url_kind='out_list' if kind == '出' else 'in_list')

# ---------- 流水（出入库单据）Excel / WPS 导入 ----------
@app.route('/txn/import', methods=['GET', 'POST'])
@app.route('/in/import', methods=['GET', 'POST'], endpoint='in_import')
@app.route('/out/import', methods=['GET', 'POST'], endpoint='out_import')
def txn_import():
    ep = request.endpoint
    fixed = '出' if ep == 'out_import' else ('进' if ep == 'in_import' else None)
    back = 'out' if fixed == '出' else ('in' if fixed == '进' else 'txn')

    if request.method == 'POST':
        mode = request.form.get('mode', 'merge')
        if request.form.get('confirm') == '1':
            p = os.path.join(TMP, os.path.basename(request.form.get('f', '')))
            if not os.path.exists(p):
                return render_template('txn_import.html', fixed=fixed,
                                       err='预览已过期，请重新选择文件')
            if request.form.get('manual') == '1':
                colmap = {}
                for k in request.form.keys():
                    if k.startswith('cm_'):
                        v = request.form.get(k)
                        if v:
                            colmap[k[3:]] = v
                try:
                    start = int(request.form.get('startrow') or 1)
                except ValueError:
                    start = 1
                rows = importer.parse_txn_by_map(
                    path=p, colmap=colmap, start=start, default_kind=fixed,
                    defdate=(request.form.get('defdate') or '').strip() or today())
            else:
                rows, _, _, _ = importer.parse_txn_file(
                    path=p, default_kind=fixed or (request.form.get('defkind') or None),
                    defmonth=(request.form.get('defdate') or '')[:7])
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
            msgs = []
            # 整批导入放进一个事务：中途任何异常全回滚，绝不留下"导了一半"的数据
            try:
              with db.tx():
                for d in rows:
                  vals = {k: d.get(k, '') for k in
                          ('name', 'code', 'supplier', 'category', 'spec', 'width', 'unit', 'status')}
                  vals['opening'] = d.get('opening', 0)
                  vals['safety'] = d.get('safety', 0)
                  mid, is_new = _resolve_material(vals)
                  if not mid:
                      continue
                  newmat += is_new
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
                  db.run("INSERT INTO txns(tdate,material_id,kind,qty,pieces,per_piece,note,created_at)"
                         " VALUES(?,?,?,?,?,?,?,?)",
                         d.get('date') or dflt_date, mid, kind, qty, pc, per, d.get('note', ''),
                         datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                  saved += 1
            except Exception as e:
                try: os.remove(p)
                except OSError: pass
                return render_template('txn_import.html', fixed=fixed,
                    err='导入失败，已全部回滚（数据未改动）：%s' % e)
            try: os.remove(p)
            except OSError: pass
            tip = f'已导入 {saved} 条{"出库" if fixed=="出" else ("入库" if fixed=="进" else "")}单据'
            if newmat:
                tip += f'，新建物料 {newmat} 种'
                # 只统计"本次新建"里字段不全的，别拿全库说事
                if new_miss:
                    tip += f'（其中 {new_miss} 种没认出料号/类型：'
                    tip += '宽表前几列若没写表头就识别不出来' if is_wide else '表里缺这几列'
                    tip += '，可在「物料」页补全或改用「手动指定列」）'
            if blocked:
                tip += f'；{blocked} 行超出库存被跳过：' + '、'.join(msgs[:3])
            if skipped:
                tip += f'（忽略 {skipped} 笔{"出库" if filter_kind=="进" else "入库"}行）'
            if archived:
                tip += f'；{archived} 行没填数量，只更新了物料档案'
            return redirect(url_for(back, msg=tip))

        # 手动指定列：用户自己挑哪列是什么，绕过表头识别
        if request.form.get('manual') == '1':
            p = os.path.join(TMP, os.path.basename(request.form.get('f', '')))
            if not os.path.exists(p):
                return render_template('txn_import.html', fixed=fixed,
                                       err='预览已过期，请重新选择文件')
            colmap = {}
            for k in request.form.keys():
                if k.startswith('cm_'):
                    v = request.form.get(k)
                    if v:
                        colmap[k[3:]] = v
            try:
                start = int(request.form.get('startrow') or 1)
            except ValueError:
                start = 1
            if 'name' not in colmap.values() and 'code' not in colmap.values():
                return render_template('txn_import.html', fixed=fixed,
                                       err='至少要指定一列是「物料名称」或「料号」')
            try:
                rows = importer.parse_txn_by_map(
                    path=p, colmap=colmap, start=start, default_kind=fixed,
                    defdate=request.form.get('defdate') or today())
            except Exception as e:
                return render_template('txn_import.html', fixed=fixed, err='解析失败：%s' % e)
            if not rows:
                return render_template('txn_import.html', fixed=fixed,
                                       err='按你指定的列没读到数据，检查一下起始行是不是选错了')
            fields = sorted(set(colmap.values()))
            return render_template('txn_import.html', fixed=fixed, preview=rows[:60],
                                   total=len(rows), fields=fields,
                                   fields_str=','.join(fields),
                                   f=os.path.basename(p),
                                   manual=1, startrow=start,
                                   colmap=colmap,
                                   defkind=fixed or '',
                                   defdate=request.form.get('defdate') or today())

        f = request.files.get('file')
        if not f or not f.filename:
            return render_template('txn_import.html', fixed=fixed, err='请选择文件')
        fn = safe_name(f.filename, 'x.xlsx').lower()
        if not fn.endswith(('.xlsx', '.xlsm', '.xls', '.et', '.csv')):
            return render_template('txn_import.html', fixed=fixed,
                                   err='只支持 .xlsx / .xlsm / .xls / .et / .csv')
        # 先看大小再落盘：超大文件直接拒，别把磁盘写满
        f.seek(0, os.SEEK_END); size = f.tell(); f.seek(0)
        if size > MAX_UPLOAD:
            return render_template('txn_import.html', fixed=fixed,
                                   err='文件太大（%.1f MB），上限 %d MB。'
                                       '请拆分后再导入。' % (size / 1048576.0, MAX_UPLOAD // 1048576))
        tmp = os.path.join(TMP, '%d_%s' % (int(time.time() * 1000), fn))
        try:
            f.save(tmp)
        except (ValueError, OSError):
            return render_template('txn_import.html', fixed=fixed,
                                   err='文件名不合法，请改成普通中文/英数字再试')
        try:
            rows, fields, hi, diag = importer.parse_txn_file(
                path=tmp, default_kind=fixed or (request.form.get('defkind') or None),
                defmonth=(request.form.get('defdate') or '')[:7])
        except Exception as e:
            return render_template('txn_import.html', fixed=fixed, err='解析失败：%s' % e)
        if not rows:
            why = (diag or {}).get('why')
            if why == 'no-header':
                err = ('没认出表头。表格第一行要写列名，'
                       '至少需要「物料名称（或料号）」，'
                       '以及「数量」/「进」「出」/「件数」+「每件」之一。')
            elif why == 'no-data':
                err = '表头认出来了，但下面没有有效数据行（物料名和数量都要填）。'
            else:
                err = '没读到有效单据。'
            grid = (diag or {}).get('grid') or []
            ncol = max([len(r) for r in grid] or [0])
            return render_template('txn_import.html', fixed=fixed, err=err, diag=diag,
                                   grid=grid, ncol=range(ncol),
                                   f=os.path.basename(tmp),
                                   defkind=request.form.get('defkind') or '',
                                   defdate=request.form.get('defdate') or today())
        return render_template('txn_import.html', fixed=fixed, preview=rows[:60],
                               total=len(rows), fields=sorted(fields),
                               fields_str=','.join(sorted(fields)),
                               f=os.path.basename(tmp),
                               defkind=request.form.get('defkind') or '',
                               defdate=request.form.get('defdate') or today(),
                               wide=(hi == -2))
    return render_template('txn_import.html', fixed=fixed, defdate=today())

@app.route('/txn/import/tpl')
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

# ---------- 物料台账（库存历史查询） ----------
@app.route('/material/<int:mid>/history')
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
    return render_template('history.html', row=row, rows=rows, m=m, months=months)

# ---------- 列（表头）映射设置 ----------
@app.route('/columns', methods=['GET', 'POST'])
def columns():
    if request.method == 'POST':
        if request.form.get('reset'):
            db.reset_cols()
            return redirect(url_for('columns', msg='已恢复默认表头'))
        rows = []
        for r in db.q("SELECT fid FROM colmap"):
            fid = r['fid']
            rows.append(dict(fid=fid,
                             label=(request.form.get(f'label_{fid}') or '').strip() or fid,
                             pos=int(request.form.get(f'pos_{fid}') or 0),
                             enabled=1 if request.form.get(f'en_{fid}') else 0,
                             aliases=(request.form.get(f'al_{fid}') or '').strip()))
        db.save_cols(rows)
        return redirect(url_for('columns', msg='表头映射已保存'))
    return render_template('columns.html', cols=db.cols(False), msg=request.args.get('msg', ''),
                           SAMPLE=['物料名称', '料号', '类型', '规格', '宽幅', '供应商', '单位', '期初结存', '安全库存', '状态'])

@app.route('/txn/del/<int:tid>')
def txn_del(tid):
    db.run("DELETE FROM txns WHERE id=?", tid)
    return redirect(request.referrer or url_for('txns'))

@app.route('/txns')
def txns():
    d = request.args.get('d') or ''
    kind = request.args.get('kind') or ''
    kw = clean_kw(request.args.get('kw'))
    sort = request.args.get('sort') or ''
    dir_ = request.args.get('dir') or ''
    sql = ("SELECT t.*, m.name, m.unit, m.code, m.category FROM txns t"
           " JOIN materials m ON m.id=t.material_id")
    w, args = [], []
    if d:
        w.append("t.tdate=?"); args.append(d)
    if kind:
        w.append("t.kind=?"); args.append(kind)
    if kw:
        w.append("(m.name LIKE ? OR m.code LIKE ? OR t.note LIKE ? OR m.category LIKE ?)")
        args += ['%%%s%%' % kw] * 4
    if w:
        sql += " WHERE " + " AND ".join(w)
    allowed = {'tdate': 't.tdate', 'name': 'm.name', 'qty': 't.qty', 'kind': 't.kind',
               'pieces': 'COALESCE(t.pieces,0)', 'note': 't.note'}
    if sort in allowed and dir_:
        sql += " ORDER BY %s %s, t.id DESC" % (allowed[sort], 'ASC' if dir_ == 'asc' else 'DESC')
    else:
        sql += " ORDER BY t.tdate DESC, t.id DESC"
    sql += " LIMIT 500"
    rows = db.q(sql, *args)
    return render_template('txns.html', rows=rows, d=d, kind=kind or None, m='', kw=kw,
                           total=0, count=len(rows), url_kind='txns',
                           cur_sort=sort, cur_dir=dir_, qs={'d': d, 'kind': kind, 'kw': kw},
                           msg=request.args.get('msg', ''))

# ---------- 物料档案 ----------
@app.route('/materials')
def materials():
    kw = clean_kw(request.args.get('kw'))
    show_all = request.args.get('all') == '1'
    w, a = [], []
    if kw:
        w.append("(name LIKE ? OR code LIKE ? OR supplier LIKE ? OR category LIKE ? OR spec LIKE ?)")
        a += ['%%%s%%' % kw] * 5
    if not show_all:
        w.append("active=1")
    inline = request.args.get('edit') == '1'
    sql = "SELECT * FROM v_mats" + (" WHERE " + " AND ".join(w) if w else "")
    sort = request.args.get('sort') or ''
    dir_ = request.args.get('dir') or ''
    allowed = {'name': 'name', 'code': 'code', 'category': 'category', 'spec': 'spec',
               'width': 'width', 'supplier': 'supplier', 'opening': 'opening', 'stock': 'stock'}
    if sort in allowed and dir_:
        sql += " ORDER BY %s %s, name" % (allowed[sort], 'ASC' if dir_ == 'asc' else 'DESC')
    else:
        sql += " ORDER BY active DESC, category, name"
    rows = db.q(sql, *a)
    return render_template('materials.html', rows=rows, kw=kw, inline=request.args.get('edit') == '1',
                           show_all=show_all, cur_sort=sort, cur_dir=dir_,
                           qs={'kw': kw, 'all': '1' if show_all else '', 'edit': '1' if inline else ''},
                           msg=request.args.get('msg',''))

@app.route('/material/edit/<int:mid>', methods=['GET','POST'])
@app.route('/material/new', methods=['GET','POST'])
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
                name, f.get('code',''), f.get('unit','') or '平米',
                num(f.get('opening')), num(f.get('safety')), f.get('status','') or '常用')
        if mid:
            db.run("UPDATE materials SET supplier=?,category=?,spec=?,width=?,name=?,code=?,"
                   "unit=?,opening=?,safety=?,status=? WHERE id=?", (*data, mid))
        else:
            db.run("INSERT INTO materials(supplier,category,spec,width,name,code,unit,opening,safety,status)"
                   " VALUES(?,?,?,?,?,?,?,?,?,?)", *data)
        return redirect(url_for('materials'))
    return render_template('material_edit.html', row=row)

@app.route('/material/del/<int:mid>')
def material_del(mid):
    n = scalar("SELECT COUNT(*) FROM txns WHERE material_id=?", mid)
    db.run("UPDATE materials SET active=0 WHERE id=?", mid) if n else db.run("DELETE FROM materials WHERE id=?", mid)
    return redirect(url_for('materials'))

# ---------- 批量修改 / 删除 ----------
@app.route('/materials/batch', methods=['POST'])
def batch():
    ids = ints(request.form, 'id')
    act = request.form.get('act')
    msg = ''
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
@app.route('/materials/table', methods=['POST'])
def table_save():
    ids = ints(request.form, 'id')
    changed = 0
    for i in ids:
        vals = {}
        for f in ('name', 'code', 'supplier', 'category', 'spec', 'width', 'unit', 'status'):
            if f in request.form:
                vals[f] = request.form.get(f'{f}_{i}', '').strip()
        for f in ('opening', 'safety'):
            if f in request.form:
                vals[f] = num(request.form.get(f'{f}_{i}'))
        if not vals.get('name'):
            continue
        sets = ','.join(f'{k}=?' for k in vals)
        db.run(f"UPDATE materials SET {sets} WHERE id=?", *vals.values(), i)
        changed += 1
    return redirect(url_for('materials', msg=f'已保存 {changed} 项物料的修改',
                            kw=request.args.get('kw', ''), all=request.args.get('all', '')))

# ---------- Excel / CSV 批量导入 ----------

@app.route('/import', methods=['GET', 'POST'])
def imp():
    if request.method == 'POST':
        mode = request.form.get('mode', 'merge')
        if request.form.get('confirm') == '1':
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
                             d['unit'] or '平米', d['opening'], d['safety'], d['status'] or '常用')
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

@app.route('/import/tpl')
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
@app.route('/stock')
def stock():
    f = request.args.get('f', '')
    kw = clean_kw(request.args.get('kw'))
    sort = request.args.get('sort') or ''
    dir_ = request.args.get('dir') or ''
    w, args = [], []
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
                           qs={'f': f, 'kw': kw})

# ---------- 月报表（复刻原模板布局） ----------
@app.route('/report')
def report():
    m = safe_ym(request.args.get('m'))
    mats, days = _report_data(m)
    tot_in = sum(mt['min'] for mt in mats)
    tot_out = sum(mt['mout'] for mt in mats)
    return render_template('report.html', m=m, days=days, mats=mats,
                           tot_in=tot_in, tot_out=tot_out)

@app.route('/export.xlsx')
def export_xlsx():
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    kind = request.args.get('t', 'stock')
    kw = clean_kw(request.args.get('kw'))
    f = request.args.get('f') or ''
    d = request.args.get('d') or ''
    m = safe_ym(request.args.get('m'))
    sort = request.args.get('sort') or ''
    dir_ = request.args.get('dir') or ''
    wb = openpyxl.Workbook(); ws = wb.active
    head_font = Font(bold=True, color='FFFFFF')
    fill = PatternFill('solid', start_color='1F6FEB')
    if kind == 'stock':
        rows = _stock_rows(kw, f, sort, dir_)
        ws.title = '库存'
        ws.append(['供应商', '类型', '规格', '宽幅', '物料名称', '料号', '单位',
                   '期初', '入库', '出库', '当前库存', '安全库存', '状态'])
        for r in rows:
            ws.append([r['supplier'], r['category'], r['spec'], r['width'], r['name'],
                       r['code'], r['unit'], r['opening'], r['in_qty'], r['out_qty'],
                       r['stock'], r['safety'], r['status']])
        fn = '库存'
    elif kind == 'report':
        mats, days = _report_data(m)
        ws.title = m
        ws.append(['物料 / 料号'] + ['%d进' % d2 for d2 in range(1, days + 1)]
                  + ['%d出' % d2 for d2 in range(1, days + 1)] + ['进汇总', '出汇总', '月末'])
        for r in mats:
            ws.append([r['name'] + (' / ' + r['code'] if r['code'] else '')]
                      + [c[0] or None for c in r['cells']] + [c[1] or None for c in r['cells']]
                      + [r['min'], r['mout'], r['ending']])
        fn = '进出月报' + m
    else:
        rows = _txn_rows(kw, d, request.args.get('kind') or '', sort, dir_)
        ws.title = '流水'
        ws.append(['日期', '物料名称', '料号', '类型', '进/出', '数量', '件数', '每件', '单位', '备注'])
        for r in rows:
            ws.append([r['tdate'], r['name'], r['code'], r['category'], r['kind'],
                       r['qty'], r['pieces'], r['per_piece'], r['unit'], r['note']])
        fn = ('出入库流水' + (d or m))
    for c in ws[1]:
        c.font = head_font; c.fill = fill; c.alignment = Alignment(horizontal='center')
    ws.freeze_panes = 'A2'
    for i, w in enumerate([14, 14, 12, 10, 26, 18, 10, 10, 10, 10, 12, 10, 10, 20], 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
    bio = io.BytesIO(); wb.save(bio)
    from urllib.parse import quote
    return Response(bio.getvalue(),
                    mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition':
                             "attachment; filename=export.xlsx; filename*=UTF-8''%s.xlsx" % quote(fn)})

def _stock_rows(kw, f, sort, dir_):
    w, args = [], []
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
    if sort in allowed and dir_:
        sql += " ORDER BY %s %s, name" % (allowed[sort], 'ASC' if dir_ == 'asc' else 'DESC')
    else:
        sql += " ORDER BY category, name"
    return db.q(sql, *args)

def _txn_rows(kw, d, kind, sort, dir_):
    sql = ("SELECT t.*, m.name, m.unit, m.code, m.category FROM txns t"
           " JOIN materials m ON m.id=t.material_id")
    w, args = [], []
    if d:
        w.append("t.tdate=?"); args.append(d)
    if kind:
        w.append("t.kind=?"); args.append(kind)
    if kw:
        w.append("(m.name LIKE ? OR m.code LIKE ? OR t.note LIKE ? OR m.category LIKE ?)")
        args += ['%%%s%%' % kw] * 4
    if w:
        sql += " WHERE " + " AND ".join(w)
    sql += " ORDER BY t.tdate DESC, t.id DESC LIMIT 2000"
    return db.q(sql, *args)

def _report_data(m):
    """月报数据。（调用前应用 safe_ym 清洗月份）
    月初结存 = 物料期初 + 该月之前所有单据的净额（不是固定的 materials.opening），
    这样才能保证：上月月末 == 本月月初，且当月月末 == 实时库存。
    """
    m = safe_ym(m)
    y, mo = int(m[:4]), int(m[5:7])
    days = calendar.monthrange(y, mo)[1]
    first, last = f'{m}-01', f'{m}-{days:02d}'
    # 用 v_mats（含停用物料）而不是 v_stock（只含启用）：
    # "停用"只是让它不再出现在录单和实时库存里，历史月份既然有单据，
    # 月报就必须照实反映，否则停用某物料后查旧月报会凭空少掉一批数据。
    mats = [dict(r) for r in db.q("SELECT * FROM v_mats ORDER BY category, name")]

    # 该月之前的累计净额（进 - 出），按物料汇总
    before = {}
    for r in db.q("SELECT material_id, kind, SUM(qty) q FROM txns WHERE tdate < ?"
                  " GROUP BY material_id, kind", first):
        before[r['material_id']] = before.get(r['material_id'], 0.0) +             (r['q'] if r['kind'] == '进' else -r['q'])

    raw = db.q("SELECT tdate,material_id,kind,SUM(qty) q FROM txns WHERE tdate BETWEEN ? AND ?"
               " GROUP BY tdate,material_id,kind", first, last)
    cell = {}
    for r in raw:
        cell[(r['material_id'], int(r['tdate'][8:10]), r['kind'])] = r['q']
    for mt in mats:
        mi = mo_ = 0.0
        mt['cells'] = []
        for dd in range(1, days + 1):
            a = cell.get((mt['id'], dd, '进'), 0); b = cell.get((mt['id'], dd, '出'), 0)
            mt['cells'].append((a, b)); mi += a; mo_ += b
        mt['min'], mt['mout'] = mi, mo_
        # 月初 = 档案期初 + 历史累计；月末 = 月初 + 本月进 - 本月出
        mt['opening'] = round(float(mt['opening'] or 0) + before.get(mt['id'], 0.0), 2)
        mt['ending'] = round(mt['opening'] + mi - mo_, 2)
    return mats, days

def csv_safe(v):
    """防 CSV 公式注入。

    单元格以 = + - @ 或制表符开头时，Excel / WPS 打开会当成公式执行
    （=HYPERLINK("http://evil.com","点我")、=cmd|'/c calc'!A1 都能触发）。
    这里给文本前面加一个单引号，Excel 当纯文本显示，肉眼看不出差别。
    数字原样返回，避免破坏数值。
    """
    if v is None:
        return ''
    if isinstance(v, (int, float)):
        return v
    t = str(v)
    if t[:1] in ('=', '+', '-', '@', '\t', '\r'):
        return "'" + t
    return t

@app.route('/export.csv')
def export_csv():
    kind = request.args.get('t', 'stock')
    if kind == 'stock':
        rows, head = db.stock_rows(), ['供应商','类型','规格','宽幅','物料名称','料号','单位','期初','入库','出库','当前库存','预警值','状态']
        data = [[csv_safe(r['supplier']),csv_safe(r['category']),csv_safe(r['spec']),
                 csv_safe(r['width']),csv_safe(r['name']),csv_safe(r['code']),csv_safe(r['unit']),
                 r['opening'],r['in_qty'],r['out_qty'],r['stock'],r['safety'],
                 csv_safe(r['status'])] for r in rows]
        fn = '库存'
    else:
        m = safe_ym(request.args.get('m'))
        rows = db.q("SELECT t.tdate,m.name,m.code,m.unit,t.kind,t.qty,t.note FROM txns t"
                    " JOIN materials m ON m.id=t.material_id WHERE t.tdate LIKE ? ORDER BY t.tdate,t.id", m+'%')
        head, data = ['日期','物料名称','料号','单位','类型','数量','备注'], \
            [[r['tdate'],csv_safe(r['name']),csv_safe(r['code']),csv_safe(r['unit']),
              r['kind'],r['qty'],csv_safe(r['note'])] for r in rows]
        fn = f'流水{m}'
    out = io.StringIO(); out.write('\ufeff')
    csv.writer(out).writerow(head); csv.writer(out).writerows(data)
    from urllib.parse import quote
    return Response(out.getvalue(), mimetype='text/csv; charset=utf-8',
                    headers={'Content-Disposition':
                             "attachment; filename=export.csv; filename*=UTF-8''%s.csv" % quote(fn)})

def open_browser_later(port, delay=1.2):
    """浏览器模式下自动打开浏览器"""
    import threading, webbrowser
    def go():
        time.sleep(delay)
        try:
            webbrowser.open('http://127.0.0.1:%d' % port)
        except Exception:
            pass
    threading.Thread(target=go, daemon=True).start()

def main():
    import desktop
    args = [a for a in sys.argv[1:]]
    # --browser 强制浏览器模式；--window 强制窗口模式
    force_browser = '--browser' in args
    force_window = '--window' in args
    args = [a for a in args if not a.startswith('--')]

    try:
        port = int(args[0]) if args else desktop.free_port()
    except ValueError:
        port = desktop.free_port()

    cleanup_tmp()
    try:
        st = db.stats()
    except Exception as e:          # 数据库损坏等致命错误
        # 数据库坏了：启动阶段必须给出可执行的恢复指引，
        # 否则窗口模式（无控制台）下用户只看到程序一闪而过，完全不知道发生了什么
        import desktop as _d
        msg = []
        msg.append('数据库打不开：%s' % e)
        for line in db.check_integrity():
            msg.append(line)
        msg.append('也可以把 %s 改名（比如加 .old），程序会自动新建一个空库，'
                   '再用备份还原。' % os.path.basename(db.DB_PATH))
        _d.log('启动失败：\n  ' + '\n  '.join(msg))
        say('')
        say('  !! 启动失败 !!')
        for m in msg:
            say('  ' + m)
        say('')
        say('  详细信息已写入 %s' % _d.LOG)
        # 打包成 exe 时停一会儿，让窗口来得及显示；源码运行直接退出
        if getattr(sys, 'frozen', False):
            time.sleep(30)
        return
    banner = [
        '-' * 46,
        '  仓库管理系统',
        '  数据: %s' % st['path'],
        '  物料 %d 种 · 单据 %d 条 · 预警 %d 项'
        % (st['materials'], st['txns'], st['alerts']),
    ]
    iss = db.check_integrity()
    if iss:
        banner.append('  [!] 数据体检发现 %d 个问题，访问 /sys 查看' % len(iss))

    frozen = getattr(sys, 'frozen', False)
    # 打包成 exe 默认开独立窗口；源码运行默认浏览器（方便调试）
    want_window = force_window or (frozen and not force_browser)

    if want_window and desktop.has_webview():
        banner.append('  窗口模式: 已启动独立窗口')
        banner.append('  日志: %s' % desktop.LOG)
        banner.append('-' * 46)
        for b in banner:
            say(b); desktop.log(b.strip())
        ok = desktop.run_window(app, port)
        if not ok:                       # 窗口起不来就退回浏览器
            say('  独立窗口启动失败，已退回浏览器模式')
            open_browser_later(port)
            app.run('127.0.0.1', port, debug=False, threaded=True)
    else:
        if want_window and not force_browser:
            banner.append('  提示: 缺少 pywebview，已用浏览器模式')
        banner.append('  访问: http://127.0.0.1:%d' % port)
        banner.append('  停止: 关掉这个窗口 或 Ctrl+C')
        banner.append('-' * 46)
        for b in banner:
            say(b)
        if frozen:
            open_browser_later(port)
        try:
            app.run('127.0.0.1', port, debug=False, threaded=True)
        except OSError as e:
            say('  启动失败：%s' % e)
            say('  端口 %d 可能被占用，换个端口：仓库管理系统.exe 9000' % port)
            if frozen:
                time.sleep(8)

if __name__ == '__main__':
    try:
        main()
    except Exception:
        # 窗口模式看不见控制台，出错必须落到文件里
        import traceback
        try:
            import desktop
            desktop.log('崩溃：\n' + traceback.format_exc())
        except Exception:
            pass
        try:
            traceback.print_exc()
        except Exception:
            say('  （错误详情无法打印，已写入 warehouse.log）')
        time.sleep(10)
    finally:
        try:
            db.close()
        except Exception:
            pass
        if getattr(sys, 'frozen', False):
            time.sleep(1.5)
