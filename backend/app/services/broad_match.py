# -*- coding: utf-8 -*-
"""广义用户清单公共匹配/补齐逻辑（CID 快照 + 责任田），供 sync 的
「CID数据（智能网关）」与「广义用户清单」两个渠道共用，避免两套口径。

用户确认的规则（2026-11-07）：
1) CID：账号精确优先；清单电话（11 位纯数字）兜底；只填空值；
   旧 CID 来源的 cid_sn 可刷新；community 只填空；电话与源表处理人永不覆盖。
2) 责任田：territory_responsible 的小区名在 territory 列；归一去空格/尾部 H 后
   精确相等，且要求同区县；不做子串/模糊匹配。
3) 状态：已匹配 / 部分匹配 / 待正式匹配，未匹配原因按实际缺口重写。
4) CID 快照整表替换（cid_records），上传期先写 cid_staging，确认时才在事务内替换。
"""

import os
import re
import sqlite3
import unicodedata
from datetime import datetime

from sqlalchemy import text

DISTRICT_ALIAS = {"监利县": "监利市"}
CID_TABLE = "cid_records"
CID_STAGING = "cid_staging"
CID_MAX_BYTES = 400 * 1024 * 1024
CID_BACKUP_DIR = "/vol2/Trae/backup/cid"

_CREATE_RECORDS = (
    "CREATE TABLE IF NOT EXISTS %s ("
    "account_norm TEXT NOT NULL PRIMARY KEY, account TEXT NOT NULL DEFAULT '', "
    "cid_sn TEXT NOT NULL DEFAULT '', community TEXT NOT NULL DEFAULT '', "
    "district TEXT NOT NULL DEFAULT '', version TEXT NOT NULL DEFAULT '', "
    "source_file TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT '')" % CID_TABLE)
_CREATE_STAGING = (
    "CREATE TABLE IF NOT EXISTS %s ("
    "batch_id TEXT NOT NULL, account_norm TEXT NOT NULL, account TEXT NOT NULL DEFAULT '', "
    "cid_sn TEXT NOT NULL DEFAULT '', community TEXT NOT NULL DEFAULT '', "
    "district TEXT NOT NULL DEFAULT '', row_no INTEGER NOT NULL DEFAULT 0)" % CID_STAGING)
_CREATE_STAGE_INDEX = ("CREATE INDEX IF NOT EXISTS idx_cid_stage_batch "
                       "ON %s(batch_id, account_norm)" % CID_STAGING)


def norm_text(v) -> str:
    s = unicodedata.normalize("NFKC", str(v or "")).strip()
    return s.replace(" ", "").replace("\u3000", "")


def norm_account(v) -> str:
    return norm_text(v).upper()


def account_norm_key(v) -> str:
    """纯数字账号去前导零，用于兼容 Excel 数值化丢零。"""
    s = norm_account(v)
    if s.isdigit():
        return s.lstrip("0") or "0"
    return s


def account_keys(v) -> list:
    s = norm_account(v)
    if not s:
        return []
    keys = [s]
    n = account_norm_key(s)
    if n != s:
        keys.append(n)
    return keys


def norm_community(v) -> str:
    s = norm_text(v)
    if len(s) > 2 and s[-1] in ("H", "h"):
        s = s[:-1]
    return s.lower() if re.search(r"[a-zA-Z]", s) else s


def norm_district(v) -> str:
    return DISTRICT_ALIAS.get(norm_text(v), norm_text(v))


def is_mobile(v) -> bool:
    return bool(re.fullmatch(r"1\d{10}", norm_text(v)))


def ensure_cid_tables(db, commit: bool = True) -> None:
    db.execute(text(_CREATE_RECORDS))
    db.execute(text(_CREATE_STAGING))
    db.execute(text(_CREATE_STAGE_INDEX))
    if commit:
        db.commit()


