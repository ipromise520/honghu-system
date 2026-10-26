"""首页看板（手册 7.4）：概览卡片 / 区县排名 / 近7天趋势 / 预警处理"""
import json
from collections import defaultdict
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import COMPLETED_STATUS
from ..database import get_db
from ..models import (WorkOrder, InstallOrder, ExtensionOrder, Task, Alert, H5Complaint, H5Receipt,
                      District, User, AiVisitIssue, AiVisitStat)
from ..deps import get_current_user, scope_district_ids

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _district_filter_ids(user, db):
    return scope_district_ids(user, db)


def current_month():
    return datetime.now().strftime("%Y-%m")


# v1.3.7 数据源开关：1=用装机宽表 InstallOrder（推荐，与宽表/排名/趋势同源）
#                  0=回退旧表 WorkOrder（改动前的行为），改这一个数字即可还原
OVERVIEW_USE_INSTALL = 1


def _district_names(ids, db):
    """区县管理员是 district_id 列表，装机宽表存的是区县名称，这里做映射"""
    if ids is None:
        return None
    if not ids:
        return []
    return [d.name for d in db.query(District).filter(District.id.in_(ids)).all()]


@router.get("/overview")
def overview(user=Depends(get_current_user), db: Session = Depends(get_db)):
    month = current_month()
    ids = _district_filter_ids(user, db)
    if OVERVIEW_USE_INSTALL:
        # ---- 与 /metrics/wide/summary 完全同源，消除 1505 / 1728 并存 ----
        q = db.query(InstallOrder).filter(InstallOrder.month == month)
        names = _district_names(ids, db)
        if names is not None:
            q = q.filter(InstallOrder.district.in_(names)) if names else q.filter(False)
        total = q.count()
        done = q.filter(InstallOrder.status.in_(COMPLETED_STATUS)).count()
        # 装机宽表 broadband_type 目前全为空（导出列未填），业务主题均为家宽类，
        # 按业务确认口径：空值视为「个人」，与旧表 WorkOrder（全为个人）对齐
        is_personal = func.coalesce(InstallOrder.broadband_type, "").in_(("个人", ""))
        personal_total = q.filter(is_personal).count()
        personal_done = q.filter(is_personal,
                                 InstallOrder.status.in_(COMPLETED_STATUS)).count()
    else:
        q = db.query(WorkOrder).filter(WorkOrder.month == month)
        if ids is not None:
            q = q.filter(WorkOrder.district_id.in_(ids))
        total = q.count()
        done = q.filter(WorkOrder.status.in_(COMPLETED_STATUS)).count()
        personal_total = q.filter(WorkOrder.broadband_type == "个人").count()
        personal_done = q.filter(WorkOrder.broadband_type == "个人", WorkOrder.status.in_(COMPLETED_STATUS)).count()
    ext_q = db.query(func.sum(ExtensionOrder.income)).filter(ExtensionOrder.month == month)
    ext_income = ext_q.scalar() or 0
    task_q = db.query(Task).filter(Task.status.in_(["assigned", "accepted", "submitted"]))
    if ids is not None:
        task_q = task_q.filter(Task.district_id.in_(ids))
    open_tasks = task_q.count()
    h5_pending = _h5_pending_count(db, month)
    return {
        "month": month,
        "cards": [
            {"key": "accepted", "label": "本月受理工单", "value": total, "unit": "单"},
            {"key": "completed", "label": "本月已施工", "value": done, "unit": "单"},
            {"key": "completion_rate", "label": "竣工率(个人)", "value": round(personal_done / personal_total * 100, 2) if personal_total else 0, "unit": "%"},
            {"key": "ext_income", "label": "延伸服务收入", "value": round(ext_income, 1), "unit": "元"},
            {"key": "open_tasks", "label": "进行中任务", "value": open_tasks, "unit": "个"},
            {"key": "h5_pending", "label": "H5 待收", "value": h5_pending, "unit": "单"},
        ],
    }


