"""指标计算 —— 竣工率 / 延伸服务 / 综合得分 / 短板分析（口径严格对齐开发手册 七、八）"""
from collections import defaultdict
from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from ..config import COMPLETED_STATUS, PENDING_STATUS, TARGET_PER_PERSON
from ..database import get_db
from ..models import (WorkOrder, District, ExtensionOrder, ExtensionMember, H5Complaint, H5Receipt,
                      MetricDefinition, User, Setting, AuditLog, CompletionRecord, SyncJob,
                      InstallOrder, ComplaintOrder)
from ..deps import get_current_user, scope_district_ids, require_admin, get_role

router = APIRouter(prefix="/metrics", tags=["metrics"])

# 服务端筛选：与前端 Dashboard 明细页过滤口径完全一致（布尔参数，True=隐藏该类）
_DEFAULT_PAGE_SIZE = 500
_MAX_PAGE_SIZE = 2000


def _month_filter(col, month: str):
    """无 month 列的旧表回退：字符串前缀匹配（走不了索引）"""
    return col.like(f"{month}%")


def _base_query(db: Session, user, month: str):
    """竣工率基础过滤：宽带类型=个人 + 非HB- + 受理时间在月内 + 区县非空（手册 7.5）"""
    ids = scope_district_ids(user, db)
    q = (db.query(WorkOrder)
         .filter(WorkOrder.broadband_type == "个人",
                 ~WorkOrder.order_id.like("HB-%"),
                 WorkOrder.month == month,
                 WorkOrder.district_id.isnot(None)))
    if ids is not None:
        q = q.filter(WorkOrder.district_id.in_(ids))
    return q


# ---------------- 竣工率（手册 7.5 / 8.1） ----------------

@router.get("/completion-rate/districts")
def completion_districts(month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
                         user=Depends(get_current_user), db: Session = Depends(get_db)):
    q = _base_query(db, user, month)
    rows = q.with_entities(WorkOrder.district_id, WorkOrder.status,
                           func.count(WorkOrder.id)).group_by(WorkOrder.district_id, WorkOrder.status).all()
    agg = defaultdict(lambda: {"total": 0, "done": 0})
    for district_id, status, cnt in rows:
        agg[district_id]["total"] += cnt
        if status in COMPLETED_STATUS:
            agg[district_id]["done"] += cnt
    districts = {d.id: d.name for d in db.query(District).all()}
    out = []
    for did, v in agg.items():
        rate = v["done"] / v["total"] * 100 if v["total"] else 0
        out.append({"district_id": did, "district": districts.get(did, "未知"),
                    "total": v["total"], "completed": v["done"], "rate": round(rate, 2)})
    out.sort(key=lambda x: -x["rate"])
    for i, r in enumerate(out, 1):
        r["rank"] = i
    return out


@router.get("/completion-rate/users")
def completion_users(month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
                     district: str = Query("", description="区县名过滤，空为全部"),
                     user=Depends(get_current_user), db: Session = Depends(get_db)):
    q = _base_query(db, user, month)
    if district:
        dids = [d.id for d in db.query(District).filter(District.name == district).all()]
        q = q.filter(WorkOrder.district_id.in_(dids))
    rows = q.with_entities(WorkOrder.construction_dept, WorkOrder.status,
                           func.count(WorkOrder.id)).group_by(WorkOrder.construction_dept, WorkOrder.status).all()
    agg = defaultdict(lambda: {"total": 0, "done": 0})
    for handler, status, cnt in rows:
        if not handler:
            continue
        agg[handler]["total"] += cnt
        if status in COMPLETED_STATUS:
            agg[handler]["done"] += cnt
    # 离职过滤：仅保留在册人员（手册 8.1 fetch_inactive_handler_values）
    active_names = {u.real_name for u in db.query(User).filter(User.is_active == True).all()}  # noqa: E712
    out = []
    for name, v in agg.items():
        if name not in active_names:
            continue
        rate = v["done"] / v["total"] * 100 if v["total"] else 0
        out.append({"handler": name, "total": v["total"], "completed": v["done"], "rate": round(rate, 2)})
    out.sort(key=lambda x: -x["rate"])
    for i, r in enumerate(out, 1):
        r["rank"] = i
    return out[:100]


