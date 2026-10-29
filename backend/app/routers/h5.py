"""H5 短信盯控（对齐 8080「预警中心 - H5盯控」）
====================================================================
数据流：
  装机 H5 应发 = work_orders（状态 ∈ {已施工,待综调处理,待回访} 且 个人宽带、27 开头）
  投诉 H5 应发 = h5_complaint（状态 ∈ {已施工,待综调处理,待回访} 且 HB-058 前缀）
  时间窗口     = 今天/某月某天 → 跨天 [前一天 16:00, 次日 00:00)；当月 → 整月；或自定义起止
  回执状态     = h5_receipts（order_no+kind 唯一，h5_received='已参评'）
  超时预警     = 应发且回单/受理时间距今 > 2h 仍未收

四个上传通道（与 8080 一致）：
  ① complaint      家宽投诉综合工单列表 → h5_complaint（应发清单来源）
  ② install        装机待施工综合工单列表 → work_orders（应发清单来源）
  ③ wide           宽表查询-投诉 → 回填 h5_complaint 的小区名称/联系人/故障号码
  ④ wide_install   宽表查询-装机 → 回填 work_orders 的小区名称/所属网格/服务号码
"""
import json
import math
import re
from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..config import OVERDUE_HOURS
from ..database import get_db
from ..models import (Alert, ComplaintOrder, District, ExtensionMember, H5Complaint, H5Receipt,
                      H5Session, H5SurveyDistrict, H5SurveyRecord, H5SurveyStaff, InstallOrder,
                      Setting, SyncJob, WorkOrder)
from ..deps import get_current_user, require_admin, scope_district_ids
from ..utils_excel import parse_upload_sheets

router = APIRouter(prefix="/h5", tags=["h5"])

# 应发三态（与 8080 口径一致）：已进入施工/回访环节的工单
ISSUE_STATUSES = ("已施工", "待综调处理", "待回访")
# 未完工状态：勾选「显示未完工」时保留，取消勾选时隐藏（在当前应发集合中通常不出现）
UNFINISHED_STATUSES = {"待施工", "待预约", "待地址确认", "待调度"}
# 投诉 H5 只盯家庭市场本地投诉
COMPLAINT_PREFIX = "HB-058"
DISTRICTS = ("洪湖市", "监利市", "石首市", "松滋市", "公安县", "江陵县", "荆州区", "沙市区", "荆州开发区")
DISTRICT_ALIAS = {"开发区": "荆州开发区", "荆州经济开发区": "荆州开发区"}
ADDR_PREFIX = "湖北荆州洪湖市"


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------
def _clean(v) -> str:
    """统一清洗：None / NaN / nan 文本 → 空串"""
    if v is None:
        return ""
    s = str(v).strip()
    if s.lower() in ("nan", "nat", "none", "null"):
        return ""
    return s