def _h5_pending_count(db, window: str) -> int:
    """H5 未收 = 应发 - 已参评（装机 + 投诉，口径见 h5.py）。

    NOT EXISTS 子查询下推 SQL，避免把全部应发单号拉进内存做集合差。
    """
    install_pending = db.query(func.count(WorkOrder.id)).filter(
        WorkOrder.month == window,
        WorkOrder.status.in_(COMPLETED_STATUS),
        WorkOrder.broadband_type == "个人",
        WorkOrder.order_id.like("27%"),
        ~db.query(H5Receipt.id).filter(
            H5Receipt.order_no == WorkOrder.order_id,
            H5Receipt.h5_received == "已参评").exists(),
    ).scalar()
    complaint_pending = db.query(func.count(H5Complaint.id)).filter(
        H5Complaint.month == window,
        H5Complaint.order_status == "已施工",
        H5Complaint.eoms_order_no.like("HB-058%"),
        ~db.query(H5Receipt.id).filter(
            H5Receipt.order_no == H5Complaint.eoms_order_no,
            H5Receipt.h5_received == "已参评").exists(),
    ).scalar()
    return (install_pending or 0) + (complaint_pending or 0)


@router.get("/district-ranking")
def district_ranking(user=Depends(get_current_user), month: str = Query(None),
                     db: Session = Depends(get_db)):
    """首页区县排名（按竣工率简化版）"""
    month = month or current_month()
    ids = _district_filter_ids(user, db)
    if OVERVIEW_USE_INSTALL:
        # 与 overview 同源：装机宽表按 district 名称分组
        q = db.query(InstallOrder).filter(InstallOrder.month == month)
        names = _district_names(ids, db)
        if names is not None:
            q = q.filter(InstallOrder.district.in_(names)) if names else q.filter(False)
        rows = q.with_entities(InstallOrder.district, InstallOrder.status,
                               func.count(InstallOrder.order_id)).group_by(
            InstallOrder.district, InstallOrder.status).all()
        agg = defaultdict(lambda: {"total": 0, "done": 0})
        for dname, status, cnt in rows:
            agg[dname]["total"] += cnt
            if status in COMPLETED_STATUS:
                agg[dname]["done"] += cnt
        out = [{"district": dname or "未知", "total": v["total"], "completed": v["done"],
                "rate": round(v["done"] / v["total"] * 100, 2) if v["total"] else 0}
               for dname, v in agg.items()]
    else:
        q = db.query(WorkOrder).filter(WorkOrder.month == month,
                                       WorkOrder.district_id.isnot(None))
        if ids is not None:
            q = q.filter(WorkOrder.district_id.in_(ids))
        rows = q.with_entities(WorkOrder.district_id, WorkOrder.status, func.count(WorkOrder.id)).group_by(
            WorkOrder.district_id, WorkOrder.status).all()
        agg = defaultdict(lambda: {"total": 0, "done": 0})
        for did, status, cnt in rows:
            agg[did]["total"] += cnt
            if status in COMPLETED_STATUS:
                agg[did]["done"] += cnt
        districts = {d.id: d.name for d in db.query(District).all()}
        out = [{"district": districts.get(did, "未知"), "total": v["total"], "completed": v["done"],
                "rate": round(v["done"] / v["total"] * 100, 2) if v["total"] else 0}
               for did, v in agg.items()]
    out.sort(key=lambda x: -x["rate"])
    for i, r in enumerate(out, 1):
        r["rank"] = i
    return out


