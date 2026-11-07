"""数据同步（手册 7.11 / 10.5 三条铁律）：
1. 先预览后入库（upload 只解析，confirm 才落库）
2. 时间一律推导，禁止硬编码
3. 入库后自动重算（本地版为实时计算，无需重算）
"""
import json
import math
import os
import re
import time
import uuid
from collections import Counter
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, text
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ..database import get_db
from ..models import (SyncJob, WorkOrder, ExtensionOrder, ExtensionMember, District, AuditLog,
                      Setting, H5Complaint, CompletionRecord, InstallOrder, ComplaintOrder,
                      AiVisitIssue, AiVisitStat, QcRecord)
from ..deps import require_admin, get_role
from ..services.broad_match import (CID_MAX_BYTES, CID_STAGING, INSERT_STAGING,
                                    account_norm_key, backup_db, enrich_broad_rows,
                                    ensure_cid_tables, norm_account, norm_district,
                                    norm_text, purge_staging, replace_cid_snapshot)
from ..utils_excel import (parse_upload, parse_upload_sheets, parse_upload_raw,
                          parse_upload_multi_raw)
from ..qc_rules import (QC_UNIT_PRICE, QC_PENALTY_PER_ITEM,
                            QC_SOURCE_PRIORITY, QC_SOURCE_LABEL)

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
    # v1.7.1 已下线：H5投诉工单上传入口（表保留，口径已改读投诉宽表）
    "install_wide": {"name": "装机宽表", "target": "install_orders",
                     "columns": ["数据1：定单编码/区县/网格/受理时间/业务主题/定单状态/宽带类型/是否FTTR/回单人员/回单时间/24小时及时/8小时及时/是否履约/是否退单/一级退单原因/装机时长/完工时长/H5装机满意度",
                                 "盯控列（旧版导出无这些列时取空）：首联时间/首联是否及时/上门时间/是否及时上门/首次激活时间/回访时间/回访结果/IVR回访(装通/满意/评分/最终)/催单次数/二级退单原因/退单人员/AI回访结果/综调回访结果/地市回访结果"],
                     "note": "白名单解析「数据1」sheet（装机宽表月累计）；month 从受理时间推导；按月替换；供竣工率盯控卡片使用"},
    "complaint_wide": {"name": "投诉宽表", "target": "complaint_orders",
                       "columns": ["数据1：工单流水号/区县/投诉时间/智慧装维工单状态/当前处理人/投诉分类/24小时是否及时/8小时是否及时/是否履约/工单时长/故障是否解决/归档满意度"],
                       "note": "白名单解析「数据1」sheet（投诉宽表月累计）；month 从投诉时间推导；按月替换"},
    "install_query": {"name": "装机综合查询", "target": "install_realtime_status",
                      "columns": ["导出信息：定单编码/区域/受理时间/定单主题/工单状态/施工人/施工时间/网格名称/是否FTTR/一级退单原因"],
                      "note": "实时数据（比宽表早约2小时）；白名单解析「导出信息」sheet；"
                              "入库到独立的 install_realtime_status 表，**不覆盖**装机宽表，"
                              "仅供工单状态裁决使用（v1.6.5）"},
    "complaint_query": {"name": "投诉综合查询", "target": "complaint_realtime_status",
                        "columns": ["家宽投诉综合工单列表：EOMS工单号/区域/工单状态/当前处理人/施工部门/人/受理时间/投诉时间/投诉分类"],
                        "note": "实时数据（比宽表早约2小时）；白名单解析「家宽投诉综合工单列表」sheet；"
                                "month 从投诉时间推导；入库到独立的 complaint_realtime_status 表，**不覆盖**投诉宽表，"
                                "仅供工单状态裁决使用（v1.6.7）"},
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
    "qc": {"name": "质检规范（五项通报）", "target": "qc_records（质检通报分析·五表项合一）",           "columns": ["① 人工质检明细：区县/网格/定单编码/定单类型/回单人姓名/质检结果/不通过原因/不合格原因/质检考核/不结算金额",
                       "② FTTR质检工单汇总表：「FTTR质检数据」sheet 全月累计，取通报日当天",
                       "③ 企宽装机质检统计：「明细」sheet 全月累计，判定列「整体是否合格」",
                       "④ 装、维改约质检通报：「装机新增」「投诉新增」两个当日 sheet，判定列「听音质检（真实或虚假）」",
                       "⑤ 集团智能质检结果：「清单」sheet 全量，判定列「场景质检结果」"],
           "note": "市公司每日通报的 5 个质检表项合并为一个通报：**支持一次多选上传全部附件**，"
                   "系统按 sheet 构成自动识别来源（也可只传其中任意一个）；「分析汇总/汇总/分析」等透视表不入库，"
                   "由明细重算并与通报合计逐格核对；FTTR 月汇总 ⊇ 人工质检明细的 FTTR sheet、"
                   "企宽月统计 ⊇ 其企宽 sheet，同并存自动去重（保留带考核金额的那份）；"
                   "同一批文件归入同一「通报批次日」（取明细最大数据日），按 (批次日, 来源) 分别替换，"
                   "单传一个文件不会误删其它来源的数据；驱动「质检规范」页卡片区与不合格清单推送到装维端"},
    "broad_users": {"name": "广义用户清单", "target": "broad_user_list（广义用户明细，按工单编号增量更新）",
                    "columns": ["广义重复清单：工单上报时间/工单编号/工单建立日期/宽带账号/归属区县/"
                                "是否重复投诉/重复投诉次数/广义投诉原因明细/广义投诉七级分类"],
                    "note": "解析「广义重复清单」sheet（多行标题不参与）；工单编号为唯一键，已存在则只更新文件派生列，"
                            "保留电话/CID/小区/责任人/匹配状态等补齐字段；监利县→监利市归一；不删除文件外旧行；"
                            "仅市级管理员（super_admin）可上传与确认；入库后「广义用户清单」列表与 6 张统计卡实时生效"},
    "cid_data": {"name": "CID数据（智能网关）",
                 "target": "cid_records（CID 快照，整表替换）+ broad_user_list 关联补齐",
                 "columns": ["CID智能网关 CSV（GB18030，逗号分隔）：用户上网账号 / CID上报SN / 归属小区 / 归属区县"],
                 "note": "CID 版本周期更新（如 CID智能网关20260801.csv），上传先写 staging 预览，"
                         "确认后在事务内整表替换 cid_records，并按「账号→电话兜底」重匹配广义用户清单："
                         "只填空值（旧 CID 来源的 cid_sn 可刷新，community 只填空），"
                         "源表电话与当前处理人永不覆盖；再用「归属小区」匹配责任田回填责任人与网格。"
                         "单文件上限 400MB（其它渠道仍为 30MB），流式落盘解析；仅市级管理员可用"},
}


def _list_sheets(data: bytes, filename: str) -> list:
    """仅读取 sheet 名（不解析行），用于批量上传的自动识别。csv 返回 ['']。"""
    import io
    import openpyxl
    if not filename.lower().endswith((".xlsx", ".xlsm", ".xls")):
        return [""]
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        names = list(wb.sheetnames)
        wb.close()
        return names
    except Exception:
        return []


def _detect_metric(filename: str, sheets: list) -> str | None:
    """按 sheet 构成 + 文件名关键字识别文件归属的指标。

    质检五项优先（专属 sheet 精确匹配，避免 extension「当月明细」被误判）；
    其余指标按 sheet 签名(权重2) + 文件名关键字(权重1) 打分取最高，0 分返回 None。
    装机宽表/投诉宽表 仅 sheet 名都是「数据1」，靠文件名是否含「投诉」消歧。
    """
    names = {str(s).strip() for s in (sheets or [])}
    fn = filename or ""
    fnl = fn.lower()
    # 1) 质检五项：专属 sheet 精确匹配（注意 extension 的「当月明细」含「明细」子串，不能用子串）
    qc_sig = {"人工质检明细", "FTTR质检数据", "装机新增", "投诉新增", "明细",
              "FTTR质检工单汇总表", "集团智能质检结果", "装、维改约质检通报", "企宽装机质检统计"}
    if (qc_sig & names) or ({"清单", "分公司"} <= names) or ({"公式", "清单"} <= names) \
            or ("质检" in fn) or ("智能质检" in fn):
        return "qc"
    # 2) 其余指标：sheet 签名(权重2) + 文件名关键字(权重1) 打分
    sig = {
        "completion_rate": (["激励明细"], ["竣工率", "激励预通报", "激励预约", "激励"]),
        "extension": (["当月明细", "人员明细"], ["延伸服务"]),
        "install_wide": (["数据1"], ["装机宽表", "宽表查询", "装机"]),
        "complaint_wide": (["数据1"], ["投诉宽表", "宽表查询-投诉", "投诉宽", "投诉"]),
        "install_query": (["导出信息"], ["装机综合查询", "导出信息", "综合查询"]),
        "complaint_query": (["家宽投诉综合工单列表"], ["投诉综合查询", "家宽投诉综合工单列表"]),
        "h5_survey": (["H5数据统计", "装机参与率", "投诉参与率", "装机回单", "投诉回单", "累计装机低分核实"],
                      ["H5满意度", "装维H5", "满意度", "大音", "H5"]),
        "ai_visit": (["当月装机即访清单", "当月投诉即访清单", "装后即访-通报表", "投诉即访-通报表"],
                     ["AI装维即访", "即访", "AI装维"]),
        "broad_users": (["广义重复清单"], ["广义重复", "广义投诉", "广义用户"]),
    }
    # 装机宽表/综合查询 vs 投诉宽表/综合查询：仅 sheet 同名，靠文件名「投诉」区分
    penalty_install = "投诉" in fnl
    best, best_score = None, 0
    for m, (ss, ks) in sig.items():
        score = 2 * len(set(ss) & names) + sum(1 for k in ks if k.lower() in fnl)
        if m in ("install_wide", "install_query") and penalty_install:
            score = -1
        if score > best_score:
            best, best_score = m, score
    return best if best_score > 0 else None


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
        # 库内基线只取聚合值（最新受理时间 + 行数），不再把整月 ORM 行全部加载进内存
        mx, cnt = (db.query(func.max(model.accept_time), func.count())
                   .filter(model.month == m).one())
        old_key = ((mx or ""), int(cnt or 0))
        if new_key < old_key:
            skipped.append(f"{m}: 库内较新(受理至{old_key[0][:16]}/{old_key[1]}行)≥上传(受理至{new_key[0][:16]}/{new_key[1]}行)，未覆盖")
            continue
        # 缩水保护：行数或区县覆盖度明显下降时拒绝覆盖
        old_n, new_n = old_key[1], len(new_rows)
        if old_n >= 100 and new_n < old_n * 0.6:
            old_d = {d for (d,) in (db.query(model.district)
                                    .filter(model.month == m, model.district != "")
                                    .distinct().all()) if d}
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
                        "remark": remark,
                        "tag": _hs(_hcol(r, "业务标签(TagInformation)", "业务标签", "TagInformation",
                                         "产品标签", "业务类型"))})
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
            # 列名兼容：新版导出用「24小时是否及时」，旧版用「24小时及时」
            # 两者都没有时，按 受理→回单 时长重算，避免整列为空导致看板超时恒为 0
            "is_24h": (str(r.get("24小时及时") or r.get("24小时是否及时") or "").strip()
                       or _calc_timely(accept, _norm_time(r.get("回单时间")), 24)),
            "is_8h": (str(r.get("8小时及时") or r.get("8小时是否及时") or "").strip()
                      or _calc_timely(accept, _norm_time(r.get("回单时间")), 8)),
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
            # H5短信盯控（v1.6.8）：宽表自带，不再依赖「宽表查询-装机」单独上传
            "community_name": _short_addr(r.get("所属小区") or r.get("小区简称")),
            "service_number": _pick_num(r, "服务号码", "业务号码"),
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
            # H5短信盯控（v1.6.8）：投诉宽表没有独立的小区/号码列，从投诉正文解析
            **_cmp_contact(r),
        })
    return out


