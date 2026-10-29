# -*- coding: utf-8 -*-
"""装维人员花名册聚合 + 用户账号同步（v1.6.9）

为什么要有这个模块
------------------
「用户管理」原来只能一个个手工建账号，而装维人员的姓名/工号/网格散落在
六张业务表里，每张都只有一部分：

| 来源 | 有姓名 | 有工号 | 有网格 |
|---|---|---|---|
| ``h5_survey_records``（H5满意度回复详单） | ✓ | ✓ 5~6 位 | ✗ |
| ``report_h5_records``（省公司通报 H5 明细） | ✓ | ✓ 3~5 位（另一套） | ✗ |
| ``extension_members``（延伸服务花名册） | ✓ | ✗ | ✓ 权威 |
| ``h5_survey_staff``（H5满意度人员表） | ✓ | ✗ | ✓ 写法不统一 |
| ``install_orders`` / ``complaint_orders`` / ``alert_orders`` | ✓ handler | ✗ | ✓ 工单侧 |

单看任何一张都建不出完整账号，所以这里做多源合并。

两条必须记住的口径
------------------
1. **工号有两套体系，不能混用**。实测 9 个人两边不一致（杜健 641 vs 80079、
   陆名扬 5208 vs 104719）。``h5_survey_records`` 的号位数更长、跟真实工单走，
   定为主工号；``report_h5_records`` 的号记为备用（``staff_no_alt``）。
2. **网格以 ``extension_members`` 为准**。其它源的写法不统一（``峰口网格`` /
   ``洪湖峰口网格`` / ``洪湖峰口装维网格``），统一规范成 ``洪湖XX网格``。
"""
import re

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .models import (AlertOrder, ComplaintOrder, District, ExtensionMember, H5SurveyRecord,
                     H5SurveyStaff, InstallOrder, ReportH5Record, Role, User)

# 姓名里可能带区县前缀（``洪湖市刘中华``），统一剥掉
_PREFIXES = ("荆州开发区", "洪湖市", "荆州区", "沙市区", "公安县", "监利市",
             "江陵县", "石首市", "松滋市", "洪湖")

DEFAULT_DISTRICT = "洪湖市"
DEFAULT_PASSWORD = "Aa123456"

# 工号来源优先级：越靠前越权威
_NO_SOURCES = ("h5", "report")


def clean_name(raw: str) -> str:
    """``洪湖市刘中华`` → ``刘中华``；顺带去掉首尾空白与中间空格。"""
    v = (raw or "").strip().replace(" ", "").replace("\u3000", "")
    for p in _PREFIXES:
        if v.startswith(p) and len(v) > len(p):
            return v[len(p):].strip()
    return v


def norm_staff_no(raw) -> str:
    """``HB0716A06377`` → ``6377``；``6377`` → ``6377``；``80079.0`` → ``80079``。

    省公司通报的工号带 ``HB0716A`` 前缀并补零，详单里是裸数字，
    不归一会导致同一个人算出两个号。
    """
    v = str(raw or "").strip().upper()
    if not v or v in ("NAN", "NONE"):
        return ""
    m = re.search(r"(\d+)(?:\.0+)?$", v)
    if not m:
        return ""
    return m.group(1).lstrip("0") or "0"


def norm_grid(raw: str) -> str:
    """``峰口网格`` / ``洪湖峰口网格`` / ``洪湖峰口装维网格`` → ``洪湖峰口网格``。

    非网格归属（``洪湖市分公司``、``集客...班组``）原样归到 ``洪湖市分公司``。
    """
    v = (raw or "").strip()
    if not v:
        return ""
    if "分公司" in v:
        return "洪湖市分公司"
    if "班组" in v and "网格" not in v:
        return "洪湖市分公司"
    g = v if v.startswith("洪湖") else "洪湖" + v
    g = g.replace("装维网格", "网格")
    # 导出脏数据：洪湖市的单里混进「洪湖监利城区网格」这类跨区网格名，归到分公司，
    # 否则用户管理界面会显示一个不存在的网格。
    for other in ("监利", "公安", "石首", "松滋", "江陵", "沙市", "荆州", "开发区"):
        if other in g:
            return "洪湖市分公司"
    if not g.endswith("网格"):
        g += "网格"
    return g


