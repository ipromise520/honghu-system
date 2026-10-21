import time
import threading
from collections import defaultdict
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User, AuditLog
from ..security import create_token, decode_token, verify_password, hash_password
from ..deps import get_current_user, get_role

router = APIRouter(prefix="/auth", tags=["auth"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)

# ---------------- 登录暴力破解防护（内存滑动窗口，重启清零） ----------------
_LOGIN_MAX_ATTEMPTS = 5      # 同一账号+IP 窗口内最大失败次数
_LOGIN_WINDOW_SECONDS = 300  # 窗口期（秒）
_login_failures: dict = defaultdict(list)
_login_lock = threading.Lock()


def _check_login_rate(key: str):
    now = time.monotonic()
    with _login_lock:
        recent = [t for t in _login_failures[key] if now - t < _LOGIN_WINDOW_SECONDS]
        if len(recent) >= _LOGIN_MAX_ATTEMPTS:
            _login_failures[key] = recent
            raise HTTPException(status_code=429,
                               detail=f"失败次数过多，账号已临时锁定，请 {_LOGIN_WINDOW_SECONDS // 60} 分钟后再试")
        recent.append(now)
        _login_failures[key] = recent


def _clear_login_failures(key: str):
    with _login_lock:
        _login_failures.pop(key, None)


def user_payload(u: User):
    return {"id": u.id, "username": u.username, "real_name": u.real_name, "role": get_role(u),
            "role_name": u.role.name if u.role else "", "district_id": u.district_id,
            "district": u.district.name if u.district else "", "team": u.team, "grid": u.grid,
            "position": u.position}


@router.post("/login")
def login(form: dict, request: Request, db: Session = Depends(get_db)):
    username = (form.get("username") or "").strip()
    password = form.get("password") or ""
    if not username or not password:
        raise HTTPException(status_code=400, detail="用户名或密码错误")
    client_ip = request.client.host if request.client else "unknown"
    _check_login_rate(f"{username}|{client_ip}")
    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=400, detail="用户名或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已停用")
    _clear_login_failures(f"{username}|{client_ip}")
    db.add(AuditLog(username=user.username, action="登录"))
    db.commit()
    return {"access_token": create_token(user.id, user.username, get_role(user)),
            "refresh_token": create_token(user.id, user.username, get_role(user), "refresh"),
            "token_type": "bearer", "user": user_payload(user)}


@router.post("/refresh")
def refresh(body: dict, db: Session = Depends(get_db)):
    payload = decode_token(body.get("refresh_token", ""))
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="refresh_token 无效")
    user = db.query(User).filter(User.id == int(payload["sub"])).first()
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    return {"access_token": create_token(user.id, user.username, get_role(user)), "token_type": "bearer"}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return user_payload(user)


def _validate_new_password(pwd: str):
    if not pwd or len(pwd) < 8:
        raise HTTPException(status_code=400, detail="新密码长度至少 8 位")
    if pwd.isdigit() or pwd.isalpha():
        raise HTTPException(status_code=400, detail="新密码需同时包含字母和数字")


@router.post("/change-password")
def change_password(body: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not verify_password(body.get("old_password", ""), user.password_hash):
        raise HTTPException(status_code=400, detail="原密码错误")
    _validate_new_password(body.get("new_password", ""))
    user.password_hash = hash_password(body["new_password"])
    db.commit()
    return {"msg": "密码修改成功"}


@router.post("/reset-password")
def reset_password(body: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if get_role(user) not in ("super_admin", "district_admin"):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    target = db.query(User).filter(User.id == body["user_id"]).first()
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    if get_role(user) == "district_admin" and target.district_id != user.district_id:
        raise HTTPException(status_code=403, detail="只能重置本区县用户密码")
    # 不再默认 123456：必须显式提供符合强度要求的新密码
    _validate_new_password(body.get("new_password", ""))
    target.password_hash = hash_password(body["new_password"])
    db.commit()
    return {"msg": f"已重置 {target.real_name} 的密码"}


@router.post("/logout")
def logout():
    return {"msg": "已登出", "time": datetime.now().isoformat()}