# ---- H5 短信盯控字段（v1.6.8）：宽表自带的小区/号码直接落库 ----
_ADDR_PREFIX = "湖北荆州"
_ADDR_DISTRICTS = ("洪湖市", "监利市", "石首市", "松滋市", "公安县", "江陵县",
                   "荆州区", "沙市区", "荆州开发区")


def _short_addr(v) -> str:
    """「湖北荆州洪湖市XX小区」→「XX小区」；9 个区县前缀都剥；空/NaN → 空串"""
    t = str(v or "").strip()
    if not t or t.lower() in ("nan", "none"):
        return ""
    if t.startswith(_ADDR_PREFIX):
        t = t[len(_ADDR_PREFIX):]
        for d in _ADDR_DISTRICTS:
            if t.startswith(d):
                t = t[len(d):]
                break
    return t.strip()


def _num_str(v) -> str:
    """号码/编码转字符串：pandas 读成 float 时 5200016915001.0 → 5200016915001"""
    if v is None:
        return ""
    if isinstance(v, float):
        if v != v:                      # NaN
            return ""
        return str(int(v)) if v.is_integer() else str(v)
    t = str(v).strip()
    if t.lower() in ("nan", "none"):
        return ""
    if t.endswith(".0") and t[:-2].isdigit():
        return t[:-2]
    return t


def _pick_num(row: dict, *keys) -> str:
    """按候选列名取第一个非空号码。**不能写 a or b**：pandas 的 NaN 是真值。"""
    for k in keys:
        got = _num_str(row.get(k))
        if got:
            return got
    return ""


def _complaint_contact(topic, content, row) -> tuple:
    """从投诉主题/内容里抠 装机地址 / 主叫号码 / 受理电话号码。

    实测投诉宽表没有独立的「小区名称」「故障号码」列，这些信息只在
    「投诉内容」正文里，形如：
        [装机地址: 湖北荆州洪湖市XX小区X栋X单元]  主叫号码：139xxxx
        受理电话号码：0716xxxx
    取不到时回退表格里可能存在的同名列，再不行给空串（前端只是显示空）。
    """
    text = str(topic or "") + "\n" + str(content or "")
    community = ""
    m = re.search(r"\[\s*装机地址\s*[:：]\s*([^\]]{2,300}?)\s*\]", text)
    if m:
        community = _short_addr(m.group(1))
    if not community:
        community = _short_addr(row.get("所属小区") or row.get("小区名称") or row.get("小区简称"))
    mf = re.search(r"主叫号码\s*[:：]\s*(\d{5,20})", text)
    fault = mf.group(1) if mf else _pick_num(row, "故障号码", "主叫号码")
    mc = re.search(r"受理电话号码\s*[:：]\s*(\d{5,20})", text)
    contact = mc.group(1) if mc else ""
    if not contact:
        contact = _pick_num(row, "联系电话", "联系人")
    return community, contact, fault


def _cmp_contact(row: dict) -> dict:
    """投诉宽表一行 → {community_name, contact_person, fault_number}"""
    c, contact, fault = _complaint_contact(row.get("投诉主题"), row.get("投诉内容"), row)
    return {"community_name": c, "contact_person": contact, "fault_number": fault}


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


def _count_status_diff(db: Session, payload: list) -> int:
    """实时快照与装机宽表状态不一致的单数（含宽表侧缺失）"""
    try:
        from ..models import InstallRealtimeStatus as _IRT
        ids = [p["order_id"] for p in payload]
        cur = {r.order_id: (r.status or "") for r in
               db.query(InstallOrder.order_id, InstallOrder.status)
               .filter(InstallOrder.order_id.in_(ids)).all()}
        return sum(1 for p in payload if cur.get(p["order_id"], None) != p["status"])
    except Exception:
        return 0


def _count_complaint_status_diff(db: Session, payload: list) -> int:
    """投诉实时快照与投诉宽表状态不一致的单数（含宽表侧缺失）。

    和装机那边一样，只做「报个数」让操作者知道这次校对的价值，
    不影响入库结果——裁决在读取时做（mp.py / metrics.py 各调 status_resolver）。
    """
    try:
        nos = [p["eoms_no"] for p in payload]
        cur = {r.eoms_no: (r.status or "") for r in
               db.query(ComplaintOrder.eoms_no, ComplaintOrder.status)
               .filter(ComplaintOrder.eoms_no.in_(nos)).all()}
        return sum(1 for p in payload if cur.get(p["eoms_no"], None) != p["status"])
    except Exception:
        return 0


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
            # v1.6.5 裁决所需：实时状态要配一个「生效时间下界」，否则按时间比不过别源
            "dispatch_time": _norm_time(r.get("施工调度接收时间")),
            "remark": str(r.get("退单备注信息") or "").strip()[:255],
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
            # v1.6.7 裁决所需：实时状态要配一个「生效时间下界」。
            # 投诉综合查询没有独立的「状态更新时间」，只有派单/受理接收时间可作下界，
            # 与装机那边用「施工调度接收时间」同理。
            "dispatch_time": _norm_time(r.get("派单时间") or r.get("施工调度接收时间")
                                        or r.get("当前环节进入时间")),
            "remark": str(r.get("备注") or r.get("处理备注") or "").strip()[:255],
        })
    return out


# ---------------- 质检五项通报（v1.8.5：五个表项合并为一个通报） ----------------

QC_SRC_SHEETS = {
    "改约质检": ["分析", "装机新增", "投诉新增", "装机累计", "投诉累计"],
    "集团AI质检": ["汇总", "公式", "清单", "分公司", "不合格原因"],
    "FTTR质检": ["汇总", "FTTR质检数据", "不合格原因"],
    "企宽质检": ["汇总", "明细"],
    "人工质检明细": ["分析汇总", "人工质检明细", "FTTR", "企宽装机"],
}


def _qc_detect_source(sheets: dict, filename: str = "") -> str:
    """按 sheet 构成识别这属于哪个通报表项。

    五张表的 sheet 名互不相同，比猜文件名可靠（文件名里常有「10.31」「10月截止31日」
    等多种写法）。判断顺序按「特征最鲜明」优先：
      改约   → 有「装机新增」「投诉新增」（且「分析」里有通报日期单元格）
      集团AI → 有「清单」+「分公司」
      FTTR   → 有「FTTR质检数据」
      企宽   → 有「明细」+「汇总」（区别于 FTTR 的是它没有 FTTR质检数据）
      人工   → 有「人工质检明细」
    """
    names = {str(k).strip() for k in sheets.keys()}
    if {"装机新增", "投诉新增"} & names or {"装机累计", "投诉累计"} & names:
        return "改约质检"
    if "人工质检明细" in names:
        return "人工质检明细"
    if "FTTR质检数据" in names:
        return "FTTR质检"
    if "清单" in names and ("分公司" in names or "公式" in names):
        return "集团AI质检"
    if "明细" in names and "汇总" in names:
        return "企宽质检"
    fn = str(filename or "")
    if "改约" in fn:
        return "改约质检"
    if "企宽" in fn:
        return "企宽质检"
    if "FTTR" in fn.upper():
        return "FTTR质检"
    if "智能质检" in fn:
        return "集团AI质检"
    return "人工质检明细"


def _qc_sheet_dicts(rows: list) -> list[dict]:
    """原始矩阵 → 字典行。表头不一定在第 1 行：FTTR / 企宽 sheet 首行是「XX质检不合格」标题，
    按「同时含 区县 与 定单编码/服务号码」定位表头行，找不到就退回首行。"""
    if not rows:
        return []
    hi = 0
    for i, r in enumerate(rows[:6]):
        cells = {str(c).strip() for c in r if c is not None}
        if "区县" in cells and ({"定单编码", "服务号码"} & cells):
            hi = i
            break
    head = rows[hi]
    seen: dict = {}
    header = []
    for h in head:
        h = str(h).strip() if h is not None else ""
        if h in seen:
            seen[h] += 1
            header.append(f"{h}.{seen[h]}")
        else:
            seen[h] = 0
            header.append(h)
    out = []
    for r in rows[hi + 1:]:
        if all(v is None or str(v).strip() == "" for v in r):
            continue
        out.append({header[i]: r[i] for i in range(min(len(header), len(r)))})
    return out


