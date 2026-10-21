"""通知签收（手册 7.10）：创建/发布/撤回/签收/逾期统计"""
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Notice, NoticeSignature, User, District, AuditLog
from ..deps import get_current_user, require_admin

router = APIRouter(prefix="/notices", tags=["notices"])

SIG_DEADLINE_DAYS = 3  # 签收期限：发布后 3 天


@router.get("")
def list_notices(user=Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(Notice).order_by(Notice.id.desc()).limit(100).all()
    out = []
    for n in rows:
        total = db.query(NoticeSignature).filter(NoticeSignature.notice_id == n.id).count()
        publisher = db.query(User).filter(User.id == n.publisher_id).first()
        out.append({"id": n.id, "title": n.title, "category": n.category, "status": n.status,
                    "require_sign": n.require_sign, "publisher": publisher.real_name if publisher else "",
                    "publish_time": n.publish_time.strftime("%Y-%m-%d %H:%M") if n.publish_time else "",
                    "signed_count": total, "deadline": _deadline(n)})
    return out


def _deadline(n: Notice):
    if n.publish_time and n.require_sign:
        return (n.publish_time + timedelta(days=SIG_DEADLINE_DAYS)).strftime("%Y-%m-%d")
    return ""


@router.post("")
def create_notice(body: dict, user=Depends(require_admin), db: Session = Depends(get_db)):
    n = Notice(title=body["title"], content=body.get("content", ""), category=body.get("category", "综合"),
               require_sign=body.get("require_sign", True), publisher_id=user.id, status="draft")
    db.add(n)
    db.add(AuditLog(username=user.username, action="创建通知", detail=n.title))
    db.commit()
    return {"msg": "通知已创建（草稿）", "id": n.id}


@router.post("/{notice_id}/publish")
def publish(notice_id: int, user=Depends(require_admin), db: Session = Depends(get_db)):
    n = db.query(Notice).filter(Notice.id == notice_id).first()
    if not n:
        raise HTTPException(status_code=404, detail="通知不存在")
    n.status = "published"
    n.publish_time = datetime.now()
    db.commit()
    return {"msg": "已发布"}


@router.post("/{notice_id}/recall")
def recall(notice_id: int, user=Depends(require_admin), db: Session = Depends(get_db)):
    n = db.query(Notice).filter(Notice.id == notice_id).first()
    if not n:
        raise HTTPException(status_code=404, detail="通知不存在")
    n.status = "recalled"
    db.commit()
    return {"msg": "已撤回"}


@router.get("/{notice_id}")
def notice_detail(notice_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    n = db.query(Notice).filter(Notice.id == notice_id).first()
    if not n:
        raise HTTPException(status_code=404, detail="通知不存在")
    signed = db.query(NoticeSignature).filter(NoticeSignature.notice_id == notice_id).count()
    return {"id": n.id, "title": n.title, "content": n.content, "category": n.category,
            "status": n.status, "publish_time": n.publish_time.strftime("%Y-%m-%d %H:%M") if n.publish_time else "",
            "signed_count": signed, "deadline": _deadline(n)}


@router.get("/{notice_id}/sign-status")
def sign_status(notice_id: int, user=Depends(require_admin), db: Session = Depends(get_db)):
    """签收统计（手册 逾期统计）"""
    n = db.query(Notice).filter(Notice.id == notice_id).first()
    if not n:
        raise HTTPException(status_code=404, detail="通知不存在")
    signed_ids = {s.user_id for s in db.query(NoticeSignature).filter(NoticeSignature.notice_id == notice_id).all()}
    users = db.query(User).filter(User.is_active == True).all()  # noqa: E712
    deadline = n.publish_time + timedelta(days=SIG_DEADLINE_DAYS) if n.publish_time else None
    now = datetime.now()
    signed_list, unsigned_list, overdue_list = [], [], []
    for u in users:
        if u.id == n.publisher_id:
            continue
        if u.id in signed_ids:
            signed_list.append(u.real_name)
        else:
            unsigned_list.append(u.real_name)
            if deadline and now > deadline:
                overdue_list.append(u.real_name)
    return {"total": len(users) - 1, "signed": len(signed_list), "unsigned": len(unsigned_list),
            "overdue": len(overdue_list), "signed_names": signed_list[:50],
            "unsigned_names": unsigned_list[:50], "overdue_names": overdue_list[:50]}


@router.get("/my/list")
def my_notices(user=Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(Notice).filter(Notice.status == "published").order_by(Notice.id.desc()).limit(50).all()
    out = []
    for n in rows:
        sig = db.query(NoticeSignature).filter(NoticeSignature.notice_id == n.id, NoticeSignature.user_id == user.id).first()
        out.append({"id": n.id, "title": n.title, "content": n.content, "category": n.category,
                    "publish_time": n.publish_time.strftime("%Y-%m-%d %H:%M") if n.publish_time else "",
                    "signed": bool(sig), "deadline": _deadline(n)})
    return out


@router.post("/my/{notice_id}/sign")
def sign(notice_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    n = db.query(Notice).filter(Notice.id == notice_id, Notice.status == "published").first()
    if not n:
        raise HTTPException(status_code=404, detail="通知不存在或未发布")
    exists = db.query(NoticeSignature).filter(NoticeSignature.notice_id == notice_id, NoticeSignature.user_id == user.id).first()
    if exists:
        return {"msg": "已签收过"}
    db.add(NoticeSignature(notice_id=notice_id, user_id=user.id))
    db.commit()
    return {"msg": "签收成功"}
