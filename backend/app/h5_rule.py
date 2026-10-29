# -*- coding: utf-8 -*-
"""H5 测评短信触发口径（唯一真源，供 alerts.py / metrics.py 共用）

用户确认的口径（2026-10-29）：
    触发H5  =  ① 工单号以 HB-058 开头
               ② 投诉分类（首级） = 家庭市场
    两者同时满足 → 触发H5

⚠️ 为什么必须真读分类，不能只看单号前缀：
   实测 HB-058 单号下的投诉分类首级并不单一：
       家庭业务 2800 条 / 家庭市场 1729 条 / 政企市场 18 条
   而 alert_orders.order_type 清一色「家庭宽带」，与分类首级毫无对应关系。
   用 order_type 或单号前缀硬推都会错判。

⚠️ 分类的来源与时效：
   预警表 alert_orders 本身**没有**分类字段，只能拿单号去
   ① report_complaint_orders.flow_no（重要通报，T-1 数据）
   ② complaint_orders.eoms_no（投诉宽表，当日上传时刻为止）
   反查。当天新受理的单两边都可能还没覆盖 → 分类未知。
   这种情况返回 UNKNOWN（待确认），**不硬判不触发**，次日通报更新后自动补全。
"""

# 投诉 H5 只覆盖这一类工单
COMPLAINT_PREFIX = "HB-058"
# 触发所需的分类首级（精确值，不含「家庭业务」）
TARGET_ROOT = "家庭市场"
# 分类多级串分隔符
SEP = "\u2192"   # →

ON = "on"            # 触发H5
NO = "no"            # 不触发
UNKNOWN = "unknown"  # 分类未关联到，待确认

LABEL = {ON: "触发H5", NO: "不触发", UNKNOWN: "待确认"}


def _root(category) -> str:
    """取分类首级：'家庭市场→通信连接→…' → '家庭市场'"""
    s = (category or "").strip()
    if not s:
        return ""
    return s.split(SEP)[0].strip()


def judge_complaint_h5(order_no, category) -> dict:
    """判定一张投诉单是否会触发 H5 测评短信。

    order_no : 工单号，如 HB-058-261029-14190
    category : 投诉分类多级串（可为空 → 返回待确认）

    返回 {"trigger_h5": "on"|"no"|"unknown", "h5_label": str, "h5_why": str}
    """
    no = (order_no or "").strip()
    root = _root(category)

    # 条件①：单号必须是 HB-058
    if not no.startswith(COMPLAINT_PREFIX):
        return {
            "trigger_h5": NO,
            "h5_label": LABEL[NO],
            "h5_why": "单号非 %s 开头，不在投诉H5覆盖范围" % COMPLAINT_PREFIX,
        }

    # 条件②：分类必须是家庭市场；分类缺失时不硬判
    if not root:
        return {
            "trigger_h5": UNKNOWN,
            "h5_label": LABEL[UNKNOWN],
            "h5_why": "单号为 %s，但投诉分类暂未关联到（通报为T-1数据，当天新单次日补全）" % COMPLAINT_PREFIX,
        }
    if root == TARGET_ROOT:
        return {
            "trigger_h5": ON,
            "h5_label": LABEL[ON],
            "h5_why": "单号 %s + 投诉分类「%s」，满足触发条件" % (COMPLAINT_PREFIX, root),
        }
    return {
        "trigger_h5": NO,
        "h5_label": LABEL[NO],
        "h5_why": "投诉分类首级为「%s」，非「%s」，不触发H5" % (root, TARGET_ROOT),
    }