def _qc_report_date(sheets: dict, rows: list[dict], filename: str) -> str:
    """通报批次日。优先「分析」sheet 里的通报日期单元格，其次明细最大数据日。

    邯郸的日常类推陷阱：改约通报文件名/标题写「10.31」但明细是 30 日的单 ——
    单行数据日是对的，批次日要按通报标题走，否则五个来源会被拆到两天去。"""
    # 只认「分析」sheet 里形如「通报日期：2026/10/31」的明确标注。
    # 不能放宽到「汇总」sheet：FTTR 月汇总的标题行写的是「通报时间：2026-11-01」
    # （次日凌晨生成报表），照它取会把批次日推到 11-01，与名列应有的 10-31 不符。
    for sh, rw in (sheets or {}).items():
        if str(sh).strip() != "分析":
            continue
        for r in list(rw)[:6]:
            for c in r:
                s2 = str(c or "").strip()
                m = re.match(r"^\u901a\u62a5\u65e5\u671f\uff1a\s*(20\d{2})[/-](\d{1,2})[/-](\d{1,2})", s2)
                if m:
                    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    ds = sorted({r.get("date") for r in rows if r.get("date")})
    return ds[-1] if ds else ""


def _qc_category(raw_type, sheet: str) -> str:
    """原始定单类型 → 通报八类之一。

    注意顺序：先 FTTR 再 装机（「FTTR全量质检」不含「装机」但「企宽装机质检」含），
    投诉类三种（投诉质检 / 互联网电视投诉归因质检 / 一次上门解决不彻底投诉质检）归为一类。
    """
    t = str(raw_type or "").strip()
    if t:
        if "FTTR" in t.upper():
            return "FTTR质检"
        if "企宽" in t:
            return "企宽装机质检"
        if "装机" in t:
            return "装机质检"
        if "投诉" in t:
            return "投诉质检"
        if "义诊" in t:
            return "义诊质检"
        if "延伸" in t:
            return "延伸服务质检"
        if "看家" in t:
            return "移动看家质检"
        if "组网" in t:
            return "智能组网质检"
        return t
    st = str(sheet or "")
    if "企宽" in st:
        return "企宽装机质检"
    if "FTTR" in st.upper():
        return "FTTR质检"
    return ""


def _qc_ng_item(reason, fallback="") -> str:
    """不合格原因原文 → 归类项（聚合用）。
    原文形如「资源清查不规范-清查结果与现场图片不一致|」「服务卡片-其他（请备注说明）|」，
    取第一条，破折号后为具体项，去掉「（请备注说明）」这类占位后缀。"""
    txt = str(reason or "").replace("|", "\n").strip()
    first = next((ln.strip() for ln in txt.split("\n") if ln.strip()), "")
    if not first:
        first = str(fallback or "").strip()
    if not first:
        return "未填原因"
    for sep in ("-", "—", "－"):
        if sep in first:
            first = first.split(sep, 1)[1].strip() or first
            break
    for junk in ("（请备注说明）", "(请备注说明)", "（备注）"):
        first = first.replace(junk, "")
    return first.strip()[:64] or "未填原因"


def _qc_row(**kw) -> dict:
    """统一补齐 qc_records 行字段，避免五个解析器各写一遍默认值。"""
    base = {
        "date": "", "month": "", "district": "", "grid": "", "category": "", "raw_type": "",
        "order_no": "", "qc_order_no": "", "service_no": "", "biz_no": "", "subject": "",
        "handler": "", "handler_no": "", "qc_person": "", "result": "", "is_pass": True,
        "ng_item": "", "ng_detail": "", "penalty": 0.0, "no_settle": 0.0,
        "dispatch_time": "", "finish_time": "", "source": "",
    }
    base.update(kw)
    base["month"] = (base["date"] or "")[:7]
    return base


# ---------- 1. 人工质检明细（抽检 6 类 + 自带的 FTTR / 企宽 sheet） ----------

def _qc_parse_manual(sheets: dict) -> list[dict]:
    """人工质检明细：三个明细 sheet 合一。跳过无区县/无编码/无法归类的行（多为小计行）。"""
    out: list[dict] = []
    seen: set = set()
    for sheet in ("人工质检明细", "FTTR", "企宽装机"):
        for r in _qc_sheet_dicts(sheets.get(sheet) or []):
            district = str(r.get("区县") or "").strip()
            order_no = str(r.get("定单编码") or "").strip()
            raw_type = str(r.get("定单类型") or "").strip()
            cat = _qc_category(raw_type, sheet)
            if not district or not order_no or not cat:
                continue
            # 去重键含「环节」：同一质检单会有一级+二级两条（SGZJ...013 装机、SGZJ...011 组网），
            # 通报是按行计的（装机 26 / 组网 13 就含这两条），只按 定单编码+类型 去重会少 2 单
            key = (order_no, cat, str(r.get("环节") or "").strip())
            if key in seen:
                continue
            seen.add(key)
            finish = _norm_time(r.get("质检单完成时间") or r.get("质检时间") or r.get("回单时间"))
            dispatch = _norm_time(r.get("质检单派单时间") or r.get("受理时间"))
            date = (finish or dispatch)[:10]
            if not date:
                continue
            res_raw = str(r.get("质检结果") or r.get("整体是否合格") or "").strip()
            is_pass = res_raw in ("通过", "合格", "是")
            detail = str(r.get("不通过原因") or r.get("质检不合格原因") or "").strip()
            detail = detail.replace("|", "；").strip("；")[:500]
            remark = str(r.get("不合格原因") or "").strip()
            penalty = _to_num(r.get("质检考核"), as_int=False)
            no_settle = _to_num(r.get("不结算金额"), as_int=False)
            if not is_pass:
                if not penalty:
                    penalty = QC_PENALTY_PER_ITEM
                if not no_settle:
                    no_settle = -QC_UNIT_PRICE.get(cat, 0.0)
            out.append(_qc_row(
                date=date, district=district, grid=str(r.get("网格") or "").strip(),
                category=cat, raw_type=raw_type or sheet, order_no=order_no,
                qc_order_no=str(r.get("质检定单编码") or "").strip(),
                service_no=str(r.get("服务号码") or "").strip(),
                biz_no=str(r.get("业务号码") or "").strip()[:128],
                subject=str(r.get("定单主题") or r.get("业务主题") or "").strip()[:128],
                handler=_strip_district_prefix(r.get("回单人姓名") or r.get("回单人"), district),
                handler_no=str(r.get("回单人工号") or r.get("回单工号") or "").strip(),
                qc_person=str(r.get("质检人姓名") or r.get("质检人") or "").strip(),
                result="通过" if is_pass else "不通过", is_pass=is_pass,
                # 「不合格原因」列是质检员归并后的规范说法（回单内容不一致 / 未填写虚拟号码），
                # 比机器拼的「服务卡片-其他（请备注说明）」更适合聚合展示，优先用它
                ng_item="" if is_pass else (
                    (str(r.get("不合格原因") or "").strip()[:64] or _qc_ng_item(detail, remark))),
                ng_detail=detail, penalty=round(penalty, 2), no_settle=round(no_settle, 2),
                dispatch_time=dispatch, finish_time=finish, source="人工质检明细"))
    return out


# ---------- 2. FTTR质检工单汇总表（当月累计，全量） ----------

def _qc_trim_to_latest(rows: list[dict]) -> list[dict]:
    """FTTR 月汇总 / 企宽月统计是「截止当日」的累计台账，整表入库会把整月的行都写进去。

    通报卡片只认「当日质检量」那一列，所以截断到文件内的最大数据日
    （实测 FTTR 汇总 sheet「当日质检量」、企宽汇总「当天（31日）抽检量」逐区县吻合）。
    """
    dates = {r.get("date") or "" for r in rows} - {""}
    if len(dates) <= 1:
        return rows
    latest = max(dates)
    return [r for r in rows if (r.get("date") or "") == latest]


def _qc_parse_fttr(sheets: dict) -> list[dict]:
    """FTTR 质检工单汇总表 → FTTR质检。 sheet「FTTR质检数据」，表头在第 1 行。

    与人工质检明细里的 FTTR sheet 是同一批单（2026-10-31 实测 122 单完全重叠），
    同批上传时按 QC_SOURCE_PRIORITY 保留人工质检明细那份（它带考核/不结算金额列）。
    """
    out = []
    seen = set()
    for r in _qc_sheet_dicts(sheets.get("FTTR质检数据") or []):
        district = str(r.get("区县") or "").strip()
        order_no = str(r.get("定单编码") or "").strip()
        if not district or not order_no:
            continue
        key = (order_no, str(r.get("环节") or "").strip())
        if key in seen:
            continue
        seen.add(key)
        finish = _norm_time(r.get("质检单完成时间"))
        dispatch = _norm_time(r.get("质检单派单时间"))
        date = (finish or dispatch)[:10]
        if not date:
            continue
        is_pass = str(r.get("质检结果") or "").strip() in ("通过", "合格")
        detail = str(r.get("不通过原因") or "").strip().replace("|", "；").strip("；")[:500]
        out.append(_qc_row(
            date=date, district=district, grid=str(r.get("网格") or "").strip(),
            category="FTTR质检", raw_type=str(r.get("定单类型") or "FTTR全量质检").strip(),
            order_no=order_no, qc_order_no=str(r.get("质检定单编码") or "").strip(),
            service_no=str(r.get("服务号码") or "").strip(),
            biz_no=str(r.get("业务号码") or "").strip()[:128],
            subject=str(r.get("定单主题") or "").strip()[:128],
            handler=_strip_district_prefix(r.get("回单人姓名"), district),
            handler_no=str(r.get("回单人工号") or "").strip(),
            qc_person=str(r.get("质检人姓名") or "").strip(),
            result="通过" if is_pass else "不通过", is_pass=is_pass,
            ng_item="" if is_pass else _qc_ng_item(detail),
            ng_detail=detail,
            penalty=0.0 if is_pass else QC_PENALTY_PER_ITEM,
            no_settle=0.0 if is_pass else -QC_UNIT_PRICE.get("FTTR质检", 0.0),
            dispatch_time=dispatch, finish_time=finish, source="FTTR质检"))
    return _qc_trim_to_latest(out)


# ---------- 3. 企宽装机质检统计（当月累计，全量） ----------

