# -*- coding: utf-8 -*-
"""表格模块 · 电子表格页路由（类 Excel / WPS 的制表台）

页面
    /sheet              工作簿列表
    /sheet/<id>         打开一本工作簿（网格 + 公式栏 + 工具栏 + 表标签）

数据接口（全部 POST JSON，返回 JSON）
    api/grid            取一块区域用于渲染
    api/set             写入若干格
    api/fill            向下/右/上/左填充
    api/rows api/cols   插入删除行列
    api/sort api/filter 排序与筛选
    api/style           刷样式（粗体/颜色/对齐/数字格式/边框）
    api/merge           合并拆分单元格
    api/freeze          冻结窗格
    api/sheets          表的增删改序
    api/undo api/redo   撤销重做
    api/find            查找替换
    api/names           命名区域
    api/save            立即落库

所有改动即时写库，刷新/重启不丢。
"""
import os
import re
import json
import datetime
import urllib.parse

from flask import request, jsonify, render_template, redirect, url_for, Response

from ..core import db, util
from ..core.router import Router
from ..core.util import new_nonce
from . import sp_engine as E
from . import batch, ref
from . import sp_group as GR
from . import sp_io as IO
from . import sp_style as ST
from . import sp_funcs as F
from .sp_extra import EXTRA, check_validation

bp = Router('sheet')

# 打开中的工作簿：{id: {'book': Workbook, 'undo': [], 'redo': []}}
_LIVE = {}
MAX_UNDO = 60


# ------------------------------------------------------------------ 存取
def _load(bid):
    st = _LIVE.get(bid)
    if st:
        return st['book'], st
    row = db.q("SELECT * FROM wb WHERE id=?", bid)
    if not row:
        return None, None
    try:
        book = E.Workbook.from_dict(json.loads(row[0]['data'] or '{}'))
    except Exception:
        book = E.Workbook('工作簿')
        book.add('Sheet1')
    st = {'book': book, 'undo': [], 'redo': []}
    _LIVE[bid] = st
    return book, st


def _flush(bid, book):
    db.run("UPDATE wb SET data=?, updated=? WHERE id=?",
           json.dumps(book.to_dict(), ensure_ascii=False),
           datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), bid)


def _snap(sheet, r1, c1, r2, c2):
    """记录改动前的快照，用于撤销"""
    out = []
    for i in range(r1, r2 + 1):
        for j in range(c1, c2 + 1):
            cl = sheet.get(i, j)
            out.append([i, j,
                        cl.raw if cl else '',
                        cl.fmt if cl else '',
                        cl.style.to_dict() if (cl and cl.style) else None,
                        cl.note if cl else ''])
    return out


def _restore(sheet, snap):
    for i, j, raw, fmt, style, note in snap:
        cl = sheet.cell(i, j, create=True)
        if cl is None:
            continue
        cl.raw = raw
        cl.fmt = fmt
        cl.note = note
        cl.style = ST.Style.from_dict(style) if style else None
        cl.ast = None
        cl.bad = ''
        sheet._classify(cl)
    sheet.invalidate()


def _push(st, label, sheet_name, snap):
    st['undo'].append({'label': label, 'sheet': sheet_name, 'snap': snap})
    if len(st['undo']) > MAX_UNDO:
        st['undo'].pop(0)
    st['redo'] = []


def _rect(args):
    g = args or {}
    def n(k, d=0):
        try:
            return int(g.get(k, d))
        except (TypeError, ValueError):
            return d
    r1, c1, r2, c2 = n('r1'), n('c1'), n('r2'), n('c2')
    return (min(r1, r2), min(c1, c2), max(r1, r2), max(c1, c2))


def _sh(book, name=None):
    return book.sheet(name) if name else book.act


# ------------------------------------------------------------------ 页面
def _uni_ok():
    """新引擎离线资源是否可用（精简包返回 False，列表页就不显示入口）"""
    try:
        from .univer import engine_available
        return engine_available()
    except Exception:
        return False


# ------------------------------------------------------- 引擎偏好（v3.77）
# 两个引擎都保留，只是决定「点工作簿名字默认进哪个」。
# 旧的那个不删：新引擎加载失败时它就是退路。
_ENGINE_DEFAULT = 'new'   # new=新引擎(Univer) / old=自带制表台


