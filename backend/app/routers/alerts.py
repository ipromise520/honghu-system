# -*- coding: utf-8 -*-
"""省公司139邮箱预警邮件：拉取最新「待处理工单预警」附件 → 荆州数据入库（替换式快照）"""
import imaplib
import email as email_lib
import io
import math
import ssl
import re
from email.header import decode_header

import pandas as pd
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..config import MAIL_USER, MAIL_AUTH
from ..database import get_db
from ..deps import get_current_user, require_admin
from ..models import (AlertMeta, AlertOrder, ReportMeta, ReportDistrict, ReportTimeout,
                      ReportWideOrder, ReportComplaintOrder, ReportH5Record, ReportDupOrder,
                      InstallOrder, ComplaintOrder, SyncJob)
from .sync import sync_wide_batch, _to_num, _norm_time, _strip_district_prefix

router = APIRouter(prefix="/alerts", tags=["alerts"])

SUBJECT_KEY = "待处理工单预警"
REPORT_FROM = "13135792966@139.com"
REPORT_SUBJECT_KEY = "重要通报"
CITY = "荆州"
JZ_DISTRICTS = {"沙市区", "荆州区", "公安县", "监利市", "江陵县", "石首市", "洪湖市", "松滋市", "荆州开发区"}


def _dec(s: str) -> str:
    if not s:
        return ""
    out = ""
    for t, enc in decode_header(s):
        out += t.decode(enc or "utf-8", "ignore") if isinstance(t, bytes) else t
    return out


def _conn() -> imaplib.IMAP4_SSL:
    """139邮箱 IMAP 需兼容老 TLS 套件（SECLEVEL=1）"""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
    m = imaplib.IMAP4_SSL("imap.139.com", 993, ssl_context=ctx, timeout=60)
    m.login(MAIL_USER, MAIL_AUTH)
    m.select("INBOX")
    return m


