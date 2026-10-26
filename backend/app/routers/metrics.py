"""指标计算 —— 竣工率 / 延伸服务 / 综合得分 / 短板分析（口径严格对齐开发手册 七、八）"""
from collections import defaultdict
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from ..config import COMPLETED_STATUS, PENDING_STATUS, TARGET_PER_PERSON
from ..database import get_db
from ..models import (WorkOrder, District, ExtensionOrder, ExtensionMember, H5Complaint, H5Receipt,
                      MetricDefinition, User, Setting, AuditLog, CompletionRecord, SyncJob,
                      InstallOrder, ComplaintOrder)
from ..deps import get_current_user, scope_district_ids, require_admin, get_role

router = APIRouter(prefix="/metrics", tags=["metrics"])


def _month_filter(col, month: str):
    return col.like(f"{month}%")


def _base_query(db: Session, user, month: str):
    """竣工率基础过滤：宽带类型=个人 + 非HB- + 受理时间在月内 + 区县非空（手册 7.5）"""
    ids = scope_district_ids(user, db)
    q = (db.query(WorkOrder)
         .filter(WorkOrder.broadband_type == "个人",
                 ~WorkOrder.order_id.like("HB-%"),
                 _month_filter(WorkOrder.accept_time, month),
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
    q = db.query(WorkOrder).filter(_month_filter(WorkOrder.accept_time, month),
                                   WorkOrder.district_id.isnot(None))
    ids = scope_district_ids(user, db)
    if ids is not None:
        q = q.filter(WorkOrder.district_id.in_(ids))
    rows = q.with_entities(WorkOrder.order_theme, func.count(WorkOrder.id)).group_by(WorkOrder.order_theme).all()
    cr_q = db.query(WorkOrder.order_theme, func.count()).join(
        CancelRescheduleOrder, CancelRescheduleOrder.order_id == WorkOrder.order_id).filter(
        _month_filter(WorkOrder.accept_time, month)).group_by(WorkOrder.order_theme)
    cancel_map = dict(cr_q.all())
    return [{"outlet": t, "accepted": c, "cancelled": cancel_map.get(t, 0)} for t, c in rows]


@router.get("/completion-rate/cancel-reschedule")
def cancel_reschedule(month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
                      user=Depends(get_current_user), db: Session = Depends(get_db)):
    from ..models import CancelRescheduleOrder, District
    q = db.query(CancelRescheduleOrder).filter(_month_filter(CancelRescheduleOrder.accept_time, month))
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
        _month_filter(WorkOrder.accept_time, month)).scalar() or ""
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
                 "fttr_rate": round(r.fttr_done / r.fttr_orders * 100, 1) if r.fttr_orders else 0,
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

# ---- 洪湖市 7 标准网格映射（子网格 → 标准网格） ----
_HONGHU_GRID_MAP = {
    "洪湖城区网格": "洪湖城区网格",
    "洪湖荷花网格": "洪湖城区网格",
    "洪湖柏枝网格": "洪湖柏枝网格",
    "洪湖文泉网格": "洪湖柏枝网格",
    "洪湖峰口网格": "洪湖峰口网格",
    "洪湖沙口网格": "洪湖峰口网格",
    "洪湖万全网格": "洪湖峰口网格",
    "洪湖瞿家湾网格": "洪湖峰口网格",
    "洪湖大同网格": "洪湖大同网格",
    "洪湖乌林网格": "洪湖大同网格",
    "洪湖老湾网格": "洪湖大同网格",
    "洪湖新滩网格": "洪湖大同网格",
    "洪湖曹市网格": "洪湖曹市网格",
    "洪湖府场网格": "洪湖曹市网格",
    "洪湖戴家场网格": "洪湖曹市网格",
    "洪湖大沙网格": "洪湖大沙网格",
    "洪湖龙口网格": "洪湖大沙网格",
    "洪湖燕窝网格": "洪湖大沙网格",
    "洪湖滨湖网格": "洪湖滨湖网格",
    "洪湖汊河网格": "洪湖滨湖网格",
    "洪湖螺山网格": "洪湖滨湖网格",
    "洪湖小港网格": "洪湖滨湖网格",
    "洪湖黄家口网格": "洪湖滨湖网格",
}


def _std_grid(grid: str, district: str = "") -> str:
    """只对洪湖市做 7 标准网格归并，其他区县原样返回"""
    if district != "洪湖市" or not grid:
        return grid
    return _HONGHU_GRID_MAP.get(grid, grid)


def _build_wide_summary(month: str, district: str, db: Session):
    """宽表汇总核心逻辑"""
    iq = db.query(InstallOrder).filter(InstallOrder.month == month)
    cq = db.query(ComplaintOrder).filter(ComplaintOrder.month == month)
    if district:
        iq = iq.filter(InstallOrder.district == district)
        cq = cq.filter(ComplaintOrder.district == district)
    installs = iq.all()
    complaints = cq.all()
    if not installs and not complaints:
        return {"has_data": False, "month": month}

    # ---- 装机 ----
    total = len(installs)
    status_of = lambda r: r.status or ""
    done = sum(1 for r in installs if status_of(r) == "已施工")
    # 撤单/退单拆分（按 status 关键字，is_refund="是" 兜底）
    cancel = sum(1 for r in installs if "撤" in status_of(r))
    refund_only = sum(1 for r in installs if "退" in status_of(r) and "撤" not in status_of(r))
    if cancel + refund_only == 0:
        refund_total = sum(1 for r in installs if r.is_refund == "是")
        cancel = refund_total  # 兜底：若 status 没细分，全部算撤单
    refund = cancel + refund_only  # 退撤单合计
    # 处理中：非已施工、非撤、非退、非延伸/换光猫
    pending = sum(1 for r in installs
                  if status_of(r) != "已施工"
                  and "撤" not in status_of(r) and "退" not in status_of(r)
                  and "延伸服务" not in (r.order_theme or "")
                  and "更换光猫" not in (r.order_theme or ""))
    rate = round(done / total * 100, 1) if total else 0
    refund_rate = round(refund / total * 100, 1) if total else 0
    timeout_24h = sum(1 for r in installs if r.is_24h == "否")
    timeout_8h = sum(1 for r in installs if r.is_8h == "否")
    perform_yes = sum(1 for r in installs if r.is_perform == "是")
    perform_no = sum(1 for r in installs if r.is_perform == "否")
    perform = perform_yes
    fttr = sum(1 for r in installs if r.is_fttr == "是")

    # 区县聚合（保留）
    dist_agg = defaultdict(lambda: {"total": 0, "done": 0, "refund": 0})
    for r in installs:
        d = dist_agg[r.district or "未知"]
        d["total"] += 1; d["done"] += (1 if status_of(r) == "已施工" else 0)
        d["refund"] += (1 if "退" in status_of(r) or "撤" in status_of(r) else 0)
    by_district = [{"district": d, "total": a["total"], "done": a["done"], "refund": a["refund"],
                    "rate": round(a["done"] / a["total"] * 100, 1) if a["total"] else 0}
                   for d, a in dist_agg.items()]
    by_district.sort(key=lambda x: -x["rate"])
    for i, d in enumerate(by_district, 1):
        d["rank"] = i

    # 人员聚合（保留）
    handler_agg = defaultdict(lambda: {"total": 0, "done": 0})
    for r in installs:
        if not r.handler:
            continue
        a = handler_agg[r.handler]
        a["total"] += 1; a["done"] += (1 if status_of(r) == "已施工" else 0)
    by_handler = [{"handler": h, "total": a["total"], "done": a["done"],
                   "rate": round(a["done"] / a["total"] * 100, 1) if a["total"] else 0}
                  for h, a in handler_agg.items()]
    by_handler.sort(key=lambda x: -x["done"])

    # ---- 网格汇总（新，洪湖市按 7 标准网格归并） ----
    grid_agg = defaultdict(lambda: {"total": 0, "done": 0, "cancel": 0, "refund_only": 0, "pending": 0})
    for r in installs:
        if not r.grid:
            continue
        g = grid_agg[_std_grid(r.grid, r.district)]
        g["total"] += 1
        s = status_of(r)
        if s == "已施工":
            g["done"] += 1
        elif "撤" in s:
            g["cancel"] += 1
        elif "退" in s:
            g["refund_only"] += 1
        else:
            g["pending"] += 1
    by_grid = []
    for g, a in sorted(grid_agg.items(), key=lambda x: -x[1]["done"]):
        refund_g = a["cancel"] + a["refund_only"]
        by_grid.append({
            "grid": g, "total": a["total"], "done": a["done"],
            "cancel": a["cancel"], "refund_only": a["refund_only"],
            "pending": a["pending"], "refund_total": refund_g,
            "completion_rate": round(a["done"] / a["total"] * 100, 1) if a["total"] else 0,
            "refund_rate": round(refund_g / a["total"] * 100, 1) if a["total"] else 0,
        })

    # ---- 撤单分析-网格（新） ----
    cancel_grid = []
    for g, a in sorted(grid_agg.items(), key=lambda x: -x[1]["cancel"]):
        refund_g = a["cancel"] + a["refund_only"]
        cancel_grid.append({
            "grid": g, "total": a["total"],
            "cancel": a["cancel"],
            "cancel_rate": round(a["cancel"] / a["total"] * 100, 1) if a["total"] else 0,
            "refund_total": refund_g,
            "cancel_share": round(a["cancel"] / refund_g * 100, 1) if refund_g else 0,
        })

    # ---- 撤单分析-人员 Top30（新） ----
    person_agg = defaultdict(lambda: {"total": 0, "done": 0, "cancel": 0, "refund_only": 0, "pending": 0})
    for r in installs:
        if not r.handler:
            continue
        a = person_agg[r.handler]
        a["total"] += 1
        s = status_of(r)
        if s == "已施工":
            a["done"] += 1
        elif "撤" in s:
            a["cancel"] += 1
        elif "退" in s:
            a["refund_only"] += 1
        else:
            a["pending"] += 1
    cancel_person = []
    for h, a in sorted(person_agg.items(), key=lambda x: -x[1]["cancel"])[:30]:
        refund_h = a["cancel"] + a["refund_only"]
        cancel_person.append({
            "handler": h, "total": a["total"], "done": a["done"],
            "cancel": a["cancel"], "refund_only": a["refund_only"],
            "pending": a["pending"],
            "completion_rate": round(a["done"] / a["total"] * 100, 1) if a["total"] else 0,
            "cancel_rate": round(a["cancel"] / a["total"] * 100, 1) if a["total"] else 0,
            "refund_rate": round(refund_h / a["total"] * 100, 1) if a["total"] else 0,
        })

    # ---- 装维人员履约 Top30（新） ----
    perform_agg = defaultdict(lambda: {"done": 0, "perf_yes": 0, "perf_no": 0})
    for r in installs:
        if not r.handler or status_of(r) != "已施工":
            continue
        a = perform_agg[r.handler]
        a["done"] += 1
        if r.is_perform == "是":
            a["perf_yes"] += 1
        elif r.is_perform == "否":
            a["perf_no"] += 1
    total_done = sum(1 for r in installs if status_of(r) == "已施工") or 1
    perform_person = []
    for h, a in sorted(perform_agg.items(), key=lambda x: -x[1]["done"])[:30]:
        perform_person.append({
            "handler": h, "done": a["done"],
            "perf_yes": a["perf_yes"], "perf_no": a["perf_no"],
            "perform_rate": round(a["perf_yes"] / a["done"] * 100, 1) if a["done"] else 0,
            "done_share": round(a["done"] / total_done * 100, 1),
        })

    # 每日趋势
    daily_agg = defaultdict(lambda: {"total": 0, "done": 0, "refund": 0})
    for r in installs:
        day = r.accept_time[:10] if r.accept_time else ""
        if not day:
            continue
        a = daily_agg[day]
        a["total"] += 1; a["done"] += (1 if status_of(r) == "已施工" else 0)
        a["refund"] += (1 if ("退" in status_of(r) or "撤" in status_of(r)) else 0)
    daily = [{"day": d, "total": a["total"], "done": a["done"], "refund": a["refund"],
              "pending": a["total"] - a["done"] - a["refund"],
              "rate": round(a["done"] / a["total"] * 100, 1) if a["total"] else 0}
             for d, a in sorted(daily_agg.items())]

    # 退单原因（一级 + 二级细分；按关键字归类前台 vs 用户）
    reason_agg = defaultdict(int)
    reason_category = defaultdict(int)   # 前台原因 vs 用户原因
    for r in installs:
        if not r.refund_reason:
            continue
        reason_agg[r.refund_reason] += 1
        k = r.refund_reason
        if "用户" in k or "不安装" in k or "拒接" in k:
            reason_category["用户原因"] += 1
        else:
            reason_category["前台原因"] += 1
    refund_reasons = [{"reason": k, "count": v} for k, v in sorted(reason_agg.items(), key=lambda x: -x[1])]
    refund_reason_cat = [{"category": k, "count": v} for k, v in sorted(reason_category.items(), key=lambda x: -x[1])]

    # ---- 投诉 ----
    c_total = len(complaints)
    solved = sum(1 for r in complaints if r.is_solved == "已维修")
    c_pending = c_total - solved
    c_rate = round(solved / c_total * 100, 1) if c_total else 0
    c_timeout_24h = sum(1 for r in complaints if r.is_24h == "否")
    c_timeout_8h = sum(1 for r in complaints if r.is_8h == "否")
    cat_agg = defaultdict(int)
    for r in complaints:
        if r.complaint_category:
            cat_agg[r.complaint_category] += 1
    by_category = [{"category": k, "count": v} for k, v in sorted(cat_agg.items(), key=lambda x: -x[1])]
    c_dist_agg = defaultdict(int)
    for r in complaints:
        c_dist_agg[r.district or "未知"] += 1
    c_by_district = [{"district": d, "count": c} for d, c in sorted(c_dist_agg.items(), key=lambda x: -x[1])]
    c_handler_agg = defaultdict(int)
    for r in complaints:
        if r.handler:
            c_handler_agg[r.handler] += 1
    c_by_handler = [{"handler": h, "count": c} for h, c in sorted(c_handler_agg.items(), key=lambda x: -x[1])]
    c_daily_agg = defaultdict(int)
    for r in complaints:
        day = r.accept_time[:10] if r.accept_time else ""
        if day:
            c_daily_agg[day] += 1
    c_daily = [{"day": d, "count": c} for d, c in sorted(c_daily_agg.items())]

    return {
        "has_data": True, "month": month,
        "install": {
            # 指标总览 8 卡
            "total": total, "done": done, "cancel": cancel, "refund_only": refund_only,
            "refund": refund, "pending": pending,
            "rate": rate, "refund_rate": refund_rate,
            "timeout_24h": timeout_24h, "timeout_8h": timeout_8h,
            "perform": perform, "perform_yes": perform_yes, "perform_no": perform_no,
            "fttr": fttr,
            # 各维度聚合
            "by_district": by_district,
            "by_handler": by_handler,
            "by_grid": by_grid,            # 网格汇总
            "cancel_grid": cancel_grid,    # 撤单分析-网格
            "cancel_person": cancel_person,  # 撤单分析-人员 Top30
            "perform_person": perform_person,  # 装维履约 Top30
            # 退单原因
            "refund_reasons": refund_reasons,
            "refund_reason_cat": refund_reason_cat,  # 前台原因 vs 用户原因
            # 每日
            "daily": daily,
        },
        "complaint": {"total": c_total, "solved": solved, "pending": c_pending, "rate": c_rate,
                      "timeout_24h": c_timeout_24h, "timeout_8h": c_timeout_8h,
                      "by_category": by_category, "by_district": c_by_district,
                      "by_handler": c_by_handler, "daily": c_daily},
    }


@router.get("/wide/summary")
def wide_summary(month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
                 district: str = Query("", description="区县名过滤，空为全部"),
                 user=Depends(get_current_user), db: Session = Depends(get_db)):
    """基于装机宽表(InstallOrder)+投诉宽表(ComplaintOrder)的当月汇总，供看板/竣工率/H5/退单页使用（兼容 GET）"""
    return _build_wide_summary_cached(month, district, db)


# ---------------- 宽表汇总 TTL 缓存（2026-10-26 性能优化） ----------------
import time as _time
import threading as _threading

_WIDE_CACHE: dict = {}          # (month, district) -> (ver, expire_ts, payload)
_WIDE_CACHE_TTL = 60            # 秒
_WIDE_CACHE_LOCK = _threading.Lock()


def _wide_data_version(db) -> str:
    """数据版本位：两张宽表的行数 + 最大 rowid。上传/覆盖后必然变化，用于让缓存失效。"""
    try:
        i = db.execute(text("SELECT COUNT(*), COALESCE(MAX(rowid),0) FROM install_orders")).fetchone()
        c = db.execute(text("SELECT COUNT(*), COALESCE(MAX(rowid),0) FROM complaint_orders")).fetchone()
        return f"{i[0]}-{i[1]}-{c[0]}-{c[1]}"
    except Exception:
        return ""


def _build_wide_summary_cached(month: str, district: str, db):
    key = (month, district)
    now = _time.time()
    ver = _wide_data_version(db)
    with _WIDE_CACHE_LOCK:
        hit = _WIDE_CACHE.get(key)
        if hit and hit[0] == ver and hit[1] > now:
            return hit[2]
    payload = _build_wide_summary(month, district, db)   # 未命中才真算
    with _WIDE_CACHE_LOCK:
        _WIDE_CACHE[key] = (ver, _time.time() + _WIDE_CACHE_TTL, payload)
        # 防止字典无界增长
        if len(_WIDE_CACHE) > 64:
            for k in [k for k, v in _WIDE_CACHE.items() if v[1] <= _time.time()]:
                _WIDE_CACHE.pop(k, None)
    return payload


class _WideSummaryReq(BaseModel):
    month: str
    district: str = ""


@router.post("/wide/summary")
def wide_summary_post(req: _WideSummaryReq,
                      user=Depends(get_current_user), db: Session = Depends(get_db)):
    """POST 版本：参数放 body，规避中文 query 被浏览器/uvicorn h11 拒绝的问题"""
    return _build_wide_summary_cached(req.month, req.district, db)


# ---------------- 宽表明细（首页工单明细展示） ----------------

@router.get("/wide/orders")
def wide_orders(month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
                type: str = Query("install", pattern="^(install|complaint)$"),
                district: str = Query(""),
                page: int = Query(1, ge=1),
                page_size: int = Query(100, ge=1, le=500),
                hide_done: bool = Query(False),
                hide_refund: bool = Query(False),
                hide_ext: bool = Query(False),
                hide_modem: bool = Query(False),
                hide_hkbz: bool = Query(False),
                limit: int = Query(20000, le=20000),
                user=Depends(get_current_user), db: Session = Depends(get_db)):
    """宽表工单明细：装机/投诉按受理(投诉)时间倒序

    2026-10-26 性能修复：原实现只认 limit(默认20000)，前端传的 page/page_size/hide_*
    被 FastAPI 静默丢弃，导致每次全量返回 640KB+、分页与复选框全部失效。
    现改为 SQL 层过滤 + 真分页，只回传当前页，并返回 total 供分页器。
    """
    if type == "complaint":
        q = db.query(ComplaintOrder).filter(ComplaintOrder.month == month)
        if district:
            q = q.filter(ComplaintOrder.district == district)
        total = q.count()
        # 同时间点多条时 SQLite 排序不稳定，补主键(eoms_no)保证翻页不跳行
        rows = q.order_by(ComplaintOrder.accept_time.desc(),
                          ComplaintOrder.eoms_no.desc()).offset(
            (page - 1) * page_size).limit(page_size).all()
        return {"rows": [{
            "no": r.eoms_no, "district": r.district, "accept_time": r.accept_time,
            "status": r.status, "handler": r.handler, "category": r.complaint_category,
            "is_solved": r.is_solved, "duration": r.order_duration,
            "appointment_time": r.appointment_time, "grid": r.grid,
            "is_high_quality": r.is_high_quality, "global_pass": r.global_pass} for r in rows],
            "total": total, "page": page, "page_size": page_size}

    q = db.query(InstallOrder).filter(InstallOrder.month == month)
    if district:
        q = q.filter(InstallOrder.district == district)
    # ---- 过滤下推到 SQL，与明细页签复选框一一对应 ----
    if hide_done:      # 不显示「已施工」
        q = q.filter(func.coalesce(InstallOrder.status, "") != "已施工")
    if hide_refund:    # 不显示已退/撤单
        q = q.filter(~func.coalesce(InstallOrder.status, "").like("%撤%"),
                     ~func.coalesce(InstallOrder.status, "").like("%退%"),
                     func.coalesce(InstallOrder.is_refund, "") != "是")
    if hide_ext:       # 不显示延伸服务
        q = q.filter(~func.coalesce(InstallOrder.order_theme, "").like("%延伸服务%"))
    if hide_modem:     # 不显示更换光猫
        q = q.filter(~func.coalesce(InstallOrder.order_theme, "").like("%更换光猫%"))
    if hide_hkbz:      # 不显示 HB-KHBZ 单（定单编码含 KHBZ）
        q = q.filter(~func.coalesce(InstallOrder.order_id, "").like("%KHBZ%"))
    total = q.count()
    # 同时间点多条时 SQLite 排序不稳定，补主键(order_id)保证翻页不跳行
    rows = q.order_by(InstallOrder.accept_time.desc(),
                      InstallOrder.order_id.desc()).offset(
        (page - 1) * page_size).limit(page_size).all()
    return {"rows": [{
        "no": r.order_id, "district": r.district, "grid": r.grid,
        "accept_time": r.accept_time, "theme": r.order_theme, "status": r.status,
        "handler": r.handler, "completion_time": r.completion_time,
        "is_refund": r.is_refund, "broadband_type": r.broadband_type,
        "appointment_time": r.appointment_time,
        "is_high_quality": "是" if r.is_high_quality else "否",
        "is_fttr": r.is_fttr or "否",
        "current_step": r.current_step or r.status} for r in rows],
        "total": total, "page": page, "page_size": page_size}


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
        _month_filter(ExtensionOrder.accept_time, month)).scalar() or ""
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