def _q(s):
    """中文提示塞进 URL 查询串"""
    return urllib.parse.quote(str(s))


def _cfg_table():
    try:
        db.run("CREATE TABLE IF NOT EXISTS cfg "
               "(k TEXT PRIMARY KEY, v TEXT)")
    except Exception:
        pass


def get_engine():
    """读取引擎偏好；没设过就返回默认。库不可用时也不崩。"""
    try:
        _cfg_table()
        rows = db.q("SELECT v FROM cfg WHERE k='sheet_engine'")
        if rows:
            v = (rows[0]['v'] or '').strip().lower()
            if v in ('new', 'old'):
                return v
    except Exception:
        pass
    return _ENGINE_DEFAULT


def set_engine(v):
    v = (v or '').strip().lower()
    if v not in ('new', 'old'):
        return False
    try:
        _cfg_table()
        db.run("INSERT INTO cfg(k,v) VALUES('sheet_engine',?) "
               "ON CONFLICT(k) DO UPDATE SET v=excluded.v", v)
        return True
    except Exception:
        return False


@bp.route('/sheet')
def sheet_index():
    rows = db.q("SELECT id, name, updated FROM wb ORDER BY id DESC")
    uni = _uni_ok()
    eng = get_engine()
    # 偏好新引擎但资源没随包装上（精简 APK）→ 静默回退，不让用户撞红字
    if eng == 'new' and not uni:
        eng = 'old'
    return render_template('sheet.html', mode='list', books=rows,
                           uni=uni, eng=eng,
                           msg=request.args.get('msg', ''))


@bp.route('/sheet/engine', methods=['POST'])
def sheet_engine_set():
    """切换「点工作簿名字默认进哪个引擎」。两个引擎都还在，只是改默认。"""
    v = (request.form.get('engine') or '').strip().lower()
    if v not in ('new', 'old'):
        return redirect('/sheet?msg=' + _q('引擎参数不对'))
    if v == 'new' and not _uni_ok():
        return redirect('/sheet?msg=' + _q('新引擎资源没随包安装'))
    set_engine(v)
    tip = '已切换：默认用新引擎' if v == 'new' else '已切换：默认用自带制表台'
    return redirect('/sheet?msg=' + _q(tip))


@bp.route('/sheet/new', methods=['POST'])
def sheet_new():
    # v3.44：A 列默认批次号并自动编号，建表时可勾选是否启用。
    name = (request.form.get('name') or '').strip() or '工作簿'
    # 勾选项是「不启用」，所以没勾 = 启用（默认开）
    with_batch = request.form.get('no_batch') != '1'
    book = E.Workbook(name)
    sh = book.add('Sheet1')
    sh.book = book
    if with_batch:
        batch.fill(sh, rows=batch.DEFAULT_ROWS, with_header=True)
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    db.run("INSERT INTO wb(name, data, updated) VALUES(?,?,?)",
           name, json.dumps(book.to_dict(), ensure_ascii=False), now)
    row = db.q("SELECT last_insert_rowid() AS id")[0]
    return redirect(url_for('sheet_open', bid=row['id']))


@bp.route('/sheet/ref', methods=['POST'])
def sheet_ref():
    """把在用的库存模板 / 采购模板转成参考表（只读快照）。"""
    ok, skip, notes = ref.convert_all(
        with_batch=(request.form.get('no_batch') != '1'))
    msg = ('已生成 %d 份参考表' % ok) if ok else '没有可转换的模板'
    return redirect(url_for('sheet_index', msg=msg, n=skip))


@bp.route('/sheet/<int:bid>')
def sheet_open(bid):
    book, st = _load(bid)
    if not book:
        return redirect(url_for('sheet_index', msg='这本工作簿不存在'))
    row = db.q("SELECT name FROM wb WHERE id=?", bid)
    sh = book.act
    r2 = max(sh.max_used_row(), 30)
    c2 = max(sh.max_used_col(), 12)
    return render_template('sheet.html', mode='open', bid=bid,
                           uni=_uni_ok(),
                           name=(row[0]['name'] if row else ''),
                           book=book, sheets=book.sheets,
                           active=book.active,
                           r2=r2, c2=c2,
                           fmts=ST.PRESET_FORMATS,
                           fonts=ST.FONTS, sizes=ST.SIZES,
                           bdstys=ST.BORDER_STYLES,
                           funcs=F.all_names(),
                           nonce=new_nonce())