def _s(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("nan", "nat", "none") else s


def _t(v) -> str:
    """时间列：datetime → 'YYYY-MM-DD HH:MM:SS'"""
    try:
        if v is None or pd.isna(v):
            return ""
    except Exception:
        pass
    if hasattr(v, "strftime"):
        try:
            return v.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return ""
    s = str(v).strip()
    return "" if s.lower() in ("nan", "nat", "none") else s


def fetch_mail() -> dict:
    """拉取最新预警邮件，返回 {subject, mail_date, attachments: {name: bytes}}"""
    m = _conn()
    try:
        _, data = m.search(None, "ALL")
        ids = data[0].split()
        latest = None
        subject = ""
        for i in reversed(ids[-80:]):                     # 最近80封内找主题
            _, md = m.fetch(i, "(BODY[HEADER.FIELDS (SUBJECT)])")
            s = _dec(email_lib.message_from_bytes(md[0][1]).get("Subject"))
            if SUBJECT_KEY in s:
                latest, subject = i, s
                break
        if latest is None:
            raise RuntimeError("收件箱最近邮件中未找到「待处理工单预警」")
        _, md = m.fetch(latest, "(RFC822)")
        msg = email_lib.message_from_bytes(md[0][1])
        atts = {}
        for part in msg.walk():
            fn = part.get_filename()
            if fn:
                raw = part.get_payload(decode=True)
                if raw:
                    atts[_dec(fn)] = raw
        return {"subject": subject, "mail_date": msg.get("Date") or "", "attachments": atts}
    finally:
        m.logout()


def _pick(atts: dict, key: str) -> bytes:
    for name, raw in atts.items():
        if key in name:
            return raw
    raise RuntimeError(f"邮件附件缺少「{key}」")


def _parse(raw: bytes) -> pd.DataFrame:
    return pd.read_excel(io.BytesIO(raw), sheet_name=0, header=2, engine="openpyxl")


def _jz(df: pd.DataFrame) -> pd.DataFrame:
    """只保留荆州地市数据"""
    col = "地市名称" if "地市名称" in df.columns else df.columns[1]
    return df[df[col].astype(str).str.contains(CITY, na=False)]


def _export_time(raw: bytes) -> str:
    try:
        head = pd.read_excel(io.BytesIO(raw), sheet_name=0, header=None, nrows=2, engine="openpyxl")
        for v in head.values.flatten():
            s = str(v)
            mm = re.search(r"导出时间[：:](.+)", s)
            if mm:
                return mm.group(1).strip()
    except Exception:
        pass
    return ""


def sync_alerts(db: Session) -> dict:
    """拉取→解析→替换入库（只保留最新快照）；附件允许缺省（如部分时段无监控预警）"""
    info = fetch_mail()
    atts = info["attachments"]
    df1 = _jz(_parse(_pick(atts, "投诉清单")))          # 装机+投诉清单（必需）
    df2 = df3 = None
    export_time = _export_time(_pick(atts, "投诉清单"))
    try:                                                # 预约状态清单（可选）
        df2 = _jz(_parse(_pick(atts, "预约状态清单")))
    except RuntimeError:
        pass
    try:                                                # 人员单量预警（可选，部分时段无此附件）
        df3 = _jz(_parse(_pick(atts, "监控预警")))
    except RuntimeError:
        pass
    # 注：千里眼.xlsx(千里眼-加装工单清单)/待区县.xlsx(家宽投诉施工单) 均非人员预警表，不作为监控预警数据源

    rows: list[dict] = []
    # ---- 1) 装机/投诉在途清单 ----
    for _, r in df1.iterrows():
        typ = "complaint" if _s(r.get("类型")) == "投诉" else "install"
        rows.append(dict(
            type=typ, district=_s(r.get("区县名称")), grid=_s(r.get("网格名称")),
            handler=_s(r.get("处理人姓名")), order_no=_s(r.get("定单编码")),
            service_no=_s(r.get("服务号码")), order_type=_s(r.get("工单类型")),
            status=_s(r.get("工单状态")), community=_s(r.get("小区名称")),
            is_fttr=_s(r.get("是否FTTR")), is_high_quality=_s(r.get("是否高品质")),
            accept_time=_t(r.get("受理时间_秒")), appoint_time=_t(r.get("预约时间_秒")),
            visit_time=_t(r.get("上门时间_秒")), punch_time=_t(r.get("最后一次上门打卡时间")),
            pkg_name=_s(r.get("智能组网套餐名称"))))
    n_install = sum(1 for r in rows if r["type"] == "install")
    n_complaint = sum(1 for r in rows if r["type"] == "complaint")
    # ---- 2) 预约异常（附件缺省时为 0）----
    for _, r in (df2.iterrows() if df2 is not None else []):
        rows.append(dict(
            type="book", district=_s(r.get("区县名称")), grid=_s(r.get("网格名称")),
            handler=_s(r.get("处理人姓名")), order_no=_s(r.get("定单编码")),
            service_no=_s(r.get("服务号码")), community=_s(r.get("小区名称")),
            status=_s(r.get("定单状态")), book_status=_s(r.get("预约状态")),
            appoint_time=_t(r.get("预约时间_秒")), visit_time=_t(r.get("上门时间_秒")),
            supervise_info=_s(r.get("督办信息")), supervise_person=_s(r.get("督办人员"))))
    n_book = 0 if df2 is None else len(df2)
    # ---- 3) 人员单量预警（附件缺省时为 0）----
    for _, r in (df3.iterrows() if df3 is not None else []):
        rows.append(dict(
            type="monitor", district=_s(r.get("区县名称")), grid=_s(r.get("网格名称")),
            handler=_s(r.get("人员名称")), warn_level=_s(r.get("预警级别")),
            warn_time=_t(r.get("预警时间_秒")),
            order_count=int(r.get("工单量") or 0) if _s(r.get("工单量")) else 0))
    n_yellow = sum(1 for r in rows if r["type"] == "monitor" and "黄" in r["warn_level"])
    n_red = sum(1 for r in rows if r["type"] == "monitor" and "红" in r["warn_level"])

    # ---- 替换式入库：清旧 → 插新 → 更新 meta ----
    db.query(AlertOrder).delete()
    CHUNK = 2000
    for i in range(0, len(rows), CHUNK):
        db.bulk_insert_mappings(AlertOrder, rows[i:i + CHUNK])
    meta = db.get(AlertMeta, 1) or AlertMeta(id=1)
    meta.mail_subject = info["subject"]
    meta.mail_time = _mail_time_cn(info["mail_date"], info["subject"])
    meta.export_time = export_time
    from datetime import datetime
    meta.fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    meta.install_total = n_install
    meta.complaint_total = n_complaint
    meta.book_abnormal = n_book
    meta.warn_yellow = n_yellow
    meta.warn_red = n_red
    db.add(meta)
    db.commit()
    return {"subject": info["subject"], "mail_time": meta.mail_time, "export_time": export_time,
            "install": n_install, "complaint": n_complaint, "book": n_book,
            "warn_yellow": n_yellow, "warn_red": n_red}


def _mail_time_cn(mail_date: str, subject: str) -> str:
    """数据时间：优先取主题（如 10.18-17点 → 2026-10-18 17时），否则邮件 Date"""
    mm = re.search(r"(\d{1,2})\.(\d{1,2})-(\d{1,2})点", subject)
    if mm:
        mo, d, h = mm.groups()
        return f"2026-{int(mo):02d}-{int(d):02d} {int(h):02d}时"
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(mail_date)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return mail_date


# ---------------- 接口 ----------------

# ---------------- 省公司【重要通报】邮件 ----------------

def _pct(v):
    """0.9839 → 98.4；已是百分数或文本则原样；NaN/空 → 空串"""
    try:
        f = float(v)
        if not math.isfinite(f):
            return ""
        return round(f * 100, 1) if 0 <= f <= 1.5 else round(f, 1)
    except Exception:
        return _s(v)


def _num(v, as_int=True):
    try:
        f = float(v)
        return int(f) if as_int else round(f, 2)
    except Exception:
        return 0 if as_int else 0.0


def _find_sheet(xls, keywords):
    for sh in xls.sheet_names:
        if all(k in sh for k in keywords):
            return sh
    return None


def _dist_row(df, col, name):
    """按区县名取一行（兼容 '区 县' 等列名变体）"""
    sub = df[df[col].astype(str).str.replace(" ", "").str.contains(name, na=False)]
    return sub.iloc[0] if len(sub) else None


def fetch_report_mail() -> dict:
    """按发件人找最新【重要通报】邮件"""
    m = _conn()
    try:
        _, data = m.search(None, "FROM", f'"{REPORT_FROM}"')
        ids = data[0].split()
        latest, subject = None, ""
        for i in reversed(ids[-10:]):
            _, md = m.fetch(i, "(BODY[HEADER.FIELDS (SUBJECT)])")
            s = _dec(email_lib.message_from_bytes(md[0][1]).get("Subject"))
            if REPORT_SUBJECT_KEY in s:
                latest, subject = i, s
                break
        if latest is None:
            raise RuntimeError("未找到【重要通报】邮件")
        _, md = m.fetch(latest, "(RFC822)")
        msg = email_lib.message_from_bytes(md[0][1])
        atts = {}
        for part in msg.walk():
            fn = part.get_filename()
            if fn:
                raw = part.get_payload(decode=True)
                clean = _dec(fn).strip().rstrip(";").strip()
                if raw and clean.lower().endswith(("xlsx", "xls")):
                    atts[clean] = raw
        if not atts:
            raise RuntimeError("邮件中无 Excel 附件")
        return {"subject": subject, "mail_date": msg.get("Date") or "", "attachments": atts}
    finally:
        m.logout()


def sync_report(db: Session) -> dict:
    """拉取最新【重要通报】→ 解析 5 类区县指标 + 超时盯控明细 → 替换入库"""
    import io, json as _json
    from datetime import datetime as _dt
    info = fetch_report_mail()
    atts = info["attachments"]
    inst_raw = next((v for k, v in atts.items() if "装机指标" in k), None)
    comp_raw = next((v for k, v in atts.items() if "投诉" in k), None)
    if not inst_raw or not comp_raw:
        raise RuntimeError(f"附件不完整：{list(atts.keys())}")

    recs = []   # (kind, district, data_dict)
    _seen = set()

    def _put(kind, d, data):
        """白名单只收荆州9区县；H5/通报 sheet 含全省多段快照，每类每区县只保留首段（月累计）"""
        if d in JZ_DISTRICTS and (kind, d) not in _seen:
            _seen.add((kind, d))
            recs.append((kind, d, data))

    # ---- 1) 装机通报（sheet 装机，标题2行+列名行 → header=2；第一段后还有FTTR段，靠 _seen 排除）----
    xls1 = pd.ExcelFile(io.BytesIO(inst_raw), engine="openpyxl")
    sh = _find_sheet(xls1, ["装机"]) or xls1.sheet_names[0]
    df = pd.read_excel(io.BytesIO(inst_raw), sheet_name=sh, header=2, engine="openpyxl")
    if "区县" in df.columns:
        for _, r in df.iterrows():
            _put("install", _s(r.get("区县")), {
                "month_order": _num(r.get("当月宽带收单数")), "month_done": _num(r.get("当月宽带竣工数")),
                "month_rate": _pct(r.get("当月宽带竣工率")), "day_order": _num(r.get("当日装机收单数")),
                "day_done": _num(r.get("当日装机竣工数")), "day_rate": _pct(r.get("当日宽带竣工率")),
                "untimely": _num(r.get("当月装机不及时数")), "timely_rate": _pct(r.get("当月装机及时率")),
                "timeout_orders": _num(r.get("当月超时工单数")), "timeout_rate": _pct(r.get("当月超时工单占比")),
                "visit_untimely": _num(r.get("当月装机上门不及时数")), "visit_timely_rate": _pct(r.get("当月装机上门及时率"))})
    # ---- 2) 装机 H5（sheet H5，标题1行+列名行 → header=1；首段为荆州月累计，后续为全省分日快照）----
    sh = _find_sheet(xls1, ["H5"])
    if sh and "明细" not in sh:
        df = pd.read_excel(io.BytesIO(inst_raw), sheet_name=sh, header=1, engine="openpyxl")
        col0 = df.columns[0]
        for _, r in df.iterrows():
            _put("h5_install", _s(r.get(col0)), {
                "satisfaction": _pct(r.get("满意度")), "q24": _pct(r.get("Q24上门及时性")),
                "q25": _pct(r.get("Q25人员服务")), "q26": _pct(r.get("Q26安装效果")),
                "install_cnt": _num(r.get("装机数")), "joined": _num(r.get("参评数")),
                "join_rate": _pct(r.get("参评率")), "day_joined": _num(r.get("当日参评数")),
                "day_join_rate": _pct(r.get("当日参评率"))})
    # ---- 3) 装机超时盯控明细（sheet 超时明细）----
    sh = _find_sheet(xls1, ["超时"])
    timeouts = []
    if sh:
        df = pd.read_excel(io.BytesIO(inst_raw), sheet_name=sh, header=0, engine="openpyxl")
        for _, r in df.iterrows():
            if _s(r.get("地市")) != CITY:
                continue
            timeouts.append(dict(
                district=_s(r.get("区县")), order_no=_s(r.get("单号")), theme=_s(r.get("定单主题")),
                accept_time=_t(r.get("受理时间")), appoint_end=_t(r.get("预约结束时间")),
                step=_s(r.get("当前环节")), handler=_s(r.get("当前处理人")),
                phone=_s(r.get("当前处理人电话")), ctrl_type=_s(r.get("预约盯控类型"))))
    # ---- 4) 投诉通报（sheet 投诉指标通报，header=1；白名单排除荆州市全市行/通报文字行）----
    xls2 = pd.ExcelFile(io.BytesIO(comp_raw), engine="openpyxl")
    sh = _find_sheet(xls2, ["投诉指标通报"])
    if sh:
        df = pd.read_excel(io.BytesIO(comp_raw), sheet_name=sh, header=1, engine="openpyxl")
        for _, r in df.iterrows():
            _put("complaint", _s(r.get("区县")), {
                "total": _num(r.get("工单总量")), "day_timeout": _num(r.get("当当慢超时工单量")),
                "unfinished": _num(r.get("未完工")), "hq_normal_rate": _pct(r.get("高品质+普通及时率")),
                "hq_rate": _pct(r.get("高品质投诉处理及时率")), "normal_rate": _pct(r.get("普通用户投诉处理及时率(当日修)")),
                "visit_rate": _pct(r.get("投诉上门处理及时率")), "comm_rate": _pct(r.get("省公司通信连接及时率")),
                "platinum_rate": _pct(r.get("省公司全球通白金卡以上及时率")),
                "comm_duration": _num(r.get("省公司通信连接投诉处理时长"), as_int=False),
                "vip_rate": _pct(r.get("VIP处理及时率")), "hv_rate": _pct(r.get("高价值处理及时率")),
                "vip_hv_duration": _num(r.get("VIP及钻石卡平均处理时长"), as_int=False),
                "h5_join_rate": _pct(r.get("H5参与率")), "h5_satisfaction": _pct(r.get("H5满意度")),
                "dup_rate": _pct(r.get("广义重复投诉率"))})
    # ---- 5) 投诉 H5 通报（header=0；首段为月累计：满意度/Q27/Q28/Q29/FTTR/工单数/参评数/参评率/当日回单/当日完成测评/当日引导参评率）----
    sh = _find_sheet(xls2, ["投诉H5"]) or _find_sheet(xls2, ["H5"])
    if sh and "清单" not in sh:
        df = pd.read_excel(io.BytesIO(comp_raw), sheet_name=sh, header=0, engine="openpyxl")
        cols = df.columns
        for _, r in df.iterrows():
            vals = list(r.values)
            _put("h5_complaint", _s(r.get(cols[0])), {
                "satisfaction": _pct(vals[1]), "q27": _pct(vals[2]), "q28": _pct(vals[3]),
                "q29": _pct(vals[4]), "fttr_satisfaction": _pct(vals[5]),
                "orders": _num(vals[6]), "joined": _num(vals[7]), "join_rate": _pct(vals[8]),
                "day_back": _num(vals[9]) if len(vals) > 9 else 0,
                "day_joined": _num(vals[10]) if len(vals) > 10 else 0,
                "day_join_rate": _pct(vals[11]) if len(vals) > 11 else ""})
    # ---- 6) 广义重复投诉通报（标题1行+列名行 → header=1，左栏 5 列）----
    sh = _find_sheet(xls2, ["广义重复通报"])
    if sh:
        df = pd.read_excel(io.BytesIO(comp_raw), sheet_name=sh, header=1, engine="openpyxl")
        cols = list(df.columns)
        for _, r in df.iterrows():
            _put("dup", _s(r.get(cols[0])), {
                "dup_users": _num(r.get(cols[1])), "total": _num(r.get(cols[2])),
                "dup_rate": _pct(r.get(cols[3])), "rank": _num(r.get(cols[4]))})

    # ---- 7) 明细①：装机宽表明细（荆州全部，用于上门不及时下钻）----
    def _col(df, *prefixes):
        """按前缀模糊找列名（附件列名带后缀/换行，如 装维人员姓名(ServStaffName)）"""
        for p in prefixes:
            for c in df.columns:
                if str(c).startswith(p):
                    return c
        return None

    wide_rows = []
    inst_rows = []                      # 联动看板：InstallOrder 全字段映射
    sh = _find_sheet(xls1, ["宽表明细"])
    if sh:
        df = pd.read_excel(io.BytesIO(inst_raw), sheet_name=sh, header=0, engine="openpyxl")
        df.columns = [str(c).replace("\n", "").strip() for c in df.columns]   # 列名含换行清洗（如'预约时间段结束时间'）
        if "分公司" in df.columns:
            df = df[df["分公司"].astype(str).str.contains(CITY, na=False)]
        vcol = _col(df, "是否及时上门.1") or _col(df, "是否及时上门")
        for _, r in df.iterrows():
            wide_rows.append(dict(
                district=_s(r.get("区县")), grid=_s(r.get("网格")), order_no=_s(r.get("定单编码")),
                theme=_s(r.get("业务主题")), broadband_type=_s(r.get("宽带类型")),
                status=_s(r.get("定单状态")), step=_s(r.get("当前环节")),
                accept_time=_t(r.get("受理时间")), appoint_end=_t(r.get("预约结束时间")),
                handler=_s(r.get("当前处理人或回单人")),
                is_timely=str(_num(r.get("是否及时"))), visit_timely=str(_num(r.get(vcol))),
                fttr_over=str(_num(r.get("FTTR超2.5小时"))), is_high_quality=_s(r.get("高品质标签"))))
            accept = _norm_time(_t(r.get("受理时间")))
            pkg = _s(r.get("智能组网套餐编码"))
            inst_rows.append(dict(
                order_id=_s(r.get("定单编码")), month=accept[:7], district=_s(r.get("区县")),
                grid=_s(r.get("网格")), accept_time=accept, order_theme=_s(r.get("业务主题")),
                status=_s(r.get("定单状态")), broadband_type=_s(r.get("宽带类型")),
                is_fttr="是" if pkg.startswith("全光组网") else "",
                handler=_strip_district_prefix(r.get("当前处理人或回单人"), r.get("区县")),
                completion_time=_norm_time(_t(r.get("回单时间"))),
                is_24h=_s(r.get("24小时及时")), is_8h=_s(r.get("8小时及时")),
                is_perform=_s(r.get("是否履约(新)") or r.get("是否履约")),
                is_refund=_s(r.get("是否退单")), refund_reason=_s(r.get("一级退单原因")),
                install_duration=_to_num(r.get("装机时长"), as_int=False),
                completion_duration=_to_num(r.get("完工时长"), as_int=False),
                h5_satisfaction=_s(r.get("H5装机满意度")),
                appointment_time=_norm_time(_t(r.get("预约时间段结束时间") or r.get("首次预约时间"))),
                is_high_quality=_s(r.get("高品质标签")), current_step=_s(r.get("当前环节"))))

    # ---- 8) 明细②：投诉受理明细（当日修及时性/全球通标识，用于投诉及时率与全球通下钻）----
    comp_rows = []
    comp_order_rows = []                # 联动看板：ComplaintOrder 全字段映射
    sh = _find_sheet(xls2, ["受理"])
    if sh:
        df = pd.read_excel(io.BytesIO(comp_raw), sheet_name=sh, header=0, engine="openpyxl")
        df.columns = [str(c).replace("\n", "").strip() for c in df.columns]
        if "地市" in df.columns:
            df = df[df["地市"].astype(str).str.contains(CITY, na=False)]
        for _, r in df.iterrows():
            comp_rows.append(dict(
                district=_s(r.get("区县")), flow_no=_s(r.get("工单流水号")), theme=_s(r.get("投诉主题")),
                category=_s(r.get("投诉分类")), accept_time=_t(r.get("投诉时间")),
                status=_s(r.get("智慧装维工单状态")), handler=_s(r.get("当前处理人")),
                phone=_s(r.get("当前处理人电话")), grid=_s(r.get("最终回单网格")),
                duration=_s(r.get("投诉处理时长")), visit_timely=_s(r.get("是否上门及时")),
                day_timely=_s(r.get("当日修是否及时")), global_pass=_s(r.get("全球通标识")),
                is_high_quality=_s(r.get("是否高品质")), account=_s(r.get("宽带帐号"))))
            accept = _norm_time(_t(r.get("投诉时间")))
            dur_raw = _s(r.get("工单时长")) or _s(r.get("投诉处理时长"))
            comp_order_rows.append(dict(
                eoms_no=_s(r.get("工单流水号")), month=accept[:7], district=_s(r.get("区县")),
                accept_time=accept, status=_s(r.get("智慧装维工单状态")),
                handler=_strip_district_prefix(r.get("当前处理人"), r.get("区县")),
                complaint_category=_s(r.get("投诉分类")),
                is_24h=_s(r.get("24小时是否及时")), is_8h=_s(r.get("8小时是否及时")),
                is_perform=_s(r.get("是否履约")),
                order_duration=_to_num(dur_raw, as_int=False),
                is_solved=_s(r.get("是否维修完成") or r.get("故障是否解决") or r.get("最终处理结果")),
                satisfaction=_s(r.get("归档满意度")),
                appointment_time=_norm_time(_t(r.get("预约上门时间止") or r.get("首次预约时间"))),
                grid=_s(r.get("首次派单目标网格") or r.get("最终回单网格")),
                is_high_quality=_s(r.get("是否高品质")), global_pass=_s(r.get("全球通标识"))))

    # ---- 9) 明细③：投诉H5参评记录（按装维人员统计参评率）----
    h5_rows = []
    sh = _find_sheet(xls2, ["H5清单"])
    if sh:
        df = pd.read_excel(io.BytesIO(comp_raw), sheet_name=sh, header=0, engine="openpyxl")
        c_city = _col(df, "地市标识")
        c_dist = _col(df, "区县名称")
        c_staff = _col(df, "装维人员姓名")
        c_sid = _col(df, "装维人员工号")
        c_ord = _col(df, "装维工单编码")
        c_phone = _col(df, "目标手机号码")
        c_send = _col(df, "下发时间")
        c_reply = _col(df, "回复时间")
        if c_city is not None:
            df = df[df[c_city].astype(str).str.contains(CITY, na=False)]
        for _, r in df.iterrows():
            h5_rows.append(dict(
                source="投诉", district=_s(r.get(c_dist)), staff=_s(r.get(c_staff)),
                staff_id=_s(r.get(c_sid)), order_no=_s(r.get(c_ord)), phone=_s(r.get(c_phone)),
                send_time=_t(r.get(c_send)), reply_time=_t(r.get(c_reply))))

    # ---- 10) 明细④：广义重复投诉工单（广义分子）----
    dup_rows = []
    sh = _find_sheet(xls2, ["广义分子"])
    if sh:
        df = pd.read_excel(io.BytesIO(comp_raw), sheet_name=sh, header=0, engine="openpyxl")
        for _, r in df.iterrows():
            d = _s(r.get("归属区县")).replace("监利县", "监利市")
            dup_rows.append(dict(
                district=d, order_no=_s(r.get("工单编号")), account=_s(r.get("宽带账号")),
                dup_cnt=_s(r.get("重复投诉次数")), reason=_s(r.get("广义投诉七级分类")) or _s(r.get("广义投诉原因一级")),
                first_time=_t(r.get("第一次投诉时间")), first_reason=_s(r.get("第一次投诉时间原因")),
                second_time=_t(r.get("第二次投诉时间")), second_reason=_s(r.get("第二次投诉时间原因")),
                handler=_s(r.get("当前处理人")), grid=_s(r.get("最终回单网格"))))

    # ---- 替换入库 ----
    db.query(ReportDistrict).delete()
    db.query(ReportTimeout).delete()
    db.query(ReportWideOrder).delete()
    db.query(ReportComplaintOrder).delete()
    db.query(ReportH5Record).delete()
    db.query(ReportDupOrder).delete()
    for kind, d, data in recs:
        db.add(ReportDistrict(kind=kind, district=d, data=_json.dumps(data, ensure_ascii=False)))
    if timeouts:
        db.bulk_insert_mappings(ReportTimeout, timeouts)
    if wide_rows:
        db.bulk_insert_mappings(ReportWideOrder, wide_rows)
    if comp_rows:
        db.bulk_insert_mappings(ReportComplaintOrder, comp_rows)
    if h5_rows:
        db.bulk_insert_mappings(ReportH5Record, h5_rows)
    if dup_rows:
        db.bulk_insert_mappings(ReportDupOrder, dup_rows)

    # ---- 联动更新看板数据（install_orders / complaint_orders）----
    # 邮件明细与手动上传共用批次比较机制：只让更新的快照覆盖（旧通报不会回退手动上传的新宽表）
    inst_sync = sync_wide_batch(db, InstallOrder, "order_id",
                                [r for r in inst_rows if r["month"]]) if inst_rows else {"replaced": [], "skipped": []}
    comp_sync = sync_wide_batch(db, ComplaintOrder, "eoms_no",
                                [r for r in comp_order_rows if r["month"]]) if comp_order_rows else {"replaced": [], "skipped": []}

    mm = re.search(r"(\d{8})", info["subject"])
    data_date = f"{mm.group(1)[:4]}-{mm.group(1)[4:6]}-{mm.group(1)[6:]}" if mm else ""
    meta = db.get(ReportMeta, 1) or ReportMeta(id=1)
    meta.mail_subject = info["subject"]
    meta.data_date = data_date
    meta.mail_time = _mail_time_cn(info["mail_date"], info["subject"])
    meta.fetched_at = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
    db.add(meta)
    db.commit()
    return {"ok": True, "subject": info["subject"], "data_date": data_date,
            "mail_time": meta.mail_time, "districts": len({d for _, d, _ in recs}),
            "timeouts": len(timeouts), "wide": len(wide_rows), "complaints": len(comp_rows),
            "h5": len(h5_rows), "dups": len(dup_rows),
            "board_sync": {"install_replaced": inst_sync["replaced"], "install_skipped": inst_sync["skipped"],
                           "complaint_replaced": comp_sync["replaced"], "complaint_skipped": comp_sync["skipped"]}}


@router.post("/fetch-report")
def fetch_report(user=Depends(require_admin), db: Session = Depends(get_db)):
    """管理员：拉取最新【重要通报】邮件"""
    try:
        return sync_report(db)
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _json_clean(o):
    """递归清洗 NaN/Inf（旧快照数据兼容），保证可 JSON 序列化"""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _json_clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_json_clean(v) for v in o]
    return o


