"""数据同步（手册 7.11 / 10.5 三条铁律）：
1. 先预览后入库（upload 只解析，confirm 才落库）
2. 时间一律推导，禁止硬编码
3. 入库后自动重算（本地版为实时计算，无需重算）
"""
import json
import math
import re
import uuid
from collections import Counter
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (SyncJob, WorkOrder, ExtensionOrder, ExtensionMember, District, AuditLog,
                      Setting, H5Complaint, CompletionRecord, InstallOrder, ComplaintOrder,
                      AiVisitIssue, AiVisitStat)
from ..deps import require_admin
from ..utils_excel import parse_upload, parse_upload_sheets, parse_upload_raw

router = APIRouter(prefix="/sync", tags=["sync"])

SYNC_MODULES = {
    "completion_rate": {"name": "竣工率指标",
                        "target": "completion_records（通报分析，按月替换）",
                        "columns": ["激励明细：装维人员/宽带收单/宽带回单/FTTR收单/FTTR回单/激励后金额/区县"],
                        "note": "白名单解析「激励明细」sheet，人员粒度按月替换；上传时通过 month 参数指定通报月份；"
                                "竣工率页「工单口径」卡片在当月无系统工单时自动按装机宽表统计"},
    "extension": {"name": "延伸服务指标", "target": "extension_orders + extension_members",
                  "columns": ["当月明细：订单号/县域/受理人/受理时间/服务项目名称/服务项目收入",
                              "人员明细：区县/网格/姓名/职位/状态"],
                  "note": "白名单解析「当月明细」+「人员明细」两个 sheet；month 从受理时间推导；通报为当月累计快照，订单按月替换、人员全量替换"},
    "h5_complaint": {"name": "H5投诉工单", "target": "h5_complaint",
                     "columns": ["工单编号(HB-058前缀)", "工单状态", "施工人", "受理时间"],
                     "note": "投诉 H5 工单列表"},
    "install_wide": {"name": "装机宽表", "target": "install_orders",
                     "columns": ["数据1：定单编码/区县/网格/受理时间/业务主题/定单状态/宽带类型/是否FTTR/回单人员/回单时间/24小时及时/8小时及时/是否履约/是否退单/一级退单原因/装机时长/完工时长/H5装机满意度",
                                 "盯控列（旧版导出无这些列时取空）：首联时间/首联是否及时/上门时间/是否及时上门/首次激活时间/回访时间/回访结果/IVR回访(装通/满意/评分/最终)/催单次数/二级退单原因/退单人员/AI回访结果/综调回访结果/地市回访结果"],
                     "note": "白名单解析「数据1」sheet（装机宽表月累计）；month 从受理时间推导；按月替换；供竣工率盯控卡片使用"},
    "complaint_wide": {"name": "投诉宽表", "target": "complaint_orders",
                       "columns": ["数据1：工单流水号/区县/投诉时间/智慧装维工单状态/当前处理人/投诉分类/24小时是否及时/8小时是否及时/是否履约/工单时长/故障是否解决/归档满意度"],
                       "note": "白名单解析「数据1」sheet（投诉宽表月累计）；month 从投诉时间推导；按月替换"},
    "install_query": {"name": "装机综合查询", "target": "install_orders",
                      "columns": ["导出信息：定单编码/区域/受理时间/定单主题/工单状态/施工人/施工时间/网格名称/是否FTTR/一级退单原因"],
                      "note": "实时数据（比宽表早约2小时）；白名单解析「导出信息」sheet；month 从受理时间推导；按月替换"},
    "complaint_query": {"name": "投诉综合查询", "target": "complaint_orders",
                        "columns": ["家宽投诉综合工单列表：EOMS工单号/区域/工单状态/当前处理人/施工部门/人/受理时间/投诉时间/投诉分类"],
                        "note": "实时数据（比宽表早约2小时）；白名单解析「家宽投诉综合工单列表」sheet；month 从投诉时间推导；按月替换"},
    "h5_survey": {"name": "H5满意度通报", "target": "h5_survey_districts + h5_survey_staff + h5_survey_records + h5_low_scores",
                  "columns": ["新版导出：装机/投诉参与率（人员）+ 装机/投诉回单 + 装机/投诉H5回复详单",
                              "混合导出（无参与率sheet）：装机/投诉回单 + 装机/投诉H5回复详单 + H5数据统计 + 累计低分核实",
                              "旧版导出：H5数据统计（区县汇总）+ 装机/投诉H5回复详单 + 累计装机/投诉低分核实"],
                  "note": "集团大音平台装维H5满意度通报；「H5参与率通报」图1 人员明细需上传含人员粒度数据的导出："
                      "「装维H5满意度参与率」导出（含「装机参与率」「投诉参与率」sheet），或含「装机回单」「投诉回单」"
                      "sheet 的导出（系统按「区县×人员」聚合回单量与测评量成明细，与平台「H5数据统计」统计块逐格吻合）；"
                      "旧版「装维H5满意度（集团大音平台)」导出仅含「H5数据统计」区县汇总（无人员明细、仅当月），"
                      "上传时预览会给出告警；区县汇总（当日/当月）与人员参与率明细均由本地聚合"
                      "（满意度=0.4×Q1+0.2×Q2+0.4×Q3），驱动「H5参与率通报」页面与首页「重要通报 → H5满意度」卡片；"
                      "四表全量替换，保留最新快照"},
    "ai_visit": {"name": "AI装维即访", "target": "ai_visit_issues（不规范明细） + ai_visit_stats（规范率通报）",
                 "columns": ["装后即访-通报表 / 投诉即访-通报表：区县/外呼量/接通量/有问题工单量/规范率 + 各勾选项(调研量/不是/占比)",
                             "当月装机即访清单 / 当月投诉即访清单：责任人/不规范项/工单号/回单时间"],
                 "note": "通报表→区县×类型×周期(当日/当月)规范率；清单→不规范明细（仅「外呼成功」且勾选项答案为「不是」，工单×不规范项 一行）；"
                         "日期从文件名推导，month 取其年月；均按月替换；展示在首页「AI装维即访-不规范」卡片与「AI装维即访」页面"},
}


