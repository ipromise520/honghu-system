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
import re
from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from ..config import OVERDUE_HOURS
from ..database import get_db
from ..models import Alert, District, H5Complaint, H5Receipt, H5Session, WorkOrder
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
def _derive_install(db: Session, start, end, district, hide_unfinished, name_scope) -> list[dict]:
    """装机 H5 应发：work_orders（个人宽带、27 开头定单编码）"""
    dmap = {d.id: d.name for d in db.query(District).all()}
    q = db.query(WorkOrder).filter(WorkOrder.status.in_(ISSUE_STATUSES),
                                   WorkOrder.broadband_type == "个人",
                                   WorkOrder.order_id.like("27%"))
    if name_scope is not None:
        ids = [i for i, n in dmap.items() if n in name_scope]
        q = q.filter(WorkOrder.district_id.in_(ids or [-1]))
    out = []
    for o in q.all():
        bt = _norm_dt(o.completion_time) or _norm_dt(o.accept_time)
        if not bt or not (start <= bt < end):
            continue
        dist = dmap.get(o.district_id, "")
        if district and dist != district:
            continue
        status = _clean(o.status)
        if hide_unfinished and status in UNFINISHED_STATUSES:
            continue
        out.append({
            "order_no": o.order_id, "kind": "install", "district": dist,
            "district_unknown": not bool(dist), "handler": _clean(o.construction_dept),
            "grid": _clean(o.grid), "back_time": _fmt(bt), "accept_time": _clean(o.accept_time),
            "status": status, "community_name": _clean(o.community_name),
            "contact_person": _clean(o.construction_dept),
            "fault_number": _clean(o.service_number),
            "service_number": _clean(o.service_number),
        })
    return out


def _derive_complaint(db: Session, start, end, district, hide_unfinished, name_scope) -> list[dict]:
    """投诉 H5 应发：h5_complaint（HB-058 前缀）"""
    q = db.query(H5Complaint).filter(H5Complaint.eoms_order_no.like(COMPLAINT_PREFIX + "%"),
                                     H5Complaint.order_status.in_(ISSUE_STATUSES))
    out = []
    for c in q.all():
        t = _norm_dt(c.accept_time)
        if not t or not (start <= t < end):
            continue
        dist = _norm_district(c.district)
        if district and dist != district:
            continue
        if name_scope is not None and dist not in name_scope:
            continue
        status = _clean(c.order_status)
        if hide_unfinished and status in UNFINISHED_STATUSES:
            continue
        out.append({
            "order_no": c.eoms_order_no, "kind": "complaint", "district": dist,
            "district_unknown": not bool(dist), "handler": _clean(c.construction_dept_person),
            "grid": _clean(c.grid), "back_time": _fmt(t), "accept_time": _clean(c.accept_time),
            "status": status, "community_name": _clean(c.community_name),
            "contact_person": _clean(c.contact_person), "fault_number": _clean(c.fault_number),
        })
    return out


def _receipt_map(db: Session) -> dict:
    return {(r.order_no, r.kind): _clean(r.h5_received) for r in db.query(H5Receipt).all()}


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
    rmap_ts = {(r.order_no, r.kind): _fmt(r.updated_at) for r in db.query(H5Receipt).all()}
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
