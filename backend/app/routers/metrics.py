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
from ..h5_rule import judge_complaint_h5 as _h5j
from ..status_resolver import (NOT_PENDING, classify, is_pending, realtime_cand,
                               report_cand, resolve, wide_cand, complaint_realtime_cand)

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
# v1.7.0：标准名统一用用户表格口径「洪湖新堤网格」（原写作「洪湖城区网格」，
# 与《工单多维分析_标准7网格版》的 7 网格名不一致，页面与表格对不上）。
# 映射依据：表格「洪湖标准7网格」sheet 第 26~34 行的归属映射表。
_HONGHU_GRID_MAP = {
    # 新堤网格 = 荷花 + 城区 + 新堤（表格第 30 行）
    "洪湖城区网格": "洪湖新堤网格",
    "洪湖荷花网格": "洪湖新堤网格",
    "洪湖新堤网格": "洪湖新堤网格",
    # 柏枝网格 = 聚金 + 文泉 + 柏枝（表格第 34 行）
    "洪湖柏枝网格": "洪湖柏枝网格",
    "洪湖文泉网格": "洪湖柏枝网格",
    "洪湖聚金网格": "洪湖柏枝网格",
    # 峰口网格 = 万全 + 峰口 + 瞿家湾 + 沙口（表格第 28 行）
    "洪湖峰口网格": "洪湖峰口网格",
    "洪湖沙口网格": "洪湖峰口网格",
    "洪湖万全网格": "洪湖峰口网格",
    "洪湖瞿家湾网格": "洪湖峰口网格",
    # 大同网格 = 大同 + 老湾 + 新滩 + 乌林（表格第 33 行）
    "洪湖大同网格": "洪湖大同网格",
    "洪湖乌林网格": "洪湖大同网格",
    "洪湖老湾网格": "洪湖大同网格",
    "洪湖新滩网格": "洪湖大同网格",
    # 曹市网格 = 府场 + 戴家场 + 曹市（表格第 31 行）
    "洪湖曹市网格": "洪湖曹市网格",
    "洪湖府场网格": "洪湖曹市网格",
    "洪湖戴家场网格": "洪湖曹市网格",
    # 大沙网格 = 燕窝 + 龙口 + 大沙（表格第 32 行）
    "洪湖大沙网格": "洪湖大沙网格",
    "洪湖龙口网格": "洪湖大沙网格",
    "洪湖燕窝网格": "洪湖大沙网格",
    # 滨湖网格 = 汊河 + 黄家口 + 螺山 + 小港 + 滨湖（表格第 29 行）
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


# 明细「裁决后过滤」单次最多扫描的行数。2026-10 装机 2.2 万、投诉 3.3 千，均在上限内。
# 超出时只取前 MAX_SCAN 行（按受理时间倒序），返回 truncated=True 让前端提示。
MAX_SCAN = 30000
DONE_ST_ALL = ("已施工", "已归档", "已完工", "已回单", "已竣工")

# ---------------------------------------------------------------- 跨源状态裁决
# 与装维端 mp.py 同一套规则（status_resolver 唯一真源），保证桌面端与手机端
# 看到的是**同一个状态**。批量 in_() 查询，禁止逐行查。

def _adh_install(db, rows):
    """给一批 InstallOrder 做裁决，原地挂 _st_status / _st_src / _st_note / _st_conflict"""
    if not rows:
        return rows
    nos = [r.order_id for r in rows]
    rep, rt = {}, {}
    try:
        from ..models import ReportWideOrder
        rep = {x[0]: (x[1] or "") for x in db.query(ReportWideOrder.order_no, ReportWideOrder.status)
               .filter(ReportWideOrder.order_no.in_(nos)).all()}
    except Exception:
        pass
    try:
        from ..models import InstallRealtimeStatus as _IRT
        rt = {x[0]: (x[1] or "", x[2] or "") for x in
              db.query(_IRT.order_id, _IRT.status, _IRT.dispatch_time)
              .filter(_IRT.order_id.in_(nos)).all()}
    except Exception:
        pass
    for r in rows:
        cands = [wide_cand(r.status, r.is_refund, r.refund_reason)]
        if rep.get(r.order_id):
            cands.append(report_cand(rep[r.order_id]))
        if rt.get(r.order_id):
            cands.append(realtime_cand(rt[r.order_id][0], rt[r.order_id][1]))
        st = resolve(cands)
        r._st_status = st["status"]
        r._st_src = st.get("src_name", "")
        r._st_conflict = bool(st.get("conflict"))
        note = st.get("reason", "")
        if st.get("extra"):
            note = (note + "；" if note else "") + st["extra"]
        r._st_note = note
    return rows


def _adh_complaint(db, rows):
    """给一批 ComplaintOrder 做裁决（投诉域，来源名是「投诉综合查询」）"""
    if not rows:
        return rows
    nos = [r.eoms_no for r in rows]
    rep, rt = {}, {}
    try:
        from ..models import ReportWideOrder
        rep = {x[0]: (x[1] or "") for x in db.query(ReportWideOrder.order_no, ReportWideOrder.status)
               .filter(ReportWideOrder.order_no.in_(nos)).all()}
    except Exception:
        pass
    try:
        from ..models import ComplaintRealtimeStatus as _CRT
        rt = {x[0]: (x[1] or "", x[2] or "") for x in
              db.query(_CRT.eoms_no, _CRT.status, _CRT.dispatch_time)
              .filter(_CRT.eoms_no.in_(nos)).all()}
    except Exception:
        pass
    for r in rows:
        cands = [wide_cand(r.status)]
        if rep.get(r.eoms_no):
            cands.append(report_cand(rep[r.eoms_no]))
        if rt.get(r.eoms_no):
            cands.append(complaint_realtime_cand(rt[r.eoms_no][0], rt[r.eoms_no][1]))
        st = resolve(cands, domain="complaint")
        r._st_status = st["status"]
        r._st_src = st.get("src_name", "")
        r._st_conflict = bool(st.get("conflict"))
        note = st.get("reason", "")
        if st.get("extra"):
            note = (note + "；" if note else "") + st["extra"]
        r._st_note = note
    return rows


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
    # v1.6.7：先做跨源裁决，下面所有口径都用裁决后的状态 —— 与装维端同一口径。
    # 只改明细不改汇总会制造新的「卡片/明细不一致」，那是 v1.6.6 踩过的坑。
    _adh_install(db, installs)
    total = len(installs)
    status_of = lambda r: getattr(r, "_st_status", None) or (r.status or "")
    done = sum(1 for r in installs if status_of(r) == "已施工")
    # 撤单/退单拆分（按 status 关键字，is_refund="是" 兜底）
    cancel = sum(1 for r in installs if "撤" in status_of(r))
    refund_only = sum(1 for r in installs if "退" in status_of(r) and "撤" not in status_of(r))
    if cancel + refund_only == 0:
        refund_total = sum(1 for r in installs if r.is_refund == "是")
        cancel = refund_total  # 兜底：若 status 没细分，全部算撤单
    refund = cancel + refund_only  # 退撤单合计
    # 处理中（v1.6.7）：改用 is_pending() 统一判定，与装维端完全同口径。
    #
    # 旧写法 `status != 已施工` 会把**状态为空**的行算成处理中。实测 2412 条
    # 空状态单：有区县但无处理人、无当前环节，受理都是 10-01，月底仍未完工——
    # 这是宽表导出的残缺行，不是真实在途单。旧口径让 pending 虚高 2412。
    # 现在空串不计待办，延伸/光猫仍排除（加装类挂靠单）。
    pending = sum(1 for r in installs
                  if is_pending(status_of(r))
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
    _adh_complaint(db, complaints)
    c_total = len(complaints)
    # v1.6.7：「是否维修完成/故障是否解决」列在本库全表为空（3380 行无一非空），
    # 原写法恒为 0 → 解决率常年 0.0、待办等于全部。改为「列优先、状态兜底」：
    # 状态族属 done（已施工/已归档/已完工/已回单）即视为已解决。
    def _solved(r) -> bool:
        v = (getattr(r, "is_solved", "") or "").strip()
        if v:
            return v in ("已解决", "已维修", "已处理", "是")
        return classify(getattr(r, "_st_status", "") or r.status or "") == "done"

    solved = sum(1 for r in complaints if _solved(r))
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

    # 裁决后状态与宽表原始状态差异有多大，让用的人心里有数
    # 对照用：如果**不做裁决**、只看宽表原始状态，pending 会是多少（同一 is_pending 判定）
    _raw_pending = sum(1 for r in installs
                       if is_pending(r.status)
                       and "延伸服务" not in (r.order_theme or "")
                       and "更换光猫" not in (r.order_theme or ""))
    _adj_diff = abs(pending - _raw_pending)

    return {
        "has_data": True, "month": month,
        "status_source": "跨源裁决（装维端与桌面端同口径）",
        "status_adj": {"pending_raw": _raw_pending, "pending": pending, "changed": _adj_diff},
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

# ================================================================
# v1.7.0 竣工率多维分析（按标准7网格，对齐《工单多维分析》表格）
# ================================================================

# 21 个维度的定义表。前端卡片墙、明细表列、图例全部由这里驱动，
# 新增维度只改这张表，不用动前端。
#   kind: count = 计数型（分子=该维度命中数，分母另有说明）
#         rate  = 比率型（分子分母都在 num/den 里给）
#   bucket: 该维度在明细里怎么筛（对应 _pick 的 metric 参数）
_MULTIDIM_COLS = [
    # key         标题        类型    说明（悬浮提示用）
    ("come",      "来单",      "count", "受理时间非空的工单记录"),
    ("done",      "已施工",    "count", "定单状态=已施工（裁决后）"),
    ("cancel",    "已撤单",    "count", "定单状态=已撤单"),
    ("refund",    "已退单",    "count", "定单状态=已退单"),
    ("pending",   "处理中",    "count", "待地址确认/待施工/待预约/待PBOSS审核等"),
    ("nostate",   "无状态",    "count", "定单状态、当前环节、业务主题均为空"),
    ("h8",        "8h及时",    "count", "已施工单内，8小时及时=是"),
    ("h24",       "24h及时",   "count", "已施工单内，24小时及时=是"),
    ("perform",   "履约新",    "count", "已施工单内，是否履约(新)=是"),
    ("valid",     "有效可施工", "count", "来单 - 撤单 - 无状态"),
    ("rate_nom",  "名义竣工率", "rate",  "已施工 / 来单总量"),
    ("rate_eff",  "有效竣工率", "rate",  "已施工 / 有效可施工（剔除撤单与无状态）"),
    ("rate_pend", "压单率",    "rate",  "处理中 / 有效可施工"),
    ("rate_re",   "退撤单率",  "rate",  "(退单+撤单) / 来单总量"),
    ("rate_can",  "撤单率",    "rate",  "撤单 / 来单总量"),
    ("rate_clear","消单率",    "rate",  "(已施工+撤单+退单) / 来单总量"),
    ("rate_8h",   "8h及时率",  "rate",  "8h及时 / 已施工"),
    ("rate_24h",  "24h及时率", "rate",  "24h及时 / 已施工"),
    ("rate_perf", "履约率",    "rate",  "履约(新) / 已施工"),
]


def _pct(a, b):
    """安全百分比：分母为 0 返回 0.0，保留 1 位（与表格一致 67.9 而非 67.91）"""
    if not b:
        return 0.0
    return round(a / b * 100, 1)


def _is_nostate(r):
    """无状态：状态、当前环节、业务主题三者皆空（与表格「说明」第 19 条一致）"""
    return (not (r.status or "").strip()
            and not (r.current_step or "").strip()
            and not (r.order_theme or "").strip())


def _status_of(r):
    return getattr(r, "_st_status", None) or (r.status or "")


def _pick(rows, metric):
    """**唯一**筛选入口：卡片计数与明细列表都调它。

    metric 取值见 _MULTIDIM_COLS 的 key；也接受 "all"（全部来单）。
    """
    if metric in (None, "", "all", "come"):
        return list(rows)
    if metric == "done":
        return [r for r in rows if _status_of(r) == "已施工"]
    if metric == "cancel":
        return [r for r in rows if "撤" in _status_of(r)]
    if metric == "refund":
        return [r for r in rows if "退" in _status_of(r) and "撤" not in _status_of(r)]
    if metric == "pending":
        return [r for r in rows if is_pending(_status_of(r))]
    if metric == "nostate":
        return [r for r in rows if _is_nostate(r)]
    if metric == "h8":
        return [r for r in rows if _status_of(r) == "已施工" and (r.is_8h or "") == "是"]
    if metric == "h24":
        return [r for r in rows if _status_of(r) == "已施工" and (r.is_24h or "") == "是"]
    if metric == "perform":
        return [r for r in rows if _status_of(r) == "已施工" and (r.is_perform or "") == "是"]
    if metric == "valid":
        # 有效可施工：排除撤单与无状态。
        # v1.7.0：已施工单一律保留 —— 与 _agg() 里 valid=max(n-cancel-nostate, done)
        # 兜底保持**同一判定**。否则「未知」组会出现卡片 3 / 明细 2（v1.6.6 的老坑）。
        return [r for r in rows
                if _status_of(r) == "已施工"
                or ("撤" not in _status_of(r) and not _is_nostate(r))]
    # 比率型指标没有独立明细，给分子那一侧（前端点率类卡片时跳到分子单）
    if metric in ("rate_nom", "rate_eff"):
        return [r for r in rows if _status_of(r) == "已施工"]
    if metric == "rate_pend":
        return [r for r in rows if is_pending(_status_of(r))]
    if metric == "rate_re":
        return [r for r in rows if ("退" in _status_of(r) or "撤" in _status_of(r))]
    if metric == "rate_can":
        return [r for r in rows if "撤" in _status_of(r)]
    if metric == "rate_clear":
        return [r for r in rows if ("已施工" == _status_of(r) or "退" in _status_of(r) or "撤" in _status_of(r))]
    if metric == "rate_8h":
        return [r for r in rows if _status_of(r) == "已施工" and (r.is_8h or "") == "是"]
    if metric == "rate_24h":
        return [r for r in rows if _status_of(r) == "已施工" and (r.is_24h or "") == "是"]
    if metric == "rate_perf":
        return [r for r in rows if _status_of(r) == "已施工" and (r.is_perform or "") == "是"]
    return list(rows)


def _grid_rows(db, month, district):
    """取某月某区县的装机单（裁决后），只在内存里做一次。带 TTL 缓存。"""
    key = ("multidim", month, district)
    now = _time.time()
    ver = _wide_data_version(db)
    with _WIDE_CACHE_LOCK:
        hit = _WIDE_CACHE.get(key)
        if hit and hit[0] == ver and hit[1] > now:
            return hit[2]
    q = db.query(InstallOrder).filter(InstallOrder.month == month)
    if district:
        q = q.filter(InstallOrder.district == district)
    rows = q.all()
    _adh_install(db, rows)      # 跨源裁决，与装维端同口径
    with _WIDE_CACHE_LOCK:
        _WIDE_CACHE[key] = (ver, _time.time() + _WIDE_CACHE_TTL, rows)
        if len(_WIDE_CACHE) > 64:
            for k in [k for k, v in _WIDE_CACHE.items() if v[1] <= _time.time()]:
                _WIDE_CACHE.pop(k, None)
    return rows


def _agg(rows):
    """把一个分组的行聚合成 21 个维度值（统计与明细共用 _pick）"""
    n = len(rows)
    done = len(_pick(rows, "done"))
    cancel = len(_pick(rows, "cancel"))
    refund = len(_pick(rows, "refund"))
    pending = len(_pick(rows, "pending"))
    nostate = len(_pick(rows, "nostate"))
    h8 = len(_pick(rows, "h8"))
    h24 = len(_pick(rows, "h24"))
    perform = len(_pick(rows, "perform"))
    # 有效可施工 = 来单 - 撤单 - 无状态。
    # v1.7.0 兜底：至少要 >= 已施工数。个别单 status/step/theme 全空被判「无状态」，
    # 但它本身是已施工（加装类挂靠单常见），否则会出现 done > valid、有效竣工率 >100%。
    valid = max(n - cancel - nostate, done)
    clear = done + cancel + refund        # 消单
    return {
        "come": n, "done": done, "cancel": cancel, "refund": refund,
        "pending": pending, "nostate": nostate, "h8": h8, "h24": h24,
        "perform": perform, "valid": valid,
        "rate_nom": _pct(done, n),
        "rate_eff": _pct(done, valid),
        "rate_pend": _pct(pending, valid),
        "rate_re": _pct(cancel + refund, n),
        "rate_can": _pct(cancel, n),
        "rate_clear": _pct(clear, n),
        "rate_8h": _pct(h8, done),
        "rate_24h": _pct(h24, done),
        "rate_perf": _pct(perform, done),
    }


class _MultiDimReq(BaseModel):
    month: str
    district: str = ""


@router.post("/completion/multidim")
def completion_multidim(req: _MultiDimReq,
                        user=Depends(get_current_user), db: Session = Depends(get_db)):
    """竣工率多维分析：按标准7网格输出 21 个维度（POST 规避中文 query 被 h11 拒绝）"""
    rows = _grid_rows(db, req.month, req.district)
    if not rows:
        return {"has_data": False, "month": req.month, "district": req.district,
                "cols": [], "grids": [], "total": {}}

    # 按标准网格分组。「未知」= 网格为空，单独列示（与表格一致）
    groups = {}
    unmapped_raw = {}
    for r in rows:
        g = (r.grid or "").strip()
        if not g:
            unmapped_raw["(空)"] = unmapped_raw.get("(空)", 0) + 1
            groups.setdefault("未知(未映射)", []).append(r)
            continue
        if r.district == "洪湖市" and g not in _HONGHU_GRID_MAP:
            # 映射表里没有的网格名 → 归「未知」，并记下来方便补映射
            unmapped_raw[g] = unmapped_raw.get(g, 0) + 1
            groups.setdefault("未知(未映射)", []).append(r)
        else:
            groups.setdefault(_std_grid(g, r.district), []).append(r)

    grids = []
    for g, rs in groups.items():
        item = {"grid": g, "is_unknown": g.startswith("未知")}
        item.update(_agg(rs))
        grids.append(item)
    # 7 标准网格按来单降序，未知固定排最后
    grids.sort(key=lambda x: (x["is_unknown"], -x["come"]))

    total = _agg(rows)
    # 网格 avg 参考值：用于卡片上「vs 全区」的比较
    total["grid_count"] = len([g for g in grids if not g["is_unknown"]])

    return {
        "has_data": True,
        "month": req.month,
        "district": req.district or "全部区县",
        "cols": [{"key": k, "label": lb, "type": t, "hint": h} for k, lb, t, h in _MULTIDIM_COLS],
        "grids": grids,
        "total": total,
        "unmapped": dict(sorted(unmapped_raw.items(), key=lambda x: -x[1])),
        "note": "口径：来单=受理非空；竣工=已施工；有效可施工=来单-撤单-无状态。状态取跨源裁决结果。",
    }


# ============================ 来单分析（v1.8.0） ============================
# 移植自 Streamlit「待装工单盯控」的 5 块功能，数据**全部**读装机宽表 install_orders
# （即「数据同步 → 装机宽表」维护的那张表），口径与竣工率多维分析完全一致。
#
# 设计要点：
#   * 只新增接口，不改动 multidim / drill 的既有行为
#   * 所有分组聚合调 _agg()，与 21 维度表同一套 _pick()，不会出现「卡片 N / 明细 0」
#   * 状态一律用 _status_of(r)（裁决后），不用 r.status（宽表原始值）

_INTAKE_UNASSIGNED = "(未分配)"
_INTAKE_NOSTATE = "(无状态)"

# 按单量分档的告警阈值（用户 2026-10-30 确认：≥5 高负荷 / 3~4 偏高 / ≤2 正常）
_INTAKE_WARN_HIGH = 5
_INTAKE_WARN_MID = 3


def _intake_handler(r) -> str:
    """取处理人。空处理人分两种，必须分开统计：
    - 无状态单（status/step/theme 全空）：压根没进流程，谈不上「分配给谁」
    - 其余空处理人：确实未指派，单独成行，否则各人加总对不上总数
    """
    h = (r.handler or "").strip()
    if h:
        return h
    return _INTAKE_NOSTATE if _is_nostate(r) else _INTAKE_UNASSIGNED


def _intake_is_pending(r, include_nostate: bool = False) -> bool:
    """是否算「待装」（未完工且未退撤）。

    口径与竣工率页「在途压单」（_pick(rows, "pending") → is_pending()）保持**完全一致**，
    两处卡片数字必须相等，否则同一页面两个数字打架。

    历史版本曾把「全字段空白」的无状态单也算进来，实测洪湖 10 月会凭空多出 2480 单压单
    （占全量 10.8%），与「在途压单 148」矛盾。现改为默认不算；这些单在「全量来单」
    口径里归入 (无状态) 独立分组展示，盯控不被遗漏。
    include_nostate 仅供排查用，默认 False。
    """
    st = _status_of(r)
    if is_pending(st):
        return True
    if include_nostate and _is_nostate(r) and not st:
        return True
    return False


def _intake_day(t: str) -> str:
    return (t or "")[:10]


class _IntakeReq(BaseModel):
    month: str
    district: str = ""
    scope: str = "all"          # all=全量来单 / pending=仅待装（Streamlit 原口径）
    handler: str = ""           # 可选：只看某个处理人（点人名时用）


@router.post("/completion/intake")
def completion_intake(req: _IntakeReq,
                      user=Depends(get_current_user), db: Session = Depends(get_db)):
    """来单分析：统计信息 / 按人图表 / 详细统计表 / 压单分组 / 网格压单 / 人员详情

    一次请求返回全部 5 块数据，前端只发一次，避免 46 个人各发一次请求。
    """
    rows = _grid_rows(db, req.month, req.district)

    # v1.8.1：柱状图 tooltip 要显示「压 N / 全月 M」，这里的 M 必须恒为全月来单量，
    # 不能跟着 scope 变 —— 否则「仅待装」口径下 M 等于待装量，读起来就是「全月来单 8 单」。
    total_by_handler = {}
    for _r in rows:
        _h = _intake_handler(_r)
        total_by_handler[_h] = total_by_handler.get(_h, 0) + 1

    scope = (req.scope or "all").strip()
    if scope == "pending":
        rows = [r for r in rows if _intake_is_pending(r)]

    if not rows:
        return {"has_data": False, "month": req.month,
                "district": req.district or "全部区县", "scope": scope}

    # ---------- ① 统计信息 ----------
    # v1.8.1：状态种类只数「待装」单。盯控工具要回答的是「还没做完的单卡在哪些环节」，
    # 混进已施工/已撤单/已退单三个终态会虚增（洪湖 2026-10：全量 11 种 vs 待装 7 种）。
    pend_rows = [r for r in rows if _intake_is_pending(r)]
    states = sorted({_status_of(r) or "(空)" for r in pend_rows})
    t = _agg(rows)
    info = {
        "records": len(rows),                                    # 生成记录数
        "refund_back": len(_pick(rows, "refund")),               # 退单打回
        "state_count": len(states),                              # 选择状态数
        "states": states,
        "days": sorted({_intake_day(r.accept_time) for r in rows if _intake_day(r.accept_time)}),
        "fttr_yes": t["come"] and len([r for r in rows if (r.is_fttr or "") == "是"]) or 0,
        "fttr_all": len([r for r in rows if (r.is_fttr or "") == "是"]) == len(rows),
        "total": t,
    }

    # ---------- 按处理人分组（② ③ ④ 共用） ----------
    by_handler = {}
    for r in rows:
        by_handler.setdefault(_intake_handler(r), []).append(r)

    today = _time.strftime("%Y-%m-%d")
    people = []
    for name, rs in by_handler.items():
        a = _agg(rs)
        # 所属网格：一人可能跨多网格（实测「万里」跨 4 个），取单量最多的为主网格
        gc = {}
        for r in rs:
            gg = _std_grid((r.grid or "").strip(), r.district) if (r.grid or "").strip() else "未知(未映射)"
            gc[gg] = gc.get(gg, 0) + 1
        gc_sorted = sorted(gc.items(), key=lambda x: -x[1])
        main_grid = gc_sorted[0][0] if gc_sorted else "未知(未映射)"
        # 今日受理 / 今日完工（按日期前缀，宽表无独立字段）
        today_accept = len([r for r in rs if _intake_day(r.accept_time) == today])
        today_done = len([r for r in rs if _intake_day(r.completion_time) == today])
        # 压单时长：最早受理时间距今天数（越大越久）
        acc_days = [_intake_day(r.accept_time) for r in rs if _intake_day(r.accept_time)]
        oldest = min(acc_days) if acc_days else ""
        pend = len([r for r in rs if _intake_is_pending(r)])
        lvl = ("高负荷" if pend >= _INTAKE_WARN_HIGH
               else "偏高" if pend >= _INTAKE_WARN_MID
               else "正常")
        people.append({
            "name": name,
            "is_unassigned": name in (_INTAKE_UNASSIGNED, _INTAKE_NOSTATE),
            "grid": main_grid,
            "grid_count": len(gc_sorted),
            "grid_extra": [{"grid": g, "n": n} for g, n in gc_sorted[1:4]],
            "today_accept": today_accept,
            "today_done": today_done,
            "today_rate": _pct(today_done, today_accept) if today_accept else None,
            "count": a["come"],          # 单量（该范围内）
            "pending": pend,
            "done": a["done"],
            "oldest_accept": oldest,
            "warn": lvl,
            **{k: a[k] for k in ("cancel", "refund", "rate_eff")},
        })
    # 未分配/无状态固定排最后，其余按单量降序
    people.sort(key=lambda x: (x["is_unassigned"], -x["count"], x["name"]))

    # ---------- ② 图表数据（按「待装」量，与 Streamlit 盯控图一致） ----------
    # v1.8.1：原来取 count（全月受理量），画出来是「谁这个月接单多」，不是「谁手上压着单」，
    # 与盯控目的相反（石力全月 93 单最显眼，实际只压 6 单）。
    # 现改为 pending；pending=0 的人不画 —— 一排空柱只会把真实压力压扁。
    # total 一并带上，tooltip 里可显示「压 N / 全月 M」。
    chart = [{"name": p["name"], "count": p["pending"], "grid": p["grid"],
              "total": total_by_handler.get(p["name"], p["count"])}
             for p in people if not p["is_unassigned"] and p["pending"] > 0]
    chart.sort(key=lambda x: (-x["count"], x["name"]))

    # ---------- ④ 按单量分组分布 ----------
    dist = {}
    for p in people:
        if p["is_unassigned"]:
            continue
        dist[p["count"]] = dist.get(p["count"], 0) + 1
    dist_rows = [{"count": k, "people": v, "orders": k * v}
                 for k, v in sorted(dist.items(), key=lambda x: -x[0])]

    # ---------- ④ 按网格压单 ----------
    by_grid = {}
    for r in rows:
        g = _std_grid((r.grid or "").strip(), r.district) if (r.grid or "").strip() else "未知(未映射)"
        by_grid.setdefault(g, []).append(r)
    grid_rows = []
    for g, rs in by_grid.items():
        a = _agg(rs)
        pend_rs = [r for r in rs if _intake_is_pending(r)]
        grid_rows.append({
            "grid": g,
            "is_unknown": g.startswith("未知"),
            "count": a["come"],
            "pending": len(pend_rs),
            "done": a["done"],
            "people": len({_intake_handler(r) for r in rs if not _intake_handler(r).startswith("(")}),
            "rate_eff": a["rate_eff"],
        })
    grid_rows.sort(key=lambda x: (x["is_unknown"], -x["pending"]))

    # ---------- ④ 人员工单详情（每个人的压单概况） ----------
    staff = [{
        "name": p["name"], "grid": p["grid"], "pending": p["pending"],
        "count": p["count"], "oldest_accept": p["oldest_accept"], "warn": p["warn"],
    } for p in people if not p["is_unassigned"]]

    pend_total = len([r for r in rows if _intake_is_pending(r)])
    # 待装里有多少挂在真人名下、多少是 (未分配)/(无状态)。不拆开的话，
    # 「待装 148 件、涉及 45 人」会让人以为这 45 个人扛了 148 单。
    pend_assigned = len([r for r in pend_rows
                         if _intake_handler(r) not in (_INTAKE_UNASSIGNED, _INTAKE_NOSTATE)])
    return {
        "has_data": True,
        "month": req.month,
        "district": req.district or "全部区县",
        "scope": scope,
        "today": today,
        "info": info,
        "chart": chart,
        "people": people,
        "dist": dist_rows,
        "grids": grid_rows,
        "staff": staff,
        "summary": {
            "orders": len(rows),
            "orders": len(rows),
            "pending": pend_total,
            "assigned_pending": pend_assigned,
            "unassigned_pending": pend_total - pend_assigned,
            "people": len(chart),
            "top": chart[0]["name"] if chart else "",
            "top_count": chart[0]["count"] if chart else 0,
        },
        "note": "数据源：装机宽表（数据同步 → 装机宽表）。状态取跨源裁决结果，与竣工率多维分析同口径。",
    }


class _DrillReq(BaseModel):
    month: str
    district: str = ""
    grid: str = ""          # 标准网格名；"未知(未映射)" 表示未映射
    metric: str = "come"
    keyword: str = ""
    page: int = 1
    page_size: int = 50
    # v1.8.0 来单分析点人名时用；默认空串 = 不加限制，原有调用行为完全不变
    handler: str = ""
    # v1.8.0 来单分析「仅待装」口径下钻取要跟着收窄，否则卡片 18 / 明细 1538。
    # 取值 all（默认，原行为不变）/ pending
    scope: str = "all"


@router.post("/completion/drill")
def completion_drill(req: _DrillReq,
                     user=Depends(get_current_user), db: Session = Depends(get_db)):
    """多维分析钻取明细。**与卡片数字共用 _pick**，不存在「卡片 N 单、明细 0 条」。"""
    rows = _grid_rows(db, req.month, req.district)

    # 先按网格圈定范围（与 multidim 完全相同的分组逻辑）
    if req.grid:
        if req.grid.startswith("未知"):
            rows = [r for r in rows
                    if not (r.grid or "").strip()
                    or (r.district == "洪湖市" and (r.grid or "").strip() not in _HONGHU_GRID_MAP)]
        else:
            rows = [r for r in rows if _std_grid((r.grid or "").strip(), r.district) == req.grid]

    rows = _pick(rows, req.metric)

    # v1.8.0：按处理人过滤（来单分析点人名弹抽屉）。
    # 判定复用 _intake_handler，保证「(未分配)」「(无状态)」两个虚拟组也能钻取。
    if (req.scope or "all").strip() == "pending":
        rows = [r for r in rows if _intake_is_pending(r)]
    if (req.handler or "").strip():
        rows = [r for r in rows if _intake_handler(r) == req.handler.strip()]

    kw = (req.keyword or "").strip()
    if kw:
        rows = [r for r in rows
                if kw in (r.order_id or "") or kw in (r.handler or "")
                or kw in (r.community_name or "") or kw in (r.service_number or "")]

    rows.sort(key=lambda r: (r.accept_time or ""), reverse=True)
    total = len(rows)
    ps = max(1, min(req.page_size, 200))
    st = max(0, (req.page - 1) * ps)
    page_rows = rows[st:st + ps]

    return {
        "total": total, "page": req.page, "page_size": ps,
        "rows": [{
            "no": r.order_id,
            "grid": _std_grid((r.grid or "").strip(), r.district) or "未知(未映射)",
            "grid_raw": r.grid or "",
            "district": r.district,
            "accept_time": (r.accept_time or "")[:16],
            "theme": r.order_theme or "",
            "status": _status_of(r),
            "status_raw": r.status or "",
            "status_src": getattr(r, "_st_src", "") or "",
            "status_note": getattr(r, "_st_note", "") or "",
            "status_conflict": bool(getattr(r, "_st_conflict", False)),
            "handler": r.handler or "",
            "community": r.community_name or "",
            "number": r.service_number or "",
            "completion_time": (r.completion_time or "")[:16],
            "is_8h": r.is_8h or "",
            "is_24h": r.is_24h or "",
            "is_perform": r.is_perform or "",
            "broadband_type": r.broadband_type or "",
            "current_step": r.current_step or "",
            # v1.8.0：来单分析个人详情需要的 3 列（原有调用方不读则忽略，无副作用）
            "is_fttr": r.is_fttr or "",
            "is_high_quality": r.is_high_quality or "",
            "urge_cnt": r.urge_cnt or 0,
            "appointment_time": (r.appointment_time or "")[:16],
            "district_name": r.district or "",
        } for r in page_rows],
    }


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
                hide_zhzw: bool = Query(False),
                pending_only: bool = Query(False, description="只列进行中（与看板 pending 卡同口径）"),
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
        # ---- 过滤下推到 SQL，与明细页签复选框一一对应 ----
        # 2026-10-26 修复：此前投诉分支完全没有 hide_* 过滤，5 个复选框在投诉页签全部失效
        # ---- 与状态无关的条件照旧下推 SQL（性能需要）----
        if hide_ext:       # 不显示延伸服务
            q = q.filter(~func.coalesce(ComplaintOrder.complaint_category, "").like("%延伸服务%"))
        if hide_modem:     # 不显示更换光猫
            q = q.filter(~func.coalesce(ComplaintOrder.complaint_category, "").like("%光猫%"))
        if hide_hkbz:      # 不显示 HB-KHBZ 单
            q = q.filter(~func.coalesce(ComplaintOrder.eoms_no, "").like("%KHBZ%"))
        if hide_zhzw:      # 不显示 HB-ZHZW 单（政企支撑类，前缀匹配）
            q = q.filter(~func.coalesce(ComplaintOrder.eoms_no, "").like("HB-ZHZW%"))

        # ---- 状态相关条件必须等裁决后再判（v1.6.7）----
        # 原因见上方 docstring：SQL 按原始状态筛、显示按裁决状态，两者不同步就会漏。
        objs = q.order_by(ComplaintOrder.accept_time.desc(),
                          ComplaintOrder.eoms_no.desc()).limit(MAX_SCAN).all()
        _adh_complaint(db, objs)
        if hide_done:      # 不显示已完成（按**裁决后**状态）
            objs = [r for r in objs if (r._st_status or "") not in DONE_ST_ALL]
        if hide_refund:    # 不显示已退/撤单（按**裁决后**状态）
            objs = [r for r in objs
                    if "撤" not in (r._st_status or "") and "退" not in (r._st_status or "")]
        if pending_only:   # 只列进行中：与 summary.pending 同一个判定
            objs = [r for r in objs if is_pending(r._st_status)]
        total = len(objs)
        rows = objs[(page - 1) * page_size:(page - 1) * page_size + page_size]
        return {"rows": [{
            "no": r.eoms_no, "district": r.district, "accept_time": r.accept_time,
            "status": r._st_status or r.status, "status_raw": r.status,
            "status_src": r._st_src, "status_note": r._st_note,
            "status_conflict": r._st_conflict,
            "handler": r.handler, "category": r.complaint_category,
            "is_solved": r.is_solved, "duration": r.order_duration,
            "appointment_time": r.appointment_time, "grid": r.grid,
            "is_high_quality": r.is_high_quality, "global_pass": r.global_pass,
            # 是否触发H5：口径见 h5_rule.py（HB-058 前缀 + 分类首级=家庭市场）
            **_h5j(r.eoms_no, r.complaint_category)} for r in rows],
            "total": total, "page": page, "page_size": page_size,
            "truncated": total >= MAX_SCAN}

    q = db.query(InstallOrder).filter(InstallOrder.month == month)
    if district:
        q = q.filter(InstallOrder.district == district)
    # ---- 与状态无关的条件照旧下推 SQL（性能需要）----
    if hide_ext:       # 不显示延伸服务
        q = q.filter(~func.coalesce(InstallOrder.order_theme, "").like("%延伸服务%"))
    if hide_modem:     # 不显示更换光猫
        q = q.filter(~func.coalesce(InstallOrder.order_theme, "").like("%更换光猫%"))
    if hide_hkbz:      # 不显示 HB-KHBZ 单（定单编码含 KHBZ）
        q = q.filter(~func.coalesce(InstallOrder.order_id, "").like("%KHBZ%"))

    # ---- 状态相关条件必须等裁决后再判（v1.6.7）----
    # 实测：SQL 按宽表原始状态筛「未完工」，而实时表已判成撤单/已施工的单会漏出来，
    # 待办口径明细 3938 vs 卡片 1150。详见函数 docstring。
    objs = q.order_by(InstallOrder.accept_time.desc(),
                      InstallOrder.order_id.desc()).limit(MAX_SCAN).all()
    _adh_install(db, objs)
    if hide_done:      # 不显示「已施工」（按**裁决后**状态）
        objs = [r for r in objs if (r._st_status or "") not in DONE_ST_ALL]
    if hide_refund:    # 不显示已退/撤单（按**裁决后**状态）
        objs = [r for r in objs
                if "撤" not in (r._st_status or "") and "退" not in (r._st_status or "")]
    if pending_only:   # 只列进行中：与 summary.pending 同一个判定
        objs = [r for r in objs if is_pending(r._st_status)]
    total = len(objs)
    rows = objs[(page - 1) * page_size:(page - 1) * page_size + page_size]
    return {"rows": [{
        "no": r.order_id, "district": r.district, "grid": r.grid,
        "accept_time": r.accept_time, "theme": r.order_theme,
        "status": r._st_status or r.status, "status_raw": r.status,
        "status_src": r._st_src, "status_note": r._st_note,
        "status_conflict": r._st_conflict,
        "handler": r.handler, "completion_time": r.completion_time,
        "is_refund": r.is_refund, "broadband_type": r.broadband_type,
        "appointment_time": r.appointment_time,
        "is_high_quality": "是" if r.is_high_quality else "否",
        "is_fttr": r.is_fttr or "否",
        "current_step": r.current_step or r.status} for r in rows],
        "total": total, "page": page, "page_size": page_size,
        "truncated": total >= MAX_SCAN}


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
