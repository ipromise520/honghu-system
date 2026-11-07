# -*- coding: utf-8 -*-
"""质检规范（五项通报合一）接口

数据源：市公司每天下发的 **5 个质检表项**（经「数据同步」一次多选上传入库到 qc_records）
  1. 人工质检明细      → 投诉 / 义诊 / 延伸服务 / 移动看家 / 智能组网 / 装机（抽检）
  2. FTTR质检工单汇总表 → FTTR质检（全量）
  3. 企宽装机质检统计   → 企宽装机质检（全量）
  4. 装、维改约质检通报 → 装机改约质检 / 投诉改约质检（听音判真实·虚假）
  5. 集团智能质检结果   → 集团AI质检（AI 图像质检）

通报分析维度：
  · 卡片区：五个来源各自一张卡（抽检量 / 合格 / 不合格 / 合格率 / 待推送）
  · 类型维度：11 个通报类型 × 抽检量/合格量/合格率/达标线/考核/不结算
  · 区县维度：不合格件数 / 考核 / 不结算 / 短板类型
  · 全市：不合格原因 Top（不规范项聚合）

推送：把「不通过」记录按 责任人 → 装维人员账号 匹配后推到装维端（/m/qc），
      装维人员提交整改后回写 fix_status='fixed'，页面据此显示整改结果。

分组键是 report_date（通报批次日）而非 date（数据日期）：
改约通报用前一日的数据、集团AI质检自带独立日期标注，五个来源的数据日本来就不齐，
按各自数据日分组会把一张通报拆成两天，看不全。
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, require_admin
from ..models import QcRecord, District, User, AuditLog
from ..qc_rules import (QC_ORDER, QC_THRESHOLD, QC_SOURCE_ORDER,
                        QC_SOURCE_LABEL, QC_SOURCE_TYPES)

router = APIRouter(prefix="/qc", tags=["qc"])


def _r1(v: float) -> float:
    return round(float(v or 0), 1)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _dates(db: Session) -> list[dict]:
    """通报批次日列表（倒序）。olds：同一天合并了几个来源、多少行、多少件不合格。

    单条聚合查询：不合格数与来源数用条件聚合/COUNT(DISTINCT) 一次算完，
    避免按批次日逐日再查 2 次（N+1）。
    """
    rows = (db.query(QcRecord.report_date,
                     func.count(QcRecord.id),
                     func.sum(case((QcRecord.is_pass.is_(False), 1), else_=0)),
                     func.count(func.distinct(QcRecord.source)))
            .filter(QcRecord.report_date != "")
            .group_by(QcRecord.report_date)
            .order_by(QcRecord.report_date.desc()).all())
    return [{"date": d, "rows": int(n or 0), "ng": int(ng or 0), "sources": int(src or 0)}
            for d, n, ng, src in rows if d]


@router.get("/dates")
def dates(db: Session = Depends(get_db), user=Depends(get_current_user)):
    rows = _dates(db)
    src_opts = sorted({s for (s,) in db.query(QcRecord.source).distinct().all() if s})
    return {"rows": rows, "sources": src_opts}


def _match_worker(db: Session, rec: QcRecord):
    """责任人 → 装维端账号。**必须同区县同名**，否则视为没账号。

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


def _base_date(db: Session, date: str, ds: list) -> str:
    if not date:
        date = ds[0]["date"] if ds else ""
    return date