@router.get("/report-summary")
def report_summary(user=Depends(get_current_user), db: Session = Depends(get_db)):
    """通报汇总：meta + 5类区县指标 + 超时盯控区县统计"""
    import json as _json
    from collections import defaultdict
    meta = db.get(ReportMeta, 1)
    if not meta or not meta.mail_subject:
        return {"has_data": False}
    kinds = {}
    for kind in ("install", "h5_install", "complaint", "h5_complaint", "dup"):
        rows = db.query(ReportDistrict).filter(ReportDistrict.kind == kind).all()
        kinds[kind] = {r.district: _json_clean(_json.loads(r.data)) for r in rows}
    t_cnt = defaultdict(int)
    for r in db.query(ReportTimeout.district).all():
        t_cnt[r[0] or "未知"] += 1
    return {"has_data": True, "meta": {"mail_subject": meta.mail_subject, "data_date": meta.data_date,
                                       "mail_time": meta.mail_time, "fetched_at": meta.fetched_at},
            "kinds": kinds, "timeout_by_district": dict(sorted(t_cnt.items(), key=lambda x: -x[1])),
            "timeout_total": sum(t_cnt.values())}


@router.get("/report-timeouts")
def report_timeouts(district: str = Query(""), limit: int = Query(500, le=2000),
                    user=Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(ReportTimeout)
    if district:
        q = q.filter(ReportTimeout.district == district)
    rows = q.order_by(ReportTimeout.id.desc()).limit(limit).all()
    keys = ("district", "order_no", "theme", "accept_time", "appoint_end", "step",
            "handler", "phone", "ctrl_type")
    return {"rows": [{k: getattr(r, k) for k in keys} for r in rows]}


@router.get("/h5-survey")
def h5_survey(user=Depends(get_current_user), db: Session = Depends(get_db)):
    """H5满意度通报（集团大音平台）：区县汇总 + 人员测评汇总 + 低分核实 + 快照信息"""
    from ..models import H5SurveyDistrict, H5SurveyRecord, H5LowScore
    from ..models import Setting
    dists = db.query(H5SurveyDistrict).order_by(H5SurveyDistrict.id).all()
    recs = db.query(H5SurveyRecord).all()
    lows = db.query(H5LowScore).order_by(H5LowScore.id.desc()).all()
    # 人员测评汇总：姓名 → {装机测评/投诉测评/低分}；"开发区李平"等短前缀归一，与 h5staff 口径一致
    staff: dict = {}
    for r in recs:
        name = r.staff or "（未识别）"
        for p in ("开发区", "第三方"):
            if name.startswith(p):
                name = name[len(p):]
                break
        s = staff.setdefault(name, {"staff": name, "district": r.district,
                                    "install": 0, "complaint": 0, "low": 0})
        if r.kind == "装机":
            s["install"] += 1
        else:
            s["complaint"] += 1
        if (r.is_low or "").lower() in ("1", "是", "true", "yes"):
            s["low"] += 1
    staff_rows = sorted(staff.values(), key=lambda x: -(x["install"] + x["complaint"]))
    dd = db.query(Setting).filter(Setting.key == "h5_survey_date").first()
    job = (db.query(SyncJob).filter(SyncJob.metric == "h5_survey", SyncJob.status == "confirmed")
           .order_by(SyncJob.confirmed_at.desc()).first())
    return {
        "has_data": bool(dists or recs),
        "data_date": dd.value if dd else "",
        "uploaded_at": job.confirmed_at.strftime("%Y-%m-%d %H:%M:%S") if job and job.confirmed_at else "",
        "filename": job.filename if job else "",
        "districts": [{k: getattr(r, k) for k in
                       ("kind", "district", "back_cnt", "survey_cnt", "satisfaction", "q_timely",
                        "q_service", "q_effect", "fttr_sat", "join_rate", "affect_cnt", "affect_ratio")}
                      for r in dists],
        "staff": staff_rows,
        "lows": [{k: getattr(r, k) for k in
                  ("kind", "district", "order_no", "score", "message", "person", "guide", "note", "assess")}
                 for r in lows],
    }


@router.get("/report-drill")
def report_drill(kind: str = Query(...), district: str = Query(""), level: str = Query(""),
                 show_done: bool = Query(False),
                 limit: int = Query(1000, le=2000),
                 user=Depends(get_current_user), db: Session = Depends(get_db)):
    """通报卡片下钻：untimely上门不及时 / timeout超时盯控 / complaint当日修不及时 / h5staff人员参评率 / dup广义重复 / global全球通投诉
    global：默认只列「待施工」工单，show_done=true 时包含已完工"""
    if kind == "untimely":
        q = db.query(ReportWideOrder).filter(ReportWideOrder.visit_timely == "1")
        if district:
            q = q.filter(ReportWideOrder.district == district)
        rows = q.order_by(ReportWideOrder.id.desc()).limit(limit).all()
        keys = ("district", "grid", "order_no", "theme", "broadband_type", "accept_time",
                "appoint_end", "step", "handler", "status", "is_high_quality")
        return {"rows": [{k: getattr(r, k) for k in keys} for r in rows]}

    if kind == "timeout":
        q = db.query(ReportTimeout)
        if district:
            q = q.filter(ReportTimeout.district == district)
        rows = q.order_by(ReportTimeout.id.desc()).limit(limit).all()
        keys = ("district", "order_no", "theme", "accept_time", "appoint_end", "step",
                "handler", "phone", "ctrl_type")
        return {"rows": [{k: getattr(r, k) for k in keys} for r in rows]}

    if kind == "complaint":
        q = db.query(ReportComplaintOrder).filter(ReportComplaintOrder.day_timely == "否")
        if district:
            q = q.filter(ReportComplaintOrder.district == district)
        rows = q.order_by(ReportComplaintOrder.id.desc()).limit(limit).all()
        keys = ("district", "flow_no", "category", "theme", "accept_time", "duration",
                "day_timely", "status", "handler", "phone", "global_pass", "is_high_quality")
        return {"rows": [{k: getattr(r, k) for k in keys} for r in rows]}

    if kind == "h5staff":
        from collections import defaultdict
        def _norm(n):
            n = (n or "").strip()
            for d in JZ_DISTRICTS:
                if n.startswith(d):
                    n = n[len(d):]
                    break
            for p in ("开发区", "第三方"):   # 受理侧短前缀（开发区涂长静/第三方罗慰）
                if n.startswith(p):
                    n = n[len(p):]
            return n
        orders = defaultdict(int)
        dist_of = {}
        q = db.query(ReportComplaintOrder.handler, ReportComplaintOrder.district)
        for h, d in q.all():
            k = _norm(h)
            if not k:
                continue
            orders[k] += 1
            dist_of.setdefault(k, d or "")
        joined = defaultdict(int)
        q = db.query(ReportH5Record.staff, ReportH5Record.district)
        for s, d in q.all():
            k = _norm(s)
            if not k:
                continue
            joined[k] += 1
            dist_of.setdefault(k, d or "")
        rows = [{"staff": k, "district": dist_of.get(k, ""), "orders": orders.get(k, 0),
                 "joined": joined.get(k, 0),
                 "rate": round(joined.get(k, 0) / orders[k] * 100, 1) if orders.get(k) else None}
                for k in orders]
        rows.sort(key=lambda x: (x["rate"] is None, x["rate"] if x["rate"] is not None else 0))
        if district:
            rows = [r for r in rows if r["district"] == district]
        return {"rows": rows[:limit]}

    if kind == "dup":
        q = db.query(ReportDupOrder)
        if district:
            q = q.filter(ReportDupOrder.district == district)
        rows = q.order_by(ReportDupOrder.id.desc()).limit(limit).all()
        keys = ("district", "order_no", "account", "dup_cnt", "reason", "first_time",
                "first_reason", "second_time", "second_reason", "handler", "grid")
        return {"rows": [{k: getattr(r, k) for k in keys} for r in rows]}

    if kind == "global":
        q = db.query(ReportComplaintOrder)
        if level == "白金":
            q = q.filter(ReportComplaintOrder.global_pass.like("%白金%"))
        elif level == "钻石":
            q = q.filter(ReportComplaintOrder.global_pass.like("%钻石%"))
        elif level == "金":
            q = q.filter(ReportComplaintOrder.global_pass.like("%金卡%"),
                         ~ReportComplaintOrder.global_pass.like("%白金%"))
        if not show_done:      # 默认只列待施工；勾选「显示已完工」后放开状态过滤
            q = q.filter(ReportComplaintOrder.status.like("%待施工%"))
        if district:
            q = q.filter(ReportComplaintOrder.district == district)
        rows = q.order_by(ReportComplaintOrder.id.desc()).limit(limit).all()
        keys = ("district", "flow_no", "global_pass", "category", "theme", "accept_time",
                "status", "handler", "phone", "duration", "is_high_quality")
        return {"rows": [{k: getattr(r, k) for k in keys} for r in rows]}

    return {"rows": []}


@router.post("/fetch")
def fetch_alerts(user=Depends(require_admin), db: Session = Depends(get_db)):
    """管理员：立即拉取最新预警邮件并入库"""
    try:
        return {"ok": True, **sync_alerts(db)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/summary")
def alert_summary(district: str = Query(""), user=Depends(get_current_user), db: Session = Depends(get_db)):
    """预警快照汇总：meta + 分类型计数/分布（支持区县筛选）+ 全球通投诉计数（最新通报受理口径）"""
    meta = db.get(AlertMeta, 1)
    if not meta or meta.mail_subject == "":
        return {"has_data": False}

    def _flt(q):
        if district:
            q = q.filter(AlertOrder.district == district)
        return q.all()
    installs = _flt(db.query(AlertOrder).filter(AlertOrder.type == "install"))
    complaints = _flt(db.query(AlertOrder).filter(AlertOrder.type == "complaint"))
    books = _flt(db.query(AlertOrder).filter(AlertOrder.type == "book"))
    monitors = _flt(db.query(AlertOrder).filter(AlertOrder.type == "monitor"))

    from collections import Counter, defaultdict
    st = Counter(r.status or "未知" for r in installs)
    i_status = [{"status": k, "count": v} for k, v in st.most_common()]
    c_st = Counter(r.status or "未知" for r in complaints)
    c_status = [{"status": k, "count": v} for k, v in c_st.most_common()]
    bk = Counter(r.book_status or "未知" for r in books)
    b_status = [{"status": k, "count": v} for k, v in bk.most_common()]

    def by_district(rs):
        d = defaultdict(int)
        for r in rs:
            d[r.district or "未知"] += 1
        return [{"district": k, "count": v} for k, v in sorted(d.items(), key=lambda x: -x[1])]

    warn = [{"handler": r.handler, "district": r.district, "grid": r.grid,
             "level": r.warn_level, "count": r.order_count, "warn_time": r.warn_time}
            for r in monitors if "告警" in (r.warn_level or "")]

    # 全球通客户投诉计数：来自最新【重要通报】受理明细（预警附件无全球通列）
    # 口径：仅「待施工」工单；已完工工单通过下钻明细的「显示已完工」勾选查看
    cq = db.query(ReportComplaintOrder)
    if district:
        cq = cq.filter(ReportComplaintOrder.district == district)
    gps = [(r.global_pass or "", r.status or "") for r in cq.all()]
    global_cnt = {
        "platinum": sum(1 for g, s in gps if "白金" in g and "待施工" in s),
        "diamond": sum(1 for g, s in gps if "钻石" in g and "待施工" in s),
        "gold": sum(1 for g, s in gps if "金卡" in g and "白金" not in g and "待施工" in s),
        "total": len(gps),
    }
    return {
        "has_data": True,
        "meta": {"mail_subject": meta.mail_subject, "mail_time": meta.mail_time,
                 "export_time": meta.export_time, "fetched_at": meta.fetched_at,
                 "install_total": meta.install_total, "complaint_total": meta.complaint_total,
                 "book_abnormal": meta.book_abnormal, "warn_yellow": meta.warn_yellow,
                 "warn_red": meta.warn_red},
        "counts": {"install": len(installs), "complaint": len(complaints),
                   "book": len(books), "warn_yellow": len([r for r in monitors if "黄" in (r.warn_level or "")]),
                   "warn_red": len([r for r in monitors if "红" in (r.warn_level or "")])},
        "install_status": i_status, "complaint_status": c_status, "book_status": b_status,
        "by_district": {"install": by_district(installs), "complaint": by_district(complaints),
                        "book": by_district(books)},
        "warn_persons": warn,
        "global_cnt": global_cnt,
    }


@router.get("/orders")
def alert_orders(type: str = Query("install", pattern="^(install|complaint|book|monitor)$"),
                 district: str = Query(""),
                 limit: int = Query(2000, le=5000),
                 user=Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(AlertOrder).filter(AlertOrder.type == type)
    if district:
        q = q.filter(AlertOrder.district == district)
    rows = q.order_by(AlertOrder.id.desc()).limit(limit).all()
    keys = ("district", "grid", "handler", "order_no", "service_no", "order_type", "status",
            "community", "is_fttr", "is_high_quality", "accept_time", "appoint_time",
            "visit_time", "punch_time", "pkg_name", "book_status", "supervise_info",
            "supervise_person", "warn_level", "warn_time", "order_count")
    return {"rows": [{k: getattr(r, k) for k in keys} for r in rows]}