@router.get("/completion-rate/daily")
def completion_daily(month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
                     user=Depends(get_current_user), db: Session = Depends(get_db)):
    q = _base_query(db, user, month)
    rows = q.with_entities(func.substr(WorkOrder.accept_time, 1, 10).label("day"), WorkOrder.status,
                           func.count(WorkOrder.id)).group_by("day", WorkOrder.status).all()
    agg = defaultdict(lambda: {"total": 0, "done": 0, "pending": 0})
    for day, status, cnt in rows:
        agg[day]["total"] += cnt
        if status in COMPLETED_STATUS:
            agg[day]["done"] += cnt
        elif status in PENDING_STATUS:
            agg[day]["pending"] += cnt
    out = [{"day": d, **v, "rate": round(v["done"] / v["total"] * 100, 2) if v["total"] else 0}
           for d, v in sorted(agg.items())]
    return out


@router.get("/completion-rate/outlets")
def completion_outlets(month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
                       user=Depends(get_current_user), db: Session = Depends(get_db)):
    from ..models import CancelRescheduleOrder
    q = db.query(WorkOrder).filter(WorkOrder.month == month,
                                   WorkOrder.district_id.isnot(None))
    ids = scope_district_ids(user, db)
    if ids is not None:
        q = q.filter(WorkOrder.district_id.in_(ids))
    rows = q.with_entities(WorkOrder.order_theme, func.count(WorkOrder.id)).group_by(WorkOrder.order_theme).all()
    cr_q = db.query(WorkOrder.order_theme, func.count()).join(
        CancelRescheduleOrder, CancelRescheduleOrder.order_id == WorkOrder.order_id).filter(
        WorkOrder.month == month).group_by(WorkOrder.order_theme)
    cancel_map = dict(cr_q.all())
    return [{"outlet": t, "accepted": c, "cancelled": cancel_map.get(t, 0)} for t, c in rows]


@router.get("/completion-rate/cancel-reschedule")
def cancel_reschedule(month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
                      user=Depends(get_current_user), db: Session = Depends(get_db)):
    from ..models import CancelRescheduleOrder, District
    q = db.query(CancelRescheduleOrder).filter(CancelRescheduleOrder.month == month)
    ids = scope_district_ids(user, db)
    if ids is not None:
        q = q.filter(CancelRescheduleOrder.district_id.in_(ids))
    rows = q.with_entities(CancelRescheduleOrder.type, func.count(CancelRescheduleOrder.id)).group_by(
        CancelRescheduleOrder.type).all()
    reasons = q.with_entities(CancelRescheduleOrder.reason, func.count(CancelRescheduleOrder.id)).group_by(
        CancelRescheduleOrder.reason).order_by(func.count(CancelRescheduleOrder.id).desc()).all()
    districts = {d.id: d.name for d in db.query(District).all()}
    by_district = q.with_entities(CancelRescheduleOrder.district_id, func.count(CancelRescheduleOrder.id)).group_by(
        CancelRescheduleOrder.district_id).all()
    return {"by_type": [{"type": t or "未知", "count": c} for t, c in rows],
            "by_district": [{"district": districts.get(d, "未知"), "count": c} for d, c in by_district],
            "top_reasons": [{"reason": r, "count": c} for r, c in reasons[:10]]}


