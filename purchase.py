# -*- coding: utf-8 -*-
"""采购台账 —— 与仓库库存分离的业务模块。

设计原则（制造业工业级）：
1. 采购单(PO)是**业务凭证**，仓库单据(txn)是**库存事实**，两者分开存。
   采购到货时才生成入库单，避免"还没到货但库存已经涨了"。
2. 金额一律现算不落库：只存 数量 + 单价，金额/税额/价税合计由查询时算出。
   存两份必然会在改数量或改税率时对不上。
3. 单价有两个：订购价(po_items.price) 和 到货实价(po_receipts.price)。
   供应商调价是常态，分开记才能做"计划价 vs 实际价"的差异分析。
4. 状态由数据推导，不手工维护 —— 到货数量变了状态自动跟着变，
   不会出现"明细都到齐了，单头还写着部分到货"这种脏数据。
"""
from datetime import datetime
import os
import db

STATUS = ['草稿', '已下单', '部分到货', '已完成', '已取消']


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


def po_head(po_id):
    """取单头的税率与"单价是否含税"。所有金额计算都从这里取口径，避免各处写法不一"""
    r = db.q("SELECT tax_rate, price_tax FROM pos WHERE id=?", po_id)
    if not r:
        return 0.0, 1
    try:
        tax = float(r[0]['tax_rate'] or 0)
    except (TypeError, ValueError):
        tax = 0.0
    try:
        pt = int(r[0]['price_tax']) if r[0]['price_tax'] is not None else 1
    except (TypeError, ValueError):
        pt = 1
    return tax, (1 if pt else 0)


def po_totals(po_id):
    """整单汇总：订购/到货/未到 的数量与金额（金额一律不含税，税额单列）"""
    items = db.q("SELECT * FROM po_items WHERE po_id=? ORDER BY id", po_id)
    tax, pt = po_head(po_id)
    tot_qty = tot_amt = tot_tax = 0.0
    recv_qty = recv_amt = 0.0
    real_amt = 0.0          # 按到货实价计的金额（可能与订购价不同）
    for it in items:
        a, t, _ = line_amount(it['qty'], it['price'], tax, pt)
        tot_qty += float(it['qty'] or 0)
        tot_amt += a
        tot_tax += t
        rq = float(it['recv_qty'] or 0)
        recv_qty += rq
        # 到货金额按"实收"算：先取到货记录的实价，没有就退回订购价
        got = db.q("SELECT COALESCE(SUM(qty),0) q, COALESCE(SUM(qty*price),0) s"
                   " FROM po_receipts WHERE item_id=?", it['id'])
        rqty = float(got[0]['q'] or 0)
        real = float(got[0]['s'] or 0)
        if rqty:
            # 到货实价与订购价同一口径，先折成不含税再汇总
            real_amt += round(rqty * net_price(real / rqty, tax, pt), 2)
            recv_amt += round(rqty * net_price(real / rqty, tax, pt), 2)
        else:
            recv_amt += round(rq * net_price(it['price'], tax, pt), 2)
    return {
        'qty': tot_qty, 'amount': round(tot_amt, 2), 'tax': round(tot_tax, 2),
        'total': round(tot_amt + tot_tax, 2),
        'recv_qty': recv_qty, 'recv_amount': round(recv_amt, 2),
        'open_qty': round(tot_qty - recv_qty, 2),
        'real_amount': round(real_amt, 2),
        'tax_rate': tax,
        'price_tax': pt,
    }


def paid_amount(po_id):
    got = db.q("SELECT COALESCE(SUM(amount),0) s FROM po_payments WHERE po_id=?", po_id)
    return round(float(got[0]['s'] or 0), 2)


def owed(po_id):
    """欠款 = 到货价税合计 − 已付。只按实际到货算，没到货的不该付钱"""
    t = po_totals(po_id)
    _, tax_amt, recv_total = line_amount(1, t['recv_amount'], t['tax_rate'], 0)
    return round(recv_total - paid_amount(po_id), 2), recv_total


