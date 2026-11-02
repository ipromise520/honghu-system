# -*- coding: utf-8 -*-
"""责任田对应责任人（v1.9.0）

背景
----
洪湖的装维责任是「责任田 → 责任人」的点对点绑定：一块责任田（小区/片区/微网格）
只归一个装维师傅，考核、派单、竣工率都按这个口径算。这张对应表原先维护在
金山文档在线表「责任田调整【模版】」里，每次调整都要人肉翻表去核对，
Web8011 的工单/质检/竣工率各页面反倒拿不到这层关系。

本模块做三件事：
  1. 把在线表**手动**同步进库（按钮在页面顶部，不做定时 —— 责任田调整是低频
     人工动作，自动同步容易把别人改到一半的表灌进来）；
  2. 提供按网格 / 班组 / 责任人 / 关键字的多维汇总与明细查询；
  3. 对外暴露 match()，供工单、质检、竣工率等模块按
     「工号 → 姓名 → 责任田名」三级优先级回填责任人。

数据获取的三条路（按优先级）
--------------------------
  A. settings.territory_source_url + settings.territory_cookie 直接 HTTP 拉取
     （金山文档公开链接 / 内网共享 xlsx 直链都行），解析后入库；
  B. 页面「上传表格」直接传 xlsx，走同一套解析（链接要登录时的兜底）；
  C. 两者都不行时给出可执行的提示，不吞错、不写脏数据。

金山文档注意：kdocs 的 /l/xxxx 短链默认要求登录（会 302 到 account.kdocs.cn）。
若同步返回 NEED_AUTH，说明这份表没开「互联网获得链接的人可查看」，
或需要在系统里补一条 WPS Cookie。
"""
import io
import json
import re
import urllib.request
import urllib.error
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, require_admin
from ..models import TerritoryResponsible, TerritorySyncLog, Setting, User, AuditLog

router = APIRouter(prefix="/territory", tags=["territory"])

DEFAULT_URL = "https://www.kdocs.cn/l/cbwHYXktINGj"

# 列名别名：先精确等于，再包含匹配；顺序即优先级（「责任人」不能被「备份责任人」抢走）
# 在线表「责任田调整【模版】」实际列：地市/区县/市场网格编码/市场网格名称/小区编码/小区名称/责任人/用户数
#   → 责任田 = 小区名称，标准网格 = 市场网格名称
COLUMN_ALIAS = {
    "district":       ["区县", "所属区县"],
    "city":           ["地市"],                     # 仅用于区县缺失时兜底，不入库
    "territory":      ["实际责任田", "责任田名称", "责任田", "小区名称", "微网格", "责任区域", "区域"],
    "territory_type": ["责任田类型", "区域类型", "类型"],
    "territory_code": ["小区编码", "责任田编码"],
    "community":      ["所属小区", "片区"],          # 不再吃「小区」，「小区编码」会被误匹配
    "grid":           ["市场网格名称", "标准网格", "所属网格", "网格名称", "网格"],
    "grid_code":      ["市场网格编码", "网格编码"],
    "team":           ["所属班组", "班组名称", "班组", "团队", "小组"],
    "person_name":    ["责任田责任人", "对应责任人", "责任人", "装机人员清单", "装机人员",
                       "负责人", "装维人员", "姓名"],
    # 「装机人员清单」另一路：姓名-手机-工号 拼装串，用来补责任人缺失的电话/工号
    "staff_list":     ["装机人员清单", "装机人员", "装维人员清单"],
    "person_no":      ["人员工号", "员工编号", "工号", "员工号"],
    "person_phone":   ["联系电话", "联系方式", "手机号", "手机", "电话"],
    "complaint_person": ["投诉人员清单", "投诉人员", "投诉责任人"],
    "compliance":     ["是否达标", "达标情况", "达标状态"],
    "backup_name":    ["备份责任人", "协同责任人", "副责任人", "B角"],
    "user_count":     ["小区用户数", "用户数", "户数", "用户数量", "在网用户数"],
    "remark":         ["备注", "说明"],
}
HEADER_HINT = ("责任田", "责任人", "网格", "班组", "工号", "小区")

# 辅助表：不在这些表里，靠表名识别
AUX_HR_SHEET = "工号对虚拟号"          # 人员档案（姓名 / 工号ID / 电话）
AUX_CREW_SHEET = "装维+集客人员信息"    # 无表头花名册，只补电话
AUX_COMP_SHEET = "智慧系统责任田"       # 投诉人员清单 / 实际责任人

# 「装机人员清单 / 投诉人员清单」是拼装串：姓名-手机-工号，多人用顿号或逗号分隔
STAFF_RE = re.compile(r"^\s*([^\d\-－]+?)\s*[-－]\s*(\d{7,11})\s*[-－]\s*(\d{2,8})\s*$")

# 在线表的清单串常带区县前缀（「洪湖市刘中华-13554561720-74096」），入库前去掉
CITY_PREFIX_RE = re.compile(r"^([\u4e00-\u9fa5]{2,4}[市县区])([\u4e00-\u9fa5]{2,4})$")

# 合法区县名：XX市 / XX县 / XX区。写成「荆州」这种地市名的一律按本表多数区县归位
DISTRICT_RE = re.compile(r"^[\u4e00-\u9fa5]{2,6}[市县区]$")

# 只认这几张表当责任田对应表；其余（尤其「数据1」是全省 13 万行原始清单）一律不走
SHEET_PRIORITY = ("实际责任田", "责任田对应责任人", "责任田对应网格", "智慧系统责任田",
                  "责任田对虚拟号", "班组&责任人场景")
MAX_SHEET_ROWS = 20000          # 超过这个行数说明不是「责任田→责任人」表