def _norm_position(raw: str) -> str:
    v = (raw or "").strip()
    if "网格长" in v:
        return "网格长"
    if "管理员" in v:
        return "装维管理员"
    return "装维人员"


def _team_of(grid: str) -> str:
    """``洪湖峰口网格`` → ``峰口装维班组``（与人工建的「峰口镇装维班组」同风格）"""
    if not grid or grid == "洪湖市分公司":
        return ""
    g = grid[2:] if grid.startswith("洪湖") else grid
    if g.endswith("网格"):
        g = g[:-2]
    return g + "装维班组" if g else ""


def collect_staff(db: Session, district: str = DEFAULT_DISTRICT) -> list:
    """合并六源，返回按姓名去重的人员列表。

    返回项：``{name, staff_no, staff_no_alt, grid, grid_src, position, sources}``
    """
    people: dict = {}

    def touch(name, source):
        n = clean_name(name)
        if not n or len(n) < 2:
            return None
        p = people.setdefault(n, {"name": n, "staff_no": "", "staff_no_alt": "",
                                  "grid": "", "grid_src": "", "position": "",
                                  "sources": set()})
        p["sources"].add(source)
        return p

    # ① H5 详单（主工号来源）
    for staff, sid in db.query(H5SurveyRecord.staff, H5SurveyRecord.staff_id).filter(
            H5SurveyRecord.district == district).all():
        p = touch(staff, "h5详单")
        no = norm_staff_no(sid)
        if p and no and not p["staff_no"]:
            p["staff_no"] = no

    # ② 省公司通报 H5（备用工号，与详单不是同一套编号）
    for staff, sid in db.query(ReportH5Record.staff, ReportH5Record.staff_id).filter(
            ReportH5Record.district == district).all():
        p = touch(staff, "通报H5")
        no = norm_staff_no(sid)
        if p and no and not p["staff_no_alt"]:
            p["staff_no_alt"] = no

    # ③ 延伸服务花名册（网格权威）
    for name, grid, position in db.query(ExtensionMember.name, ExtensionMember.grid,
                                         ExtensionMember.position).filter(
            ExtensionMember.district == district).all():
        p = touch(name, "花名册")
        if p:
            g = norm_grid(grid)
            if g and not p["grid"]:
                p["grid"], p["grid_src"] = g, "花名册"
            if position and not p["position"]:
                p["position"] = _norm_position(position)

    # ④ H5 满意度人员表（网格次选）
    for name, grid in db.query(H5SurveyStaff.name, H5SurveyStaff.grid).filter(
            H5SurveyStaff.district == district).all():
        p = touch(name, "满意度人员")
        if p and not p["grid"]:
            g = norm_grid(grid)
            if g:
                p["grid"], p["grid_src"] = g, "满意度人员"

    # ⑤ 工单处理人（只在上面都没有时兜底网格）
    for model, label in ((InstallOrder, "装机工单"), (ComplaintOrder, "投诉工单"),
                         (AlertOrder, "预警工单")):
        try:
            rows = db.query(model.handler, model.grid).filter(
                model.district == district,
                model.handler != "").all()
        except Exception:
            continue
        for handler, grid in rows:
            p = touch(handler, label)
            if p and not p["grid"] and grid:
                p["grid"], p["grid_src"] = norm_grid(grid), label

    # 主号为空时用备用号顶上；主备相同时清掉备用（两套编号体系对同一人一致的情况很多）
    for p in people.values():
        if not p["staff_no"] and p["staff_no_alt"]:
            p["staff_no"], p["staff_no_alt"] = p["staff_no_alt"], ""
        if p["staff_no_alt"] and p["staff_no_alt"] == p["staff_no"]:
            p["staff_no_alt"] = ""
        if not p["position"]:
            p["position"] = "装维人员"
        if not p["grid"]:
            p["grid"], p["grid_src"] = "洪湖市分公司", "兜底"
        p["team"] = _team_of(p["grid"])
        p["sources"] = sorted(p["sources"])

    return sorted(people.values(), key=lambda x: (x["grid"], x["name"]))