# ---------- 汇总快照：把整单金额落成一张表，便于单独查看与历史对账 ----------
def save_summary(po_id):
    """重算并保存采购单汇总快照。

    为什么要落库：金额原本全是现算的，改了税率/单价后历史单的金额也跟着变，
    跟当时打印出来给供应商的凭证对不上。落一份快照，翻旧单看到的就是当时的数。
    建单、改明细、到货、撤销到货、付款、删付款、改税率 都要调一次。
    """
    t = po_totals(po_id)
    paid = paid_amount(po_id)
    _, recv_total = owed(po_id)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    db.run("INSERT INTO po_summary(po_id,qty,amount,tax,total,recv_qty,recv_amount,"
           "recv_total,paid,owed,open_qty,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)"
           " ON CONFLICT(po_id) DO UPDATE SET qty=excluded.qty, amount=excluded.amount,"
           " tax=excluded.tax, total=excluded.total, recv_qty=excluded.recv_qty,"
           " recv_amount=excluded.recv_amount, recv_total=excluded.recv_total,"
           " paid=excluded.paid, owed=excluded.owed, open_qty=excluded.open_qty,"
           " updated_at=excluded.updated_at",
           po_id, t['qty'], t['amount'], t['tax'], t['total'],
           t['recv_qty'], t['recv_amount'], recv_total,
           paid, round(recv_total - paid, 2), t['open_qty'], now)
    return get_summary(po_id)


def get_summary(po_id):
    r = db.q("SELECT * FROM po_summary WHERE po_id=?", po_id)
    return dict(r[0]) if r else None


def summary_list(st='', sup='', kw='', m=''):
    """汇总清单：可直接看，也可导出"""
    w, a = [], []
    if st:
        w.append("p.status=?"); a.append(st)
    if sup:
        w.append("p.supplier=?"); a.append(sup)
    if kw:
        w.append("(p.pono LIKE ? OR p.supplier LIKE ?)"); a += ['%%%s%%' % kw] * 2
    if m:
        w.append("p.odate LIKE ?"); a.append(m + '%')
    where = (" WHERE " + " AND ".join(w)) if w else ""
    rows = []
    for r in db.q("SELECT p.id,p.pono,p.odate,p.ddate,p.supplier,p.status,p.tax_rate,"
                  " p.price_tax, s.* FROM pos p"
                  " LEFT JOIN po_summary s ON s.po_id=p.id"
                  + where + " ORDER BY p.odate DESC, p.id DESC LIMIT 500", *a):
        d = dict(r)
        for k in ('qty', 'amount', 'tax', 'total', 'recv_qty', 'recv_amount',
                  'recv_total', 'paid', 'owed', 'open_qty'):
            d[k] = round(float(d.get(k) or 0), 2)
        rows.append(d)
    tot = {k: round(sum(r[k] for r in rows), 2)
           for k in ('amount', 'tax', 'total', 'recv_amount', 'recv_total',
                     'paid', 'owed')}
    return rows, tot


def sync_all():
    """把没有快照或已过期的采购单全部重算一遍（升级老库、或发现数据对不上时用）"""
    n = 0
    for r in db.q("SELECT id FROM pos ORDER BY id"):
        save_summary(r['id']); n += 1
    return n


# ---------- 状态：由到货数据推导，不手工维护 ----------
def derive_status(po_id):
    head = db.q("SELECT status FROM pos WHERE id=?", po_id)
    if not head:
        return None
    cur = head[0]['status']
    if cur in ('草稿', '已取消'):
        return cur                      # 人工状态，不自动改
    items = db.q("SELECT qty, recv_qty FROM po_items WHERE po_id=?", po_id)
    if not items:
        return cur
    tot = sum(float(i['qty'] or 0) for i in items)
    got = sum(float(i['recv_qty'] or 0) for i in items)
    if got <= 0:
        return '已下单'
    if got >= tot - 1e-9:
        return '已完成'
    return '部分到货'


def refresh_status(po_id):
    """到货/取消到货后调用，把推导出的状态写回单头"""
    st = derive_status(po_id)
    if st:
        db.run("UPDATE pos SET status=? WHERE id=?", st, po_id)
    return st


