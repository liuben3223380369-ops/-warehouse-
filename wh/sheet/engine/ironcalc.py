# -*- coding: utf-8 -*-
"""IronCalc 计算后端（可选 · 纯新增）

定位
----
本文件**不接入现有求值链**——import 它不会改变任何既有行为。
它的用途是「第二计算层」，服务于两件事：

    1. 双算比对：用 IronCalc 复算自研制表引擎的结果，产出差异报告
    2. 为将来把计算层整体切换到 IronCalc 做准备（先验证，后切换）

为什么需要第二计算层
--------------------
自研制表引擎（sp_engine / formula_engine / sp_funcs）175 个函数是我们自己实现的，
历史上出现过 MATCH 精确匹配失效这类"不报错但算错"的缺陷。
独立实现一遍同样语义的引擎，用同一批公式做交叉验算，
是发现这类静默错误最有效的手段。

授权
----
IronCalc: Apache-2.0 OR MIT（双许可），可安全用于闭源项目。
许可证兼容性上，与本项目（MIT/Apache 系）无冲突。

坐标约定
--------
    自研引擎  (r, c)  0-based
    IronCalc  (row, col) 1-based
    本模块统一在边界处做 +1 / -1 转换

用法
----
    from wh.sheet.engine import ironcalc as IC
    if IC.available():
        report = IC.compare_book(book)      # 全簿比对
        for d in report['diffs']:
            print(d['addr'], d['own'], d['ic'])
"""
from __future__ import unicode_literals

import re

try:                                    # 可选依赖：装不上不影响主程序
    import ironcalc as _ic
except Exception:                       # pragma: no cover
    _ic = None


# ------------------------------------------------------------------ 可用性
def available():
    """IronCalc 是否已安装可用"""
    return _ic is not None


def version():
    """IronCalc 版本，未安装返回 None"""
    return getattr(_ic, '__version__', None) if _ic else None


# ------------------------------------------------------------------ 值归一化
_NUM_ERR = ('#CIRC!', '#REF!', '#VALUE!', '#DIV/0!', '#NAME?',
            '#N/A', '#NULL!', '#NUM!', '#ERROR!', '#NOT_EVALUATED')


def normalize(v):
    """把两侧的值归一成可比形式。

    数值 -> 保留 6 位小数的浮点（抹掉显示精度差异，如 366.666666667 /
    366.6666666666667）；错误字面量 -> 原样；其余 -> 去空格字符串。
    返回 (类型, 值)，类型用于判定两值是否"同类"。
    """
    if v is None:
        return ('blank', '')
    if isinstance(v, bool):
        return ('bool', v)
    if isinstance(v, (int, float)):
        return ('num', round(float(v), 6))
    s = ('' if v is None else str(v)).strip()
    if s == '':
        return ('blank', '')
    if s.upper() in _NUM_ERR:
        return ('err', s.upper())
    # IronCalc 返回的是格式化字符串，可能是数字
    try:
        return ('num', round(float(s), 6))
    except (TypeError, ValueError):
        pass
    return ('text', s)


def same(a, b, tol=1e-6):
    """两侧值是否一致"""
    ka, va = normalize(a)
    kb, vb = normalize(b)
    if ka != kb:
        # 空 vs 空文本视为一致
        if ka == 'blank' and kb == 'text' and vb == '':
            return True
        if kb == 'blank' and ka == 'text' and va == '':
            return True
        return False
    if ka == 'num':
        return abs(va - vb) <= max(tol, abs(va) * tol)
    return va == vb


# ------------------------------------------------------------------ 同步
def _col_name(c):
    """0-based 列号 -> A / B / AA"""
    s = ''
    c += 1
    while c:
        c, r = divmod(c - 1, 26)
        s = chr(65 + r) + s
    return s


def addr_of(r, c):
    """0-based (r,c) -> 'B3'"""
    return _col_name(c) + str(r + 1)


def sync(sheet, model=None, sheet_index=0):
    """把一个自研 Sheet 的**原始输入**同步进 IronCalc model。

    只同步 raw 文本（含 '=' 开头的公式），不搬运自研的求值结果——
    否则就变成"拿自研结果喂给 IronCalc"，比对失去意义。
    """
    if _ic is None:
        return None
    if model is None:
        model = _ic.UserModel(sheet.name or 'sheet')
    for (r, c), cl in sheet.cells.items():
        txt = cl.raw if cl is not None else ''
        if txt is None or str(txt).strip() == '':
            continue
        try:
            model.set_user_input(sheet_index, r + 1, c + 1, str(txt))
        except Exception:
            # 单格写失败不应中断整表同步（例如 IronCalc 拒收的写法）
            pass
    return model


