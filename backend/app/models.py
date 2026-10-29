from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import String, Integer, Float, Boolean, DateTime, Text, ForeignKey, UniqueConstraint, Index, Computed
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def now_cn():
    return datetime.now(timezone.utc).astimezone().replace(tzinfo=None)


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(64))
    level: Mapped[int] = mapped_column(Integer, default=1)
    permissions: Mapped[str] = mapped_column(Text, default="*")


class District(Base):
    __tablename__ = "districts"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(64))
    city: Mapped[str] = mapped_column(String(64), default="荆州市")
    standard_grid: Mapped[str] = mapped_column(String(64), default="")


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    real_name: Mapped[str] = mapped_column(String(64))
    phone: Mapped[str] = mapped_column(String(32), default="")
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"))
    district_id: Mapped[int | None] = mapped_column(ForeignKey("districts.id"), nullable=True)
    team: Mapped[str] = mapped_column(String(64), default="")
    grid: Mapped[str] = mapped_column(String(64), default="")
    position: Mapped[str] = mapped_column(String(64), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # ---- 登录失败锁定（v1.6.4）----
    # 原实现把失败次数只存在进程内存 _login_failures 里，重启即清零，
    # 管理端既看不到「谁被锁了」也没有解除入口。改为落库，管理员可查可解。
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[str] = mapped_column(String(32), default="")
    last_failed_at: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_cn)
    role: Mapped["Role"] = relationship("Role", lazy="joined")
    district: Mapped["District"] = relationship("District", lazy="joined")


class WorkOrder(Base):
    """装机宽表工单（手册 5.2）"""
    __tablename__ = "work_orders"
    __table_args__ = (
        Index("ix_wo_month", "month"),
        Index("ix_wo_district_status", "district_id", "status"),
        Index("ix_wo_type_status", "broadband_type", "status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[str] = mapped_column(String(64), unique=True)          # 27 开头定单编码
    order_theme: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(32), default="")             # 已施工/处理中/...
    accept_time: Mapped[str] = mapped_column(String(32), default="")        # YYYY-MM-DD HH:MM:SS
    # month 为生成列（VIRTUAL）：由 accept_time 前 7 位自动推导，免人工维护；
    # 已有库由 main._migrate_columns 用 ALTER TABLE 补齐，新库由 create_all 直接建
    month: Mapped[str] = mapped_column(String(7), Computed("substr(accept_time, 1, 7)"))
    completion_time: Mapped[str] = mapped_column(String(32), default="")
    district_id: Mapped[int | None] = mapped_column(ForeignKey("districts.id"), nullable=True)
    construction_dept: Mapped[str] = mapped_column(String(64), default="")  # 当前处理人或回单人
    broadband_type: Mapped[str] = mapped_column(String(32), default="个人")  # 宽带类型
    community_name: Mapped[str] = mapped_column(String(255), default="")     # 小区名称（宽表-装机回填）
    grid: Mapped[str] = mapped_column(String(128), default="")              # 所属网格（宽表-装机回填）
    service_number: Mapped[str] = mapped_column(String(64), default="")     # 服务号码（宽表-装机回填）
    raw_data: Mapped[str] = mapped_column(Text, default="{}")


class CancelRescheduleOrder(Base):
    """退单 / 改约工单（手册 5.2）"""
    __tablename__ = "cancel_reschedule_orders"
    __table_args__ = (Index("ix_cro_month", "month"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[str] = mapped_column(String(64), default="")
    type: Mapped[str] = mapped_column(String(16), default="")               # 退单 / 改约
    district_id: Mapped[int | None] = mapped_column(ForeignKey("districts.id"), nullable=True)
    handler: Mapped[str] = mapped_column(String(64), default="")
    accept_time: Mapped[str] = mapped_column(String(32), default="")
    month: Mapped[str] = mapped_column(String(7), Computed("substr(accept_time, 1, 7)"))
    reason: Mapped[str] = mapped_column(String(255), default="")


class ExtensionOrder(Base):
    """延伸服务订单（手册 5.4）"""
    __tablename__ = "extension_orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(64), default="")
    district: Mapped[str] = mapped_column(String(64), default="")
    handler: Mapped[str] = mapped_column(String(64), default="")
    accept_time: Mapped[str] = mapped_column(String(32), default="")
    service_item: Mapped[str] = mapped_column(String(64), default="")
    income: Mapped[float] = mapped_column(Float, default=0.0)
    month: Mapped[str] = mapped_column(String(7), default="")               # YYYY-MM，从受理时间推导
    __table_args__ = (Index("ix_ext_month", "month"),)


class ExtensionMember(Base):
    """延伸服务人员（手册 5.4，仅四维度）"""
    __tablename__ = "extension_members"
    id: Mapped[int] = mapped_column(primary_key=True)
    district: Mapped[str] = mapped_column(String(64), default="")
    grid: Mapped[str] = mapped_column(String(64), default="")
    name: Mapped[str] = mapped_column(String(64), default="")
    position: Mapped[str] = mapped_column(String(64), default="")


class H5Complaint(Base):
    """投诉 H5 工单（手册 5.2）"""
    __tablename__ = "h5_complaint"
    __table_args__ = (
        Index("ix_hc_month", "month"),
        Index("ix_hc_status", "order_status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    eoms_order_no: Mapped[str] = mapped_column(String(64), unique=True)     # HB-058 前缀
    order_status: Mapped[str] = mapped_column(String(32), default="")
    construction_dept_person: Mapped[str] = mapped_column(String(64), default="")
    district: Mapped[str] = mapped_column(String(64), default="洪湖市")
    accept_time: Mapped[str] = mapped_column(String(32), default="")
    # month 生成列：由 accept_time 前 7 位推导（与 work_orders 同口径）
    month: Mapped[str] = mapped_column(String(7), Computed("substr(accept_time, 1, 7)"))
    community_name: Mapped[str] = mapped_column(String(255), default="")     # 小区名称（宽表-投诉回填）
    contact_person: Mapped[str] = mapped_column(String(64), default="")      # 联系人（宽表-投诉回填）
    fault_number: Mapped[str] = mapped_column(String(64), default="")        # 故障号码（宽表-投诉回填）
    grid: Mapped[str] = mapped_column(String(128), default="")               # 所属网格
    raw_data: Mapped[str] = mapped_column(Text, default="{}")


class H5Install(Base):
    """装机 H5 工单（手册 5.2）"""
    __tablename__ = "h5_install"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(64), default="")
    team_leader: Mapped[str] = mapped_column(String(64), default="")
    constructor: Mapped[str] = mapped_column(String(64), default="")
    date: Mapped[str] = mapped_column(String(32), default="")
    h5_received: Mapped[str] = mapped_column(String(32), default="")
    raw_data: Mapped[str] = mapped_column(Text, default="{}")


class H5Receipt(Base):
    """H5 回执状态表（手册 5.5，order_no+kind 唯一）"""
    __tablename__ = "h5_receipts"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16))                           # install / complaint
    h5_received: Mapped[str] = mapped_column(String(32), default="")        # 已参评 / ''
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_cn, onupdate=now_cn)
    __table_args__ = (UniqueConstraint("order_no", "kind", name="uq_receipt_order_kind"),)


class H5Session(Base):
    """H5 上传批次（手册 5.5）"""
    __tablename__ = "h5_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    session: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16))
    filename: Mapped[str] = mapped_column(String(255), default="")
    rows: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_cn)


