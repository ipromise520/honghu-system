# -*- coding: utf-8 -*-
"""质检规范（人工质检通报）接口

数据源：市公司每日「人工质检明细」xlsx（经「数据同步」上传入库到 qc_records）
  · 人工质检明细（抽检：投诉 / 义诊 / 延伸服务 / 移动看家 / 智能组网 / 装机）
  · FTTR（全量）
  · 企宽装机（全量）

通报分析维度（对齐「分析汇总」sheet）：
  区县 × 质检类型 → 抽检量 / 合格量 / 合格率（各类型有达标线）
  区县 → 不合格件数 / 考核金额 / 不结算金额
  全市 → 不合格原因 Top（不规范项聚合）

推送：把「不通过」记录按 回单人 → 装维人员账号 匹配后推到装维端（/m/qc），
      装维人员提交整改后回写 fix_status='fixed'，页面据此显示整改结果。
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import QcRecord, District, User, AuditLog
from ..qc_rules import QC_ORDER, QC_THRESHOLD, QC_UNIT_PRICE

router = APIRouter(prefix="/qc", tags=["qc"])


def _r1(v: float) -> float:
    return round(float(v or 0), 1)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _dates(db: Session) -> list[dict]:
    rows = (db.query(QcRecord.date, func.count(QcRecord.id))
            .group_by(QcRecord.date).order_by(QcRecord.date.desc()).all())
    out = []
    for d, n in rows:
        if not d:
            continue
        ng = (db.query(func.count(QcRecord.id))
              .filter(QcRecord.date == d, QcRecord.is_pass.is_(False)).scalar() or 0)
        out.append({"date": d, "rows": n, "ng": ng})
    return out


@router.get("/dates")
def dates(db: Session = Depends(get_db), user=Depends(get_current_user)):
    return {"rows": _dates(db)}


def _match_worker(db: Session, rec: QcRecord):
    """回单人 → 装维端账号。**必须同区县同名**，否则视为没账号。

    为什么不能跨区县按名字兜底：重名是常态 —— 洪湖有「彭伟」「陈远清」，
    江陵也有一件「彭伟」的 FTTR 不合格、石首有一件「陈远清」的投诉不合格。
    早先的实现「同区县找不到就取任意区县同名用户」，会把石首的单推给洪湖的另一个人，
    装维端收到的整改单根本不是自己的。宁可显示「未匹配」也不能张冠李戴。

    上游回单人姓名带区县前缀（洪湖市张进），入库时已剥离；
    回单人工号列是手机号，与 users.staff_no 不同源，只作同区县内的兜底。
    """
    name = (rec.handler or "").strip()
    d = db.query(District).filter(District.name == (rec.district or "")).first()
    if name:
        q = db.query(User).filter(User.real_name == name)
        if d:
            u = q.filter(User.district_id == d.id).first()
            if u:
                return u
        else:
            u = q.first()
            if u:
                return u
    no = (rec.handler_no or "").strip()
    if no:
        q = db.query(User).filter((User.staff_no == no) | (User.username == no)
                                  | (User.phone == no))
        if d:
            q = q.filter(User.district_id == d.id)
        u = q.first()
        if u:
            return u
    return None


@router.get("/overview")
def overview(date: str = "", district: str = "", db: Session = Depends(get_db),
             user=Depends(get_current_user)):
    ds = _dates(db)
    if not date:
        date = ds[0]["date"] if ds else ""
    if not date:
        return {"date": "", "has_data": False, "dates": ds}

    all_rows = db.query(QcRecord).filter(QcRecord.date == date).all()
    rows = [r for r in all_rows if not district or district == "全部" or r.district == district]

    scanned = len(rows)
    ng_rows = [r for r in rows if not r.is_pass]
    passed = scanned - len(ng_rows)
    penalty = sum(float(r.penalty or 0) for r in ng_rows)
    no_settle = sum(float(r.no_settle or 0) for r in ng_rows)

    # ---------- 类型维度（区县×类型 的通报口径） ----------
    types = []
    for cat in QC_ORDER:
        sub = [r for r in rows if r.category == cat]
        if not sub:
            continue
        ok_n = sum(1 for r in sub if r.is_pass)
        rate = _r1(ok_n * 100.0 / len(sub))
        thr = QC_THRESHOLD.get(cat, 100.0)
        types.append({
            "category": cat, "scanned": len(sub), "passed": ok_n,
            "ng": len(sub) - ok_n, "rate": rate, "threshold": thr,
            "ok": rate >= thr,
            "penalty": round(sum(float(r.penalty or 0) for r in sub if not r.is_pass), 2),
            "no_settle": round(sum(float(r.no_settle or 0) for r in sub if not r.is_pass), 2),
        })

    # ---------- 区县维度（始终全量展示，便于横向比较） ----------
    districts = []
    for dname in sorted({r.district for r in all_rows if r.district}):
        sub = [r for r in all_rows if r.district == dname]
        sub_ng = [r for r in sub if not r.is_pass]
        rate = _r1((len(sub) - len(sub_ng)) * 100.0 / len(sub)) if sub else 0.0
        weak = []
        for cat in QC_ORDER:
            s2 = [r for r in sub if r.category == cat]
            if not s2:
                continue
            r2 = (sum(1 for r in s2 if r.is_pass)) * 100.0 / len(s2)
            if r2 < QC_THRESHOLD.get(cat, 100.0):
                weak.append(f"{cat}{_r1(r2)}%")
        districts.append({
            "district": dname, "scanned": len(sub), "passed": len(sub) - len(sub_ng),
            "ng": len(sub_ng), "rate": rate,
            "penalty": round(sum(float(r.penalty or 0) for r in sub_ng), 2),
            "no_settle": round(sum(float(r.no_settle or 0) for r in sub_ng), 2),
            "weak": weak,
        })
    districts.sort(key=lambda x: (-x["ng"], -abs(x["no_settle"]), x["district"]))

    # ---------- 不合格原因 Top ----------
    cnt: dict[str, int] = {}
    for r in ng_rows:
        k = (r.ng_item or "未填原因").strip() or "未填原因"
        cnt[k] = cnt.get(k, 0) + 1
    reasons = [{"item": k, "count": v} for k, v in
               sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))][:12]

    # ---------- 推送/整改状态 ----------
    fixed = sum(1 for r in ng_rows if r.fix_status == "fixed")
    pushed = sum(1 for r in ng_rows if r.push_at)
    push = {"ng_total": len(ng_rows), "pushed": pushed, "fixed": fixed,
            "todo": len(ng_rows) - pushed, "fixing": pushed - fixed}

    # 明细里哪些人还没有装维端账号（推送会跳过）—— 只算未推送的，避免每次重算全表
    unmatched_people = []
    seen = set()
    for r in ng_rows:
        if r.push_at or not r.handler or r.handler in seen:
            continue
        seen.add(r.handler)
        if not _match_worker(db, r):
            unmatched_people.append({"handler": r.handler, "district": r.district})
    push["unmatched_people"] = unmatched_people

    return {
        "date": date, "has_data": scanned > 0, "dates": ds,
        "summary": {
            "scanned": scanned, "passed": passed, "ng": len(ng_rows),
            "rate": _r1(passed * 100.0 / scanned) if scanned else 0.0,
            "penalty": round(penalty, 2), "no_settle": round(no_settle, 2),
            "districts": len({r.district for r in rows if r.district and not r.is_pass}),
            "people": len({r.handler for r in rows if r.handler}),
            "types": len(types),
        },
        "types": types, "districts": districts, "reasons": reasons, "push": push,
    }


@router.get("/records")
def records(date: str = "", district: str = "", category: str = "", result: str = "",
            keyword: str = "", push_status: str = "", page: int = 1, size: int = 50,
            db: Session = Depends(get_db), user=Depends(get_current_user)):
    ds = _dates(db)
    if not date:
        date = ds[0]["date"] if ds else ""
    q = db.query(QcRecord).filter(QcRecord.date == date)
    if district and district != "全部":
        q = q.filter(QcRecord.district == district)
    if category:
        q = q.filter(QcRecord.category == category)
    if result == "ng":
        q = q.filter(QcRecord.is_pass.is_(False))
    elif result == "ok":
        q = q.filter(QcRecord.is_pass.is_(True))
    if push_status == "pushed":
        q = q.filter(QcRecord.push_at != "")
    elif push_status == "fixed":
        q = q.filter(QcRecord.fix_status == "fixed")
    elif push_status == "todo":
        q = q.filter(QcRecord.push_at == "")
    if keyword:
        like = f"%{keyword.strip()}%"
        q = q.filter((QcRecord.handler.like(like)) | (QcRecord.order_no.like(like))
                     | (QcRecord.ng_item.like(like)) | (QcRecord.district.like(like)))
    total = q.count()
    rows = (q.order_by(QcRecord.is_pass.asc(), QcRecord.district.asc(), QcRecord.id.asc())
            .offset(max(0, (page - 1) * size)).limit(size).all())
    return {"date": date, "total": total, "page": page, "size": size,
            "rows": [{
                "id": r.id, "date": r.date, "district": r.district, "grid": r.grid,
                "category": r.category, "order_no": r.order_no, "service_no": r.service_no,
                "subject": r.subject, "handler": r.handler, "handler_no": r.handler_no,
                "qc_person": r.qc_person, "result": r.result, "is_pass": bool(r.is_pass),
                "ng_item": r.ng_item, "ng_detail": r.ng_detail,
                "penalty": float(r.penalty or 0), "no_settle": float(r.no_settle or 0),
                "finish_time": r.finish_time,
                "push_to_name": r.push_to_name, "push_at": r.push_at, "push_by": r.push_by,
                "fix_status": r.fix_status, "fix_at": r.fix_at, "fix_note": r.fix_note,
            } for r in rows]}


@router.post("/push")
def push(body: dict, db: Session = Depends(get_db), user=Depends(get_current_user)):
    """把「不通过」记录推到对应装维人员的装维端。

    body: {ids: [], date: '', district: '', category: '', only_unpushed: true}
    ids 为空时按 date/district/category 范围推送（只推不通过、未推送、未整改的）。
    """
    ids = [int(x) for x in (body.get("ids") or [])]
    only_unpushed = body.get("only_unpushed", True)
    q = db.query(QcRecord)
    if ids:
        q = q.filter(QcRecord.id.in_(ids))
    else:
        date = (body.get("date") or "").strip()
        if not date:
            ds = _dates(db)
            date = ds[0]["date"] if ds else ""
        q = q.filter(QcRecord.date == date)
        district = (body.get("district") or "").strip()
        if district and district != "全部":
            q = q.filter(QcRecord.district == district)
        category = (body.get("category") or "").strip()
        if category:
            q = q.filter(QcRecord.category == category)
    rows = q.filter(QcRecord.is_pass.is_(False)).all()
    if not rows:
        raise HTTPException(status_code=400, detail="没有可推送的不合格记录")

    now = _now()
    pushed = skipped = 0
    unmatched: list[dict] = []
    for r in rows:
        if r.fix_status == "fixed":
            skipped += 1
            continue
        if only_unpushed and r.push_at:
            skipped += 1
            continue
        u = _match_worker(db, r)
        if not u:
            skipped += 1
            unmatched.append({"handler": r.handler, "district": r.district,
                              "order_no": r.order_no, "reason": "未找到装维端账号"})
            continue
        r.push_to_id = u.id
        r.push_to_name = u.real_name
        r.push_at = now
        r.push_by = (user.real_name or user.username or "")
        r.fix_status = "pushed"
        r.fix_at = ""
        r.fix_note = ""
        pushed += 1
    db.add(AuditLog(username=user.username, action="质检推送",
                    detail=f"{pushed} 条不规范清单推送至装维端，跳过 {skipped} 条"))
    db.commit()
    return {"pushed": pushed, "skipped": skipped, "unmatched": unmatched[:50],
            "unmatched_count": len(unmatched), "time": now}


@router.post("/unpush")
def unpush(body: dict, db: Session = Depends(get_db), user=Depends(get_current_user)):
    """撤回推送（装维端不再显示），用于误推/推给离职人员。"""
    ids = [int(x) for x in (body.get("ids") or [])]
    if not ids:
        raise HTTPException(status_code=400, detail="缺少 ids")
    n = 0
    for r in db.query(QcRecord).filter(QcRecord.id.in_(ids)).all():
        if r.fix_status == "fixed":
            continue
        r.push_to_id = 0
        r.push_to_name = ""
        r.push_at = ""
        r.push_by = ""
        r.fix_status = ""
        n += 1
    db.commit()
    return {"msg": f"已撤回 {n} 条推送"}