def _qc_parse_ent(sheets: dict) -> list[dict]:
    """企宽装机质检统计 → 企宽装机质检。sheet「明细」，判定列「整体是否合格」。

    不合格原因散在三列（录音不合格原因 / 场景一致性不一致原因 / 主光猫质检不合格原因），
    取第一个非空的；表尾还有一根汇总性质的「不合格原因」列，优先用它。
    """
    out = []
    seen = set()
    for r in _qc_sheet_dicts(sheets.get("明细") or []):
        district = str(r.get("区县") or "").strip()
        order_no = str(r.get("定单编码") or "").strip()
        if not district or not order_no or order_no in seen:
            continue
        seen.add(order_no)
        finish = _norm_time(r.get("质检时间"))
        dispatch = _norm_time(r.get("受理时间"))
        date = (finish or dispatch)[:10]
        if not date:
            continue
        is_pass = str(r.get("整体是否合格") or "").strip() in ("合格", "是", "通过")
        reason = (str(r.get("不合格原因") or "").strip()
                  or str(r.get("质检不合格原因") or "").strip()
                  or str(r.get("录音不合格原因") or "").strip()
                  or str(r.get("箱内全景和线缆标签照片不一致原因") or "").strip())
        detail = reason.replace("|", "；").strip("；")[:500]
        out.append(_qc_row(
            date=date, district=district, grid=str(r.get("网格") or "").strip(),
            category="企宽装机质检", raw_type="企宽装机全量质检",
            order_no=order_no, service_no=str(r.get("服务号码") or "").strip(),
            biz_no=str(r.get("业务号码") or "").strip()[:128],
            subject=str(r.get("业务主题") or "").strip()[:128],
            handler=_strip_district_prefix(r.get("回单人员"), district),
            handler_no=str(r.get("回单工号") or "").strip(),
            qc_person=str(r.get("质检人") or "").strip(),
            result="通过" if is_pass else "不通过", is_pass=is_pass,
            ng_item="" if is_pass else (_qc_ng_item(detail) if detail else "未填原因"),
            ng_detail=detail,
            penalty=0.0 if is_pass else QC_PENALTY_PER_ITEM,
            no_settle=0.0 if is_pass else -QC_UNIT_PRICE.get("企宽装机质检", 0.0),
            dispatch_time=dispatch, finish_time=finish, source="企宽质检"))
    return _qc_trim_to_latest(out)


# ---------- 4. 装、维改约质检通报（听音判「真实 / 虚假」） ----------

def _qc_parse_change(sheets: dict) -> list[dict]:
    """装、维改约质检通报 → 装机改约质检 + 投诉改约质检。

    只取「当日新增」两个 sheet（装机新增 + 投诉新增）—— 与通报「30日」列口径一致，
    实测 44 + 16 = 60 件 = 通报合计抽检量 60，虚假 2 件也与通报「虚假量」逐区县吻合。
    「装机累计 / 投诉累计 / 撤单 / 未听师傅改约*」是月度滚动台账，不计入当日通报。
    """
    out = []
    seen: set = set()
    specs = [("装机新增", "装机改约质检", "装机"), ("投诉新增", "投诉改约质检", "维")]
    for sheet, cat, kind in specs:
        for r in _qc_sheet_dicts(sheets.get(sheet) or []):
            district = str(r.get("区县") or "").strip()
            order_no = str(r.get("定单编码") or r.get("工单流水号") or r.get("ID") or "").strip()
            verdict = str(r.get("听音质检（真实或虚假）") or "").strip()
            if not district or not order_no or verdict not in ("真实", "虚假"):
                continue
            key = (sheet, order_no)
            if key in seen:
                continue
            seen.add(key)
            # 改约通报没有「合格/不合格」措辞，「虚假」即不合格
            is_pass = verdict == "真实"
            date = _norm_time(r.get("受理时间") or r.get("投诉时间") or r.get("派单时间"))[:10]
            if not date:
                continue
            reason = str(r.get("虚假原因") or "").strip()
            if kind == "装机":
                handler = str(r.get("当前处理人或回单人") or r.get("回单人员") or "").strip()
                handler_no = str(r.get("回单工号") or "").strip()
                grid = str(r.get("网格") or "").strip()
                subject = str(r.get("业务主题") or "").strip()[:128]
                service_no = str(r.get("服务号码") or "").strip()
                finish = _norm_time(r.get("回单时间") or r.get("受理时间"))
            else:
                handler = str(r.get("当前处理人") or r.get("最终回单人员") or "").strip()
                handler_no = str(r.get("最终回单人员工号") or "").strip()
                grid = str(r.get("最终回单网格") or r.get("首次派单目标网格") or "").strip()
                subject = str(r.get("投诉分类") or r.get("投诉主题") or "").strip()[:128]
                service_no = str(r.get("宽带帐号") or r.get("上网账号") or "").strip()
                finish = _norm_time(r.get("派单时间"))
            out.append(_qc_row(
                date=date, district=district, grid=grid, category=cat,
                raw_type=f"改约听音质检（{kind}）", order_no=order_no,
                service_no=service_no,
                biz_no=str(r.get("业务号码") or r.get("宽带帐号") or "").strip()[:128],
                subject=subject,
                handler=_strip_district_prefix(handler, district),
                handler_no=handler_no, qc_person="",
                result="真实" if is_pass else "虚假", is_pass=is_pass,
                ng_item="" if is_pass else (reason[:64] or "改约虚假"),
                ng_detail=reason[:500],
                # 改约通报只有「真实/虚假」判定，不带考核与不结算金额列 —— 不自作主张计数，记 0
                penalty=0.0, no_settle=0.0,
                dispatch_time=_norm_time(r.get("受理时间") or r.get("派单时间")),
                finish_time=finish, source="改约质检"))
    return out


# ---------- 5. 集团智能质检结果（AI 图像质检） ----------

# 荆州九个区县（集团智能质检「清单」sheet 的「区」列取值为纯区县名，
# 用它过滤掉合计行 / 外市数据 —— 换县名这里同步维护即可）
_QC_DISTRICTS = {"荆州区", "沙市区", "荆州开发区", "公安县", "监利市", "江陵县",
                 "石首市", "松滋市", "洪湖市"}


def _qc_parse_aiqc(sheets: dict) -> list[dict]:
    """集团智能质检结果 → 集团AI质检。

    取「清单」sheet（全量 351 单，与「分公司」sheet 的荆州总量逐区县完全一致），
    判定列「场景质检结果」= 合格 / 不合格 / 场景不支持（后两者都算不合格）。
    「不合格原因」sheet 是质检员的复核台账（对的 IA-analysis 结果、person 精确责任人），
    仅用于给清单行的不合格项补一层人核结果，不额外计单。
    """
    # 台账：定单编码 → (机器原因, 人工核实原因, 责任人)
    manual: dict = {}
    for r in _qc_sheet_dicts(sheets.get("不合格原因") or []):
        no = str(r.get("定单编码") or "").strip()
        if not no:
            continue
        manual[no] = (str(r.get("原因") or "").strip(),
                      str(r.get("人工核实原因") or "").strip(),
                      str(r.get("责任人") or "").strip())

    out = []
    seen: set = set()
    for r in _qc_sheet_dicts(sheets.get("清单") or []):
        district = str(r.get("区") or "").strip()
        order_no = str(r.get("定单编码") or "").strip()
        # 只收荆州下属区县：sheet 里可能夹着外市/合计行
        if not district or not order_no or district not in _QC_DISTRICTS:
            continue
        if order_no in seen:
            continue
        seen.add(order_no)
        finish = _norm_time(r.get("机器质检完成时间") or r.get("工单完成时间"))
        dispatch = _norm_time(r.get("工单完成时间"))
        date = (finish or dispatch)[:10]
        if not date:
            continue
        res = str(r.get("场景质检结果") or "").strip()
        is_pass = res == "合格"
        raw_reason = str(r.get("原因.1") or r.get("原因") or "").strip()
        if not is_pass and not raw_reason:
            raw_reason = res or "场景质检未通过"
        detail = raw_reason.replace("|", "；").strip("；")[:500]
        m = manual.get(order_no) or ("", "", "")
        out.append(_qc_row(
            date=date, district=district, grid=str(r.get("所属网格") or "").strip(),
            category="集团AI质检", raw_type=str(r.get("业务类型") or "集团智能质检").strip(),
            order_no=order_no,
            qc_order_no=str(r.get("工单编号") or "").strip(),
            service_no=str(r.get("宽带账号") or "").strip(),
            biz_no=str(r.get("尾纤二维码标识") or "").strip()[:128],
            subject=str(r.get("分纤箱名称") or "").strip()[:128],
            handler=_strip_district_prefix(r.get("装维姓名"), district),
            handler_no=str(r.get("装维工号") or "").strip(),
            qc_person=m[2] if not is_pass else "",
            result="合格" if is_pass else res or "不合格", is_pass=is_pass,
            ng_item="" if is_pass else (m[1] or _qc_ng_item(detail)),
            ng_detail=detail if is_pass or not m[0] else f"{detail}｜人工核实：{m[0]}"[:500],
            # 集团智能质检同样不带金额列，只作清单推送整改用
            penalty=0.0, no_settle=0.0,
            dispatch_time=dispatch, finish_time=finish, source="集团AI质检"))
    return out


_QC_PARSERS = {
    "人工质检明细": _qc_parse_manual,
    "FTTR质检": _qc_parse_fttr,
    "企宽质检": _qc_parse_ent,
    "改约质检": _qc_parse_change,
    "集团AI质检": _qc_parse_aiqc,
}


def _normalize_qc_file(sheets: dict, filename: str = "") -> tuple[str, list[dict]]:
    """单个通报文件 → (来源, 明细行)。识别不了来源返回 ('', [])。"""
    src = _qc_detect_source(sheets, filename)
    rows = _QC_PARSERS[src](sheets)
    return src, rows


