# -*- coding: utf-8 -*-
"""表格模块 · Univer 引擎适配层

把自研工作簿与 Univer 快照互转，并负责存取。

为什么要单独存一份（wb.uni）
    Univer 的快照包含自研引擎没有的东西（富文本、条件格式、图表、
    数字格式、批注……），来回转换必然丢信息。所以新引擎的表格
    另存一份原始快照，自研那份只做"能读能导出"的降级副本——
    这样旧引擎页面、xlsx 导出、批注预览都不受影响。

    打开时优先用 uni；没有（老表）就从自研格式现场转一次。
"""
import json
import datetime

from ..core import db

ENGINE_VER = '0.25.1'
_CACHED_COL = {'ok': False}


# ------------------------------------------------------------------ 表结构
def ensure_col():
    """给 wb 表补 uni 列（只补一次）"""
    if _CACHED_COL['ok']:
        return
    try:
        names = set()
        for r in db.q("PRAGMA table_info(wb)"):
            try:
                names.add(r['name'])
            except Exception:
                names.add(r[1])
        if 'uni' not in names:
            db.run("ALTER TABLE wb ADD COLUMN uni TEXT")
    except Exception:
        pass
    _CACHED_COL['ok'] = True


def load_uni(bid):
    """取快照；没有就返回 None"""
    ensure_col()
    try:
        row = db.q("SELECT uni FROM wb WHERE id=?", bid)
        if row and row[0]['uni']:
            return json.loads(row[0]['uni'])
    except Exception:
        pass
    return None


def save_uni(bid, snap, book=None):
    """存快照，同时把降级副本写回 wb.data（供旧引擎 / 导出使用）"""
    ensure_col()
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    txt = json.dumps(snap, ensure_ascii=False)
    try:
        db.run("UPDATE wb SET uni=?, updated=? WHERE id=?", txt, now, bid)
    except Exception:
        return False
    # 降级副本
    try:
        d = univer_to_book(snap)
        db.run("UPDATE wb SET data=?, updated=? WHERE id=?",
               json.dumps(d, ensure_ascii=False), now, bid)
    except Exception:
        pass
    return True


# ------------------------------------------------------------------ 样式
def _rgb(v):
    """把各种颜色写法统一成 #RRGGBB；认不出就返回 None"""
    if not v:
        return None
    s = str(v).strip()
    if s.startswith('#'):
        s = s[1:]
    if len(s) == 6:
        try:
            int(s, 16)
            return '#' + s
        except ValueError:
            return None
    if len(s) == 8:                      # ARGB
        try:
            int(s, 16)
            return '#' + s[2:]
        except ValueError:
            return None
    return None


def _style_uni(st):
    """自研 Style → Univer IStyleData（认不出的字段直接跳过）"""
    if not st:
        return None
    g = lambda k: getattr(st, k, None)
    d = {}
    if g('bold'):
        d['bl'] = 1
    if g('italic'):
        d['it'] = 1
    if g('underline'):
        d['ul'] = {'s': 1}
    fs = g('size') or g('font_size')
    if fs:
        try:
            d['fs'] = float(fs)
        except (TypeError, ValueError):
            pass
    ff = g('font') or g('font_name')
    if ff:
        d['ff'] = str(ff)
    c = _rgb(g('color') or g('font_color'))
    if c:
        d['cl'] = {'rgb': c}
    b = _rgb(g('bg') or g('fill') or g('bg_color'))
    if b:
        d['bg'] = {'rgb': b}
    return d or None


# ------------------------------------------------------------------ 自研 → Univer
def _cell_uni(cl):
    d = {}
    raw = cl.raw if isinstance(cl.raw, str) else ('' if cl.raw is None else str(cl.raw))
    if raw.startswith('='):
        d['f'] = raw
        d['v'] = '' if cl.v is None else cl.v
    else:
        d['v'] = '' if cl.v is None else cl.v
    s = _style_uni(getattr(cl, 'style', None))
    if s:
        d['s'] = s
    return d