def _to_num(v, as_int=True):
    """通报表数值容错转换（'8'/'8.0'/8 都接受；NaN/空 → 0）"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0 if as_int else 0.0
    if f != f:  # NaN
        return 0 if as_int else 0.0
    return int(round(f)) if as_int else round(f, 2)


def _batch_key(rows: list[dict], time_field: str = "accept_time") -> tuple:
    """批次新旧比较键：(最新受理时间, 行数)。快照数据越新 → 受理时间越晚、行数越多"""
    times = [str(r.get(time_field) or "") for r in rows if r.get(time_field)]
    return (max(times) if times else "", len(rows))


def _rep_count(s: str) -> int:
    """从 sync_wide_batch 的 replaced 描述串中取行数；格式异常时容错为 0，避免确认入库再 500"""
    try:
        return int(s.split("(")[1].split("行")[0])
    except (IndexError, ValueError, AttributeError):
        return 0


def sync_wide_batch(db: Session, model, key_field: str, rows: list[dict]) -> dict:
    """宽表批次比较入库（邮件同步与手动上传共用，保证 Web 展示始终为最新快照）：
    对新数据涉及的每个月，与库内同月数据比较 (最新受理时间, 行数)——
    新批次 >= 库内 → 整月替换；新批次 < 库内 → 跳过该月（保留更新的现有数据）。
    rows 需已按 model 字段名映射好，且 month 从受理时间推导（铁律 2）

    ⚠ 缩水保护（2026-10-26 新增）：装机宽表/装机查询/投诉宽表/投诉查询共用本函数
    且都写 install_orders / complaint_orders，后传的批次会整月覆盖前一批次。
    曾发生「19823 行全荆州宽表」被「2048 行仅洪湖的综合查询」覆盖，
    导致区县排名只剩 1 个区县、24h超时归零。故当新批次行数不足库内 60%
    （或区县数明显减少）时拒绝覆盖，只在 skipped 里说明，由用户确认后手动处理。
    """
    months = sorted({r["month"] for r in rows if r.get("month")})
    replaced, skipped, guarded = [], [], []
    for m in months:
        new_rows = [r for r in rows if r["month"] == m]
        # 同批次同键去重：导出文件常见同一工单多段/重复记录，保留受理时间最新的一条，
        # 否则 bulk_insert 遇重复主键直接 IntegrityError（确认入库 500）
        best: dict = {}
        for r in new_rows:
            k = r.get(key_field) or ""
            if k and (k not in best
                      or str(r.get("accept_time") or "") > str(best[k].get("accept_time") or "")):
                best[k] = r
        new_rows = list(best.values())
        new_key = _batch_key(new_rows)
        cur = db.query(model).filter(model.month == m).all()
        old_key = (max((getattr(r, "accept_time") or "" for r in cur), default=""), len(cur))
        if new_key < old_key:
            skipped.append(f"{m}: 库内较新(受理至{old_key[0][:16]}/{old_key[1]}行)≥上传(受理至{new_key[0][:16]}/{new_key[1]}行)，未覆盖")
            continue
        # 缩水保护：行数或区县覆盖度明显下降时拒绝覆盖
        old_n, new_n = len(cur), len(new_rows)
        if old_n >= 100 and new_n < old_n * 0.6:
            old_d = {getattr(r, "district", "") for r in cur if getattr(r, "district", "")}
            new_d = {r.get("district") for r in new_rows if r.get("district")}
            msg = (f"{m}: 上传仅 {new_n} 行/{len(new_d)} 个区县，"
                   f"少于库内 {old_n} 行/{len(old_d)} 个区县的 60%，已拒绝覆盖"
                   f"（疑似综合查询小批次覆盖宽表；如确需缩减请联系管理员）")
            if len(old_d) > len(new_d) and old_d - new_d:
                msg += f"；将丢失区县：{'、'.join(sorted(old_d - new_d))}"
            guarded.append(msg)
            continue
        db.query(model).filter(model.month == m).delete(synchronize_session=False)
        # 分批插入：避免大文件触发 SQLite 变量数上限（too many SQL variables）
        CHUNK = 500
        for i in range(0, len(new_rows), CHUNK):
            db.bulk_insert_mappings(model, new_rows[i:i + CHUNK])
        replaced.append(f"{m}({len(new_rows)}行,受理至{new_key[0][:16]})")
    return {"replaced": replaced, "skipped": skipped, "guarded": guarded}


def _normalize_completion(sheets: dict, month: str) -> list[dict]:
    """白名单「激励明细」→ 人员粒度竣工率记录（区县名带前缀自动剥离，如'公安县陈登明'→陈登明）"""
    out: list[dict] = []
    for r in sheets.get("激励明细") or []:
        name = str(r.get("装维人员") or "").strip()
        if not name:
            continue
        district = str(r.get("区县") or "").strip()
        if district and name.startswith(district):
            name = name[len(district):]
        else:
            m = re.match(r"^[\u4e00-\u9fa5]{2,4}(?:营业区|省|市|县|区)(.{2,6})$", name)
            if m:
                name = m.group(1)
        out.append({"month": month, "handler": name, "district": district,
                    "bb_orders": _to_num(r.get("宽带收单")), "bb_done": _to_num(r.get("宽带回单")),
                    "fttr_orders": _to_num(r.get("FTTR收单")), "fttr_done": _to_num(r.get("FTTR回单")),
                    "incentive": _to_num(r.get("激励后金额"), as_int=False)})
    return out


def _norm_time(v) -> str:
    """统一时间为 YYYY-MM-DD HH:MM:SS（支持 datetime / YYYY-MM-DD / YYYY/M/D / YYYYMMDD）"""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    s = str(v or "").strip()
    if not s:
        return ""
    s = s.replace("/", "-").replace(".", "-")
    if len(s) == 8 and s.isdigit():                       # 20261001 → 2026-10-01
        s = f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


def _normalize_extension(sheets: dict) -> list[dict]:
    """手册 10.2：白名单 sheet 归一化——「当月明细」→ 延伸订单、「人员明细」→ 延伸人员"""
    out: list[dict] = []
    for r in sheets.get("当月明细") or []:
        accept = _norm_time(r.get("受理时间"))
        try:
            income = round(float(r.get("服务项目收入") or 0), 2)
        except (TypeError, ValueError):
            income = 0.0
        out.append({"_kind": "order", "order_no": str(r.get("订单号") or "").strip(),
                    "district": str(r.get("县域") or "").strip(),
                    "handler": str(r.get("受理人") or "").strip(),
                    "accept_time": accept,
                    "service_item": str(r.get("服务项目名称") or "").strip(),
                    "income": income,
                    "month": accept[:7] if len(accept) >= 7 else ""})   # 铁律 2：month 推导
    for r in sheets.get("人员明细") or []:
        status = str(r.get("状态") or "").strip()
        if status and status != "有效":
            continue
        district = str(r.get("区县") or "").strip()
        name = str(r.get("姓名") or "").strip()
        if district and name.startswith(district):        # 姓名可能带区县前缀，如"监利市张三"
            name = name[len(district):]
        else:
            m = re.match(r"^[\u4e00-\u9fa5]{2,4}(?:营业区|省|市|县|区)(.{2,4})$", name)
            if m:
                name = m.group(1)
        out.append({"_kind": "member", "district": district,
                    "grid": str(r.get("网格") or "").strip(),
                    "name": name, "position": str(r.get("职位") or "").strip()})
    return out


def _strip_district_prefix(name: str, district: str) -> str:
    """姓名带区县前缀时剥离（如'洪湖市廖义(柏枝网格)'→廖义）"""
    if name is None or (isinstance(name, float) and name != name):  # None/NaN
        return ""
    name = str(name).strip()
    district = str(district or "").strip()
    if not name or name.lower() == "nan":
        return ""
    if district and name.startswith(district):
        name = name[len(district):]
    # 去掉括号内的网格备注，如 "廖义(柏枝网格)" → "廖义"
    m = re.match(r"^([^\(（]+)", name)
    if m:
        name = m.group(1)
    return name.strip()


# ---------------- H5满意度通报（集团大音平台） ----------------

def _hs(v):
    if v is None or (isinstance(v, float) and v != v):
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("nan", "none") else s


def _hf(v, r=1):
    """满意度/占比：0.9839→98.4；已是百分数原样；NaN→0.0"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    if f != f:
        return 0.0
    return round(f * 100, 1) if 0 <= f <= 1.5 else round(f, r)


def _hi(v):
    try:
        f = float(v)
        return 0 if f != f else int(round(f))
    except (TypeError, ValueError):
        return 0


def _ht(v):
    s = _hs(v)
    return s.replace("/", "-")[:19] if s else ""


def _hq(r: dict, prefix: str):
    """Q 得分列按前缀取第一个非空值（同前缀多个场景变体列，仅一个有值）"""
    for c, v in r.items():
        if str(c).startswith(prefix) and _hs(v):
            return _hs(v)
    return ""


def _hq_score(r: dict, n: int) -> str:
    """第 n 题得分：优先取表头以 'Qn.' 开头且含「打几分」的列（过滤不满意原因/建议列），
    旧版导出措辞不同时兜底按前缀取（_hq）"""
    for c, v in r.items():
        cs = str(c)
        if cs.startswith(f"Q{n}.") and "打几分" in cs and _hs(v):
            return _hs(v)
    return _hq(r, f"Q{n}.")


def _hflag(r: dict, prefix: str) -> bool:
    """不满意/有问题标志：新版导出同一问题有「原因文本列」与「0/1 标志列」（重名双列，
    标志列自动加 .1 后缀）；任一列值为 '1' 或原因为具体文本（非 '0'）即计一次"""
    for c, v in r.items():
        if not str(c).startswith(prefix):
            continue
        s = _hs(v)
        if s == "1" or (s and s != "0"):
            return True
    return False


def _hcol(r: dict, *names):
    """按优先级取第一个存在的列（兼容表头改名，如「区县名称」vs「区县名称(TCountyName)」）"""
    for n in names:
        if n in r:
            return r.get(n)
    return None


def _xround(v: float) -> int:
    """Excel ROUND 口径（四舍五入远离零，如 ROUND(-4.5)=-5）；Python round 为 banker's rounding"""
    return int(math.floor(v + 0.5)) if v >= 0 else int(math.ceil(v - 0.5))


def _person_key(name) -> str:
    """人员匹配键：剥「第三方」/任意区县前缀、括号备注与「-手机号」后缀
    （跨表匹配需归一：投诉回单「最终回单人员」为「区县姓名-手机号」，
    而详单「装维人员姓名」为「区县姓名」，不归一会把同一人拆成两行）"""
    n = _strip_district_prefix(name, "")
    n = re.sub(r"-\d{6,}$", "", n)
    for p in ("第三方", "沙市区", "荆州区", "公安县", "监利市", "江陵县", "石首市", "洪湖市", "松滋市", "荆州开发区"):
        if n.startswith(p) and len(n) > len(p):
            n = n[len(p):]
            break
    return n.strip()


# 区县别名/角色后缀归一（投诉回单无独立区县列，区县藏在「最终回单角色」如「监利县装维人员」里；
# 2026-10-21 混合格式实测全部取值：{区县}{网格长?}装维人员/装维管理员 + 「经开区」简称 + 「监利县」旧称）
_H5_DISTRICT_ALIAS = {"监利县": "监利市", "经开区": "荆州开发区"}
_H5_ROLE_SUFFIX = ("网格长装维人员", "装维管理员", "装维人员", "网格长")


def _h5_norm_district(v) -> str:
    """区县列归一：「监利县装维人员」「沙市区网格长装维人员」「经开区装维人员」→ 裸区县名；
    已是裸区县名（如装机回单「区县」列）原样返回。"""
    s = _hs(v)
    if not s:
        return ""
    for suf in _H5_ROLE_SUFFIX:
        if s.endswith(suf) and len(s) > len(suf):
            s = s[: -len(suf)]
            break
    s = s.strip()
    return _H5_DISTRICT_ALIAS.get(s, s)


def _h5_survey_fmt(sheets: dict) -> str:
    """导出格式识别（三种变体文件名相同，只能按 sheet 构成区分）：
    - new：含「装机/投诉参与率」人员 sheet（2026-10-19 新版）
    - mix：无参与率 sheet，但含「装机/投诉回单」+ 详单（2026-10-21 混合变体，可聚合出人员明细）
    - old：仅「H5数据统计」区县汇总 + 详单 + 低分核实（无人员粒度，图1 必然为空）
    """
    if sheets.get("装机参与率") or sheets.get("投诉参与率"):
        return "new"
    if sheets.get("装机回单") or sheets.get("投诉回单"):
        return "mix"
    return "old"