def _clean_name(name: str) -> str:
    """'洪湖市刘中华' → '刘中华'（区县前缀会污染按姓名匹配）"""
    n = (name or "").strip()
    m = CITY_PREFIX_RE.match(n)
    return m.group(2) if m else n


def _split_staff(s: str) -> tuple[str, str, str]:
    """'彭宋华-15771113381-86299' → ('彭宋华', '15771113381', '86299')"""
    s = (s or "").strip()
    if not s:
        return "", "", ""
    m = STAFF_RE.match(s)
    if m:
        return _clean_name(m.group(1)), m.group(2), m.group(3)
    multi = re.split(r"[、,，;；/]+", s)
    if len(multi) > 1:
        return _split_staff(multi[0])          # 多人共管：取第一责任人
    parts = [p.strip() for p in re.split(r"[-－]", s) if p.strip()]
    name = parts[0] if parts else ""
    tel = next((p for p in parts[1:] if p.isdigit() and len(p) == 11), "")
    no = next((p for p in parts[1:] if p.isdigit() and len(p) <= 8), "")
    return name, tel, no


# 在线表公式没算出来 / 查不到人时写进去的占位文字，当成「没有人」处理
JUNK_STAFF = {"未找到", "无", "空", "-", "—", "/", "Nan", "None", "#NAME?", "#N/A", "#REF!", "#VALUE!"}


def _is_junk_staff(name: str) -> bool:
    n = (name or "").strip()
    return (not n) or n in JUNK_STAFF or n.startswith("#")


def _split_multi_staff(s: str) -> list[tuple[str, str, str]]:
    """「洪湖市陈笛-15171201226-101495、蔡明-13972126106-80274」→ 多人三元组列表

    在线表「装机人员清单」经常是一条小区对应多名装机人员，逗号/顿号/分号分隔。
    原来只取第一个人，导致统计时把「装机人」的电话工号写成了「责任人」的。
    """
    out: list[tuple[str, str, str]] = []
    for part in re.split(r"[、,，;；/\n]+", (s or "").strip()):
        part = part.strip()
        if not part:
            continue
        nm, tel, no = _split_staff(part)
        if _is_junk_staff(nm):
            continue
        out.append((nm, tel, no))
    return out


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# 查询串清洗：从 Excel / 微信复制小区名时，常带前导 Tab、全角空格、不换行空格，
# LIKE '%\t 桃源新村%' 会一条都匹配不到（用户以为查不到数据，其实是空白字符）
_WS_RE = re.compile(r"[\s\u3000\u00a0]+")


def _clean_q(s: str) -> str:
    return _WS_RE.sub("", (s or ""))


def _batch_id() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S")


def _get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.query(Setting).filter(Setting.key == key).first()
    return (row.value if row and row.value else default) or default


def _set_setting(db: Session, key: str, value: str):
    row = db.query(Setting).filter(Setting.key == key).first()
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))


# ---------------------------------------------------------------- 表格解析

def _pick_col(header: list[str], aliases: list[str]) -> int:
    for alias in aliases:                       # 1) 精确等于
        for i, h in enumerate(header):
            if h == alias:
                return i
    for alias in aliases:                       # 2) 包含（排除「备份/协同」类前缀抢词）
        for i, h in enumerate(header):
            if alias in h and not any(k in h for k in ("备份", "协同", "副")):
                return i
    return -1


def _find_header(rows: list[tuple]) -> tuple[int, list[str]]:
    """在线表常带标题行/说明行，取前 20 行里命中关键词最多的一行当表头"""
    best, best_score, best_idx = [], -1, -1
    for idx, row in enumerate(rows[:20]):
        cells = [str(c).strip() if c is not None else "" for c in row]
        score = sum(1 for c in cells if any(k in c for k in HEADER_HINT))
        if score > best_score:
            best, best_score, best_idx = cells, score, idx
    if best_score < 2:
        raise HTTPException(status_code=400, detail="表格里找不到表头行：至少需要包含「责任田 / 责任人」等列")
    return best_idx, best


def _sheet_records(ws) -> list[dict]:
    """按首行表头把工作表读成 dict 列表（同名列取第一次出现的）"""
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    idx: dict[str, int] = {}
    for i, h in enumerate(rows[0]):
        key = ("" if h is None else str(h).strip())
        if key and key not in idx:
            idx[key] = i
    out = []
    for r in rows[1:]:
        if not any(c not in (None, "") for c in r):
            continue
        out.append({k: ("" if i >= len(r) or r[i] is None else str(r[i]).strip())
                    for k, i in idx.items()})
    return out


def _aux_maps(wb) -> tuple[dict, dict]:
    """人员档案 + 投诉责任人，都来自在线表的辅助 Sheet

    主表「实际责任田」只有 11 列，没有人员电话、也没有投诉人。同工作簿里另外两张表
    才是权威来源：
      · 工号对虚拟号     → 人员姓名 / 工号ID / 电话（65 人，含张军 82337/18571371001）
      · 装维+集客人员信息 → 无表头花名册，补「工号对虚拟号」里缺的人（如陆名扬）
      · 智慧系统责任田    → 投诉人员清单（按小区编码关联）
    """
    hr: dict[str, tuple[str, str]] = {}

    if AUX_HR_SHEET in wb.sheetnames:
        for r in _sheet_records(wb[AUX_HR_SHEET]):
            nm = _clean_name(r.get("人员姓名") or "")
            no = (r.get("工号ID") or "").strip()
            ph = (r.get("工号") or "").strip()        # 这张表的「工号」列其实存的是手机号
            if not nm or not (ph.isdigit() and len(ph) == 11):
                continue
            hr[nm] = (no if no.isdigit() and len(no) <= 8 else "", ph)

    if AUX_CREW_SHEET in wb.sheetnames:
        # 这张表没有规整表头（前两行是空行 + 标题），列序固定：
        # [0]'' [1]区县 [2]网格 [3]装维工号 [4]姓名 [5]电话
        for r in wb[AUX_CREW_SHEET].iter_rows(values_only=True):
            v = ["" if c is None else str(c).strip() for c in r]
            if len(v) < 6:
                continue
            nm, ph = _clean_name(v[4]), v[5]
            if not nm or nm in ("姓名", "单位") or len(nm) < 2:
                continue
            if not (ph.isdigit() and len(ph) == 11):
                continue
            hr.setdefault(nm, ("", ph))               # 已有档案的不覆盖

    comp: dict[str, str] = {}
    if AUX_COMP_SHEET in wb.sheetnames:
        for r in _sheet_records(wb[AUX_COMP_SHEET]):
            code = (r.get("小区编码") or "").strip()
            v = (r.get("投诉人员清单") or "").strip()
            if code and v and not _is_junk_staff(v):
                comp[code] = v
    return hr, comp