def _merge_uni(m):
    """自研 merge 可能是元组也可能是 dict，都兜住"""
    try:
        if isinstance(m, dict):
            return {'startRow': int(m.get('startRow', m.get('r1', 0))),
                    'endRow': int(m.get('endRow', m.get('r2', 0))),
                    'startColumn': int(m.get('startColumn', m.get('c1', 0))),
                    'endColumn': int(m.get('endColumn', m.get('c2', 0)))}
        if isinstance(m, (list, tuple)) and len(m) >= 4:
            return {'startRow': int(m[0]), 'endRow': int(m[2]),
                    'startColumn': int(m[1]), 'endColumn': int(m[3])}
    except Exception:
        pass
    return None


def book_to_univer(book):
    """自研 Workbook → Univer 快照"""
    sheets, order = {}, []
    for i, sh in enumerate(book.sheets):
        sid = 'sh%d' % (i + 1)
        order.append(sid)

        cell_data = {}
        for (r, c), cl in (getattr(sh, 'cells', None) or {}).items():
            if not (getattr(cl, 'raw', '') or getattr(cl, 'style', None)
                    or getattr(cl, 'note', '') or getattr(cl, 'fmt', '')):
                continue
            cell_data.setdefault(str(int(r)), {})[str(int(c))] = _cell_uni(cl)

        col_data = {}
        for c, w in (getattr(sh, 'col_width', None) or {}).items():
            try:
                col_data[str(int(c))] = {'w': float(w) * 8.0}   # 字符宽 → 像素
            except (TypeError, ValueError):
                pass
        row_data = {}
        for r, h in (getattr(sh, 'row_height', None) or {}).items():
            try:
                row_data[str(int(r))] = {'h': float(h)}
            except (TypeError, ValueError):
                pass

        mg = []
        for m in (getattr(sh, 'merges', None) or []):
            x = _merge_uni(m)
            if x:
                mg.append(x)

        sheets[sid] = {
            'id': sid,
            'name': sh.name or ('Sheet%d' % (i + 1)),
            'rowCount': max(int(getattr(sh, 'rows', 200) or 200), 200),
            'columnCount': max(int(getattr(sh, 'cols', 26) or 26), 26),
            'cellData': cell_data,
            'rowData': row_data,
            'columnData': col_data,
            'mergeData': mg,
            'freeze': {'xSplit': int(getattr(sh, 'frozen_cols', 0) or 0),
                       'ySplit': int(getattr(sh, 'frozen_rows', 0) or 0)},
            'hidden': 1 if getattr(sh, 'hidden', 0) else 0,
            'tabColor': getattr(sh, 'tab_color', '') or '',
        }

    return {
        'id': 'wh-book',
        'name': book.name or '工作簿',
        'appVersion': ENGINE_VER,
        'locale': 'zhCN',
        'styles': {},
        'sheetOrder': order,
        'sheets': sheets,
    }