@bp.route('/sheet/<int:bid>/del')
def sheet_del(bid):
    db.run("DELETE FROM wb WHERE id=?", bid)
    _LIVE.pop(bid, None)
    return redirect(url_for('sheet_index', msg='已删除'))


@bp.route('/sheet/<int:bid>/rename', methods=['POST'])
def sheet_rename(bid):
    nm = (request.form.get('name') or '').strip()
    if nm:
        db.run("UPDATE wb SET name=? WHERE id=?", nm, bid)
        if bid in _LIVE:
            _LIVE[bid]['book'].name = nm
    return redirect(url_for('sheet_open', bid=bid))


# ------------------------------------------------------------------ 导入导出
@bp.route('/sheet/<int:bid>/import', methods=['POST'])
def sheet_import(bid):
    book, st = _load(bid)
    if not book:
        return redirect(url_for('sheet_index'))
    f = request.files.get('file')
    if not f or not f.filename:
        return redirect(url_for('sheet_open', bid=bid, msg='没选文件'))
    ext = os.path.splitext(f.filename or '')[1].lower()
    tmp = os.path.join(util.TMP, '%d_%s' % (bid, os.path.basename(f.filename)))
    try:
        os.makedirs(util.TMP, exist_ok=True)
        f.save(tmp)
        if ext in ('.csv', '.txt'):
            nb = IO.from_csv(path=tmp, name='导入')
        else:
            nb = IO.from_xlsx(path=tmp)
    except Exception as e:
        return redirect(url_for('sheet_open', bid=bid,
                                msg='导入失败：%s' % e))
    # 追加到当前工作簿（保留已有的表）
    for s in nb.sheets:
        nm = s.name
        i = 2
        while book.sheet(nm):
            nm = '%s%d' % (s.name, i)
            i += 1
        s.name = nm
        s.book = book
        book.sheets.append(s)
    _flush(bid, book)
    return redirect(url_for('sheet_open', bid=bid, msg='已导入 %d 张表'
                            % len(nb.sheets)))


@bp.route('/sheet/<int:bid>/export.xlsx')
def sheet_export_xlsx(bid):
    book, _ = _load(bid)
    if not book:
        return redirect(url_for('sheet_index'))
    data = IO.to_xlsx(book)
    from urllib.parse import quote
    return Response(data, mimetype='application/vnd.openxmlformats-'
                    'officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition':
                             "attachment; filename=wb.xlsx; filename*=UTF-8''%s.xlsx"
                             % quote(book.name)})


@bp.route('/sheet/<int:bid>/export.csv')
def sheet_export_csv(bid):
    book, _ = _load(bid)
    if not book:
        return redirect(url_for('sheet_index'))
    txt = IO.to_csv(book.act)          # to_csv 自己已带 BOM，这里不能再加一次
    from urllib.parse import quote
    return Response(txt, mimetype='text/csv; charset=utf-8',
                    headers={'Content-Disposition':
                             "attachment; filename=wb.csv; filename*=UTF-8''%s.csv"
                             % quote(book.act.name)})


# ------------------------------------------------------------------ 接口
def _api(bid, act):
    book, st = _load(bid)
    if not book:
        return {'err': '工作簿不存在'}
    g = request.get_json(silent=True) or request.form or {}
    sname = g.get('sheet') or ''
    sh = _sh(book, sname) or book.act
    r1, c1, r2, c2 = _rect(g)
    fn = _ACTIONS.get(act)
    if not fn:
        return {'err': '没有这个操作：%s' % act}
    try:
        out = fn(book, st, sh, g, (r1, c1, r2, c2))
    except Exception as e:
        return {'err': '%s' % e}
    if act not in ('grid', 'find'):
        _flush(bid, book)
    out = out or {}
    out['ok'] = 1
    return out


@bp.route('/sheet/api/<int:bid>/<act>', methods=['GET', 'POST'])
def sheet_api(bid, act):
    return jsonify(_api(bid, act))


