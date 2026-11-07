# -*- coding: utf-8 -*-
"""广义用户清单匹配补齐（幂等，可重复执行）

来源与口径
----------
1) 责任人：Web8011 库内 territory_responsible（责任田对应责任人，1542 行）。
   优先按「归属小区」精确匹配 territory/community，退化为按「最终回单网格」
   取该网格下 person_name 众数；匹配上写入 owner，原备选值留存在 owner_original。
2) CID：服务器现存 /home/ipromise/.local/share/Trash/files/CID智能网关(7-14).csv
   （GB18030，2026-07-12 单日快照，1004546 行），按「用户上网账号」关联清单
   的 internet_account，回填 CID上报SN / 归属小区 / 空缺的用户联系电话。
   ★ 该快照不是用户指定的「CID智能网关20260801.csv」，逐行写入 cid_source 标注，
     指定源到位后按同一关联键 UPDATE 覆盖即可。

保守规则：不覆盖已有的源表值（用户手机号、源表小区、源表当前处理人仅在
责任田匹配上时替换并留存原值）；未匹配字段留空，不臆造；工单编号主键不变。
"""
import csv
import sqlite3
from collections import defaultdict, Counter

DB = '/vol2/Trae/backend/honghu_new.db'
CID_CSV = '/home/ipromise/.local/share/Trash/files/CID智能网关(7-14).csv'
CID_SOURCE = 'CID智能网关(7-14).csv(2026-07-12快照,非指定20260801)'

con = sqlite3.connect(DB, timeout=90)
con.execute('PRAGMA busy_timeout=90000')
cols = {r[1] for r in con.execute('PRAGMA table_info(broad_user_list)')}
for name in ('cid_source', 'owner_original'):
    if name not in cols:
        con.execute("ALTER TABLE broad_user_list ADD COLUMN %s TEXT DEFAULT ''" % name)
        print('已补列', name)
con.commit()

# ---------- 责任田映射 ----------
by_terr, by_comm = {}, {}
grid_votes = defaultdict(Counter)
for territory, community, grid, person, installer in con.execute(
        "SELECT territory, community, grid, person_name, installer_name "
        "FROM territory_responsible WHERE active = 1"):
    name = (person or installer or '').strip()
    if not name:
        continue
    if territory:
        by_terr.setdefault(territory.strip(), name)
    if community:
        by_comm.setdefault(community.strip(), name)
    if grid:
        grid_votes[grid.strip()][name] += 1
grid_pick = {g: c.most_common(1)[0][0] for g, c in grid_votes.items()}


def match_owner(community, grid):
    if community:
        v = by_terr.get(community.strip()) or by_comm.get(community.strip())
        if v:
            return v, '小区精确'
    if grid:
        v = grid_pick.get(grid.strip())
        if v:
            return v, '网格众数'
    return None, ''


# ---------- CID 映射（流式，仅保留清单账号命中项） ----------
accounts = {r[0].strip() for r in con.execute(
    'SELECT internet_account FROM broad_user_list') if r[0] and r[0].strip()}
hits = {}
with open(CID_CSV, encoding='gb18030', errors='replace', newline='') as f:
    rd = csv.reader(f)
    next(rd)
    for row in rd:
        if len(row) < 31:
            continue
        a = row[3].strip()
        if a in accounts and a not in hits:
            hits[a] = (row[13].strip(), row[30].strip())
print('CID 命中账号:', len(hits), '/ 清单非空账号:', len(accounts))

# ---------- 逐行补齐 ----------
rows = con.execute(
    'SELECT id, internet_account, phone, cid_sn, community, owner, grid, owner_source '
    'FROM broad_user_list').fetchall()
updates = []
stat = {'cid': 0, 'sn': 0, 'comm': 0, 'phone': 0, 'owner': 0,
        'matched': 0, 'partial': 0, 'pending': 0}

for rid, acct, phone, cid_sn, community, owner, grid, owner_source in rows:
    u = {}
    acct = (acct or '').strip()
    hit = hits.get(acct)
    if hit:
        sn, cid_comm = hit
        stat['cid'] += 1
        u['cid_source'] = CID_SOURCE
        if not (cid_sn or '').strip() and sn:
            u['cid_sn'] = sn
            stat['sn'] += 1
        if not (community or '').strip() and cid_comm:
            u['community'] = cid_comm
            stat['comm'] += 1
        # 按需求口径「用户联系电话即 CID 用户上网账号」；不覆盖已有源表手机号
        if not (phone or '').strip():
            u['phone'] = acct
            u['phone_source'] = 'CID智能网关(7-14).csv'
            stat['phone'] += 1

    eff_comm = u.get('community') or community or ''
    o, how = match_owner(eff_comm, grid or '')
    # 幂等：已由责任田表匹配过的行不再覆盖，避免把原备选值冲掉
    if o and 'Web8011责任田表' not in (owner_source or ''):
        u['owner'] = o
        u['owner_source'] = 'Web8011责任田表(%s)' % how
        u['owner_original'] = owner or ''
        stat['owner'] += 1

    f_phone = u.get('phone', phone) or ''
    f_sn = u.get('cid_sn', cid_sn) or ''
    f_comm = u.get('community', community) or ''
    f_owner = u.get('owner', owner) or ''
    missing = []
    if not f_phone.strip():
        missing.append('用户联系电话缺')
    if not f_sn.strip():
        missing.append('CID上报SN缺(待指定20260801 CID源)')
    if not f_comm.strip():
        missing.append('归属小区缺')
    if not f_owner.strip():
        missing.append('责任人缺(责任田表无对应网格/小区)')
    if not hit:
        missing.append('CID账号未命中7-14快照')

    if not missing:
        status = '已匹配·待20260801复核'
        stat['matched'] += 1
    elif hit or o:
        status = '部分匹配'
        stat['partial'] += 1
    else:
        status = '待正式匹配'
        stat['pending'] += 1
    u['match_status'] = status
    u['unmatch_reason'] = '；'.join(missing)
    updates.append((u, rid))

for u, rid in updates:
    con.execute('UPDATE broad_user_list SET %s WHERE id = ?' %
                ', '.join('%s = ?' % k for k in u), list(u.values()) + [rid])
con.commit()

print('补齐统计:', stat)
print('状态分布:', list(con.execute(
    'SELECT match_status, COUNT(*) FROM broad_user_list GROUP BY match_status')))
print('字段完整度:', {
    'phone': con.execute("SELECT COUNT(*) FROM broad_user_list WHERE phone <> ''").fetchone()[0],
    'cid_sn': con.execute("SELECT COUNT(*) FROM broad_user_list WHERE cid_sn <> ''").fetchone()[0],
    'community': con.execute("SELECT COUNT(*) FROM broad_user_list WHERE community <> ''").fetchone()[0],
    'owner': con.execute("SELECT COUNT(*) FROM broad_user_list WHERE owner <> ''").fetchone()[0],
})
con.close()
