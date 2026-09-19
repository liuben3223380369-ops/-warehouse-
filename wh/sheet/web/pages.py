# -*- coding: utf-8 -*-
"""电子表格页面路由：列表、新建、参考模板、打开、重命名、删除、引擎切换。"""
from .common import *                                  # noqa: F401,F403
from .common import (_LIVE, _cfg_table, _load, _q, _sh,     # noqa: F401
                    _uni_ok)
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
    bid = row['id']
    # 引擎偏好是「新引擎」且资源随包装上了 → 直接进新引擎，别让用户再点一次
    if get_engine() == 'new' and _uni_ok():
        return redirect(url_for('uni_open', bid=bid))
    return redirect(url_for('sheet_open', bid=bid))


@bp.route('/sheet/<int:bid>')
def sheet_open(bid):
    book, st = _load(bid)
    if not book:
        return redirect(url_for('sheet_index', msg='这本工作簿不存在'))
    row = db.q("SELECT name FROM wb WHERE id=?", bid)
    sh = book.act
    r2 = max(sh.max_used_row(), 60)
    c2 = max(sh.max_used_col(), 20)
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


