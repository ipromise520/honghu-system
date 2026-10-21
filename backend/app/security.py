import bcrypt
import jwt
from datetime import datetime, timedelta, timezone

from .config import SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_HOURS, REFRESH_TOKEN_EXPIRE_DAYS

if not SECRET_KEY:
    # 密钥未配置时直接拒绝启动（fail-fast），避免使用空/默认密钥签发令牌
    raise RuntimeError(
        "HONGHU_SECRET_KEY 未配置：请在 /etc/honghu/env 中设置后重启服务 "
        "（生成方式：python3 -c \"import secrets; print(secrets.token_hex(32))\"）"
    )


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_token(user_id: int, username: str, role: str, token_type: str = "access") -> str:
    expire = datetime.now(timezone.utc) + (
        timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS) if token_type == "access"
        else timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    )
    payload = {"sub": str(user_id), "username": username, "role": role,
               "type": token_type, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None