def _apply_roster(out: list[dict], hr: dict, comp: dict) -> None:
    """给每条记录挂上 ①责任人工号/电话 ②投诉人员原串"""
    from collections import Counter

    # 档案里查不到的人，做一次「一字之差」别名纠正：在线表把「陆明扬」写成「陆名扬」这种。
    # 只在本人没有档案、且该别名在其名下小区里稳定出现（≥3 行）时才认，避免误把别人的号挂上去。
    crew: dict[str, Counter] = {}          # 责任人 -> 其名下小区装机人的出现次数
    crew_best: dict[str, tuple[str, str]] = {}   # 装机人 -> (工号, 电话) 众数
    pairs: dict[str, Counter] = {}
    for r in out:
        p = (r.get("person_name") or "").strip()
        lst = r.get("staff_list") or ""
        if not lst:
            continue
        for nm, tel, sno in _split_multi_staff(lst)[:1]:
            if p:
                crew.setdefault(p, Counter())[nm] += 1
            if sno or tel:
                pairs.setdefault(nm, Counter())[(sno, tel)] += 1
    for nm, cnt in pairs.items():
        crew_best[nm] = cnt.most_common(1)[0][0]

    alias: dict[str, str] = {}
    for p, cnt in crew.items():
        if p in hr:
            # 档案有电话但缺工号（花名册没登记工号ID）→ 用本人装机清单里的工号补
            if not hr[p][0] and p in crew_best:
                hr[p] = (crew_best[p][0], hr[p][1] or crew_best[p][1])
            continue
        cands = [(name, c) for name, c in cnt.items()
                 if name != p and len(name) == len(p) and name in hr
                 and sum(a != b for a, b in zip(name, p)) == 1]
        if not cands:
            continue
        cand, n = max(cands, key=lambda x: x[1])
        if n >= 3:                                   # 至少 3 个小区这么写，才算稳定别名
            hr[p] = hr[cand]
            if cand in crew_best and (not hr[p][0] or not hr[p][1]):
                hr[p] = (hr[p][0] or crew_best[cand][0], hr[p][1] or crew_best[cand][1])
            alias[p] = cand

    for r in out:
        p = (r.get("person_name") or "").strip()
        r["hr_no"], r["hr_phone"] = hr.get(p, ("", ""))
        r["hr_alias"] = alias.get(p, "")
        code = (r.get("territory_code") or "").strip()
        if code and not (r.get("complaint_person") or "").strip():
            r["complaint_raw"] = comp.get(code, "")


