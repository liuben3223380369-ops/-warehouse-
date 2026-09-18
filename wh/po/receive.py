# -*- coding: utf-8 -*-
"""到货入库 —— 采购与库存的唯一连接点。

采购单只是业务凭证，到货时才生成入库单；这条边界守住了"还没到货但库存已涨"。

依赖：amount / status / summary
"""
from datetime import datetime

from ..core import db
from .amount import _log_err, item_sig
from .status import refresh_status
from .summary import save_summary



# ---------- 到货入库：采购与库存的唯一连接点 ----------
def new_item_batch(po_id):
    """给新明细生成批次号：单号-本单第几条。

    一条明细一个号，入库时填它就能对上这条明细。使用者也可以在采购详情页
    改成自己好记的号（比如供应商的送货批号），只要唯一即可。
    例：CG20260916-001 的第 2 条明细 -> CG20260916-001-2
    """
    try:
        po = db.q("SELECT pono FROM pos WHERE id=?", po_id)
        pono = (po[0]['pono'] or 'PO%s' % po_id) if po else 'PO%s' % po_id
        n = db.q("SELECT COUNT(*) c FROM po_items WHERE po_id=?", po_id)
        base = '%s-%d' % (pono, (n[0]['c'] if n else 0) + 1)
        cand, i = base, 1
        while db.q("SELECT id FROM po_items WHERE batch=?", cand):
            i += 1
            cand = '%s(%d)' % (base, i)
        return cand
    except Exception:
        return ''


def next_batch(po_id, item_id, rdate):
    """生成本次到货的批次号：单号-明细序号-到货序号。

    为什么自动生成：让使用者手编批次号容易重号、也懒得填，
    自动给一个唯一可读的号，他只在想自定义时才填。
    例：CG20260916-001 第 2 条明细的第 3 次到货 -> CG20260916-001-2-3
    """
    try:
        po = db.q("SELECT pono FROM pos WHERE id=?", po_id)
        pono = (po[0]['pono'] or 'PO%s' % po_id) if po else 'PO%s' % po_id
        seq = db.q("SELECT COUNT(*) c FROM po_items WHERE po_id=? AND id<=?", po_id, item_id)
        n = db.q("SELECT COUNT(*) c FROM po_receipts WHERE item_id=?", item_id)
        base = '%s-%d-%d' % (pono, (seq[0]['c'] if seq else 1), (n[0]['c'] if n else 0) + 1)
        # 极端情况下（同一明细同一天多次到货又手工填了同号）加后缀保唯一
        cand = base
        i = 1
        while db.q("SELECT id FROM po_receipts WHERE batch=?", cand):
            i += 1
            cand = '%s(%d)' % (base, i)
        return cand
    except Exception:
        return ''


def receive(item_id, rdate, qty, price=None, note='', batch=None):
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

    # 采购与库存互相独立：到货只做状态登记，不生成入库单、不动库存。
    # 唯一的联系是月报统计时会读取这里的到货数据做汇总。
    # 物料只做**只读匹配**（按名称找已有档案），找不到也不自动建档——
    # 建档会往库存里塞一个空物料，等于采购又反过来影响了库存。
    mid = it['material_id']
    if not mid:
        hit = db.q("SELECT id FROM materials WHERE name=?", it['name'])
        mid = hit[0]['id'] if hit else None
        if mid:
            db.run("UPDATE po_items SET material_id=? WHERE id=?", mid, item_id)

    # 批次号：没填就自动生成（单号-明细序-到货序），填了就用使用者的。
    # 批次是"这一批货"的身份，入库时填同一个号就能精确对上这一批的实价。
    bt = (batch or '').strip()
    if not bt:
        bt = next_batch(po['id'], item_id, rdate)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        with db.tx() as c:
            c.execute("INSERT INTO po_receipts(item_id,txn_id,rdate,qty,price,note,created_at,batch)"
                      " VALUES(?,?,?,?,?,?,?,?)",
                      (item_id, None, rdate, qty, unit_price, note, now, bt))
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
    tip = '已登记到货 %g%s' % (qty, it['unit'])
    if bt:
        tip += '，批次 %s' % bt
    if abs(conv - 1.0) > 1e-9:
        tip += '（折合 %g%s，仅供统计，不入库存）' % (
            stock_qty, it['stock_unit'] or it['unit'])
    if not mid:
        tip += '；物料档案里没有同名物料，月报会按采购名称单独统计'
    return True, tip


