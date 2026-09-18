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
import os
import sys
import datetime

from ...core import db

ENGINE_VER = '0.25.1'
_CACHED_COL = {'ok': False}
_NEED = ('univer-presets.js', 'react.js', 'univer-sheets-core.js')

# ------------------------------------------------------------------ 可选能力包
# 核心预设只带公式/数字格式/基础 UI。下面这些是社区版免费、官方单独发布的
# 预设包，每个都自包含（已内联其实现与 facade），彼此的依赖靠顺序解决：
#   筛选(1) 排序(2) 查找替换(3) 图形(4) 批注(5) 备注(6)
#   数据验证(7) ← 超链接(10) 依赖它    表格(8) 依赖 排序
# 顺序改动前请先跑 tools 里的依赖自检，否则会白屏。
#
# 第 6 位 exp 有两种形态（实测 UMD 导出后确定，勿凭包名推断）：
#   · 字符串  → 预设形态：window[ns][exp]() 得到 {plugins:[...]}
#   · 列表    → 插件形态：该包没有预设，只有底层 Plugin，
#               [[全局名, 导出名], ...] 逐个取出后手工包成 {plugins:[...]}
#               筛选与数据验证就是这种，写成预设名会静默失效（不报错、功能没了）。
EXTRA_MODULES = [
    ('筛选',     'univer-ps-filter.js',                'univer-zh-CN-filter.js', 'univer-filter.css',
     '-', [['UniverSheetsFilter', 'UniverSheetsFilterPlugin'],
           ['UniverSheetsFilterUi', 'UniverSheetsFilterUIPlugin']]),
    ('排序',     'univer-ps-sort.js',                  'univer-zh-CN-sort.js',   'univer-sort.css',
     'UniverPresetSheetsSort',                   'UniverSheetsSortPreset'),
    ('查找替换', 'univer-ps-find-replace.js',          'univer-zh-CN-find.js',   'univer-find.css',
     'UniverPresetSheetsFindReplace',            'UniverSheetsFindReplacePreset'),
    ('图形',     'univer-ps-drawing.js',               'univer-zh-CN-draw.js',   'univer-draw.css',
     'UniverPresetSheetsDrawing',                'UniverSheetsDrawingPreset'),
    ('批注',     'univer-ps-thread-comment.js',        'univer-zh-CN-tc.js',     'univer-tc.css',
     'UniverPresetSheetsThreadComment',          'UniverSheetsThreadCommentPreset'),
    ('备注',     'univer-ps-note.js',                  'univer-zh-CN-note.js',   'univer-note.css',
     'UniverPresetSheetsNote',                   'UniverSheetsNotePreset'),
    ('数据验证', 'univer-ps-data-validation.js',       'univer-zh-CN-dv.js',     'univer-dv.css',
     '-', [['UniverDataValidation', 'UniverDataValidationPlugin'],
           ['UniverSheetsDataValidation', 'UniverSheetsDataValidationPlugin'],
           ['UniverSheetsDataValidationUi', 'UniverSheetsDataValidationUIPlugin']]),
    ('表格',     'univer-ps-table.js',                 'univer-zh-CN-table.js',  'univer-table.css',
     'UniverPresetSheetsTable',                  'UniverSheetsTablePreset'),
    ('条件格式', 'univer-ps-conditional-formatting.js', 'univer-zh-CN-cf.js',    'univer-cf.css',
     'UniverPresetSheetsConditionalFormatting',  'UniverSheetsConditionalFormattingPreset'),
    ('超链接',   'univer-ps-hyper-link.js',            'univer-zh-CN-hl.js',     'univer-hl.css',
     'UniverPresetSheetsHyperLink',              'UniverSheetsHyperLinkPreset'),
    # 这两个包没有预设形态，同样是插件形态；水印无中文包与样式，留空由 extra_assets 跳过
    ('十字准星', 'univer-crosshair.js',                'univer-zh-CN-crosshair.js', 'univer-crosshair.css',
     '-', [['UniverSheetsCrosshairHighlight', 'UniverSheetsCrosshairHighlightPlugin']]),
    ('水印',     'univer-watermark.js',                '',                       '',
     '-', [['UniverWatermark', 'UniverWatermarkPlugin']]),
]

