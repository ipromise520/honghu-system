# -*- coding: utf-8 -*-
"""装维人员端（微信小程序 / 移动 H5）聚合接口

设计要点
--------
1. 登录复用 ``/auth/login``（username 即手机号），本模块只做数据聚合，不新建账号体系。
2. 人员口径：``users.real_name`` → 各业务表 ``handler``。实际数据里 handler 有两种写法：
   纯姓名（``柴达``）与「区县+姓名」（``开发区丁毅``），故用「等于 或 以姓名结尾」匹配，
   **不用 contains**——否则「王刚」会误吃「王刚强」。
3. 网格口径：花名册 ``extension_members.grid``（``洪湖峰口网格``）去掉区县前缀与「网格」后缀
   得到关键词 ``峰口``，再在各表 ``grid`` 上做 contains 匹配。
4. 快超时等级**按预约时间剩余分档**（用户口径「按预约时间区分」）：
   钻石 = 剩余 ≤ 60 分钟（含已逾期）；白金 = 60~240 分钟；普通 = 240~1440 分钟（只进明细）。
   源表 ``alert_orders.warn_level`` 只有空值与「正常单量」，``global_pass`` 仅投诉宽表有，
   装机预警单拿不到客户卡等级，硬套会让两张卡恒为 0。
5. 无数据源的指标返回 ``state="none"``（前端灰显「待接入」）；有源但当期无数据返回
   ``state="empty"``。两者视觉要区分开。
"""
import datetime as _dt
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..status_resolver import (NOT_PENDING, is_pending, realtime_cand, report_cand,
                              resolve, wide_cand, complaint_realtime_cand)
# 「H5回收」与 H5短信盯控同一套应发口径（同一函数），不另写 SQL —— 
# 两端对不上是本项目最高频的 bug 来源，见 AGENTS §三十一/§三十三。
from .h5 import mine_pending as _h5_mine_pending
from ..models import (
    AiVisitIssue, AlertOrder, ComplaintOrder, District, ExtensionMember,
    H5SurveyStaff, InstallOrder, Notice, NoticeSignature, ReportDupOrder,
    ReportTimeout, ReportWideOrder, Task, TaskLog, User,
)

router = APIRouter(prefix="/mp", tags=["mp"])

# 三张订单表统一用「已施工」表示完成（详见项目记忆第十四节）
DONE_ST = ("已施工", "已归档", "已完工", "已回单", "已竣工")
# 「在途待办」判定统一走 status_resolver.NOT_PENDING：
# 除 DONE_ST 外还排除 已撤单/已取消/已作废/已退单/退单中（v1.6.5）。
# 之前只排 DONE_ST，「已撤单」的单被当成在途一直挂在待办里。
REPORT_TABLE_OK = True   # 通报宽表存在时才做跨源裁决（首次部署自动检测）

# 快超时分级阈值（分钟）——用户口径为「按预约时间区分」，集中在此便于调整
LEVEL_DIAMOND = 60
LEVEL_PLATINUM = 240
LEVEL_NORMAL = 1440
# 「工单提前 2 小时预警」窗口（用户口径）：剩余 ≤120 分钟且在途，含已逾期
LEVEL_ALERT = 120
# 超过该窗口的过期单不再纳入（多为历史遗留的僵尸单）
WINDOW_PAST = 48 * 60


# ---------------------------------------------------------------- 基础工具

def _month(m: str) -> str:
    return m or _dt.date.today().strftime("%Y-%m")


def _now() -> _dt.datetime:
    return _dt.datetime.now()


def _h(col, name: str):
    """handler 匹配：等于姓名，或以姓名结尾（兼容「洪湖市柴达」）。"""
    if not name:
        return col == "__no_match__"
    return or_(col == name, col.like("%" + name))


def _grid_key(grid: str, district: str) -> str:
    """``洪湖峰口网格`` + ``洪湖市`` → ``峰口``"""
    g = (grid or "").strip()
    if not g:
        return ""
    for p in (district, district[:-1] if district else ""):
        if p and g.startswith(p):
            g = g[len(p):]
    if g.endswith("网格"):
        g = g[:-2]
    return g.strip()


def _parse_dt(s: str) -> Optional[_dt.datetime]:
    s = (s or "").strip()
    if not s or s.startswith("0000"):
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S"):
        try:
            return _dt.datetime.strptime(s[:len(fmt) + 2].strip(), fmt)
        except ValueError:
            continue
    try:
        return _dt.datetime.fromisoformat(s)
    except ValueError:
        return None


def _hm(ts: Optional[_dt.datetime]) -> str:
    return ts.strftime("%m-%d %H:%M") if ts else ""


def _resolve(db: Session, user: User) -> dict:
    """人员档案：区县名、花名册网格（权威口径）、网格关键词"""
    name = (user.real_name or "").strip()
    member = None
    if name:
        member = db.query(ExtensionMember).filter(ExtensionMember.name == name).first()
    district = ""
    if user.district_id:
        d = db.get(District, user.district_id)
        district = d.name if d else ""
    grid = (member.grid if member else "") or (user.grid or "")
    return {
        "username": user.username,
        "name": name or user.username,
        "phone": user.phone or user.username,
        "district": district,
        "team": user.team or "",
        "grid": grid,
        "grid_key": _grid_key(grid, district),
        "position": user.position or (member.position if member else ""),
        "roster_position": member.position if member else "",
        "in_roster": bool(member),
    }


# ---------------------------------------------------------------- 跨源状态裁决