def _h5_synth_participation(sheets: dict) -> tuple[list[dict], list[dict]]:
    """混合变体（无「装机/投诉参与率」sheet）→ 按「区县×人员」聚合出人员粒度参与率表
    （区县/装维人员/回单量/H5测评量），使 v2 的区县汇总与人员明细逻辑原样复用：
    - 回单量 ← 「装机/投诉回单」行数（当月累计；装机取「区县」列，投诉取「最终回单角色」前缀归一）
    - H5测评量 ← 「装机/投诉H5回复详单」行数（当月累计）
    10-21 实测与平台自带「H5数据统计」统计块合计精确一致：装机当月 7491/2015（洪湖 762/217）、
    当日 410/125；投诉当月 1362/331、当日 64/16（回单/详单均无缺失人员行，合计无损；
    投诉分区县按「最终回单角色」前缀归属，与平台统计存在 ≤3 单的分区县偏差，装机侧逐格吻合）。"""
    back: dict = {}   # (kind, district, key) -> n
    for sheet, kind, dcol, pcol in (("装机回单", "装机", "区县", "回单人员"),
                                    ("投诉回单", "投诉", "最终回单角色", "最终回单人员")):
        for r in sheets.get(sheet) or []:
            d = _h5_norm_district(r.get(dcol))
            if d not in _JZ_SET:
                continue
            k = _person_key(r.get(pcol))
            if not k:
                continue
            back[(kind, d, k)] = back.get((kind, d, k), 0) + 1
    ev: dict = {}     # (kind, district, key) -> n
    for sheet, kind in (("装机H5回复详单", "装机"), ("投诉H5回复详单", "投诉")):
        for r in sheets.get(sheet) or []:
            d = _hs(_hcol(r, "区县名称", "区县名称(TCountyName)"))
            if d not in _JZ_SET:
                continue
            k = _person_key(_hcol(r, "装维人员姓名(ServStaffName)"))
            if not k:
                continue
            ev[(kind, d, k)] = ev.get((kind, d, k), 0) + 1

    def _rows(kind: str) -> list[dict]:
        keys = {k[1:] for k in back if k[0] == kind} | {k[1:] for k in ev if k[0] == kind}
        return [{"区县": d, "装维人员": key,
                 "回单量": back.get((kind, d, key), 0), "H5测评量": ev.get((kind, d, key), 0)}
                for (d, key) in sorted(keys)]

    return _rows("装机"), _rows("投诉")


def _normalize_h5_survey(sheets: dict, data_date: str = "") -> list[dict]:
    """H5满意度通报三格式兼容（带 _kind，供 confirm 分表入库）：
    - 新版导出（含「装机/投诉参与率」人员 sheet）→ v2 本地聚合
    - 混合导出（无参与率 sheet，含「装机/投诉回单」+详单）→ 聚合出参与率表后走 v2，并保留低分核实
    - 旧版导出（含「H5数据统计」区县汇总 sheet）→ v1 原逻辑
    """
    fmt = _h5_survey_fmt(sheets)
    if fmt == "new":
        return _normalize_h5_survey_v2(sheets, data_date)
    if fmt == "mix":
        sheets = dict(sheets)
        sheets["装机参与率"], sheets["投诉参与率"] = _h5_synth_participation(sheets)
        out = _normalize_h5_survey_v2(sheets, data_date)
        out.extend(_h5_survey_lows(sheets))   # 混合变体仍带「累计装机/投诉低分核实」sheet
        return out
    return _normalize_h5_survey_v1(sheets)


def _h5_survey_records(sheets: dict) -> list[dict]:
    """测评明细：装机/投诉 H5 回复详单（新旧格式共用；表头改名/得分列兜底已兼容）"""
    out: list[dict] = []
    for kind, sh in (("装机", "装机H5回复详单"), ("投诉", "投诉H5回复详单")):
        for r in sheets.get(sh) or []:
            d = _hs(_hcol(r, "区县名称", "区县名称(TCountyName)"))
            if not d:
                continue
            qcols = [1, 2, 3] if kind == "装机" else [1, 2, 3]

            def _reason(prefix):
                """不满意原因（过滤 0/1 标志列噪音）"""
                v = _hq(r, prefix)
                return "" if v in ("0", "1") else v

            remark = " | ".join(filter(None, [
                _reason("Q1-1."), _reason("Q2-1."), _reason("Q3-1."),
                _reason("Q1-2."), _reason("Q2-2."),
                _hs(r.get("不满意原因（可多选）") or r.get("不满意原因(可多选)")),
                _hs(r.get("建议及意见")), _hs(r.get("用户留言"))]))[:250]
            out.append({"_kind": "record", "kind": kind, "district": d,
                        "staff": _strip_district_prefix(_hcol(r, "装维人员姓名(ServStaffName)"), d),
                        "staff_id": _hs(_hcol(r, "装维人员工号(ServStaffId)")),
                        "order_no": _hs(_hcol(r, "装维工单编码(WBSerialNo)")),
                        "phone": _hs(_hcol(r, "目标手机号码", "目标手机号码(TargetMSISDN)")),
                        "send_time": _ht(_hcol(r, "下发时间", "下发时间(SendingTime)")),
                        "reply_time": _ht(_hcol(r, "回复时间", "回复时间(AnswerTime)")),
                        "is_low": _hs(_hcol(r, "是否低分用户", "是否低分用户(IsLowScore)")),
                        "q1": _hq_score(r, qcols[0]), "q2": _hq_score(r, qcols[1]),
                        "q3": _hq_score(r, qcols[2]) if kind == "装机" else "",
                        "remark": remark})
    return out


def _normalize_h5_survey_v1(sheets: dict) -> list[dict]:
    """旧版导出（含「H5数据统计」区县汇总 sheet）：区县汇总(当月) + 测评明细 + 低分核实"""
    JZ = {"沙市区", "荆州区", "公安县", "监利市", "江陵县", "石首市", "洪湖市", "松滋市", "荆州开发区", "荆州"}
    out: list[dict] = []
    # ---- 1) 区县汇总：装机段+投诉段同表两段；同区县有当日段+累计段两行，dict 覆盖取最后（累计） ----
    # 白名单只保留9区县+荆州市合计行（尾部说明行整行文本落在区县列，一并排除）
    dist: dict = {}
    for r in sheets.get("H5数据统计") or []:
        d = _hs(r.get("区县"))
        if d not in JZ or ("合计" in d) or ("平均" in d):
            continue
        dist[d] = [
            {"_kind": "district", "kind": "装机", "period": "当月", "district": d,
             "back_cnt": _hi(r.get("回单量")), "survey_cnt": _hi(r.get("H5装机测评数")),
             "satisfaction": _hf(r.get("装机满意度")),
             "q_timely": _hf(r.get("Q24上门及时性40%")), "q_service": _hf(r.get("Q25人员服务20%")),
             "q_effect": _hf(r.get("Q26安装效果40%")), "fttr_sat": _hf(r.get("FTTR满意度")) or None,
             "join_rate": _hf(r.get("装机参与率")),
             "affect_cnt": _hi(r.get("装机影响客户感知工单数量")),
             "affect_ratio": _hf(r.get("装机影响客户感知工单占比0.3%")),
             "unsat_timely": 0, "unsat_service": 0, "unsat_effect": 0, "unsat_image": 0, "diff_cnt": 0},
            {"_kind": "district", "kind": "投诉", "period": "当月", "district": d,
             "back_cnt": _hi(r.get("回单量.1")), "survey_cnt": _hi(r.get("H5投诉测评数")),
             "satisfaction": _hf(r.get("投诉满意度")),
             "q_timely": _hf(r.get("Q27上门及时性40%")), "q_service": _hf(r.get("Q28人员服务20%")),
             "q_effect": _hf(r.get("Q29问题解决情况40%")), "fttr_sat": None,
             "join_rate": _hf(r.get("投诉参与率")),
             "affect_cnt": _hi(r.get("投诉影响客户感知工单数量")),
             "affect_ratio": _hf(r.get("投诉影响客户感知工单占比0.3%")),
             "unsat_timely": 0, "unsat_service": 0, "unsat_effect": 0, "unsat_image": 0, "diff_cnt": 0}]
    for d, pair in dist.items():
        out.extend(pair)
    # ---- 2) 测评明细：装机/投诉 H5 回复详单 ----
    out.extend(_h5_survey_records(sheets))
    # ---- 3) 低分核实明细 ----
    out.extend(_h5_survey_lows(sheets))
    return out


def _h5_survey_lows(sheets: dict) -> list[dict]:
    """低分核实明细（旧版/混合格式共用：「累计装机/投诉低分核实」sheet）"""
    out: list[dict] = []
    for kind, sh in (("装机", "累计装机低分核实"), ("投诉", "累计投诉低分核实")):
        for r in sheets.get(sh) or []:
            if not _hs(r.get("工单号")):
                continue
            guide = _hq(r, "10分引导")
            out.append({"_kind": "low", "kind": kind,
                        "district": _hs(r.get("中心")) or _hs(r.get("区县")),
                        "order_no": _hs(r.get("工单号")), "score": _hs(r.get("低分")),
                        "message": _hs(r.get("用户留言")), "person": _hs(r.get("责任人")),
                        "guide": ("是" if "是" in guide else "否" if "否" in guide else guide),
                        "note": _hs(r.get("回访备注")),
                        "assess": _hs(r.get("考核")) or _hs(r.get("考核（元）")) or _hs(r.get("考核(元)"))})
    return out


# 新版导出固定 sheet 名与关键列（2026-10 大音平台改版：取消「H5数据统计」区县汇总 sheet）
_JZ_ORDER = ["沙市区", "荆州区", "公安县", "监利市", "江陵县", "石首市", "洪湖市", "松滋市", "荆州开发区"]
_JZ_SET = set(_JZ_ORDER)