@router.get("/trend")
def trend(days: int = 7, user=Depends(get_current_user), db: Session = Depends(get_db)):
    start = (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    # accept_time 为 'YYYY-MM-DD HH:MM:SS' 定长格式，直接字符串比较等价于按天截断比较
    ids = _district_filter_ids(user, db)
    if OVERVIEW_USE_INSTALL:
        q = db.query(InstallOrder).filter(InstallOrder.accept_time >= start)
        names = _district_names(ids, db)
        if names is not None:
            q = q.filter(InstallOrder.district.in_(names)) if names else q.filter(False)
        rows = q.with_entities(func.substr(InstallOrder.accept_time, 1, 10).label("day"),
                               InstallOrder.status, func.count(InstallOrder.order_id)).group_by(
            "day", InstallOrder.status).all()
    else:
        q = db.query(WorkOrder).filter(WorkOrder.accept_time >= start)
        if ids is not None:
            q = q.filter(WorkOrder.district_id.in_(ids))
        rows = q.with_entities(func.substr(WorkOrder.accept_time, 1, 10).label("day"),
                               WorkOrder.status, func.count(WorkOrder.id)).group_by(
            "day", WorkOrder.status).all()
    agg = defaultdict(lambda: {"accepted": 0, "completed": 0})
    for day, status, cnt in rows:
        agg[day]["accepted"] += cnt
        if status in COMPLETED_STATUS:
            agg[day]["completed"] += cnt
    return [{"day": d, **v} for d, v in sorted(agg.items())]


@router.get("/alerts")
def alerts(is_resolved: bool | None = None, user=Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(Alert)
    if is_resolved is not None:
        q = q.filter(Alert.is_resolved == is_resolved)
    return [{"id": a.id, "type": a.type, "order_no": a.order_no, "content": a.content,
             "level": a.level, "is_resolved": a.is_resolved,
             "created_at": a.created_at.strftime("%Y-%m-%d %H:%M:%S")} for a in q.order_by(Alert.id.desc()).limit(50)]


@router.get("/ai-visit-issues")
def ai_visit_issues(month: str = Query(None), district: str = Query(None),
                    user=Depends(get_current_user), db: Session = Depends(get_db)):
    """AI装维即访-不规范 人员通报：责任人 | 不规范项 | 工单号 | 回单时间（按责任人排序）"""
    month = month or current_month()
    q = db.query(AiVisitIssue).filter(AiVisitIssue.month == month)
    if district:
        q = q.filter(AiVisitIssue.district == district)
    rows = q.all()
    rows.sort(key=lambda r: (r.handler or "", r.completion_time or ""))
    return {"month": month, "total": len(rows),
            "rows": [{"kind": r.kind, "district": r.district, "handler": r.handler, "item": r.item,
                      "order_no": r.order_no, "completion_time": r.completion_time} for r in rows]}


# 区县固定顺序（与通报表一致：9 区县 + 荆州合计）
_AI_DISTRICT_ORDER = ["公安县", "洪湖市", "监利市", "江陵县", "荆州开发区",
                      "荆州区", "沙市区", "石首市", "松滋市", "荆州"]


@router.get("/ai-visit-rates")
def ai_visit_rates(month: str = Query(None),
                   user=Depends(get_current_user), db: Session = Depends(get_db)):
    """AI装维即访 规范率通报：区县 × 类型(装机/投诉) × 周期(当日/当月)，
    含 外呼量/接通量/有问题工单量/问题单占比/规范率 及各勾选项(调研量/不是/占比)。"""
    month = month or current_month()
    rows = db.query(AiVisitStat).filter(AiVisitStat.month == month).all()
    order = {d: i for i, d in enumerate(_AI_DISTRICT_ORDER)}
    out = [{"period": r.period, "kind": r.kind, "district": r.district,
            "outbound": r.outbound, "connected": r.connected, "problem": r.problem,
            "problem_rate": r.problem_rate, "rate": r.rate,
            "metrics": json.loads(r.metrics) if r.metrics else []} for r in rows]
    out.sort(key=lambda x: (x["kind"] != "装机", x["period"] != "当日", order.get(x["district"], 99)))
    return {"month": month, "date": rows[0].date if rows else "", "has_data": bool(out), "rows": out}


@router.post("/alerts/{alert_id}/resolve")
def resolve_alert(alert_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    a = db.query(Alert).filter(Alert.id == alert_id).first()
    if not a:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="预警不存在")
    a.is_resolved = True
    db.commit()
    return {"msg": "已处理"}