def _status_maps(db, order_nos):
    """批量取「重要通报」里同单的 status，供裁决使用。

    一次 ``in_()`` 查完，**不要逐单查**——明细列表动辄上百条，逐条查会变成上百次往返。
    报表不存在或没有该单时返回空 dict，调用方按「只有宽表一个源」处理。
    """
    nos = [n for n in {str(x or "").strip() for x in order_nos} if n]
    if not nos or not REPORT_TABLE_OK:
        return {}
    try:
        rows = db.query(ReportWideOrder.order_no, ReportWideOrder.status
                        ).filter(ReportWideOrder.order_no.in_(nos)).all()
    except Exception:
        return {}
    return {r[0]: (r[1] or "") for r in rows}


def _realtime_maps(db, order_nos):
    """批量取「装机综合查询」实时状态。表不存在/未上传过时返回空 dict。"""
    nos = [n for n in {str(x or "").strip() for x in order_nos} if n]
    if not nos:
        return {}
    try:
        from ..models import InstallRealtimeStatus as _IRT
        rs = db.query(_IRT.order_id, _IRT.status, _IRT.dispatch_time
                      ).filter(_IRT.order_id.in_(nos)).all()
    except Exception:
        return {}
    return {r[0]: (r[1] or "", r[2] or "") for r in rs}


def _complaint_realtime_maps(db, eoms_nos):
    """批量取「投诉综合查询」实时状态（v1.6.7 起独立表）。

    返回 ``{eoms_no: (status, dispatch_time)}``；表不存在或还没传过就返回空 dict，
    调用方按「只有投诉宽表一个源」处理，不会报错。
    """
    nos = [n for n in {str(x or "").strip() for x in eoms_nos} if n]
    if not nos:
        return {}
    try:
        from ..models import ComplaintRealtimeStatus as _CRT
        rs = db.query(_CRT.eoms_no, _CRT.status, _CRT.dispatch_time
                      ).filter(_CRT.eoms_no.in_(nos)).all()
    except Exception:
        return {}
    return {r[0]: (r[1] or "", r[2] or "") for r in rs}


def _complaint_report_maps(db, eoms_nos):
    """批量取「重要通报」里同单的投诉状态，供投诉裁决使用。"""
    nos = [n for n in {str(x or "").strip() for x in eoms_nos} if n]
    if not nos or not REPORT_TABLE_OK:
        return {}
    try:
        rows = db.query(ReportWideOrder.order_no, ReportWideOrder.status
                        ).filter(ReportWideOrder.order_no.in_(nos)).all()
    except Exception:
        return {}
    return {r[0]: (r[1] or "") for r in rows}


def _adjudicate_complaint(db, rows, status_of=None):
    """对一批投诉宽表行做跨源裁决，原地写 _st_status / _st_src / _st_note / _st_conflict。

    规则与装机完全相同（时间优先、其次 投诉综合查询 > 投诉宽表 > 通报），
    只是来源名走 `domain="complaint"`。
    """
    status_of = status_of or (lambda r: r.status)
    nos = [getattr(r, "eoms_no", "") for r in rows]
    rep = _complaint_report_maps(db, nos)
    rt = _complaint_realtime_maps(db, nos)
    for r in rows:
        no = getattr(r, "eoms_no", "")
        cands = [wide_cand(status_of(r))]
        if rep.get(no):
            cands.append(report_cand(rep[no]))
        if rt.get(no):
            cands.append(complaint_realtime_cand(rt[no][0], rt[no][1]))
        st = resolve(cands, domain="complaint")
        r._st_status = st["status"]
        r._st_src = st.get("src_name", "")
        r._st_conflict = bool(st.get("conflict"))
        note = st.get("reason", "")
        if st.get("extra"):
            note = (note + "；" if note else "") + st["extra"]
        r._st_note = note
    return rows


def _adjudicate(db, rows, status_of, src_status_of=None, dispatch_of=None):
    """对一批装机宽表行做跨源裁决，原地写入 status / status_src / status_note / status_conflict。

    ``rows``           ORM 行列表
    ``status_of(r)``   宽表状态
    ``src_status_of``  实时源状态（可选，取自运维贴进来的「导出信息」，落库后才有）
    ``dispatch_of``    施工调度接收时间（实时源状态的生效下界）

    三源参与裁决（v1.6.5）：

    | 源 | 表 | 优先级 |
    |---|---|---|
    | 装机综合查询（实时） | ``install_realtime_status`` | 最高——状态最接近当前时刻 |
    | 装机宽表 | ``install_orders`` | 次之——列最全，带退单旁证 |
    | 重要通报 | ``report_wide_orders`` | 末位——每小时刷新的粗粒度快照 |

    ``src_status_of`` / ``dispatch_of`` 保留为可选覆盖（测试或临时比对用），
    正常情况留空，实时源一律从库里的 ``install_realtime_status`` 取。
    """
    nos = [getattr(r, "order_id", "") for r in rows]
    rep = _status_maps(db, nos)
    rt = {} if src_status_of else _realtime_maps(db, nos)
    for r in rows:
        oid = getattr(r, "order_id", "")
        cands = [wide_cand(status_of(r),
                           getattr(r, "is_refund", ""),
                           getattr(r, "refund_reason", ""))]
        if rep.get(oid):
            cands.append(report_cand(rep[oid]))
        if src_status_of:
            cands.append(realtime_cand(src_status_of(r), dispatch_of(r) if dispatch_of else None))
        elif rt.get(oid):
            cands.append(realtime_cand(rt[oid][0], rt[oid][1]))
        st = resolve(cands)
        r._st_status = st["status"]
        r._st_src = st.get("src_name", "")
        r._st_conflict = bool(st.get("conflict"))
        note = st.get("reason", "")
        if st.get("extra"):
            note = (note + "；" if note else "") + st["extra"]
        r._st_note = note
    return rows


