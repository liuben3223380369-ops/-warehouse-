# -*- coding: utf-8 -*-
"""采购金额口径与物料指纹 —— 全系统金额计算的唯一入口。

为什么单独成文件：金额口径散在各处必然对不上（把含税价当不含税是最典型的坑）。
这里提供 net_price / line_amount / price_map 三个原语，其它模块一律调这里，
不允许自己写 qty*price。

依赖：仅 core.db，无内部依赖（本包最底层）。
"""
from datetime import datetime
import os

from ..core import db


def _log_err(tag, detail=''):
    """把异常写进程序目录的 warehouse.log。

    为什么不能 except: pass —— 汇总快照保存失败时用户看不到任何提示，
    汇总页会静静显示上一版旧数据。这种"静默失败"最坑：数字看着正常，
    其实是过期的。窗口模式没有控制台，不落盘就永远查不到原因。
    """
    try:
        import traceback
        base = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(base, 'warehouse.log'), 'a', encoding='utf-8',
                  errors='replace') as f:
            f.write('[%s] %s\n%s\n%s\n' % (
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'), tag,
                detail or traceback.format_exc(), '-' * 46))
    except Exception:
        pass


# ---------- 金额计算（唯一入口，全系统都调这里，保证口径一致） ----------
def net_price(price, tax_rate=0, price_tax=1):
    """把单价统一折算成**不含税**单价。

    采购报价有两种习惯：有的供应商报含税价（多数），有的报不含税价。
    存的 price 就是当时填的那个数，到底是哪种由 pos.price_tax 决定。
    所有金额计算都先过这道折算，口径才不会乱。
    """
    try:
        p = float(price or 0)
    except (TypeError, ValueError):
        return 0.0
    if not price_tax:
        return p
    try:
        r = float(tax_rate or 0) / 100.0
    except (TypeError, ValueError):
        r = 0.0
    if r <= -1:                      # 税率 -100% 及更离谱的值：不折算，免得除出负数
        return p
    return p / (1.0 + r)


def _norm_txt(s):
    """指纹用：去空格/括号/全角，统一小写。

    采购单里「铜箔(卷)」「铜箔 （卷）」「铜箔卷」是同一个东西，
    不归一化就配不上，映射关系会静默失效。
    """
    import re as _r
    t = str(s or '').strip()
    t = _r.sub(r'[（(].*?[)）]', '', t)      # 去掉括号及其内容
    t = _r.sub(r'[\s\u3000]+', '', t)       # 去空格/全角空格
    return t.lower()


def item_sig(name, spec='', unit='', supplier=''):
    """采购明细行的指纹：供应商 + 物料名 + 规格 + 单位。

    为什么四样都要：同名不同规格（铜箔 0.05 / 铜箔 0.1）是两种货，
    单价差很多，只按名称匹配会把贵的价套到便宜的上，金额全错。
    """
    parts = [_norm_txt(x) for x in (supplier, name, spec, unit)]
    parts = [p for p in parts if p]
    return '|'.join(parts) if parts else ''


def item_sig_keys(name, spec='', unit='', supplier=''):
    """同一行可能配得上的几种指纹，按"越全越准"排序。

    供应商经常不写、规格有时空着，所以给 4 档回退，
    先用最全的配，配不上再逐级放宽。
    """
    n, s, u, sup = (_norm_txt(name), _norm_txt(spec),
                    _norm_txt(unit), _norm_txt(supplier))
    out = []
    def add(*xs):
        v = '|'.join([x for x in xs if x])
        if v and v not in out:
            out.append(v)
    add(sup, n, s, u)
    add(n, s, u)
    add(n, s)
    add(n)
    return out


def backfill_sigs():
    """给老采购明细补指纹（老库升级时用一次）。"""
    try:
        n = 0
        for it in db.q("SELECT i.id, i.name, i.spec, i.unit, p.supplier"
                       " FROM po_items i LEFT JOIN pos p ON p.id=i.po_id"
                       " WHERE COALESCE(i.sig,'')=''"):
            sg = item_sig(it['name'], it['spec'], it['unit'], it['supplier'] or '')
            if sg:
                db.run("UPDATE po_items SET sig=? WHERE id=?", sg, it['id'])
                n += 1
        return n
    except Exception as e:
        _log_err('backfill_sigs', str(e))
        return 0