def _normalize_h5_survey_v2(sheets: dict, data_date: str = "") -> list[dict]:
    """新版平台导出的本地聚合（无「H5数据统计」sheet，需自行计算区县汇总与人员明细）：
    ①「装机/投诉参与率」：人员粒度当月累计（回单量/H5测评量/参与率，权威口径）
    ②「装机/投诉回单」：工单粒度（回单时间→当日拆分；回单人员→人员网格归属）
    ③「装机/投诉H5回复详单」：测评明细（Q 得分→满意度 0.4×Q1+0.2×Q2+0.4×Q3；回复时间→当日拆分）
    → 区县汇总（_kind=district：当日/当月 × 装机/投诉）+ 人员明细（_kind=staff）+ 测评记录（_kind=record）
    """
    out: list[dict] = []
    day = (data_date or "")[:10]

    def _d10(v):
        s = _hs(v)
        return s[:10] if len(s) >= 10 else ""

    # data_date 缺省（文件名无日期）时，取详单回复时间/回单时间的最大日期为「当日」
    if not day:
        dates: list[str] = []
        for sh, tcol in (("装机回单", "回单时间"), ("投诉回单", "回单时间"),
                         ("装机H5回复详单", "回复时间(AnswerTime)"), ("投诉H5回复详单", "回复时间(AnswerTime)")):
            for r in sheets.get(sh) or []:
                d = _d10(_hcol(r, tcol, "回复时间"))
                if d:
                    dates.append(d)
        day = max(dates) if dates else ""

    # ---------- 1) 人员参与率（当月累计，权威口径：区县汇总/人员明细同源，保证图1与图2对账） ----------
    persons: dict = {}   # (district, key) -> {"name": 展示名, "i_back": , "i_eval": , "c_back": , "c_eval": }
    back_rate: dict = {}  # (district, kind) -> 当月回单量合计（参与率表口径，按人员归属区县）
    eval_rate: dict = {}  # (district, kind) -> 当月测评量合计
    for kind_sheet, kind in (("装机参与率", "i"), ("投诉参与率", "c")):
        for r in sheets.get(kind_sheet) or []:
            d = _hs(r.get("区县"))
            raw = _hs(r.get("装维人员"))
            if d not in _JZ_SET or not raw:
                continue
            k = (d, _person_key(raw))
            p = persons.setdefault(k, {"name": raw, "i_back": 0, "i_eval": 0, "c_back": 0, "c_eval": 0})
            if kind == "i":
                p["name"] = raw
                p["i_back"] = _hi(r.get("回单量"))
                p["i_eval"] = _hi(r.get("H5测评量"))
                back_rate[(d, "装机")] = back_rate.get((d, "装机"), 0) + _hi(r.get("回单量"))
                eval_rate[(d, "装机")] = eval_rate.get((d, "装机"), 0) + _hi(r.get("H5测评量"))
            else:
                if not p["i_back"] and not p["i_eval"]:
                    p["name"] = raw
                p["c_back"] = _hi(r.get("回单量"))
                p["c_eval"] = _hi(r.get("H5测评量"))
                back_rate[(d, "投诉")] = back_rate.get((d, "投诉"), 0) + _hi(r.get("回单量"))
                eval_rate[(d, "投诉")] = eval_rate.get((d, "投诉"), 0) + _hi(r.get("H5测评量"))

    # ---------- 2) 回单：当日拆分 + 人员网格归属 ----------
    back_cnt: dict = {}          # (district, kind, period) -> n
    person_grid: dict = {}       # (district, key) -> Counter(网格)
    for sheet, kind, dcol, pcol, gcol, tcol, ecol in (
            # 装机：回单人员→网格；投诉：最终回单人员→最终回单网格，另加 当前处理人→最终回单网格
            # （仅投诉人员不出现在最终回单人员，但出现在当前处理人，与旧快照网格口径一致）
            ("装机回单", "装机", "区县", "回单人员", "网格", "回单时间", None),
            ("投诉回单", "投诉", "最终回单角色", "最终回单人员", "最终回单网格", "回单时间", "当前处理人")):
        for r in sheets.get(sheet) or []:
            d = _h5_norm_district(r.get(dcol))
            if d not in _JZ_SET:
                continue
            back_cnt[(d, kind, "当月")] = back_cnt.get((d, kind, "当月"), 0) + 1
            if day and _d10(r.get(tcol)) == day:
                back_cnt[(d, kind, "当日")] = back_cnt.get((d, kind, "当日"), 0) + 1
            g = _hs(r.get(gcol))
            for pcol_i in filter(None, (pcol, ecol)):
                raw = _hs(r.get(pcol_i))
                if raw and g:
                    person_grid.setdefault((d, _person_key(raw)), Counter())[g] += 1

    # ---------- 3) H5 回复详单：Q 得分聚合（区县×周期 / 人员）+ 不满意人数 + 低分 ----------
    # q 聚合结构：[s1,c1,s2,c2,s3,c3]（得分合计/计数，仅计非空）
    def _qacc():
        return [0.0, 0, 0.0, 0, 0.0, 0]

    dist_q: dict = {}            # (district, kind, period) -> q 聚合
    person_q: dict = {}          # (district, key, kind) -> q 聚合
    unsat: dict = {}             # (district, period) -> [t, s, e, i]（仅装机）
    low_cnt: dict = {}           # (district, kind, period) -> n
    eval_cnt: dict = {}          # (district, kind, period) -> n
    for sheet, kind in (("装机H5回复详单", "装机"), ("投诉H5回复详单", "投诉")):
        for r in sheets.get(sheet) or []:
            d = _hs(_hcol(r, "区县名称", "区县名称(TCountyName)"))
            if d not in _JZ_SET:
                continue
            is_day = bool(day) and _d10(_hcol(r, "回复时间", "回复时间(AnswerTime)")) == day
            raw = _hs(_hcol(r, "装维人员姓名(ServStaffName)"))
            key = _person_key(raw)
            qs = [_hq_score(r, 1), _hq_score(r, 2), _hq_score(r, 3)]
            for period in (("当月", "当日") if is_day else ("当月",)):
                eval_cnt[(d, kind, period)] = eval_cnt.get((d, kind, period), 0) + 1
                acc = dist_q.setdefault((d, kind, period), _qacc())
                for i, q in enumerate(qs):
                    f = _hf(q) if q else 0.0
                    if q and 0 < f <= 10:
                        acc[i * 2] += f
                        acc[i * 2 + 1] += 1
            pacc = person_q.setdefault((d, key, kind), _qacc())
            for i, q in enumerate(qs):
                f = _hf(q) if q else 0.0
                if q and 0 < f <= 10:
                    pacc[i * 2] += f
                    pacc[i * 2 + 1] += 1
            if kind == "装机":
                for period in (("当月", "当日") if is_day else ("当月",)):
                    u = unsat.setdefault((d, period), [0, 0, 0, 0])
                    if _hflag(r, "Q1-1."):
                        u[0] += 1
                    if _hflag(r, "Q2-1."):
                        u[1] += 1
                    if _hflag(r, "Q3-1."):
                        u[2] += 1
                    if _hflag(r, "Q4."):
                        u[3] += 1
            if _hs(_hcol(r, "是否低分用户", "是否低分用户(IsLowScore)")).lower() in ("1", "是", "true", "yes"):
                for period in (("当月", "当日") if is_day else ("当月",)):
                    low_cnt[(d, kind, period)] = low_cnt.get((d, kind, period), 0) + 1

    # ---------- 4) 区县汇总行（当日/当月 × 装机/投诉） ----------
    def _sat(acc):
        """满意度 % = 0.4×Q1 + 0.2×Q2 + 0.4×Q3（得分均值×10 转百分数）"""
        if not acc[1] and not acc[3] and not acc[5]:
            return None
        q1 = acc[0] / acc[1] * 10 if acc[1] else 0.0
        q2 = acc[2] / acc[3] * 10 if acc[3] else 0.0
        q3 = acc[4] / acc[5] * 10 if acc[5] else 0.0
        return round(0.4 * q1 + 0.2 * q2 + 0.4 * q3, 4)

    def _qmean(acc, i):
        return round(acc[i * 2] / acc[i * 2 + 1] * 10, 4) if acc[i * 2 + 1] else 0.0

    for d in _JZ_ORDER:
        for kind in ("装机", "投诉"):
            for period in ("当日", "当月"):
                if period == "当月":
                    # 当月：参与率表口径（与人员明细同源，保证图1合计=图2区县行）
                    back = back_rate.get((d, kind), back_cnt.get((d, kind, "当月"), 0))
                    ev = eval_rate.get((d, kind), eval_cnt.get((d, kind, "当月"), 0))
                else:
                    # 当日：回单/详单按时间拆分（参与率表无日粒度）
                    back = back_cnt.get((d, kind, "当日"), 0)
                    ev = eval_cnt.get((d, kind, "当日"), 0)
                acc = dist_q.get((d, kind, period), _qacc())
                u = unsat.get((d, period), [0, 0, 0, 0]) if kind == "装机" else [0, 0, 0, 0]
                low = low_cnt.get((d, kind, period), 0)
                out.append({"_kind": "district", "kind": kind, "period": period, "district": d,
                            "back_cnt": back, "survey_cnt": ev,
                            "satisfaction": _sat(acc),
                            "q_timely": _qmean(acc, 0) if acc[1] else None,
                            "q_service": _qmean(acc, 1) if acc[3] else None,
                            "q_effect": _qmean(acc, 2) if acc[5] else None,
                            "fttr_sat": None,
                            "join_rate": round(ev / back * 100, 2) if back else 0.0,
                            "affect_cnt": low,
                            "affect_ratio": round(low / back * 100, 2) if back else 0.0,
                            "unsat_timely": u[0], "unsat_service": u[1],
                            "unsat_effect": u[2], "unsat_image": u[3],
                            "diff_cnt": 0})

    # ---------- 5) 人员明细行（当月） ----------
    for (d, key), p in persons.items():
        i_score = _sat(person_q.get((d, key, "装机")) or _qacc())
        c_score = _sat(person_q.get((d, key, "投诉")) or _qacc())
        grids = person_grid.get((d, key)) or Counter()
        grid = grids.most_common(1)[0][0] if grids else ""
        out.append({"_kind": "staff", "district": d, "grid": grid, "name": p["name"],
                    "i_back": p["i_back"], "i_eval": p["i_eval"],
                    "i_short": _xround(p["i_eval"] - 0.5 * p["i_back"]),
                    "i_score": i_score,
                    "c_back": p["c_back"], "c_eval": p["c_eval"],
                    "c_short": _xround(p["c_eval"] - 0.5 * p["c_back"]),
                    "c_score": c_score,
                    "no_install": 1 if p["i_back"] == 0 else 0})

    # ---------- 6) 测评明细记录 ----------
    out.extend(_h5_survey_records(sheets))
    return out