def _norm_dt(v):
    """宽松解析日期时间 → datetime or None"""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    s = _clean(v)
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _fmt(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if isinstance(dt, datetime) else ""


def _norm_district(v) -> str:
    s = _clean(v)
    if not s:
        return ""
    s = DISTRICT_ALIAS.get(s, s)
    return s if s in DISTRICTS else ""


def _cross_day_bounds(base_day: datetime):
    """跨天窗口：[前一天 16:00, 次日 00:00)"""
    start = base_day.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1) + timedelta(hours=16)
    end = (base_day + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, end


def _resolve_window(date: str, start_dt: str, end_dt: str):
    """时间窗口优先级：自定义起止 > date（today / YYYY-MM / YYYY-MM-DD）"""
    s, e = _norm_dt(start_dt), _norm_dt(end_dt)
    if s is not None or e is not None:
        return (s or datetime.min), (e or datetime.max)
    d = _clean(date) or "today"
    if d == "today":
        return _cross_day_bounds(datetime.now())
    if len(d) == 7 and d[4] == "-":
        try:
            y, m = int(d[:4]), int(d[5:7])
            end = datetime(y + 1, 1, 1) if m == 12 else datetime(y, m + 1, 1)
            return datetime(y, m, 1), end
        except ValueError:
            pass
    if len(d) == 10:
        day = _norm_dt(d)
        if day:
            return _cross_day_bounds(day)
    return _cross_day_bounds(datetime.now())


def _first(row: dict, *keys):
    """按候选列名依次取值，返回首个非空"""
    for k in keys:
        if k in row:
            v = _clean(row.get(k))
            if v:
                return v
    return ""


def _strip_addr_prefix(v: str) -> str:
    s = _clean(v)
    return s[len(ADDR_PREFIX):].strip() if s.startswith(ADDR_PREFIX) else s


# --------------------------------------------------------------------------
# 应发明细派生
# --------------------------------------------------------------------------
def _adjudicate_install(db, rows):
    """跨源状态裁决（与桌面端/装维端同一套规则，status_resolver 唯一真源）"""
    from .metrics import _adh_install
    return _adh_install(db, rows)


def _adjudicate_complaint(db, rows):
    from .metrics import _adh_complaint
    return _adh_complaint(db, rows)


def _back_col_install():
    """装机的「回单时间」，缺失时回落受理时间；截到秒，避免 '...:44.0' 与 '...:44' 比较错位"""
    return func.substr(func.coalesce(func.nullif(InstallOrder.completion_time, ""),
                                     InstallOrder.accept_time), 1, 19)


def _derive_install(db: Session, start, end, district, hide_unfinished, name_scope) -> list[dict]:
    """装机 H5 应发：install_orders（「数据同步 → 装机宽表」）

    v1.6.8 起只读装机宽表。原来读 work_orders（H5盯控页自带的独立上传口），
    那份数据停在 10-22，用户在「数据同步」传了宽表也看不到任何数。
    宽表自带 所属小区 / 服务号码（见 sync.py 的 _normalize_install_wide），
    所以这里不再需要「宽表查询-装机」单独回填。
    """
    bt = _back_col_install()
    q = db.query(InstallOrder).filter(
        # SQL 层先放宽：宽表可能滞后 2 小时（还是「处理中」），实时查询已判成「已施工」，
        # 如果这里只放 ISSUE_STATUSES，这类单在裁决前就被筛掉了。裁决后再精确判一次。
        InstallOrder.status.in_(ISSUE_STATUSES + ("处理中",)),
        InstallOrder.order_id.like("27%"),
        # 宽表 broadband_type 有空值（导出缺失），业务主题均为家宽类，空值视为个人；
        # 「小微」是明确的非个人，排除。
        func.coalesce(InstallOrder.broadband_type, "").in_(("个人", "")),
        # 全空壳行（状态/处理人/环节都空，仅剩区县和受理时间）是导出残缺记录，不是真实在途单
        or_(InstallOrder.handler != "", InstallOrder.current_step != ""),
        bt >= _fmt(start), bt < _fmt(end))
    if district:
        q = q.filter(InstallOrder.district == district)
    if name_scope is not None:
        q = q.filter(InstallOrder.district.in_(list(name_scope) or ["-"]))
    rows = q.all()
    _adjudicate_install(db, rows)          # 跨源裁决：实时判成已撤单的单不该再催 H5
    out = []
    for o in rows:
        status = _clean(getattr(o, "_st_status", "") or o.status)
        if status not in ISSUE_STATUSES:
            continue
        if hide_unfinished and status in UNFINISHED_STATUSES:
            continue
        bt_s = _clean(o.completion_time) or _clean(o.accept_time)
        out.append({
            "order_no": o.order_id, "kind": "install", "district": _clean(o.district),
            "district_unknown": not bool(o.district), "handler": _clean(o.handler),
            "grid": _clean(o.grid), "back_time": bt_s, "accept_time": _clean(o.accept_time),
            "status": status, "community_name": _clean(o.community_name),
            "contact_person": _clean(o.handler),
            "fault_number": _clean(o.service_number),
            "service_number": _clean(o.service_number),
        })
    return out


def _derive_complaint(db: Session, start, end, district, hide_unfinished, name_scope) -> list[dict]:
    """投诉 H5 应发：complaint_orders（「数据同步 → 投诉宽表」）

    原来读 h5_complaint（独立上传口，停在 10-22）。投诉宽表的小区/号码从
    「投诉内容」正文里解析写入（见 sync.py 的 _cmp_contact）。
    """
    t = func.substr(func.coalesce(func.nullif(ComplaintOrder.accept_time, ""), ""), 1, 19)
    q = db.query(ComplaintOrder).filter(
        ComplaintOrder.eoms_no.like(COMPLAINT_PREFIX + "%"),
        ComplaintOrder.status.in_(ISSUE_STATUSES + ("处理中",)),
        t >= _fmt(start), t < _fmt(end))
    if district:
        q = q.filter(ComplaintOrder.district == district)
    if name_scope is not None:
        q = q.filter(ComplaintOrder.district.in_(list(name_scope) or ["-"]))
    rows = q.all()
    _adjudicate_complaint(db, rows)
    out = []
    for c in rows:
        status = _clean(getattr(c, "_st_status", "") or c.status)
        if status not in ISSUE_STATUSES:
            continue
        if hide_unfinished and status in UNFINISHED_STATUSES:
            continue
        dist = _clean(c.district)
        out.append({
            "order_no": c.eoms_no, "kind": "complaint", "district": dist,
            "district_unknown": not bool(dist), "handler": _clean(c.handler),
            "grid": _clean(c.grid), "back_time": _clean(c.accept_time),
            "accept_time": _clean(c.accept_time), "status": status,
            "community_name": _clean(c.community_name),
            "contact_person": _clean(c.contact_person),
            "fault_number": _clean(c.fault_number),
        })
    return out


def _receipt_map(db: Session) -> dict:
    """(工单号, 类型) → 已参评 / 空。

    两个来源合并，**缺一不可**：
      ① h5_survey_records —— 「H5满意度通报」里的装机H5回复详单，是**真实回执**。
         实测 2556 条装机详单里 2470 条能对上装机宽表（96.6%）。
      ② h5_receipts —— 网格长在页面上手动点「已参评」补标的，以及历史遗留记录。
    投诉详单用的是 CRM 工单号（20261001114634X…），与 EOMS 单号（HB-058-…）
    是两套编号体系，无法自动关联，投诉只能靠 ② 手动标记。
    """
    m: dict = {}
    try:
        for (no,) in db.query(H5SurveyRecord.order_no).filter(
                H5SurveyRecord.kind == "装机",
                H5SurveyRecord.order_no != "",
                H5SurveyRecord.reply_time != "").all():
            if no:
                m[(no, "install")] = "已参评"
    except Exception:
        pass
    # 人工标记后写覆盖：非空=手动标已参评，空=手动取消（点错了要能撤回来）
    for r in db.query(H5Receipt).all():
        m[(_clean(r.order_no), _clean(r.kind))] = _clean(r.h5_received) or ""
    return m


def _is_received(item: dict, rmap: dict) -> bool:
    return rmap.get((item["order_no"], item["kind"])) == "已参评"


def _collect(db, user, date, start_dt, end_dt, district, hide_unfinished, kind) -> tuple[list, dict]:
    start, end = _resolve_window(date, start_dt, end_dt)
    ids = scope_district_ids(user, db)
    name_scope = None
    if ids is not None:
        name_scope = {d.name for d in db.query(District).filter(District.id.in_(ids or [-1])).all()}
    items = []
    if kind in ("all", "install", None):
        items += _derive_install(db, start, end, district, hide_unfinished, name_scope)
    if kind in ("all", "complaint", None):
        items += _derive_complaint(db, start, end, district, hide_unfinished, name_scope)
    rmap = _receipt_map(db)
    for it in items:
        it["h5_received"] = "已参评" if _is_received(it, rmap) else ""
    return items, rmap


def _age_hours(back_time: str):
    bt = _norm_dt(back_time)
    if not bt:
        return 0.0
    return round((datetime.now() - bt).total_seconds() / 3600, 1)


# --------------------------------------------------------------------------
# 看板 / 清单 / 预警
# --------------------------------------------------------------------------
@router.get("/dashboard")
def h5_dashboard(date: str = Query("today"), district: str = Query(""),
                 start_dt: str = Query(""), end_dt: str = Query(""),
                 user=Depends(get_current_user), db: Session = Depends(get_db)):
    """两张卡片：装机/投诉 应发、已收、未收、收取率"""
    items, rmap = _collect(db, user, date, start_dt, end_dt, district, False, "all")
    res = {"date": date or "today", "kinds": {}}
    for kind in ("install", "complaint"):
        sub = [p for p in items if p["kind"] == kind]
        received = sum(1 for p in sub if _is_received(p, rmap))
        total = len(sub)
        res["kinds"][kind] = {"total": total, "received": received, "pending": total - received,
                              "ratio": round(received / total * 100, 1) if total else 0.0}
    res["summary"] = {"total": sum(v["total"] for v in res["kinds"].values()),
                      "received": sum(v["received"] for v in res["kinds"].values()),
                      "pending": sum(v["pending"] for v in res["kinds"].values())}
    return res


@router.get("/pending")
def h5_pending(kind: str = Query("all"), date: str = Query("today"), district: str = Query(""),
               hide_unfinished: bool = Query(False), start_dt: str = Query(""), end_dt: str = Query(""),
               user=Depends(get_current_user), db: Session = Depends(get_db)):
    """待收清单：未收的应发，按 (施工人, 区县) 聚合并携带明细"""
    items, rmap = _collect(db, user, date, start_dt, end_dt, district, hide_unfinished, kind)
    groups: dict = {}
    for p in items:
        if _is_received(p, rmap):
            continue
        key = (p["handler"] or "未命名", p["district"] or "")
        g = groups.setdefault(key, {"handler": p["handler"] or "未命名", "district": p["district"],
                                    "district_unknown": p["district_unknown"], "kinds": {},
                                    "items": [], "max_age_h": 0})
        g["kinds"][p["kind"]] = g["kinds"].get(p["kind"], 0) + 1
        g["items"].append(p)
        g["max_age_h"] = max(g["max_age_h"], _age_hours(p["back_time"]))
    glist = list(groups.values())
    for g in glist:
        g["count"] = len(g["items"])
        g["items"].sort(key=lambda x: x["back_time"], reverse=True)
        grid = ""
        for it in g["items"]:
            if it["kind"] == "install" and it["grid"]:
                grid = it["grid"]
                break
        if not grid:
            for it in g["items"]:
                if it["grid"]:
                    grid = it["grid"]
                    break
        g["grid"] = grid
    glist.sort(key=lambda x: (x["max_age_h"], x["count"]), reverse=True)
    return glist


@router.get("/alerts")
def h5_alerts(date: str = Query("today"), district: str = Query(""),
              start_dt: str = Query(""), end_dt: str = Query(""),
              user=Depends(get_current_user), db: Session = Depends(get_db)):
    """超时未收预警：回单/受理后超过 2h 仍未收"""
    items, rmap = _collect(db, user, date, start_dt, end_dt, district, False, "all")
    rows = []
    for p in items:
        if _is_received(p, rmap):
            continue
        age = _age_hours(p["back_time"])
        if age >= OVERDUE_HOURS:
            rows.append({**p, "age_h": age})
    rows.sort(key=lambda x: x["age_h"], reverse=True)
    return {"overdue_hours": OVERDUE_HOURS, "count": len(rows), "items": rows[:200]}


@router.get("/receipt-list")
def h5_receipt_list(kind: str = Query("all"), date: str = Query("today"), district: str = Query(""),
                    start_dt: str = Query(""), end_dt: str = Query(""),
                    user=Depends(get_current_user), db: Session = Depends(get_db)):
    """已收清单：已标记「已参评」的应发工单（按工单时间窗口，与待收清单口径一致）"""
    items, rmap = _collect(db, user, date, start_dt, end_dt, district, False, kind)
    # 标记时间：手动标记用 h5_receipts.updated_at；来自回复详单的用 reply_time（否则整列空着）
    rmap_ts = {(r.order_no, r.kind): _fmt(r.updated_at) for r in db.query(H5Receipt).all()}
    try:
        for no, rt in db.query(H5SurveyRecord.order_no, H5SurveyRecord.reply_time).filter(
                H5SurveyRecord.kind == "装机", H5SurveyRecord.order_no != "",
                H5SurveyRecord.reply_time != "").all():
            rmap_ts.setdefault((no, "install"), rt or "")
    except Exception:
        pass
    out = []
    for p in items:
        if not _is_received(p, rmap):
            continue
        out.append({"order_no": p["order_no"], "kind": p["kind"], "handler": p["handler"],
                    "district": p["district"], "community_name": p["community_name"],
                    "grid": p["grid"], "service_number": p.get("service_number", ""),
                    "accept_time": p["accept_time"],
                    "h5_received": "已参评", "updated_at": rmap_ts.get((p["order_no"], p["kind"]), "")})
    out.sort(key=lambda x: x["updated_at"], reverse=True)
    return out


@router.patch("/receipts")
def mark_receipts(body: dict, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """批量标记回执：{items:[{order_no, kind, h5_received}]}，h5_received 为空串表示取消"""
    items = body.get("items") or body.get("marks") or []
    n = 0
    for m in items:
        order_no = _clean(m.get("order_no"))
        kind = _clean(m.get("kind")) or "install"
        if not order_no:
            continue
        received = "已参评" if m.get("received", m.get("h5_received")) else ""
        r = db.query(H5Receipt).filter(H5Receipt.order_no == order_no, H5Receipt.kind == kind).first()
        if r:
            r.h5_received = received
            r.updated_at = datetime.now()
        else:
            db.add(H5Receipt(order_no=order_no, kind=kind, h5_received=received))
        n += 1
    db.commit()
    return {"ok": True, "updated": n, "msg": f"已更新 {n} 条回执"}


@router.post("/notify")
def h5_notify(user=Depends(get_current_user), db: Session = Depends(get_db)):
    """生成超时未收站内通知（当天去重）"""
    items, rmap = _collect(db, user, "today", "", "", "", False, "all")
    overdue = [i for i in items if not _is_received(i, rmap) and _age_hours(i["back_time"]) >= OVERDUE_HOURS]
    by_handler = defaultdict(int)
    for i in overdue:
        by_handler[i["handler"] or "未命名"] += 1
    today = datetime.now().strftime("%Y-%m-%d")
    created = 0
    for handler, cnt in by_handler.items():
        content = f"{today} H5 超时提醒：{handler} 有 {cnt} 单超时 {OVERDUE_HOURS} 小时未收回执，请尽快联系用户参评。"
        if not db.query(Alert).filter(Alert.type == "h5_overdue", Alert.content == content).first():
            db.add(Alert(type="h5_overdue", order_no="", content=content, level="danger"))
            created += 1
    db.commit()
    return {"msg": f"生成 {created} 条站内通知", "overdue_total": len(overdue)}


@router.get("/sessions")
def h5_sessions(db: Session = Depends(get_db), user=Depends(get_current_user)):
    return [{"id": s.id, "session": s.session, "kind": s.kind, "filename": s.filename,
             "rows": s.rows, "created_at": s.created_at.strftime("%Y-%m-%d %H:%M:%S")}
            for s in db.query(H5Session).order_by(H5Session.id.desc()).limit(50)]


# --------------------------------------------------------------------------
# 上传通道
# --------------------------------------------------------------------------
def _pick_sheet(sheets: dict, prefer_kw) -> list[dict]:
    for name, rows in sheets.items():
        if any(k in name for k in prefer_kw):
            return rows
    for rows in sheets.values():
        return rows
    return []


def _record_session(db, kind, filename, rows):
    db.add(H5Session(session=f"SES{datetime.now().strftime('%Y%m%d%H%M%S%f')}", kind=kind,
                     filename=filename, rows=rows))


@router.post("/upload/{kind}")
async def h5_upload(kind: str, request: Request,
                    user=Depends(require_admin), db: Session = Depends(get_db)):
    """上传 H5 盯控源文件（kind = complaint / install / wide / wide_install）"""
    if kind not in ("complaint", "install", "wide", "wide_install"):
        raise HTTPException(status_code=400, detail="kind 必须是 complaint / install / wide / wide_install")
    filename, sheets = await parse_upload_sheets(request)
    if kind == "complaint":
        return _up_complaint(db, filename, sheets)
    if kind == "install":
        return _up_install(db, filename, sheets)
    if kind == "wide":
        return _up_wide(db, filename, sheets)
    return _up_wide_install(db, filename, sheets)


def _up_complaint(db, filename, sheets):
    rows = _pick_sheet(sheets, ("家宽投诉", "投诉"))
    added = updated = 0
    for r in rows:
        no = _first(r, "工单编号", "EOMS工单号", "工单流水号", "eoms_order_no")
        if not no:
            continue
        fields = {
            "order_status": _first(r, "工单状态", "智慧装维工单状态", "order_status") or "已施工",
            "construction_dept_person": _first(r, "施工部门/人", "当前处理人", "最终回单人员", "施工人"),
            "accept_time": _first(r, "投诉时间", "受理时间"),
            "district": _norm_district(_first(r, "区域", "区县")) or "洪湖市",
            "community_name": _strip_addr_prefix(_first(r, "小区名称", "所属小区")),
            "contact_person": _first(r, "联系人", "联系电话", "受理电话号码"),
            "fault_number": _first(r, "故障号码", "主叫号码"),
            "grid": _first(r, "最终回单网格", "首次派单目标网格", "网格"),
        }
        obj = db.query(H5Complaint).filter(H5Complaint.eoms_order_no == no).first()
        if obj:
            for k, v in fields.items():
                if v:
                    setattr(obj, k, v)
            updated += 1
        else:
            db.add(H5Complaint(eoms_order_no=no, raw_data=json.dumps(r, ensure_ascii=False, default=str), **fields))
            added += 1
    _record_session(db, "complaint", filename, added)
    db.commit()
    return {"ok": True, "filename": filename, "inserted": added, "updated": updated,
            "total": len(rows), "columns": list(rows[0].keys()) if rows else []}


def _up_install(db, filename, sheets):
    rows = _pick_sheet(sheets, ("导出信息", "装机", "待施工"))
    dmap = {d.name: d.id for d in db.query(District).all()}
    added = updated = 0
    for r in rows:
        oid = _first(r, "定单编码", "工单编码", "订单编码", "工单号")
        if not oid:
            continue
        dname = _norm_district(_first(r, "区域", "区县"))
        fields = {
            "status": _first(r, "工单状态", "当前状态", "状态") or "已施工",
            "accept_time": _first(r, "受理时间", "投诉时间"),
            "completion_time": _first(r, "施工时间", "回单时间", "完工时间"),
            "construction_dept": _first(r, "施工人", "当前处理人", "回单人员"),
            "broadband_type": _first(r, "宽带类型") or "个人",
            "grid": _first(r, "网格名称", "网格"),
            "community_name": _strip_addr_prefix(_first(r, "小区名称", "小区")),
            "service_number": _first(r, "服务号码", "号码", "联系电话"),
            "district_id": dmap.get(dname) if dname else None,
        }
        obj = db.query(WorkOrder).filter(WorkOrder.order_id == oid).first()
        if obj:
            for k, v in fields.items():
                if v not in (None, ""):
                    setattr(obj, k, v)
            updated += 1
        else:
            db.add(WorkOrder(order_id=oid, raw_data=json.dumps(r, ensure_ascii=False, default=str), **fields))
            added += 1
    _record_session(db, "install", filename, added)
    db.commit()
    return {"ok": True, "filename": filename, "inserted": added, "updated": updated,
            "total": len(rows), "columns": list(rows[0].keys()) if rows else []}


def _up_wide(db, filename, sheets):
    """宽表查询-投诉：从「投诉内容」提取 小区名称/联系人/故障号码，回填 h5_complaint（仅补空值）"""
    rows = _pick_sheet(sheets, ("数据1", "宽表"))
    matched = updated = 0
    for r in rows:
        no = _first(r, "工单流水号", "EOMS工单号", "工单编号")
        if not no:
            continue
        content = _first(r, "投诉主题") + "\n" + _first(r, "投诉内容")
        m = re.search(r"\[\s*装机地址\s*[:：]\s*([^\]]{2,300}?)\s*\]", content)
        community = _strip_addr_prefix(m.group(1)) if m else _strip_addr_prefix(_first(r, "所属小区", "小区名称"))
        mf = re.search(r"主叫号码[：:]\s*(\d+)", content)
        fault = mf.group(1) if mf else ""
        mc = re.search(r"受理电话号码[：:]\s*(\d+)", content)
        contact = mc.group(1) if mc else ""
        if not contact:
            contact = _first(r, "联系电话", "联系人")
        matched += 1
        obj = db.query(H5Complaint).filter(H5Complaint.eoms_order_no == no).first()
        if not obj:
            continue
        if community and not obj.community_name:
            obj.community_name = community
        if contact and not obj.contact_person:
            obj.contact_person = contact
        if fault and not obj.fault_number:
            obj.fault_number = fault
        updated += 1
    _record_session(db, "wide", filename, matched)
    db.commit()
    return {"ok": True, "filename": filename, "rows": matched, "updated_complaint": updated,
            "columns": list(rows[0].keys()) if rows else []}


def _up_wide_install(db, filename, sheets):
    """宽表查询-装机：按定单编码匹配，回填 work_orders 的小区名称/所属网格/服务号码"""
    rows = _pick_sheet(sheets, ("数据1", "导出信息", "宽表"))
    matched = updated = 0
    for r in rows:
        oid = _first(r, "定单编码", "工单编码", "订单编码", "工单号")
        if not oid:
            continue
        matched += 1
        obj = db.query(WorkOrder).filter(WorkOrder.order_id == oid).first()
        if not obj:
            continue
        community = _strip_addr_prefix(_first(r, "小区名称", "小区"))
        grid = _first(r, "网格名称", "网格")
        service = _first(r, "服务号码", "号码")
        if community and not obj.community_name:
            obj.community_name = community
        if grid and not obj.grid:
            obj.grid = grid
        if service and not obj.service_number:
            obj.service_number = service
        updated += 1
    _record_session(db, "wide_install", filename, matched)
    db.commit()
    return {"ok": True, "filename": filename, "rows": matched, "matched": updated,
            "columns": list(rows[0].keys()) if rows else []}


# --------------------------------------------------------------------------
# H5参与率通报（前端 /h5-rate 页面）：数据来自「数据同步 → H5满意度通报」最新确认快照
# 图1 人员参与率明细（默认洪湖市，district 参数可切换）+ 图2 区县汇总（当日/当月）
# --------------------------------------------------------------------------
_H5_RATE_DISTRICTS = ["沙市区", "荆州区", "荆州开发区", "洪湖市", "监利市", "江陵县", "公安县", "松滋市", "石首市"]

# ---------------- 参与率奖励规则（以下数值为可修改变量，按考核办法调整） ----------------
REWARD_RATE_MIN = 0.5        # 参与率门槛：达到按件计奖，未达到按欠产扣罚
REWARD_INSTALL_UNIT = 20     # 装机参与奖励单价（元/件）
REWARD_COMPLAINT_UNIT = 30   # 故障（投诉）参与奖励单价（元/件）

# 洪湖市 网格→班组长 补齐映射：延伸花名册（extension_members）只覆盖 7 个主网格，
# 子网格（燕窝/万全/小港等）与主网格同班长，此处按 10月18日 通报快照补齐；
# 花名册中有记录的网格以花名册为准（setdefault 语义），组织调整时改这里即可。
_H5_GRID_LEADERS = {
    "大同网格": "孙澳", "洪湖大同网格": "孙澳", "洪湖新滩网格": "孙澳",
    "大沙网格": "蔡明", "洪湖大沙网格": "蔡明", "洪湖燕窝网格": "蔡明",
    "峰口网格": "柴达", "洪湖峰口网格": "柴达", "洪湖沙口网格": "柴达",
    "新堤网格": "张茂礼", "洪湖新堤网格": "张茂礼", "洪湖城区网格": "张茂礼",
    "洪湖龙口网格": "张茂礼", "洪湖瞿家湾网格": "张茂礼",
    "柏枝网格": "张进", "洪湖柏枝网格": "张进", "洪湖乌林网格": "张进",
    "洪湖文泉网格": "张进", "洪湖荷花网格": "张进", "洪湖螺山网格": "张进",
    "滨湖网格": "叶超", "洪湖滨湖网格": "叶超", "洪湖万全网格": "叶超",
    "洪湖小港网格": "叶超", "洪湖汊河网格": "叶超", "洪湖黄家口网格": "叶超",
    "洪湖府场网格": "何支军", "洪湖戴家场网格": "何支军", "洪湖曹市网格": "何支军",
}


def _xround(v: float) -> int:
    """Excel ROUND 口径：四舍五入远离零（ROUND(-4.5)=-5）；Python round 为银行家舍入不可用"""
    return int(math.floor(v + 0.5)) if v >= 0 else int(math.ceil(v - 0.5))


def _reward(rate, eval_cnt: int, back_cnt: int, short_cnt, unit: int) -> int:
    """预计奖励（元）：参与率达到门槛 → 已测评件数×单价；未达到 → 50%参与率欠产数×单价（欠产为负即扣罚）。
    无回单不计奖；short_cnt 为已按 Excel 口径算好的欠产数（fig1 传库存值，fig2 传 None 现算）。"""
    if not back_cnt:
        return 0
    if rate is not None and rate >= REWARD_RATE_MIN:
        return int(eval_cnt * unit)
    short = short_cnt if short_cnt is not None else _xround(eval_cnt - 0.5 * back_cnt)
    return int(short * unit)


def _h5_rate_meta(db, district: str) -> dict:
    dd = db.query(Setting).filter(Setting.key == "h5_survey_date").first()
    job = (db.query(SyncJob).filter(SyncJob.metric == "h5_survey", SyncJob.status == "confirmed")
           .order_by(SyncJob.confirmed_at.desc()).first())
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", (dd.value if dd else "") or "")
    if m:
        day_label = f"{int(m.group(2))}月{int(m.group(3))}日"
        month_label = f"{int(m.group(2))}月截止{int(m.group(3))}日"
    else:
        day_label = month_label = ""
    return {"source": job.filename if job else "", "day_label": day_label, "month_label": month_label,
            "fig1_title": "(集团大音平台) 装维H5满意度", "fig1_subtitle": district,
            "data_date": dd.value if dd else "",
            # 奖励规则（可修改变量，同步给前端标题旁说明与底部口径注）
            "reward": {"rate_min": REWARD_RATE_MIN, "install_unit": REWARD_INSTALL_UNIT,
                       "complaint_unit": REWARD_COMPLAINT_UNIT},
            "reward_note": (f"参与率（{int(REWARD_RATE_MIN * 100)}%）奖励装机参与（{REWARD_INSTALL_UNIT}）/件；"
                            f"故障参与（{REWARD_COMPLAINT_UNIT}）元/件。")}


@router.get("/rate")
def h5_rate(district: str = Query("洪湖市"), user=Depends(get_current_user), db: Session = Depends(get_db)):
    """H5参与率通报：图1 人员明细 + 图2 区县汇总（当日/当月）。
    回单量/测评数取参与率与回单明细（当月按人员归属、当日按回单时间拆分）；
    满意度 = 0.4×Q1 + 0.2×Q2 + 0.4×Q3（详单得分均值）；欠产数 = ROUND(测评量 − 50%×回单量)"""
    dists = db.query(H5SurveyDistrict).all()
    staff_rows = db.query(H5SurveyStaff).all()
    meta = _h5_rate_meta(db, district)

    # ---- 图1：人员明细（班组长 = extension_members 网格长 + 子网格补齐映射，按网格名关联） ----
    leaders: dict = {}
    for m in db.query(ExtensionMember).filter(ExtensionMember.position.contains("网格长")).all():
        if m.grid and m.name:
            leaders[m.grid] = m.name
    for g, n in _H5_GRID_LEADERS.items():
        leaders.setdefault(g, n)
    # staff 表 grid 为非网格名（如「集客洪湖市投诉代维班组」「洪湖市分公司」）时，
    # 用花名册中同名人员的 grid 反查班组长；匹配不到则留空（如管理层「装维管理员」）
    member_grid: dict = {}
    for m in db.query(ExtensionMember).all():
        if m.name and m.grid:
            bare = re.sub(r"^.*?市", "", m.name)
            member_grid.setdefault(m.name, m.grid)
            member_grid.setdefault(bare, m.grid)

    def _leader_of(grid, name):
        if grid in leaders:
            return leaders[grid]
        for k in (name, re.sub(r"^.*?市", "", name or "")):
            mg = member_grid.get(k)
            if mg and mg in leaders:
                return leaders[mg]
        return ""

    fig1_rows = []
    for s in staff_rows:
        if s.district != district:
            continue
        i_rate_v = (s.i_eval / s.i_back) if s.i_back else None
        c_rate_v = (s.c_eval / s.c_back) if s.c_back else None
        i_rw = _reward(i_rate_v, s.i_eval, s.i_back, s.i_short, REWARD_INSTALL_UNIT)
        c_rw = _reward(c_rate_v, s.c_eval, s.c_back, s.c_short, REWARD_COMPLAINT_UNIT)
        fig1_rows.append({
            "grid": s.grid, "leader": _leader_of(s.grid, s.name), "name": s.name,
            "i_back": s.i_back, "i_eval": s.i_eval,
            "i_rate": i_rate_v if i_rate_v is not None else 0.0,
            "i_short": s.i_short, "i_score": s.i_score,
            "i_reward": i_rw,
            "c_back": s.c_back, "c_eval": s.c_eval,
            "c_rate": c_rate_v if c_rate_v is not None else 0.0,
            "c_short": s.c_short, "c_score": s.c_score,
            "c_reward": c_rw,
            "rw_total": i_rw + c_rw,
            "no_install": bool(s.no_install),
        })
    fig1_rows.sort(key=lambda r: (r["grid"], r["name"]))

    def _wavg(score_key, eval_key, rows):
        num = sum((r[score_key] or 0.0) * r[eval_key] for r in rows if r[score_key] is not None)
        den = sum(r[eval_key] for r in rows if r[score_key] is not None)
        return round(num / den, 4) if den else None

    t_ib = sum(r["i_back"] for r in fig1_rows)
    t_ie = sum(r["i_eval"] for r in fig1_rows)
    t_cb = sum(r["c_back"] for r in fig1_rows)
    t_ce = sum(r["c_eval"] for r in fig1_rows)
    t_i_rw = _reward((t_ie / t_ib) if t_ib else None, t_ie, t_ib,
                     sum(r["i_short"] for r in fig1_rows), REWARD_INSTALL_UNIT)
    t_c_rw = _reward((t_ce / t_cb) if t_cb else None, t_ce, t_cb,
                     sum(r["c_short"] for r in fig1_rows), REWARD_COMPLAINT_UNIT)
    fig1 = {"rows": fig1_rows,
            "totals": {"i_back": t_ib, "i_eval": t_ie,
                       "i_short": sum(r["i_short"] for r in fig1_rows),
                       "i_score": _wavg("i_score", "i_eval", fig1_rows),
                       "i_reward": t_i_rw,
                       "c_back": t_cb, "c_eval": t_ce,
                       "c_short": sum(r["c_short"] for r in fig1_rows),
                       "c_score": _wavg("c_score", "c_eval", fig1_rows),
                       "c_reward": t_c_rw,
                       "rw_total": t_i_rw + t_c_rw}}

    # ---- 图2：区县汇总（当日/当月 × 装机/投诉，9 区县 + 荆州合计行） ----
    def _pick(d, kind, period):
        return next((x for x in dists if x.district == d and x.kind == kind and x.period == period), None)

    def _fig2_block(period):
        rows = []
        for d in _H5_RATE_DISTRICTS:
            i, c = _pick(d, "装机", period), _pick(d, "投诉", period)
            if not i and not c:
                continue
            rows.append({
                "district": d,
                "i_back": i.back_cnt if i else 0, "i_eval": i.survey_cnt if i else 0,
                "i_sat": i.satisfaction if i else None,
                "q24": i.q_timely if i else None, "q25": i.q_service if i else None,
                "q26": i.q_effect if i else None, "fttr": i.fttr_sat if i else None,
                "i_rate": (i.survey_cnt / i.back_cnt) if i and i.back_cnt else None,
                "i_reward": _reward((i.survey_cnt / i.back_cnt) if i and i.back_cnt else None,
                                    i.survey_cnt if i else 0, i.back_cnt if i else 0,
                                    None, REWARD_INSTALL_UNIT),
                "i_affect": i.affect_cnt if i else 0,
                "i_affect_rate": (i.affect_ratio / 100) if i else None,
                "i_diff": i.diff_cnt if i else 0,
                "bm": i.unsat_timely if i else 0, "bn": i.unsat_service if i else 0,
                "bo": i.unsat_effect if i else 0, "bp": i.unsat_image if i else 0,
                "c_back": c.back_cnt if c else 0, "c_eval": c.survey_cnt if c else 0,
                "c_sat": c.satisfaction if c else None,
                "q27": c.q_timely if c else None, "q28": c.q_service if c else None,
                "q29": c.q_effect if c else None,
                "c_rate": (c.survey_cnt / c.back_cnt) if c and c.back_cnt else None,
                "c_reward": _reward((c.survey_cnt / c.back_cnt) if c and c.back_cnt else None,
                                    c.survey_cnt if c else 0, c.back_cnt if c else 0,
                                    None, REWARD_COMPLAINT_UNIT),
                "c_affect": c.affect_cnt if c else 0,
                "c_affect_rate": (c.affect_ratio / 100) if c else None,
                "c_diff": c.diff_cnt if c else 0,
            })
        if not rows:
            return {"rows": [], "totals": {}}
        t_ib, t_ie = sum(r["i_back"] for r in rows), sum(r["i_eval"] for r in rows)
        t_cb, t_ce = sum(r["c_back"] for r in rows), sum(r["c_eval"] for r in rows)
        q24, q25, q26 = (_wavg(k, "i_eval", rows) for k in ("q24", "q25", "q26"))
        q27, q28, q29 = (_wavg(k, "c_eval", rows) for k in ("q27", "q28", "q29"))
        totals = {"district": "荆州",
                  "i_back": t_ib, "i_eval": t_ie,
                  "i_sat": round(0.4 * (q24 or 0) + 0.2 * (q25 or 0) + 0.4 * (q26 or 0), 4) if q24 is not None else None,
                  "q24": q24, "q25": q25, "q26": q26, "fttr": _wavg("fttr", "i_eval", rows),
                  "i_rate": (t_ie / t_ib) if t_ib else None,
                  "i_reward": _reward((t_ie / t_ib) if t_ib else None, t_ie, t_ib, None, REWARD_INSTALL_UNIT),
                  "i_affect": sum(r["i_affect"] for r in rows), "i_affect_rate": None,
                  "i_diff": sum(r["i_diff"] for r in rows),
                  "bm": sum(r["bm"] for r in rows), "bn": sum(r["bn"] for r in rows),
                  "bo": sum(r["bo"] for r in rows), "bp": sum(r["bp"] for r in rows),
                  "c_back": t_cb, "c_eval": t_ce,
                  "c_sat": round(0.4 * (q27 or 0) + 0.2 * (q28 or 0) + 0.4 * (q29 or 0), 4) if q27 is not None else None,
                  "q27": q27, "q28": q28, "q29": q29,
                  "c_rate": (t_ce / t_cb) if t_cb else None,
                  "c_reward": _reward((t_ce / t_cb) if t_cb else None, t_ce, t_cb, None, REWARD_COMPLAINT_UNIT),
                  "c_affect": sum(r["c_affect"] for r in rows), "c_affect_rate": None,
                  "c_diff": sum(r["c_diff"] for r in rows)}
        return {"rows": rows, "totals": totals}

    # 图1 空态说明：staff 表为空但区县汇总有数据 → 当前快照为旧版导出（无人员粒度），
    # 明确告知用户该传哪种导出，避免「明细不显示」再次发生（2026-10-22 用户反馈）
    fig1_hint = ""
    if not staff_rows and dists:
        fig1_hint = ("当前快照为旧版导出格式（「H5数据统计」区县汇总），无人员粒度数据，图1 暂无明细。"
                     "请在「数据同步 → H5满意度通报」上传「装维H5满意度参与率」导出"
                     "（含「装机参与率」「投诉参与率」sheet）并确认入库后即可显示。")
    elif not fig1_rows and staff_rows:
        fig1_hint = f"当前快照中无{district}的人员明细数据。"

    return {"has_data": bool(dists or staff_rows), "meta": meta, "fig1": fig1, "fig1_hint": fig1_hint,
            "fig2": {"day": _fig2_block("当日"), "month": _fig2_block("当月")}}
