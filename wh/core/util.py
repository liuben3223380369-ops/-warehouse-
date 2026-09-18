# -*- coding: utf-8 -*-
"""公共工具层 —— 所有模块的共用底座。

这里不依赖 Flask 的 app 实例，只提供：
  * 容错输出（Windows GBK 控制台 / 无控制台的窗口模式都不会崩）
  * 一次性令牌（防重复提交）
  * 输入校验（数量、日期、搜索词）
  * 日期与数值处理

任何业务模块都可以 `from ..core.util import ...`，不需要知道 Web 层的存在。
"""
import sys as _sys


def _safe_stdio():
    """Windows 中文版控制台是 GBK，print 一个 emoji 就会 UnicodeEncodeError 崩溃。
    必须在任何输出之前把 stdout/stderr 改成容错模式。"""
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

from datetime import datetime, date
import calendar, os, time, json, re
from collections import OrderedDict

from . import db

BASE = db.app_dir()

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
# 用 OrderedDict 而不是 set：淘汰时必须淘汰"最早发的"那一批。
# set 的迭代顺序由哈希决定，是随机的 —— 满仓淘汰时会随机丢掉一半，
# 用户正在填的表单令牌有 50% 概率被误淘汰，提交时判成"重复提交"，
# 填了半天的数据全丢。改为先进先出后，淘汰的一定是最老的那批。
from collections import OrderedDict
_nonces = OrderedDict()
_NONCE_MAX = 500          # 上限：防止开着几十个页面把内存撑大


def new_nonce():
    """发一个新令牌（渲染表单时调用）"""
    import uuid
    n = uuid.uuid4().hex[:16]
    _nonces[n] = True
    if len(_nonces) > _NONCE_MAX:      # 超量就淘汰最早的一批（FIFO）
        for _ in range(_NONCE_MAX // 2):
            try:
                _nonces.popitem(last=False)
            except KeyError:
                break
    return n


def take_nonce(n):
    """核销令牌：有效返回 True（并作废），重复/伪造返回 False"""
    if not n:
        return False
    if n in _nonces:
        _nonces.pop(n, None)
        return True
    return False
TMP = os.path.join(BASE, '.uploads')
os.makedirs(TMP, exist_ok=True)

# （启动副作用已移到调度文件 dispatch.py：建 app、初始化数据库、补指纹）


TXN_PAGE = 500       # 流水页单页最多显示条数
# 金额不单独存库：只存单价，金额 = 数量 × 单价，查询时现算。
# 存两份的话改了数量金额还是旧值，必然对不上。
AMT = "ROUND(t.qty * COALESCE(t.price,0), 2)" 

LABELS = {'name': '物料名称', 'code': '料号', 'supplier': '供应商', 'category': '类型',
          'spec': '规格', 'width': '宽幅', 'unit': '单位', 'status': '状态',
          'opening': '期初结存', 'safety': '安全库存'}

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
# 数量上限：表里多输几个零不该把库存冲到天文数字（v3.4 在导入侧已加，
# 但手动录入和采购到货当时漏了 —— 同一类防护要覆盖全部入口）
try:
    from ..importer import QTY_MAX
except Exception:
    QTY_MAX = 1e9

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

def prev_ym(m):
    """上一个月，如 2026-01 -> 2025-12。用于月报的环比对比。"""
    m = safe_ym(m)
    y, mo = int(m[:4]), int(m[5:7])
    mo -= 1
    if mo == 0:
        y -= 1; mo = 12
    return '%04d-%02d' % (y, mo)


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


def int_arg(store, key, default=0, lo=None, hi=None):
    """安全取整数：网址/表单里的值可能是手改坏的（?tpl=abc），不能让页面 500。

    用法：tid = int_arg(request.args, 'tpl') or db.default_tpl_id()
    """
    try:
        v = int(str(store.get(key) or '').strip())
    except (TypeError, ValueError):
        return default
    if lo is not None and v < lo:
        v = lo
    if hi is not None and v > hi:
        v = hi
    return v


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
        try:
            d['qty_unit'] = m['qty_unit'] or '平米'
        except (IndexError, KeyError):
            d['qty_unit'] = '平米'
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

def opt_ym(m):
    """可选月份：没传或格式不对都返回 ''（表示不限月份）。

    不能拿 safe_ym 顶替 —— 它没传时会回退到当前月，等于悄悄加了个筛选条件：
    上一版 CSV 导出就是这么「默认只导当月」的，看着像全量其实不是。
    """
    m = (m or '').strip() if isinstance(m, str) else ''
    if len(m) == 7 and m[4] == '-' and m[:4].isdigit() and m[5:].isdigit():
        return m
    if len(m) == 6 and m[4] == '-' and m[:4].isdigit() and m[5].isdigit():
        return m[:4] + '-0' + m[5]
    return ''


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


# ---------- 列名默认显示名 ----------
# 模板里改了显示名就以模板为准，这里是兜底。
LABELS = {'name': '物料名称', 'code': '料号', 'supplier': '供应商', 'category': '类型',
          'spec': '规格', 'width': '宽幅', 'unit': '单位', 'status': '状态',
          'opening': '期初结存', 'safety': '安全库存'}
