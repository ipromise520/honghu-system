"""演示种子数据生成（本地运行版）—— 数据口径与开发手册一致"""
import json
import random
from datetime import datetime, timedelta

from .database import SessionLocal, engine, Base
from .models import (Role, District, User, WorkOrder, CancelRescheduleOrder, ExtensionOrder,
                     ExtensionMember, H5Complaint, H5Receipt, Task, TaskLog, Notice,
                     NoticeSignature, MetricDefinition, Alert)
from .security import hash_password

random.seed(2026)

DISTRICTS = ["洪湖市", "监利市", "公安县", "荆州区", "江陵县", "松滋市", "沙市区", "石首市", "荆州开发区"]

SURNAMES = "王李张刘陈杨赵黄周吴徐孙马朱胡郭何高林郑谢罗宋唐韩冯于董萧程曹袁邓许傅沈曾彭吕"
GIVEN = ["伟", "芳", "娜", "敏", "静", "磊", "军", "洋", "勇", "艳", "杰", "涛", "明", "超", "秀兰", "霞", "平", "刚", "桂英", "华", "强", "辉", "斌", "俊", "峰", "波", "翔", "宇", "浩", "凯"]

STATUS_POOL = (["已施工"] * 78 + ["处理中"] * 6 + ["待施工"] * 4 + ["待预约"] * 4 +
               ["待智能预约"] * 3 + ["待地址确认"] * 2 + ["流程未启动"] * 2 + ["驳回中"] * 1)
ORDER_THEMES = ["新装宽带", "宽带移机", "FTTR升级", "装机+电视", "融合新装", "宽带续装", "千兆升级"]
SERVICE_ITEMS = ["安防摄像头", "FTTR全屋WiFi", "电视会员", "云盘业务", "智能组网", "家庭安防包"]

MONTHS = ["2026-08", "2026-09", "2026-10"]


def rand_name():
    return random.choice(SURNAMES) + random.choice(GIVEN)


def rand_time_in_month(month: str) -> str:
    y, m = map(int, month.split("-"))
    today = datetime.now()
    max_day = 28 if m == 2 else 30
    if month == today.strftime("%Y-%m"):        # 当月只生成到今天，避免"未来"数据
        max_day = min(max_day, today.day)
    day = random.randint(1, max_day)
    return f"{month}-{day:02d} {random.randint(8, 19):02d}:{random.randint(0, 59):02d}:{random.randint(0, 59):02d}"


def rand_order_id():
    return "27" + "".join(random.choices("0123456789", k=20))