# ------------------------------------------------------------------ Univer → 自研
def univer_to_book(snap):
    """Univer 快照 → 自研 book dict（降级副本，够导出和旧引擎看就行）"""
    snap = snap or {}
    sheets_out = []
    sheet_map = snap.get('sheets') or {}
    order = snap.get('sheetOrder') or list(sheet_map.keys())

    for sid in order:
        sh = sheet_map.get(sid) or {}
        cells = []
        for rk, row in (sh.get('cellData') or {}).items():
            try:
                r = int(rk)
            except (TypeError, ValueError):
                continue
            for ck, c in (row or {}).items():
                try:
                    c2 = int(ck)
                except (TypeError, ValueError):
                    continue
                c = c or {}
                val = c.get('v', '')
                f = c.get('f')
                if f:
                    raw, kind = str(f), 'formula'
                else:
                    raw = '' if val is None else str(val)
                    kind = 'num' if isinstance(val, (int, float)) else 'text'
                if raw == '' and kind != 'formula':
                    continue
                cells.append({'r': r, 'c': c2, 'raw': raw, 'kind': kind,
                              'fmt': '', 'note': '', 'v': val})

        cw = {}
        for k, v in (sh.get('columnData') or {}).items():
            try:
                cw[int(k)] = float(v.get('w', 0)) / 8.0
            except (TypeError, ValueError, AttributeError):
                pass
        rh = {}
        for k, v in (sh.get('rowData') or {}).items():
            try:
                rh[int(k)] = float(v.get('h', 0))
            except (TypeError, ValueError, AttributeError):
                pass

        fr = sh.get('freeze') or {}
        sheets_out.append({
            'name': sh.get('name') or 'Sheet1',
            'rows': int(sh.get('rowCount') or 200),
            'cols': int(sh.get('columnCount') or 26),
            'cells': cells,
            'col_width': cw,
            'row_height': rh,
            'frozen_rows': int(fr.get('ySplit') or 0),
            'frozen_cols': int(fr.get('xSplit') or 0),
            'merges': [{'r1': m.get('startRow', 0), 'c1': m.get('startColumn', 0),
                        'r2': m.get('endRow', 0), 'c2': m.get('endColumn', 0)}
                       for m in (sh.get('mergeData') or [])],
            'filter': None, 'cond': [],
            'tab_color': sh.get('tabColor') or '',
            'hidden': 1 if sh.get('hidden') else 0,
            'hidden_rows': [], 'hidden_cols': [],
            'validations': [], 'charts': [],
        })

    if not sheets_out:
        sheets_out.append({
            'name': 'Sheet1', 'rows': 200, 'cols': 26, 'cells': [],
            'col_width': {}, 'row_height': {}, 'frozen_rows': 0, 'frozen_cols': 0,
            'merges': [], 'filter': None, 'cond': [], 'tab_color': '',
            'hidden': 0, 'hidden_rows': [], 'hidden_cols': [],
            'validations': [], 'charts': []})

    return {'name': snap.get('name') or '工作簿', 'active': 0,
            'names': {}, 'sheets': sheets_out}


# ------------------------------------------------------------------ 路由
def register(bp):
    """挂到表格模块的蓝图上"""
    from flask import request, jsonify, render_template, redirect
    from ..core.util import new_nonce
    from . import sp_engine as E

    def _load(bid):
        row = db.q("SELECT * FROM wb WHERE id=?", bid)
        if not row:
            return None, None
        return row[0], None

    @bp.route('/sheet/<int:bid>/univer')
    def uni_open(bid):
        row, _ = _load(bid)
        if not row:
            return redirect('/sheet')
        snap = load_uni(bid)
        if snap is None:                       # 老表：现场转一次
            try:
                book = E.Workbook.from_dict(json.loads(row['data'] or '{}'))
            except Exception:
                book = E.Workbook('工作簿')
                book.add('Sheet1')
            snap = book_to_univer(book)
        return render_template(
            'univer.html',
            name=row['name'], bid=bid,
            snapshot=json.dumps(snap, ensure_ascii=False),
            nonce=new_nonce(),
            ver=ENGINE_VER,
        )

    @bp.route('/api/univer/<int:bid>')
    def uni_data(bid):
        snap = load_uni(bid)
        if snap is None:
            row, _ = _load(bid)
            if not row:
                return jsonify({'error': 'not found'}), 404
            try:
                book = E.Workbook.from_dict(json.loads(row['data'] or '{}'))
            except Exception:
                book = E.Workbook('工作簿')
                book.add('Sheet1')
            snap = book_to_univer(book)
        return jsonify(snap)

    @bp.route('/api/univer/<int:bid>/save', methods=['POST'])
    def uni_save(bid):
        try:
            snap = request.get_json(force=True, silent=True)
        except Exception:
            snap = None
        if not snap or not isinstance(snap, dict):
            return jsonify({'ok': False, 'msg': '数据为空'}), 400
        ok = save_uni(bid, snap)
        return jsonify({'ok': bool(ok)})

    return bp