@router.get("/completion-rate/report")
def completion_report(month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
                      user=Depends(get_current_user), db: Session = Depends(get_db)):
    """竣工率激励通报分析（数据来自上传的通报表「激励明细」，人员粒度按月替换）"""
    # 数据截止时间：工单口径取当月最大受理时间；通报取最近一次入库时间
    latest_order_time = db.query(func.max(WorkOrder.accept_time)).filter(
        WorkOrder.month == month).scalar() or ""
    up_job = db.query(SyncJob).filter(SyncJob.metric == "completion_rate",
                                      SyncJob.confirmed_at.isnot(None)).order_by(
        SyncJob.confirmed_at.desc()).first()
    latest_upload = up_job.confirmed_at.strftime("%Y-%m-%d %H:%M") if up_job else ""
    q = db.query(CompletionRecord).filter(CompletionRecord.month == month)
    if get_role(user) != "super_admin" and user.district:
        q = q.filter(CompletionRecord.district == user.district.name)
    recs = q.all()
    if not recs:
        return {"has_data": False, "month": month,
                "latest_order_time": latest_order_time, "latest_upload": latest_upload}
    bb_o = sum(r.bb_orders for r in recs)
    bb_d = sum(r.bb_done for r in recs)
    ft_o = sum(r.fttr_orders for r in recs)
    ft_d = sum(r.fttr_done for r in recs)
    inc = round(sum(r.incentive for r in recs), 2)
    agg = defaultdict(lambda: {"bb_o": 0, "bb_d": 0, "ft_o": 0, "ft_d": 0, "inc": 0.0, "people": 0})
    for r in recs:
        a = agg[r.district or "未知"]
        a["bb_o"] += r.bb_orders; a["bb_d"] += r.bb_done
        a["ft_o"] += r.fttr_orders; a["ft_d"] += r.fttr_done
        a["inc"] += r.incentive; a["people"] += 1
    districts = [{"district": d, "bb_orders": a["bb_o"], "bb_done": a["bb_d"],
                  "rate": round(a["bb_d"] / a["bb_o"] * 100, 1) if a["bb_o"] else 0,
                  "fttr_orders": a["ft_o"], "fttr_done": a["ft_d"],
                  "fttr_rate": round(a["ft_d"] / a["ft_o"] * 100, 1) if a["ft_o"] else 0,
                  "incentive": round(a["inc"], 2), "people": a["people"]}
                 for d, a in agg.items()]
    districts.sort(key=lambda x: (-x["rate"], -x["bb_orders"]))
    for i, d in enumerate(districts, 1):
        d["rank"] = i
    personal = [{"handler": r.handler, "district": r.district,
                 "bb_orders": r.bb_orders, "bb_done": r.bb_done,
                 "bb_rate": round(r.bb_done / r.bb_orders * 100, 1) if r.bb_orders else 0,
                 "fttr_orders": r.fttr_orders, "fttr_done": r.fttr_done,
                 "fttr_rate": round(r.ft_d / r.ft_o * 100, 1) if r.ft_o else 0,
                 "incentive": r.incentive} for r in recs]
    personal.sort(key=lambda x: (-x["incentive"], -x["bb_rate"]))
    return {"has_data": True, "month": month,
            "latest_order_time": latest_order_time, "latest_upload": latest_upload,
            "summary": {"people": len(recs),
                        "bb_orders": bb_o, "bb_done": bb_d,
                        "bb_rate": round(bb_d / bb_o * 100, 1) if bb_o else 0,
                        "fttr_orders": ft_o, "fttr_done": ft_d,
                        "fttr_rate": round(ft_d / ft_o * 100, 1) if ft_o else 0,
                        "incentive": inc,
                        "zero_incentive": sum(1 for r in recs if r.incentive <= 0)},
            "districts": districts, "personal": personal}


# ---------------- 宽表数据聚合（装机宽表 + 投诉宽表） ----------------

def _district_key(col):
    """区县分组键：NULL/空串归一为「未知」（与原 Python 端 r.district or "未知" 一致）"""
    return func.coalesce(func.nullif(col, ""), "未知")


def _day_key(col):
    """按天分组键：substr(accept_time,1,10)"""
    return func.substr(col, 1, 10)