def parse_workbook(data: bytes) -> list[dict]:
    """把 xlsx 字节流解析成标准记录列表

    在线表里同时存在「数据1（全省 13 万行原始清单）」和「实际责任田（洪湖 1500+ 行）」
    等多张表，按名字优先级挑表，并按行数排除原始清单 —— 否则会把全省所有地市的
    小区都灌进洪湖的责任田库。
    """
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise HTTPException(status_code=500, detail="服务端缺少 openpyxl，无法解析表格")
    try:
        wb = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"表格解析失败（不是有效的 xlsx？）：{e}")

    names = list(wb.sheetnames)
    ordered = [n for n in SHEET_PRIORITY if n in names] + [n for n in names if n not in SHEET_PRIORITY]

    out: list[dict] = []
    for nm in ordered:
        ws = wb[nm]
        try:
            if (ws.max_row or 0) > MAX_SHEET_ROWS:
                continue                                  # 全省原始清单，跳过
        except Exception:
            pass
        rows = list(ws.iter_rows(values_only=True))
        if len(rows) < 2:
            continue
        try:
            hidx, header = _find_header(rows)
        except HTTPException:
            continue
        cols = {k: _pick_col(header, v) for k, v in COLUMN_ALIAS.items()}
        if cols["territory"] < 0 and cols["person_name"] < 0:
            continue
        for n, row in enumerate(rows[hidx + 1:], start=hidx + 2):
            vals = ["" if c is None else str(c).strip() for c in row]
            if not any(vals):
                continue
            rec = {k: (vals[i] if 0 <= i < len(vals) else "") for k, i in cols.items()}
            # 整行都空 / 只有序号行，跳过
            if not rec.get("territory") and not rec.get("person_name"):
                continue
            try:
                rec["user_count"] = int(float(rec.get("user_count") or 0))
            except (TypeError, ValueError):
                rec["user_count"] = 0
            rec["seq"] = n
            # 「是否达标」列偶尔被右移的清单串污染（含姓名/工号），非达标语义一律置空，
            # 否则「不达标责任田」卡片会把脏行算进去
            cp = (rec.get("compliance") or "").strip()
            rec["compliance"] = cp if "达标" in cp else ""
            # 区县缺失时用「地市」兜底：在线表里偶尔整行右移一格，
            # 把「洪湖市」写到地市列，不兜底会漏掉几十条
            if not rec.get("district"):
                c = (rec.get("city") or "").strip()
                if c.endswith("市") or c.endswith("县") or c.endswith("区"):
                    rec["district"] = c
            rec["raw_json"] = json.dumps(dict(zip([h for h in header if h], vals)), ensure_ascii=False)[:2000]
            out.append(rec)
        if out:
            # ① 区县归一：只认「XX市/县/区」，空值或写成「荆州」这类地市名的，
            #    用本表占多数的区县兜底（「实际责任田」整表就是洪湖市，缺一行不能丢）
            cnt: dict[str, int] = {}
            for r in out:
                d = (r.get("district") or "").strip()
                if DISTRICT_RE.match(d):
                    cnt[d] = cnt.get(d, 0) + 1
            main = max(cnt, key=cnt.get) if cnt else ""
            if main:
                for r in out:
                    if not DISTRICT_RE.match((r.get("district") or "").strip()):
                        r["district"] = main
            # ② 网格回填：在线表 1/4 的行没填「市场网格名称」。只在「该责任人全表只归属
            #    一个网格」时回填 —— 一人多网格的不猜，避免把小区挂错班组。
            cnt_by_person: dict[str, dict[str, int]] = {}
            for r in out:
                g = (r.get("grid") or "").strip()
                p = (r.get("person_name") or "").strip()
                if g and p:
                    bag = cnt_by_person.setdefault(p, {})
                    bag[g] = bag.get(g, 0) + 1
            single = {p: max(c, key=c.get) for p, c in cnt_by_person.items() if len(c) == 1}
            for r in out:
                if not (r.get("grid") or "").strip():
                    r["grid"] = single.get((r.get("person_name") or "").strip(), "")
            # ③ 补人员档案 / 投诉责任人（来自同工作簿的辅助表）
            try:
                hr, comp = _aux_maps(wb)
                _apply_roster(out, hr, comp)
            except Exception:
                for r in out:
                    r.setdefault("hr_no", "")
                    r.setdefault("hr_phone", "")
            break                                         # 只取第一个能识别的工作表
    if not out:
        raise HTTPException(status_code=400, detail="表格里没有解析到有效数据行")
    return out


# ---------------------------------------------------------------- 在线表拉取

def _http_get(url: str, cookie: str = "", timeout: int = 30,
              referer: str = "https://www.kdocs.cn/") -> tuple[bytes, str, str]:
    """返回 (body, content_type, final_url)。不跟随到登录页 —— 那说明没权限。"""
    req = urllib.request.Request(url, headers={
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
        "Accept": "*/*",
        "Referer": referer,
    })
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(), (resp.headers.get("Content-Type") or ""), resp.geturl()
    except urllib.error.HTTPError as e:
        return (e.read() or b""), (e.headers.get("Content-Type") or ""), url
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"在线表拉取失败（网络不可达）：{e}")


def _download_by_fileid(fileid: str, cookie: str, referer: str) -> bytes:
    """用登录态 Cookie 换取对象存储直链并下载（kdocs /api/v3/office/file/{id}/download）

    返回的 JSON 形如 {"download_url": "https://ksc-bj.ag.kdocs.cn/..."}，
    直链本身不再校验 Cookie，但有时效（分钟级），所以拿到就立刻下。
    """
    body, _, _ = _http_get(f"https://www.kdocs.cn/api/v3/office/file/{fileid}/download",
                           cookie, referer=referer)
    try:
        j = json.loads(body)
    except Exception:
        j = {}
    du = (j.get("download_url") or j.get("url") or "") if isinstance(j, dict) else ""
    if not du:
        return b""
    data, _, _ = _http_get(du, "", timeout=120, referer=referer)   # 直链带签名，不要 Cookie
    return data if data[:2] == b"PK" else b""


def fetch_online(url: str, cookie: str = "") -> bytes:
    """拉在线表并返回 xlsx 字节流；拿不到就抛带指引的 HTTPException"""
    body, ctype, final = _http_get(url, cookie)

    if "account.kdocs.cn" in final or "passport" in final:
        raise HTTPException(status_code=400, detail=(
            "金山文档需要登录授权：该链接未开放匿名访问，且系统里没有可用的 WPS Cookie。\n"
            "解决方式（任选其一）：\n"
            "① 在金山文档里把「分享」设为「互联网获得链接的人可查看」，再点同步；\n"
            "② 在页面「同步设置」里填入 WPS 登录 Cookie（浏览器 F12 → Network → 任一 kdocs.cn 请求 → Cookie）；\n"
            "③ 直接把表格导出为 xlsx 后用「上传表格」导入。"))

    # 情况一：直接就是 xlsx（内网直链 / 导出接口）
    if body[:2] == b"PK":
        return body

    # 情况二：kdocs 分享页 → 抓 fileid 再走导出
    html = body.decode("utf-8", errors="ignore")
    m = (re.search(r'"fileid"\s*:\s*"?(\d{6,})', html)
         or re.search(r'fileid=(\d{6,})', html)
         or re.search(r'"id"\s*:\s*"?(1\d{8,})', html))
    if m:
        fileid = m.group(1)
        # 2a. 有 Cookie：走官方 download 接口拿直链（在线表 12MB，直接下 xlsx）
        if cookie:
            data = _download_by_fileid(fileid, cookie, url)
            if data:
                return data
            raise HTTPException(status_code=400, detail=(
                f"已用 Cookie 定位到金山文档（fileid={fileid}），但下载接口没有返回表格文件。\n"
                "通常是 Cookie 过期（wps_sid 有时效）或无该文档的下载权限。\n"
                "请重新复制一次 Cookie 填到「同步设置」，或在分享设置里允许下载，或用「上传表格」导入。"))
        # 2b. 无 Cookie：试匿名导出
        for tpl in ("https://www.kdocs.cn/api/v3/office/file/{f}/download?isblocks=false&type=download",
                    "https://www.kdocs.cn/api/v3/office/file/{f}/export?type=xlsx",
                    "https://www.kdocs.cn/api/v3/office/file/{f}/download"):
            b2, _, _ = _http_get(tpl.format(f=fileid), cookie, referer=url)
            if b2[:2] == b"PK":
                return b2
        raise HTTPException(status_code=400, detail=(
            f"已定位到金山文档 fileid={fileid}，但导出被拒绝（未登录 / 没有下载权限）。"
            "请在「同步设置」里填入 WPS Cookie，或用「上传表格」导入。"))

    if "kdocs" in final:
        raise HTTPException(status_code=400, detail=(
            "金山文档返回的是网页而非表格文件，说明未开放导出。\n"
            "请在分享设置里设为「互联网获得链接的人可查看」并允许下载，或用「上传表格」导入。"))

    raise HTTPException(status_code=400, detail="该链接返回的不是可解析的表格文件（需 .xlsx 或金山文档公开链接）")


