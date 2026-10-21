"""用户 / 区县 / 人员管理（手册 7.2）"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User, Role, District, AuditLog
from ..deps import get_current_user, require_admin, require_super_admin, get_role
from ..security import hash_password

router = APIRouter(tags=["users"])


@router.get("/users/roles/list")
def role_list(user=Depends(get_current_user), db: Session = Depends(get_db)):
    return [{"id": r.id, "code": r.code, "name": r.name, "level": r.level} for r in db.query(Role).all()]


@router.get("/users")
def list_users(page: int = 1, page_size: int = 50, role: str = None, district_id: int = None,
               keyword: str = None, user=Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(User)
    my_role = get_role(user)
    if my_role == "district_admin" and user.district_id:
        q = q.filter(User.district_id == user.district_id)
    if district_id:
        q = q.filter(User.district_id == district_id)
    roles = {r.id: r.code for r in db.query(Role).all()}
    if role:
        q = q.filter(User.role_id.in_([rid for rid, code in roles.items() if code == role]))
    if keyword:
        kw = f"%{keyword}%"
        q = q.filter(User.username.like(kw) | User.real_name.like(kw))
    total = q.count()
    rows = q.order_by(User.id).offset((page - 1) * page_size).limit(page_size).all()
    districts = {d.id: d.name for d in db.query(District).all()}
    role_names = {r.id: r.name for r in db.query(Role).all()}
    return {"total": total, "items": [{
        "id": u.id, "username": u.username, "real_name": u.real_name, "phone": u.phone,
        "role": roles.get(u.role_id, ""), "role_name": role_names.get(u.role_id, ""),
        "district_id": u.district_id, "district": districts.get(u.district_id, ""),
        "team": u.team, "grid": u.grid, "position": u.position, "is_active": u.is_active}
        for u in rows]}


@router.post("/users")
def create_user(body: dict, user=Depends(require_admin), db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == body["username"]).first():
        raise HTTPException(status_code=400, detail="用户名已存在")
    role = db.query(Role).filter(Role.code == body.get("role", "field_worker")).first()
    if not role:
        raise HTTPException(status_code=400, detail="角色无效")
    if get_role(user) == "district_admin" and role.code != "field_worker":
        raise HTTPException(status_code=403, detail="区县管理员只能创建装维人员")
    u = User(username=body["username"], password_hash=hash_password(body.get("password", "123456")),
             real_name=body["real_name"], phone=body.get("phone", ""), role_id=role.id,
             district_id=body.get("district_id"), team=body.get("team", ""),
             grid=body.get("grid", ""), position=body.get("position", ""))
    db.add(u)
    db.add(AuditLog(username=user.username, action="创建用户", detail=u.username))
    db.commit()
    return {"msg": "创建成功", "id": u.id}


@router.put("/users/{user_id}")
def update_user(user_id: int, body: dict, user=Depends(require_admin), db: Session = Depends(get_db)):
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="用户不存在")
    if get_role(user) == "district_admin" and u.district_id != user.district_id:
        raise HTTPException(status_code=403, detail="只能修改本区县用户")
    for f in ["real_name", "phone", "team", "grid", "position"]:
        if f in body:
            setattr(u, f, body[f])
    if "district_id" in body:
        u.district_id = body["district_id"]
    if "is_active" in body:
        u.is_active = bool(body["is_active"])
    if "role" in body:
        role = db.query(Role).filter(Role.code == body["role"]).first()
        if role:
            u.role_id = role.id
    if body.get("password"):
        u.password_hash = hash_password(body["password"])
    db.commit()
    return {"msg": "更新成功"}


@router.delete("/users/{user_id}")
def delete_user(user_id: int, user=Depends(require_super_admin), db: Session = Depends(get_db)):
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="用户不存在")
    if u.username == "admin":
        raise HTTPException(status_code=400, detail="不能删除内置管理员")
    u.is_active = False
    db.add(AuditLog(username=user.username, action="停用用户", detail=u.username))
    db.commit()
    return {"msg": "已停用"}


@router.get("/districts")
def district_list(user=Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(District).all()
    cnt = {d: 0 for d in [x.id for x in rows]}
    from sqlalchemy import func
    from ..models import WorkOrder
    return [{"id": d.id, "code": d.code, "name": d.name, "city": d.city, "standard_grid": d.standard_grid}
            for d in rows]


@router.get("/districts/{district_id}/grids")
def district_grids(district_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    d = db.query(District).filter(District.id == district_id).first()
    if not d:
        raise HTTPException(status_code=404, detail="区县不存在")
    grids = sorted({u.grid for u in db.query(User).filter(User.district_id == district_id).all() if u.grid})
    return {"district": d.name, "grids": grids or ([d.standard_grid] if d.standard_grid else [])}


@router.get("/personnel/list")
def personnel_list(user=Depends(get_current_user), db: Session = Depends(get_db)):
    """人员清单（执行人下拉，手册 7.2）"""
    q = db.query(User).filter(User.is_active == True)  # noqa: E712
    if get_role(user) == "district_admin" and user.district_id:
        q = q.filter(User.district_id == user.district_id)
    rows = q.all()
    districts = {d.id: d.name for d in db.query(District).all()}
    return [{"id": u.id, "real_name": u.real_name, "district": districts.get(u.district_id, ""),
             "team": u.team, "grid": u.grid, "position": u.position} for u in rows]