def _install_scope(db, sub, name, district, gkey, done: bool):
    """装机工单「筛选 + 裁决 + 裁决后过滤」的**唯一入口**。卡片计数与明细列表都走它。

    为什么必须抽出来（v1.6.6）
    ------------------------
    v1.6.5 的事故：卡片用 `status NOT IN NOT_PENDING` 数出 2 单，
    明细却因为「裁决后发现是已撤单」把这 2 单滤掉了 → 用户看到
    「卡片 2 单，点进去空」。根因**不是某个条件写错，而是两处各写了一遍**：
    卡片只做 SQL 过滤，明细还要过裁决。只要逻辑分两处，迟早再分叉。

    现在两处调用同一个函数，物理上不可能再不一致。

    参数
    ----
    ``done``  True = 本月已施工；False = 在途待办
    返回 ORM 行列表，每行已带 ``_st_status/_st_src/_st_note/_st_conflict``
    """
    q = db.query(InstallOrder)
    q = q.filter(InstallOrder.status.in_(DONE_ST)) if done else q.filter(
        ~InstallOrder.status.in_(list(NOT_PENDING)))
    if not done:
        # 延伸服务/更换光猫是加装类挂靠单，不算「在途装机」（与 metrics.py 对齐）
        for kw in ("延伸服务", "更换光猫"):
            q = q.filter(~InstallOrder.order_theme.contains(kw))
    else:
        q = q.filter(InstallOrder.month == _dt.date.today().strftime("%Y-%m"))
    q = _scope(q, InstallOrder, sub, name, district, gkey)
    if q is None:
        return []
    objs = q.order_by(InstallOrder.accept_time.desc()).limit(2000).all()

    # 空壳行：状态/区县/网格/责任人都空，是导出文件尾部或异常行
    objs = [r for r in objs
            if (r.status or "").strip() or (r.district or "").strip()
            or (r.handler or "").strip() or (r.current_step or "").strip()]
    _adjudicate(db, objs, lambda r: r.status)
    # SQL 层只能按「宽表状态」筛，裁决可能把某单判成已撤单 → 必须按裁决结果再过一遍。
    # 这一步就是 v1.6.5「卡片 2 / 明细 0」的根源，现在放在唯一入口里，两边都吃到。
    if done:
        return [r for r in objs if (r._st_status or "") in DONE_ST]
    return [r for r in objs if is_pending(r._st_status)]


def _repair_scope(db, sub, name, district, gkey, done: bool = False):
    """投诉工单「筛选 + 裁决 + 裁决后过滤」的**唯一入口**。卡片与明细都走它。

    为什么要有这个函数：投诉这条路原来和 v1.6.6 修掉的装机那条一模一样——
    卡片用 SQL 计数、明细另写一个查询。装机那边就是因此出了「卡片 2 单、点进去空」。
    v1.6.7 投诉综合查询落独立表后，实时状态和宽表不一致的单会更多，
    不合并这里必然重演，故一并处理。
    """
    q = db.query(ComplaintOrder)
    if done:
        q = q.filter(ComplaintOrder.status.in_(DONE_ST))
    else:
        q = q.filter(~ComplaintOrder.status.in_(list(NOT_PENDING)))
    q = _scope(q, ComplaintOrder, sub, name, district, gkey)
    if q is None:
        return []
    objs = q.order_by(ComplaintOrder.accept_time.desc()).limit(2000).all()
    # 空壳行（状态/区县/处理人全空）不占待办
    objs = [r for r in objs
            if (r.status or "").strip() or (r.district or "").strip()
            or (r.handler or "").strip()]
    _adjudicate_complaint(db, objs)
    if done:
        return [r for r in objs if (r._st_status or "") in DONE_ST]
    return [r for r in objs if is_pending(r._st_status)]


def _has_grid(model) -> bool:
    return hasattr(model, "grid")


def _scope(q, model, sub: str, name: str, district: str, gkey: str):
    """给查询套上「我的 / 本网格」范围。

    模型没有 ``grid`` 列时（``report_timeouts`` / ``ai_visit_issues``）网格口径**不可算**，
    返回 None，由调用方把该指标降级为空（不要硬 filter，会 AttributeError 直接 500）。
    """
    if sub == "grid":
        if not _has_grid(model):
            return None
        q = q.filter(model.district == district)
        if gkey:
            q = q.filter(model.grid.like("%" + gkey + "%"))
        return q
    return q.filter(_h(model.handler, name))


# ---------------------------------------------------------------- 指标计算

def _open_orders(db, sub, name, district, gkey, month=None):
    """在途装机 / 在途投诉 / 当月完工

    注意：在途类**不按月过滤**（跨月未完成的单仍是待办），只有「当月完工」才限月，
    否则卡片数字会小于明细条数（曾出现卡片 72 / 明细 81 的口径冲突）。
    """
    # v1.6.6/1.6.7：卡片一律 `len(唯一入口)`，不再自己写 SQL 计数。
    # 两端口径必须物理同源 —— 「卡片有数、点进去空」已经踩过两次。
    return {
        "pending_install": len(_install_scope(db, sub, name, district, gkey, False)),
        "pending_repair": len(_repair_scope(db, sub, name, district, gkey, False)),
        "completed_month": len(_install_scope(db, sub, name, district, gkey, True)),
    }


