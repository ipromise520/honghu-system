import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SECRET_KEY = os.environ.get("HONGHU_SECRET_KEY", "honghu-local-dev-secret-key-2026")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24
REFRESH_TOKEN_EXPIRE_DAYS = 30

DB_PATH = os.path.join(BASE_DIR, "honghu_new.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

TARGET_PER_PERSON = 160          # 延伸服务人均任务目标（手册 7.6）
OVERDUE_HOURS = 2                # H5 回单后超时阈值（手册 8.3）

COMPLETED_STATUS = ["已施工"]
PENDING_STATUS = ["处理中", "流程未启动", "待施工", "待预约", "待智能预约", "待地址确认", "驳回中"]

# 省公司预警邮件（139邮箱 IMAP，授权码登录）
MAIL_USER = os.environ.get("HONGHU_MAIL_USER", "13797308606@139.com")
MAIL_AUTH = os.environ.get("HONGHU_MAIL_AUTH", "ff61668740542f271600")