# ---------- 单号：CG + 日期 + 当日流水，保证唯一 ----------
def next_pono(odate=None):
    d = (odate or datetime.now().strftime('%Y-%m-%d')).replace('-', '')
    pre = 'CG' + d + '-'
    last = db.q("SELECT pono FROM pos WHERE pono LIKE ? ORDER BY pono DESC LIMIT 1", pre + '%')
    n = 1
    if last:
        try:
            n = int(last[0]['pono'].rsplit('-', 1)[-1]) + 1
        except (ValueError, IndexError):
            n = 1
    while db.q("SELECT id FROM pos WHERE pono=?", pre + '%03d' % n):
        n += 1
    return pre + '%03d' % n


# ---------- 到货入库：采购与库存的唯一连接点 ----------
def receive(item_id, rdate, qty, price=None, note=''):
    """登记到货，并生成入库单。
    返回 (ok, msg)。任何一步失败都整体回滚，绝不出现"记了到货但没入库"。
    """
    try:
        qty = float(qty or 0)
    except (TypeError, ValueError):
        return False, '数量必须是数字'
    if qty <= 0:
        return False, '到货数量必须大于 0'
    if qty > 1e9:
        return False, '到货数量 %g 太大了，请确认是不是多输了几个零' % qty

    # 日期自校验：不能只靠路由层的 safe_date，
    # 直接调函数的路径（将来加接口/脚本）也必须挡住 2026-13-45 这种非法日期
    rdate = (rdate or '').strip()
    try:
        datetime.strptime(rdate, '%Y-%m-%d')
    except (ValueError, TypeError):
        return False, '到货日期不合法（要 YYYY-MM-DD 格式的真实日期）：%r' % (rdate,)

    it = db.q("SELECT * FROM po_items WHERE id=?", item_id)
    if not it:
        return False, '找不到这条采购明细'
    it = it[0]
    po = db.q("SELECT * FROM pos WHERE id=?", it['po_id'])
    if not po:
        return False, '找不到采购单'
    po = po[0]
    if po['status'] == '已取消':
        return False, '采购单已取消，不能到货'
    if po['status'] == '草稿':
        db.run("UPDATE pos SET status='已下单' WHERE id=?", po['id'])

    unit_price = float(price) if price not in (None, '') else float(it['price'] or 0)
    remain = round(float(it['qty'] or 0) - float(it['recv_qty'] or 0), 6)
    if qty > remain + 1e-9:
        return False, '到货 %g 超过未交数量 %g（订购 %g，已到 %g）' % (
            qty, remain, it['qty'], it['recv_qty'])

    # 双单位换算：按 采购单位 收货，按 库存单位 入账。
    # 例：采购 10 卷，1 卷 = 100 平米 -> 库存 +1000 平米。
    # 单价也要跟着换算，否则 10 卷的价钱会算成 1000 平米的价钱，成本翻 100 倍。
    conv = float(it['conv'] or 1) or 1.0
    stock_qty = round(qty * conv, 6)
    stock_price = round(unit_price / conv, 6) if conv else unit_price

    # 物料：优先用明细上关联的，其次按名称找，都没有就新建
    mid = it['material_id']
    if not mid:
        hit = db.q("SELECT id FROM materials WHERE name=?", it['name'])
        mid = hit[0]['id'] if hit else None
    if not mid:
        # 归入采购单指定的模板；没指定就归默认模板，
        # 否则这些物料会变成"无模板"，库存页按模板筛选看不到、月报多出一个"未归类"
        mid = db.run("INSERT INTO materials(name,spec,unit,opening,safety,status,tpl_id)"
                     " VALUES(?,?,?,?,?,?,?)", it['name'], it['spec'], it['unit'], 0, 0,
                     '常用', po['tpl_id'] or db.default_tpl_id())
    db.run("UPDATE po_items SET material_id=? WHERE id=?", mid, item_id)

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    memo = '采购到货 %s' % po['pono']
    if note:
        memo += ' · ' + note
    try:
        with db.tx() as c:
            txn_id = c.execute(
                "INSERT INTO txns(tdate,material_id,kind,qty,price,note,created_at,tpl_id)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (rdate, mid, '进', stock_qty, stock_price, memo, now,
                 po['tpl_id'] or db.default_tpl_id())).lastrowid
            c.execute("INSERT INTO po_receipts(item_id,txn_id,rdate,qty,price,note,created_at)"
                      " VALUES(?,?,?,?,?,?,?)",
                      (item_id, txn_id, rdate, qty, unit_price, note, now))
            c.execute("UPDATE po_items SET recv_qty=ROUND(recv_qty+?,6) WHERE id=?", (qty, item_id))
    except Exception as e:
        return False, '到货登记失败：%s' % e
    refresh_status(po['id'])
    try:
        save_summary(po['id'])
    except Exception as _e:
        # 不静默：快照失败必须留痕，否则汇总页显示旧数据用户还以为是对的
        _log_err('采购汇总快照保存失败 po_id=%s（到货后）' % po['id'],
                 'save_summary: %s' % _e)
    if abs(conv - 1.0) > 1e-9:
        return True, ('已到货 %g%s，按 1%s=%g%s 折算入库 %g%s（成本单价 %g/%s）'
                      % (qty, it['unit'], it['unit'], conv, it['stock_unit'] or it['unit'],
                         stock_qty, it['stock_unit'] or it['unit'],
                         round(stock_price, 4), it['stock_unit'] or it['unit']))
    return True, '已到货 %g%s，生成入库单（单价 %g）' % (qty, it['unit'], unit_price)