@router.get("/wide/summary")
def wide_summary(month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
                 district: str = Query("", description="区县名过滤，空为全部"),
                 user=Depends(get_current_user), db: Session = Depends(get_db)):
    """基于装机宽表(InstallOrder)+投诉宽表(ComplaintOrder)的当月汇总，供看板/竣工率/H5/退单页使用。

    全部聚合下推 SQL（条件计数），不再把上万行 ORM 对象拉进内存。
    """
    iq = db.query(InstallOrder).filter(InstallOrder.month == month)
    cq = db.query(ComplaintOrder).filter(ComplaintOrder.month == month)
    if district:
        iq = iq.filter(InstallOrder.district == district)
        cq = cq.filter(ComplaintOrder.district == district)
    if not iq.first() and not cq.first():
        return {"has_data": False, "month": month}

    # ---- 装机总体（条件聚合一次扫描） ----
    i_status = func.coalesce(InstallOrder.status, "")
    i_theme = func.coalesce(InstallOrder.order_theme, "")
    i_row = db.query(
        func.count(InstallOrder.order_id),
        func.sum(case((i_status == "已施工", 1), else_=0)),
        func.sum(case((InstallOrder.is_refund == "是", 1), else_=0)),
        # 待装口径与前端明细页过滤一致：非已施工、非退/撤、非延伸服务、非更换光猫
        func.sum(case((i_status.like("已施工%"), 0),
                      (i_status.like("%退%"), 0), (i_status.like("%撤%"), 0),
                      (i_theme.like("%延伸服务%"), 0), (i_theme.like("%更换光猫%"), 0),
                      else_=1)),
        func.sum(case((InstallOrder.is_24h == "否", 1), else_=0)),
        func.sum(case((InstallOrder.is_8h == "否", 1), else_=0)),
        func.sum(case((InstallOrder.is_perform == "是", 1), else_=0)),
        func.sum(case((InstallOrder.is_fttr == "是", 1), else_=0)),
    ).filter(InstallOrder.month == month)
    if district:
        i_row = i_row.filter(InstallOrder.district == district)
    (total, done, refund, pending, timeout_24h, timeout_8h,
     perform, fttr) = i_row.one()
    total = total or 0
    done = int(done or 0); refund = int(refund or 0); pending = int(pending or 0)
    timeout_24h = int(timeout_24h or 0); timeout_8h = int(timeout_8h or 0)
    perform = int(perform or 0); fttr = int(fttr or 0)
    rate = round(done / total * 100, 1) if total else 0

    # ---- 装机区县聚合 ----
    dist_rows = db.query(
        _district_key(InstallOrder.district).label("district"),
        func.count(InstallOrder.order_id),
        func.sum(case((InstallOrder.status == "已施工", 1), else_=0)),
        func.sum(case((InstallOrder.is_refund == "是", 1), else_=0)),
    ).filter(InstallOrder.month == month)
    if district:
        dist_rows = dist_rows.filter(InstallOrder.district == district)
    by_district = [{"district": d, "total": t, "done": dn, "refund": rf,
                    "rate": round(dn / t * 100, 1) if t else 0}
                   for d, t, dn, rf in dist_rows.group_by(_district_key(InstallOrder.district)).all()]
    by_district.sort(key=lambda x: -x["rate"])
    for i, d in enumerate(by_district, 1):
        d["rank"] = i

    # ---- 装机人员聚合 ----
    h_rows = db.query(
        InstallOrder.handler,
        func.count(InstallOrder.order_id),
        func.sum(case((InstallOrder.status == "已施工", 1), else_=0)),
    ).filter(InstallOrder.month == month,
             func.coalesce(InstallOrder.handler, "") != "")
    if district:
        h_rows = h_rows.filter(InstallOrder.district == district)
    by_handler = [{"handler": h, "total": t, "done": dn,
                   "rate": round(dn / t * 100, 1) if t else 0}
                  for h, t, dn in h_rows.group_by(InstallOrder.handler).all()]
    by_handler.sort(key=lambda x: -x["done"])

    # ---- 装机每日趋势 ----
    d_rows = db.query(
        _day_key(InstallOrder.accept_time).label("day"),
        func.count(InstallOrder.order_id),
        func.sum(case((InstallOrder.status == "已施工", 1), else_=0)),
        func.sum(case((InstallOrder.is_refund == "是", 1), else_=0)),
    ).filter(InstallOrder.month == month,
             func.coalesce(InstallOrder.accept_time, "") != "")
    if district:
        d_rows = d_rows.filter(InstallOrder.district == district)
    daily = [{"day": d, "total": t, "done": dn, "refund": rf,
              "pending": t - dn - rf,
              "rate": round(dn / t * 100, 1) if t else 0}
             for d, t, dn, rf in d_rows.group_by(_day_key(InstallOrder.accept_time)).all()]
    daily.sort(key=lambda x: x["day"])

    # ---- 退单原因 ----
    rr_rows = db.query(
        InstallOrder.refund_reason, func.count(InstallOrder.order_id),
    ).filter(InstallOrder.month == month,
             func.coalesce(InstallOrder.refund_reason, "") != "")
    if district:
        rr_rows = rr_rows.filter(InstallOrder.district == district)
    refund_reasons = [{"reason": k, "count": c}
                      for k, c in sorted(rr_rows.group_by(InstallOrder.refund_reason).all(),
                                         key=lambda x: -x[1])]

    # ---- 投诉总体 ----
    c_row = db.query(
        func.count(ComplaintOrder.eoms_no),
        func.sum(case((ComplaintOrder.is_solved == "已维修", 1), else_=0)),
        func.sum(case((ComplaintOrder.is_24h == "否", 1), else_=0)),
        func.sum(case((ComplaintOrder.is_8h == "否", 1), else_=0)),
    ).filter(ComplaintOrder.month == month)
    if district:
        c_row = c_row.filter(ComplaintOrder.district == district)
    c_total, solved, c_timeout_24h, c_timeout_8h = c_row.one()
    c_total = c_total or 0
    solved = int(solved or 0)
    c_pending = c_total - solved
    c_rate = round(solved / c_total * 100, 1) if c_total else 0
    c_timeout_24h = int(c_timeout_24h or 0); c_timeout_8h = int(c_timeout_8h or 0)

    # ---- 投诉分类 ----
    cat_rows = db.query(
        ComplaintOrder.complaint_category, func.count(ComplaintOrder.eoms_no),
    ).filter(ComplaintOrder.month == month,
             func.coalesce(ComplaintOrder.complaint_category, "") != "")
    if district:
        cat_rows = cat_rows.filter(ComplaintOrder.district == district)
    by_category = [{"category": k, "count": c}
                   for k, c in sorted(cat_rows.group_by(ComplaintOrder.complaint_category).all(),
                                      key=lambda x: -x[1])]

    # ---- 投诉区县 ----
    cd_rows = db.query(
        _district_key(ComplaintOrder.district).label("district"),
        func.count(ComplaintOrder.eoms_no),
    ).filter(ComplaintOrder.month == month)
    if district:
        cd_rows = cd_rows.filter(ComplaintOrder.district == district)
    c_by_district = [{"district": d, "count": c}
                     for d, c in sorted(cd_rows.group_by(_district_key(ComplaintOrder.district)).all(),
                                        key=lambda x: -x[1])]

    # ---- 投诉处理人 ----
    ch_rows = db.query(
        ComplaintOrder.handler, func.count(ComplaintOrder.eoms_no),
    ).filter(ComplaintOrder.month == month,
             func.coalesce(ComplaintOrder.handler, "") != "")
    if district:
        ch_rows = ch_rows.filter(ComplaintOrder.district == district)
    c_by_handler = [{"handler": h, "count": c}
                    for h, c in sorted(ch_rows.group_by(ComplaintOrder.handler).all(),
                                       key=lambda x: -x[1])]

    # ---- 投诉每日 ----
    cdly_rows = db.query(
        _day_key(ComplaintOrder.accept_time).label("day"),
        func.count(ComplaintOrder.eoms_no),
    ).filter(ComplaintOrder.month == month,
             func.coalesce(ComplaintOrder.accept_time, "") != "")
    if district:
        cdly_rows = cdly_rows.filter(ComplaintOrder.district == district)
    c_daily = [{"day": d, "count": c}
               for d, c in sorted(cdly_rows.group_by(_day_key(ComplaintOrder.accept_time)).all())]

    return {
        "has_data": True, "month": month,
        "install": {"total": total, "done": done, "pending": pending, "refund": refund, "rate": rate,
                    "timeout_24h": timeout_24h, "timeout_8h": timeout_8h, "perform": perform, "fttr": fttr,
                    "by_district": by_district, "by_handler": by_handler, "daily": daily,
                    "refund_reasons": refund_reasons},
        "complaint": {"total": c_total, "solved": solved, "pending": c_pending, "rate": c_rate,
                      "timeout_24h": c_timeout_24h, "timeout_8h": c_timeout_8h,
                      "by_category": by_category, "by_district": c_by_district,
                      "by_handler": c_by_handler, "daily": c_daily},
    }


