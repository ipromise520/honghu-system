import time
import threading
from collections import defaultdict
from datetime import datetime, timedelta
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

# ---------------- 登录暴力破解防护（v1.6.4：改为落库，管理端可查可解） ----------------
# 原实现把失败记录只放在进程内存 dict 里，带来三个问题：
#   ① uvicorn 重启即清零，锁定形同虚设；
#   ② 管理端完全看不到「谁被锁了」，用户只会说「我登不上」；
#   ③ 管理员没有任何解除入口，只能干等 5 分钟。
# 现在计数与锁定时间写进 users 表（failed_attempts / locked_until / last_failed_at）。
_LOGIN_MAX_ATTEMPTS = 5      # 连续失败达到此数即锁定
_LOGIN_LOCK_MINUTES = 5      # 锁定时长（分钟）


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _lock_remaining_seconds(u: User) -> int:
    """返回锁定剩余秒数；<=0 表示未锁定或已过期"""
    if not u or not getattr(u, "locked_until", ""):
        return 0
    try:
        until = datetime.strptime(u.locked_until, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return 0
    return int((until - datetime.now()).total_seconds())


def _raise_if_locked(u: User):
    """账号处于锁定期内则直接拒绝（在验密码之前调用）"""
    left = _lock_remaining_seconds(u)
    if left > 0:
        mins = (left + 59) // 60
        raise HTTPException(
            status_code=429,
            detail=f"密码连续错误 {_LOGIN_MAX_ATTEMPTS} 次，账号已锁定，请 {mins} 分钟后再试"
                   f"（可联系管理员在「用户管理」中立即解锁）")


def _record_failure(u: User, db: Session):
    """记录一次密码错误；达到阈值则写锁定截止时间"""
    if u is None:
        return
    u.failed_attempts = (u.failed_attempts or 0) + 1
    u.last_failed_at = _now_str()
    if u.failed_attempts >= _LOGIN_MAX_ATTEMPTS:
        u.locked_until = (datetime.now() + timedelta(minutes=_LOGIN_LOCK_MINUTES)).strftime(
            "%Y-%m-%d %H:%M:%S")
    db.commit()


def _clear_login_failures(u: User, db: Session):
    """登录成功 / 管理员解锁：清空计数与锁定"""
    if u is None:
        return
    if (u.failed_attempts or 0) or (u.locked_until or ""):
        u.failed_attempts = 0
        u.locked_until = ""
        db.commit()


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
    user = db.query(User).filter(User.username == username).first()

    # ① 先判锁定（不消耗计数）：锁定期内直接告知剩余时间，不再累计失败
    if user:
        _raise_if_locked(user)

    # ② 再验密码：错才计数（原实现在验密码前就无条件 +1，导致密码正确也被算失败）
    if not user or not verify_password(password, user.password_hash):
        _record_failure(user, db)
        left = (_LOGIN_MAX_ATTEMPTS - (user.failed_attempts or 0)) if user else 0
        if user and left <= 0:
            raise HTTPException(
                status_code=429,
                detail=f"密码连续错误 {_LOGIN_MAX_ATTEMPTS} 次，账号已锁定 {_LOGIN_LOCK_MINUTES} 分钟"
                       f"（可联系管理员在「用户管理」中立即解锁）")
        tip = f"，还可尝试 {left} 次" if user and left > 0 else ""
        raise HTTPException(status_code=400, detail="用户名或密码错误" + tip)

    # ③ 密码正确但账号被停用
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已停用，请联系管理员启用")

    _clear_login_failures(user, db)
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