# ------------------------------------------------------------------ 各操作
def a_grid(book, st, sh, g, rect):
    r1, c1, r2, c2 = rect
    cells = []
    for i in range(r1, r2 + 1):
        row = []
        for j in range(c1, c2 + 1):
            cl = sh.get(i, j)
            if cl is None:
                row.append(None)
                continue
            v = sh.value(i, j) if cl.kind == 'formula' else cl.v
            d = {'v': _j(v), 't': cl.display() if cl.kind != 'formula'
                 else ST.format_value(v, cl.fmt),
                 'raw': cl.raw, 'k': cl.kind}
            if cl.fmt:
                d['f'] = cl.fmt
            if cl.style:
                d['s'] = cl.style.to_dict()
            if cl.note:
                d['n'] = cl.note
            if cl.bad:
                d['bad'] = cl.bad
            row.append(d)
        cells.append(row)
    return {'cells': cells,
            'rows': sh.rows, 'cols': sh.cols,
            'merges': sh.merges,
            'frozen': [sh.frozen_rows, sh.frozen_cols],
            'widths': sh.col_width,
            'heights': sh.row_height,
            'cond': [x.to_dict() for x in sh.cond],
            'sheets': [{'name': s.name, 'hidden': s.hidden,
                        'color': s.tab_color} for s in book.sheets],
            'active': book.sheets.index(sh) if sh in book.sheets else 0,
            'used': sh.used_range(),
            'names': book.names,
            'hidden_rows': sorted(sh.hidden_rows),
            'hidden_cols': sorted(sh.hidden_cols),
            'validations': sh.validations,
            'charts': sh.charts,
            'undo': len(st['undo']), 'redo': len(st['redo'])}


def col_name(i):
    from .sp_addr import col_letter
    return col_letter(i)


def _j(v):
    if isinstance(v, (list, tuple)):
        return [_j(x) for x in v]
    if isinstance(v, bool) or isinstance(v, (int, float)):
        return v
    if v is None:
        return ''
    return str(v)


def a_set(book, st, sh, g, rect):
    edits = g.get('edits') or []
    if not edits:
        return {'err': '没有要写的内容'}
    rs = [int(e[0]) for e in edits]
    cs = [int(e[1]) for e in edits]
    _push(st, '写入', sh.name, _snap(sh, min(rs), min(cs), max(rs), max(cs)))
    n = 0
    warn = []
    for e in edits:
        r, c = int(e[0]), int(e[1])
        txt = e[2] if len(e) > 2 else ''
        if txt and sh.validations:
            _w = check_validation(sh, r, c, txt)
            if _w:
                warn.append('%s%d：%s' % (col_name(c), r + 1, _w))
        _, changed = sh.set_raw(r, c, txt)
        n += 1 if changed else 0
    out = {'n': n}
    if warn:
        # 拦下来，但已经写进去的部分保留 —— 提示里说清第几格不合规
        out['warn'] = warn[:6]
        out['warn_n'] = len(warn)
    return out


def _contig(sh, a, b, c1, c2, by_row=True):
    """从起始位置往下（往右）找连续有内容的行（列），返回源区下标列表。

    至少返回起始那一行 —— Excel 拖一个格子也是"复制"，不是什么都不做。
    """
    out = [a]
    for k in range(a + 1, b + 1):
        has = False
        for j in range(c1, c2 + 1):
            v = sh.raw(k, j) if by_row else sh.raw(j, k)
            if v not in ('', None):
                has = True
                break
        if not has:
            break
        out.append(k)
    return out


def _n2s(v):
    """数字转回文本，去掉浮点长尾（0.30000000000000004 → 0.3）"""
    if isinstance(v, bool):
        return str(v)
    r = round(v, 10)
    if abs(r - int(r)) < 1e-9:
        return str(int(r))
    return str(r)


_DAY = datetime.timedelta(days=1)
_RE_DATE = re.compile(r'^(\d{4})[-/](\d{1,2})[-/](\d{1,2})')