# ---------------- 宽表明细（首页工单明细展示，服务端筛选 + 分页） ----------------

def _apply_detail_filters(q, model, detail_type: str, hide_done: bool, hide_refund: bool,
                          hide_ext: bool, hide_modem: bool, hide_hkbz: bool):
    """明细过滤口径与前端 Dashboard 完全一致（hide_* = True 表示隐藏该类）"""
    no_col = model.order_id if detail_type == "install" else model.eoms_no
    theme_col = model.order_theme if detail_type == "install" else model.complaint_category
    status_col = func.coalesce(model.status, "")
    theme_expr = func.coalesce(theme_col, "")
    if hide_hkbz:
        q = q.filter(func.substr(no_col, 1, 7) != "HB-KHBZ")
    if hide_done:
        q = q.filter(status_col.notlike("%已施工%"))
    if hide_refund and detail_type == "install":
        q = q.filter(~(status_col.like("%退%") | status_col.like("%撤%")))
    if hide_ext:
        q = q.filter(theme_expr.notlike("%延伸服务%"))
    if hide_modem and detail_type == "install":
        q = q.filter(theme_expr.notlike("%更换光猫%"))
    return q


@router.get("/wide/orders")
def wide_orders(month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
                type: str = Query("install", pattern="^(install|complaint)$"),
                district: str = Query(""),
                page: int = Query(1, ge=1),
                page_size: int = Query(_DEFAULT_PAGE_SIZE, ge=1, le=_MAX_PAGE_SIZE),
                hide_done: bool = Query(True),
                hide_refund: bool = Query(True),
                hide_ext: bool = Query(True),
                hide_modem: bool = Query(True),
                hide_hkbz: bool = Query(True),
                user=Depends(get_current_user), db: Session = Depends(get_db)):
    """宽表工单明细：装机/投诉按受理(投诉)时间倒序，服务端筛选 + 分页（不再一次性拉 2 万行）"""
    if type == "complaint":
        model = ComplaintOrder
        pk_col = ComplaintOrder.eoms_no
    else:
        model = InstallOrder
        pk_col = InstallOrder.order_id
    q = db.query(model).filter(model.month == month)
    if district:
        q = q.filter(model.district == district)
    q = _apply_detail_filters(q, model, type, hide_done, hide_refund, hide_ext, hide_modem, hide_hkbz)
    total = q.count()
    rows = (q.order_by(model.accept_time.desc(), pk_col.desc())
            .offset((page - 1) * page_size).limit(page_size).all())
    if type == "complaint":
        return {"total": total, "rows": [{
            "no": r.eoms_no, "district": r.district, "accept_time": r.accept_time,
            "status": r.status, "handler": r.handler, "category": r.complaint_category,
            "is_solved": r.is_solved, "duration": r.order_duration,
            "appointment_time": r.appointment_time, "grid": r.grid,
            "is_high_quality": r.is_high_quality, "global_pass": r.global_pass} for r in rows]}
    return {"total": total, "rows": [{
        "no": r.order_id, "district": r.district, "grid": r.grid,
        "accept_time": r.accept_time, "theme": r.order_theme, "status": r.status,
        "handler": r.handler, "completion_time": r.completion_time,
        "is_refund": r.is_refund, "broadband_type": r.broadband_type,
        "appointment_time": r.appointment_time,
        "is_high_quality": "是" if r.is_high_quality else "否",
        "is_fttr": r.is_fttr or "否",
        "current_step": r.current_step or r.status} for r in rows]}