# 洪湖市按标准 7 网格通报（2026-10-26 新增）；其他区县仍按岗位汇总
HONGHU_GRID_DISTRICT = "洪湖市"
HONGHU_STD_GRIDS = ("洪湖柏枝网格", "洪湖滨湖网格", "洪湖曹市网格", "洪湖大沙网格",
                    "洪湖大同网格", "洪湖峰口网格", "洪湖新堤网格")
HONGHU_OTHER_LABEL = "洪湖市分公司/其他"


def _norm_honghu_grid(grid: str) -> str:
    """把洪湖花名册的 grid 归类到 7 个标准网格；分公司/临时网格/空值归入「分公司/其他」。
    非标准名（未来新增的网格）原样保留，避免静默丢数据。"""
    g = (grid or "").strip()
    if not g or "分公司" in g or "临时" in g:
        return HONGHU_OTHER_LABEL
    return g


@router.get("/extension/team")
def extension_team(month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
                   user=Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(ExtensionOrder.handler, ExtensionOrder.district, func.sum(ExtensionOrder.income),
                    func.count(ExtensionOrder.id)).filter(ExtensionOrder.month == month).group_by(
        ExtensionOrder.handler, ExtensionOrder.district).all()
    member_pos = {}
    member_grid = {}
    for m in db.query(ExtensionMember).all():
        member_pos[m.name] = m.position
        member_grid[m.name] = m.grid
    team_agg = defaultdict(lambda: {"income": 0.0, "orders": 0, "people": set()})

    # 洪湖市：以花名册为基准按真实网格铺底（含 0 单人员），保证 7 网格即使无人接单也出现在通报里
    hh_agg = defaultdict(lambda: {"income": 0.0, "orders": 0, "people": set()})
    for m in db.query(ExtensionMember).filter(ExtensionMember.district == HONGHU_GRID_DISTRICT).all():
        hh_agg[_norm_honghu_grid(m.grid)]["people"].add(m.name)

    for h, d, i, c in rows:
        if d == HONGHU_GRID_DISTRICT:
            g = _norm_honghu_grid(member_grid.get(h))
            hh_agg[g]["income"] += i or 0
            hh_agg[g]["orders"] += c
            hh_agg[g]["people"].add(h)
        else:
            key = (d, member_pos.get(h, "施工员"))
            team_agg[key]["income"] += i or 0
            team_agg[key]["orders"] += c
            team_agg[key]["people"].add(h)

    out = [{"district": d, "position": p, "people": len(v["people"]), "orders": v["orders"],
            "income": round(v["income"], 1)} for (d, p), v in team_agg.items()]
    # 洪湖网格行：position 字段承载网格名，前端「网格」列直接展示；group_by='grid' 供前端区分渲染
    for g, v in hh_agg.items():
        out.append({"district": HONGHU_GRID_DISTRICT, "position": g, "grid": g,
                    "group_by": "grid", "people": len(v["people"]),
                    "orders": v["orders"], "income": round(v["income"], 1)})
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
        _month_filter(H5Complaint.accept_time, month)).all()
    comp_sent, comp_done = defaultdict(int), defaultdict(int)
    for no, person in complaints:
        comp_sent[person] += 1
        if receipts.get(no) == "已参评":
            comp_done[person] += 1
    # 存量压降：处理中/待处理工单占比（低优）
    wo_q = db.query(WorkOrder.district_id, WorkOrder.status, func.count(WorkOrder.id)).filter(
        _month_filter(WorkOrder.accept_time, month), WorkOrder.district_id.isnot(None))
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