def _step_vals(sh, raws):
    """给一串源值算外推步长。返回 (kind, vals_or_step)

    kind: 'num' 等差外推 / 'date' 日期递增 / 'copy' 原样循环复制
    """
    if any(str(v).startswith('=') for v in raws if v not in ('', None)):
        return ('copy', raws)
    nums = [F.to_num(v) for v in raws]
    if len(raws) >= 2 and all(x is not None for x in nums):
        return ('num', nums)
    d0 = _RE_DATE.match(str(raws[0]).strip())
    if d0:
        try:
            d = datetime.date(int(d0.group(1)), int(d0.group(2)), int(d0.group(3)))
            if len(raws) >= 2:
                d1 = _RE_DATE.match(str(raws[1]).strip())
                if d1:
                    dd = datetime.date(int(d1.group(1)), int(d1.group(2)),
                                       int(d1.group(3)))
                    return ('date', [d, (dd - d).days or 1])
            return ('date', [d, 1])
        except Exception:
            pass
    return ('copy', raws)


def a_fill(book, st, sh, g, rect):
    """填充（拖填充柄 / Ctrl+D / Ctrl+R）

    跟 Excel 一个脾气：源区只有一格就复制；有规律（等差、日期）就延续规律；
    源是公式就按相对引用平移着复制。
    """
    r1, c1, r2, c2 = rect
    dir_ = (g.get('dir') or 'down')
    _push(st, '填充', sh.name, _snap(sh, r1, c1, r2, c2))

    if dir_ in ('down', 'up'):
        srcs = _contig(sh, r1, r2, c1, c2, True) if dir_ == 'down' else \
               _contig(sh, r2, r1, c1, c2, True)
        if dir_ == 'up':
            srcs = list(reversed(srcs))
        n = len(srcs)
        if r1 + n > r2:                      # 没有目标行，Excel 也是什么都不做
            return {}
        for j in range(c1, c2 + 1):
            raws = [sh.raw(i, j) for i in srcs]
            kind, info = _step_vals(sh, raws)
            for k, i in enumerate(range(r1 + n, r2 + 1)):
                if kind == 'num':
                    step = (info[-1] - info[0]) / (len(info) - 1)
                    sh.set_raw(i, j, _n2s(info[-1] + step * (k + 1)))
                elif kind == 'date':
                    d, sd = info
                    nd = d + datetime.timedelta(days=sd * (k + 1 + len(srcs) - 1))
                    sh.set_raw(i, j, nd.strftime('%Y-%m-%d'))
                else:
                    s = srcs[k % n]
                    sh.set_raw(i, j, _shift_formula(sh.raw(s, j), i - s, 0))
    else:
        srcs = _contig(sh, c1, c2, r1, r2, False) if dir_ == 'right' else \
               _contig(sh, c2, c1, r1, r2, False)
        n = len(srcs)
        if c1 + n > c2:
            return {}
        for i in range(r1, r2 + 1):
            raws = [sh.raw(i, j) for j in srcs]
            kind, info = _step_vals(sh, raws)
            for k, j in enumerate(range(c1 + n, c2 + 1)):
                if kind == 'num':
                    step = (info[-1] - info[0]) / (len(info) - 1)
                    sh.set_raw(i, j, _n2s(info[-1] + step * (k + 1)))
                elif kind == 'date':
                    d, sd = info
                    nd = d + datetime.timedelta(days=sd * (k + 1 + len(srcs) - 1))
                    sh.set_raw(i, j, nd.strftime('%Y-%m-%d'))
                else:
                    s = srcs[k % n]
                    sh.set_raw(i, j, _shift_formula(sh.raw(i, s), 0, j - s))
    return {}


def _shift_formula(raw, dr, dc):
    """复制/填充时把相对引用按偏移平移（跟 Excel 一致）"""
    if not raw or not raw.startswith('='):
        return raw
    try:
        from . import sp_parser as P
        import re
        ast = P.parse(raw)
        refs = P.refs_of(ast)
        if not refs:
            return raw
        # 逐个替换文本里的引用（按长度从长到短，避免 A1 命中 A10 的前缀）
        out = raw
        pairs = sorted(((r.to_str(), r.shift(dr, dc).to_str()) for r in refs),
                       key=lambda x: -len(x[0]))
        for old, new in pairs:
            if old == new:
                continue
            out = re.sub(r'(?<![A-Za-z0-9_$!])' + re.escape(old) + r'(?![0-9])',
                         new.replace('\\', '\\\\'), out)
        return out
    except Exception:
        return raw