# ---------------------------------------------------------------- 入库

def _norm_key(r: dict) -> str:
    """幂等键：**小区编码优先**

    一个小区编码就是一块责任田，编码才是唯一身份；「小区名称+责任人」会被人为改名、
    同码拆行（沈庙村居民小区同一编码两行不同责任人）打散，导致重复入库/对不上。
    编码缺失时才退回「小区名称||责任人」。
    """
    code = (r.get("territory_code") or "").strip()
    if code:
        return f"#{code}"
    ter = (r.get("territory") or "").strip()
    who = (r.get("person_name") or "").strip() or (r.get("person_no") or "").strip()
    return f"{ter}||{who}"


def _build_roster(records: list[dict], db: Session) -> dict:
    """责任人档案：姓名 → {phone, no}

    只按「姓名相同」学习，绝不把装机人员的号借给责任人。来源两处，优先级从高到低：
      1. 全表「装机人员清单」里同名的人（同一人多次出现取众数）
      2. 系统 users 表（real_name 唯一时）
    """
    from collections import Counter, defaultdict

    ph_cnt: dict = defaultdict(Counter)
    no_cnt: dict = defaultdict(Counter)
    for r in records:
        for nm, tel, no in _split_multi_staff(r.get("staff_list") or ""):
            if tel:
                ph_cnt[nm][tel] += 1
            if no:
                no_cnt[nm][no] += 1

    roster: dict = {}
    for nm in set(ph_cnt) | set(no_cnt):
        roster[nm] = {
            "phone": ph_cnt[nm].most_common(1)[0][0] if ph_cnt[nm] else "",
            "no": no_cnt[nm].most_common(1)[0][0] if no_cnt[nm] else "",
        }
    for u in db.query(User).filter(User.real_name != "").all():
        slot = roster.setdefault(u.real_name, {"phone": "", "no": ""})
        slot["phone"] = slot["phone"] or (u.phone or "")
        slot["no"] = slot["no"] or (u.staff_no or "")
    return roster