class Task(Base):
    """任务分派（手册 7.9 全流程）"""
    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_task_status", "status"),
        Index("ix_task_assignee", "assignee_id"),
        Index("ix_task_district", "district_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(128))
    content: Mapped[str] = mapped_column(Text, default="")
    district_id: Mapped[int | None] = mapped_column(ForeignKey("districts.id"), nullable=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="created")      # created/assigned/accepted/submitted/completed/rejected
    priority: Mapped[str] = mapped_column(String(16), default="normal")     # low/normal/high/urgent
    deadline: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_cn)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_cn, onupdate=now_cn)


class TaskLog(Base):
    __tablename__ = "task_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(32))
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_cn)


class Notice(Base):
    """通知（手册 7.10）"""
    __tablename__ = "notices"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(128))
    content: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(64), default="综合")
    status: Mapped[str] = mapped_column(String(16), default="draft")        # draft/published/recalled
    require_sign: Mapped[bool] = mapped_column(Boolean, default=True)
    publisher_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_cn)
    publish_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class NoticeSignature(Base):
    __tablename__ = "notice_signatures"
    id: Mapped[int] = mapped_column(primary_key=True)
    notice_id: Mapped[int] = mapped_column(ForeignKey("notices.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    signed_at: Mapped[datetime] = mapped_column(DateTime, default=now_cn)
    __table_args__ = (UniqueConstraint("notice_id", "user_id", name="uq_sig_notice_user"),)


class MetricDefinition(Base):
    """指标定义（手册 5.3，5 个）"""
    __tablename__ = "metrics_definition"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(64))
    weight: Mapped[float] = mapped_column(Float, default=0.2)
    max_score: Mapped[float] = mapped_column(Float, default=100)
    formula: Mapped[str] = mapped_column(Text, default="")


class MetricSummary(Base):
    """综合得分汇总（手册 5.3）"""
    __tablename__ = "metrics_summary"
    id: Mapped[int] = mapped_column(primary_key=True)
    period: Mapped[str] = mapped_column(String(7))                          # YYYY-MM
    district_id: Mapped[int | None] = mapped_column(ForeignKey("districts.id"), nullable=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    metric_code: Mapped[str] = mapped_column(String(64), default="")
    raw_value: Mapped[float] = mapped_column(Float, default=0.0)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    total_score: Mapped[float] = mapped_column(Float, default=0.0)
    rank: Mapped[int] = mapped_column(Integer, default=0)
    is_shortcoming: Mapped[bool] = mapped_column(Boolean, default=False)


class SyncJob(Base):
    """数据同步批次（手册 7.11）"""
    __tablename__ = "sync_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[str] = mapped_column(String(64), unique=True)
    metric: Mapped[str] = mapped_column(String(32))
    filename: Mapped[str] = mapped_column(String(255), default="")
    rows: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="preview")      # preview/confirmed
    preview_data: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_cn)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class CompletionRecord(Base):
    """竣工率激励通报（人员粒度，来自通报表「激励明细」sheet，按月替换）"""
    __tablename__ = "completion_records"
    __table_args__ = (UniqueConstraint("month", "handler", "district", name="uq_completion_month_person"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    month: Mapped[str] = mapped_column(String(7), index=True)               # YYYY-MM
    handler: Mapped[str] = mapped_column(String(64))                        # 装维人员
    district: Mapped[str] = mapped_column(String(32), default="")           # 区县
    bb_orders: Mapped[int] = mapped_column(Integer, default=0)              # 宽带收单
    bb_done: Mapped[int] = mapped_column(Integer, default=0)                # 宽带回单
    fttr_orders: Mapped[int] = mapped_column(Integer, default=0)            # FTTR收单
    fttr_done: Mapped[int] = mapped_column(Integer, default=0)              # FTTR回单
    incentive: Mapped[float] = mapped_column(Float, default=0.0)            # 激励后金额(元)


class InstallOrder(Base):
    """装机工单详情（来自「宽表查询」装机宽表 / 综合查询，按月替换）"""
    __tablename__ = "install_orders"
    order_id: Mapped[str] = mapped_column(String(64), primary_key=True)   # 定单编码（27开头）
    month: Mapped[str] = mapped_column(String(7), index=True, default="")
    district: Mapped[str] = mapped_column(String(32), default="")
    grid: Mapped[str] = mapped_column(String(64), default="")
    accept_time: Mapped[str] = mapped_column(String(32), default="")
    order_theme: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(32), default="")            # 已施工/已退单/处理中...
    broadband_type: Mapped[str] = mapped_column(String(16), default="")
    is_fttr: Mapped[str] = mapped_column(String(8), default="")
    handler: Mapped[str] = mapped_column(String(64), default="")           # 回单人员/施工人
    completion_time: Mapped[str] = mapped_column(String(32), default="")  # 回单时间
    is_24h: Mapped[str] = mapped_column(String(8), default="")
    is_8h: Mapped[str] = mapped_column(String(8), default="")
    is_perform: Mapped[str] = mapped_column(String(8), default="")
    is_refund: Mapped[str] = mapped_column(String(8), default="")
    refund_reason: Mapped[str] = mapped_column(String(64), default="")
    install_duration: Mapped[float] = mapped_column(Float, default=0.0)
    completion_duration: Mapped[float] = mapped_column(Float, default=0.0)
    h5_satisfaction: Mapped[str] = mapped_column(String(16), default="")
    appointment_time: Mapped[str] = mapped_column(String(32), default="")   # 预约截止时间
    is_high_quality: Mapped[str] = mapped_column(String(128), default="")   # 高品质标签
    current_step: Mapped[str] = mapped_column(String(32), default="")       # 当前环节
    # ---- 竣工率盯控维度（装机宽表回填，供盯控卡片使用）----
    first_contact_time: Mapped[str] = mapped_column(String(32), default="")     # AL 首联时间
    first_contact_timely: Mapped[str] = mapped_column(String(8), default="")    # AD 首联是否及时
    visit_time: Mapped[str] = mapped_column(String(32), default="")             # AO 上门时间
    visit_timely: Mapped[str] = mapped_column(String(8), default="")            # AV 是否及时上门
    activate_time: Mapped[str] = mapped_column(String(32), default="")          # AQ 首次激活时间
    revisit_time: Mapped[str] = mapped_column(String(32), default="")           # AS 回访时间
    revisit_result: Mapped[str] = mapped_column(String(16), default="")         # AT 回访结果
    ivr_pass: Mapped[str] = mapped_column(String(8), default="")                # BF IVR人工回访是否装通
    ivr_satisfied: Mapped[str] = mapped_column(String(8), default="")           # BG IVR人工回访是否满意
    ivr_score: Mapped[str] = mapped_column(String(16), default="")              # BH IVR人工回访满意度评分
    ivr_final: Mapped[str] = mapped_column(String(8), default="")               # BI IVR人工回访最终是否满意
    urge_cnt: Mapped[int] = mapped_column(Integer, default=0)                   # BJ 催单次数
    refund_reason2: Mapped[str] = mapped_column(String(64), default="")         # BN 二级退单原因
    refund_person: Mapped[str] = mapped_column(String(64), default="")          # BQ 退单人员
    ai_result: Mapped[str] = mapped_column(String(16), default="")              # BV AI回访结果
    zongdiao_result: Mapped[str] = mapped_column(String(16), default="")        # BY 综调回访结果
    city_result: Mapped[str] = mapped_column(String(16), default="")            # CB 地市回访结果
    # ---- H5 短信盯控所需（v1.6.8，宽表自带，不再依赖独立上传口）----
    community_name: Mapped[str] = mapped_column(String(255), default="")   # 所属小区
    service_number: Mapped[str] = mapped_column(String(64), default="")     # 服务号码


class ComplaintOrder(Base):
    """投诉工单详情（来自「宽表查询-投诉」/ 家宽投诉综合工单列表，按月替换）"""
    __tablename__ = "complaint_orders"
    eoms_no: Mapped[str] = mapped_column(String(64), primary_key=True)     # 工单流水号 / EOMS工单号
    month: Mapped[str] = mapped_column(String(7), index=True, default="")
    district: Mapped[str] = mapped_column(String(32), default="")
    accept_time: Mapped[str] = mapped_column(String(32), default="")       # 投诉时间
    status: Mapped[str] = mapped_column(String(32), default="")            # 智慧装维工单状态
    handler: Mapped[str] = mapped_column(String(64), default="")           # 当前处理人/最终回单人员
    complaint_category: Mapped[str] = mapped_column(String(255), default="")
    is_24h: Mapped[str] = mapped_column(String(8), default="")
    is_8h: Mapped[str] = mapped_column(String(8), default="")
    is_perform: Mapped[str] = mapped_column(String(8), default="")
    order_duration: Mapped[float] = mapped_column(Float, default=0.0)
    is_solved: Mapped[str] = mapped_column(String(16), default="")         # 故障是否解决
    satisfaction: Mapped[str] = mapped_column(String(16), default="")      # 归档满意度
    appointment_time: Mapped[str] = mapped_column(String(32), default="")  # 预约截止时间
    grid: Mapped[str] = mapped_column(String(64), default="")              # 网格
    is_high_quality: Mapped[str] = mapped_column(String(8), default="")    # 是否高品质
    global_pass: Mapped[str] = mapped_column(String(32), default="")       # 全球通标识
    # ---- H5 短信盯控所需（v1.6.8，从宽表投诉内容解析）----
    community_name: Mapped[str] = mapped_column(String(255), default="")   # 装机地址/所属小区
    contact_person: Mapped[str] = mapped_column(String(64), default="")     # 受理电话号码
    fault_number: Mapped[str] = mapped_column(String(64), default="")       # 主叫号码


class Setting(Base):
    """系统参数（key-value，如每月人均延伸任务 ext_target_YYYY-MM）"""
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_cn, onupdate=now_cn)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), default="")
    action: Mapped[str] = mapped_column(String(64), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_cn)


