"""工单管理（手册 7.3）"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import WorkOrder, District, Alert, AuditLog
from ..deps import get_current_user, require_admin, scope_district_ids
from ..config import COMPLETED_STATUS, PENDING_STATUS

router = APIRouter(prefix="/work-orders", tags=["work-orders"])


@router.get("")
def list_orders(page: int = 1, page_size: int = 20, status: str = None, district_id: int = None,
                month: str = None, keyword: str = None, broadband_type: str = None,
                user=Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(WorkOrder)
    ids = scope_district_ids(user, db)
    if ids is not None:
        q = q.filter(WorkOrder.district_id.in_(ids))
    if status:
        q = q.filter(WorkOrder.status == status)
    if broadband_type:
        q = q.filter(WorkOrder.broadband_type == broadband_type)
    if district_id:
        q = q.filter(WorkOrder.district_id == district_id)
    if month:
        q = q.filter(WorkOrder.month == month)
    if keyword:
        kw = f"%{keyword}%"
        q = q.filter(or_(WorkOrder.order_id.like(kw), WorkOrder.construction_dept.like(kw),
                         WorkOrder.order_theme.like(kw)))
    total = q.count()
    rows = q.order_by(WorkOrder.accept_time.desc()).offset((page - 1) * page_size).limit(page_size).all()
    districts = {d.id: d.name for d in db.query(District).all()}
    return {"total": total, "items": [{
        "id": o.id, "order_id": o.order_id, "order_theme": o.order_theme, "status": o.status,
        "accept_time": o.accept_time, "completion_time": o.completion_time,
        "district": districts.get(o.district_id, ""), "construction_dept": o.construction_dept,
        "broadband_type": o.broadband_type} for o in rows]}


@router.get("/stats")
def stats(month: str = None, user=Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(WorkOrder)
    ids = scope_district_ids(user, db)
    if ids is not None:
        q = q.filter(WorkOrder.district_id.in_(ids))
    if month:
        q = q.filter(WorkOrder.month == month)
    total = q.count()
    done = q.filter(WorkOrder.status.in_(COMPLETED_STATUS)).count()
    by_status = dict(q.with_entities(WorkOrder.status, func.count(WorkOrder.id)).group_by(WorkOrder.status).all())
    return {"total": total, "completed": done, "pending": total - done,
            "by_status": by_status,
            "completed_status_list": COMPLETED_STATUS, "pending_status_list": PENDING_STATUS}


@router.post("")
def create_order(body: dict, user=Depends(require_admin), db: Session = Depends(get_db)):
    if not body.get("order_id"):
        raise HTTPException(status_code=400, detail="order_id 必填")
    if db.query(WorkOrder).filter(WorkOrder.order_id == body["order_id"]).first():
        raise HTTPException(status_code=400, detail="工单编号已存在")
    o = WorkOrder(order_id=body["order_id"], order_theme=body.get("order_theme", ""),
                  status=body.get("status", "流程未启动"), accept_time=body.get("accept_time", ""),
                  completion_time=body.get("completion_time", ""),
                  district_id=body.get("district_id"), construction_dept=body.get("construction_dept", ""),
                  broadband_type=body.get("broadband_type", "个人"))
    db.add(o)
    db.add(AuditLog(username=user.username, action="创建工单", detail=o.order_id))
    db.commit()
    return {"msg": "创建成功", "id": o.id}


@router.put("/{order_id}")
def update_order(order_id: str, body: dict, user=Depends(require_admin), db: Session = Depends(get_db)):
    o = db.query(WorkOrder).filter(WorkOrder.order_id == order_id).first()
    if not o:
        raise HTTPException(status_code=404, detail="工单不存在")
    for f in ["order_theme", "status", "accept_time", "completion_time", "construction_dept", "broadband_type"]:
        if f in body:
            setattr(o, f, body[f])
    if "district_id" in body:
        o.district_id = body["district_id"]
    db.add(AuditLog(username=user.username, action="更新工单", detail=order_id))
    db.commit()
    return {"msg": "更新成功"}


@router.delete("/{order_id}")
def delete_order(order_id: str, user=Depends(require_admin), db: Session = Depends(get_db)):
    o = db.query(WorkOrder).filter(WorkOrder.order_id == order_id).first()
    if not o:
        raise HTTPException(status_code=404, detail="工单不存在")
    db.query(Alert).filter(Alert.order_no == order_id).delete()
    db.delete(o)
    db.add(AuditLog(username=user.username, action="删除工单", detail=order_id))
    db.commit()
    return {"msg": "已删除"}