def _upsert(db: Session, records: list[dict], batch: str) -> dict:
    old = db.query(TerritoryResponsible).all()
    old_map = {_norm_key({"territory": o.territory, "territory_code": o.territory_code,
                          "person_no": o.person_no, "person_name": o.person_name}): o
               for o in old}

    roster = _build_roster(records, db)

    def _fill(r):
        """统一格式化一条记录：责任人 与 装机人员 分开落库"""
        grid = (r.get("grid") or "").strip()
        grid = re.sub(r"^洪湖(?=.+网格)", "", grid)            # 「洪湖曹市网格」→「曹市网格」
        team = (r.get("team") or "").strip() or grid          # 洪湖口径：市场网格 ≈ 班组
        raw_person = (r.get("person_name") or "").strip()
        # 「责任人」列偶尔自带 姓名-手机-工号
        if "-" in raw_person or "－" in raw_person:
            nm, tel, sno = _split_staff(raw_person)
        else:
            nm, tel, sno = _clean_name(raw_person), "", ""
        if _is_junk_staff(nm):
            nm = ""

        # —— 责任人本人的电话/工号：人员档案 > 同名装机清单 > 系统用户表，不拿别人的顶替 ——
        book = roster.get(nm) or {}
        no = ((r.get("person_no") or "").strip() or (r.get("hr_no") or "")
              or book.get("no", "") or sno)
        ph = ((r.get("person_phone") or "").strip() or (r.get("hr_phone") or "")
              or book.get("phone", "") or tel)

        # —— 投诉责任人 / 装机人员：口径统一为「该小区编码对应的责任人本人」——
        # 源表「装机人员清单」与「投诉人员清单」两列 1549/1550 行一字不差，是同一份抄写；
        # 且其中 400 行（26%）填的是别的师傅（施工班组的人），挂到考核口径上就张冠李戴
        # （例：峰口 BUSINESS_COMMUNITY-141452979019 责任人王刚，清单写尹述香）。
        # 因此这两列一律取责任人本人，源表原串仍完整保留在 raw_json，可随时回溯核对。
        crew = _split_multi_staff(r.get("staff_list") or "")
        own = {nm, (r.get("hr_alias") or "").strip()}          # 含「陆名扬/陆明扬」一字之差别名
        own_pick = next((x for x in crew if x[0] and x[0] in own), None)
        if own_pick:                                           # 清单里写的就是本人 → 缺号时补档
            _, tel2, no2 = own_pick
            no = no or no2
            ph = ph or tel2

        inst_name = inst_no = inst_phone = ""
        inst_src = ""
        cperson = ""
        if nm:
            inst_name, inst_no, inst_phone = nm, no, ph
            cperson = nm
            if not crew:
                inst_src = "owner"                             # 源表写「未找到 / #NAME?」
            elif own_pick:
                inst_src = "owner_same"                        # 源表装机清单本来就是本人
            else:
                inst_src = "owner_forced"                      # 源表写的是别人，按口径覆盖
        elif crew:                                             # 责任人缺失（极少数）才退回清单第一人
            inst_name, inst_phone, inst_no = crew[0]
        return dict(
            district=(r.get("district") or "").strip(),
            territory=(r.get("territory") or "").strip(),
            territory_type=(r.get("territory_type") or "").strip(),
            territory_code=(r.get("territory_code") or "").strip(),
            community=(r.get("community") or "").strip(),
            grid=grid, grid_code=(r.get("grid_code") or "").strip(),
            team=team,
            person_name=nm, person_no=no, person_phone=ph,
            installer_name=inst_name, installer_no=inst_no, installer_phone=inst_phone,
            installer_source=inst_src,
            complaint_person=cperson,
            compliance=(r.get("compliance") or "").strip(),
            backup_name=(r.get("backup_name") or "").strip(),
            user_count=int(r.get("user_count") or 0),
            remark=(r.get("remark") or "").strip(),
            raw_json=r.get("raw_json", ""),
        )

    ins = upd = 0
    seen = set()
    keep: set = set()                   # 本轮真正保留下来的行（按主键/ID），用于收口
    for r in records:
        f = _fill(r)                    # 先补全（工号/电话/网格），键必须用补全后的值
        key = _norm_key(f)              # 否则每次同步键都对不上，会整表重插
        if key in seen:                 # 同码重复行（在线表重复登记），保留首条
            continue
        seen.add(key)
        obj = old_map.get(key)
        if obj:
            # 在线表本身缺这列（如葛凯 4 张人员表都没有号码），人工在系统里补的值不能被
            # 下一次同步抹掉：新值为空时沿用库里已有值。源表给了值则以源表为准。
            for k in ("person_no", "person_phone", "installer_no", "installer_phone",
                      "grid", "team", "remark", "backup_name"):
                if not (f.get(k) or "").strip() and (getattr(obj, k, "") or "").strip():
                    f[k] = getattr(obj, k)
            obj.seq = r.get("seq") or 0
            for k, v in f.items():
                setattr(obj, k, v)
            obj.batch = batch
            obj.active = True
            keep.add(obj.id)
            upd += 1
        else:
            new = TerritoryResponsible(seq=r.get("seq") or 0, batch=batch, active=True, **f)
            db.add(new)
            db.flush()                  # 取到自增 ID，否则同码旧行会漏停用
            keep.add(new.id)
            ins += 1

    # 本轮没保留的旧记录 —— 不物理删除，留痕可回溯（active=False 后续查询默认过滤）
    # 按 ID 判断而不是按幂等键：同一个小区编码在库里有两条（源表重复登记）时，
    # 只有一条被更新，另一条必须显式停用，否则卡片/明细会重复计数
    deact = 0
    for o in old:
        if o.id not in keep and o.active:
            o.active = False
            deact += 1
    db.commit()
    return {"inserted": ins, "updated": upd, "deactivated": deact, "total": len(records)}


def _write_log(db: Session, *, batch, source, status, total, ins, upd, deact, msg, operator):
    db.add(TerritorySyncLog(batch=batch, source=source[:255], status=status, total=total,
                            inserted=ins, updated=upd, deactivated=deact,
                            message=msg[:500], operator=operator))
    db.commit()


# ---------------------------------------------------------------- 接口