def a_rows(book, st, sh, g, rect):
    op = g.get('op') or 'insert'
    r1, c1, r2, c2 = rect
    n = max(1, int(g.get('n') or 1))
    _push(st, '行', sh.name, _snap(sh, 0, 0, sh.rows - 1, sh.cols - 1))
    if op == 'insert':
        sh.insert_rows(r1, n)
    else:
        sh.delete_rows(r1, n)
    return {}


def a_cols(book, st, sh, g, rect):
    op = g.get('op') or 'insert'
    r1, c1, r2, c2 = rect
    n = max(1, int(g.get('n') or 1))
    _push(st, '列', sh.name, _snap(sh, 0, 0, sh.rows - 1, sh.cols - 1))
    if op == 'insert':
        sh.insert_cols(c1, n)
    else:
        sh.delete_cols(c1, n)
    return {}


def a_sort(book, st, sh, g, rect):
    r1, c1, r2, c2 = rect
    keys = g.get('keys') or [[c1, 1]]
    _push(st, '排序', sh.name, _snap(sh, r1, c1, r2, c2))
    hdr = int(g.get('header') or 0)
    start = r1 + (1 if hdr else 0)
    order = list(range(start, r2 + 1))

    def keyf(i):
        out = []
        for col, asc in keys:
            col = int(col)
            v = sh.value(i, col)
            n = F.to_num(v)
            out.append(((n is None), (n if n is not None else 0),
                        F.to_text(v).lower(), -(1 if asc else -1)))
        return out
    order.sort(key=lambda i: [k[:-1] for k in keyf(i)])
    for col, asc in keys:
        if not asc:
            order.reverse()
            break
    data = [[sh.raw(i, j) for j in range(c1, c2 + 1)] for i in order]
    for k, i in enumerate(range(start, r2 + 1)):
        for jj, j in enumerate(range(c1, c2 + 1)):
            sh.set_raw(i, j, data[k][jj])
    sh.sort_spec = [(int(a), int(b)) for a, b in keys]
    return {}


def a_filter(book, st, sh, g, rect):
    """筛选：hide 是要隐藏的行号列表"""
    r1, c1, r2, c2 = rect
    hide = [int(x) for x in (g.get('hide') or [])]
    on = g.get('on')
    if on == '0':
        sh.filter = None
    else:
        sh.filter = {'r1': r1, 'c1': c1, 'r2': r2, 'c2': c2, 'hide': hide}
    return {}


def a_style(book, st, sh, g, rect):
    r1, c1, r2, c2 = rect
    _push(st, '格式', sh.name, _snap(sh, r1, c1, r2, c2))
    patch = g.get('style') or {}
    clear = g.get('clear') == '1'
    for i in range(r1, r2 + 1):
        for j in range(c1, c2 + 1):
            cl = sh.cell(i, j, create=True)
            if cl is None:
                continue
            if clear:
                cl.style = None
                if 'fmt' in patch:
                    cl.fmt = ''
                continue
            if 'fmt' in patch:
                cl.fmt = patch['fmt'] or ''
            # 只要 patch 里带了任意样式字段就合并（下划线/字体/缩进/细致边框等）
            if any(k in patch for k in ST.Style.__slots__ if k != 'fmt'):
                base = cl.style or ST.Style()
                cl.style = base.merge(ST.Style.from_dict(patch))
    return {}


def a_width(book, st, sh, g, rect):
    r1, c1, r2, c2 = rect
    w = g.get('w')
    if g.get('what') == 'row':
        if w:
            for i in range(r1, r2 + 1):
                sh.row_height[i] = float(w)
        else:
            for i in range(r1, r2 + 1):
                sh.row_height.pop(i, None)
    else:
        if w:
            for j in range(c1, c2 + 1):
                sh.col_width[j] = float(w)
        else:
            for j in range(c1, c2 + 1):
                sh.col_width.pop(j, None)
    return {}