class Alert(Base):
    """工单/预警（手册 5.2 work_order_alerts 简化）"""
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(32), default="")               # pending_workorder / h5_overdue
    order_no: Mapped[str] = mapped_column(String(64), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    level: Mapped[str] = mapped_column(String(16), default="warning")       # info/warning/danger
    is_resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_cn)


class AlertMeta(Base):
    """省公司邮件预警快照元数据（只保留最新一次拉取）"""
    __tablename__ = "alert_meta"
    id: Mapped[int] = mapped_column(primary_key=True)
    mail_subject: Mapped[str] = mapped_column(String(128), default="")      # 如 待处理工单预警10.18-17点
    mail_time: Mapped[str] = mapped_column(String(32), default="")          # 邮件时间(数据时间)
    export_time: Mapped[str] = mapped_column(String(32), default="")        # 附件导出时间
    fetched_at: Mapped[str] = mapped_column(String(32), default="")         # 本系统拉取时间
    install_total: Mapped[int] = mapped_column(default=0)                   # 荆州待装
    complaint_total: Mapped[int] = mapped_column(default=0)                 # 荆州投诉待处理
    book_abnormal: Mapped[int] = mapped_column(default=0)                   # 预约异常
    warn_yellow: Mapped[int] = mapped_column(default=0)                     # 黄色告警人员
    warn_red: Mapped[int] = mapped_column(default=0)                        # 红色告警人员