def backup_db(db) -> str:
    """上传前用 sqlite3 在线备份 API 落一份一致性快照（不是直接 cp 主库）。"""
    path = (db.get_bind().url.database or "").strip()
    if not path or not os.path.exists(path):
        return ""
    os.makedirs(CID_BACKUP_DIR, exist_ok=True)
    dest = os.path.join(CID_BACKUP_DIR, "honghu_new.db.bak-%s"
                        % datetime.now().strftime("%Y%m%d-%H%M%S"))
    src = sqlite3.connect(path)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return dest


def purge_staging(db, keep_batch: str = None) -> int:
    """清理历史暂存，避免预览批次遗留在库里。"""
    ensure_cid_tables(db)
    sql = "DELETE FROM %s" % CID_STAGING
    params = {}
    if keep_batch:
        sql += " WHERE batch_id <> :keep"
        params["keep"] = keep_batch
    n = db.execute(text(sql), params).rowcount
    db.commit()
    return n or 0


def load_territory(db) -> dict:
    """归属小区 → (网格, 责任人, 区县)；territory 列才是小区名。"""
    rows = db.execute(text(
        "SELECT territory, grid, person_name, installer_name, district "
        "FROM territory_responsible WHERE active = 1")).fetchall()
    out = {}
    for terr, grid, person, installer, district in rows:
        nm = norm_community(terr)
        if not nm or nm in out:
            continue
        name = (person or installer or "").strip()
        if not name:
            continue
        out[nm] = (norm_text(grid), name, norm_district(district))
    return out


def match_owner(terr_map: dict, community: str, district: str):
    """归一后精确 + 同区县；无命中返回 None。"""
    nm = norm_community(community)
    if not nm:
        return None
    t = terr_map.get(nm)
    if not t:
        return None
    grid, person, tdist = t
    nd = norm_district(district)
    if tdist and nd and tdist != nd:
        return None
    return person, grid


def lookup_cid(db, keys: set, batch_id: str = None) -> dict:
    """按 account_norm 批量查快照；batch_id 给定时查 staging（预览用）。"""
    hits = {}
    keys = [k for k in keys if k]
    if batch_id:
        table, cond = CID_STAGING, " AND batch_id = :bid"
    else:
        table, cond = CID_TABLE, ""
    for i in range(0, len(keys), 400):
        chunk = keys[i:i + 400]
        sql = ("SELECT account_norm, cid_sn, community, district FROM %s "
               "WHERE account_norm IN (%s)%s"
               % (table, ",".join([":k%d" % j for j in range(len(chunk))]), cond))
        params = {"k%d" % j: k for j, k in enumerate(chunk)}
        if batch_id:
            params["bid"] = batch_id
        for kn, sn, comm, dist in db.execute(text(sql), params).fetchall():
            hits.setdefault(kn, (norm_text(sn), norm_text(comm), norm_district(dist)))
    return hits


def _row_keys(account: str, phone: str) -> list:
    keys = []
    for k in account_keys(account):
        keys.append(account_norm_key(k))
    if is_mobile(phone):
        for k in account_keys(phone):
            keys.append(account_norm_key(k))
    return keys


def _load_broad_rows(db, ids: list = None) -> list:
    if ids:
        out = []
        for i in range(0, len(ids), 400):
            chunk = ids[i:i + 400]
            out += db.execute(text(
                "SELECT id, internet_account, phone, cid_sn, community, owner, grid, district, "
                "cid_source, phone_source, owner_source, owner_original, match_status "
                "FROM broad_user_list WHERE id IN (%s)" % ",".join([":i%d" % j for j in range(len(chunk))])),
                {"i%d" % j: v for j, v in enumerate(chunk)}).fetchall()
        return out
    return db.execute(text(
        "SELECT id, internet_account, phone, cid_sn, community, owner, grid, district, "
        "cid_source, phone_source, owner_source, owner_original, match_status "
        "FROM broad_user_list")).fetchall()


