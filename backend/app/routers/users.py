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
        "team": u.team, "grid": u.grid, "position": u.position, "is_active": u.is_active,
        # 装维人员工号（v1.6.9）：从 H5 详单/省公司通报自动同步，无工号时为空
        "staff_no": getattr(u, "staff_no", "") or "",
        # 登录锁定状态（v1.6.4）：供管理端展示「已锁定 剩余X分钟」与解锁按钮
        "failed_attempts": u.failed_attempts or 0,
        "locked_until": u.locked_until or "",
        "last_failed_at": u.last_failed_at or "",
        "is_locked": _lock_left(u) > 0,
        "lock_left_min": (_lock_left(u) + 59) // 60 if _lock_left(u) > 0 else 0,
    } for u in rows]}


def _lock_left(u) -> int:
    """锁定剩余秒数；<=0 表示未锁定。与 auth.py 的 _lock_remaining_seconds 同口径。"""
    if not u or not getattr(u, "locked_until", ""):
        return 0
    try:
        from datetime import datetime
        until = datetime.strptime(u.locked_until, "%Y-%m-%d %H:%M:%S")
        return int((until - datetime.now()).total_seconds())
    except Exception:
        return 0


@router.put("/users/{user_id}/status")
def set_user_status(user_id: int, body: dict, user=Depends(require_admin),
                    db: Session = Depends(get_db)):
    """启用 / 停用用户（v1.6.4）

    原实现只有 DELETE /users/{id} 做停用，**没有启用接口**，前端也只在
    is_active=True 时才渲染按钮 —— 账号一旦停用就再也点不到，只能改库。
    这里补一个显式启停接口，前端常驻按钮。
    """
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="用户不存在")
    if u.username == "admin":
        raise HTTPException(status_code=400, detail="不能停用内置管理员")
    # 区县管理员只能管本区县
    if get_role(user) == "district_admin" and u.district_id != user.district_id:
        raise HTTPException(status_code=403, detail="只能管理本区县用户")

    active = bool(body.get("is_active"))
    u.is_active = active
    if active:
        # 启用同时清空失败计数与锁定，避免「刚启用又显示被锁」
        u.failed_attempts = 0
        u.locked_until = ""
    db.add(AuditLog(username=user.username, action="启用用户" if active else "停用用户",
                    detail=u.username))
    db.commit()
    return {"msg": "已启用" if active else "已停用", "is_active": active}


@router.post("/users/{user_id}/unlock")
def unlock_user(user_id: int, user=Depends(require_admin), db: Session = Depends(get_db)):
    """立即解除登录锁定（v1.6.4）

    之前失败计数只在进程内存里，管理员既看不到也解不了，用户只能干等 5 分钟。
    现在落库后可在这里一键清零。
    """
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="用户不存在")
    if get_role(user) == "district_admin" and u.district_id != user.district_id:
        raise HTTPException(status_code=403, detail="只能管理本区县用户")
    was_locked = _lock_left(u) > 0
    u.failed_attempts = 0
    u.locked_until = ""
    db.add(AuditLog(username=user.username, action="解除登录锁定", detail=u.username))
    db.commit()
    return {"msg": "已解除锁定" if was_locked else "该账号当前未被锁定（已清零失败计数）",
            "is_locked": False}


@router.post("/users/sync-staff")
def sync_staff(body: dict = None, user=Depends(require_admin), db: Session = Depends(get_db)):
    """从各业务表聚合装维人员并批量建号（v1.6.9）

    姓名/工号/网格散在六张表里，单看任何一张都建不出完整账号，这里做多源合并。
    默认 ``dry_run=True``，先返回「将新建 N / 将更新 N / 跳过 N」让人确认，再落库。

    幂等：已有账号只补空的字段，**不重置密码、不改姓名**；
    同一人若早先用手机号建过号（如柴达 15171201240），按姓名命中后只补工号，不会重复建。
    """
    from ..staff_directory import DEFAULT_DISTRICT, DEFAULT_PASSWORD, sync_users
    body = body or {}
    district = body.get("district") or DEFAULT_DISTRICT
    if get_role(user) == "district_admin" and user.district_id:
        d = db.query(District).filter(District.id == user.district_id).first()
        district = d.name if d else district
    res = sync_users(db, district=district,
                     default_password=body.get("default_password") or DEFAULT_PASSWORD,
                     dry_run=bool(body.get("dry_run", True)),
                     operator=user.username)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error") or "同步失败")
    return res


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
             grid=body.get("grid", ""), position=body.get("position", ""),
             staff_no=body.get("staff_no", ""))
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
    for f in ["real_name", "phone", "team", "grid", "position", "staff_no"]:
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