class AlertOrder(Base):
    """省公司邮件预警明细（type: install装机/complaint投诉/book预约异常/monitor人员预警）"""
    __tablename__ = "alert_orders"
    __table_args__ = (Index("ix_alert_type_district", "type", "district"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(16), default="")               # install/complaint/book/monitor
    district: Mapped[str] = mapped_column(String(32), default="")
    grid: Mapped[str] = mapped_column(String(64), default="")
    handler: Mapped[str] = mapped_column(String(64), default="")
    order_no: Mapped[str] = mapped_column(String(64), default="")           # 定单编码
    service_no: Mapped[str] = mapped_column(String(32), default="")         # 服务号码
    order_type: Mapped[str] = mapped_column(String(32), default="")         # 工单类型(家庭宽带/商客宽带)
    status: Mapped[str] = mapped_column(String(32), default="")             # 工单状态(待地址确认/待施工…)
    community: Mapped[str] = mapped_column(String(128), default="")         # 小区名称
    is_fttr: Mapped[str] = mapped_column(String(8), default="")
    is_high_quality: Mapped[str] = mapped_column(String(8), default="")
    accept_time: Mapped[str] = mapped_column(String(32), default="")        # 受理时间
    appoint_time: Mapped[str] = mapped_column(String(32), default="")       # 预约时间
    visit_time: Mapped[str] = mapped_column(String(32), default="")         # 上门时间
    punch_time: Mapped[str] = mapped_column(String(32), default="")         # 最后打卡时间
    pkg_name: Mapped[str] = mapped_column(String(128), default="")          # 智能组网套餐名称
    book_status: Mapped[str] = mapped_column(String(32), default="")        # 预约状态(超时/1小时/2小时/冲突)
    supervise_info: Mapped[str] = mapped_column(String(128), default="")    # 督办信息
    supervise_person: Mapped[str] = mapped_column(String(64), default="")   # 督办人员
    warn_level: Mapped[str] = mapped_column(String(16), default="")         # 预警级别
    warn_time: Mapped[str] = mapped_column(String(32), default="")          # 预警时间
    order_count: Mapped[int] = mapped_column(default=0)                     # 人员工单量


class ReportMeta(Base):
    """省公司【重要通报】快照元数据（只保留最新一次）"""
    __tablename__ = "report_meta"
    id: Mapped[int] = mapped_column(primary_key=True)
    mail_subject: Mapped[str] = mapped_column(String(128), default="")      # 【重要通报】荆州各区县家宽装维通报-20261015
    data_date: Mapped[str] = mapped_column(String(16), default="")          # 数据截至 2026-10-15
    mail_time: Mapped[str] = mapped_column(String(32), default="")          # 收件时间
    fetched_at: Mapped[str] = mapped_column(String(32), default="")


class ReportDistrict(Base):
    """重要通报-区县级指标（kind: install/h5_install/complaint/h5_complaint/dup，data 为 JSON）"""
    __tablename__ = "report_district"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), default="")
    district: Mapped[str] = mapped_column(String(32), default="")
    data: Mapped[str] = mapped_column(Text, default="")                     # JSON