def seed(force: bool = False):
    has_data = False
    try:
        has_data = __import__("sqlalchemy").inspect(engine).has_table("users") and \
            SessionLocal().query(User).count() > 0
    except Exception:
        has_data = False
    if has_data and not force:
        return False

    # 全量重建（演示环境最干净的方式）
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        # 角色
        roles = {"super_admin": Role(code="super_admin", name="市级管理员", level=1),
                 "district_admin": Role(code="district_admin", name="区县级管理员", level=2),
                 "field_worker": Role(code="field_worker", name="装维人员", level=3)}
        db.add_all(roles.values())

        # 区县
        districts = [District(code=f"HH{i:02d}", name=n, city="荆州市",
                              standard_grid=f"洪湖{i}网格") for i, n in enumerate(DISTRICTS, 1)]
        db.add_all(districts)
        db.commit()

        # 用户：admin + 区县管理员 + 装维人员
        users = [User(username="admin", password_hash=hash_password("admin123"), real_name="系统管理员",
                      role_id=roles["super_admin"].id, district_id=None, position="市级管理员")]
        d_names, worker_names = [], []
        for i, d in enumerate(districts):
            dname = rand_name()
            d_names.append(dname)
            users.append(User(username=f"d{i+1:02d}admin", password_hash=hash_password("123456"),
                              real_name=dname, role_id=roles["district_admin"].id,
                              district_id=d.id, position="区县经理"))
            for _ in range(5):
                wname = rand_name()
                worker_names.append((wname, d))
                users.append(User(username=f"w{i+1:02d}{random.randint(100,999)}", password_hash=hash_password("123456"),
                                  real_name=wname, role_id=roles["field_worker"].id, district_id=d.id,
                                  team=f"{d.name}装维班组", grid=d.standard_grid, position=random.choice(["施工员", "维护员", "网格长"])))
        db.add_all(users)
        db.commit()

        # 装机宽表工单（个人口径用于竣工率；order_id 27 开头定单编码）
        orders = []
        for month in MONTHS:
            n_month = 2800 if month != "2026-10" else 1200
            for _ in range(n_month):
                st = random.choice(STATUS_POOL)
                at = rand_time_in_month(month)
                completed = st == "已施工"
                ct = at if completed else ""
                d = random.choice(districts)
                wname = random.choice(worker_names)[0]
                broadband = "个人" if random.random() < 0.85 else "政企"
                orders.append(WorkOrder(
                    order_id=rand_order_id(),
                    order_theme=random.choice(ORDER_THEMES),
                    status=st,
                    accept_time=at,
                    completion_time=ct,
                    district_id=d.id if random.random() > 0.02 else None,
                    construction_dept=wname,
                    broadband_type=broadband,
                    raw_data=json.dumps({"宽带类型": broadband, "定单编码": "27...", "区县": d.name},
                                        ensure_ascii=False)))
        db.add_all(orders)
        db.commit()

        # 退单/改约
        cr = []
        for month in MONTHS:
            for _ in range(280):
                cr.append(CancelRescheduleOrder(
                    order_id=rand_order_id(), type=random.choice(["退单", "改约"]),
                    district_id=random.choice(districts).id,
                    handler=random.choice(worker_names)[0],
                    accept_time=rand_time_in_month(month),
                    reason=random.choice(["用户取消", "地址错误", "无法联系", "时间冲突", "资源不具备"])))
        db.add_all(cr)

        # 延伸服务人员 + 订单
        members = []
        for d in districts:
            for _ in range(random.randint(10, 16)):
                members.append(ExtensionMember(district=d.name, grid=d.standard_grid,
                                               name=rand_name(),
                                               position=random.choice(["施工员", "直销员", "维护员", "网格长"])))
        db.add_all(members)
        ext_orders = []
        for month in MONTHS:
            for _ in range(120):
                m = random.choice(members)
                ext_orders.append(ExtensionOrder(
                    order_no="EXT" + "".join(random.choices("0123456789", k=10)),
                    district=m.district, handler=m.name,
                    accept_time=rand_time_in_month(month),
                    service_item=random.choice(SERVICE_ITEMS),
                    income=round(random.uniform(30, 260), 1),
                    month=month))
        db.add_all(ext_orders)

        # 投诉 H5（HB-058 前缀）+ 回执
        complaints, receipts = [], []
        now = datetime.now()
        seq = 0
        for month in MONTHS:
            for _ in range(140):
                seq += 1
                at_s = rand_time_in_month(month)
                no = f"HB-058-{month.replace('-', '')[2:]}-{seq:05d}"
                st = random.choice(["已施工"] * 7 + ["处理中"] * 2 + ["待施工"])
                complaints.append(H5Complaint(
                    eoms_order_no=no, order_status=st,
                    construction_dept_person=random.choice(worker_names)[0],
                    district="洪湖市", accept_time=at_s,
                    raw_data=json.dumps({"CRM工单编号": "CRM" + str(random.randint(10**8, 10**9))}, ensure_ascii=False)))
                if st == "已施工" and random.random() < 0.8:
                    at_dt = datetime.strptime(at_s, "%Y-%m-%d %H:%M:%S")
                    late = at_dt + timedelta(hours=random.choice([0.5, 1, 2, 3, 5, 10]))
                    receipts.append(H5Receipt(order_no=no, kind="complaint",
                                              h5_received="已参评" if late < now else "",
                                              updated_at=late))
        db.add_all(complaints)

        # 装机 H5 回执（自动派生口径：已施工 + 个人 + 27 开头；整体约 8 成已参评，当月留部分未收用于演示盯控）
        for o in orders:
            if o.status == "已施工" and o.broadband_type == "个人" and random.random() < 0.8:
                try:
                    at_dt = datetime.strptime(o.accept_time, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                late = at_dt + timedelta(hours=random.choice([0.5, 1, 1.5, 2, 3, 6, 12]))
                is_current = o.accept_time.startswith("2026-10")
                received = (late < now) if is_current else True
                receipts.append(H5Receipt(order_no=o.order_id, kind="install",
                                          h5_received="已参评" if received else "",
                                          updated_at=late))
        db.add_all(receipts)

        # 任务
        task_status = ["created", "assigned", "accepted", "submitted", "completed", "rejected", "completed", "completed"]
        for i in range(18):
            d = random.choice(districts)
            creator = users[1 + i % 9]
            assignee = random.choice([u for u in users if u.role_id == roles["field_worker"].id and u.district_id == d.id])
            t = Task(title=random.choice(["弱光整治专项", "投诉压降攻坚", "FTTR营销推广", "存量到期续约回访", "营业网点协同走访"]),
                     content="请按片区要求在期限内完成整改并提交验收材料。",
                     district_id=d.id, creator_id=creator.id, assignee_id=assignee.id,
                     status=task_status[i % len(task_status)],
                     priority=random.choice(["low", "normal", "high", "urgent"]),
                     deadline=(now + timedelta(days=random.randint(1, 14))).strftime("%Y-%m-%d"))
            db.add(t)
            db.flush()
            db.add(TaskLog(task_id=t.id, user_id=creator.id, action="创建任务"))
        db.add_all([TaskLog(task_id=i + 1, user_id=1, action="创建任务") for i in range(0)])

        # 通知
        for i, title in enumerate(["关于开展10月弱光整治的通知", "H5 参评盯控工作要求", "竣工率考核口径说明",
                                   "延伸服务月度目标下达", "安全生产作业规范学习"]):
            n = Notice(title=title, content="请各班组认真落实执行，并及时完成电子签收。",
                       category=random.choice(["考核", "盯控", "综合"]),
                       status="published", require_sign=True, publisher_id=1,
                       publish_time=now - timedelta(days=i + 1))
            db.add(n)
            db.flush()
            for u in users[1:]:
                if random.random() < 0.7:
                    db.add(NoticeSignature(notice_id=n.id, user_id=u.id))

        # 指标定义（5 个，手册 8.4）
        defs = [("timely_rate", "装机及时率", 0.25, "按时竣工工单/受理工单"),
                ("fcr_rate", "一次解决率", 0.20, "一次解决工单/总工单"),
                ("complaint_rate", "百户投诉率", 0.20, "投诉量/用户数(低优)"),
                ("fttr_rate", "FTTR 渗透率", 0.15, "FTTR新装/宽带新装"),
                ("per_capita_output", "人均产值", 0.20, "延伸服务收入/人数")]
        db.add_all([MetricDefinition(code=c, name=n, weight=w, max_score=100, formula=f)
                    for c, n, w, f in defs])

        # 预警样例
        for _ in range(6):
            db.add(Alert(type="pending_workorder", order_no=rand_order_id(),
                         content=random.choice(["待处理工单超过48小时未响应", "投诉工单临近超时"]),
                         level=random.choice(["warning", "danger"])))

        db.commit()
        return True
    finally:
        db.close()