@router.get("/summary")
def summary(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """顶部卡片 + 上次同步时间"""
    base = db.query(TerritoryResponsible).filter(TerritoryResponsible.active.is_(True))
    total = base.count()
    territories = db.query(func.count(func.distinct(TerritoryResponsible.territory))).filter(
        TerritoryResponsible.active.is_(True)).scalar() or 0
    persons = db.query(func.count(func.distinct(TerritoryResponsible.person_name))).filter(
        TerritoryResponsible.active.is_(True), TerritoryResponsible.person_name != "").scalar() or 0
    grids = db.query(func.count(func.distinct(TerritoryResponsible.grid))).filter(
        TerritoryResponsible.active.is_(True), TerritoryResponsible.grid != "").scalar() or 0
    teams = db.query(func.count(func.distinct(TerritoryResponsible.team))).filter(
        TerritoryResponsible.active.is_(True), TerritoryResponsible.team != "").scalar() or 0
    # 在线表「市场网格名称」有整列空着的情况，页面要提示去补表，否则网格/班组统计少一截
    grid_empty = db.query(func.count(TerritoryResponsible.id)).filter(
        TerritoryResponsible.active.is_(True),
        or_(TerritoryResponsible.grid == "", TerritoryResponsible.grid.is_(None))).scalar() or 0

    # 无责任田的责任人（用来提示漏配）
    unbound = db.query(func.count(func.distinct(TerritoryResponsible.person_name))).filter(
        TerritoryResponsible.active.is_(True),
        TerritoryResponsible.person_name != "",
        TerritoryResponsible.territory == "").scalar() or 0

    users_sum = db.query(func.coalesce(func.sum(TerritoryResponsible.user_count), 0)).filter(
        TerritoryResponsible.active.is_(True)).scalar() or 0
    districts = db.query(func.count(func.distinct(TerritoryResponsible.district))).filter(
        TerritoryResponsible.active.is_(True), TerritoryResponsible.district != "").scalar() or 0
    # 在线表带「是否达标」列时，直接给出不达标责任田数（短板分析/督办用）
    non_ok = db.query(func.count(TerritoryResponsible.id)).filter(
        TerritoryResponsible.active.is_(True),
        TerritoryResponsible.compliance.like("%不达标%")).scalar() or 0
    has_compliance = db.query(func.count(TerritoryResponsible.id)).filter(
        TerritoryResponsible.active.is_(True),
        TerritoryResponsible.compliance != "").scalar() or 0

    last = (db.query(TerritorySyncLog).order_by(TerritorySyncLog.id.desc()).first())
    return {
        "total": total,
        "territories": territories,
        "persons": persons,
        "grids": grids,
        "teams": teams,
        "districts": districts,
        "users_sum": int(users_sum),
        "non_ok": non_ok,
        "has_compliance": has_compliance,
        "grid_empty": grid_empty,
        "unbound_persons": unbound,
        "last_sync": {
            "time": last.created_at.strftime("%Y-%m-%d %H:%M:%S") if last else "",
            "status": last.status if last else "",
            "message": last.message if last else "",
            "total": last.total if last else 0,
            "operator": last.operator if last else "",
            "source": last.source if last else "",
        },
        "source_url": _get_setting(db, "territory_source_url", DEFAULT_URL),
        "has_cookie": bool(_get_setting(db, "territory_cookie", "")),
    }


@router.get("/grids")
def grid_options(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """筛选下拉：网格 / 班组 / 责任人"""
    rows = (db.query(TerritoryResponsible.grid, TerritoryResponsible.team,
                     func.count(TerritoryResponsible.id))
            .filter(TerritoryResponsible.active.is_(True))
            .group_by(TerritoryResponsible.grid, TerritoryResponsible.team).all())
    grids, teams = set(), set()
    for g, t, _ in rows:
        if g:
            grids.add(g)
        if t:
            teams.add(t)
    persons = [p for (p,) in db.query(TerritoryResponsible.person_name).filter(
        TerritoryResponsible.active.is_(True), TerritoryResponsible.person_name != ""
    ).distinct().all()]
    return {"grids": sorted(grids), "teams": sorted(teams), "persons": sorted(persons)}


@router.get("/list")
def list_items(keyword: str = "", grid: str = "", team: str = "", person: str = "",
               page: int = 1, page_size: int = 50,
               db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    keyword, grid, team, person = (_clean_q(keyword), _clean_q(grid),
                                   _clean_q(team), _clean_q(person))
    q = db.query(TerritoryResponsible).filter(TerritoryResponsible.active.is_(True))
    if keyword:
        like = f"%{keyword}%"
        q = q.filter(or_(TerritoryResponsible.territory.like(like),
                         TerritoryResponsible.person_name.like(like),
                         TerritoryResponsible.person_no.like(like),
                         TerritoryResponsible.community.like(like)))
    if grid:
        q = q.filter(TerritoryResponsible.grid == grid)
    if team:
        q = q.filter(TerritoryResponsible.team == team)
    if person:
        q = q.filter(TerritoryResponsible.person_name == person)
    total = q.count()
    rows = (q.order_by(TerritoryResponsible.seq, TerritoryResponsible.id)
            .offset((page - 1) * page_size).limit(page_size).all())
    return {
        "total": total,
        "items": [{
            "id": r.id, "seq": r.seq, "district": r.district, "territory": r.territory,
            "territory_type": r.territory_type, "territory_code": r.territory_code,
            "community": r.community, "grid": r.grid, "grid_code": r.grid_code,
            "team": r.team, "person_name": r.person_name,
            "person_no": r.person_no, "person_phone": r.person_phone,
            "installer_name": r.installer_name, "installer_no": r.installer_no,
            "installer_phone": r.installer_phone, "installer_source": r.installer_source,
            "complaint_person": r.complaint_person, "compliance": r.compliance,
            "backup_name": r.backup_name, "user_count": r.user_count, "remark": r.remark,
            "updated_at": r.updated_at.strftime("%Y-%m-%d %H:%M") if r.updated_at else "",
        } for r in rows],
    }


@router.get("/by-person")
def by_person(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """按责任人汇总：一个人带几块责任田、覆盖哪些网格班组（派单/考核看这张）

    不按 team 分组 —— 同一个人名下有的小区挂了网格、有的没挂（在线表 393 行网格为空），
    按 team 分会把一个人拆成两三行，看不出真实负载。
    """
    rows = (db.query(TerritoryResponsible.person_name, TerritoryResponsible.person_no,
                     TerritoryResponsible.person_phone,
                     func.group_concat(TerritoryResponsible.team, "|"),
                     func.count(TerritoryResponsible.id),
                     func.count(func.distinct(TerritoryResponsible.grid)),
                     func.coalesce(func.sum(TerritoryResponsible.user_count), 0))
            .filter(TerritoryResponsible.active.is_(True),
                    TerritoryResponsible.person_name != "")
            .group_by(TerritoryResponsible.person_name, TerritoryResponsible.person_no,
                      TerritoryResponsible.person_phone)
            .order_by(func.count(TerritoryResponsible.id).desc()).all())
    out = []
    for p, no, ph, teams, cnt, gc, us in rows:
        tset = [t for t in dict.fromkeys((teams or "").split("|")) if t]
        out.append({"person_name": p, "person_no": no, "phone": ph,
                    "team": "、".join(tset), "teams": tset,
                    "territories": cnt, "grids": gc, "users_sum": int(us or 0)})
    return out


@router.get("/by-grid")
def by_grid(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """按网格/班组汇总：覆盖度与人均负载"""
    rows = (db.query(TerritoryResponsible.grid, TerritoryResponsible.team,
                     func.count(TerritoryResponsible.id),
                     func.count(func.distinct(TerritoryResponsible.person_name)),
                     func.count(func.distinct(TerritoryResponsible.territory)),
                     func.coalesce(func.sum(TerritoryResponsible.user_count), 0))
            .filter(TerritoryResponsible.active.is_(True))
            .group_by(TerritoryResponsible.grid, TerritoryResponsible.team)
            .order_by(func.count(TerritoryResponsible.id).desc()).all())
    return [{"grid": g or "未填网格", "team": t or "未填班组", "count": c,
             "persons": p, "territories": tt, "users_sum": int(us or 0)}
            for g, t, c, p, tt, us in rows]


@router.post("/sync")
def sync(db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """手动同步在线表：拉取 → 解析 → 入库 → 记录时间"""
    url = _get_setting(db, "territory_source_url", DEFAULT_URL)
    cookie = _get_setting(db, "territory_cookie", "")
    batch = _batch_id()
    operator = user.real_name or user.username
    try:
        data = fetch_online(url, cookie)
        records = parse_workbook(data)
        stat = _upsert(db, records, batch)
    except HTTPException as e:
        _write_log(db, batch=batch, source=url, status="failed", total=0, ins=0, upd=0,
                   deact=0, msg=str(e.detail), operator=operator)
        raise
    _write_log(db, batch=batch, source=url, status="success", total=stat["total"],
               ins=stat["inserted"], upd=stat["updated"], deact=stat["deactivated"],
               msg=f"同步成功：新增 {stat['inserted']}、更新 {stat['updated']}、下线 {stat['deactivated']}",
               operator=operator)
    db.add(AuditLog(username=user.username, action="territory_sync",
                    detail=f"batch={batch} total={stat['total']}"))
    db.commit()
    return {"ok": True, "batch": batch, **stat, "sync_time": _now(), "source": url}


@router.post("/upload")
async def upload(file: UploadFile = File(...), db: Session = Depends(get_db),
                 user: User = Depends(require_admin)):
    """上传 xlsx 导入（在线表要登录时的兜底通道）"""
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="空文件")
    batch = _batch_id()
    operator = user.real_name or user.username
    records = parse_workbook(data)
    stat = _upsert(db, records, batch)
    _write_log(db, batch=batch, source=f"上传文件：{file.filename}", status="success",
               total=stat["total"], ins=stat["inserted"], upd=stat["updated"],
               deact=stat["deactivated"],
               msg=f"导入成功：{file.filename}", operator=operator)
    return {"ok": True, "batch": batch, **stat, "sync_time": _now(), "source": file.filename}


@router.post("/settings")
def save_settings(payload: dict, db: Session = Depends(get_db),
                  user: User = Depends(require_admin)):
    if "source_url" in payload:
        _set_setting(db, "territory_source_url", (payload.get("source_url") or "").strip())
    if "cookie" in payload:
        _set_setting(db, "territory_cookie", (payload.get("cookie") or "").strip())
    db.commit()
    return {"ok": True}


@router.put("/{item_id}")
def update_item(item_id: int, payload: dict, db: Session = Depends(get_db),
                user: User = Depends(require_admin)):
    obj = db.query(TerritoryResponsible).filter(TerritoryResponsible.id == item_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="记录不存在")
    for f in ("district", "territory", "territory_type", "territory_code", "community",
              "grid", "grid_code", "team", "person_name", "person_no", "person_phone",
              "installer_name", "installer_no", "installer_phone",
              "complaint_person", "compliance", "backup_name", "remark"):
        if f in payload:
            setattr(obj, f, (payload.get(f) or "").strip())
    # 手工改过装机人 → 来源记为 manual，下次同步会被在线表覆盖回去（符合预期）
    if any(k in payload for k in ("installer_name", "installer_no", "installer_phone")):
        obj.installer_source = "manual"
    if "user_count" in payload:
        try:
            obj.user_count = int(payload.get("user_count") or 0)
        except (TypeError, ValueError):
            obj.user_count = 0
    db.commit()
    return {"ok": True}


@router.get("/logs")
def logs(limit: int = 20, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.query(TerritorySyncLog).order_by(TerritorySyncLog.id.desc()).limit(limit).all()
    return [{"id": r.id, "batch": r.batch, "source": r.source, "status": r.status,
             "total": r.total, "inserted": r.inserted, "updated": r.updated,
             "deactivated": r.deactivated, "message": r.message, "operator": r.operator,
             "time": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else ""}
            for r in rows]


@router.get("/match")
def match(name: str = "", staff_no: str = "", territory: str = "",
          db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """供其他模块回填责任人：工号 → 姓名 → 责任田名，三级优先级"""
    name, staff_no, territory = _clean_q(name), _clean_q(staff_no), _clean_q(territory)
    if staff_no:
        r = db.query(TerritoryResponsible).filter(
            TerritoryResponsible.active.is_(True),
            TerritoryResponsible.person_no == staff_no).first()
        if r:
            return {"matched": True, "by": "工号", "person_name": r.person_name,
                    "team": r.team, "grid": r.grid, "territory": r.territory}
    if name:
        r = db.query(TerritoryResponsible).filter(
            TerritoryResponsible.active.is_(True),
            TerritoryResponsible.person_name == name).first()
        if r:
            return {"matched": True, "by": "姓名", "person_name": r.person_name,
                    "team": r.team, "grid": r.grid, "territory": r.territory}
    if territory:
        r = db.query(TerritoryResponsible).filter(
            TerritoryResponsible.active.is_(True),
            TerritoryResponsible.territory.like(f"%{territory}%")).first()
        if r:
            return {"matched": True, "by": "责任田", "person_name": r.person_name,
                    "team": r.team, "grid": r.grid, "territory": r.territory}
    return {"matched": False}
