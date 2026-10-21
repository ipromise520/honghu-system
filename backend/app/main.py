"""洪湖支撑服务中心（8080 本地运行版）— FastAPI 入口"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.database import engine, SessionLocal, Base
from app.seed import seed
from app.routers import auth, dashboard, metrics, h5, work_orders, tasks, notices, sync, users, alerts, honghu

app = FastAPI(title="洪湖支撑服务中心管理系统（本地版）", version="1.0.0")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

for r in [auth.router, dashboard.router, metrics.router, h5.router, work_orders.router,
          tasks.router, notices.router, sync.router, users.router, alerts.router, honghu.router]:
    app.include_router(r, prefix="/api/v1")

FRONTEND_DIST = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                             "frontend", "dist")


def _migrate_columns():
    """SQLite 幂等补列：create_all 只建新表、不会给已存在的表加列。

    H5 盯控需要 work_orders / h5_complaint 上的明细字段（小区名称/联系人/故障号码等）。
    """
    from sqlalchemy import text
    adds = {
        "work_orders": [("community_name", "VARCHAR(255)"), ("grid", "VARCHAR(128)"),
                        ("service_number", "VARCHAR(64)")],
        "h5_complaint": [("community_name", "VARCHAR(255)"), ("contact_person", "VARCHAR(64)"),
                         ("fault_number", "VARCHAR(64)"), ("grid", "VARCHAR(128)")],
    }
    with engine.connect() as conn:
        for tbl, cols in adds.items():
            try:
                have = {row[1] for row in conn.execute(text(f"PRAGMA table_info({tbl})"))}
            except Exception:
                continue
            if not have:
                continue
            for name, typ in cols:
                if name not in have:
                    conn.execute(text(f"ALTER TABLE {tbl} ADD COLUMN {name} {typ}"))
        conn.commit()


@app.on_event("startup")
def startup():
    Base.metadata.create_all(engine)
    _migrate_columns()
    created = seed()
    if created:
        print("[honghu] 演示数据初始化完成")


@app.get("/api/v1/health")
def health():
    return {"status": "ok", "system": "洪湖支撑服务中心(8080 本地版)", "time": __import__("datetime").datetime.now().isoformat()}


if os.path.isdir(FRONTEND_DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIST, "assets")), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        fp = os.path.join(FRONTEND_DIST, full_path)
        if full_path and os.path.isfile(fp):
            return FileResponse(fp)
        # index.html 不缓存，确保发版后浏览器立即加载新版本（assets 文件名带 hash 可长缓存）
        return FileResponse(os.path.join(FRONTEND_DIST, "index.html"),
                            headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