def _merge_qc_files(parsed: list[tuple[str, list[dict]]], sheets_map: dict) -> tuple[list[dict], str, dict]:
    """把同批上传的多个通报文件合并成一批入库行。

    · 通报批次日 report_date **整批统一**：改约通报是前一日数据却按次日通报、
      集团AI质检更是自带 10-30 标注，各取各的会被拆成两天，看不到一张完整通报。
      取各文件候选日期的最大值：改约认「分析」sheet 的「通报日期：」标注，其余取明细最大数据日。
    · 跨文件去重：FTTR 月汇总 ⊇ 人工质检明细的 FTTR sheet、企宽月统计 ⊇ 其企宽 sheet，
      专项来源优先占位（这样每个来源卡片的数量 = 该张通报自己的「汇总」sheet 合计行），
      同名重复行由综合表（人工质检明细）承担；同一单的「一级+二级质检」两条都保留。
    """
    # 按来源优先级升序：专项表（FTTR/企宽）优先占位，综合表（人工质检明细）的同名行随后被去重
    ordered = list(parsed)
    ordered.sort(key=lambda x: QC_SOURCE_PRIORITY.get(x[0], 9))
    # 通报批次日 **整批统一**：改约通报的数据日是前一日、集团AI质检更是独立标注 10-30，
    # 若各自取自己的日期，五个来源会被拆到两天去看不到一张完整通报。
    # 取各文件候选日期的最大值（改约取「分析」sheet 的通报日期标注，其余取明细最大数据日）。
    cand = [(_qc_report_date(sheets_map.get(src) or {}, rows, src) or "").strip()
            for src, rows in ordered]
    cand = [c for c in cand if c]
    report_date = max(cand) if cand else ""
    all_rows: list[dict] = []
    for src, rows in ordered:
        if not rows:
            continue
        for r in rows:
            r["report_date"] = report_date or r["date"]
            all_rows.append(r)
    # 去重：同一 (单号, 类型) 在每个来源里出现几次就保留几次，由优先级高的来源先占额度。
    # 不能简单地按键 Set 去重 —— 人工质检明细里同一单有「一级质检 + 二级质检」两条，
    # 通报本身就是按行计的，只按键去重会把这 2 单吞掉（实测 198 → 196）。
    occurs: dict = {}
    per_src: dict = {}
    for src, rows in ordered:
        c: dict = {}
        for r in rows:
            key = (r.get("order_no"), r.get("category"))
            c[key] = c.get(key, 0) + 1
        per_src[src] = c
        for key, n in c.items():
            occurs[key] = max(occurs.get(key, 0), n)
    # 额度是 **全局** 的：all_rows 已按来源优先级升序，高优先级来源先占，
    # 占满 occurs[key] 后，低优先级来源（人工质检明细）的同名单不再计入。
    out, emitted = [], {}
    for r in all_rows:
        key = (r.get("order_no"), r.get("category"))
        cap = occurs.get(key, 0)
        used = emitted.get(key, 0)
        if cap and used >= cap:
            continue
        emitted[key] = used + 1
        out.append(r)
    out.sort(key=lambda x: (x.get("is_pass") is True, x.get("district") or "", x.get("category") or ""))
    stat = {src: len(rows) for src, rows in ordered}
    return out, report_date, stat


# ---------- 广义用户清单导入（broad_user_list） ----------
_BROAD_DISTRICT = {"监利县": "监利市"}
_BROAD_DETAIL_MAX = 200


def _broad_date(v) -> str:
    """工单上报/建立日期：支持 20261104、2026-11-04、Excel 日期。"""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d")
    s = re.sub(r"\D", "", str(v or ""))
    if len(s) < 8:
        return ""
    try:
        datetime.strptime(s[:8], "%Y%m%d")
    except ValueError:
        return ""
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def _broad_text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).replace("\u3000", " ").strip()


def _normalize_broad_users(raw: dict, filename: str = ""):
    """解析「广义重复清单」→ (rows, errors, warnings, dup_rows)。

    rows 的字段与 broad_user_list 对齐；errors 行不入库，warnings（缺宽带账号）仍入库。
    """
    rows_raw = raw.get("广义重复清单")
    if not rows_raw:
        raise HTTPException(status_code=400,
                            detail="未找到「广义重复清单」sheet，请上传广义投诉重复清单原件")
    header = [_broad_text(x) for x in rows_raw[0]]
    idx: dict = {}
    for i, h in enumerate(header):
        if h and h not in idx:
            idx[h] = i
    if "工单编号" not in idx or "工单上报时间" not in idx:
        raise HTTPException(status_code=400, detail="表头缺少必需列：工单上报时间 / 工单编号")
    rows, errors, warnings = [], [], []
    seen: dict = {}
    dup_rows = 0
    for i, raw_row in enumerate(rows_raw[1:], start=2):
        if not any(_broad_text(v) for v in raw_row):
            continue
        val = lambda name: raw_row[idx[name]] if name in idx and idx[name] < len(raw_row) else None
        order = _broad_text(val("工单编号"))
        if not order:
            errors.append({"row": i, "order_no": "", "error": "工单编号为空"})
            continue
        d = _broad_date(val("工单上报时间"))
        if not d:
            errors.append({"row": i, "order_no": order,
                           "error": "工单上报时间无法解析：%s" % _broad_text(val("工单上报时间"))})
            continue
        dist = _broad_text(val("归属区县"))
        if not dist:
            errors.append({"row": i, "order_no": order, "error": "归属区县为空"})
            continue
        dist = _BROAD_DISTRICT.get(dist, dist)
        acc = _broad_text(val("宽带账号"))
        if not acc:
            warnings.append({"row": i, "order_no": order, "warning": "宽带账号为空"})
        detail = _broad_text(val("广义投诉原因明细"))
        if len(detail) > _BROAD_DETAIL_MAX:
            detail = detail[:_BROAD_DETAIL_MAX - 9] + "…[原文见源表]"
        rep = _broad_text(val("是否重复投诉"))
        rec = {
            "work_order_no": order,
            "source_month": f"{int(d[5:7])}月",
            "report_date": d,
            "setup_date": _broad_date(val("工单建立日期")) or d,
            "district": dist,
            "internet_account": acc,
            "is_repeat": rep if rep in ("是", "否") else "",
            "repeat_times": _broad_text(val("重复投诉次数")) or "0",
            "complaint_lv7": _broad_text(val("广义投诉七级分类")),
            "complaint_detail": detail,
            "source_file": filename,
            "source_row": str(i),
        }
        if order in seen:
            dup_rows += 1
            rows[seen[order]] = rec       # 文件内同单号以最后一条为准
        else:
            seen[order] = len(rows)
            rows.append(rec)
    return rows, errors, warnings, dup_rows


def _broad_counts(db: Session, rows: list[dict]) -> tuple[int, int]:
    """返回 (库里已存在条数, 新增条数)。"""
    keys = [r["work_order_no"] for r in rows]
    exist: set = set()
    for i in range(0, len(keys), 400):
        chunk = keys[i:i + 400]
        sql = ("SELECT work_order_no FROM broad_user_list WHERE work_order_no IN (%s)"
               % ", ".join(":k%d" % j for j in range(len(chunk))))
        for (k,) in db.execute(text(sql), {"k%d" % j: v for j, v in enumerate(chunk)}):
            exist.add(k)
    upd = sum(1 for k in keys if k in exist)
    return upd, len(keys) - upd


_BROAD_UPSERT = text("""
INSERT INTO broad_user_list
  (seq, source_month, work_order_no, report_date, setup_date, district, internet_account,
   phone, cid_sn, community, owner, grid, is_repeat, repeat_times, complaint_lv7, complaint_detail,
   phone_source, owner_source, match_status, unmatch_reason, source_file, source_row,
   cid_source, owner_original)
VALUES
  (:seq, :source_month, :work_order_no, :report_date, :setup_date, :district, :internet_account,
   '', '', '', '', '', :is_repeat, :repeat_times, :complaint_lv7, :complaint_detail,
   '无(待CID匹配)', '无(待责任田匹配)', '待正式匹配',
   '用户联系电话缺；CID上报SN缺；归属小区缺；责任人缺', :source_file, :source_row, '', '')
ON CONFLICT(work_order_no) DO UPDATE SET
  source_month = excluded.source_month,
  report_date = excluded.report_date,
  setup_date = excluded.setup_date,
  district = excluded.district,
  internet_account = excluded.internet_account,
  is_repeat = excluded.is_repeat,
  repeat_times = excluded.repeat_times,
  complaint_lv7 = excluded.complaint_lv7,
  complaint_detail = excluded.complaint_detail,
  source_file = excluded.source_file,
  source_row = excluded.source_row
""")


def _apply_broad_users(db: Session, rows: list[dict]) -> tuple[int, int]:
    """按工单编号 upsert（一次事务）。返回 (更新条数, 新增条数)。"""
    upd, new = _broad_counts(db, rows)
    base = db.execute(text("SELECT COALESCE(MAX(seq), 0) FROM broad_user_list")).scalar() or 0
    for i, r in enumerate(rows, start=1):
        db.execute(_BROAD_UPSERT, dict(r, seq=base + i))
    return upd, new


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
    """上传解析预览（不入库，铁律 1）。单文件入口：从 request 读出字节后交给 _process_metric。"""
    if metric not in SYNC_MODULES:
        raise HTTPException(status_code=404, detail="模块不存在")
    if metric == "broad_users" and get_role(user) != "super_admin":
        raise HTTPException(status_code=403, detail="广义用户清单导入仅限市级管理员")
    if metric == "cid_data":
        # CID 快照体积大（约 260MB）：不走 read() 整读，直接流式落盘解析
        if get_role(user) != "super_admin":
            raise HTTPException(status_code=403, detail="CID 数据上传仅限市级管理员")
        ctype = request.headers.get("content-type", "")
        if "multipart/form-data" not in ctype:
            raise HTTPException(status_code=400, detail="请使用 multipart/form-data 上传（字段名 file）")
        cl = request.headers.get("content-length") or ""
        if cl.isdigit() and int(cl) > CID_MAX_BYTES + 8 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="请求体超过 CID 渠道 400MB 上限")
        form = await request.form()
        up = form.get("file")
        if up is None:
            raise HTTPException(status_code=400, detail="缺少 file 字段")
        fn = up.filename or "CID智能网关.csv"
        batch_id = str(uuid.uuid4())[:8].upper()
        try:
            res = await run_in_threadpool(_cid_upload_job, db, up.file, fn, batch_id)
        except HTTPException:
            raise
        except Exception as e:  # 解析异常转为 400，避免 500 只留日志
            raise HTTPException(status_code=400, detail="CID 解析失败：%s" % str(e)[:300])
        db.add(SyncJob(batch_id=batch_id, metric="cid_data", filename=fn,
                       rows=res["total"], status="preview",
                       preview_data=json.dumps(res, ensure_ascii=False)))
        db.commit()
        return {"batch_id": batch_id, "filename": fn, "total_rows": res["total"],
                "metric": "cid_data", "columns": [], "preview": [], **res}
    ctype = request.headers.get("content-type", "")
    if "multipart/form-data" in ctype:
        form = await request.form()
        up = form.get("file")
        if up is None:
            raise HTTPException(status_code=400, detail="缺少 file 字段")
        fn = up.filename or "upload"
        data = await up.read()
    else:
        fn = "raw.csv"
        data = await request.body()
    month = request.query_params.get("month")
    return await _process_metric(metric, [(fn, data)], db, month=month)