def _near_timeout(db, sub, name, district, gkey):
    """快超时预警：按预约时间剩余分档，返回 {diamond, platinum, normal, rows}"""
    q = db.query(AlertOrder)
    if sub == "grid":
        q = q.filter(AlertOrder.district == district)
        if gkey:
            q = q.filter(AlertOrder.grid.like("%" + gkey + "%"))
    else:
        q = q.filter(_h(AlertOrder.handler, name))
    now = _now()
    out = {"diamond": 0, "platinum": 0, "normal": 0, "rows": []}
    for r in q.all():
        if (r.status or "").strip() in DONE_ST:
            continue
        ts = _parse_dt(r.appoint_time)
        if not ts:
            continue
        left = int((ts - now).total_seconds() // 60)
        if left > LEVEL_NORMAL or left < -WINDOW_PAST:
            continue
        if left <= LEVEL_DIAMOND:
            lvl, lvl_name = "diamond", "钻石"
        elif left <= LEVEL_PLATINUM:
            lvl, lvl_name = "platinum", "白金"
        else:
            lvl, lvl_name = "normal", "普通"
        out[lvl] += 1
        out["rows"].append({
            "no": r.order_no or "",
            "level": lvl,
            "level_name": lvl_name,
            "appoint": _hm(ts),
            "left_min": left,
            "left_text": ("已逾期 %d 分钟" % -left) if left < 0 else ("剩 %d 分钟" % left),
            "status": r.status or "",
            "grid": r.grid or "",
            "community": r.community or "",
            "handler": _short_name(r.handler, r.district),
            "supervise": (r.supervise_person or "").strip(),
            "customer": _customer_level(db, r.order_no),
        })
    out["rows"].sort(key=lambda x: x["left_min"])
    return out


def _short_name(raw: str, district: str = "") -> str:
    """洪湖市刘中华 → 刘中华。表里 handler 多数带区县前缀，去掉便于按人督办。"""
    v = (raw or "").strip()
    if not v:
        return ""
    for p in (district, "洪湖市", "荆州区", "沙市区", "公安县", "监利市",
              "江陵县", "石首市", "松滋市", "洪湖", "荆州开发区"):
        if p and v.startswith(p):
            return v[len(p):].strip()
    return v


def _age_text(age_h) -> str:
    """小时数 → 人话（``3.5小时`` / ``2天4小时``）。"""
    try:
        h = float(age_h or 0)
    except Exception:
        return ""
    if h <= 0:
        return ""
    if h < 24:
        return "%.1f 小时" % h
    return "%d 天 %d 小时" % (int(h // 24), int(h % 24))


def _customer_level(db, order_no: str) -> str:
    """客户卡等级（仅投诉宽表有 global_pass，装机单通常拿不到 → 空串）"""
    if not order_no:
        return ""
    row = db.query(ComplaintOrder.global_pass).filter(ComplaintOrder.eoms_no == order_no).first()
    return (row[0] or "") if row else ""


def _rewards(db, sub, name, district, gkey, month):
    """奖惩类计数 + 安装不规范质检（无 grid 列的指标在网格口径下返回 None）"""
    def simple(model, col):
        q = db.query(func.count(col))
        q = _scope(q, model, sub, name, district, gkey)
        return None if q is None else (q.scalar() or 0)

    timeout = simple(ReportTimeout, ReportTimeout.id)          # 快照表，无 month / 无 grid
    broad = simple(ReportDupOrder, ReportDupOrder.id)          # 快照表，无 month
    ai = simple(AiVisitIssue, AiVisitIssue.id)                 # 无 grid 列

    install_qc = None
    q = db.query(func.count(AiVisitIssue.id)).filter(AiVisitIssue.month == month,
                                                     AiVisitIssue.kind == "装机")
    q = _scope(q, AiVisitIssue, sub, name, district, gkey)
    if q is not None:
        install_qc = q.scalar() or 0

    # H5 参与率：抓取的人员快照行（网格口径也可算）
    q = db.query(H5SurveyStaff)
    if sub == "grid":
        q = q.filter(H5SurveyStaff.district == district)
        if gkey:
            q = q.filter(H5SurveyStaff.grid.like("%" + gkey + "%"))
    else:
        q = q.filter(_h(H5SurveyStaff.name, name))
    rows = q.all()
    i_back = sum(r.i_back or 0 for r in rows)
    i_eval = sum(r.i_eval or 0 for r in rows)
    c_back = sum(r.c_back or 0 for r in rows)
    c_eval = sum(r.c_eval or 0 for r in rows)
    back, ev = i_back + c_back, i_eval + c_eval
    rate = round(ev * 100.0 / back, 1) if back else None
    return {
        "timeout": timeout,
        "broad": broad,
        "ai_issue": ai,
        "install_qc": install_qc,
        "h5_rate": rate,
        "h5_sub": ("装机 %d/%d · 投诉 %d/%d" % (i_eval, i_back, c_eval, c_back)) if back else "",
        "h5_empty": back == 0,
    }


# ---------------------------------------------------------------- 卡片定义

def _card(key, label, value, unit="单", kind=None, level=None, grid=None,
          sub="", state="ok"):
    return {"key": key, "label": label, "value": value, "unit": unit,
            "kind": kind, "level": level, "grid": grid, "sub": sub, "state": state}


@router.get("/profile")
def profile(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    p = _resolve(db, user)
    p.pop("grid_key", None)
    return p


@router.get("/overview")
def overview(month: str = Query(""), scope: str = Query("mine"),
             db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """首页所有卡片的数字，一次请求返回（首屏只发 1 个请求）"""
    sub = "grid" if scope == "grid" else "mine"
    m = _month(month)
    p = _resolve(db, user)
    name, district, gkey = p["name"], p["district"], p["grid_key"]

    mine = _open_orders(db, "mine", name, district, gkey, month=m)
    grid = _open_orders(db, "grid", name, district, gkey, month=m) if gkey else None
    nt_mine = _near_timeout(db, "mine", name, district, gkey)
    nt_grid = _near_timeout(db, "grid", name, district, gkey) if gkey else None
    rw_mine = _rewards(db, "mine", name, district, gkey, m)
    rw_grid = _rewards(db, "grid", name, district, gkey, m) if gkey else None

    def g(d, key):
        return d.get(key) if d else None

    orders = [
        _card("pending_install", "待装工单", mine["pending_install"], kind="pending_install",
              grid=g(grid, "pending_install")),
        _card("pending_repair", "待修工单", mine["pending_repair"], kind="pending_repair",
              grid=g(grid, "pending_repair")),
        _card("near_diamond", "快超时·钻石", nt_mine["diamond"], kind="near_timeout", level="diamond",
              grid=(nt_grid["diamond"] if nt_grid else None), sub="≤60分/已逾期",
              state="ok"),
        _card("near_platinum", "快超时·白金", nt_mine["platinum"], kind="near_timeout", level="platinum",
              grid=(nt_grid["platinum"] if nt_grid else None), sub="1-4小时"),
        _card("completed_month", "本月完工", mine["completed_month"], kind="completed_month",
              grid=g(grid, "completed_month"), sub="%s" % m),
    ]

    h5 = rw_mine["h5_rate"]
    rewards = [
        _card("timeout", "超时工单", rw_mine["timeout"], kind="timeout",
              grid=g(rw_grid, "timeout"), sub="省公司通报"),
        _card("broad", "广义重复投诉", rw_mine["broad"], kind="broad",
              grid=g(rw_grid, "broad")),
        _card("ai_issue", "AI回访不规范", rw_mine["ai_issue"], kind="ai_issue",
              grid=g(rw_grid, "ai_issue")),
        _card("install_qc", "安装不规范质检", rw_mine["install_qc"], kind="install_qc",
              grid=g(rw_grid, "install_qc"), sub="装机类"),
        _card("h5_rate", "H5参与率", h5, unit="%", kind="h5", grid=g(rw_grid, "h5_rate"),
              sub=rw_mine["h5_sub"],
              state="empty" if rw_mine["h5_empty"] else "ok"),
        _card("qc", "工单质检", None, kind="qc", state="none"),
        _card("trace", "工器具追溯", None, kind="trace", state="none"),
    ]

    # 本人名下「本月已完工、已发 H5 短信、客户还没参评」的单（v1.6.9）
    h5_rec = _h5_mine_pending(db, name, district, m) if name else []
    tasks = [
        _card("h5_recover", "H5回收", len(h5_rec), kind="h5_recover",
              sub=("应发未收 %d 单" % len(h5_rec)) if h5_rec else "本月无待回收"),
        _card("port_clear", "端口清虚", None, kind="port_clear", state="none"),
        _card("inspection", "小区巡检", None, kind="inspection", state="none"),
        _card("care", "广义用户关怀", None, kind="care", state="none"),
        _card("xf", "XF修复工单", None, kind="xf", state="none"),
    ]

    total_n, unsign = _notice_count(db, user.id)
    task_open, task_rows = _my_task(db, user.id)
    if task_open:
        _task_sub = "待办 %d 项" % task_open
    elif task_rows:
        _task_sub = "共 %d 项·均已完成" % len(task_rows)
    else:
        _task_sub = "暂无任务"
    notices = [
        _card("my_task", "我的任务", task_open, kind="my_task", sub=_task_sub),
        _card("notice", "待签通知", unsign, kind="notice",
              sub=("共 %d 条" % total_n) if total_n else "暂无通知"),
        _card("knowledge", "知识库", None, unit="", kind="knowledge", state="none"),
    ]

    return {
        "month": m,
        "scope": sub,
        "profile": {k: v for k, v in p.items() if k != "grid_key"},
        "groups": [
            {"key": "orders", "label": "工单", "cards": orders},
            {"key": "rewards", "label": "奖惩", "cards": rewards},
            {"key": "tasks", "label": "专项任务", "cards": tasks},
            {"key": "msg", "label": "消息与通知", "cards": notices},
        ],
    }


def _notice_count(db, uid):
    """返回 (已发布通知数, 本人待签数)。

    待签只统计「必签」通知（require_sign=True）——非必签通知签了也白签，
    算进待签会让数字永远清不掉。签收数用 join 限定在当前已发布集合内，
    避免历史撤回/草稿通知的签收记录把待签数冲成 0（曾出过这种错）。
    """
    pub_ids = [r[0] for r in db.query(Notice.id).filter(Notice.status == "published").all()]
    total = len(pub_ids)
    need = db.query(func.count(Notice.id)).filter(
        Notice.status == "published", Notice.require_sign == True).scalar() or 0  # noqa: E712
    if not pub_ids:
        return 0, 0
    signed = db.query(func.count(NoticeSignature.id)).filter(
        NoticeSignature.user_id == uid,
        NoticeSignature.notice_id.in_(pub_ids),
    ).scalar() or 0
    return total, max(need - signed, 0)


# ---------------------------------------------------------------- 明细

COLS_ORDER = {
    "pending_install": [("no", "工单号", 1), ("handler", "责任人", 0), ("theme", "主题", 0),
                        ("appoint", "预约时间", 0), ("status", "状态", 0), ("grid", "网格", 0)],
    "pending_repair": [("no", "工单号", 1), ("handler", "责任人", 0), ("cat", "投诉类型", 0),
                       ("appoint", "预约时间", 0), ("status", "状态", 0)],
    "near_timeout": [("no", "工单号", 1), ("handler", "责任人", 0), ("level_name", "等级", 0),
                     ("appoint", "预约时间", 0), ("left_text", "剩余", 0), ("customer", "客户等级", 0)],
    "completed_month": [("no", "工单号", 1), ("handler", "责任人", 0), ("theme", "主题", 0),
                        ("appoint", "预约时间", 0), ("status", "状态", 0)],
    # H5回收（v1.6.9）：本人名下已发短信但客户未参评的单，按「已等多久」排序
    # 状态固定是「已施工」（应发前提就是完工），不重复占一列；「已等」才是要盯的东西
    "h5_recover": [("no", "工单号", 1), ("kind_name", "类型", 0), ("community", "小区", 0),
                   ("number", "服务号码", 1), ("back", "回单时间", 0), ("wait", "已等", 0)],
}


@router.get("/orders")
def orders(kind: str = Query("pending_install"), scope: str = Query("mine"),
           level: str = Query(""), month: str = Query(""), page: int = Query(1),
           page_size: int = Query(50),
           db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sub = "grid" if scope == "grid" else "mine"
    p = _resolve(db, user)
    name, district, gkey = p["name"], p["district"], p["grid_key"]
    rows = []

    if kind == "near_timeout":
        data = _near_timeout(db, sub, name, district, gkey)
        rows = data["rows"]
        if level:
            rows = [r for r in rows if r["level"] == level]
    elif kind in ("pending_install", "completed_month"):
        done = kind == "completed_month"
        # 与卡片同一个函数（v1.6.6）：筛选/裁决/裁决后过滤全在里面，
        # 卡片数字与明细条数从此同源，不会再现「卡片 2 单、点进去空」。
        objs = _install_scope(db, sub, name, district, gkey, done)
        rows = [{"no": r.order_id, "theme": r.order_theme or "", "appoint": r.appointment_time or "",
                 "status": r._st_status or r.status or "", "status_src": r._st_src,
                 "status_note": r._st_note, "status_conflict": r._st_conflict,
                 "grid": r.grid or "", "accept": r.accept_time or "",
                 "handler": _short_name(r.handler, r.district)}
                for r in objs]
    elif kind == "pending_repair":
        # 与卡片同一个函数（v1.6.7）
        objs = _repair_scope(db, sub, name, district, gkey, False)
        rows = [{"no": r.eoms_no, "cat": (r.complaint_category or "")[-24:],
                 "appoint": r.appointment_time or "",
                 "status": r._st_status or r.status or "",
                 "status_src": r._st_src, "status_note": r._st_note,
                 "status_conflict": r._st_conflict,
                 "grid": r.grid or "", "accept": r.accept_time or "",
                 "handler": _short_name(r.handler, r.district)} for r in objs]
    elif kind == "h5_recover":
        # 与卡片同一个函数（v1.6.9）：卡片 len() / 明细都走 mine_pending
        items = _h5_mine_pending(db, name, district, _month(month)) if name else []
        rows = [{"no": it.get("order_no", ""),
                 "kind_name": "装机" if it.get("kind") == "install" else "投诉",
                 "community": it.get("community_name", ""),
                 "number": it.get("service_number") or it.get("fault_number", ""),
                 "back": (it.get("back_time") or "")[:16],
                 "wait": _age_text(it.get("age_h")),
                 "wait_days": int((it.get("age_h") or 0) // 24),
                 "status": it.get("status", ""),
                 "handler": _short_name(it.get("handler", ""), district)} for it in items]
    else:
        raise HTTPException(400, "未知的工单类型：%s" % kind)

    total = len(rows)
    start = max((page - 1) * page_size, 0)
    _SRC = {"near_timeout": "省公司预警附件（按预约时间剩余分档）",
            "pending_install": "装机宽表（在途未完工）",
            "pending_repair": "投诉宽表 + 投诉综合查询（在途未完工，跨源裁决）",
            "completed_month": "装机宽表（本月已施工）",
            "h5_recover": "装机宽表 + 投诉宽表（已发 H5 短信、客户未参评；回执来自 H5 满意度通报）"}
    return {"kind": kind, "scope": sub, "total": total, "source": _SRC.get(kind, ""),
            "columns": [{"key": k, "label": lb, "mono": mono} for k, lb, mono in COLS_ORDER[kind]],
            "rows": rows[start:start + page_size]}


@router.get("/rewards")
def rewards(kind: str = Query("timeout"), scope: str = Query("mine"),
            db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sub = "grid" if scope == "grid" else "mine"
    p = _resolve(db, user)
    name, district, gkey = p["name"], p["district"], p["grid_key"]
    month = _dt.date.today().strftime("%Y-%m")

    if kind == "timeout":
        q = _scope(db.query(ReportTimeout), ReportTimeout, sub, name, district, gkey)
        rows = [] if q is None else [
            {"no": r.order_no, "theme": r.theme or "", "appoint": r.appoint_end or "",
             "ctrl": r.ctrl_type or "", "step": r.step or "", "district": r.district or ""}
            for r in q.order_by(ReportTimeout.accept_time.desc()).limit(500).all()]
        cols = [("no", "工单号", 1), ("theme", "主题", 0), ("appoint", "预约截止", 0),
                ("ctrl", "盯控类型", 0), ("step", "环节", 0)]
        source = "省公司通报快照（无月份维度，无法按网格拆）"
    elif kind == "broad":
        q = _scope(db.query(ReportDupOrder), ReportDupOrder, sub, name, district, gkey)
        rows = [] if q is None else [
            {"no": r.order_no, "account": r.account or "", "cnt": r.dup_cnt or "",
             "reason": (r.reason or "")[-30:], "first": r.first_time or "",
             "second": r.second_time or ""}
            for r in q.limit(500).all()]
        cols = [("no", "工单号", 1), ("account", "宽带账号", 0), ("cnt", "重复次数", 0),
                ("reason", "投诉分类", 0), ("second", "最近投诉", 0)]
        source = "广义重复投诉快照（无月份维度）"
    elif kind in ("ai_issue", "install_qc"):
        q = db.query(AiVisitIssue).filter(AiVisitIssue.month == month)
        if kind == "install_qc":
            q = q.filter(AiVisitIssue.kind == "装机")
        q = _scope(q, AiVisitIssue, sub, name, district, gkey)
        rows = [] if q is None else [
            {"no": r.order_no, "kind": r.kind or "", "item": r.item or "",
             "time": r.completion_time or "", "district": r.district or ""}
            for r in q.order_by(AiVisitIssue.completion_time.desc()).limit(500).all()]
        cols = [("no", "工单号", 1), ("kind", "业务", 0), ("item", "不规范项", 0), ("time", "回单时间", 0)]
        source = "AI装维即访明细（%s）" % month
    elif kind == "h5":
        rw = _rewards(db, sub, name, district, gkey, month)
        rows = [{"label": "H5 参与率", "value": ("%s%%" % rw["h5_rate"]) if rw["h5_rate"] is not None else "—"},
                {"label": "装机 测评/回单", "value": rw["h5_sub"] or "—"},
                {"label": "口径", "value": "参与率 = 已测评量 / 回单量"}]
        cols = [("label", "项目", 0), ("value", "数值", 0)]
        return {"kind": kind, "scope": sub, "total": len(rows), "source": "H5满意度通报人员快照",
                "columns": [{"key": k, "label": lb, "mono": mono} for k, lb, mono in cols],
                "rows": rows, "flat": True}
    else:
        raise HTTPException(400, "未知的奖惩类型：%s" % kind)

    return {"kind": kind, "scope": sub, "total": len(rows), "source": source,
            "columns": [{"key": k, "label": lb, "mono": mono} for k, lb, mono in cols],
            "rows": rows}


@router.get("/tasks")
def tasks(kind: str = Query("port_clear"), db: Session = Depends(get_db),
          user: User = Depends(get_current_user)):
    """专项任务：库里暂无对应表（端口清虚/小区巡检/广义关怀/XF修复），
    统一返回 source=None，前端显示「待接入」。以后接表只改这里。"""
    return {"kind": kind, "total": 0, "source": None, "rows": [],
            "columns": [{"key": "no", "label": "单号", "mono": True},
                        {"key": "title", "label": "任务", "mono": False},
                        {"key": "status", "label": "状态", "mono": False}]}


# ---------------------------------------------------------------- 通知 / 知识库

@router.get("/notices")
def notices(only_unsigned: int = Query(0), db: Session = Depends(get_db),
            user: User = Depends(get_current_user)):
    signed_ids = {r[0] for r in db.query(NoticeSignature.notice_id).filter(
        NoticeSignature.user_id == user.id).all()}
    rows = []
    for n in db.query(Notice).filter(Notice.status == "published").order_by(
            Notice.publish_time.desc().nullslast(), Notice.id.desc()).all():
        is_signed = n.id in signed_ids
        if only_unsigned and is_signed:
            continue
        rows.append({
            "id": n.id, "title": n.title or "", "category": n.category or "",
            "require_sign": bool(n.require_sign),
            "time": (n.publish_time or n.created_at).strftime("%Y-%m-%d %H:%M") if (n.publish_time or n.created_at) else "",
            "signed": is_signed,
            "digest": (n.content or "").strip().replace("\n", " ")[:60],
        })
    return {"total": len(rows), "unsigned": sum(1 for r in rows if not r["signed"]), "rows": rows}


@router.get("/notices/{nid}")
def notice_detail(nid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    n = db.get(Notice, nid)
    if not n or n.status != "published":
        raise HTTPException(404, "通知不存在")
    sig = db.query(NoticeSignature).filter(NoticeSignature.notice_id == nid,
                                           NoticeSignature.user_id == user.id).first()
    return {"id": n.id, "title": n.title or "", "category": n.category or "",
            "content": n.content or "", "require_sign": bool(n.require_sign),
            "time": (n.publish_time or n.created_at).strftime("%Y-%m-%d %H:%M") if (n.publish_time or n.created_at) else "",
            "signed": bool(sig),
            "signed_at": sig.signed_at.strftime("%Y-%m-%d %H:%M") if sig and sig.signed_at else ""}


@router.post("/notices/{nid}/sign")
def notice_sign(nid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    n = db.get(Notice, nid)
    if not n or n.status != "published":
        raise HTTPException(404, "通知不存在")
    sig = db.query(NoticeSignature).filter(NoticeSignature.notice_id == nid,
                                           NoticeSignature.user_id == user.id).first()
    if not sig:
        sig = NoticeSignature(notice_id=nid, user_id=user.id, signed_at=_now())
        db.add(sig)
        db.commit()
        db.refresh(sig)
    return {"signed": True, "signed_at": sig.signed_at.strftime("%Y-%m-%d %H:%M") if sig.signed_at else ""}


@router.get("/knowledge")
def knowledge(keyword: str = Query(""), db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    """知识库：库里暂无文档表，返回 source=None（前端显示「待接入」）"""
    return {"total": 0, "source": None, "keyword": keyword, "rows": []}


# ---------------------------------------------------------------- 我的任务（服务端任务分派 → 装维端）

TASK_STATUS_NAME = {"created": "待派单", "assigned": "待接单", "accepted": "进行中",
                    "submitted": "待验收", "completed": "已验收", "rejected": "已驳回"}
TASK_PRIORITY_NAME = {"urgent": "紧急", "high": "高", "normal": "普通", "low": "低"}
TASK_OPEN = ("assigned", "accepted", "rejected")   # 需要本人动作的未完成态


def _task_dto(t, db):
    creator = db.query(User).filter(User.id == t.creator_id).first()
    dist = db.get(District, t.district_id) if t.district_id else None
    return {
        "id": t.id, "title": t.title or "", "content": t.content or "",
        "status": t.status,
        "status_name": TASK_STATUS_NAME.get(t.status, t.status),
        "priority": t.priority or "normal",
        "priority_name": TASK_PRIORITY_NAME.get(t.priority, t.priority or "普通"),
        "deadline": t.deadline or "",
        "created_at": t.created_at.strftime("%Y-%m-%d %H:%M") if t.created_at else "",
        "creator": creator.real_name if creator else "",
        "district": dist.name if dist else "",
        "open": t.status in TASK_OPEN,
    }


def _my_task(db: Session, uid: int):
    """返回 (待办数, 任务列表)。只取指派给本人的任务。"""
    rows = [_task_dto(t, db) for t in
            db.query(Task).filter(Task.assignee_id == uid).order_by(Task.id.desc()).limit(100).all()]
    open_cnt = sum(1 for r in rows if r["open"])
    return open_cnt, rows


@router.get("/my-tasks")
def my_tasks(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    open_cnt, rows = _my_task(db, user.id)
    return {"total": len(rows), "open": open_cnt, "rows": rows}


@router.post("/my-tasks/{tid}/accept")
def my_task_accept(tid: int, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    t = db.query(Task).filter(Task.id == tid, Task.assignee_id == user.id).first()
    if not t:
        raise HTTPException(404, "任务不存在")
    if t.status != "assigned":
        raise HTTPException(400, "任务当前不可接单")
    t.status = "accepted"
    db.add(TaskLog(task_id=t.id, user_id=user.id, action="接单"))
    db.commit()
    return {"msg": "接单成功，开始执行", "status": "accepted"}


@router.post("/my-tasks/{tid}/submit")
def my_task_submit(tid: int, body: dict = None, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    t = db.query(Task).filter(Task.id == tid, Task.assignee_id == user.id).first()
    if not t:
        raise HTTPException(404, "任务不存在")
    if t.status not in TASK_OPEN:
        raise HTTPException(400, "任务当前不可提交")
    t.status = "submitted"
    db.add(TaskLog(task_id=t.id, user_id=user.id, action="提交验收",
                   comment=(body or {}).get("comment", "")))
    db.commit()
    return {"msg": "已提交验收", "status": "submitted"}


# ---------------------------------------------------------------- 四类提醒（铃铛）

ALERT_META = {
    "task":   {"label": "任务",     "detail": "my_task",      "hint": "待接单/进行中/已驳回"},
    "notice": {"label": "通知",     "detail": "notice",       "hint": "必签且未签收"},
    "kb":     {"label": "知识库",   "detail": "knowledge",    "hint": "新上架文档"},
    "warn":   {"label": "工单预警", "detail": "alert_order",  "hint": "预约剩余 ≤2 小时"},
    # v1.6.9：H5 短信盯控里本人名下未回收的单，直接推到装维端提醒
    "h5":     {"label": "H5回收",   "detail": "h5_recover",   "hint": "已发短信、客户未参评"},
}


def _kb_new_count(db: Session, uid: int):
    """知识库上新数。库里暂无知识库表 → 返回 (0, "none")。

    建表后在此实现「本人未读且近 N 天上架」即可，前端无需改动。
    """
    return 0, "none"


def _warn_rows(db: Session, name, district, gkey):
    """工单预警：在途 + 预约剩余 ≤ LEVEL_ALERT 分钟（含已逾期，逾期窗口 WINDOW_PAST 内）"""
    q = db.query(AlertOrder).filter(AlertOrder.district == district)
    if gkey:
        q = q.filter(AlertOrder.grid.like("%" + gkey + "%"))
    now = _now()
    rows = []
    for r in q.all():
        if (r.status or "").strip() in DONE_ST:
            continue
        ts = _parse_dt(r.appoint_time)
        if not ts:
            continue
        left = int((ts - now).total_seconds() // 60)
        if left > LEVEL_ALERT or left < -WINDOW_PAST:
            continue
        rows.append({
            "no": r.order_no or "",
            "handler": _short_name(r.handler, r.district),
            "appoint": _hm(ts),
            "left_min": left,
            "left_text": ("已逾期 %d 分钟" % -left) if left < 0 else ("剩 %d 分钟" % left),
            "overdue": left < 0,
            "status": r.status or "",
            "grid": r.grid or "",
            "community": r.community or "",
        })
    rows.sort(key=lambda x: x["left_min"])
    return rows


@router.get("/alerts")
def alerts_summary(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """四类提醒统一入口。数值全部按「本网格」口径（网格长视角）。"""
    p = _resolve(db, user)
    name, district, gkey = p["name"], p["district"], p["grid_key"]

    task_cnt, _ = _my_task(db, user.id)
    _, notice_unsigned = _notice_count(db, user.id)
    kb_cnt, kb_state = _kb_new_count(db, user.id)
    warn_rows = _warn_rows(db, name, district, gkey)
    h5_cnt = len(_h5_mine_pending(db, name, district, _month(""))) if name else 0

    counts = {"task": task_cnt, "notice": notice_unsigned, "kb": kb_cnt,
              "warn": len(warn_rows), "h5": h5_cnt}
    total = sum(counts.values())

    items = []
    for key in ("task", "notice", "h5", "kb", "warn"):
        m = ALERT_META[key]
        items.append({
            "key": key, "label": m["label"], "hint": m["hint"],
            "detail": m["detail"], "count": counts[key],
            "state": kb_state if key == "kb" else "ok",
        })

    # 最紧急的一条优先提示（逾期 > 钻石 > 白金 > 普通），无预警则取任务
    top = ""
    if warn_rows:
        w = warn_rows[0]
        top = ("%s %s" % (w["handler"] or "未派单", w["left_text"]))
    elif task_cnt:
        top = "有 %d 项任务待处理" % task_cnt

    return {
        "total": total, "counts": counts, "items": items,
        "urgent": sum(1 for w in warn_rows if w["overdue"]),
        "top": top, "month": _month(""),
    }


@router.get("/alerts/warn")
def alerts_warn(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """工单预警明细（≤2 小时）"""
    p = _resolve(db, user)
    rows = _warn_rows(db, p["name"], p["district"], p["grid_key"])
    cols = [("no", "工单号", 1), ("handler", "责任人", 0), ("appoint", "预约时间", 0),
            ("left_text", "剩余", 0), ("status", "状态", 0), ("community", "小区", 0)]
    return {"kind": "warn", "total": len(rows),
            "source": "省公司预警附件（预约剩余 ≤2 小时的未完工单，含已逾期）",
            "columns": [{"key": k, "label": lb, "mono": mono} for k, lb, mono in cols],
            "rows": rows}
