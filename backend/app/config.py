import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# JWT 签名密钥：必须通过环境变量注入（/etc/honghu/env → systemd EnvironmentFile），
# 不再提供源码内默认值（防止持码即可伪造任意身份）。未配置时 security.py 将拒绝启动。
SECRET_KEY = os.environ.get("HONGHU_SECRET_KEY", "")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24
REFRESH_TOKEN_EXPIRE_DAYS = 30

DB_PATH = os.path.join(BASE_DIR, "honghu_new.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

TARGET_PER_PERSON = 160          # 延伸服务人均任务目标（手册 7.6）
OVERDUE_HOURS = 2                # H5 回单后超时阈值（手册 8.3）

COMPLETED_STATUS = ["已施工"]
PENDING_STATUS = ["处理中", "流程未启动", "待施工", "待预约", "待智能预约", "待地址确认", "驳回中"]

# 省公司预警邮件（139邮箱 IMAP，授权码登录）——凭据经环境变量注入，不再硬编码在源码
MAIL_USER = os.environ.get("HONGHU_MAIL_USER", "")
MAIL_AUTH = os.environ.get("HONGHU_MAIL_AUTH", "")

# 交互式文档（/docs、/openapi.json）开关：生产默认关闭，调试时 HONGHU_DOCS=1 开启
DOCS_ENABLED = os.environ.get("HONGHU_DOCS", "0") == "1"

# CORS 白名单（逗号分隔）。SPA 与 API 同源部署，仅需覆盖实际访问地址
CORS_ORIGINS = [o.strip() for o in os.environ.get(
    "HONGHU_CORS_ORIGINS",
    "http://10.31.88.155:8011,http://100.71.222.104:8011,http://127.0.0.1:8011,http://localhost:8011"
).split(",") if o.strip()]