def a_group(book, st, sh, g, rect):
    """按列分组 / 折叠展开（开源表格的通用能力）。"""
    r1, c1, r2, c2 = rect
    col = int(g.get('col') if g.get('col') is not None else c1)
    op = g.get('op') or 'group'
    hd = int(g.get('header') or 0)
    if op == 'collapse' or op == 'expand':
        gs = GR.build_groups(sh, col, r1, r2, hd)
        n = GR.collapse(sh, gs, hide=(op == 'collapse'))
        return {'n': len(gs), 'hidden': n}
    _push(st, '分组', sh.name, _snap(sh, r1, 0, r2, sh.cols - 1))
    gs, ok = GR.group_by(sh, col, r1, r2, hd, insert=(op != 'sortonly'))
    return {'groups': gs, 'ok': ok}


def a_merge(book, st, sh, g, rect):
    r1, c1, r2, c2 = rect
    op = g.get('op') or 'merge'
    _push(st, '合并', sh.name, _snap(sh, r1, c1, r2, c2))
    sh.merges = [m for m in sh.merges
                 if not (m[0] <= r2 and m[2] >= r1 and m[1] <= c2 and m[3] >= c1)]
    if op == 'merge':
        sh.merges.append((r1, c1, r2, c2))
    return {}


def a_freeze(book, st, sh, g, rect):
    sh.frozen_rows = max(0, int(g.get('rows') or 0))
    sh.frozen_cols = max(0, int(g.get('cols') or 0))
    return {}


def a_sheets(book, st, sh, g, rect):
    op = g.get('op') or 'add'
    if op == 'add':
        s = book.add(g.get('name') or '')
        book.active = book.sheets.index(s)
    elif op == 'del':
        if len(book.sheets) <= 1:
            return {'err': '至少保留一张表'}
        book.remove(g.get('name') or sh.name)
        book.active = min(book.active, len(book.sheets) - 1)
    elif op == 'rename':
        old = g.get('name') or sh.name
        nm = (g.get('new') or '').strip()
        if not nm:
            return {'err': '表名不能为空'}
        if book.sheet(nm) and nm.lower() != old.lower():
            return {'err': '已经有同名的工作表了'}
        book.rename(old, nm)
    elif op == 'copy':
        src = sh
        nm = (g.get('new') or (src.name + ' 副本')).strip()
        i = 2
        while book.sheet(nm):
            nm = '%s%d' % (src.name + ' 副本', i)
            i += 1
        ns = E.Sheet.from_dict(src.to_dict(), book)
        ns.name = nm
        book.sheets.insert(book.sheets.index(src) + 1, ns)
        book.active = book.sheets.index(ns)
    elif op == 'order':
        names = g.get('names') or []
        book.order(names)
    elif op == 'active':
        s = book.sheet(g.get('name'))
        if s:
            book.active = book.sheets.index(s)
    elif op == 'color':
        s = book.sheet(g.get('name')) or sh
        s.tab_color = (g.get('color') or '')
    elif op == 'hide':
        s = book.sheet(g.get('name')) or sh
        s.hidden = bool(g.get('v'))
    return {}


def a_undo(book, st, sh, g, rect):
    if not st['undo']:
        return {'err': '没有可撤销的操作'}
    item = st['undo'].pop()
    cur = book.sheet(item['sheet']) or sh
    snap = _snap(cur,
                 min(x[0] for x in item['snap']), min(x[1] for x in item['snap']),
                 max(x[0] for x in item['snap']), max(x[1] for x in item['snap']))
    st['redo'].append({'label': item['label'], 'sheet': item['sheet'],
                       'snap': snap})
    _restore(cur, item['snap'])
    return {'label': item['label'], 'sheet': item['sheet']}


def a_redo(book, st, sh, g, rect):
    if not st['redo']:
        return {'err': '没有可重做的操作'}
    item = st['redo'].pop()
    cur = book.sheet(item['sheet']) or sh
    snap = _snap(cur,
                 min(x[0] for x in item['snap']), min(x[1] for x in item['snap']),
                 max(x[0] for x in item['snap']), max(x[1] for x in item['snap']))
    st['undo'].append({'label': item['label'], 'sheet': item['sheet'],
                       'snap': snap})
    _restore(cur, item['snap'])
    return {'label': item['label'], 'sheet': item['sheet']}