_EMPTY_STAT = {"rows": 0, "hit": 0, "hit_account": 0, "hit_phone": 0,
               "cid_sn_filled": 0, "cid_sn_refreshed": 0, "community_filled": 0,
               "phone_filled": 0, "owner_filled": 0, "owner_refreshed": 0,
               "grid_filled": 0, "grid_refreshed": 0,
               "conflict_sn": 0, "conflict_community": 0,
               "status_matched": 0, "status_partial": 0, "status_pending": 0}


def _apply_rules(row_list, hits: dict, terr: dict, label: str, want_updates: bool):
    """逐行套用补齐规则，返回 (统计 dict, 待写库 updates 列表)。"""
    st = dict(_EMPTY_STAT, rows=len(row_list))
    updates = []

    for (rid, account, phone, cid_sn, community, owner, grid, district,
         cid_source, phone_source, owner_source, owner_orig, _status) in row_list:
        account = norm_account(account)
        phone = norm_text(phone)
        cid_sn = norm_text(cid_sn)
        community = norm_text(community)
        owner = norm_text(owner)
        grid = norm_text(grid)
        district = norm_text(district)
        cid_source = cid_source or ""
        phone_source = phone_source or ""
        owner_source = owner_source or ""

        hit = None
        via = ""
        for k in account_keys(account):
            kn = account_norm_key(k)
            if kn in hits:
                hit, via = hits[kn], "account"
                break
        if not hit and is_mobile(phone):
            kn = account_norm_key(phone)
            if kn in hits:
                hit, via = hits[kn], "phone"

        new_phone, new_sn, new_comm = phone, cid_sn, community
        new_phone_source, new_cid_source = phone_source, cid_source
        if hit:
            st["hit"] += 1
            st["hit_" + via] += 1
            sn, comm, dist = hit
            if not new_sn.strip() and sn:
                new_sn, new_cid_source = sn, label + ("" if via == "account" else "(电话兜底)")
                st["cid_sn_filled"] += 1
            elif new_sn.strip() and sn and new_sn != sn and "CID" in cid_source:
                # 旧 CID 来源允许刷新；源表/人工填写不覆盖
                new_sn, new_cid_source = sn, label + ("" if via == "account" else "(电话兜底)")
                st["cid_sn_refreshed"] += 1
            elif new_sn.strip() and sn and new_sn != sn:
                st["conflict_sn"] += 1

            if not new_comm.strip() and comm:
                new_comm = comm
                if not new_cid_source:
                    new_cid_source = label + ("" if via == "account" else "(电话兜底)")
                st["community_filled"] += 1
            elif new_comm.strip() and comm and new_comm != comm:
                st["conflict_community"] += 1

            # 电话兜底只在清单电话为空、且账号本身就是 11 位手机号时补
            if not new_phone.strip() and is_mobile(account):
                new_phone = account
                new_phone_source = label + "(账号即手机号)"
                st["phone_filled"] += 1

        # 责任田：用最终小区 + 清单区县（空则用 CID 区县）
        use_dist = district or (hit[2] if hit else "")
        m = match_owner(terr, new_comm, use_dist)
        new_owner, new_grid = owner, grid
        new_owner_source, new_owner_orig = owner_source, owner_orig or ""
        if m:
            o, g = m
            if not new_owner.strip():
                new_owner, new_grid = o, g
                new_owner_source = "Web8011责任田表(小区精确)"
                new_owner_orig = owner
                st["owner_filled"] += 1
                st["grid_filled"] += 1
            elif "责任田" in owner_source:
                if new_owner != o:
                    new_owner = o
                    new_owner_source = "Web8011责任田表(小区精确)"
                    new_owner_orig = owner
                    st["owner_refreshed"] += 1
                if g and (not new_grid.strip() or new_grid != g):
                    new_grid = g
                    st["grid_refreshed"] += 1
            # 源表-当前处理人：不覆盖

        # 状态与缺口：四要素齐全即「已匹配」，CID 未命中不降级
        missing = []
        if not new_phone.strip():
            missing.append("用户联系电话缺")
        if not new_sn.strip():
            missing.append("CID上报SN缺")
        if not new_comm.strip():
            missing.append("归属小区缺")
        if not new_owner.strip():
            missing.append("责任人缺")
        if not missing:
            status = "已匹配"
            st["status_matched"] += 1
        elif hit or m:
            status = "部分匹配"
            st["status_partial"] += 1
            if not hit:
                missing.append("CID账号/电话未命中")
        else:
            status = "待正式匹配"
            st["status_pending"] += 1
            if not hit:
                missing.append("CID账号/电话未命中")
        reason = "；".join(missing)

        if want_updates:
            updates.append({"id": rid, "phone": new_phone, "cid_sn": new_sn,
                            "community": new_comm, "owner": new_owner, "grid": new_grid,
                            "cid_source": new_cid_source, "phone_source": new_phone_source,
                            "owner_source": new_owner_source,
                            "owner_original": new_owner_orig,
                            "match_status": status, "unmatch_reason": reason})

    return st, updates