def _spool_size(f) -> int:
    """上传文件字节数（SpooledTemporaryFile 的 fileno() 会先落盘，避免整读进内存）。"""
    try:
        f.seek(0)
        return os.fstat(f.fileno()).st_size
    except (AttributeError, OSError, ValueError):
        f.seek(0)
        n = len(f.read())
        f.seek(0)
        return n


def _cid_version(filename: str) -> str:
    """从文件名取 CID 版本号，如 CID智能网关20260801.csv → 20260801。"""
    m = re.search(r"(20\d{2})(\d{2})(\d{2})", filename or "")
    return m.group(0) if m else ""


_CID_REQUIRED = ("用户上网账号", "CID上报SN", "归属小区")


def _cid_upload_job(db, fobj, filename: str, batch_id: str) -> dict:
    """流式解析 CID CSV（GB18030）→ cid_staging，并做不写库的匹配预览。

    不替换 cid_records、不写 broad_user_list，确认入库时才在事务内整表替换。
    """
    import csv
    import io

    t0 = time.time()
    size = _spool_size(fobj)
    if size > CID_MAX_BYTES:
        raise HTTPException(status_code=400, detail="文件 %.1fMB 超过 CID 渠道 400MB 上限，请拆分后上传"
                            % (size / 1048576.0))
    db_path = (db.get_bind().url.database or "").strip()
    try:
        sver = os.statvfs(os.path.dirname(db_path) or "/")
        free = sver.f_bavail * sver.f_frsize
    except OSError:
        free = 0
    if free and free < max(size * 3, 512 * 1024 * 1024):
        raise HTTPException(status_code=400, detail="服务器磁盘余量不足（剩 %.1fGB），暂不接收该 CID 文件"
                            % (free / 1073741824.0))

    backup = backup_db(db)
    ensure_cid_tables(db)
    purge_staging(db, keep_batch=batch_id)
    db.execute(text("DROP INDEX IF EXISTS idx_cid_stage_batch"))  # 批量插入后再建索引，快很多

    version = _cid_version(filename)
    total = skipped = no_sn = no_comm = 0
    buf = []
    wrapper = io.TextIOWrapper(fobj, encoding="gb18030", errors="replace", newline="")
    rd = csv.reader(wrapper)
    header = next(rd, None)
    if not header:
        raise HTTPException(status_code=400, detail="CID 文件为空")
    idx = {}
    for i, h in enumerate(header):
        h = norm_text(h)
        if h and h not in idx:
            idx[h] = i
    need = [h for h in _CID_REQUIRED if h not in idx]
    if need:
        raise HTTPException(status_code=400, detail="CID 表头缺少必需列：%s（实际表头：%s）"
                            % ("、".join(need), "、".join([norm_text(h) for h in header[:16]])))
    i_acc, i_sn, i_comm = idx["用户上网账号"], idx["CID上报SN"], idx["归属小区"]
    i_dist = idx.get("归属区县")
    for row in rd:
        total += 1
        acc = norm_account(row[i_acc] if i_acc < len(row) else "")
        if not acc:
            skipped += 1
            continue
        sn = norm_text(row[i_sn]) if i_sn < len(row) else ""
        comm = norm_text(row[i_comm]) if i_comm < len(row) else ""
        dist = norm_district(row[i_dist]) if (i_dist is not None and i_dist < len(row)) else ""
        if not sn:
            no_sn += 1
        if not comm:
            no_comm += 1
        buf.append({"batch_id": batch_id, "account_norm": account_norm_key(acc),
                    "account": acc, "cid_sn": sn, "community": comm,
                    "district": dist, "row_no": rd.line_num})
        if len(buf) >= 10000:
            db.execute(INSERT_STAGING, buf)
            buf = []
    if buf:
        db.execute(INSERT_STAGING, buf)
    db.execute(text("CREATE INDEX IF NOT EXISTS idx_cid_stage_batch "
                    "ON %s(batch_id, account_norm)" % CID_STAGING))
    db.commit()
    unique = db.execute(text(
        "SELECT COUNT(DISTINCT account_norm) FROM %s WHERE batch_id = :b" % CID_STAGING),
        {"b": batch_id}).scalar() or 0

    label = "CID智能网关" + version
    st = enrich_broad_rows(db, cid_label=label, apply=False, batch_id=batch_id)
    return {"version": version, "label": label, "total": total, "unique": unique,
            "skipped": skipped, "no_sn": no_sn, "no_comm": no_comm,
            "size_mb": round(size / 1048576.0, 1),
            "backup": backup, "elapsed": round(time.time() - t0, 1),
            "impact": st,
            "note": ("确认入库后：cid_records 整表替换为本次快照，并立即按账号（电话兜底）重匹配"
                     "广义用户清单，再用归属小区匹配责任田；只填空值，源表电话与当前处理人不覆盖")}


async def _process_metric(metric: str, files: list, db: Session, month: str = None) -> dict:
    """按指标解析一组文件（files: list[(filename, bytes)]）。

    - 非 qc 指标：取 files[0] 单文件解析；
    - qc 指标：files 内所有质检通报按 sheet 构成识别来源后合并为一个批次。
    返回 response dict（含 batch_id 等）。解析失败抛出 HTTPException——
    单文件入口 /upload/{metric} 原样透传 400，批量入口 /upload-batch 在内部捕获并转为带 error 的结果。
    """
    if not files:
        raise HTTPException(status_code=400, detail="未收到文件")
    extra: dict = {}
    col_override = None
    if metric == "extension":
        # 手册 10.2：白名单只取「当月明细」「人员明细」，避免全年明细/日明细等 sheet 混入
        # 手册 10.2：白名单只取「当月明细」「人员明细」，避免全年明细/日明细等 sheet 混入
        filename, sheets = await parse_upload_sheets(data=files[0][1], filename=files[0][0],
                                                     sheet_names=["当月明细", "人员明细"])
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
        # 竣工率激励通报：白名单只取「激励明细」；月份优先用调用方传入（批量时按文件名推导），
        # 否则缺省当月（通报表内无时间列，铁律 2 禁止硬编码）
        mm = re.search(r"(\d{1,2})月", files[0][0] or "")
        comp_month = month
        if not comp_month:
            if mm:
                y = datetime.now().year
                mo = int(mm.group(1))
                yr = y - 1 if mo > datetime.now().month else y  # 跨年（如 12 月文件在 1 月上传）
                comp_month = f"{yr}-{mo:02d}"
            else:
                comp_month = datetime.now().strftime("%Y-%m")
        if not re.match(r"^\d{4}-\d{2}$", comp_month):
            raise HTTPException(status_code=400, detail="month 格式应为 YYYY-MM")
        filename, sheets = await parse_upload_sheets(data=files[0][1], filename=files[0][0], sheet_names=["激励明细"])
        rows = _normalize_completion(sheets, comp_month)
        if not rows:
            raise HTTPException(status_code=400,
                                detail="「激励明细」sheet 未解析到数据行，请确认上传的是竣工率激励预通报表")
        extra = {"people": len(rows), "months": [comp_month],
                 "incentive_total": round(sum(r["incentive"] for r in rows), 2)}
        preview = rows[:100]
    elif metric == "install_wide":
        # 装机宽表：白名单「数据1」；month 从受理时间推导
        filename, sheets = await parse_upload_sheets(data=files[0][1], filename=files[0][0], sheet_names=["数据1"])
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
        filename, sheets = await parse_upload_sheets(data=files[0][1], filename=files[0][0], sheet_names=["数据1"])
        rows = _normalize_complaint_wide(sheets)
        if not rows:
            raise HTTPException(status_code=400,
                                detail="「数据1」sheet 未解析到数据行，请确认上传的是投诉宽表")
        months = sorted({r["month"] for r in rows if r["month"]})
        solved = sum(1 for r in rows if r["is_solved"] in ("已解决", "已维修"))
        extra = {"orders": len(rows), "months": months, "solved": solved}
        preview = rows[:100]
    elif metric == "install_query":
        filename, sheets = await parse_upload_sheets(data=files[0][1], filename=files[0][0], sheet_names=["导出信息"])
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
        filename, sheets = await parse_upload_sheets(data=files[0][1], filename=files[0][0],
                                                    sheet_names=["家宽投诉综合工单列表"])
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
            data=files[0][1], filename=files[0][0],
            sheet_names=["H5数据统计", "装机H5回复详单", "投诉H5回复详单", "累计装机低分核实", "累计投诉低分核实",
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
            data=files[0][1], filename=files[0][0],
            sheet_names=["装后即访-通报表", "投诉即访-通报表", "当月装机即访清单", "当月投诉即访清单"])
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
    elif metric == "qc":
        # 质检规范：市公司每天 5 个通报表项，支持一次多选全部上传；也允许只传其中一个。
        # 每个文件按 sheet 构成识别来源后独立解析，再合并成一个通报批次。
        raw_files = []
        for _fn, _data in files:
            _, _raw = await parse_upload_raw(data=_data, filename=_fn)
            raw_files.append((_fn, _raw))
        parsed_pairs: list = []
        sheets_map: dict = {}
        per_file: list[dict] = []
        for fname, raw in raw_files:
            src = _qc_detect_source(raw, fname)
            if src not in QC_SRC_SHEETS:
                per_file.append({"file": fname, "source": "无法识别", "rows": 0, "ng": 0})
                continue
            srows = _QC_PARSERS[src](raw)
            sheets_map[src] = raw
            parsed_pairs.append((src, srows))
            per_file.append({"file": fname, "source": QC_SOURCE_LABEL.get(src, src),
                             "rows": len(srows), "ng": sum(1 for r in srows if not r["is_pass"])})
        if not parsed_pairs or not any(n for _, n in parsed_pairs):
            raise HTTPException(
                status_code=400,
                detail="未解析到质检明细。请上传以下任一通报表原件：① 人工质检明细 ② FTTR质检工单汇总表 "
                       "③ 企宽装机质检统计 ④ 装、维改约质检通报 ⑤ 集团智能质检结果"
                       + (f"（已读取 {len(files)} 个文件：" + "、".join(f["file"] for f in per_file) + "）"
                          if per_file else ""))
        rows, report_date, _stat = _merge_qc_files(parsed_pairs, sheets_map)
        filename = "、".join(f["file"] for f in per_file if f["rows"]) or "质检通报"
        if not rows:
            raise HTTPException(status_code=400, detail="未解析到质检明细行，请检查文件是否为通报原件")
        ng = [r for r in rows if not r["is_pass"]]
        ty: dict = {}
        for r in rows:
            t = ty.setdefault(r["category"], {"scanned": 0, "ng": 0})
            t["scanned"] += 1
            if not r["is_pass"]:
                t["ng"] += 1
        # 去重后每个来源实际计入的行数（跨表重复的单归到专项通报，人工质检明细会少于原始行数）
        kept: dict = {}
        for r in rows:
            k = kept.setdefault(r["source"], {"rows": 0, "ng": 0})
            k["rows"] += 1
            if not r["is_pass"]:
                k["ng"] += 1
        for f in per_file:
            kk = kept.get(f["source"], {"rows": 0, "ng": 0})
            f["kept"] = kk["rows"]
            f["kept_ng"] = kk["ng"]
        extra = {"date": report_date, "report_date": report_date, "ng": len(ng),
                 "data_dates": "、".join(sorted({r["date"] for r in rows if r.get("date")})),
                 "sources": per_file, "source_count": len([f for f in per_file if f["rows"]]),
                 "penalty": round(sum(r["penalty"] for r in ng), 2),
                 "no_settle": round(sum(r["no_settle"] for r in ng), 2),
                 "districts": len({r["district"] for r in rows}),
                 "types": "、".join(f"{k} {v['scanned'] - v['ng']}/{v['scanned']}"
                                   for k, v in ty.items()),
                 "note": "五个通报表项已合并；请核对不合格件数、考核与不结算金额是否与各通报「分析汇总」合计行一致"}
        preview = rows[:150]
    elif metric == "broad_users":
        # 广义投诉重复清单：唯一键 work_order_no 增量更新，仅市级管理员可用
        filename, raw = await parse_upload_raw(data=files[0][1], filename=files[0][0],
                                              sheet_names=["广义重复清单"])
        rows, errors, warns, dup_rows = _normalize_broad_users(raw, files[0][0])
        if not rows:
            detail = "未解析到有效数据行"
            if errors:
                detail += "：" + "；".join("%s（%s）" % (e["error"], e["order_no"] or "无单号")
                                           for e in errors[:5])
            raise HTTPException(status_code=400, detail=detail)
        if len(rows) > 50000:
            raise HTTPException(status_code=400, detail="单次最多导入 5 万行，请拆分文件后上传")
        upd, new = _broad_counts(db, rows)
        extra = {
            "new": new, "update": upd, "invalid": len(errors),
            "errors": errors[:50], "warnings": warns[:30], "dup_rows": dup_rows,
            "months": "、".join(sorted({r["source_month"] for r in rows})) or "无",
            "warn": (f"有 {len(errors)} 行未通过校验，确认入库时将跳过" if errors else ""),
            "note": ("按工单编号增量更新：已存在只更新文件派生列，保留电话/CID/小区/责任人/匹配状态；"
                     "监利县→监利市；文件外旧行不删除"),
        }
        preview = rows[:100]
    else:
        filename, rows = await parse_upload(data=files[0][1], filename=files[0][0])
        if not rows:
            raise HTTPException(status_code=400, detail="解析不到任何数据行")
        preview = rows[:100]
    batch_id = str(uuid.uuid4())[:8].upper()
    db.add(SyncJob(batch_id=batch_id, metric=metric, filename=filename, rows=len(rows),
                   status="preview", preview_data=json.dumps(rows, ensure_ascii=False)))
    db.commit()
    return {"batch_id": batch_id, "filename": filename, "total_rows": len(rows),
            "metric": metric,
            "columns": col_override or (list(rows[0].keys()) if rows else []),
            "preview": preview, **extra}


