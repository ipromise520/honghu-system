# -*- coding: utf-8 -*-
"""cron 任务：每整点 20 分同步两类邮件（省公司预警 + 重要通报）

对应看板两个「同步邮件」按钮的后端逻辑：
  - POST /alerts/fetch        → sync_alerts(db)   「待处理工单预警」邮件（省公司每小时预警）
  - POST /alerts/fetch-report → sync_report(db)   「重要通报」邮件（当月区县指标通报）

调度：ipromise crontab「20 * * * *」（每个整点的 20 分，如 10:20、11:20）
日志：/vol2/Trae/backend/logs/cron_sync_emails.log
"""
import os
import sys
import traceback
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)


def _log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def _load_env_file() -> None:
    """cron 环境没有 systemd EnvironmentFile，需手动加载 /etc/honghu/env（IMAP 凭据等）。
    已存在于环境变量中的值不覆盖（与 systemd 行为保持一致）。"""
    path = "/etc/honghu/env"
    if not os.path.exists(path):
        _log(f"警告：{path} 不存在，依赖进程环境变量")
        return
    n = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            if k and os.environ.get(k) is None:
                os.environ[k] = v
                n += 1
    _log(f"env 加载完成（{path}，新增 {n} 项）")


def main() -> int:
    _load_env_file()

    # 延迟 import：确保 env 先于 app.config 读取环境变量
    from app.database import SessionLocal
    from app.routers.alerts import sync_alerts, sync_report

    rc = 0

    db = SessionLocal()
    try:
        _log("开始同步省公司预警邮件（待处理工单预警）…")
        r = sync_alerts(db)
        _log(f"省公司预警同步完成: {r}")
    except Exception:
        rc = 1
        _log("省公司预警同步失败:\n" + traceback.format_exc())
    finally:
        db.close()

    db = SessionLocal()
    try:
        _log("开始同步重要通报邮件…")
        r = sync_report(db)
        _log(f"重要通报同步完成: {r}")
    except Exception:
        rc = 1
        _log("重要通报同步失败:\n" + traceback.format_exc())
    finally:
        db.close()

    _log(f"本轮邮件同步结束（rc={rc}）")
    return rc


if __name__ == "__main__":
    sys.exit(main())
