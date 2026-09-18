# -*- coding: utf-8 -*-
"""运维：数据完整性检查、孤儿数据修复、备份、统计概览、别名表。"""
import os, sqlite3, json, sys
from datetime import datetime
from .paths import (app_dir, res_dir, BASE, DB_PATH, SEED, UNITS,
                   unit_choices, _is_frozen, _writable, _user_data_dir)
from .dbconn import (conn, tx, close, q, run, runmany, _flat, _cols,
                    _run_now, _runmany_now, _write_lock, _is_write, _busy_retry)
import threading

# ---------- 数据完整性 / 备份 ----------
def check_integrity(deep=False):
    """体检：返回问题列表（空列表=健康）

    deep=False（默认）用 quick_check：只校验文件结构，5万单据下约 0.2 秒。
    deep=True 用 integrity_check：逐页校验内容，同一库实测 12 秒，
    只在用户手动点「深度体检」时才跑——系统页每次都跑会卡十几秒。
    两者都能检出文件级损坏；差别在于 deep 还会逐行核对索引与数据是否一致。
    """
    issues = []
    pragma = 'PRAGMA integrity_check' if deep else 'PRAGMA quick_check'
    try:
        if q(pragma)[0][0] != 'ok':
            issues.append('数据库文件结构异常（%s 未通过）' % pragma.split()[1])
    except sqlite3.DatabaseError as e:
        issues.append('数据库文件损坏，读不了了：%s' % e)
        # 只说"坏了"没用，得告诉用户手上有哪些备份可以救
        try:
            d = os.path.dirname(os.path.abspath(DB_PATH)) or '.'
            baks = sorted((f for f in os.listdir(d)
                           if f.startswith(os.path.basename(DB_PATH) + '.')
                           and f.endswith('.bak')), reverse=True)
        except OSError:
            baks = []
        if baks:
            issues.append('别慌，有 %d 份自动备份可以还原，最新的是 %s' % (len(baks), baks[0]))
            issues.append('还原方法：退出程序 → 把 %s 改名成 %s → 重新打开'
                          % (baks[0], os.path.basename(DB_PATH)))
        else:
            issues.append('没有找到自动备份，数据库只能重建，此前的单据将无法恢复')
        return issues
    for r in q("SELECT t.id, t.material_id FROM txns t"
               " LEFT JOIN materials m ON m.id=t.material_id WHERE m.id IS NULL"):
        issues.append('单据 #%s 指向不存在的物料（%s）' % (r['id'], r['material_id']))
    for r in q("SELECT id, name FROM materials WHERE name IS NULL OR trim(name)=''"):
        issues.append('物料 #%s 没有名称' % r['id'])
    for r in q("SELECT id, tdate FROM txns WHERE tdate IS NULL OR tdate NOT LIKE '____-__-__'"):
        issues.append('单据 #%s 日期格式异常（%s）' % (r['id'], r['tdate']))
    for r in q("SELECT id, qty FROM txns WHERE qty IS NULL OR qty <= 0"):
        issues.append('单据 #%s 数量不合法（%s）' % (r['id'], r['qty']))
    for r in q("SELECT id, kind FROM txns WHERE kind NOT IN ('进','出')"):
        issues.append('单据 #%s 进出类型异常（%s）' % (r['id'], r['kind']))
    for r in q("SELECT id, name, stock FROM v_stock WHERE stock < 0"):
        issues.append('【%s】库存为负（%g）——出库可能超过了入库' % (r['name'], r['stock']))
    # 只按名称判重会误报：原表里「0.05金」本来就有 0.04 / 0.045 两种宽幅，
    # 它们是不同物料。真正有问题的是「名称+规格+宽幅」完全一样却建了两条。
    dup = q("SELECT name, COALESCE(spec,'') sp, COALESCE(width,'') wd, COUNT(*) c"
            " FROM materials WHERE active=1"
            " GROUP BY name, COALESCE(spec,''), COALESCE(width,'') HAVING c > 1")
    for r in dup:
        tag = r['name'] + ((' 规格' + r['sp']) if r['sp'] else '') \
                        + ((' 宽' + r['wd']) if r['wd'] else '')
        issues.append('物料重复：%s（%d 条，规格完全一样，建议合并）' % (tag, r['c']))
    dup2 = q("SELECT code, COUNT(*) c FROM materials WHERE active=1 AND code<>''"
             " GROUP BY code HAVING c > 1")
    for r in dup2:
        issues.append('料号重复：%s（%d 条）' % (r['code'], r['c']))
    return issues