@router.post("/upload-batch")
async def upload_batch(request: Request, user=Depends(require_admin), db: Session = Depends(get_db)):
    """批量上传：一次接收多个文件，按 sheet 名 + 文件名自动识别指标后分别解析预览（不入库）。

    字段名 files[]（multipart）。每个文件先识别指标（质检五项按专属 sheet 先行识别，
    其余按 sheet 签名 + 文件名关键字打分），同指标文件归入一组；qc 多项通报自动合并为一个批次。
    返回按 SYNC_MODULES 顺序排好的识别结果列表，每项含 batch_id（确认入库复用 /sync/confirm）。
    """
    ctype = request.headers.get("content-type", "")
    if "multipart/form-data" not in ctype:
        raise HTTPException(status_code=400, detail="请使用 multipart/form-data 上传（字段名 files[]）")
    form = await request.form()
    uploads = form.getlist("files")
    if not uploads:
        raise HTTPException(status_code=400, detail="未收到文件，请使用字段名 files 上传多个文件")
    loaded = []
    for up in uploads:
        if up is None:
            continue
        fn = up.filename or "upload"
        data = await up.read()
        if not data:
            continue
        loaded.append((fn, data))
    if not loaded:
        raise HTTPException(status_code=400, detail="文件均为空")

    # 识别 + 分组：qc 多项并入同组，其余每文件独立一组
    groups: dict = {}
    unrecognized = []
    for fn, data in loaded:
        try:
            sheets = _list_sheets(data, fn)
        except Exception:
            sheets = []
        m = _detect_metric(fn, sheets)
        if m:
            groups.setdefault(m, []).append((fn, data))
        else:
            unrecognized.append({"filename": fn, "sheets": sheets})

    results = []
    if "broad_users" in groups and get_role(user) != "super_admin":
        raise HTTPException(status_code=403, detail="广义用户清单导入仅限市级管理员")
    for m, items in groups.items():
        try:
            res = await _process_metric(m, items, db)
        except HTTPException as e:
            res = {"metric": m, "error": e.detail}
        except Exception as e:  # 解析/归一化异常：转为带 error 的结果，不中断其它文件
            res = {"metric": m, "error": str(e)[:300]}
        res["metric"] = m
        res["name"] = SYNC_MODULES[m]["name"]
        res["files"] = [i[0] for i in items]
        results.append(res)
    for u in unrecognized:
        results.append({"metric": None, "name": "未能自动识别", "files": [u["filename"]],
                        "error": "无法根据文件名 / sheet 自动识别指标，请在结果区手动指定",
                        "sheets": u["sheets"]})

    order = list(SYNC_MODULES.keys())
    results.sort(key=lambda r: order.index(r["metric"]) if r["metric"] in order else 999)
    return {"total": len(loaded), "recognized": len(loaded) - len(unrecognized), "results": results}


