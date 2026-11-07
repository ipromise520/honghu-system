# -*- coding: utf-8 -*-
"""广义用户清单（6–11 月广义重复清单合并去重，9094 条）

数据由「广义用户清单_导入数据.json」灌入 SQLite 表 broad_user_list，
工单编号为唯一键。本模块只提供只读查询与统计。

CID 智能网关 CSV 与「责任人对应责任田」关系表到位后，直接 UPDATE 补齐
phone / cid_sn / community / owner 四列并更新 match_status 即可，
不改动工单编号主键，也不删除任何记录。
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, get_role
from ..models import User

router = APIRouter(prefix="/broad-user-list", tags=["broad-user-list"])

# 区县别名：源数据同时存在「监利县」（1545 条）与「监利市」（15 条），下拉只展示「监利市」，
# 查询时两者合并统计，避免同一区县被拆成两份。
DISTRICT_ALIAS = {"监利市": ("监利市", "监利县")}

# 数据库列名 ↔ 交付清单中文字段名（与导入 JSON 同源）
FIELDS = [
    ("seq", "序号"),
    ("source_month", "来源月份"),
    ("work_order_no", "工单编号"),
    ("report_date", "工单上报时间"),
    ("setup_date", "工单建立日期"),
    ("district", "归属区县"),
    ("internet_account", "用户上网账号(匹配键)"),
    ("phone", "用户联系电话"),
    ("cid_sn", "CID上报SN"),
    ("community", "归属小区"),
    ("owner", "责任人"),
    ("grid", "最终回单网格"),
    ("is_repeat", "是否重复投诉"),
    ("repeat_times", "重复投诉次数"),
    ("complaint_lv7", "广义投诉七级分类"),
    ("complaint_detail", "广义投诉原因明细"),
    ("phone_source", "联系电话来源"),
    ("owner_source", "责任人来源"),
    ("match_status", "匹配状态"),
    ("unmatch_reason", "未匹配原因"),
    ("source_file", "来源文件"),
    ("source_row", "源行号"),
]
_SELECT = ", ".join('%s AS "%s"' % (col, alias) for col, alias in FIELDS)


def _where(month: str, months: str, dup: int, has_phone: int, nosn: int, keyword: str, district: str):
    parts, params = [], {}
    # months 优先（逗号分隔多选）；单值 month 保留兼容旧调用方
    vals = [m.strip() for m in (months or "").split(",") if m.strip()]
    if vals:
        keys = []
        for i, v in enumerate(vals):
            k = "month_%d" % i
            params[k] = v
            keys.append(":" + k)
        parts.append("source_month IN (%s)" % ", ".join(keys))
    elif month:
        parts.append("source_month = :month")
        params["month"] = month
    if district:
        names = []
        for i, v in enumerate(DISTRICT_ALIAS.get(district, (district,))):
            k = "district_%d" % i
            params[k] = v
            names.append(":" + k)
        parts.append("district IN (%s)" % ", ".join(names))
    if dup:
        parts.append("is_repeat = '是'")
    if has_phone:
        parts.append("phone IS NOT NULL AND phone <> ''")
    if nosn:
        parts.append("(cid_sn IS NULL OR cid_sn = '')")
    if keyword:
        parts.append("(work_order_no LIKE :kw OR internet_account LIKE :kw "
                     "OR community LIKE :kw OR owner LIKE :kw OR district LIKE :kw)")
        params["kw"] = "%%%s%%" % keyword
    return (" WHERE " + " AND ".join(parts)) if parts else "", params


def _scope_district(user: User, district: str) -> str:
    """区县管理员强制只看本区县（忽略前端传参）；市级管理员按所选区县。"""
    if get_role(user) == "district_admin" and user.district:
        return user.district.name
    return district


@router.get("")
def list_rows(page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=200),
             month: str = "", months: str = "", district: str = "",
             dup: int = 0, has_phone: int = 0, nosn: int = 0,
             keyword: str = "",
             db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    district = _scope_district(user, district)
    where, params = _where(month, months, dup, has_phone, nosn, keyword, district)
    total = db.execute(text("SELECT COUNT(*) FROM broad_user_list" + where), params).scalar() or 0
    rows = db.execute(
        text("SELECT %s FROM broad_user_list%s ORDER BY seq LIMIT :lim OFFSET :off" % (_SELECT, where)),
        dict(params, lim=size, off=(page - 1) * size),
    ).mappings().all()
    return {"total": total, "page": page, "size": size, "items": [dict(r) for r in rows]}


@router.get("/stats")
def stats(month: str = "", months: str = "", district: str = "", dup: int = 0, has_phone: int = 0,
          nosn: int = 0, keyword: str = "",
          db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """统计卡与列表共用同一套筛选条件（月份多选/区县/状态/关键字），保证口径一致。"""
    district = _scope_district(user, district)
    where, params = _where(month, months, dup, has_phone, nosn, keyword, district)
    row = db.execute(text(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN is_repeat = '是' THEN 1 ELSE 0 END) AS dup, "
        "SUM(CASE WHEN phone IS NOT NULL AND phone <> '' THEN 1 ELSE 0 END) AS phone, "
        "SUM(CASE WHEN cid_sn IS NOT NULL AND cid_sn <> '' THEN 1 ELSE 0 END) AS cid_matched, "
        "SUM(CASE WHEN match_status LIKE '待%' THEN 1 ELSE 0 END) AS pending "
        "FROM broad_user_list" + where), params).mappings().first()
    months = [{"month": r[0], "count": r[1]} for r in db.execute(text(
        "SELECT source_month, COUNT(*) FROM broad_user_list" + where +
        " GROUP BY source_month ORDER BY MIN(seq)"), params)]
    grand_total = db.execute(text("SELECT COUNT(*) FROM broad_user_list")).scalar() or 0
    return {"total": row["total"] or 0, "dup": row["dup"] or 0,
            "phone": row["phone"] or 0, "cid_matched": row["cid_matched"] or 0,
            "pending": row["pending"] or 0, "months": months, "grand_total": grand_total}