def price_map():
    """采购到货实价映射表：{指纹 or 物料id: 不含税单价}。

    流水页的金额为什么要靠它算 —— 入库随手填的单价（甚至空着）不代表真实成本，
    真正付出去的钱记在采购到货上。映射规则：
      · 取该明细**最近一次到货**的实价（供应商调价是常态，实收的价才准）
      · 折成**不含税**（存货成本口径，税率不同的单子混在一起也不会乱）
      · 除以换算率 conv（按卷采购、按平米入库时，单价要跟着换算）
    同一把钥匙被多个明细命中时，后登记的（id 更大）覆盖前者。
    """
    out, by_mid = {}, {}
    try:
        for r in db.q(
            "SELECT i.id, i.material_id mid, i.name, i.spec, i.unit, i.conv,"
            " i.sig, p.supplier, p.tax_rate, p.price_tax,"
            " (SELECT pr.price FROM po_receipts pr WHERE pr.item_id=i.id"
            "  ORDER BY pr.rdate DESC, pr.id DESC LIMIT 1) last_price,"
            " i.price AS ord_price"
            " FROM po_items i LEFT JOIN pos p ON p.id=i.po_id"
            " ORDER BY i.id"):
            try:
                tax = float(r['tax_rate'] or 0)
            except (TypeError, ValueError):
                tax = 0.0
            try:
                pt = int(r['price_tax']) if r['price_tax'] is not None else 1
            except (TypeError, ValueError):
                pt = 1
            raw = r['last_price']
            if raw is None:
                raw = r['ord_price']
            try:
                raw = float(raw or 0)
            except (TypeError, ValueError):
                raw = 0.0
            if raw <= 0:
                continue
            unit_p = net_price(raw, tax, pt)
            if unit_p <= 0:
                continue
            try:
                conv = float(r['conv'] or 1) or 1.0
            except (TypeError, ValueError):
                conv = 1.0
            unit_p = unit_p / conv
            # 除税、除换算率会带出浮点毛刺（100.0 -> 20.000000000000004），
            # 显示是两位小数看不出来，但累加金额时会飘，这里先收敛掉
            unit_p = round(unit_p, 6)
            if unit_p <= 0:
                continue

            sig = (r['sig'] or '').strip() or item_sig(
                r['name'], r['spec'], r['unit'], r['supplier'] or '')
            keys = item_sig_keys(r['name'], r['spec'], r['unit'], r['supplier'] or '')
            for k in keys:
                out[k] = unit_p
            if sig and sig not in out:
                out[sig] = unit_p
            if r['mid']:
                by_mid[int(r['mid'])] = unit_p
    except Exception as e:
        _log_err('price_map', str(e))

    # 批次级映射：每一批到货一个价，比"物料最新价"精确得多。
    # 同一物料分三批进、每批价不同时，只有批次能对上真实成本。
    by_batch = {}
    try:
        for r in db.q(
            "SELECT pr.batch, pr.price, pr.rdate, pr.id, i.material_id mid, i.name,"
            " i.spec, i.unit, i.conv, i.sig, p.supplier, p.tax_rate, p.price_tax"
            " FROM po_receipts pr JOIN po_items i ON i.id=pr.item_id"
            " LEFT JOIN pos p ON p.id=i.po_id"
            " WHERE COALESCE(pr.batch,'')<>''"
            " ORDER BY pr.rdate, pr.id"):
            b = (r['batch'] or '').strip()
            if not b:
                continue
            try:
                tax = float(r['tax_rate'] or 0)
                pt = int(r['price_tax']) if r['price_tax'] is not None else 1
            except (TypeError, ValueError):
                tax, pt = 0.0, 1
            try:
                raw = float(r['price'] or 0)
            except (TypeError, ValueError):
                continue
            if raw <= 0:
                continue
            up = net_price(raw, tax, pt)
            if up <= 0:
                continue
            try:
                conv = float(r['conv'] or 1) or 1.0
            except (TypeError, ValueError):
                conv = 1.0
            up = round(up / conv, 6)
            if up <= 0:
                continue
            # 记下这一批属于哪个物料（用指纹集合校验，防止手工批次号跨物料重名）
            keys = set(item_sig_keys(r['name'], r['spec'], r['unit'], r['supplier'] or ''))
            if (r['sig'] or '').strip():
                keys.add((r['sig'] or '').strip())
            old = by_batch.get(b)
            if old:
                # 同批次多条到货（补货同批次）：保留较晚的一次，但并集物料指纹
                old['keys'] |= keys
                if r['mid'] and not old['mid']:
                    old['mid'] = r['mid']
                old['p'] = up
            else:
                by_batch[b] = {'p': up, 'keys': keys, 'mid': r['mid']}
    except Exception as e:
        _log_err('price_map(batch)', str(e))
    out['__mid__'] = by_mid
    out['__batch__'] = by_batch
    return out


def txn_unit_price(pmap, mid, name, spec='', unit='', supplier='', batch=''):
    """给一条出入库单据查映射单价，返回 (单价, 来源)。

    优先级：**批次 > 物料id > 指纹逐级回退**。
    批次能命中就用这一批的实价（最准）；没有批次或批次对不上，
    才退到"该物料最近一次到货价"。

    批次命中要校验物料：手工填的批次号可能在不同物料间重名（比如都填"A"），
    不校验就会把 A 批铜箔的价套到 A 批胶水头上。
    """
    if not pmap:
        return None, ''
    b = (batch or '').strip()
    if b:
        bm = (pmap.get('__batch__') or {}).get(b)
        if bm:
            ok = False
            if mid and bm.get('mid') and int(mid) == int(bm['mid']):
                ok = True
            else:
                keys = set(item_sig_keys(name, spec, unit, supplier))
                ok = bool(keys & (bm.get('keys') or set()))
            # 批次号唯一且只有一个物料用过它时，放宽也认（老数据可能没记物料）
            if not ok and len((pmap.get('__batch__') or {})) and not bm.get('mid') and not bm.get('keys'):
                ok = True
            if ok:
                return bm['p'], 'batch'
    by_mid = pmap.get('__mid__') or {}
    if mid and int(mid) in by_mid:
        return by_mid[int(mid)], 'mat'
    for k in item_sig_keys(name, spec, unit, supplier):
        if k in pmap:
            return pmap[k], 'mat'
    return None, ''


def line_amount(qty, price, tax_rate=0, price_tax=1):
    """单行金额：(不含税金额, 税额, 价税合计)

    price_tax=1 表示 price 是含税单价，需要先除税；0 表示本身就是不含税价。
    """
    try:
        QMAX = 1e9      # 与 app/importer 的 QTY_MAX 一致
        q = float(qty or 0)
        if q > QMAX:
            raise ValueError('数量太大（上限 %d），请检查是否多输了几个零' % int(QMAX))
    except (TypeError, ValueError):
        q = 0.0
    p = net_price(price, tax_rate, price_tax)
    amt = round(q * p, 2)
    tax = round(amt * float(tax_rate or 0) / 100.0, 2)
    return amt, tax, round(amt + tax, 2)