def set_item_unit(item_id, unit=None, conv=None, stock_unit=None, price=None):
    """随时改单位。换算率变了不影响已入库存量（历史单据记的是当时的数），
    只影响之后的新到货。这样"发现单位写错了"改一下就行，不用删单重来。"""
    it = db.q("SELECT * FROM po_items WHERE id=?", item_id)
    if not it:
        return False, '找不到这条明细'
    it = it[0]
    sets, vals = [], []
    if unit is not None:
        u = (unit or '').strip()[:20]
        if not u:
            return False, '单位不能为空'
        sets.append("unit=?"); vals.append(u)
    if stock_unit is not None:
        sets.append("stock_unit=?"); vals.append((stock_unit or '').strip()[:20])
    if conv is not None:
        try:
            cv = float(conv)
        except (TypeError, ValueError):
            return False, '换算率必须是数字'
        if cv <= 0:
            return False, '换算率要大于 0'
        sets.append("conv=?"); vals.append(cv)
    if price is not None:
        try:
            pv = float(price)
        except (TypeError, ValueError):
            return False, '单价必须是数字'
        if pv < 0:
            return False, '单价不能为负'
        sets.append("price=?"); vals.append(pv)
    if not sets:
        return False, '没有要改的内容'
    vals.append(item_id)
    db.run("UPDATE po_items SET " + ",".join(sets) + " WHERE id=?", *vals)
    # 换算率/单价变了，到货金额和欠款都得跟着重算，否则汇总表是旧的
    got = db.q("SELECT po_id FROM po_items WHERE id=?", item_id)
    if got:
        try:
            save_summary(got[0]['po_id'])
        except Exception as _e:
            _log_err('采购汇总快照保存失败 po_id=%s（改单位/单价后）' % got[0]['po_id'],
                     'save_summary: %s' % _e)
    return True, '已更新：%s' % ('、'.join(
        x.split('=')[0] for x in sets))


def unreceive(receipt_id):
    """取消一次到货：删到货记录 + 删对应入库单 + 扣回累计数量。"""
    r = db.q("SELECT * FROM po_receipts WHERE id=?", receipt_id)
    if not r:
        return False, '找不到这条到货记录'
    r = r[0]
    it = db.q("SELECT * FROM po_items WHERE id=?", r['item_id'])
    po_id = it[0]['po_id'] if it else None
    try:
        with db.tx() as c:
            # 顺序很关键：po_receipts.txn_id 外键指向 txns，
            # 先删 txns 会被外键拦下。必须先断开引用再删。
            c.execute("UPDATE po_receipts SET txn_id=NULL WHERE id=?", (receipt_id,))
            if r['txn_id']:
                c.execute("DELETE FROM txns WHERE id=?", (r['txn_id'],))
            c.execute("DELETE FROM po_receipts WHERE id=?", (receipt_id,))
            c.execute("UPDATE po_items SET recv_qty=MAX(0, ROUND(recv_qty-?,6)) WHERE id=?",
                      (r['qty'], r['item_id']))
    except Exception as e:
        return False, '取消失败：%s' % e
    if po_id:
        refresh_status(po_id)
        try:
            save_summary(po_id)
        except Exception as _e:
            _log_err('采购汇总快照保存失败 po_id=%s（撤销到货后）' % po_id,
                     'save_summary: %s' % _e)
    return True, '已取消到货 %g，库存同步扣回' % r['qty']