@router.get("/overview")
def overview(date: str = "", district: str = "", db: Session = Depends(get_db),
             user=Depends(get_current_user)):
    ds = _dates(db)
    date = _base_date(db, date, ds)
    if not date:
        return {"date": "", "has_data": False, "dates": ds, "sources": []}

    all_rows = db.query(QcRecord).filter(QcRecord.report_date == date).all()
    rows = [r for r in all_rows if not district or district == "全部" or r.district == district]

    scanned = len(rows)
    ng_rows = [r for r in rows if not r.is_pass]
    passed = scanned - len(ng_rows)
    penalty = sum(float(r.penalty or 0) for r in ng_rows)
    no_settle = sum(float(r.no_settle or 0) for r in ng_rows)

    # ---------- 卡片区：五个通报来源各一张（卡片区通报） ----------
    sources = []
    for src in QC_SOURCE_ORDER:
        sub = [r for r in rows if r.source == src]
        if not sub:
            continue
        s_ng = [r for r in sub if not r.is_pass]
        rate = _r1((len(sub) - len(s_ng)) * 100.0 / len(sub))
        thr = QC_SOURCE_TYPES.get(src) or []
        ok = [QC_THRESHOLD.get(t) for t in thr]
        ok = [t for t in ok if t is not None]
        # 该来源有自己的达标线才判达标，否则留空（改约只通报「虚假占比」，没有达标线）
        thr_val = min(ok) if ok else None
        people = len({r.handler for r in s_ng if r.handler})
        sources.append({
            "source": src, "label": QC_SOURCE_LABEL.get(src, src),
            "scanned": len(sub), "passed": len(sub) - len(s_ng), "ng": len(s_ng),
            "rate": rate, "ok": (rate >= thr_val) if thr_val is not None else None,
            "threshold": _r1(thr_val) if thr_val is not None else None,
            "people": people,
            "penalty": round(sum(float(r.penalty or 0) for r in s_ng), 2),
            "no_settle": round(sum(float(r.no_settle or 0) for r in s_ng), 2),
            "todo": sum(1 for r in s_ng if not r.push_at),
            "types": sorted({r.category for r in sub}),
        })

    # ---------- 类型维度（区县×类型 的通报口径） ----------
    types = []
    for cat in QC_ORDER:
        sub = [r for r in rows if r.category == cat]
        if not sub:
            continue
        ok_n = sum(1 for r in sub if r.is_pass)
        rate = _r1(ok_n * 100.0 / len(sub))
        thr = QC_THRESHOLD.get(cat)
        types.append({
            "category": cat, "scanned": len(sub), "passed": ok_n,
            "ng": len(sub) - ok_n, "rate": rate,
            "threshold": None if thr is None else _r1(thr),
            "ok": None if thr is None else rate >= thr,
            "source": next((r.source for r in sub if r.source), ""),
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
            thr = QC_THRESHOLD.get(cat)
            if thr is None:
                continue
            r2 = (sum(1 for r in s2 if r.is_pass)) * 100.0 / len(s2)
            if r2 < thr:
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

    # ---------- 推送 / 整改状态 ----------
    fixed = sum(1 for r in ng_rows if r.fix_status == "fixed")
    pushed = sum(1 for r in ng_rows if r.push_at)
    push = {"ng_total": len(ng_rows), "pushed": pushed, "fixed": fixed,
            "todo": len(ng_rows) - pushed, "fixing": pushed - fixed}

    unmatched_people = []
    seen = set()
    for r in ng_rows:
        if r.push_at or not r.handler or r.handler in seen:
            continue
        seen.add(r.handler)
        if not _match_worker(db, r):
            unmatched_people.append({"handler": r.handler, "district": r.district,
                                     "source": r.source})
    push["unmatched_people"] = unmatched_people

    return {
        "date": date, "has_data": scanned > 0, "dates": ds,
        "summary": {
            "scanned": scanned, "passed": passed, "ng": len(ng_rows),
            "rate": _r1(passed * 100.0 / scanned) if scanned else 0.0,
            "penalty": round(penalty, 2), "no_settle": round(no_settle, 2),
            "districts": len({r.district for r in rows if r.district and not r.is_pass}),
            "people": len({r.handler for r in rows if r.handler}),
            "types": len(types), "sources": len(sources),
            "data_dates": sorted({r.date for r in rows if r.date}),
        },
        "types": types, "districts": districts, "reasons": reasons,
        "sources": sources, "push": push,
    }


@router.get("/records")
def records(date: str = "", district: str = "", category: str = "", source: str = "",
            result: str = "", keyword: str = "", push_status: str = "",
            page: int = 1, size: int = 50,
            db: Session = Depends(get_db), user=Depends(get_current_user)):
    ds = _dates(db)
    date = _base_date(db, date, ds)
    q = db.query(QcRecord).filter(QcRecord.report_date == date)
    if district and district != "全部":
        q = q.filter(QcRecord.district == district)
    if category:
        q = q.filter(QcRecord.category == category)
    if source:
        q = q.filter(QcRecord.source == source)
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
                "id": r.id, "date": r.date, "report_date": r.report_date, "source": r.source,
                "district": r.district, "grid": r.grid, "category": r.category,
                "order_no": r.order_no, "service_no": r.service_no, "subject": r.subject,
                "handler": r.handler, "handler_no": r.handler_no, "qc_person": r.qc_person,
                "result": r.result, "is_pass": bool(r.is_pass),
                "ng_item": r.ng_item, "ng_detail": r.ng_detail,
                "penalty": float(r.penalty or 0), "no_settle": float(r.no_settle or 0),
                "finish_time": r.finish_time,
                "push_to_name": r.push_to_name, "push_at": r.push_at, "push_by": r.push_by,
                "fix_status": r.fix_status, "fix_at": r.fix_at, "fix_note": r.fix_note,
            } for r in rows]}


@router.post("/push")
def push(body: dict, db: Session = Depends(get_db), user=Depends(require_admin)):
    """把「不通过」记录推到对应装维人员的装维端（仅管理员）。

    body: {ids: [], date: '', district: '', category: '', source: '', only_unpushed: true}
    ids 为空时按 date/district/category/source 范围推送（只推不通过、未推送、未整改的）。
    """
    ids = [int(x) for x in (body.get("ids") or [])]
    only_unpushed = body.get("only_unpushed", True)
    q = db.query(QcRecord)
    if ids:
        q = q.filter(QcRecord.id.in_(ids))
    else:
        ds = _dates(db)
        date = _base_date(db, (body.get("date") or "").strip(), ds)
        if not date:
            raise HTTPException(status_code=400, detail="没有可推送的通报批次")
        q = q.filter(QcRecord.report_date == date)
        district = (body.get("district") or "").strip()
        if district and district != "全部":
            q = q.filter(QcRecord.district == district)
        category = (body.get("category") or "").strip()
        if category:
            q = q.filter(QcRecord.category == category)
        source = (body.get("source") or "").strip()
        if source:
            q = q.filter(QcRecord.source == source)
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
def unpush(body: dict, db: Session = Depends(get_db), user=Depends(require_admin)):
    """撤回推送（仅管理员），用于误推/推给离职人员。"""
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