@router.post("/confirm/{batch_id}")
def confirm(batch_id: str, user=Depends(require_admin), db: Session = Depends(get_db)):
    """确认入库（铁律 3：入库后重算——本地版指标实时计算）"""
    job = db.query(SyncJob).filter(SyncJob.batch_id == batch_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="批次不存在")
    if job.status == "confirmed":
        raise HTTPException(status_code=400, detail="该批次已入库")
    if job.metric in ("broad_users", "cid_data") and get_role(user) != "super_admin":
        raise HTTPException(status_code=403, detail="该渠道仅限市级管理员")
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
    # v1.7.1 已下线：H5投诉工单上传入口（表保留，口径已改读投诉宽表）
    # elif job.metric == "h5_complaint":  ← 原入库逻辑已删除
    #     若要恢复：改回写 H5Complaint 表即可，但该表已不参与计算，
    #     投诉 H5 应发请走「投诉宽表」(complaint_orders)。
    elif job.metric == "install_query":
        # v1.6.5：综合查询是「实时状态快照」，写独立表，**整表替换**
        # 不再走 sync_wide_batch 覆盖 install_orders——那会把宽表 97 列冲成 20 列，
        # 且反过来被宽表把实时状态冲掉，两个源永远只有一个在。
        rows = [r for r in rows if r.get("order_id")]
        # 同批次同键去重：导出文件里同一工单可能有多段记录（宽表侧也有同样的坑），
        # 不去重直接 bulk_insert 会撞主键 → IntegrityError 500。
        # 保留「施工调度接收时间」最新的一条，它最接近当前状态。
        best_rt: dict = {}
        for r in rows:
            k = r["order_id"]
            if k not in best_rt or str(r.get("dispatch_time") or "") >= str(best_rt[k].get("dispatch_time") or ""):
                best_rt[k] = r
        rows = list(best_rt.values())
        from ..models import InstallRealtimeStatus as _IRT
        now_s = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        db.query(_IRT).delete(synchronize_session=False)
        payload = [{"order_id": r["order_id"], "month": r.get("month", ""),
                    "district": r.get("district", ""), "status": r.get("status", ""),
                    "dispatch_time": r.get("dispatch_time", ""),
                    "accept_time": r.get("accept_time", ""),
                    "handler": r.get("handler", ""),
                    "refund_reason": r.get("refund_reason", "")[:255],
                    "remark": r.get("remark", ""), "batch_id": job.batch_id,
                    "fetched_at": now_s} for r in rows]
        CHUNK = 500
        for i in range(0, len(payload), CHUNK):
            db.bulk_insert_mappings(_IRT, payload[i:i + CHUNK])
        n = len(payload)
        months = sorted({r.get("month") for r in payload if r.get("month")})
        # 顺手报一下「和宽表状态打架」的单数，让操作者当场知道这次校对有多大价值
        diff = _count_status_diff(db, payload)
        msg = (f"入库成功：实时状态 {n} 行（月份 {'、'.join(months) or '无'}），已整表替换；"
               f"与装机宽表状态不一致 {diff} 单——这些单在装维端将按「取最接近当前时间」裁决显示"
               f"（综合查询为实时源，通常以它为准），指标统计不受影响")
    elif job.metric == "install_wide":
        # 装机宽表为当月累计快照：批次比较入库（与邮件明细同机制，旧快照不覆盖新数据）
        rows = [r for r in rows if r.get("order_id") and r.get("month")]
        res = sync_wide_batch(db, InstallOrder, "order_id", rows)
        n = sum(_rep_count(s) for s in res["replaced"])
        msg = (f"入库成功：装机宽表替换 {('、'.join(res['replaced']) or '无月份')}；"
               + (f"；跳过较早快照：{('；'.join(res['skipped']))}" if res["skipped"] else "")
               + (f"；⚠ 已阻止缩水覆盖：{('；'.join(res['guarded']))}" if res.get("guarded") else "")
               + "，指标已实时生效")
    elif job.metric == "complaint_query":
        # v1.6.7：与装机综合查询同因同解 —— 写独立表，**整表替换**
        # 不再和 complaint_wide 抢 complaint_orders。后者被抢的后果更严重：
        # 综合查询导出没有「24小时是否及时/是否履约/归档满意度/是否高品质/全球通标识」，
        # 一旦它覆盖过去，超时统计恒 0、看板红标无数据、全球通卡片归零。
        rows = [r for r in rows if r.get("eoms_no")]
        best_rt: dict = {}
        for r in rows:
            k = r["eoms_no"]
            if k not in best_rt or str(r.get("dispatch_time") or "") >= str(best_rt[k].get("dispatch_time") or ""):
                best_rt[k] = r
        rows = list(best_rt.values())
        from ..models import ComplaintRealtimeStatus as _CRT
        now_s = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        db.query(_CRT).delete(synchronize_session=False)
        payload = [{"eoms_no": r["eoms_no"], "month": r.get("month", ""),
                    "district": r.get("district", ""), "status": r.get("status", ""),
                    "dispatch_time": r.get("dispatch_time", ""),
                    "accept_time": r.get("accept_time", ""),
                    "handler": r.get("handler", ""),
                    "category": (r.get("complaint_category") or "")[:255],
                    "remark": r.get("remark", ""), "batch_id": job.batch_id,
                    "fetched_at": now_s} for r in rows]
        CHUNK = 500
        for i in range(0, len(payload), CHUNK):
            db.bulk_insert_mappings(_CRT, payload[i:i + CHUNK])
        n = len(payload)
        months = sorted({r.get("month") for r in payload if r.get("month")})
        diff = _count_complaint_status_diff(db, payload)
        msg = (f"入库成功：投诉实时状态 {n} 行（月份 {'、'.join(months) or '无'}），已整表替换；"
               f"与投诉宽表状态不一致 {diff} 单——这些单在装维端将按「取最接近当前时间」裁决显示"
               f"（综合查询为实时源，通常以它为准），指标统计不受影响")
    elif job.metric == "complaint_wide":
        # 投诉宽表为当月累计快照：批次比较入库（旧快照不覆盖新数据）
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
    elif job.metric == "qc":
        # 质检五项通报：按 (通报批次日 report_date, 来源 source) 替换。
        # 为什么不用 date：五个表项的数据日本来就不齐（改约/集团AI 是前一日数据、其余是当日），
        # 且单传某一个文件时若按 date 整批删，会把同一天其它来源的行一并抹掉。
        # 同键（批次日+来源+单号+类型）的已推送/已整改状态沿用旧行 —— 否则补传一次就白推了。
        from ..models import QcRecord as _QR
        pairs = {(r.get("report_date") or r.get("date") or "", r.get("source") or "")
                 for r in rows}
        keep: dict = {}
        for rdate, src in pairs:
            q = db.query(_QR)
            q = q.filter(_QR.report_date == rdate) if rdate else q.filter(
                (_QR.report_date == "") | (_QR.report_date.is_(None)))
            q = q.filter(_QR.source == src) if src else q.filter(
                (_QR.source == "") | (_QR.source.is_(None)))
            for old in q.all():
                keep[(old.report_date, old.source, old.order_no, old.category)] = old
            q.delete(synchronize_session=False)
        for r in rows:
            rdate = r.get("report_date") or r.get("date") or ""
            old = keep.get((rdate, r.get("source") or "", r.get("order_no") or "",
                            r.get("category") or ""))
            db.add(_QR(
                report_date=rdate, source=r.get("source") or "",
                date=r.get("date") or "", month=r.get("month") or "",
                district=r.get("district") or "", grid=r.get("grid") or "",
                category=r.get("category") or "", raw_type=r.get("raw_type") or "",
                order_no=r.get("order_no") or "", qc_order_no=r.get("qc_order_no") or "",
                service_no=r.get("service_no") or "", biz_no=r.get("biz_no") or "",
                subject=r.get("subject") or "", handler=r.get("handler") or "",
                handler_no=r.get("handler_no") or "", qc_person=r.get("qc_person") or "",
                result=r.get("result") or "", is_pass=bool(r.get("is_pass")),
                ng_item=r.get("ng_item") or "", ng_detail=r.get("ng_detail") or "",
                penalty=float(r.get("penalty") or 0), no_settle=float(r.get("no_settle") or 0),
                dispatch_time=r.get("dispatch_time") or "",
                finish_time=r.get("finish_time") or "",
                push_to_id=old.push_to_id if old else 0,
                push_to_name=old.push_to_name if old else "",
                push_at=old.push_at if old else "",
                push_by=old.push_by if old else "",
                fix_status=old.fix_status if old else "",
                fix_at=old.fix_at if old else "",
                fix_note=old.fix_note if old else "",
            ))
            n += 1
        ng = [r for r in rows if not r.get("is_pass")]
        rdates = sorted({r.get("report_date") or r.get("date") or "" for r in rows}
                        - {""}) or ["无日期"]
        srcs = sorted({r.get("source") or "未标注" for r in rows})
        msg = (f"入库成功：质检通报 {len(rows)} 单（通报日 {'、'.join(rdates)}；来源 {'、'.join(srcs)}；"
               f"按 (批次日, 来源) 替换）；"
               f"不合格 {len(ng)} 件 / 考核 {abs(round(sum(r['penalty'] for r in ng), 2))} 元 / "
               f"不结算 {abs(round(sum(r['no_settle'] for r in ng), 2))} 元；"
               f"「质检规范」页已更新，已推送与整改状态已保留")
    elif job.metric == "broad_users":
        # 广义用户清单：按 work_order_no upsert（同一事务，失败整体回滚）；
        # 入库后共用公共口径做一次 CID + 责任田补齐，新增行也能立即带上 CID/责任人
        ensure_cid_tables(db)  # 幂等建表，先建好再开写事务
        try:
            upd, new = _apply_broad_users(db, rows)
            st = enrich_broad_rows(db, apply=True, commit=False, ensure=False)
            db.commit()
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=400, detail=f"导入失败，已回滚：{str(e)[:200]}")
        n = len(rows)
        months = sorted({r.get("source_month", "") for r in rows} - {""})
        msg = (f"入库成功：广义用户 {n} 行（新增 {new} / 更新 {upd}，月份 {'、'.join(months) or '无'}）；"
               f"CID 补齐 SN {st['cid_sn_filled']}（刷新 {st['cid_sn_refreshed']}）/ 小区 {st['community_filled']}"
               f"/ 电话 {st['phone_filled']}；责任人 {st['owner_filled']}（刷新 {st['owner_refreshed']}）"
               f"/ 网格 {st['grid_filled']}；「广义用户清单」列表与 6 张统计卡已实时更新")
    elif job.metric == "cid_data":
        # CID 快照：确认时才在**一个事务内**整表替换 cid_records + 全量重匹配广义用户清单
        stats = rows if isinstance(rows, dict) else {}
        t0 = time.time()
        ensure_cid_tables(db, commit=False)
        try:
            snapshot = replace_cid_snapshot(db, job.batch_id, version=stats.get("version", ""),
                                            source_file=job.filename)
            st = enrich_broad_rows(db, cid_label=stats.get("label", ""), apply=True,
                                   commit=False, ensure=False)
            db.commit()
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=400, detail=f"CID 入库失败，已回滚：{str(e)[:200]}")
        n = snapshot
        cost = round(time.time() - t0, 1)
        msg = (f"入库成功：CID 快照 {snapshot} 个账号（版本 {stats.get('version') or '未标'}，整表替换）；"
               f"广义用户回填 SN {st['cid_sn_filled']}（刷新 {st['cid_sn_refreshed']}）/ 小区 {st['community_filled']}"
               f"/ 电话 {st['phone_filled']}；责任人 {st['owner_filled']}（刷新 {st['owner_refreshed']}）"
               f"/ 网格 {st['grid_filled']}；冲突未覆盖 SN {st['conflict_sn']} / 小区 {st['conflict_community']}；"
               f"状态 已匹配 {st['status_matched']} / 部分 {st['status_partial']} / 待匹配 {st['status_pending']}；"
               f"耗时 {cost}s")
    job.status = "confirmed"
    job.confirmed_at = datetime.now()
    # 修复 P1-6：入库后不再需要原始预览 JSON（此前永久保留，单库因此膨胀到 250MB）。
    # 保留批次元信息供 /sync/history 展示，明细数据已在宽表落库。
    job.preview_data = "[]"
    db.add(AuditLog(username=user.username, action="数据入库", detail=f"{job.metric} {n} 行"))
    db.commit()
    if job.metric in ("extension", "completion_rate", "install_wide", "complaint_wide",
                      "install_query", "complaint_query", "ai_visit", "h5_survey", "qc",
                      "broad_users", "cid_data"):
        return {"msg": msg, "rows": n}
    return {"msg": f"入库成功 {n} 行（指标实时计算，无需重算）", "rows": n}


@router.get("/history")
def history(user=Depends(require_admin), db: Session = Depends(get_db)):
    return [{"id": j.id, "batch_id": j.batch_id, "metric": j.metric, "filename": j.filename,
             "rows": j.rows, "status": j.status,
             "created_at": j.created_at.strftime("%Y-%m-%d %H:%M:%S"),
             "confirmed_at": j.confirmed_at.strftime("%Y-%m-%d %H:%M:%S") if j.confirmed_at else ""}
            for j in db.query(SyncJob).order_by(SyncJob.id.desc()).limit(50)]