# ---------------- 延伸服务（手册 7.6 / 8.2） ----------------

def get_ext_target(db: Session, month: str) -> float:
    """每月人均延伸任务（元/人）：settings 表 ext_target_{month}，未配置则用默认 160"""
    s = db.get(Setting, f"ext_target_{month}")
    try:
        return float(s.value) if s and s.value else TARGET_PER_PERSON
    except (TypeError, ValueError):
        return TARGET_PER_PERSON


@router.get("/extension/target")
def extension_target(month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
                     user=Depends(get_current_user), db: Session = Depends(get_db)):
    latest = db.query(func.max(ExtensionOrder.accept_time)).filter(
        ExtensionOrder.month == month).scalar() or ""
    return {"month": month, "target": get_ext_target(db, month), "default": TARGET_PER_PERSON,
            "latest": latest}


@router.put("/extension/target")
def set_extension_target(month: str = Query(..., pattern=r"^\d{4}-\d{2}$"), target: float = Query(..., gt=0),
                         user=Depends(require_admin), db: Session = Depends(get_db)):
    key = f"ext_target_{month}"
    s = db.get(Setting, key)
    if s:
        s.value = str(target)
    else:
        db.add(Setting(key=key, value=str(target)))
    db.add(AuditLog(username=user.username, action="修改人均任务", detail=f"{month} → {target} 元/人"))
    db.commit()
    return {"month": month, "target": target}


def _ext_ranking(db: Session, user, month: str):
    members = db.query(ExtensionMember).all()
    member_cnt = defaultdict(int)
    grids = defaultdict(list)
    for m in members:
        member_cnt[m.district] += 1
        grids[m.district].append({"name": m.name, "grid": m.grid, "position": m.position})
    q = db.query(ExtensionOrder).filter(ExtensionOrder.month == month)  # month 从受理时间推导，禁止硬编码（手册 10.2）
    ids = scope_district_ids(user, db)
    if ids is not None:
        dnames = {d.name for d in db.query(District).all() if d.id in ids}
        q = q.filter(ExtensionOrder.district.in_(dnames))
    rows = q.with_entities(ExtensionOrder.district, func.sum(ExtensionOrder.income),
                           func.count(ExtensionOrder.id)).group_by(ExtensionOrder.district).all()
    active_rows = q.with_entities(ExtensionOrder.district,
                                  func.count(func.distinct(ExtensionOrder.handler))
                                  ).group_by(ExtensionOrder.district).all()
    active_map = {d: c for d, c in active_rows}
    ext_target = get_ext_target(db, month)
    out = []
    for dname, income, cnt in rows:
        target = member_cnt.get(dname, 0) * ext_target
        out.append({"district": dname, "members": member_cnt.get(dname, 0), "target": target,
                    "income": round(income or 0, 1), "orders": cnt,
                    "active": active_map.get(dname, 0),
                    "gap": round((income or 0) - target, 1),
                    "ratio": round((income or 0) / target * 100, 1) if target else 0})
    out.sort(key=lambda x: -x["ratio"])
    for i, r in enumerate(out, 1):
        r["rank"] = i
    return out, grids