def sync_txn_to_po(batch, name='', mid=None, qty=0, price=None, date='', note='',
                   txn_id=None, qty_unit=''):
    """入库页按批次号录入时，同步生成一条采购流水（用于结单）。

    这是 v3.35 的核心：采购与库存**不直接牵连**——库存记自己的 txns，
    采购记自己的 po_receipts，两者只通过「批次号」这个桥联系。
    到货数按**入库实际录入**算，采购页不再手工登记到货。

    qty_unit：这一笔数量填的是按「平米」还是按「卷」（v3.27 的双口径）。
    必须带上它来折采购单位，见下面 _to_po_qty 的说明。

    返回 (ok, msg)。找不到批次时返回 (False, '')——没对上采购单是正常的
    （自产、调拨、没建采购单的货），不该报错打断入库。
    """
    b = (batch or '').strip()
    if not b:
        return False, ''
    try:
        qty = float(qty or 0)
    except (TypeError, ValueError):
        return False, ''
    if qty <= 0:
        return False, ''

    rows = db.q("SELECT i.*, p.status pstatus, p.tax_rate, p.price_tax, p.pono"
                " FROM po_items i JOIN pos p ON p.id=i.po_id"
                " WHERE TRIM(COALESCE(i.batch,''))=? ORDER BY i.id", b)
    if not rows:
        return False, ''
    it = None
    for r in rows:
        if mid and r['material_id'] and int(r['material_id']) == int(mid):
            it = r
            break
    if it is None:
        for r in rows:
            if (r['name'] or '').strip() and (name or '').strip() \
                    and (r['name'] or '').strip() == (name or '').strip():
                it = r
                break
    if it is None:
        it = rows[0]
    if it['pstatus'] == '已取消':
        return False, ''
    # 有货到了就不再是草稿（原先靠采购页手登到货时自动转，现在到货改由入库驱动，
    # 这一步得在这里做，否则单子永远是草稿、结单推不动）
    if it['pstatus'] == '草稿':
        db.run("UPDATE pos SET status='已下单' WHERE id=?", it['po_id'])

    # 入库数量 → 采购单位。
    # 采购记的是采购单位（卷），入库填的可能是库存单位（平米），所以要折算。
    #
    # v3.42 修的坑：以前不看口径，一律 qty/conv。但使用者在入库页选了
    # 「数量按 卷」时，qty 本来就是卷，再除一次换算率就少记了 conv 倍 ——
    # 订 10 卷、供应商交齐 10 卷，采购单却只记 2 卷，一直停在「部分到货」，
    # 结不了单、欠款也对不上。
    # 所以：口径跟采购单位一致就原样记，跟库存单位一致才除换算率；
    # 老单据没记口径（v3.27 之前）维持原行为除换算率，不改动历史数据。
    try:
        conv = float(it['conv'] or 1) or 1.0
    except (TypeError, ValueError):
        conv = 1.0
    _qu = (qty_unit or '').strip()
    _pu = (it['unit'] or '').strip()             # 采购单位（卷）
    _su = (it['stock_unit'] or '').strip()       # 库存单位（平米）
    if _qu and _pu and _qu == _pu:
        po_qty = qty                              # 填的就是卷，不用折
    elif _qu and _su and _qu == _su:
        po_qty = round(qty / conv, 6) if conv else qty
    else:
        po_qty = round(qty / conv, 6) if conv else qty   # 老数据/认不出：原行为
    po_qty = round(po_qty, 6)
    remain = round(float(it['qty'] or 0) - float(it['recv_qty'] or 0), 6)
    if remain <= 1e-9:
        return False, '批次 %s 已经交齐了，这次入库没有计入采购单' % b
    # 超交部分不记进采购单（采购单只认订购量），但要告诉使用者
    over = False
    if po_qty > remain + 1e-9:
        po_qty = remain
        over = True

    unit_price = float(price) if price not in (None, '') else float(it['price'] or 0)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        with db.tx() as c:
            c.execute("INSERT INTO po_receipts(item_id,txn_id,rdate,qty,price,note,"
                      "created_at,batch,src) VALUES(?,?,?,?,?,?,?,?,?)",
                      (it['id'], txn_id, date or datetime.now().strftime('%Y-%m-%d'),
                       po_qty, unit_price,
                       (note or '')[:80], now, b, 'txn'))
            c.execute("UPDATE po_items SET recv_qty=ROUND(recv_qty+?,6) WHERE id=?",
                      (po_qty, it['id']))
    except Exception as e:
        _log_err('sync_txn_to_po 批次=%s' % b, str(e))
        return False, '采购流水写入失败（库存已入库）：%s' % e
    refresh_status(it['po_id'])
    try:
        save_summary(it['po_id'])
    except Exception as _e:
        _log_err('采购汇总快照保存失败 po_id=%s（入库同步后）' % it['po_id'], str(_e))

    tip = '批次 %s 已计入采购单 %s：本次到货 %g%s' % (b, it['pono'], po_qty, it['unit'])
    if over:
        tip += '（入库数超出未交量，多出的部分没计入采购单）'
    return True, tip


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
    # 单位是指纹的一部分，改了就得重算，否则流水页会按旧指纹配到别的货上
    if unit is not None:
        _sup = ''
        try:
            _r = db.q("SELECT p.supplier FROM pos p JOIN po_items i ON i.po_id=p.id"
                      " WHERE i.id=?", item_id)
            _sup = (_r[0]['supplier'] or '') if _r else ''
        except Exception:
            _sup = ''
        sets.append("sig=?")
        vals.append(item_sig(it['name'], it['spec'], (unit or '').strip(), _sup))
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

    # 采购与库存独立：撤销到货只改采购侧，绝不动入库单/库存。
    # 老数据里残留的 txn_id（旧版到货会生成入库单）也不删，
    # 那批货既然已经进了仓库，就由仓库侧自己处理（去流水页删），
    # 否则采购一个操作就把库存改了，两边又缠在一起。
    keep = bool(r['txn_id'])
    try:
        with db.tx() as c:
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
    tip = '已取消到货 %g（采购侧已撤销，不影响库存）' % r['qty']
    if keep:
        tip += '；这是旧版生成的入库单，已保留在库存里，要删请去流水页手动删'
    return True, tip


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
    # 采购与库存独立：明细下的到货记录随明细一起删（CASCADE），
    # 但不再去撤什么入库单——现在到货本来就不生成入库单了。
    n = db.q("SELECT COUNT(*) c FROM po_receipts WHERE item_id=?", item_id)[0]['c']
    db.run("DELETE FROM po_items WHERE id=?", item_id)
    refresh_status(po_id)
    try:
        save_summary(po_id)
    except Exception as _e:
        _log_err('采购汇总快照保存失败 po_id=%s（删明细后）' % po_id,
                 'save_summary: %s' % _e)
    tip = '明细已删除' + ('（同时删除 %d 条到货记录，不影响库存）' % n if n else '')
    return True, tip