# 各能力包语言包写入的全局名（mergeLocales 用）
EXTRA_LOCALE_GLOBALS = [
    'UniverSheetsFilterZhCN', 'UniverSheetsFilterUiZhCN',
    'UniverSheetsSortUiZhCN', 'UniverFindReplaceZhCN',
    'UniverDrawingUiZhCN', 'UniverSheetsDrawingUiZhCN',
    'UniverThreadCommentUiZhCN', 'UniverSheetsThreadCommentUiZhCN',
    'UniverSheetsNoteUiZhCN',
    'UniverDataValidationZhCN', 'UniverSheetsDataValidationZhCN', 'UniverSheetsDataValidationUiZhCN',
    'UniverSheetsTableZhCN', 'UniverSheetsTableUiZhCN',
    'UniverSheetsConditionalFormattingUiZhCN',
    'UniverSheetsHyperLinkZhCN', 'UniverSheetsHyperLinkUiZhCN',
]


def extra_assets():
    """返回实际存在的能力包资源（缺哪个就静默跳过哪个，不让页面 404）"""
    d = os.path.join(static_dir(), 'univer')
    js, loc, css, mods = [], [], [], []
    for label, j, l, c, ns, exp in EXTRA_MODULES:
        if not os.path.exists(os.path.join(d, j)):
            continue
        js.append(j)
        # 空串必须挡住：os.path.exists(目录) 恒为真，会生成 src 指向目录的 404 标签
        if l and os.path.exists(os.path.join(d, l)):
            loc.append(l)
        if c and os.path.exists(os.path.join(d, c)) and os.path.getsize(os.path.join(d, c)) > 0:
            css.append(c)
        mods.append([label, ns, exp])
    return js, loc, css, mods


# ------------------------------------------------- 离线资源是否齐全
def static_dir():
    """静态资源目录（跟 dispatch._static_folder 保持同一套解析规则）"""
    try:
        from flask import current_app
        sf = getattr(current_app, 'static_folder', None)
        if sf:
            return sf
    except Exception:
        pass
    _env = os.environ.get('WAREHOUSE_STATIC')
    if _env:
        return _env
    if getattr(sys, 'frozen', False):
        return os.path.join(db.res_dir(), 'static')
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, 'static')


def engine_available():
    """新引擎的离线资源是否随包安装了。

    Android 精简版会把 static/univer（约 12MB JS）剔掉以缩小体积，
    这时不能让用户撞到红色报错——应当静默用回自带制表台。
    """
    d = os.path.join(static_dir(), 'univer')
    return all(os.path.exists(os.path.join(d, f)) for f in _NEED)


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
    from ...core.util import new_nonce
    from ..engine import core as E

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
        if not engine_available():          # 精简包：静默回退，不报错
            return redirect('/sheet/%d?msg=%s' % (
                bid, '本机未随包安装新引擎，已用自带制表台打开'))
        snap = load_uni(bid)
        if snap is None:                       # 老表：现场转一次
            try:
                book = E.Workbook.from_dict(json.loads(row['data'] or '{}'))
            except Exception:
                book = E.Workbook('工作簿')
                book.add('Sheet1')
            snap = book_to_univer(book)
        _js, _loc, _css, _mods = extra_assets()
        return render_template(
            'univer.html',
            name=row['name'], bid=bid,
            snapshot=json.dumps(snap, ensure_ascii=False),
            nonce=new_nonce(),
            ver=ENGINE_VER,
            extra_js=_js, extra_locale=_loc, extra_css=_css,
            extra_mods=json.dumps(_mods, ensure_ascii=False),
            extra_locale_globals=json.dumps(EXTRA_LOCALE_GLOBALS, ensure_ascii=False),
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