@router.get("/extension/district-ranking")
def extension_ranking(month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
                      user=Depends(get_current_user), db: Session = Depends(get_db)):
    out, _ = _ext_ranking(db, user, month)
    return out


@router.get("/extension/personal")
def extension_personal(month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
                       user=Depends(get_current_user), db: Session = Depends(get_db)):
    ext_target = get_ext_target(db, month)
    # 以在册人员为基准（含 0 收入人员），叠加当月订单聚合；不在册但有单的人员追加在后
    agg: dict = {}
    for m in db.query(ExtensionMember).all():
        agg[m.name] = {"handler": m.name, "district": m.district, "grid": m.grid, "income": 0.0, "orders": 0}
    rows = db.query(ExtensionOrder.handler, ExtensionOrder.district,
                    func.sum(ExtensionOrder.income), func.count(ExtensionOrder.id)).filter(
        ExtensionOrder.month == month).group_by(ExtensionOrder.handler, ExtensionOrder.district).all()
    for h, d, i, c in rows:
        if h in agg:
            agg[h]["income"] += i or 0
            agg[h]["orders"] += c
        else:
            agg[h] = {"handler": h, "district": d, "grid": "", "income": i or 0, "orders": c}
    out = [{"handler": v["handler"], "district": v["district"], "grid": v["grid"],
            "income": round(v["income"], 1), "orders": v["orders"], "target": ext_target,
            "ratio": round(v["income"] / ext_target * 100, 1) if ext_target else 0}
           for v in agg.values()]
    out.sort(key=lambda x: -x["income"])
    return out