def enrich_broad_rows(db, ids: list = None, cid_label: str = "", apply: bool = True,
                      batch_id: str = None, commit: bool = True, ensure: bool = True) -> dict:
    """对 broad_user_list 做 CID + 责任田补齐（幂等）。

    - ids 为空表示全量；
    - batch_id 给定时从 cid_staging 取 CID（上传预览，不写库），否则读 cid_records；
    - apply=False 只统计不写库；
    - ensure=False 用于调用方已建表、且要求全程不开新事务的场景。
    """
    if ensure:
        ensure_cid_tables(db)
    row_list = _load_broad_rows(db, ids)
    terr = load_territory(db)
    keys = set()
    for r in row_list:
        keys.update(_row_keys(r[1] or "", r[2] or ""))
    hits = lookup_cid(db, keys, batch_id=batch_id)

    label = norm_text(cid_label) or "CID智能网关"
    st, updates = _apply_rules(row_list, hits, terr, label, want_updates=apply)

    if apply and updates:
        db.execute(text(
            "UPDATE broad_user_list SET phone=:phone, cid_sn=:cid_sn, community=:community, "
            "owner=:owner, grid=:grid, cid_source=:cid_source, phone_source=:phone_source, "
            "owner_source=:owner_source, owner_original=:owner_original, match_status=:match_status, "
            "unmatch_reason=:unmatch_reason WHERE id=:id"), updates)
        if commit:
            db.commit()
    return st


INSERT_STAGING = text(
    "INSERT INTO %s (batch_id, account_norm, account, cid_sn, community, district, row_no) "
    "VALUES (:batch_id, :account_norm, :account, :cid_sn, :community, :district, :row_no)" % CID_STAGING)


def replace_cid_snapshot(db, batch_id: str, version: str = "", source_file: str = "") -> int:
    """在调用方的事务内整表替换 cid_records，并清掉该批次 staging。返回快照行数。"""
    ensure_cid_tables(db, commit=False)
    now_s = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db.execute(text("DELETE FROM %s" % CID_TABLE))
    db.execute(text(
        "INSERT OR IGNORE INTO %s (account_norm, account, cid_sn, community, district, "
        "version, source_file, updated_at) "
        "SELECT account_norm, MAX(account), MAX(cid_sn), MAX(community), MAX(district), "
        ":v, :f, :t FROM %s WHERE batch_id = :b GROUP BY account_norm"
        % (CID_TABLE, CID_STAGING)),
        {"v": version, "f": source_file, "t": now_s, "b": batch_id})
    db.execute(text("DELETE FROM %s WHERE batch_id = :b" % CID_STAGING), {"b": batch_id})
    return db.execute(text("SELECT COUNT(*) FROM %s" % CID_TABLE)).scalar() or 0
