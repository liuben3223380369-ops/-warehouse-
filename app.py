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
from urllib.parse import quote
import db, importer, purchase


def _log_err(tag, detail=''):
    """把错误写进程序目录的 warehouse.log。
    窗口模式没有控制台，异常不落盘就永远查不到原因。"""
    try:
        import traceback
        with open(os.path.join(BASE, 'warehouse.log'), 'a', encoding='utf-8',
                  errors='replace') as f:
            f.write('[%s] %s\n%s\n%s\n' % (
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'), tag,
                detail or traceback.format_exc(), '-' * 46))
    except Exception:
        pass


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

# ---------- 重复提交防护（一次性令牌） ----------
# 场景：网络卡顿时用户连点两下"保存"，同一批单据会记两遍，
# 库存平白多出一笔，而且很难发现。所以每个表单发一个一次性令牌，
# 提交时核销；令牌用掉再提交就是重复，直接挡下。
_nonces = set()
_NONCE_MAX = 500          # 上限：防止开着几十个页面把内存撑大


def new_nonce():
    """发一个新令牌（渲染表单时调用）"""
    import uuid
    n = uuid.uuid4().hex[:16]
    _nonces.add(n)
    if len(_nonces) > _NONCE_MAX:      # 超量就淘汰最早的一批
        for x in list(_nonces)[:_NONCE_MAX // 2]:
            _nonces.discard(x)
    return n


def take_nonce(n):
    """核销令牌：有效返回 True（并作废），重复/伪造返回 False"""
    if not n:
        return False
    if n in _nonces:
        _nonces.discard(n)
        return True
    return False
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

TXN_PAGE = 500       # 流水页单页最多显示条数
# 金额不单独存库：只存单价，金额 = 数量 × 单价，查询时现算。
# 存两份的话改了数量金额还是旧值，必然对不上。
AMT = "ROUND(t.qty * COALESCE(t.price,0), 2)" 

LABELS = {'name': '物料名称', 'code': '料号', 'supplier': '供应商', 'category': '类型',
          'spec': '规格', 'width': '宽幅', 'unit': '单位', 'status': '状态',
          'opening': '期初结存', 'safety': '安全库存'}
app.jinja_env.globals.update(LABELS=LABELS, new_nonce=new_nonce)

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

def _last_prices():
    """每种物料最近一次填过的单价，用于录单时自动带出（省得每次重填）"""
    out = {}
    try:
        for r in db.q("SELECT material_id, price FROM txns t WHERE price IS NOT NULL"
                      " AND id=(SELECT MAX(id) FROM txns WHERE material_id=t.material_id"
                      " AND price IS NOT NULL)"):
            out[r['material_id']] = r['price']
    except Exception:
        pass
    return out

def js_mats_with_price(mats):
    lp = _last_prices()
    out = []
    for m in mats:
        d = {k: m[k] for k in ('id', 'name', 'code', 'supplier', 'category',
                               'spec', 'width', 'unit', 'stock')}
        p = lp.get(m['id'])
        if p:
            d['price'] = p
        out.append(d)
    return out

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
    recent  = db.q("SELECT t.*, m.name, m.unit, m.code, %s AS amount FROM txns t"
                   " JOIN materials m ON m.id=t.material_id"
                   " ORDER BY t.id DESC LIMIT 8" % AMT.replace('t.', 't.'))
    # 本月进出金额（只统计填了单价的单据）
    _amt = "SELECT COALESCE(SUM(qty*COALESCE(price,0)),0) s FROM txns WHERE tdate LIKE ? AND kind=?"
    mon_amt_in  = db.q(_amt, m + '%', '进')[0]['s']
    mon_amt_out = db.q(_amt, m + '%', '出')[0]['s']
    return render_template('index.html', day_in=day_in, day_out=day_out, mon_in=mon_in,
                           mon_out=mon_out, n_mat=n_mat, alerts=alerts, recent=recent,
                           mon_amt_in=mon_amt_in, mon_amt_out=mon_amt_out)

# ---------- 单据流水（表格录入，含原表 A-G 全部列） ----------
def _fingerprint(vals):
    """物料唯一指纹：名称 + 规格 + 宽幅（+供应商，若三者都相同仍冲突时区分）。

    原表里同一个「0.05金」可能有 0.04 / 0.045 / 0.05 三种宽幅的批次，
    「0.05胶」甚至分属两个不同供应商。只按名称匹配会把它们并成一个物料，
    规格信息全部丢失、库存张冠李戴。带上规格和宽幅才能唯一定位。
    """
    def c(k):
        return (str(vals.get(k) or '').strip())
    parts = [c('name')]
    sp, wd = c('spec'), c('width')
    if sp: parts.append('规格' + sp)
    if wd: parts.append('宽' + wd)
    return '|'.join(parts)

def _resolve_material(vals):
    """按 料号 -> 名称+规格指纹 匹配物料；匹配到则用行内 A-G 值同步档案，否则新建。
    返回 (material_id, is_new)"""
    name = (vals.get('name') or '').strip()
    code = (vals.get('code') or '').strip()
    hit = None
    if code:
        hit = db.q("SELECT id FROM materials WHERE code=? AND code<>''", code)
    if not hit and name:
        # 先按 名称+规格+宽幅 精确匹配：同名不同规格是两种物料
        spec = (vals.get('spec') or '').strip()
        width = (vals.get('width') or '').strip()
        if spec or width:
            hit = db.q("SELECT id FROM materials WHERE name=? AND COALESCE(spec,'')=?"
                       " AND COALESCE(width,'')=?", name, spec, width)
        if not hit:
            # 没有规格信息（或表里没填）时才退回只按名称
            hit = db.q("SELECT id FROM materials WHERE name=? AND COALESCE(spec,'')=''"
                       " AND COALESCE(width,'')=''", name) if not (spec or width) else None
        if not hit and not (spec or width):
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
        _back = 'out' if ep == 'out' else ('in' if ep == 'in' else 'txn')
        # 一次性令牌：挡住"网络卡顿时连点两下"造成的重复记账
        if not take_nonce(request.form.get('_n')):
            return redirect(url_for(_back,
                msg='这一批已经保存过了，请不要重复提交（可去流水页核对）'))
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
                # 单价不在录单页填 —— 入库成本来自采购单，采购到货时写入
                _cx.execute("INSERT INTO txns(tdate,material_id,kind,qty,pieces,per_piece,"
                       "note,created_at) VALUES(?,?,?,?,?,?,?,?)",
                       (d, mid, kind, qty, pieces, per, note,
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
                saved += 1
        except Exception:
            # 不能静默吞掉：窗口模式没有控制台，不落盘就永远查不到原因
            _log_err('录单保存失败')
            return render_template('error.html', code=500, title='保存失败',
                detail='这批单据一条都没保存（已回滚），请返回重试。'
                       '错误详情已写入 warehouse.log。'), 500
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
                           # sqlite3.Row 不能直接 tojson，前端只需要 fid/label 两列
                           col_defs=[{'fid': c['fid'], 'label': c['label']} for c in cols],
                           js_mats=js_mats_with_price(mats))

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
    sql = ("SELECT t.*, m.name, m.unit, m.code, %s AS amount FROM txns t"
           " JOIN materials m ON m.id=t.material_id WHERE t.kind=?" % AMT)
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
            if not take_nonce(request.form.get('_n')):
                return redirect(url_for(back,
                    msg='这批单据已经导入过了，请不要重复提交'))
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
                  db.run("INSERT INTO txns(tdate,material_id,kind,qty,pieces,per_piece,price,"
                         "note,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                         d.get('date') or dflt_date, mid, kind, qty, pc, per,
                         (num(d.get('price')) or None), d.get('note', ''),
                         datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                  saved += 1
                  if kind == '进': n_in += 1
                  else: n_out += 1
            except Exception as e:
                try: os.remove(p)
                except OSError: pass
                return render_template('txn_import.html', fixed=fixed,
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
                               yms=(diag or {}).get('yms') or {},
                               skipped_sum=(diag or {}).get('skipped_sum') or 0,
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
    # 金额合计（只统计填了单价的单据）
    amt_in = sum(float(r['amount'] or 0) for r in rows if r['kind'] == '进')
    amt_out = sum(float(r['amount'] or 0) for r in rows if r['kind'] == '出')
    return render_template('history.html', row=row, rows=rows, m=m, months=months,
                           amt_in=amt_in, amt_out=amt_out,
                           total_amount=amt_in - amt_out)

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

@app.route('/txns/batch', methods=['POST'])
def txns_batch():
    """流水批量删除：勾谁删谁，也可按当前筛选条件一键清空。
    整批放在一个事务里，中途出错全部回滚，不会删一半。"""
    ids = ints(request.form, 'id')
    clear = request.form.get('clear') == '1'
    back = request.form.get('back') or ''
    try:
        with db.tx() as c:
            if clear:
                # 按当前页面筛选条件删（日期 / 月份 / 类型 / 搜索词），
                # 与列表页看到的结果保持一致，避免"看到的和删掉的不是一批"
                d = (request.form.get('d') or '').strip()
                m = safe_ym(request.form.get('m')) if request.form.get('m') else ''
                kind = (request.form.get('kind') or '').strip()
                kw = clean_kw(request.form.get('kw'))
                w, args = [], []
                if d:
                    w.append("t.tdate=?"); args.append(d)
                elif m:
                    w.append("t.tdate LIKE ?"); args.append(m + '%')
                if kind in ('进', '出'):
                    w.append("t.kind=?"); args.append(kind)
                if kw:
                    w.append("(m.name LIKE ? OR m.code LIKE ? OR t.note LIKE ? OR m.category LIKE ?)")
                    args += ['%%%s%%' % kw] * 4
                if w:
                    sql = ("DELETE FROM txns WHERE id IN (SELECT t.id FROM txns t"
                           " JOIN materials m ON m.id=t.material_id WHERE " + " AND ".join(w) + ")")
                else:
                    sql = "DELETE FROM txns"
                # 安全闸门：列表只显示前 TXN_PAGE 条，但条件删除会删掉全部。
                # 必须显式传 real_total 授权，否则最多只删一页，杜绝"以为删500实际删2万"。
                try:
                    declared = int(request.form.get('real_total') or 0)
                except ValueError:
                    declared = 0
                cur = c.execute("SELECT COUNT(*) FROM txns t JOIN materials m"
                                " ON m.id=t.material_id"
                                + (" WHERE " + " AND ".join(w) if w else ""),
                                tuple(args)).fetchone()[0]
                if declared != cur:
                    # 条件实际命中数与页面声明的不一致（数据已变化/参数被改），拒绝执行
                    raise ValueError('count-mismatch')
                if cur > TXN_PAGE:
                    raise ValueError('too-many')
                cur = c.execute(sql, tuple(args))
                n = cur.rowcount if cur.rowcount and cur.rowcount > 0 else declared
            else:
                if not ids:
                    return redirect((back or url_for('txns')) + '?msg=' + quote('未勾选任何单据'))
                ph = ','.join('?' * len(ids))
                c.execute("DELETE FROM txns WHERE id IN (%s)" % ph, tuple(ids))
                n = len(ids)
    except ValueError as ex:
        msg = ('删除已取消：要删的数量超过一页上限 %d 条。'
               '请先按日期或月份缩小范围，再清空。' % TXN_PAGE) if str(ex) == 'too-many' \
            else '删除已取消：数据量与页面不符，请刷新页面后重试。'
        return redirect((back or url_for('txns')) + '?msg=' + quote(msg))
    except Exception:
        return redirect((back or url_for('txns')) + '?msg=' + quote('删除失败，已回滚，请重试'))
    return redirect((back or url_for('txns')) + '?msg=' + quote('已删除 %d 条单据' % n))

@app.route('/txns')
def txns():
    d = request.args.get('d') or ''
    kind = request.args.get('kind') or ''
    kw = clean_kw(request.args.get('kw'))
    sort = request.args.get('sort') or ''
    dir_ = request.args.get('dir') or ''
    sql = ("SELECT t.*, m.name, m.unit, m.code, m.category, %s AS amount FROM txns t"
           " JOIN materials m ON m.id=t.material_id" % AMT)
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
               'pieces': 'COALESCE(t.pieces,0)', 'note': 't.note',
               'price': 'COALESCE(t.price,0)', 'amount': AMT}
    if sort in allowed and dir_:
        sql += " ORDER BY %s %s, t.id DESC" % (allowed[sort], 'ASC' if dir_ == 'asc' else 'DESC')
    else:
        sql += " ORDER BY t.tdate DESC, t.id DESC"
    # 真实总数必须单独查：列表 LIMIT 500，用户只看到 500 条，
    # 但"清空当前筛选"删的是筛选条件的全部。若把 500 当成总数，
    # 用户以为删 500 条，实际可能删掉几万条 —— 这是灾难性误删。
    csql = "SELECT COUNT(*) FROM txns t JOIN materials m ON m.id=t.material_id"
    if w:
        csql += " WHERE " + " AND ".join(w)
    real_total = db.q(csql, *args)[0][0]

    sql += " LIMIT %d" % TXN_PAGE
    rows = db.q(sql, *args)
    return render_template('txns.html', rows=rows, d=d, kind=kind or None, m='', kw=kw,
                           total=0, count=len(rows), real_total=real_total,
                           capped=(real_total > len(rows)),
                           url_kind='txns',
                           cur_sort=sort, cur_dir=dir_, qs={'d': d, 'kind': kind, 'kw': kw},
                           msg=request.args.get('msg', ''))

# ---------- 物料档案 ----------
@app.route('/materials')
def materials():
    kw = clean_kw(request.args.get('kw'))
    # 默认显示全部（含停用）。以前默认只看启用，用户会以为"物料少了"，
    # 停用只是不参与录单和实时库存，档案本身不该凭空消失。
    show_all = request.args.get('all') != '0'
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
    n_off = db.q("SELECT COUNT(*) c FROM materials WHERE active=0")[0]['c']
    n_all = db.q("SELECT COUNT(*) c FROM materials")[0]['c']
    return render_template('materials.html', rows=rows, kw=kw, inline=request.args.get('edit') == '1',
                           show_all=show_all, n_off=n_off, n_all=n_all,
                           cur_sort=sort, cur_dir=dir_,
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
@app.route('/materials/table', methods=['POST'])
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

@app.route('/import', methods=['GET', 'POST'])
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
        ws.append(['日期', '物料名称', '料号', '类型', '进/出', '数量', '件数', '每件',
                   '单位', '单价', '金额', '备注'])
        for r in rows:
            ws.append([r['tdate'], r['name'], r['code'], r['category'], r['kind'],
                       r['qty'], r['pieces'], r['per_piece'], r['unit'],
                       r['price'], (r['amount'] if r['price'] else None), r['note']])
        fn = ('出入库流水' + (d or m))
    for c in ws[1]:
        c.font = head_font; c.fill = fill; c.alignment = Alignment(horizontal='center')
    ws.freeze_panes = 'A2'
    for i, w in enumerate([14, 14, 12, 10, 26, 18, 10, 10, 10, 10, 12, 10, 10, 20, 20], 1):
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
    sql = ("SELECT t.*, m.name, m.unit, m.code, m.category, %s AS amount FROM txns t"
           " JOIN materials m ON m.id=t.material_id" % AMT)
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

@app.route('/export/po.xlsx')
def export_po_xlsx():
    """采购台账导出。

    三种表：orders=采购单汇总 / items=明细（含未到货量）/ recv=到货流水 / pay=付款流水
    之前只有库存/流水/月报能导出，采购数据导不出来，
    月底对账、发给供应商核对都得手工抄，这里补齐。
    """
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    kind = request.args.get('t', 'items')
    st = request.args.get('st') or ''
    sup = (request.args.get('sup') or '').strip()
    kw = clean_kw(request.args.get('kw'))
    m = request.args.get('m') or ''

    w, a = [], []
    if st:
        w.append("p.status=?"); a.append(st)
    if sup:
        w.append("p.supplier=?"); a.append(sup)
    if kw:
        w.append("(p.pono LIKE ? OR p.supplier LIKE ? OR p.note LIKE ?)")
        a += ['%%%s%%' % kw] * 3
    if m:
        w.append("p.odate LIKE ?"); a.append(m + '%')
    where = (" WHERE " + " AND ".join(w)) if w else ""

    wb = openpyxl.Workbook(); ws = wb.active
    hf = Font(bold=True, color='FFFFFF')
    fill = PatternFill('solid', start_color='1F6FEB')
    # 公式注入防护：= + - @ 开头的文本会被 Excel 当公式执行
    def cv(v):
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return v
        s = str(v)
        return ("'" + s) if s[:1] in ('=', '+', '-', '@') else s

    if kind == 'items':
        ws.title = '采购明细'
        ws.append(['采购单号', '日期', '交期', '供应商', '物料名称', '规格', '单位',
                   '订购数', '单价', '金额', '已到货', '未到货', '状态', '备注'])
        sql = ("SELECT p.pono,p.odate,p.ddate,p.supplier,i.name,i.spec,i.unit,"
               " i.qty,i.price,i.note,p.status,"
               " COALESCE(SUM(r.qty),0) rq FROM po_items i"
               " JOIN pos p ON p.id=i.po_id"
               " LEFT JOIN po_receipts r ON r.item_id=i.id"
               + where + " GROUP BY i.id ORDER BY p.odate DESC, i.id")
        for r in db.q(sql, *a):
            q = float(r['qty'] or 0); rq = float(r['rq'] or 0)
            ws.append([cv(r['pono']), cv(r['odate']), cv(r['ddate']), cv(r['supplier']),
                       cv(r['name']), cv(r['spec']), cv(r['unit']),
                       q, float(r['price'] or 0), round(q * float(r['price'] or 0), 2),
                       rq, round(q - rq, 2), cv(r['status']), cv(r['note'])])
        fn = '采购明细'
    elif kind == 'recv':
        ws.title = '到货流水'
        ws.append(['到货日期', '采购单号', '供应商', '物料名称', '规格', '单位',
                   '到货数', '单价', '金额', '备注'])
        sql = ("SELECT r.rdate,p.pono,p.supplier,i.name,i.spec,i.unit,"
               " r.qty,r.price,r.note FROM po_receipts r"
               " JOIN po_items i ON i.id=r.item_id JOIN pos p ON p.id=i.po_id"
               + where + " ORDER BY r.rdate DESC, r.id DESC")
        for r in db.q(sql, *a):
            q = float(r['qty'] or 0); pr = float(r['price'] or 0)
            ws.append([cv(r['rdate']), cv(r['pono']), cv(r['supplier']), cv(r['name']),
                       cv(r['spec']), cv(r['unit']), q, pr, round(q * pr, 2), cv(r['note'])])
        fn = '到货流水'
    elif kind == 'pay':
        ws.title = '付款流水'
        ws.append(['付款日期', '采购单号', '供应商', '金额', '方式', '备注'])
        sql = ("SELECT y.pdate,p.pono,p.supplier,y.amount,y.method,y.note"
               " FROM po_payments y JOIN pos p ON p.id=y.po_id"
               + where + " ORDER BY y.pdate DESC, y.id DESC")
        for r in db.q(sql, *a):
            ws.append([cv(r['pdate']), cv(r['pono']), cv(r['supplier']),
                       float(r['amount'] or 0), cv(r['method']), cv(r['note'])])
        fn = '付款流水'
    else:
        ws.title = '采购单'
        ws.append(['采购单号', '日期', '交期', '供应商', '状态', '税率%',
                   '订购金额', '税额', '价税合计', '已付', '欠款', '备注'])
        sql = ("SELECT p.*, COALESCE(SUM(i.qty*i.price),0) amt FROM pos p"
               " LEFT JOIN po_items i ON i.po_id=p.id"
               + where + " GROUP BY p.id ORDER BY p.odate DESC, p.id DESC")
        for r in db.q(sql, *a):
            tr = float(r['tax_rate'] or 0)
            amt = round(float(r['amt'] or 0), 2)
            _, tax, total = purchase.line_amount(1, amt, tr)
            paid = purchase.paid_amount(r['id'])
            _, recv_total = purchase.owed(r['id'])
            ws.append([cv(r['pono']), cv(r['odate']), cv(r['ddate']), cv(r['supplier']),
                       cv(r['status']), tr, amt, round(tax, 2), round(total, 2),
                       paid, round(recv_total - paid, 2), cv(r['note'])])
        fn = '采购单汇总'

    for cc in ws[1]:
        cc.font = hf; cc.fill = fill; cc.alignment = Alignment(horizontal='center')
    ws.freeze_panes = 'A2'
    for i, wd in enumerate([16, 12, 12, 14, 20, 12, 8, 10, 12, 10, 10, 10, 10, 18], 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = wd
    bio = io.BytesIO(); wb.save(bio)
    from urllib.parse import quote
    if m:
        fn += m
    return Response(bio.getvalue(),
                    mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition':
                             "attachment; filename=export.xlsx; filename*=UTF-8''%s.xlsx" % quote(fn)})


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
        rows = db.q("SELECT t.tdate,m.name,m.code,m.unit,t.kind,t.qty,t.price,"
                    " %s AS amount, t.note FROM txns t"
                    " JOIN materials m ON m.id=t.material_id WHERE t.tdate LIKE ?"
                    " ORDER BY t.tdate,t.id"
                    % AMT.replace('t.', 't.'), m + '%')
        head, data = ['日期','物料名称','料号','单位','类型','数量','单价','金额','备注'], \
            [[r['tdate'],csv_safe(r['name']),csv_safe(r['code']),csv_safe(r['unit']),
              r['kind'],r['qty'],r['price'],(r['amount'] if r['price'] else None),
              csv_safe(r['note'])] for r in rows]
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

# ==================== 采购台账（与仓库库存分离，靠到货单联动） ====================
@app.route('/purchase')
def purchase_home():
    """采购首页：看板 + 采购单列表"""
    st = request.args.get('st') or ''
    sup = (request.args.get('sup') or '').strip()
    kw = clean_kw(request.args.get('kw'))
    w, a = [], []
    if st:
        w.append("p.status=?"); a.append(st)
    if sup:
        w.append("p.supplier=?"); a.append(sup)
    if kw:
        w.append("(p.pono LIKE ? OR p.supplier LIKE ? OR p.note LIKE ?)")
        a += ['%%%s%%' % kw] * 3
    sql = ("SELECT p.*, COALESCE(SUM(i.qty),0) tq, COALESCE(SUM(i.qty*i.price),0) amt,"
           " COALESCE(SUM(i.recv_qty),0) rq FROM pos p LEFT JOIN po_items i ON i.po_id=p.id")
    if w:
        sql += " WHERE " + " AND ".join(w)
    sql += " GROUP BY p.id ORDER BY p.odate DESC, p.id DESC LIMIT 300"
    rows = []
    for r in db.q(sql, *a):
        d = dict(r)
        d['tax_rate'] = float(d['tax_rate'] or 0)
        _, tax, total = purchase.line_amount(1, d['amt'], d['tax_rate'])
        d['amount'] = round(float(d['amt'] or 0), 2)
        d['tax'] = tax
        d['total'] = round(float(d['amt'] or 0) + tax, 2)
        d['paid'] = purchase.paid_amount(d['id'])
        d['open_qty'] = round(float(d['tq'] or 0) - float(d['rq'] or 0), 2)
        today_s = today()
        d['late'] = bool(d['ddate'] and d['ddate'] < today_s
                         and d['status'] in ('已下单', '部分到货'))
        rows.append(d)
    sups = [r['name'] for r in db.q(
        "SELECT DISTINCT supplier name FROM pos ORDER BY supplier")]
    return render_template('purchase.html', rows=rows, st=st, sup=sup, kw=kw,
                           sups=sups, dash=purchase.dashboard(),
                           STATUS=purchase.STATUS)


@app.route('/po/new', methods=['GET', 'POST'])
def po_new():
    """新建采购单（含明细）"""
    if request.method == 'POST':
        # 一次性令牌：挡住连点造成的重复建单
        if not take_nonce(request.form.get('_n')):
            return redirect(url_for('purchase_home',
                msg='这个单子已经建过了，请不要重复提交'))
        supplier = (request.form.get('supplier') or '').strip()
        odate = safe_date(request.form.get('odate'))
        if not supplier:
            return render_template('po_new.html', err='供应商必填',
                                   mats=_mat_choices(), today=today(),
                                   sups=_sup_names(), units=db.unit_choices())
        ddate = (request.form.get('ddate') or '').strip()
        if ddate and not is_date(ddate):
            ddate = ''
        tax = num(request.form.get('tax_rate'), default=0, lo=0, hi=100)
        note = (request.form.get('note') or '').strip()
        names = request.form.getlist('item_name')
        qtys = request.form.getlist('item_qty')
        prices = request.form.getlist('item_price')
        units = request.form.getlist('item_unit')
        specs = request.form.getlist('item_spec')
        mids = request.form.getlist('item_mid')
        convs = request.form.getlist('item_conv')
        sunits = request.form.getlist('item_stock_unit')
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        try:
            with db.tx() as c:
                pono = purchase.next_pono(odate)
                po_id = c.execute("INSERT INTO pos(pono,supplier,odate,ddate,status,"
                                  "tax_rate,note,created_at) VALUES(?,?,?,?,?,?,?,?)",
                                  (pono, supplier, odate, ddate,
                                   request.form.get('status') or '已下单',
                                   tax, note, now)).lastrowid
                n = 0
                for i in range(len(names)):
                    nm = (names[i] or '').strip()
                    q = num(qtys[i] if i < len(qtys) else 0)
                    p = num(prices[i] if i < len(prices) else 0)
                    if not nm or q <= 0:
                        continue
                    mid = None
                    if i < len(mids) and str(mids[i]).strip().isdigit():
                        mid = int(mids[i])
                    cv = num(convs[i] if i < len(convs) else 1, default=1)
                    if cv <= 0:
                        cv = 1.0
                    c.execute("INSERT INTO po_items(po_id,material_id,name,spec,unit,conv,"
                              "stock_unit,qty,price) VALUES(?,?,?,?,?,?,?,?,?)",
                              (po_id, mid, nm,
                               (specs[i] if i < len(specs) else '').strip(),
                               (units[i] if i < len(units) else '').strip() or '个',
                               cv,
                               (sunits[i] if i < len(sunits) else '').strip(),
                               q, p))
                    n += 1
        except Exception:
            _log_err('采购单保存失败')
            return render_template('po_new.html', err='保存失败，请重试（详情见 warehouse.log）',
                                   mats=_mat_choices(), today=today(), sups=_sup_names())
        if n == 0:
            db.run("DELETE FROM pos WHERE id=?", (po_id,))
            return render_template('po_new.html', err='至少要填一行物料（名称和数量）',
                                   mats=_mat_choices(), today=today(), sups=_sup_names())
        purchase.touch_supplier(supplier)
        return redirect(url_for('po_detail', po_id=po_id,
                                msg='采购单 %s 已创建，%d 条明细' % (pono, n)))
    return render_template('po_new.html', mats=_mat_choices(), today=today(),
                           sups=_sup_names(), units=db.unit_choices())


def _mat_choices():
    return [dict(id=m['id'], name=m['name'], spec=m['spec'] or '',
                 unit=m['unit'] or '', code=m['code'] or '')
            for m in db.q("SELECT id,name,spec,unit,code FROM materials"
                          " WHERE active=1 ORDER BY name")]


def _sup_names():
    return [r['name'] for r in db.q("SELECT name FROM suppliers WHERE active=1 ORDER BY name")]


@app.route('/po/<int:po_id>')
def po_detail(po_id):
    po = db.q("SELECT * FROM pos WHERE id=?", po_id)
    if not po:
        return render_template('error.html', code=404, title='找不到采购单',
                               detail='它可能已被删除。'), 404
    po = po[0]
    items = []
    for it in db.q("SELECT * FROM po_items WHERE po_id=? ORDER BY id", po_id):
        d = dict(it)
        a, t, tt = purchase.line_amount(d['qty'], d['price'], po['tax_rate'])
        d['amount'], d['tax'], d['total'] = a, t, tt
        d['remain'] = round(float(d['qty']) - float(d['recv_qty']), 2)
        d['recv_amount'] = float(db.q(
            "SELECT COALESCE(SUM(qty*price),0) s FROM po_receipts WHERE item_id=?",
            d['id'])[0]['s'] or 0)
        d['done'] = d['remain'] <= 1e-9
        items.append(d)
    recs = db.q("SELECT r.*, i.name, i.unit FROM po_receipts r JOIN po_items i"
                " ON i.id=r.item_id WHERE i.po_id=? ORDER BY r.rdate DESC, r.id DESC", po_id)
    pays = db.q("SELECT * FROM po_payments WHERE po_id=? ORDER BY pdate DESC, id DESC", po_id)
    t = purchase.po_totals(po_id)
    owed_amt, recv_total = purchase.owed(po_id)
    return render_template('po.html', po=po, items=items, recs=recs, pays=pays,
                           t=t, paid=purchase.paid_amount(po_id),
                           owed=owed_amt, recv_total=recv_total,
                           STATUS=purchase.STATUS, today=today(), mats=_mat_choices(),
                           units=db.unit_choices(),
                           msg=request.args.get('msg', ''), err=request.args.get('err', ''))


@app.route('/po/<int:po_id>/status', methods=['POST'])
def po_status(po_id):
    st = (request.form.get('status') or '').strip()
    if st in purchase.STATUS:
        db.run("UPDATE pos SET status=? WHERE id=?", st, po_id)
    return redirect(url_for('po_detail', po_id=po_id, msg='状态已改为「%s」' % st))


@app.route('/po/<int:po_id>/item/add', methods=['POST'])
def po_item_add(po_id):
    nm = (request.form.get('name') or '').strip()
    q = num(request.form.get('qty'))
    p = num(request.form.get('price'))
    if not nm or q <= 0:
        return redirect(url_for('po_detail', po_id=po_id, err='物料名称和数量都要填'))
    mid = request.form.get('material_id')
    mid = int(mid) if str(mid or '').isdigit() else None
    cv = num(request.form.get('conv'), default=1)
    if cv <= 0:
        cv = 1.0
    db.run("INSERT INTO po_items(po_id,material_id,name,spec,unit,conv,stock_unit,"
           "qty,price,note) VALUES(?,?,?,?,?,?,?,?,?,?)", po_id, mid, nm,
           (request.form.get('spec') or '').strip(),
           (request.form.get('unit') or '').strip() or '个', cv,
           (request.form.get('stock_unit') or '').strip(), q, p,
           (request.form.get('note') or '').strip())
    purchase.refresh_status(po_id)
    return redirect(url_for('po_detail', po_id=po_id, msg='已加入 %s' % nm))


@app.route('/po/item/unit', methods=['POST'])
def po_item_unit():
    """随时改采购单位 / 换算率 / 库存单位 / 单价。
    改单位只影响之后的到货，已入库存量不动（历史记的是当时实际入库数）。"""
    iid = request.form.get('item_id')
    if not str(iid or '').isdigit():
        return redirect(url_for('purchase_home'))
    it = db.q("SELECT po_id FROM po_items WHERE id=?", int(iid))
    if not it:
        return redirect(url_for('purchase_home'))
    pid = it[0]['po_id']
    ok, msg = purchase.set_item_unit(
        int(iid),
        unit=request.form.get('unit'),
        conv=request.form.get('conv'),
        stock_unit=request.form.get('stock_unit'),
        price=request.form.get('price'))
    return redirect(url_for('po_detail', po_id=pid,
                            msg=msg if ok else '', err='' if ok else msg))


@app.route('/po/item/del/<int:iid>')
def po_item_del(iid):
    it = db.q("SELECT po_id FROM po_items WHERE id=?", iid)
    if it:
        pid = it[0]['po_id']
        ok, msg = purchase.delete_item(iid)
        return redirect(url_for('po_detail', po_id=pid,
                                msg=msg if ok else '', err='' if ok else msg))
    return redirect(url_for('purchase_home'))


@app.route('/po/<int:po_id>/receive', methods=['POST'])
def po_receive(po_id):
    iid = request.form.get('item_id')
    if not str(iid or '').isdigit():
        return redirect(url_for('po_detail', po_id=po_id, err='请选择要收货的物料'))
    rdate = safe_date(request.form.get('rdate'))
    q = request.form.get('qty')
    p = request.form.get('price')
    ok, msg = purchase.receive(int(iid), rdate, q, p,
                               (request.form.get('note') or '').strip())
    return redirect(url_for('po_detail', po_id=po_id,
                            msg=msg if ok else '', err='' if ok else msg))


@app.route('/po/receive/del/<int:rid>')
def po_receive_del(rid):
    r = db.q("SELECT i.po_id FROM po_receipts r JOIN po_items i ON i.id=r.item_id"
             " WHERE r.id=?", rid)
    if r:
        pid = r[0]['po_id']
        ok, msg = purchase.unreceive(rid)
        return redirect(url_for('po_detail', po_id=pid,
                                msg=msg if ok else '', err='' if ok else msg))
    return redirect(url_for('purchase_home'))


@app.route('/po/<int:po_id>/pay', methods=['POST'])
def po_pay(po_id):
    amt = num(request.form.get('amount'))
    if amt <= 0:
        return redirect(url_for('po_detail', po_id=po_id, err='付款金额要大于 0'))
    db.run("INSERT INTO po_payments(po_id,pdate,amount,method,note,created_at)"
           " VALUES(?,?,?,?,?,?)", po_id, safe_date(request.form.get('pdate')),
           amt, (request.form.get('method') or '转账').strip(),
           (request.form.get('note') or '').strip(),
           datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    return redirect(url_for('po_detail', po_id=po_id, msg='已登记付款 %.2f' % amt))


@app.route('/po/pay/del/<int:pid>')
def po_pay_del(pid):
    r = db.q("SELECT po_id FROM po_payments WHERE id=?", pid)
    if r:
        p = r[0]['po_id']
        db.run("DELETE FROM po_payments WHERE id=?", pid)
        return redirect(url_for('po_detail', po_id=p, msg='付款记录已删除'))
    return redirect(url_for('purchase_home'))


@app.route('/po/del/<int:po_id>')
def po_del(po_id):
    # ON DELETE CASCADE 会带走明细；但要先把到货生成的入库单撤掉，
    # 否则采购单没了、库存却还留着那批货。
    recs = db.q("SELECT r.id FROM po_receipts r JOIN po_items i ON i.id=r.item_id"
                " WHERE i.po_id=?", po_id)
    for r in recs:
        purchase.unreceive(r['id'])
    db.run("DELETE FROM pos WHERE id=?", po_id)
    return redirect(url_for('purchase_home', msg='采购单已删除，相关入库单已同步撤销'))


@app.route('/suppliers')
def suppliers():
    return render_template('suppliers.html', rows=purchase.supplier_list(),
                           msg=request.args.get('msg', ''))


@app.route('/suppliers/add', methods=['POST'])
def supplier_add():
    nm = (request.form.get('name') or '').strip()
    if not nm:
        return redirect(url_for('suppliers', msg='供应商名称必填'))
    purchase.touch_supplier(nm)
    db.run("UPDATE suppliers SET contact=?, phone=?, note=? WHERE name=?",
           (request.form.get('contact') or '').strip(),
           (request.form.get('phone') or '').strip(),
           (request.form.get('note') or '').strip(), nm)
    return redirect(url_for('suppliers', msg='已保存 %s' % nm))


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
