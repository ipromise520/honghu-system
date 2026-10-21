"""任务分派全流程（手册 7.9）：创建→派单→接单→提交→验收/驳回"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Task, TaskLog, User, District, AuditLog
from ..deps import get_current_user, require_admin, get_role

router = APIRouter(prefix="/tasks", tags=["tasks"])

STATUS_NAME = {"created": "待派单", "assigned": "已派单", "accepted": "进行中",
               "submitted": "待验收", "completed": "已验收", "rejected": "已驳回"}


def task_dto(t: Task, db: Session):
    creator = db.query(User).filter(User.id == t.creator_id).first()
    assignee = db.query(User).filter(User.id == t.assignee_id).first() if t.assignee_id else None
    district = db.query(District).filter(District.id == t.district_id).first() if t.district_id else None
    return {"id": t.id, "title": t.title, "content": t.content, "status": t.status,
            "status_name": STATUS_NAME.get(t.status, t.status), "priority": t.priority,
            "deadline": t.deadline, "created_at": t.created_at.strftime("%Y-%m-%d %H:%M"),
            "creator": creator.real_name if creator else "", "assignee_id": t.assignee_id,
            "assignee": assignee.real_name if assignee else "", "district": district.name if district else ""}


@router.get("")
def list_tasks(status: str = None, user=Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(Task)
    role = get_role(user)
    if role == "field_worker":
        q = q.filter(Task.assignee_id == user.id)
    elif role == "district_admin" and user.district_id:
        q = q.filter(Task.district_id == user.district_id)
    if status:
        q = q.filter(Task.status == status)
    return [task_dto(t, db) for t in q.order_by(Task.id.desc()).limit(200)]


@router.post("")
def create_task(body: dict, user=Depends(require_admin), db: Session = Depends(get_db)):
    t = Task(title=body["title"], content=body.get("content", ""), district_id=body.get("district_id"),
             creator_id=user.id, assignee_id=body.get("assignee_id"),
             status="assigned" if body.get("assignee_id") else "created",
             priority=body.get("priority", "normal"), deadline=body.get("deadline", ""))
    db.add(t)
    db.flush()
    db.add(TaskLog(task_id=t.id, user_id=user.id, action="创建任务"))
    db.add(AuditLog(username=user.username, action="创建任务", detail=t.title))
    db.commit()
    return {"msg": "创建成功", "id": t.id}


@router.post("/{task_id}/assign")
def assign(task_id: int, body: dict, user=Depends(require_admin), db: Session = Depends(get_db)):
    t = db.query(Task).filter(Task.id == task_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="任务不存在")
    assignee = db.query(User).filter(User.id == body.get("assignee_id")).first()
    if not assignee:
        raise HTTPException(status_code=400, detail="执行人不存在")
    t.assignee_id = assignee.id
    t.status = "assigned"
    db.add(TaskLog(task_id=t.id, user_id=user.id, action="派单", comment=f"派单给 {assignee.real_name}"))
    db.commit()
    return {"msg": f"已派单给 {assignee.real_name}"}


@router.get("/{task_id}")
def task_detail(task_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    t = db.query(Task).filter(Task.id == task_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="任务不存在")
    logs = db.query(TaskLog).filter(TaskLog.task_id == task_id).order_by(TaskLog.id).all()
    d = task_dto(t, db)
    d["logs"] = [{"action": l.action, "comment": l.comment,
                  "user": db.query(User).filter(User.id == l.user_id).first().real_name,
                  "time": l.created_at.strftime("%Y-%m-%d %H:%M")} for l in logs]
    return d


@router.post("/{task_id}/acceptance")
def acceptance(task_id: int, body: dict, user=Depends(require_admin), db: Session = Depends(get_db)):
    """验收：pass → completed；reject → rejected（手册 驳回全流程）"""
    t = db.query(Task).filter(Task.id == task_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="任务不存在")
    if t.status != "submitted":
        raise HTTPException(status_code=400, detail="任务不在待验收状态")
    result = body.get("result", "pass")
    t.status = "completed" if result == "pass" else "rejected"
    db.add(TaskLog(task_id=t.id, user_id=user.id,
                   action="验收通过" if result == "pass" else "验收驳回",
                   comment=body.get("comment", "")))
    db.commit()
    return {"msg": "验收通过" if result == "pass" else "已驳回"}


# ---------- 装维端（my） ----------

@router.get("/my/list")
def my_tasks(status: str = None, user=Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(Task).filter(Task.assignee_id == user.id)
    if status:
        q = q.filter(Task.status == status)
    return [task_dto(t, db) for t in q.order_by(Task.id.desc()).limit(100)]


@router.post("/my/{task_id}/accept")
def my_accept(task_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    t = db.query(Task).filter(Task.id == task_id, Task.assignee_id == user.id).first()
    if not t:
        raise HTTPException(status_code=404, detail="任务不存在")
    if t.status != "assigned":
        raise HTTPException(status_code=400, detail="任务当前不可接单")
    t.status = "accepted"
    db.add(TaskLog(task_id=t.id, user_id=user.id, action="接单"))
    db.commit()
    return {"msg": "接单成功，开始执行"}


@router.post("/my/{task_id}/submit")
def my_submit(task_id: int, body: dict, user=Depends(get_current_user), db: Session = Depends(get_db)):
    t = db.query(Task).filter(Task.id == task_id, Task.assignee_id == user.id).first()
    if not t:
        raise HTTPException(status_code=404, detail="任务不存在")
    if t.status not in ("assigned", "accepted", "rejected"):
        raise HTTPException(status_code=400, detail="任务当前不可提交")
    t.status = "submitted"
    db.add(TaskLog(task_id=t.id, user_id=user.id, action="提交验收", comment=body.get("comment", "")))
    db.commit()
    return {"msg": "已提交验收"}


@router.post("/my/{task_id}/feedbacks")
def my_feedback(task_id: int, body: dict, user=Depends(get_current_user), db: Session = Depends(get_db)):
    db.add(TaskLog(task_id=task_id, user_id=user.id, action="进度反馈", comment=body.get("comment", "")))
    db.commit()
    return {"msg": "反馈已提交"}