def a_find(book, st, sh, g, rect):
    kw = (g.get('kw') or '')
    rep = g.get('rep')
    if not kw:
        return {'hits': 0}
    r1, c1, r2, c2 = rect
    hits = []
    for i in range(r1, r2 + 1):
        for j in range(c1, c2 + 1):
            cl = sh.get(i, j)
            if not cl:
                continue
            hay = cl.raw if (g.get('inraw') == '1') else \
                (sh.value(i, j) if cl.kind == 'formula' else cl.v)
            if kw.lower() in F.to_text(hay).lower():
                hits.append([i, j])
    if rep is not None and hits:
        _push(st, '替换', sh.name,
              _snap(sh, min(h[0] for h in hits), min(h[1] for h in hits),
                    max(h[0] for h in hits), max(h[1] for h in hits)))
        for i, j in hits:
            cl = sh.get(i, j)
            old = cl.raw
            if old.startswith('='):
                new = old.replace(kw, rep)
            else:
                new = F.to_text(
                    sh.value(i, j) if cl.kind == 'formula' else cl.v
                ).replace(kw, rep)
            sh.set_raw(i, j, new)
    return {'hits': len(hits), 'list': hits[:200]}


def a_names(book, st, sh, g, rect):
    op = g.get('op') or 'define'
    if op == 'define':
        book.define_name(g.get('name'), g.get('ref'))
    elif op == 'del':
        book.del_name(g.get('name'))
    return {'names': book.names}


def a_note(book, st, sh, g, rect):
    r1, c1, r2, c2 = rect
    cl = sh.cell(r1, c1, create=True)
    if cl is None:
        return {}
    _push(st, '批注', sh.name, _snap(sh, r1, c1, r1, c1))
    cl.note = g.get('note') or ''
    return {}


def a_cond(book, st, sh, g, rect):
    """条件格式：add / del / list"""
    op = g.get('op') or 'add'
    if op == 'del':
        sh.cond = []
        return {}
    rule = ST.CondRule.from_dict(g.get('rule') or {})
    rule.range = list(rect)
    sh.cond = [r for r in sh.cond if r.to_dict().get('ctype') != rule.ctype
               or r.op != rule.op]
    sh.cond.append(rule)
    return {'cond': [x.to_dict() for x in sh.cond]}


def a_autofit(book, st, sh, g, rect):
    """按内容自动列宽"""
    r1, c1, r2, c2 = rect
    for j in range(c1, c2 + 1):
        w = 6
        for i in range(r1, r2 + 1):
            cl = sh.get(i, j)
            if not cl:
                continue
            t = cl.display() if cl.kind != 'formula' else \
                ST.format_value(sh.value(i, j), cl.fmt)
            ln = sum(2 if ord(ch) > 127 else 1 for ch in str(t))
            w = max(w, min(ln + 2, 60))
        sh.col_width[j] = w
    return {}


def a_save(book, st, sh, g, rect):
    return {}


def a_eval(book, st, sh, g, rect):
    """给界面实时预览一个公式（不写入）"""
    expr = g.get('expr') or ''
    try:
        from . import sp_parser as P
        ast = P.parse(expr)
        v = sh._eval_ast(ast, rect[0], rect[1], 0)
        return {'v': _j(v), 't': ST.format_value(v, g.get('fmt') or '')}
    except Exception as e:
        return {'err': str(e)}


_ACTIONS = {
    'grid': a_grid, 'set': a_set, 'fill': a_fill, 'rows': a_rows,
    'cols': a_cols, 'sort': a_sort, 'filter': a_filter, 'style': a_style,
    'width': a_width, 'merge': a_merge, 'freeze': a_freeze,
    'sheets': a_sheets, 'undo': a_undo, 'redo': a_redo, 'find': a_find,
    'names': a_names, 'note': a_note, 'cond': a_cond,
    'autofit': a_autofit, 'group': a_group, 'save': a_save, 'eval': a_eval,
    # 进阶：剪贴板 / 自动求和 / 序列 / 隐藏 / 有效性 / 图表 / 透视 / 清除
    **EXTRA,
}

# ------------------------------------------------------------------ Univer 引擎
# 工业级表格内核（Apache-2.0）。资源在 static/univer，全部离线，不联网。
try:
    from . import univer as _UNI
    _UNI.register(bp)
except Exception as _e:          # 引擎挂了也要保证旧表格能用
    import sys
    print('[univer] 未启用：%s' % _e, file=sys.stderr)
