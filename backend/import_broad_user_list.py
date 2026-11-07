# -*- coding: utf-8 -*-
"""一次性导入：广义用户清单_导入数据.json → SQLite broad_user_list 表（幂等）"""
import json
import sqlite3

DB = "/vol2/Trae/backend/honghu_new.db"
SRC = "/vol2/Trae/backend/广义用户清单_导入数据.json"

MAP = [
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

DDL = """
CREATE TABLE IF NOT EXISTS broad_user_list (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  seq INTEGER,
  source_month TEXT,
  work_order_no TEXT UNIQUE,
  report_date TEXT,
  setup_date TEXT,
  district TEXT,
  internet_account TEXT,
  phone TEXT,
  cid_sn TEXT,
  community TEXT,
  owner TEXT,
  grid TEXT,
  is_repeat TEXT,
  repeat_times TEXT,
  complaint_lv7 TEXT,
  complaint_detail TEXT,
  phone_source TEXT,
  owner_source TEXT,
  match_status TEXT,
  unmatch_reason TEXT,
  source_file TEXT,
  source_row TEXT
)
"""

with open(SRC, encoding="utf-8") as f:
    rows = json.load(f)

con = sqlite3.connect(DB, timeout=60)
con.execute("PRAGMA busy_timeout=60000")
con.execute(DDL)
for stmt in [
    "CREATE INDEX IF NOT EXISTS ix_bul_month ON broad_user_list(source_month)",
    "CREATE INDEX IF NOT EXISTS ix_bul_district ON broad_user_list(district)",
    "CREATE INDEX IF NOT EXISTS ix_bul_repeat ON broad_user_list(is_repeat)",
]:
    con.execute(stmt)
con.commit()

cols = [c for c, _ in MAP]
sql = "INSERT OR REPLACE INTO broad_user_list (%s) VALUES (%s)" % (
    ",".join(cols), ",".join(["?"] * len(cols)))
buf = []
for r in rows:
    buf.append(tuple(r.get(k, "") for _, k in MAP))
    if len(buf) >= 2000:
        con.executemany(sql, buf)
        con.commit()
        buf = []
if buf:
    con.executemany(sql, buf)
    con.commit()

total = con.execute("SELECT COUNT(*) FROM broad_user_list").fetchone()[0]
uniq = con.execute("SELECT COUNT(DISTINCT work_order_no) FROM broad_user_list").fetchone()[0]
print("导入完成：总行数 %d，唯一工单编号 %d" % (total, uniq))
for m, n in con.execute(
        "SELECT source_month, COUNT(*) FROM broad_user_list GROUP BY source_month ORDER BY MIN(seq)"):
    print("  %-5s %d" % (m, n))
con.close()