# ---------------- AI装维即访（不规范明细 + 规范率通报） ----------------

_AI_ITEM_MAP = {
    "是否规范穿着": "规范穿着",
    "是否使用指导": "使用指导",
    "是否穿鞋套": "穿鞋套",
    "是否不同位置": "不同位置",
    "是否确认交付方案": "交付方案",
    "是否展示测速结果": "测速结果",
    "是否确认签字": "确认单签字",
}
_AI_INSTALL_ITEMS = ["是否规范穿着", "是否使用指导", "是否穿鞋套", "是否不同位置",
                     "是否确认交付方案", "是否展示测速结果", "是否确认签字"]
_AI_COMPLAINT_ITEMS = ["是否穿鞋套", "是否不同位置", "是否确认交付方案", "是否展示测速结果", "是否确认签字"]

# 通报表固定列位：0区县 1外呼量 2接通量 3有问题工单量 4问题工单占比 5规范率；指标组自第6列起每3列一组(调研量/不是/占比)
_AI_DISTRICTS = {"沙市区", "荆州区", "公安县", "监利市", "江陵县", "石首市", "洪湖市", "松滋市", "荆州开发区"}


def _ai_person(v, district) -> str:
    """责任人清洗：去区县/开发区/第三方前缀、去手机号与工号后缀、去括号网格备注
    （如'洪湖市张豪-13554584854'→'张豪'，'开发区-邓超群-18372380686'→'邓超群'，'开发区李平'→'李平'）"""
    s = _hs(v)
    if not s:
        return ""
    district = str(district or "").strip()
    cands = []
    if district and s.startswith(district):
        cands.append(district)
    if district.endswith("开发区") and s.startswith("开发区"):
        cands.append("开发区")
    if s.startswith("第三方"):
        cands.append("第三方")
    if cands:
        s = s[len(max(cands, key=len)):]
    s = s.lstrip("-－—· 　")
    s = re.split(r"[-－—(（]", s)[0].strip()   # 去手机号/工号后缀、去括号网格备注
    return re.sub(r"[\s\d]+$", "", s).strip()   # 去结尾数字（如 赵亮0701 → 赵亮）


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and not (isinstance(v, float) and v != v)


