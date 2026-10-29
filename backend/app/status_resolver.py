# -*- coding: utf-8 -*-
"""工单状态统一裁决（v1.6.5）

问题
----
同一张工单会同时出现在三类数据源里，状态各自不同：

| 源 | 表 | 更新节奏 | 状态写法 |
|---|---|---|---|
| 装机综合查询（实时导出） | 不落库，手工比对 | **实时** | 已撤单 / 已施工 / 处理中 |
| 宽表查询（手工上传） | ``install_orders`` | 一般晚 2 小时，慢可更久 | 处理中 / 待地址确认 / 已退单… |
| 重要通报（邮件附件） | ``report_wide_orders`` | 每小时一轮 | 处理中 / 已撤单… |

宽表里那张「处理中」的单，实时系统里可能已经撤单了。装维端照宽表显示，
人员就会一直看到一张早就作废的单挂在待办里，铃铛还天天报警。

用户口径（2026-10-29 确认）
--------------------------
> 同一工单在两张表状态不一致时，**以各表中最晚的工单时间为准，取最接近当前时间的**；
> 时间不明确或无法用时间比对时，**以「导出信息」（装机综合查询）为准**，因为它实时。

因此裁决顺序是：

1. 各源先各自算出「该状态生效于什么时刻」（``ts``）；
2. ``ts`` 都有值 → 取最晚的那个源的结论（这就是「取最接近当前时间」）；
3. ``ts`` 都缺失或相同 → 按 ``TRUST`` 优先级，综合查询 > 宽表 > 通报；
4. 只有单一源 → 直接用它的结论，并标注 ``src``。

生效时刻怎么取（关键）
----------------------
- **综合查询**：状态没有独立时间戳，只有 ``施工调度接收时间``（单子进入当前阶段的时刻），
  可作生效下界。它的价值在于：宽表/通报根本没有时间，所以时间维度上综合查询天然占优。
- **通报**：``受理时间`` 是进网时刻，不是状态时刻，**不计入**（见 ``report_cand`` 注释）。
- **宽表**：状态列的更新时刻**表里根本没有**，任何「宽表状态是最新的」都只是假设。
  所以宽表只在对端也没时间时才靠优先级说话。
  例外：宽表带 ``是否退单=是`` 或非空 ``一级退单原因`` → 视为**在退单流程中**，
  这一信息本身即证据，可独立升格为「退单中」。

为什么要区分「已撤单」和「退单中」
----------------------------------
实测 ``install_orders`` 里 535 条在途单带退单原因，其中 528 条仍是 ``处理中``。
它们不是僵尸单，而是**退单正在审批、随时可能复活**的单。
一律判成「已撤单」会让人员漏盯，一律不处理又会让作废单挂在待办里。
故单列 ``退单中`` 状态：不计在途待办，但保留可见与提示。
"""
import datetime as _dt

# ---- 状态族 --------------------------------------------------------------
DONE = ("已施工", "已归档", "已完工", "已回单", "已竣工")
CANCELLED = ("已撤单", "已取消", "已作废")
REFUNDED = ("已退单",)
# 「退单中」是合成状态，源表没有这个值
REFUNDING = "退单中"
# 需要从「在途待办」里剔除的全部终态/伪终态
NOT_PENDING = DONE + CANCELLED + REFUNDED + (REFUNDING,)

# ---- 源可信度（仅在时间无法分胜负时使用，数字越小越可信）------------------
# 「宽表」排在「通报」之前，不是笔误：
# 通报只有 status + 1 个粗粒度环节，是**快照**；宽表有 8 个终态列（是否退单、
# 一二三级退单原因、退单人员…），信息量是它的几倍。同一条单上「宽表说已施工、
# 通报说处理中」时，宽表的已施工是带退单原因/回单时间等旁证的，通报只是没刷新。
# 实测这类单 188 条，若让通报胜出会全部被降级回「在途」。
# 而「宽表说处理中、综合查询说已撤单」那种冲突，综合查询靠着时间（接收时间）仍然赢。
TRUST = {"realtime": 0, "wide": 1, "report": 2}
SRC_NAME = {"realtime": "装机综合查询", "report": "重要通报", "wide": "宽表查询"}

_TFMT = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")


def parse_ts(s):
    """宽松解析各种时间字符串，失败返回 None（不抛异常，调用方按「时间不明」处理）"""
    t = str(s or "").strip()
    if not t:
        return None
    t = t.replace("/", "-")
    if "." in t:                       # 去掉毫秒尾巴
        t = t.split(".")[0]
    for f in _TFMT:
        try:
            return _dt.datetime.strptime(t, f)
        except Exception:
            continue
    for f in _TFMT:
        try:
            return _dt.datetime.strptime(t.strip(), f)
        except Exception:
            continue
    return None


def classify(status: str) -> str:
    """把原始状态串归到 done / cancelled / refunded / pending 四族"""
    s = (status or "").strip()
    if s in DONE:
        return "done"
    if s in CANCELLED:
        return "cancelled"
    if s in REFUNDED:
        return "refunded"
    return "pending"