def evaluate(model):
    """显式求值。

    注意：IronCalc 的 pause/resume 机制在批量写入后**不做完整求值**，
    会留下 #ERROR!。必须显式 evaluate()。
    """
    if model is None:
        return False
    try:
        model.evaluate()
        return True
    except Exception:
        return False


def values(model, sheet, sheet_index=0):
    """取回 IronCalc 对每一格的求值结果 -> {(r,c): 格式化字符串}"""
    out = {}
    if model is None:
        return out
    for (r, c) in sheet.cells:
        try:
            out[(r, c)] = model.get_formatted_cell_value(sheet_index, r + 1, c + 1)
        except Exception as e:
            out[(r, c)] = '#FETCH_ERR'
    return out


# ------------------------------------------------------------------ 比对
def compare_sheet(sheet, sheet_index=0, only_formula=True):
    """比对单个表 -> dict

    返回 {'name', 'total', 'diffs': [...], 'ok': bool}
    diffs 每项：{'addr','raw','own','ic','kind'}
    kind 取值：
        value    两侧都有值但不相等
        err_ic   IronCalc 报错而自研没报（值得警惕：可能自研过于宽松）
        err_own  自研报错而 IronCalc 没报
    """
    res = {'name': sheet.name, 'total': 0, 'diffs': [], 'ok': True}
    if _ic is None:
        res['ok'] = False
        res['error'] = 'ironcalc 未安装'
        return res

    model = sync(sheet, sheet_index=sheet_index)
    if model is None:
        res['ok'] = False
        res['error'] = '同步失败'
        return res
    evaluate(model)
    icv = values(model, sheet, sheet_index)

    for (r, c) in sorted(sheet.cells):
        cl = sheet.cells.get((r, c))
        raw = (cl.raw if cl else '') or ''
        if only_formula and not str(raw).strip().startswith('='):
            continue
        res['total'] += 1
        own = sheet.value(r, c)
        ic_val = icv.get((r, c))
        if same(own, ic_val):
            continue
        k = 'value'
        ko, _ = normalize(own)
        ki, _ = normalize(ic_val)
        if ki == 'err' and ko != 'err':
            k = 'err_ic'
        elif ko == 'err' and ki != 'err':
            k = 'err_own'
        res['diffs'].append({
            'addr': addr_of(r, c), 'raw': raw,
            'own': own, 'ic': ic_val, 'kind': k,
        })
    return res


def compare_book(book, only_formula=True):
    """比对整个工作簿"""
    out = {'sheets': [], 'total': 0, 'diffs': 0}
    for i, sh in enumerate(book.sheets):
        r = compare_sheet(sh, sheet_index=i, only_formula=only_formula)
        out['sheets'].append(r)
        out['total'] += r['total']
        out['diffs'] += len(r['diffs'])
    return out


# ------------------------------------------------------------------ 函数覆盖探测
def probe(formulas, data=None):
    """用一批公式同时跑两个引擎，返回逐条结果。

    data: {(r,c): 值}  预置数据（0-based）
    formulas: {(r,c): '=公式'}  待算公式（0-based）
    返回 list of {'addr','formula','own','ic','match'}
    """
    if _ic is None:
        return []
    from .core import Workbook      # 同包相对导入，避免依赖调用方 sys.path

    b = Workbook('probe')
    s = b.add('S1')
    for (r, c), v in (data or {}).items():
        s.set_raw(r, c, v)
    for (r, c), f in formulas.items():
        s.set_raw(r, c, f)

    m = _ic.UserModel('probe')
    for (r, c), v in (data or {}).items():
        m.set_user_input(0, r + 1, c + 1, str(v))
    for (r, c), f in formulas.items():
        try:
            m.set_user_input(0, r + 1, c + 1, f)
        except Exception as e:
            pass
    evaluate(m)

    out = []
    for (r, c), f in sorted(formulas.items()):
        own = s.value(r, c)
        try:
            icv = m.get_formatted_cell_value(0, r + 1, c + 1)
        except Exception as e:
            icv = '#EXC:' + str(e)[:40]
        out.append({'addr': addr_of(r, c), 'formula': f,
                    'own': own, 'ic': icv,
                    'match': same(own, icv)})
    return out