def _pct(v) -> float:
    """占比/规范率：0.9967→99.67（报表存小数）；已是百分数原样；#DIV/0! 等非法→0.0"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    if f != f:
        return 0.0
    return round(f * 100, 2) if 0 <= f <= 1.5 else round(f, 2)


def _raw_to_dicts(rows: list[tuple]) -> list[dict]:
    """原始矩阵 → 首行为表头的字典行（重复列名加 .N，与 parse_upload_sheets 一致）"""
    if not rows:
        return []
    seen: dict = {}
    header = []
    for h in rows[0]:
        h = str(h).strip() if h is not None else ""
        if h in seen:
            seen[h] += 1
            header.append(f"{h}.{seen[h]}")
        else:
            seen[h] = 0
            header.append(h)
    out = []
    for r in rows[1:]:
        if all(v is None or str(v).strip() == "" for v in r):
            continue
        out.append({header[i]: r[i] for i in range(min(len(header), len(r)))})
    return out


def _normalize_ai_visit(sheets: dict) -> list[dict]:
    """「当月装机即访清单」「当月投诉即访清单」→ 不规范记录
    （仅取 外呼成功 且 勾选项答案为「不是」者；工单×不规范项 一行；month 从回单时间推导）"""
    out: list[dict] = []
    for r in sheets.get("当月装机即访清单") or []:
        if _hs(r.get("外呼结果")) != "外呼成功":
            continue
        district = str(r.get("区县") or "").strip()
        ct = _norm_time(r.get("回单时间"))
        row = {"_kind": "issue", "month": ct[:7] if len(ct) >= 7 else "", "kind": "装机", "district": district,
               "handler": _ai_person(r.get("回单人员"), district),
               "order_no": str(r.get("定单编码") or "").strip(), "completion_time": ct}
        for col in _AI_INSTALL_ITEMS:
            if _hs(r.get(col)) == "不是":
                out.append({**row, "item": _AI_ITEM_MAP[col]})
    for r in sheets.get("当月投诉即访清单") or []:
        if _hs(r.get("外呼结果")) != "外呼成功":
            continue
        district = str(r.get("区县") or "").strip()
        ct = _norm_time(r.get("回单时间"))
        row = {"_kind": "issue", "month": ct[:7] if len(ct) >= 7 else "", "kind": "投诉", "district": district,
               "handler": _ai_person(r.get("最终回单人员"), district),
               "order_no": str(r.get("工单流水号") or "").strip(), "completion_time": ct}
        for col in _AI_COMPLAINT_ITEMS:
            if _hs(r.get(col)) == "不是":
                out.append({**row, "item": _AI_ITEM_MAP[col]})
    return out


def _parse_ai_report(rows: list[tuple], kind: str, month: str, date: str) -> list[dict]:
    """「装后即访-通报表」「投诉即访-通报表」原始矩阵 → 区县 × 周期(当日/当月) 规范率行。

    结构：标题行(含'即访'，'累计'→当月) / 表头行(0区县..5规范率，指标组每3列) /
    子表头行(调研量/不是/占比) / 数据行(9区县 + 荆州合计) / 说明行。
    """
    out: list[dict] = []
    period = ""
    metrics: list[tuple] = []
    for row in rows:
        if not row:
            continue
        c0 = _hs(row[0])
        if "即访" in c0:                          # 标题行：切换周期
            period = "当月" if "累计" in c0 else "当日"
            metrics = []
            continue
        if c0 == "区县":                          # 表头行：重建指标列位
            metrics = []
            j = 6
            while j < len(row):
                nm = _hs(row[j])
                if nm:
                    metrics.append((nm, j))
                j += 3
            continue
        if not period or not metrics or not (c0 in _AI_DISTRICTS or c0 == "荆州"):
            continue
        if len(row) < 6 or not _is_num(row[1]):
            continue
        mt = []
        for nm, j in metrics:
            cover = _hi(row[j]) if j < len(row) else 0
            wrong = _hi(row[j + 1]) if j + 1 < len(row) else 0
            mt.append({"name": nm, "cover": cover, "wrong": wrong,
                       "rate": _pct(row[j + 2]) if j + 2 < len(row) else 0.0})
        out.append({"_kind": "stat", "month": month, "date": date, "period": period, "kind": kind,
                    "district": c0, "outbound": _hi(row[1]), "connected": _hi(row[2]),
                    "problem": _hi(row[3]), "problem_rate": _pct(row[4]), "rate": _pct(row[5]),
                    "metrics": mt})
    return out


def _normalize_ai_visit_stat(raw: dict, month: str, date: str) -> list[dict]:
    """两张通报表 → 规范率统计行（装机 + 投诉）"""
    return (_parse_ai_report(raw.get("装后即访-通报表") or [], "装机", month, date)
            + _parse_ai_report(raw.get("投诉即访-通报表") or [], "投诉", month, date))


def _normalize_install_wide(sheets: dict) -> list[dict]:
    """装机宽表「数据1」→ InstallOrder；month 从受理时间推导（铁律 2）"""
    out: list[dict] = []
    for r in sheets.get("数据1") or []:
        oid = str(r.get("定单编码") or "").strip()
        if not oid:
            continue
        accept = _norm_time(r.get("受理时间"))
        pkg = str(r.get("智能组网套餐编码") or "").strip()
        out.append({
            "order_id": oid,
            "month": accept[:7] if len(accept) >= 7 else "",
            "district": str(r.get("区县") or "").strip(),
            "grid": str(r.get("网格") or r.get("市场网格名称") or "").strip(),
            "accept_time": accept,
            "order_theme": str(r.get("业务主题") or "").strip(),
            "status": str(r.get("定单状态") or "").strip(),
            "broadband_type": str(r.get("宽带类型") or "").strip(),
            "is_fttr": "是" if pkg.startswith("全光组网") else "",
            "handler": _strip_district_prefix(r.get("回单人员") or r.get("当前处理人或回单人"),
                                               r.get("区县")),
            "completion_time": _norm_time(r.get("回单时间")),
            "is_24h": str(r.get("24小时及时") or "").strip(),
            "is_8h": str(r.get("8小时及时") or "").strip(),
            "is_perform": str(r.get("是否履约(新)") or r.get("是否履约") or "").strip(),
            "is_refund": str(r.get("是否退单") or "").strip(),
            "refund_reason": str(r.get("一级退单原因") or "").strip(),
            "install_duration": _to_num(r.get("装机时长"), as_int=False),
            "completion_duration": _to_num(r.get("完工时长"), as_int=False),
            "h5_satisfaction": str(r.get("H5装机满意度") or "").strip(),
            "appointment_time": _norm_time(r.get("预约时间段结束时间") or r.get("首次预约时间")),
            "is_high_quality": str(r.get("高品质标签") or "").strip(),
            "current_step": str(r.get("当前环节") or "").strip(),
            # 竣工率盯控维度（旧版导出无这些列时取空，不影响主链路）
            "first_contact_time": _norm_time(r.get("首联时间")),
            "first_contact_timely": str(r.get("首联是否及时") or "").strip(),
            "visit_time": _norm_time(r.get("上门时间")),
            "visit_timely": str(r.get("是否及时上门") or "").strip(),
            "activate_time": _norm_time(r.get("首次激活时间")),
            "revisit_time": _norm_time(r.get("回访时间")),
            "revisit_result": str(r.get("回访结果") or "").strip(),
            "ivr_pass": str(r.get("IVR人工回访是否装通") or "").strip(),
            "ivr_satisfied": str(r.get("IVR人工回访是否满意") or "").strip(),
            "ivr_score": str(r.get("IVR人工回访满意度评分") or "").strip(),
            "ivr_final": str(r.get("IVR人工回访最终是否满意") or "").strip(),
            "urge_cnt": _to_num(r.get("催单次数")),
            "refund_reason2": str(r.get("二级退单原因") or "").strip(),
            "refund_person": str(r.get("退单人员") or "").strip(),
            "ai_result": str(r.get("AI回访结果") or "").strip(),
            "zongdiao_result": str(r.get("综调回访结果") or "").strip(),
            "city_result": str(r.get("地市回访结果") or "").strip(),
        })
    return out


def _normalize_complaint_wide(sheets: dict) -> list[dict]:
    """投诉宽表「数据1」→ ComplaintOrder；month 从投诉时间推导（铁律 2）"""
    out: list[dict] = []
    for r in sheets.get("数据1") or []:
        no = str(r.get("工单流水号") or r.get("EOMS工单号") or "").strip()
        if not no:
            continue
        accept = _norm_time(r.get("投诉时间") or r.get("受理时间"))
        out.append({
            "eoms_no": no,
            "month": accept[:7] if len(accept) >= 7 else "",
            "district": str(r.get("区县") or "").strip(),
            "accept_time": accept,
            "status": str(r.get("智慧装维工单状态") or r.get("工单状态") or "").strip(),
            "handler": _strip_district_prefix(r.get("当前处理人") or r.get("最终回单人员"), r.get("区县")),
            "complaint_category": str(r.get("投诉分类") or "").strip(),
            "is_24h": str(r.get("24小时是否及时") or "").strip(),
            "is_8h": str(r.get("8小时是否及时") or "").strip(),
            "is_perform": str(r.get("是否履约") or "").strip(),
            "order_duration": _to_num(r.get("工单时长"), as_int=False),
            "is_solved": str(r.get("是否维修完成") or r.get("故障是否解决") or r.get("最终处理结果") or "").strip(),
            "satisfaction": str(r.get("归档满意度") or "").strip(),
            "appointment_time": _norm_time(r.get("预约上门时间止") or r.get("首次预约时间")),
            "grid": str(r.get("首次派单目标网格") or r.get("最终回单网格") or "").strip(),
            "is_high_quality": str(r.get("是否高品质") or "").strip(),
            "global_pass": str(r.get("全球通标识") or "").strip(),
        })
    return out


def _calc_timely(accept: str, done: str, hours: int) -> str:
    """综合查询导出无「24/8小时及时」列，按 受理→施工 时长推算：
    未施工返回 ""（不计入超时统计，避免把在途单误判为超时），
    已施工按 hours 阈值判 是/否。"""
    if not accept or not done:
        return ""
    try:
        fmt = "%Y-%m-%d %H:%M:%S"
        delta = (datetime.strptime(done[:19], fmt) - datetime.strptime(accept[:19], fmt)).total_seconds()
    except (ValueError, TypeError):
        return ""
    return "是" if delta <= hours * 3600 else "否"


def _normalize_install_query(sheets: dict) -> list[dict]:
    """装机综合查询「导出信息」→ InstallOrder（实时数据）"""
    out: list[dict] = []
    for r in sheets.get("导出信息") or []:
        oid = str(r.get("定单编码") or "").strip()
        if not oid:
            continue
        accept = _norm_time(r.get("受理时间"))
        done_time = _norm_time(r.get("施工时间"))
        refund_reason = str(r.get("一级退单原因") or "").strip()
        out.append({
            "order_id": oid, "month": accept[:7] if len(accept) >= 7 else "",
            "district": str(r.get("区域") or "").strip(),
            "grid": str(r.get("网格名称") or "").strip(),
            "accept_time": accept,
            "order_theme": str(r.get("定单主题") or "").strip(),
            "status": str(r.get("工单状态") or "").strip(),
            "broadband_type": "", "is_fttr": str(r.get("是否FTTR") or "").strip(),
            "handler": _strip_district_prefix(r.get("施工人") or r.get("当前处理人"), r.get("区域")),
            "completion_time": done_time,
            # 综合查询无及时性列 → 按受理→施工时长推算
            # （此前硬编码空串，整月替换宽表后「24h超时」恒为 0）
            "is_24h": _calc_timely(accept, done_time, 24),
            "is_8h": _calc_timely(accept, done_time, 8),
            "is_perform": "",
            "is_refund": "是" if refund_reason else "",
            "refund_reason": refund_reason,
            "install_duration": 0.0, "completion_duration": 0.0, "h5_satisfaction": "",
            "appointment_time": _norm_time(r.get("前台预约时间")), "is_high_quality": "",
        })
    return out


def _normalize_complaint_query(sheets: dict) -> list[dict]:
    """投诉综合查询「家宽投诉综合工单列表」→ ComplaintOrder（实时数据）"""
    out: list[dict] = []
    for r in sheets.get("家宽投诉综合工单列表") or []:
        no = str(r.get("EOMS工单号") or "").strip()
        if not no:
            continue
        accept = _norm_time(r.get("投诉时间") or r.get("受理时间"))
        out.append({
            "eoms_no": no, "month": accept[:7] if len(accept) >= 7 else "",
            "district": str(r.get("区域") or "").strip(),
            "accept_time": accept,
            "status": str(r.get("工单状态") or "").strip(),
            "handler": _strip_district_prefix(r.get("施工部门/人") or r.get("当前处理人"), r.get("区域")),
            "complaint_category": str(r.get("投诉分类") or "").strip(),
            "is_24h": "", "is_8h": "", "is_perform": "",
            "order_duration": 0.0, "is_solved": "", "satisfaction": "",
            "appointment_time": _norm_time(r.get("预约时间") or r.get("客户预约时间")),
            "grid": str(r.get("网格") or "").strip(), "is_high_quality": "", "global_pass": "",
        })
    return out


@router.get("/info/{metric}")
def sync_info(metric: str, user=Depends(require_admin)):
    if metric not in SYNC_MODULES:
        raise HTTPException(status_code=404, detail="模块不存在")
    return {"metric": metric, **SYNC_MODULES[metric]}


@router.get("/template/{metric}")
def sync_template(metric: str, user=Depends(require_admin)):
    if metric not in SYNC_MODULES:
        raise HTTPException(status_code=404, detail="模块不存在")
    return {"metric": metric, "name": SYNC_MODULES[metric]["name"],
            "columns": SYNC_MODULES[metric]["columns"]}


@router.post("/upload/{metric}")
async def upload(metric: str, request: Request, user=Depends(require_admin), db: Session = Depends(get_db)):
    """上传解析预览（不入库，铁律 1）"""
    if metric not in SYNC_MODULES:
        raise HTTPException(status_code=404, detail="模块不存在")
    extra: dict = {}
    col_override = None
    if metric == "extension":
        # 手册 10.2：白名单只取「当月明细」「人员明细」，避免全年明细/日明细等 sheet 混入
        filename, sheets = await parse_upload_sheets(request, ["当月明细", "人员明细"])
        rows = _normalize_extension(sheets)
        orders = [r for r in rows if r["_kind"] == "order"]
        members = [r for r in rows if r["_kind"] == "member"]
        if not orders:
            raise HTTPException(status_code=400,
                                detail="「当月明细」sheet 未解析到数据行，请确认上传的是延伸服务数据通报表")
        extra = {"orders": len(orders), "members": len(members),
                 "months": sorted({r["month"] for r in orders if r["month"]})}
        preview = orders[:100]
    elif metric == "completion_rate":
        # 竣工率激励通报：白名单只取「激励明细」；月份由前端明示（通报表内无时间列，铁律 2 禁止硬编码）
        month = request.query_params.get("month") or datetime.now().strftime("%Y-%m")
        if not re.match(r"^\d{4}-\d{2}$", month):
            raise HTTPException(status_code=400, detail="month 格式应为 YYYY-MM")
        filename, sheets = await parse_upload_sheets(request, ["激励明细"])
        rows = _normalize_completion(sheets, month)
        if not rows:
            raise HTTPException(status_code=400,
                                detail="「激励明细」sheet 未解析到数据行，请确认上传的是竣工率激励预通报表")
        extra = {"people": len(rows), "months": [month],
                 "incentive_total": round(sum(r["incentive"] for r in rows), 2)}
        preview = rows[:100]
    elif metric == "install_wide":
        # 装机宽表：白名单「数据1」；month 从受理时间推导
        filename, sheets = await parse_upload_sheets(request, ["数据1"])
        rows = _normalize_install_wide(sheets)
        if not rows:
            raise HTTPException(status_code=400,
                                detail="「数据1」sheet 未解析到数据行，请确认上传的是装机宽表")
        months = sorted({r["month"] for r in rows if r["month"]})
        refund = sum(1 for r in rows if r["is_refund"] == "是")
        done = sum(1 for r in rows if r["status"] == "已施工")
        extra = {"orders": len(rows), "months": months, "done": done, "refund": refund}
        preview = rows[:100]
    elif metric == "complaint_wide":
        # 投诉宽表：白名单「数据1」；month 从投诉时间推导
        filename, sheets = await parse_upload_sheets(request, ["数据1"])
        rows = _normalize_complaint_wide(sheets)
        if not rows:
            raise HTTPException(status_code=400,
                                detail="「数据1」sheet 未解析到数据行，请确认上传的是投诉宽表")
        months = sorted({r["month"] for r in rows if r["month"]})
        solved = sum(1 for r in rows if r["is_solved"] in ("已解决", "已维修"))
        extra = {"orders": len(rows), "months": months, "solved": solved}
        preview = rows[:100]
    elif metric == "install_query":
        filename, sheets = await parse_upload_sheets(request, ["导出信息"])
        rows = _normalize_install_query(sheets)
        if not rows:
            raise HTTPException(status_code=400,
                                detail="「导出信息」sheet 未解析到数据行，请确认上传的是装机综合查询表")
        months = sorted({r["month"] for r in rows if r["month"]})
        done = sum(1 for r in rows if r["status"] == "已施工")
        refund = sum(1 for r in rows if r["is_refund"] == "是")
        extra = {"orders": len(rows), "months": months, "done": done, "refund": refund}
        preview = rows[:100]
    elif metric == "complaint_query":
        filename, sheets = await parse_upload_sheets(request, ["家宽投诉综合工单列表"])
        rows = _normalize_complaint_query(sheets)
        if not rows:
            raise HTTPException(status_code=400,
                                detail="「家宽投诉综合工单列表」sheet 未解析到数据行，请确认上传的是投诉综合查询表")
        months = sorted({r["month"] for r in rows if r["month"]})
        dup = len(rows) - len({r["eoms_no"] for r in rows if r.get("eoms_no")})
        extra = {"orders": len(rows), "months": months}
        if dup:
            extra["dup_warning"] = f"检出 {dup} 个重复工单号，入库时将自动保留每条最新记录"
        preview = rows[:100]
    elif metric == "h5_survey":
        # H5满意度通报（集团大音平台）双格式：
        # 新版导出（2026-10 起）：「装机/投诉参与率」「装机/投诉回单」「装机/投诉H5回复详单」
        # 旧版导出：「H5数据统计」「装机/投诉H5回复详单」「累计装机/投诉低分核实」
        # 表头大都在第 2 行（跳 1 行标题）；新版「装机/投诉回单」表头就在第 1 行（按 sheet 指定跳行）
        filename, sheets = await parse_upload_sheets(
            request,
            ["H5数据统计", "装机H5回复详单", "投诉H5回复详单", "累计装机低分核实", "累计投诉低分核实",
             "装机参与率", "投诉参与率", "装机回单", "投诉回单"],
            skip_rows={"装机回单": 0, "投诉回单": 0})
        mm = re.search(r"(\d{1,2})月(\d{1,2})日", filename or "")
        data_date = f"{datetime.now().year}-{int(mm.group(1)):02d}-{int(mm.group(2)):02d}" if mm else ""
        rows = _normalize_h5_survey(sheets, data_date)
        n_dist = sum(1 for r in rows if r["_kind"] == "district")
        n_rec = sum(1 for r in rows if r["_kind"] == "record")
        n_staff = sum(1 for r in rows if r["_kind"] == "staff")
        n_low = sum(1 for r in rows if r["_kind"] == "low")
        if not n_dist and not n_rec:
            raise HTTPException(status_code=400,
                                detail="未解析到 H5 数据，请确认上传的是装维H5满意度通报表"
                                       "（含「H5数据统计」或「装机/投诉参与率」等 sheet）")
        jz_rate = next((r["join_rate"] for r in rows if r["_kind"] == "district" and r["kind"] == "装机"
                        and r["period"] == "当月" and r["district"] == "洪湖市"), None)
        # 格式识别（三种变体文件名相同，按 sheet 构成区分）：new=含人员粒度「装机/投诉参与率」；
        # mix=无参与率 sheet 但含「装机/投诉回单」+详单（可按人员聚合出图1 明细，2026-10-21 混合变体）；
        # old=仅「H5数据统计」区县汇总（无人员粒度），入库后图1 将为空——上传预览即告警，
        # 避免「确认入库后图1 空白」再次发生（2026-10-22 用户反馈）。
        survey_fmt = _h5_survey_fmt(sheets)
        warn = ""
        if survey_fmt == "old":
            warn = ("当前文件为旧版导出格式（仅「H5数据统计」区县汇总 + 详单 + 低分核实，无「装机/投诉回单」sheet），"
                    "无人员粒度数据：入库后「H5参与率通报」图1 人员明细将为空、图2 仅有当月汇总。"
                    "请改传「装维H5满意度参与率」导出（含「装机参与率」「投诉参与率」sheet），"
                    "或含「装机回单」「投诉回单」sheet 的导出（系统可按人员聚合成明细）后再确认入库。")
        extra = {"districts": n_dist, "records": n_rec, "staff": n_staff, "lows": n_low,
                 "join_rate": f"{jz_rate}%" if jz_rate is not None else "",
                 "data_date": data_date, "format": survey_fmt, "warn": warn}
        preview = rows[:80]
    elif metric == "ai_visit":
        # AI装维即访：白名单「装后即访-通报表」「投诉即访-通报表」（多行表头 → 原始矩阵解析）
        # +「当月装机即访清单」「当月投诉即访清单」（不规范明细）
        filename, raw = await parse_upload_raw(
            request, ["装后即访-通报表", "投诉即访-通报表", "当月装机即访清单", "当月投诉即访清单"])
        det = {"当月装机即访清单": _raw_to_dicts(raw.get("当月装机即访清单") or []),
               "当月投诉即访清单": _raw_to_dicts(raw.get("当月投诉即访清单") or [])}
        scanned = len(det["当月装机即访清单"]) + len(det["当月投诉即访清单"])
        if not scanned:
            raise HTTPException(status_code=400,
                                detail="未解析到「当月装机即访清单」「当月投诉即访清单」，请确认上传的是 AI装维即访回访清单")
        # 数据日期从文件名推导（如「AI装维即访回访清单10.19.xlsx」→ 2026-10-19），缺省取当天
        mm = re.search(r"(\d{1,2})[.\-月](\d{1,2})", filename or "")
        date = (f"{datetime.now().year}-{int(mm.group(1)):02d}-{int(mm.group(2)):02d}"
                if mm else datetime.now().strftime("%Y-%m-%d"))
        month = date[:7]
        issues = _normalize_ai_visit(det)
        stats = _normalize_ai_visit_stat(raw, month, date)
        rows = issues + stats
        months = sorted({r["month"] for r in rows if r["month"]})
        item_cnt = {it: sum(1 for r in issues if r["item"] == it) for it in sorted({r["item"] for r in issues})}
        # 合计行（荆州）规范率概览，供预览核对
        def _total(kind, period):
            for s in stats:
                if s["kind"] == kind and s["period"] == period and s["district"] == "荆州":
                    return {"rate": s["rate"], "problem": s["problem"], "connected": s["connected"]}
            return None
        extra = {"date": date, "months": months, "scanned": scanned, "stat_rows": len(stats),
                 "装机": sum(1 for r in issues if r["kind"] == "装机"),
                 "投诉": sum(1 for r in issues if r["kind"] == "投诉"),
                 "rates": {f"{k}-{p}": _total(k, p) for k in ("装机", "投诉") for p in ("当日", "当月")},
                 "items": item_cnt,
                 "note": "不规范明细仅统计外呼成功且勾选项答案为「不是」的记录；规范率取自通报表（区县×类型×当日/当月）"}
        col_override = ["_kind", "period", "kind", "district", "outbound", "connected", "problem",
                        "problem_rate", "rate", "handler", "item", "order_no", "completion_time"]
        preview = stats[:100] + issues[:200]
    else:
        filename, rows = await parse_upload(request)
        if not rows:
            raise HTTPException(status_code=400, detail="解析不到任何数据行")
        preview = rows[:100]
    batch_id = str(uuid.uuid4())[:8].upper()
    db.add(SyncJob(batch_id=batch_id, metric=metric, filename=filename, rows=len(rows),
                   status="preview", preview_data=json.dumps(rows, ensure_ascii=False)))
    db.commit()
    return {"batch_id": batch_id, "filename": filename, "total_rows": len(rows),
            "columns": col_override or (list(rows[0].keys()) if rows else []),
            "preview": preview, **extra}


@router.post("/confirm/{batch_id}")
def confirm(batch_id: str, user=Depends(require_admin), db: Session = Depends(get_db)):
    """确认入库（铁律 3：入库后重算——本地版指标实时计算）"""
    job = db.query(SyncJob).filter(SyncJob.batch_id == batch_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="批次不存在")
    if job.status == "confirmed":
        raise HTTPException(status_code=400, detail="该批次已入库")
    rows = json.loads(job.preview_data)
    # 超过预览容量的行不完整，仅演示入库预览数据
    districts = {d.name: d.id for d in db.query(District).all()}
    n = 0
    if job.metric == "completion_rate":
        # 通报为当月累计快照：按月替换（同月重复上传直接覆盖）
        # 同名同区县的多行（表内存在重名行）合并相加，(month, handler, district) 唯一
        month = rows[0]["month"] if rows else ""
        if month:
            db.query(CompletionRecord).filter(CompletionRecord.month == month).delete(
                synchronize_session=False)
        merged: dict = {}
        for r in rows:
            k = (r["month"], r["handler"], r["district"])
            if k in merged:
                m = merged[k]
                m["bb_orders"] += r["bb_orders"]; m["bb_done"] += r["bb_done"]
                m["fttr_orders"] += r["fttr_orders"]; m["fttr_done"] += r["fttr_done"]
                m["incentive"] += r["incentive"]
            else:
                merged[k] = dict(r)
        for r in merged.values():
            db.add(CompletionRecord(month=r["month"], handler=r["handler"], district=r["district"],
                                    bb_orders=r["bb_orders"], bb_done=r["bb_done"],
                                    fttr_orders=r["fttr_orders"], fttr_done=r["fttr_done"],
                                    incentive=round(r["incentive"], 2)))
            n += 1
        msg = (f"入库成功：{month} 竣工率通报 {len(rows)} 行合并为 {n} 人（按月替换），指标已实时生效")
    elif job.metric == "extension":
        orders = [r for r in rows if r.get("_kind") == "order" and r.get("month")]
        members = [r for r in rows if r.get("_kind") == "member"]
        months = sorted({r["month"] for r in orders})
        if months:
            # 通报为当月累计快照：按月替换，避免重复累计；month 已从受理时间推导（铁律 2）
            db.query(ExtensionOrder).filter(ExtensionOrder.month.in_(months)).delete(
                synchronize_session=False)
            for r in orders:
                db.add(ExtensionOrder(order_no=r["order_no"], district=r["district"], handler=r["handler"],
                                      accept_time=r["accept_time"], service_item=r["service_item"],
                                      income=r["income"], month=r["month"]))
            n += len(orders)
        if members:
            db.query(ExtensionMember).delete(synchronize_session=False)
            for r in members:
                db.add(ExtensionMember(district=r["district"], grid=r["grid"],
                                       name=r["name"], position=r["position"]))
            n += len(members)
        msg = (f"入库成功：订单 {len(orders)} 行（月份 {'、'.join(months) or '无'}，按月替换）、"
               f"人员 {len(members)} 人（全量替换），指标已实时生效")
    elif job.metric == "h5_complaint":
        for r in rows:
            no = str(r.get("工单编号") or "").strip()
            if not no:
                continue
            if not db.query(H5Complaint).filter(H5Complaint.eoms_order_no == no).first():
                db.add(H5Complaint(eoms_order_no=no, order_status=str(r.get("工单状态") or "已施工"),
                                   construction_dept_person=str(r.get("施工人") or ""),
                                   accept_time=str(r.get("受理时间") or "")))
                n += 1
    elif job.metric in ("install_wide", "install_query"):
        # 装机宽表为当月累计快照：批次比较入库（与邮件明细同机制，旧快照不覆盖新数据）
        rows = [r for r in rows if r.get("order_id") and r.get("month")]
        res = sync_wide_batch(db, InstallOrder, "order_id", rows)
        n = sum(_rep_count(s) for s in res["replaced"])
        msg = (f"入库成功：装机宽表替换 {('、'.join(res['replaced']) or '无月份')}；"
               + (f"；跳过较早快照：{('；'.join(res['skipped']))}" if res["skipped"] else "")
               + (f"；⚠ 已阻止缩水覆盖：{('；'.join(res['guarded']))}" if res.get("guarded") else "")
               + "，指标已实时生效")
    elif job.metric in ("complaint_wide", "complaint_query"):
        rows = [r for r in rows if r.get("eoms_no") and r.get("month")]
        res = sync_wide_batch(db, ComplaintOrder, "eoms_no", rows)
        n = sum(_rep_count(s) for s in res["replaced"])
        msg = (f"入库成功：投诉宽表替换 {('、'.join(res['replaced']) or '无月份')}；"
               + (f"；跳过较早快照：{('；'.join(res['skipped']))}" if res["skipped"] else "")
               + (f"；⚠ 已阻止缩水覆盖：{('；'.join(res['guarded']))}" if res.get("guarded") else "")
               + "，指标已实时生效")
    elif job.metric == "h5_survey":
        # H5满意度通报：四表全量替换（快照），保留最新上传
        from ..models import H5SurveyDistrict, H5SurveyRecord, H5LowScore, H5SurveyStaff
        dist = [r for r in rows if r.get("_kind") == "district"]
        recs = [r for r in rows if r.get("_kind") == "record"]
        lows = [r for r in rows if r.get("_kind") == "low"]
        staff = [r for r in rows if r.get("_kind") == "staff"]
        db.query(H5SurveyDistrict).delete(synchronize_session=False)
        db.query(H5SurveyRecord).delete(synchronize_session=False)
        db.query(H5LowScore).delete(synchronize_session=False)
        db.query(H5SurveyStaff).delete(synchronize_session=False)
        if dist:
            db.bulk_insert_mappings(H5SurveyDistrict, [{k: v for k, v in r.items() if k != "_kind"} for r in dist])
        if recs:
            db.bulk_insert_mappings(H5SurveyRecord, [{k: v for k, v in r.items() if k != "_kind"} for r in recs])
        if lows:
            db.bulk_insert_mappings(H5LowScore, [{k: v for k, v in r.items() if k != "_kind"} for r in lows])
        if staff:
            db.bulk_insert_mappings(H5SurveyStaff, [{k: v for k, v in r.items() if k != "_kind"} for r in staff])
        # 数据日期存 Setting（从文件名如"10月19日装维H5满意度"提取），供前端展示
        mm = re.search(r"(\d{1,2})月(\d{1,2})日", job.filename or "")
        date_val = f"{datetime.now().year}-{int(mm.group(1)):02d}-{int(mm.group(2)):02d}" if mm else ""
        if date_val:
            sv = db.query(Setting).filter(Setting.key == "h5_survey_date").first()
            if sv:
                sv.value = date_val
            else:
                db.add(Setting(key="h5_survey_date", value=date_val))
        n = len(dist) + len(recs) + len(lows) + len(staff)
        msg = (f"入库成功：H5满意度通报（区县汇总{len(dist)} · 人员明细{len(staff)} · "
               f"测评明细{len(recs)} · 低分核实{len(lows)}），已全量替换为最新快照")
    elif job.metric == "ai_visit":
        # AI装维即访：不规范明细 + 规范率通报，均按月替换（同月重复上传直接覆盖）
        issues = [r for r in rows if r.get("_kind") != "stat"]
        stats = [r for r in rows if r.get("_kind") == "stat"]
        months = sorted({r.get("month") or "" for r in rows})
        db.query(AiVisitIssue).filter(AiVisitIssue.month.in_(months)).delete(synchronize_session=False)
        for r in issues:
            db.add(AiVisitIssue(month=r.get("month") or "", kind=r.get("kind") or "",
                                district=r.get("district") or "", handler=r.get("handler") or "",
                                item=r.get("item") or "", order_no=r.get("order_no") or "",
                                completion_time=r.get("completion_time") or ""))
            n += 1
        smonths = sorted({r.get("month") or "" for r in stats})
        if smonths:
            db.query(AiVisitStat).filter(AiVisitStat.month.in_(smonths)).delete(synchronize_session=False)
            for r in stats:
                db.add(AiVisitStat(month=r.get("month") or "", date=r.get("date") or "",
                                   period=r.get("period") or "", kind=r.get("kind") or "",
                                   district=r.get("district") or "",
                                   outbound=int(r.get("outbound") or 0), connected=int(r.get("connected") or 0),
                                   problem=int(r.get("problem") or 0),
                                   problem_rate=float(r.get("problem_rate") or 0.0),
                                   rate=float(r.get("rate") or 0.0),
                                   metrics=json.dumps(r.get("metrics") or [], ensure_ascii=False)))
                n += 1
        cnt = {it: sum(1 for r in issues if r.get("item") == it) for it in sorted({r.get("item") for r in issues})}
        msg = (f"入库成功：AI装维即访 不规范 {len(issues)} 条 + 规范率通报 {len(stats)} 行"
               f"（月份 {'、'.join(m or '未标' for m in months) or '无'}，按月替换）"
               + (f"；不规范项 {'、'.join(f'{k}{v}条' for k, v in cnt.items())}" if cnt else "")
               + "，首页「AI装维即访-不规范」与「AI装维即访」页面已更新")
    job.status = "confirmed"
    job.confirmed_at = datetime.now()
    db.add(AuditLog(username=user.username, action="数据入库", detail=f"{job.metric} {n} 行"))
    db.commit()
    if job.metric in ("extension", "completion_rate", "install_wide", "complaint_wide",
                      "install_query", "complaint_query", "ai_visit", "h5_survey"):
        return {"msg": msg, "rows": n}
    return {"msg": f"入库成功 {n} 行（指标实时计算，无需重算）", "rows": n}


@router.get("/history")
def history(user=Depends(require_admin), db: Session = Depends(get_db)):
    return [{"id": j.id, "batch_id": j.batch_id, "metric": j.metric, "filename": j.filename,
             "rows": j.rows, "status": j.status,
             "created_at": j.created_at.strftime("%Y-%m-%d %H:%M:%S"),
             "confirmed_at": j.confirmed_at.strftime("%Y-%m-%d %H:%M:%S") if j.confirmed_at else ""}
            for j in db.query(SyncJob).order_by(SyncJob.id.desc()).limit(50)]