def is_pending(status: str) -> bool:
    """是否应计入「在途待办」

    注意「退单中」在 ``classify`` 里属 pending 族（单还活着、可能复活），
    但它是伪终态，**不计入待办**，否则人员会被一张正在审批退单的单牵着走。
    两个判断维度故意不同，别合并。
    """
    s = (status or "").strip()
    if not s or s in NOT_PENDING:
        return False
    return True


def _cand(src: str, status: str, ts, extra: str = "") -> dict:
    return {"src": src, "status": (status or "").strip(), "ts": ts, "extra": extra}


def resolve(cands):
    """多源状态裁决，返回

    ``{"status", "src", "src_name", "ts", "conflict", "reason"}``

    ``conflict`` 为 True 表示各源结论确实不一致——这种情况值得暴露给前端，
    让人员知道「这条单存在口径冲突」，而不是让系统默默替他选一个。
    """
    cands = [c for c in cands if c and c["status"]]
    if not cands:
        return {"status": "", "src": "", "src_name": "", "ts": None,
                "conflict": False, "reason": "各源均无状态"}

    # 结论是否一致（只比族，不比字面，「已撤单」和「已取消」算同一结论）
    fams = {classify(c["status"]) for c in cands}
    conflict = len(fams) > 1

    # ① 时间能分胜负 → 取最晚
    timed = [c for c in cands if c["ts"]]
    if len(timed) >= 2:
        timed.sort(key=lambda c: (c["ts"], -TRUST.get(c["src"], 9)), reverse=True)
        if timed[0]["ts"] > timed[1]["ts"]:
            win = timed[0]
            return {**win, "src_name": SRC_NAME.get(win["src"], win["src"]),
                    "conflict": conflict,
                    "reason": "取工时最新的「%s」（%s）" % (
                        SRC_NAME.get(win["src"], win["src"]),
                        win["ts"].strftime("%m-%d %H:%M"))}

    # ② 时间分不出（都缺 / 相同）→ 按可信度
    #    「退单中」的旁证只用来抬升**宽表自己**，不能越过综合查询：
    #    实测目标单宽表是「处理中 + 前台原因」，综合查询已撤单，此时必须听综合查询的。
    #    宽表与通报角力时，退单旁证才让它胜出。
    cands.sort(key=lambda c: TRUST.get(c["src"], 9))
    win = cands[0]
    name = SRC_NAME.get(win["src"], win["src"])
    # 文案要能指导人：说清「为什么听它的」，而不是「按可信度」这种开发黑话
    if win["src"] == "realtime":
        why = "以实时查询为准（宽表约晚2小时、通报每小时刷新）"
    elif win["src"] == "wide":
        why = "实时查询未覆盖此单，沿用宽表状态"
    else:
        why = "按通报状态显示（宽表与实时查询均无此单）"
    return {**win, "src_name": name, "conflict": conflict, "reason": why}


def realtime_cand(status, dispatch_time=None) -> dict:
    """「导出信息 / 装机综合查询」候选。

    状态**没有**独立时间戳。只有 ``施工调度接收时间`` —— 那是这张单进入当前处理阶段的时刻。
    它能当「本状态生效下界」用：单子都还没派下来，状态不可能是现在的样子。
    所以综合查询拿到时间、通报/宽表拿不到时，综合查询胜出；反之不成立。
    """
    return _cand("realtime", status, parse_ts(dispatch_time))


def report_cand(status, accept_time=None) -> dict:
    """「重要通报」候选。

    ⚠️ ``accept_time`` 是**受理时间**，即单子进网的时刻，不是状态变更时刻。
    拿它当状态时间会在实测中出错：库里 188 条「宽表已施工 / 通报处理中」的单，
    通报的 accept_time 更晚，按「取最晚」会全部被降级成「处理中」——
    **施工完的单又变成在途**，这正是 v1.3.9 那类口径倒退。

    故受理时间**不计入状态时间**（``ts=None``），通报只能靠 ``TRUST`` 优先级说话，
    而它排在综合查询之后。通报若将来带上「状态更新时间」列，改这里一处即可。
    """
    return _cand("report", status, None)


def wide_cand(status, is_refund="", refund_reason="") -> dict:
    """「宽表查询」候选。

    ``ts`` 恒为 None——宽表里没有状态更新时间，这是刻意的：
    给个假时间会让裁决看起来比实际更可靠。
    """
    s = (status or "").strip()
    if is_pending(s) and (str(is_refund or "").strip() == "是" or str(refund_reason or "").strip()):
        return _cand("wide", REFUNDING, None, "宽表已进入退单流程")
    return _cand("wide", s, None)


def decorate(row: dict, st: dict) -> dict:
    """把裁决结果铺到明细行上，供前端直接渲染。

    字段约定（前端别改名）：
    - ``status``     裁决后的状态（覆盖原值，列表「状态」列显示它）
    - ``status_note`` 一句话说明依据，前端 hover / 小字展示
    - ``status_src`` 来源名（装机综合查询 / 重要通报 / 宽表查询）
    - ``status_conflict`` True 时前端加冲突角标
    """
    row["status"] = st["status"]
    row["status_src"] = st.get("src_name", "")
    row["status_conflict"] = bool(st.get("conflict"))
    note = st.get("reason", "")
    if st.get("extra"):
        note = (note + "；" if note else "") + st["extra"]
    row["status_note"] = note
    return row
