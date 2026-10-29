"""洪湖支撑服务中心管理系统 — FastAPI 入口"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text

from fastapi import FastAPI, APIRouter
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from starlette.datastructures import MutableHeaders

from app.config import CORS_ORIGINS, DOCS_ENABLED, MOBILE_DIST
from app.database import engine, SessionLocal, Base
from app.seed import seed
from app.routers import auth, dashboard, metrics, h5, work_orders, tasks, notices, sync, users, alerts, honghu, mp

app = FastAPI(title="洪湖支撑服务中心管理系统", version="1.3.0",
              docs_url="/docs" if DOCS_ENABLED else None,
              redoc_url="/redoc" if DOCS_ENABLED else None,
              openapi_url="/openapi.json" if DOCS_ENABLED else None)

# 中间件顺序：后添加者在外层。安全头 → GZip → CORS（CORS 最外层，预检与错误响应也带头）
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"],
                   allow_headers=["*"], allow_credentials=False)

# 生产安全响应头 + 静态资源长缓存（纯 ASGI 中间件，零缓冲，不影响 SSE 流式响应）
# 注意：必须是 str —— MutableHeaders.setdefault 内部按 str 做 .lower().encode("latin-1")，
# 传 bytes 会抛 AttributeError 导致所有请求 500（Starlette 1.8 实测）。
_SECURITY_HEADERS = [
    ("x-content-type-options", "nosniff"),
    ("x-frame-options", "DENY"),
    ("referrer-policy", "strict-origin-when-cross-origin"),
    ("content-security-policy",
     "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
     "script-src 'self'; connect-src 'self'; font-src 'self' data:; frame-ancestors 'none'; base-uri 'self'"),
]


class SecurityHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        is_asset = path.startswith("/assets/")

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(raw=message["headers"])
                for key, val in _SECURITY_HEADERS:
                    headers.setdefault(key, val)
                if is_asset:
                    # 构建产物文件名带内容 hash，可长期强缓存
                    headers.setdefault("cache-control", "public, max-age=31536000, immutable")
            await send(message)

        await self.app(scope, receive, send_wrapper)


app.add_middleware(SecurityHeadersMiddleware)

for r in [auth.router, dashboard.router, metrics.router, h5.router, work_orders.router,
          tasks.router, notices.router, sync.router, users.router, alerts.router, honghu.router, mp.router]:
    app.include_router(r, prefix="/api/v1")

# 装维人员端 H5 专用 API 前缀 /m/api（由 8011 独立承载，与桌面端 /api/v1 隔离，避免跨实例 token 问题）
mobile_api = APIRouter()
mobile_api.include_router(auth.router)
mobile_api.include_router(mp.router)
app.include_router(mobile_api, prefix="/m/api")


FRONTEND_DIST = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                             "frontend", "dist-prod")

# ---------------- 幂等迁移（create_all 只建新表，不会给已存在的表加列/加索引） ----------------

# 历史补列
_ADD_COLUMNS = {
    # 登录失败锁定（v1.6.4）：原来只存进程内存，管理端看不到也解不了，改为落库
    "users": [("failed_attempts", "INTEGER DEFAULT 0"),
              ("locked_until", "VARCHAR(32) DEFAULT ''"),
              ("last_failed_at", "VARCHAR(32) DEFAULT ''")],
    "work_orders": [("community_name", "VARCHAR(255)"), ("grid", "VARCHAR(128)"),
                    ("service_number", "VARCHAR(64)")],
    "h5_complaint": [("community_name", "VARCHAR(255)"), ("contact_person", "VARCHAR(64)"),
                     ("fault_number", "VARCHAR(64)"), ("grid", "VARCHAR(128)")],
    # H5满意度通报 区县汇总：新版平台导出本地聚合出 当日/当月 两周期 + 不满意人数 + 差额
    "h5_survey_districts": [("period", "VARCHAR(8) DEFAULT '当月'"),
                            ("unsat_timely", "INTEGER DEFAULT 0"),
                            ("unsat_service", "INTEGER DEFAULT 0"),
                            ("unsat_effect", "INTEGER DEFAULT 0"),
                            ("unsat_image", "INTEGER DEFAULT 0"),
                            ("diff_cnt", "INTEGER DEFAULT 0")],
    # 竣工率盯控维度（装机宽表回填）
    "install_orders": [("first_contact_time", "VARCHAR(32) DEFAULT ''"),
                       ("first_contact_timely", "VARCHAR(8) DEFAULT ''"),
                       ("visit_time", "VARCHAR(32) DEFAULT ''"),
                       ("visit_timely", "VARCHAR(8) DEFAULT ''"),
                       ("activate_time", "VARCHAR(32) DEFAULT ''"),
                       ("revisit_time", "VARCHAR(32) DEFAULT ''"),
                       ("revisit_result", "VARCHAR(16) DEFAULT ''"),
                       ("ivr_pass", "VARCHAR(8) DEFAULT ''"),
                       ("ivr_satisfied", "VARCHAR(8) DEFAULT ''"),
                       ("ivr_score", "VARCHAR(16) DEFAULT ''"),
                       ("ivr_final", "VARCHAR(8) DEFAULT ''"),
                       ("urge_cnt", "INTEGER DEFAULT 0"),
                       ("refund_reason2", "VARCHAR(64) DEFAULT ''"),
                       ("refund_person", "VARCHAR(64) DEFAULT ''"),
                       ("ai_result", "VARCHAR(16) DEFAULT ''"),
                       ("zongdiao_result", "VARCHAR(16) DEFAULT ''"),
                       ("city_result", "VARCHAR(16) DEFAULT ''"),
                       # H5短信盯控（v1.6.8）：宽表自带小区/号码，一处导入全局生效
                       ("community_name", "VARCHAR(255) DEFAULT ''"),
                       ("service_number", "VARCHAR(64) DEFAULT ''")],
    "complaint_orders": [("community_name", "VARCHAR(255) DEFAULT ''"),
                         ("contact_person", "VARCHAR(64) DEFAULT ''"),
                         ("fault_number", "VARCHAR(64) DEFAULT ''")],
}

# month 生成列（VIRTUAL）：把 substr(accept_time,1,7) 变为可索引列，月度聚合/筛选走索引
_GENERATED_MONTH = {
    "work_orders": "substr(accept_time, 1, 7)",
    "h5_complaint": "substr(accept_time, 1, 7)",
    "cancel_reschedule_orders": "substr(accept_time, 1, 7)",
}

# 新索引（表, 索引名, 列）
_INDEXES = [
    ("work_orders", "ix_wo_month", "month"),
    ("work_orders", "ix_wo_district_status", "district_id, status"),
    ("work_orders", "ix_wo_type_status", "broadband_type, status"),
    ("extension_orders", "ix_ext_month", "month"),
    ("h5_complaint", "ix_hc_month", "month"),
    ("h5_complaint", "ix_hc_status", "order_status"),
    ("cancel_reschedule_orders", "ix_cro_month", "month"),
    ("tasks", "ix_task_status", "status"),
    ("tasks", "ix_task_assignee", "assignee_id"),
    ("tasks", "ix_task_district", "district_id"),
    ("alert_orders", "ix_alert_type_district", "type, district"),
    ("report_timeouts", "ix_rt_district", "district"),
    ("report_wide_orders", "ix_rwo_district_timely", "district, visit_timely"),
    ("report_complaint_orders", "ix_rco_district", "district"),
    ("report_dup_orders", "ix_rdo_district", "district"),
    ("ai_visit_issues", "ix_aivisit_month", "month"),
    ("ai_visit_stats", "ix_aivisitstat_month", "month"),
    # H5 盯控（v1.6.8）：应发清单要用 NOT EXISTS 反查回执/详单，没有索引会退化成全表扫
    ("h5_receipts", "ix_receipt_order", "order_no, kind"),
    ("h5_survey_records", "ix_h5rec_order", "order_no, kind"),
    ("install_orders", "ix_io_status_order", "status, order_id"),
    ("complaint_orders", "ix_co_status_eoms", "status, eoms_no"),
]


def _table_columns(conn, table: str) -> set:
    # 必须用 table_xinfo 而非 table_info：table_info 不显示生成列（SQLite 既定行为），
    # 用错会导致 month 生成列的存在性检查永远为 False（重复 ALTER / 静默跳过）。
    # row[1] 为列名（table_xinfo: cid, name, type, notnull, dflt_value, pk, hidden）。
    return {row[1] for row in conn.execute(text(f"PRAGMA table_xinfo({table})"))}


def _migrate_columns():
    """幂等迁移：补列 + month 生成列 + 新索引。SQLite >= 3.31 支持 ADD COLUMN 虚拟生成列。"""
    with engine.begin() as conn:
        for tbl, cols in _ADD_COLUMNS.items():
            try:
                have = _table_columns(conn, tbl)
            except Exception:
                continue
            if not have:
                continue
            for name, typ in cols:
                if name not in have:
                    try:
                        conn.execute(text(f"ALTER TABLE {tbl} ADD COLUMN {name} {typ}"))
                        print(f"[honghu] 已为 {tbl} 补列 {name}")
                    except Exception as e:
                        print(f"[honghu] 警告：{tbl} 补列 {name} 失败（{e}）")
        for tbl, expr in _GENERATED_MONTH.items():
            try:
                have = _table_columns(conn, tbl)
            except Exception:
                continue
            if not have or "month" in have:
                continue
            try:
                conn.execute(text(f"ALTER TABLE {tbl} ADD COLUMN month VARCHAR(7) "
                                  f"GENERATED ALWAYS AS ({expr}) VIRTUAL"))
                print(f"[honghu] 已为 {tbl} 添加 month 生成列（VIRTUAL，可索引）")
            except Exception as e:
                print(f"[honghu] 警告：{tbl}.month 生成列创建失败（{e}），该表将回退 substr 查询")
        for tbl, name, cols in _INDEXES:
            try:
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON {tbl} ({cols})"))
            except Exception as e:
                print(f"[honghu] 警告：索引 {name} 创建失败（{e}）")


def _rebuild_h5_districts():
    """h5_survey_districts 重建：旧版 satisfaction/q_timely/q_service/q_effect/fttr_sat 为 NOT NULL，
    新版解析器对「无测评」区县写 None，而 bulk_insert_mappings 会省略无默认值的列 → NOT NULL 冲突。
    SQLite 不支持 ALTER COLUMN，按新模型重建表并拷贝数据。幂等：目标列均已可空即跳过。"""
    targets = ("satisfaction", "q_timely", "q_service", "q_effect", "fttr_sat")
    with engine.begin() as conn:
        info = list(conn.execute(text("PRAGMA table_info(h5_survey_districts)")))
        if not info:
            return  # 表不存在（全新库已由 create_all 按新模型建好）
        notnull = {r[1] for r in info if r[3]}
        if not (set(targets) & notnull):
            return  # 已是可空 schema
        conn.execute(text("DROP TABLE IF EXISTS h5_survey_districts_old"))
        conn.execute(text("ALTER TABLE h5_survey_districts RENAME TO h5_survey_districts_old"))
    Base.metadata.create_all(engine)  # 独立连接按新模型重建（5 列可空）
    with engine.begin() as conn:
        old_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(h5_survey_districts_old)"))}
        new_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(h5_survey_districts)"))}
        common = [c for c in new_cols if c in old_cols and c != "id"]
        if common:
            cols = ", ".join(common)
            conn.execute(text(f"INSERT INTO h5_survey_districts ({cols}) "
                              f"SELECT {cols} FROM h5_survey_districts_old"))
        conn.execute(text("DROP TABLE h5_survey_districts_old"))
    print("[honghu] 已重建 h5_survey_districts（满意度等 5 列放宽为可空，数据已拷贝）")


@app.on_event("startup")
def startup():
    Base.metadata.create_all(engine)
    _migrate_columns()
    _rebuild_h5_districts()
    created = seed()
    if created:
        print("[honghu] 演示数据初始化完成")


@app.get("/api/v1/health")
def health():
    return {"status": "ok", "system": "洪湖支撑服务中心", "time": __import__("datetime").datetime.now().isoformat()}


if os.path.isdir(FRONTEND_DIST):
    class _HashedStatic(StaticFiles):
        """带内容哈希的静态资源（/assets/xxx.<hash>.js）：文件名即版本号，
        可安全长缓存（immutable）。发版全量重建后 hash 变化 → 浏览器自动拉新，
        无需 304 协商，重复访问秒开。index.html 仍 no-cache（见下），保证入口即时更新。
        （v1.3.12 的 no-cache 为规避「局部未重建导致旧界面」；本系统每次均全量构建，故可放心长缓存）"""
        async def get_response(self, path, scope):
            resp = await super().get_response(path, scope)
            if not resp.headers.get("Cache-Control"):
                resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            return resp

    app.mount("/assets", _HashedStatic(directory=os.path.join(FRONTEND_DIST, "assets")),
              name="assets")

    # ---------- 移动端（装维人员端 H5）：挂在 /m，独立子工程产物 ----------
    # 必须在 catch-all 之前注册，否则会被上面的 SPA 回退吞掉，表现为白屏 200
    if os.path.isdir(MOBILE_DIST):
        if os.path.isdir(os.path.join(MOBILE_DIST, "assets")):
            app.mount("/m/assets", _HashedStatic(directory=os.path.join(MOBILE_DIST, "assets")),
                      name="m-assets")

        @app.get("/m")
        @app.get("/m/{full_path:path}")
        def mobile_spa(full_path: str = ""):
            fp = os.path.join(MOBILE_DIST, full_path)
            if full_path and os.path.isfile(fp):
                return FileResponse(fp)
            return FileResponse(
                os.path.join(MOBILE_DIST, "index.html"),
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        fp = os.path.join(FRONTEND_DIST, full_path)
        if full_path and os.path.isfile(fp):
            return FileResponse(fp)
        # index.html 不缓存，确保发版后浏览器立即加载新版本（assets 文件名带 hash 可长缓存）
        return FileResponse(os.path.join(FRONTEND_DIST, "index.html"),
                            headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