def fix_orphans():
    """清掉指向已不存在物料的孤儿单据。返回清理条数"""
    n = q("SELECT COUNT(*) c FROM txns t LEFT JOIN materials m ON m.id=t.material_id"
          " WHERE m.id IS NULL")[0]['c']
    if n:
        run("DELETE FROM txns WHERE material_id NOT IN (SELECT id FROM materials)")
    return n

def backup(to_path=None, keep=10):
    """在线备份（用 SQLite 官方 backup API，拷出来的库一定是一致的）。
    同一秒重复备份会覆盖；只保留最近 keep 个，避免备份把磁盘塞满。"""
    if not to_path:
        base = DB_PATH + '.' + datetime.now().strftime('%Y%m%d_%H%M%S')
        to_path = base + '.bak'
        # 同一秒内重复备份会互相覆盖（比如程序反复重启），加序号保证每份都在，
        # 否则最后一份好备份可能被同名覆盖掉，真出事就没得还原了
        n = 1
        while os.path.exists(to_path):
            n += 1
            to_path = '%s_%d.bak' % (base, n)
    to_path = to_path
    src = conn()
    dst = sqlite3.connect(to_path)
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
    try:
        d = os.path.dirname(os.path.abspath(DB_PATH)) or '.'
        baks = sorted((f for f in os.listdir(d)
                       if f.startswith(os.path.basename(DB_PATH) + '.') and f.endswith('.bak')),
                      reverse=True)
        for old in baks[keep:]:
            try:
                os.remove(os.path.join(d, old))
            except OSError:
                pass
    except OSError:
        pass
    return to_path

def stats():
    """库概况"""
    g = lambda s, *a: q(s, *a)[0][0]
    return dict(
        materials=g("SELECT COUNT(*) FROM materials WHERE active=1"),
        all_materials=g("SELECT COUNT(*) FROM materials"),
        txns=g("SELECT COUNT(*) FROM txns"),
        first=g("SELECT MIN(tdate) FROM txns") or '—',
        last=g("SELECT MAX(tdate) FROM txns") or '—',
        alerts=g("SELECT COUNT(*) FROM v_stock WHERE stock<=safety"),
        size=os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0,
        path=DB_PATH,
    )

def aliases_map(tpl_id=None):
    """{系统字段: [别名...]}：自定义别名 + 自定义表头名，供 Excel 导入识别表头。

    模板制之后列配置存在 tpl_cols，colmap 是老表（v3.13 起不再更新）。
    这里必须优先读模板，否则使用者在模板里自己起的表头名，导入时认不出来。"""
    rows = []
    if tpl_id:
        rows = q("SELECT fid, label, aliases FROM tpl_cols WHERE tpl_id=?", tpl_id)
    if not rows:
        rows = [r for r in q("SELECT fid, label, aliases FROM tpl_cols")]
    if not rows:
        rows = q("SELECT fid, label, aliases FROM colmap")
    out = {}
    for r in rows:
        al = [x.strip() for x in (r['aliases'] or '').split(',') if x.strip()]
        if r['label'] and r['label'] not in al:
            al.append(r['label'])
        # 同名不同列都收进来，别互相覆盖
        old = out.get(r['fid']) or []
        for x in al:
            if x not in old:
                old.append(x)
        out[r['fid']] = old or [r['fid']]
    return out
