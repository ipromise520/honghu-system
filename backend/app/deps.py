import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .config import SECRET_KEY, ALGORITHM
from .database import get_db
from .models import User, Role, District


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录或 token 缺失")
    token = auth[7:]
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="token 无效或已过期，请重新登录")
    user = db.query(User).filter(User.id == int(payload["sub"])).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="用户不存在或已停用")
    return user


def get_role(user: User) -> str:
    return user.role.code if user.role else "field_worker"


def require_admin(user: User = Depends(get_current_user)):
    role = get_role(user)
    if role not in ("super_admin", "district_admin"):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def require_super_admin(user: User = Depends(get_current_user)):
    if get_role(user) != "super_admin":
        raise HTTPException(status_code=403, detail="需要市级管理员权限")
    return user


def scope_district_ids(user: User, db: Session) -> list[int] | None:
    """区县管理员自动按 district_id 过滤；super_admin 返回 None 表示全量（手册 六）"""
    role = get_role(user)
    if role == "super_admin":
        return None
    if role == "district_admin":
        return [user.district_id] if user.district_id else []
    return [user.district_id] if user.district_id else []