def delete_item(item_id):
    """删除采购明细。

    注意：不能直接 DELETE —— 明细下的到货记录会被级联删掉，
    但到货时生成的入库单(txns)不会跟着消失，于是库存凭空多出一批货，
    既没凭证可查、也对不上账。所以先逐条撤销到货，再删明细。
    """
    it = db.q("SELECT * FROM po_items WHERE id=?", item_id)
    if not it:
        return False, '找不到这条明细'
    po_id = it[0]['po_id']
    recs = db.q("SELECT id FROM po_receipts WHERE item_id=? ORDER BY id", item_id)
    n = 0
    for r in recs:
        ok, msg = unreceive(r['id'])
        if not ok:
            return False, '撤销到货失败，明细未删除：%s' % msg
        n += 1
    db.run("DELETE FROM po_items WHERE id=?", item_id)
    refresh_status(po_id)
    try:
        save_summary(po_id)
    except Exception as _e:
        _log_err('采购汇总快照保存失败 po_id=%s（删明细后）' % po_id,
                 'save_summary: %s' % _e)
    tip = '明细已删除' + ('（同时撤销 %d 次到货，库存已扣回）' % n if n else '')
    return True, tip


# ---------- 供应商：从采购单自动沉淀，也支持手工维护 ----------
def touch_supplier(name):
    if not (name or '').strip():
        return
    if not db.q("SELECT id FROM suppliers WHERE name=?", name.strip()):
        db.run("INSERT INTO suppliers(name) VALUES(?)", name.strip())


def supplier_list():
    rows = []
    for s in db.q("SELECT * FROM suppliers ORDER BY active DESC, name"):
        name = s['name']
        g = db.q("SELECT COUNT(*) c, COALESCE(SUM(i.qty*i.price),0) amt,"
                 " COALESCE(SUM(i.recv_qty),0) rq FROM pos p JOIN po_items i ON i.po_id=p.id"
                 " WHERE p.supplier=? AND p.status<>'已取消'", name)[0]
        paid = db.q("SELECT COALESCE(SUM(m.amount),0) s FROM po_payments m"
                    " JOIN pos p ON p.id=m.po_id WHERE p.supplier=?", name)[0]['s']
        owed_ = round(float(g['amt'] or 0) - float(paid or 0), 2)
        rows.append(dict(id=s['id'], name=name, contact=s['contact'], phone=s['phone'],
                         orders=g['c'], amount=round(float(g['amt'] or 0), 2),
                         recv_qty=g['rq'], paid=round(float(paid or 0), 2),
                         owed=owed_, active=s['active']))
    return rows


# ---------- 看板：采购首页要用的汇总 ----------
def dashboard():
    ym = datetime.now().strftime('%Y-%m') + '%'
    def sc(sql, *a):
        r = db.q(sql, *a)
        return float(r[0][0] or 0) if r and r[0][0] is not None else 0.0
    month_amt = sc("SELECT COALESCE(SUM(i.qty*i.price),0) FROM po_items i JOIN pos p"
                   " ON p.id=i.po_id WHERE p.odate LIKE ? AND p.status<>'已取消'", ym)
    open_cnt = sc("SELECT COUNT(*) FROM pos WHERE status IN ('已下单','部分到货')")
    # 逾期：过了要求交期还没完成
    today = datetime.now().strftime('%Y-%m-%d')
    overdue = sc("SELECT COUNT(*) FROM pos WHERE ddate<>'' AND ddate<?"
                 " AND status IN ('已下单','部分到货')", today)
    draft = sc("SELECT COUNT(*) FROM pos WHERE status='草稿'")
    paid_m = sc("SELECT COALESCE(SUM(amount),0) FROM po_payments WHERE pdate LIKE ?", ym)
    return {
        'month_amount': round(month_amt, 2),
        'open_orders': int(open_cnt),
        'overdue': int(overdue),
        'draft': int(draft),
        'paid_month': round(paid_m, 2),
    }