def sync_users(db: Session, district: str = DEFAULT_DISTRICT,
               default_password: str = DEFAULT_PASSWORD, dry_run: bool = True,
               operator: str = "") -> dict:
    """把花名册同步成 ``users`` 记录（``role=field_worker``）。

    幂等规则（**不重置已有密码、不覆盖已有姓名**）：
      * ``username`` 已存在且姓名一致 → 只补空的 grid/team/position/staff_no
      * ``username`` 已存在但姓名不一致 → 跳过，记 ``conflict``
      * 无主工号 → ``username`` 用姓名；姓名也撞车 → 加序号
    """
    from .security import hash_password
    from .models import AuditLog

    staff = collect_staff(db, district)
    role = db.query(Role).filter(Role.code == "field_worker").first()
    dist = db.query(District).filter(District.name == district).first()
    if not role or not dist:
        return {"ok": False, "error": "缺少 field_worker 角色或 %s 区县" % district}

    all_users = db.query(User).all()
    exist = {u.username: u for u in all_users}
    # 已有账号可能用手机号/姓名建的（如柴达 = 15171201240），只看 username 会重复建号。
    # 这里再按姓名+区县兜一层，命中则补字段而不是新建。
    by_name = {}
    for u in all_users:
        nm = clean_name(u.real_name)
        if nm and (u.district_id in (None, dist.id)):
            by_name.setdefault(nm, u)
    created, updated, skipped = [], [], []

    for p in staff:
        uname = p["staff_no"] or p["name"]
        u = exist.get(uname) or by_name.get(p["name"])
        if u is None and uname == p["name"]:
            # 姓名也可能被别人的工号占了，换一个可用的
            base, i = p["name"], 2
            while uname in exist:
                uname = "%s%d" % (base, i)
                i += 1
        if u is not None and (u.real_name or "").strip() != p["name"]:
            skipped.append({"name": p["name"], "username": uname,
                            "reason": "账号 %s 已被「%s」占用" % (uname, u.real_name or u.username)})
            continue

        if u is None:
            item = {"username": uname, "real_name": p["name"], "staff_no": p["staff_no"],
                    "grid": p["grid"], "team": p["team"], "position": p["position"],
                    "district": district, "action": "create"}
            if not dry_run:
                u = User(username=uname, password_hash=hash_password(default_password),
                         real_name=p["name"], phone="", role_id=role.id, district_id=dist.id,
                         team=p["team"], grid=p["grid"], position=p["position"],
                         staff_no=p["staff_no"], is_active=True)
                db.add(u)
                exist[uname] = u
            created.append(item)
        else:
            patch = {}
            for f in ("grid", "team", "position", "staff_no"):
                if p.get(f) and not (getattr(u, f, "") or "").strip():
                    patch[f] = p[f]
            if u.district_id is None:
                patch["district_id"] = dist.id
            if patch:
                if not dry_run:
                    for k, v in patch.items():
                        setattr(u, k, v)
                updated.append({"username": u.username, "real_name": p["name"], "patch": patch})

    if not dry_run:
        db.add(AuditLog(username=operator or "system", action="同步装维人员账号",
                        detail="%s 新建 %d / 更新 %d / 跳过 %d"
                               % (district, len(created), len(updated), len(skipped))))
        db.commit()

    return {"ok": True, "dry_run": dry_run, "district": district,
            "total": len(staff), "created": created, "updated": updated, "skipped": skipped,
            "summary": {"total": len(staff), "create": len(created),
                        "update": len(updated), "skip": len(skipped)},
            "default_password": "" if dry_run else default_password}