class ReportTimeout(Base):
    """重要通报-超时工单盯控明细（预约时间内未完成）"""
    __tablename__ = "report_timeouts"
    __table_args__ = (Index("ix_rt_district", "district"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    district: Mapped[str] = mapped_column(String(32), default="")
    order_no: Mapped[str] = mapped_column(String(64), default="")
    theme: Mapped[str] = mapped_column(String(128), default="")
    accept_time: Mapped[str] = mapped_column(String(32), default="")
    appoint_end: Mapped[str] = mapped_column(String(32), default="")        # 预约结束时间
    step: Mapped[str] = mapped_column(String(32), default="")               # 当前环节
    handler: Mapped[str] = mapped_column(String(64), default="")
    phone: Mapped[str] = mapped_column(String(32), default="")
    ctrl_type: Mapped[str] = mapped_column(String(64), default="")          # 预约盯控类型


class ReportWideOrder(Base):
    """重要通报-装机宽表明细（用于上门不及时等下钻）"""
    __tablename__ = "report_wide_orders"
    __table_args__ = (Index("ix_rwo_district_timely", "district", "visit_timely"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    district: Mapped[str] = mapped_column(String(32), default="")
    grid: Mapped[str] = mapped_column(String(64), default="")
    order_no: Mapped[str] = mapped_column(String(64), default="")           # 定单编码
    theme: Mapped[str] = mapped_column(String(128), default="")             # 业务主题
    broadband_type: Mapped[str] = mapped_column(String(16), default="")
    status: Mapped[str] = mapped_column(String(32), default="")             # 定单状态
    step: Mapped[str] = mapped_column(String(32), default="")               # 当前环节
    accept_time: Mapped[str] = mapped_column(String(32), default="")
    appoint_end: Mapped[str] = mapped_column(String(32), default="")        # 预约结束时间
    handler: Mapped[str] = mapped_column(String(64), default="")            # 当前处理人或回单人
    is_timely: Mapped[str] = mapped_column(String(8), default="")           # 是否及时(竣工)
    visit_timely: Mapped[str] = mapped_column(String(8), default="")        # 是否及时上门(1=不及时)
    fttr_over: Mapped[str] = mapped_column(String(8), default="")           # FTTR超2.5小时
    is_high_quality: Mapped[str] = mapped_column(String(64), default="")


class ReportComplaintOrder(Base):
    """重要通报-投诉受理明细（受理 sheet，含当日修及时性/全球通标识）"""
    __tablename__ = "report_complaint_orders"
    __table_args__ = (Index("ix_rco_district", "district"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    district: Mapped[str] = mapped_column(String(32), default="")
    flow_no: Mapped[str] = mapped_column(String(64), default="")            # 工单流水号
    theme: Mapped[str] = mapped_column(String(255), default="")             # 投诉主题
    category: Mapped[str] = mapped_column(String(128), default="")          # 投诉分类
    accept_time: Mapped[str] = mapped_column(String(32), default="")        # 投诉时间
    status: Mapped[str] = mapped_column(String(32), default="")             # 智慧装维工单状态
    handler: Mapped[str] = mapped_column(String(64), default="")            # 当前处理人
    phone: Mapped[str] = mapped_column(String(32), default="")
    grid: Mapped[str] = mapped_column(String(64), default="")               # 最终回单网格
    duration: Mapped[str] = mapped_column(String(16), default="")           # 投诉处理时长(h)
    visit_timely: Mapped[str] = mapped_column(String(8), default="")        # 是否上门及时
    day_timely: Mapped[str] = mapped_column(String(8), default="")          # 当日修是否及时
    global_pass: Mapped[str] = mapped_column(String(32), default="")        # 全球通标识
    is_high_quality: Mapped[str] = mapped_column(String(8), default="")
    account: Mapped[str] = mapped_column(String(32), default="")            # 宽带帐号


class ReportH5Record(Base):
    """重要通报-投诉H5参评记录（H5清单，用于人员参评率统计）"""
    __tablename__ = "report_h5_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(8), default="投诉")
    district: Mapped[str] = mapped_column(String(32), default="")
    staff: Mapped[str] = mapped_column(String(64), default="")              # 装维人员姓名
    staff_id: Mapped[str] = mapped_column(String(32), default="")
    order_no: Mapped[str] = mapped_column(String(64), default="")           # 装维工单编码
    phone: Mapped[str] = mapped_column(String(32), default="")              # 目标手机号码
    send_time: Mapped[str] = mapped_column(String(32), default="")          # 下发时间
    reply_time: Mapped[str] = mapped_column(String(32), default="")         # 回复时间(空=未参评)


class ReportDupOrder(Base):
    """重要通报-广义重复投诉工单（广义分子 sheet）"""
    __tablename__ = "report_dup_orders"
    __table_args__ = (Index("ix_rdo_district", "district"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    district: Mapped[str] = mapped_column(String(32), default="")
    order_no: Mapped[str] = mapped_column(String(64), default="")           # 工单编号
    account: Mapped[str] = mapped_column(String(32), default="")            # 宽带账号
    dup_cnt: Mapped[str] = mapped_column(String(8), default="")             # 重复投诉次数
    reason: Mapped[str] = mapped_column(String(255), default="")            # 广义投诉七级分类
    first_time: Mapped[str] = mapped_column(String(32), default="")         # 第一次投诉时间
    first_reason: Mapped[str] = mapped_column(String(255), default="")
    second_time: Mapped[str] = mapped_column(String(32), default="")        # 第二次投诉时间
    second_reason: Mapped[str] = mapped_column(String(255), default="")
    handler: Mapped[str] = mapped_column(String(64), default="")            # 当前处理人
    grid: Mapped[str] = mapped_column(String(64), default="")               # 最终回单网格


class H5SurveyDistrict(Base):
    """H5满意度通报（集团大音平台）- 区县汇总（装机/投诉两段，快照全量替换）
    新版平台导出（人员参与率+原始详单/回单）由本地聚合出 当日/当月 两个周期；
    旧版「H5数据统计」sheet 仅含当月累计（period='当月'）。"""
    __tablename__ = "h5_survey_districts"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(8), default="")                # 装机 / 投诉
    period: Mapped[str] = mapped_column(String(8), default="当月")          # 当日 / 当月
    district: Mapped[str] = mapped_column(String(32), default="")
    back_cnt: Mapped[int] = mapped_column(Integer, default=0)               # 回单量
    survey_cnt: Mapped[int] = mapped_column(Integer, default=0)             # H5测评数
    satisfaction: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 满意度 %（无测评→None）
    q_timely: Mapped[Optional[float]] = mapped_column(Float, nullable=True) # Q24/Q27 上门及时性 %
    q_service: Mapped[Optional[float]] = mapped_column(Float, nullable=True)# Q25/Q28 人员服务 %
    q_effect: Mapped[Optional[float]] = mapped_column(Float, nullable=True) # Q26/Q29 问题解决/安装效果 %
    fttr_sat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # FTTR满意度(仅装机；新版导出无此列→None)
    join_rate: Mapped[float] = mapped_column(Float, default=0.0)            # 参与率 %(0-100)
    affect_cnt: Mapped[int] = mapped_column(Integer, default=0)             # 影响客户感知工单数（低分用户数）
    affect_ratio: Mapped[float] = mapped_column(Float, default=0.0)         # 影响占比 %
    unsat_timely: Mapped[int] = mapped_column(Integer, default=0)           # 不满意人数-上门及时（Q1-1 非空，仅装机）
    unsat_service: Mapped[int] = mapped_column(Integer, default=0)          # 不满意人数-人员服务（Q2-1 非空，仅装机）
    unsat_effect: Mapped[int] = mapped_column(Integer, default=0)           # 不满意人数-安装效果（Q3-1 非空，仅装机）
    unsat_image: Mapped[int] = mapped_column(Integer, default=0)            # 形象提升建议人数（Q4 非空，仅装机）
    diff_cnt: Mapped[int] = mapped_column(Integer, default=0)               # 差额（平台口径，新版导出无此列→0）


class H5SurveyStaff(Base):
    """H5满意度通报 - 人员参与率明细（新版平台导出「装机/投诉参与率」sheet；
    驱动「H5参与率通报」图1 人员明细；班组长(grid.leader)查询时关联 extension_members；
    快照全量替换）"""
    __tablename__ = "h5_survey_staff"
    __table_args__ = (Index("ix_h5staff_district", "district"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    district: Mapped[str] = mapped_column(String(32), default="")           # 区县
    grid: Mapped[str] = mapped_column(String(64), default="")               # 网格（回单 sheet 聚合）
    name: Mapped[str] = mapped_column(String(64), default="")               # 装维人员（展示名，含区县/第三方前缀）
    i_back: Mapped[int] = mapped_column(Integer, default=0)                 # 装机回单量（当月）
    i_eval: Mapped[int] = mapped_column(Integer, default=0)                 # 装机H5已测评
    i_short: Mapped[int] = mapped_column(Integer, default=0)                # 50%参与率欠产数 ROUND(测评量-50%×回单量)
    i_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 装机满意度 %（无测评为 None）
    c_back: Mapped[int] = mapped_column(Integer, default=0)                 # 投诉回单量（当月）
    c_eval: Mapped[int] = mapped_column(Integer, default=0)                 # 投诉H5已测评
    c_short: Mapped[int] = mapped_column(Integer, default=0)
    c_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 投诉满意度 %
    no_install: Mapped[int] = mapped_column(Integer, default=0)             # 1=当月无装机回单


class H5SurveyRecord(Base):
    """H5满意度通报 - 测评明细（装机/投诉H5回复详单，快照全量替换）"""
    __tablename__ = "h5_survey_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(8), default="")                # 装机 / 投诉
    district: Mapped[str] = mapped_column(String(32), default="")
    staff: Mapped[str] = mapped_column(String(64), default="")              # 装维人员姓名(剥前缀)
    staff_id: Mapped[str] = mapped_column(String(32), default="")
    order_no: Mapped[str] = mapped_column(String(64), default="")           # 装维工单编码
    phone: Mapped[str] = mapped_column(String(32), default="")
    send_time: Mapped[str] = mapped_column(String(32), default="")
    reply_time: Mapped[str] = mapped_column(String(32), default="")
    is_low: Mapped[str] = mapped_column(String(8), default="")              # 是否低分用户
    q1: Mapped[str] = mapped_column(String(16), default="")                 # 得分(及时性/服务态度)
    q2: Mapped[str] = mapped_column(String(16), default="")                 # 得分(服务/问题解决)
    q3: Mapped[str] = mapped_column(String(16), default="")                 # 得分(安装效果,仅装机)
    remark: Mapped[str] = mapped_column(String(255), default="")            # 不满意原因/留言


class H5LowScore(Base):
    """H5满意度通报 - 低分测评工单核实（快照全量替换）"""
    __tablename__ = "h5_low_scores"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(8), default="")                # 装机 / 投诉
    district: Mapped[str] = mapped_column(String(32), default="")
    order_no: Mapped[str] = mapped_column(String(64), default="")
    score: Mapped[str] = mapped_column(String(16), default="")              # 如 9分
    message: Mapped[str] = mapped_column(String(255), default="")           # 用户留言
    person: Mapped[str] = mapped_column(String(64), default="")             # 责任人
    guide: Mapped[str] = mapped_column(String(16), default="")              # 10分引导(是/否)
    note: Mapped[str] = mapped_column(String(255), default="")              # 回访备注
    assess: Mapped[str] = mapped_column(String(32), default="")             # 考核(如 100元)


class AiVisitIssue(Base):
    """AI装维即访 - 不规范人员通报明细（来自「AI装维即访回访清单」；
    仅取外呼成功且勾选项答案为「不是」的记录，工单×不规范项 一行，按月替换）"""
    __tablename__ = "ai_visit_issues"
    __table_args__ = (Index("ix_aivisit_month", "month"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    month: Mapped[str] = mapped_column(String(7), default="")              # YYYY-MM，从回单时间推导
    kind: Mapped[str] = mapped_column(String(8), default="")               # 装机 / 投诉
    district: Mapped[str] = mapped_column(String(32), default="")
    handler: Mapped[str] = mapped_column(String(64), default="")            # 责任人（回单人员）
    item: Mapped[str] = mapped_column(String(32), default="")               # 不规范项（如 穿鞋套/确认单签字）
    order_no: Mapped[str] = mapped_column(String(64), default="")           # 工单号（定单编码 / 工单流水号）
    completion_time: Mapped[str] = mapped_column(String(32), default="")    # 回单时间


class AiVisitStat(Base):
    """AI装维即访 - 规范率通报（解析「装后即访-通报表」「投诉即访-通报表」；
    区县 × 类型 × 周期（当日/当月）一行，含各勾选项的调研量/不是/占比，按月替换）"""
    __tablename__ = "ai_visit_stats"
    __table_args__ = (Index("ix_aivisitstat_month", "month"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    month: Mapped[str] = mapped_column(String(7), default="")              # YYYY-MM
    date: Mapped[str] = mapped_column(String(10), default="")              # 当日数据日期 YYYY-MM-DD
    period: Mapped[str] = mapped_column(String(8), default="")             # 当日 / 当月
    kind: Mapped[str] = mapped_column(String(8), default="")               # 装机 / 投诉
    district: Mapped[str] = mapped_column(String(32), default="")          # 含「荆州」合计行
    outbound: Mapped[int] = mapped_column(Integer, default=0)              # 外呼量
    connected: Mapped[int] = mapped_column(Integer, default=0)             # 接通量
    problem: Mapped[int] = mapped_column(Integer, default=0)               # 有问题工单量
    problem_rate: Mapped[float] = mapped_column(Float, default=0.0)        # 问题工单占比 %
    rate: Mapped[float] = mapped_column(Float, default=0.0)                # 规范率 %
    metrics: Mapped[str] = mapped_column(Text, default="[]")               # JSON: [{name, cover, wrong, rate}]

class InstallRealtimeStatus(Base):
    """「导出信息 / 装机综合查询」实时工单状态快照（v1.6.5，裁决用，不参与指标）

    为什么单独建表：原来「装机宽表」与「装机综合查询」的 target 都是 install_orders，
    共用 sync_wide_batch 按月替换 → 谁后传谁赢，实时状态和完整列互相冲掉。
    用户口径要的是「两源都留着按时间裁决」，故另存一张窄表（约 2000 行/次，整表替换）。
    """
    __tablename__ = "install_realtime_status"
    __table_args__ = (Index("ix_irt_month", "month"),)

    order_id: Mapped[str] = mapped_column(String(64), primary_key=True)   # 定单编码
    month: Mapped[str] = mapped_column(String(7), default="")
    district: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(32), default="")           # 工单状态（实时）
    dispatch_time: Mapped[str] = mapped_column(String(32), default="")    # 施工调度接收时间
    accept_time: Mapped[str] = mapped_column(String(32), default="")
    handler: Mapped[str] = mapped_column(String(64), default="")
    refund_reason: Mapped[str] = mapped_column(String(255), default="")
    remark: Mapped[str] = mapped_column(String(255), default="")          # 退单备注信息
    batch_id: Mapped[int] = mapped_column(Integer, default=0)
    fetched_at: Mapped[str] = mapped_column(String(32), default="")       # 这份导出何时入库


class ComplaintRealtimeStatus(Base):
    """「家宽投诉综合工单列表 / 投诉综合查询」实时状态快照（v1.6.7，裁决用，不参与指标）

    与 ``InstallRealtimeStatus`` 同因同解：原来「投诉宽表」与「投诉综合查询」的 target
    都是 ``complaint_orders``，共用 ``sync_wide_batch`` 按月替换 → 谁后传谁赢。

    | 上传口 | 有的列 | 后果 |
    |---|---|---|
    | 投诉宽表 | 24小时/8小时是否及时、是否履约、工单时长、是否解决、归档满意度、是否高品质、全球通 | 被综合查询冲成空 |
    | 投诉综合查询 | 实时工单状态、当前处理人、投诉分类 | 被宽表冲掉实时状态 |

    `是否高品质` 被冲成空会让看板红标没数据；`global_pass` 被冲成空会让全球通卡片归零。
    故本表只存裁决需要的窄字段，整表替换，**不进任何指标**。
    """
    __tablename__ = "complaint_realtime_status"
    __table_args__ = (Index("ix_crt_month", "month"),)

    eoms_no: Mapped[str] = mapped_column(String(64), primary_key=True)     # EOMS工单号
    month: Mapped[str] = mapped_column(String(7), default="")
    district: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(32), default="")           # 工单状态（实时）
    dispatch_time: Mapped[str] = mapped_column(String(32), default="")    # 受理/派单接收时间
    accept_time: Mapped[str] = mapped_column(String(32), default="")
    handler: Mapped[str] = mapped_column(String(64), default="")
    category: Mapped[str] = mapped_column(String(255), default="")        # 投诉分类
    remark: Mapped[str] = mapped_column(String(255), default="")
    batch_id: Mapped[int] = mapped_column(Integer, default=0)
    fetched_at: Mapped[str] = mapped_column(String(32), default="")