@router.get("/extension/team")
def extension_team(month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
                   user=Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(ExtensionOrder.handler, ExtensionOrder.district, func.sum(ExtensionOrder.income),
                    func.count(ExtensionOrder.id)).filter(ExtensionOrder.month == month).group_by(
        ExtensionOrder.handler, ExtensionOrder.district).all()
    member_pos = {}
    for m in db.query(ExtensionMember).all():
        member_pos[m.name] = m.position
    team_agg = defaultdict(lambda: {"income": 0.0, "orders": 0, "people": set()})
    for h, d, i, c in rows:
        key = (d, member_pos.get(h, "施工员"))
        team_agg[key]["income"] += i or 0
        team_agg[key]["orders"] += c
        team_agg[key]["people"].add(h)
    out = [{"district": d, "position": p, "people": len(v["people"]), "orders": v["orders"],
            "income": round(v["income"], 1)} for (d, p), v in team_agg.items()]
    out.sort(key=lambda x: -x["income"])
    return out


# ---------------- 综合得分 / 短板（手册 8.4） ----------------

@router.get("/definitions")
def definitions(db: Session = Depends(get_db), user=Depends(get_current_user)):
    return [{"id": d.id, "code": d.code, "name": d.name, "weight": d.weight,
             "max_score": d.max_score, "formula": d.formula}
            for d in db.query(MetricDefinition).all()]


@router.get("/dashboard/district-ranking")
def district_ranking(month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
                     user=Depends(get_current_user), db: Session = Depends(get_db)):
    """综合得分 = 竣工率40% + 延伸完成占比30% + 投诉H5已收率15% + 存量压降10%"""
    cr = {r["district"]: r for r in completion_districts(month, user, db)}
    ext, _ = _ext_ranking(db, user, month)
    ext_map = {r["district"]: r for r in ext}
    receipts = {r[0]: r[1] for r in db.query(H5Receipt.order_no, H5Receipt.h5_received).all()}
    complaints = db.query(H5Complaint.eoms_order_no, H5Complaint.construction_dept_person).filter(
        H5Complaint.order_status == "已施工", H5Complaint.eoms_order_no.like("HB-058%"),
        H5Complaint.month == month).all()
    comp_sent, comp_done = defaultdict(int), defaultdict(int)
    for no, person in complaints:
        comp_sent[person] += 1
        if receipts.get(no) == "已参评":
            comp_done[person] += 1
    # 存量压降：处理中/待处理工单占比（低优）
    wo_q = db.query(WorkOrder.district_id, WorkOrder.status, func.count(WorkOrder.id)).filter(
        WorkOrder.month == month, WorkOrder.district_id.isnot(None))
    ids = scope_district_ids(user, db)
    if ids is not None:
        wo_q = wo_q.filter(WorkOrder.district_id.in_(ids))
    pending_by_district, total_by_district = defaultdict(int), defaultdict(int)
    for did, status, cnt in wo_q.group_by(WorkOrder.district_id, WorkOrder.status).all():
        total_by_district[did] += cnt
        if status in PENDING_STATUS:
            pending_by_district[did] += cnt
    districts = db.query(District).all()
    user_district = {u.real_name: u.district.name for u in db.query(User).all() if u.district}
    out = []
    for d in districts:
        c = cr.get(d.name, {"rate": 0})
        e = ext_map.get(d.name, {"ratio": 0, "income": 0, "target": 0})
        sent = done = 0
        for person, n in comp_sent.items():
            if user_district.get(person) == d.name:
                sent += n
                done += comp_done.get(person, 0)
        h5_rate = done / sent * 100 if sent else 0
        tot = total_by_district.get(d.id, 0)
        pend = pending_by_district.get(d.id, 0)
        backlog_score = round(100 - (pend / tot * 100), 2) if tot else 60
        scores = {"completion": round(c["rate"], 2), "extension": round(min(e["ratio"], 120), 2),
                  "h5": round(h5_rate, 2), "backlog": backlog_score}
        total = (scores["completion"] * 0.4 + min(scores["extension"], 100) * 0.3 +
                 scores["h5"] * 0.15 + min(scores["backlog"], 100) * 0.1)
        shortcoming = [k for k, v in scores.items() if v < 60]
        out.append({"district": d.name, "scores": scores, "total_score": round(total, 2),
                    "completion_rate": c["rate"], "ext_ratio": e["ratio"], "ext_income": e["income"],
                    "h5_sent": sent, "h5_done": done, "is_shortcoming": len(shortcoming) > 0,
                    "shortcomings": shortcoming})
    out.sort(key=lambda x: -x["total_score"])
    for i, r in enumerate(out, 1):
        r["rank"] = i
    return out


@router.get("/dashboard/user-ranking")
def user_ranking(month: str = Query(..., pattern=r"^\d{4}-\d{2}$"), limit: int = 50,
                 user=Depends(get_current_user), db: Session = Depends(get_db)):
    cr = {(r["handler"]): r for r in completion_users(month, user, db)}
    rows = db.query(ExtensionOrder.handler, func.sum(ExtensionOrder.income)).filter(
        ExtensionOrder.month == month).group_by(ExtensionOrder.handler).all()
    ext = {h: round(i or 0, 1) for h, i in rows}
    users = {u.real_name: u for u in db.query(User).all() if u.district}
    out = []
    for name, u in users.items():
        if u.role.code != "field_worker":
            continue
        c = cr.get(name, {"rate": 0, "total": 0})
        e_income = ext.get(name, 0)
        total = c["rate"] * 0.6 + min(e_income / get_ext_target(db, month) * 100, 100) * 0.4
        out.append({"user": name, "district": u.district.name, "team": u.team, "grid": u.grid,
                    "completion_rate": c["rate"], "orders": c["total"], "ext_income": e_income,
                    "total_score": round(total, 2)})
    out.sort(key=lambda x: -x["total_score"])
    for i, r in enumerate(out, 1):
        r["rank"] = i
    return out[:limit]


@router.get("/dashboard/shortcomings")
def shortcomings(month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
                 user=Depends(get_current_user), db: Session = Depends(get_db)):
    ranking = district_ranking(month, user, db)
    out = []
    for r in ranking:
        if r["is_shortcoming"]:
            out.append({"district": r["district"], "rank": r["rank"],
                        "total_score": r["total_score"], "shortcomings": r["shortcomings"],
                        "detail": {"竣工率": r["completion_rate"], "延伸完成占比": r["ext_ratio"],
                                   "H5已收率": round(r["h5_done"] / r["h5_sent"] * 100, 1) if r["h5_sent"] else 0}})
    return out


@router.post("/calculate")
def calculate(user=Depends(get_current_user)):
    return {"msg": "指标为实时计算，无需手动重算", "calculated": True}
