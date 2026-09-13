#!/usr/bin/env python3
"""
tool/reorder_table_columns.py — 把 `tjc_hymn` 表的物理列顺序对齐 crawler_core/db.py

背景:
  v4 之后的新字段都以 `ALTER TABLE ... ADD COLUMN` 追加（v5 `staff_png_path`/`numbered_png_path`、
  v6 `chorus`、v7 `api_raw`），SQLite 只能把新列挂到末尾 → 库内**物理列顺序**与
  `_create_table_v4()` 的建表顺序不一致（DB 查看器里 `chorus`/`api_raw` 落在最后，
  容易误判成"列缺失 / 库没更新"）。

做法（唯一权威 = 代码，不维护人工列清单）:
  1. 解析 `crawler_core/db.py` 里 `_create_table_v4` 的建表 SQL → 期望列顺序 + DDL 原文
  2. `PRAGMA table_info` 读库内现状：一致则直接退出（**幂等**，可反复执行）
  3. 事务内重建表（SQLite 没有"调整列序"的语句，只能重建）：
       CREATE 临时表（用代码 DDL 原文，保证默认值/约束逐字一致）
       → INSERT INTO 新表(列) SELECT 列 FROM 旧表（显式列名，与顺序无关地搬数据）
       → DROP 旧表 → RENAME 新表为 tjc_hymn
     保留：`id` 值、`UNIQUE(hymn_number)`、`sqlite_sequence`（AUTOINCREMENT 计数不回退）
  4. 重建后自检：列顺序 / DDL 与代码一致 / 行数 / **逐行逐列数据** / 分类表未动 / 完整性

用法:
  python3 tool/reorder_table_columns.py --dry-run   # 只报告现状与目标差异，不改库
  python3 tool/reorder_table_columns.py             # 实际重排（先备份到 /tmp，重建后自检）

恢复: `git checkout -- tjc_hymn.db`（DB 已纳入 git），或使用脚本打印的备份文件
"""
import argparse
import os
import re
import shutil
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from crawler_core.config import DB_PATH  # 需先补 sys.path 才能导入项目包

DB_PY = os.path.join(ROOT, "crawler_core", "db.py")
TABLE = "tjc_hymn"
TMP_TABLE = "tjc_hymn_reorder_tmp"

# 建表 SQL 中非列定义的行（表级约束）
_CONSTRAINT_HEADS = ("PRIMARY", "UNIQUE", "FOREIGN", "CHECK", "CONSTRAINT")

_DDL_RE = re.compile(
    r"def _create_table_v4\(c\):.*?c\.execute\('''\s*(CREATE TABLE IF NOT EXISTS tjc_hymn \(.*?\))'''",
    re.DOTALL,
)


def norm_sql(sql):
    """空白归一（比较 DDL 文本用；仅折叠空白，不改语义）"""
    return " ".join(sql.split())


def ddl_body(ddl):
    """取建表语句的**括号主体**（列定义部分）

    只比主体而不比头部：重建走"临时表 + RENAME"，SQLite 重命名后会把库里存的
    建表语句改写成 `CREATE TABLE "tjc_hymn" (...)`（带引号、无 `IF NOT EXISTS`），
    头部写法差异与列定义无关；列顺序另有 `target_cols` 比对兜底。
    """
    return norm_sql(ddl[ddl.index("(") + 1:ddl.rindex(")")])


def columns_of(ddl):
    """从建表 SQL 提取列顺序（逐行解析：本 DDL 一行一列）"""
    cols = []
    for raw in ddl.splitlines():
        line = raw.strip().rstrip(",")
        if not line or line in ("(", ")") or line.upper().startswith("CREATE TABLE"):
            continue
        head = re.match(r"[A-Za-z_]+", line)  # `UNIQUE(b)` / `CHECK (x > 0)` 也要认出表级约束
        if head and head.group(0).upper() in _CONSTRAINT_HEADS:
            continue
        cols.append(line.split()[0])
    return cols


def load_code_ddl(db_py=DB_PY):
    """从 db.py 解析 `_create_table_v4` 的建表 SQL；返回 (DDL 原文, 列名列表)"""
    with open(db_py, encoding="utf-8") as fh:
        src = fh.read()
    m = _DDL_RE.search(src)
    if not m:
        raise SystemExit(f"❌ 未能从 {db_py} 解析到 _create_table_v4 的建表语句")
    ddl = m.group(1)
    return ddl, columns_of(ddl)


def table_columns(conn, table=TABLE):
    """库内当前列顺序"""
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def table_ddl(conn, table=TABLE):
    """库内当前建表 SQL"""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row[0] if row else None


def _snapshot(conn, cols):
    """读取重建前的数据快照（显式列名 + 稳定排序，便于重排后逐行比对）"""
    collist = ", ".join(f'"{c}"' for c in cols)
    rows = conn.execute(f"SELECT {collist} FROM {TABLE} ORDER BY hymn_number").fetchall()
    seq_row = conn.execute("SELECT seq FROM sqlite_sequence WHERE name=?", (TABLE,)).fetchone()
    cats = conn.execute("SELECT * FROM hymn_category ORDER BY id").fetchall()
    return collist, rows, (seq_row[0] if seq_row else None), cats


def reorder(db_path=DB_PATH, dry_run=False, backup_dir="/tmp"):
    """把 db_path 的 `tjc_hymn` 列顺序对齐代码；返回结果字典（含自检项）"""
    code_ddl, target_cols = load_code_ddl()
    result = {"db": db_path, "target": target_cols, "dry_run": dry_run,
              "changed": False, "checks": []}

    conn = sqlite3.connect(db_path)
    try:
        before = table_columns(conn)
        result["before"] = before
        if before == target_cols:
            result["checks"].append(("列顺序已是代码顺序（无需重排）", True))
            result["ok"] = True
            return result

        missing = [c for c in target_cols if c not in before]
        extra = [c for c in before if c not in target_cols]
        result["missing"], result["extra"] = missing, extra
        if missing or extra:
            result["checks"].append(
                (f"列集合与代码不一致：代码独有 {missing} / 库内独有 {extra}", False))
            result["ok"] = False
            return result

        collist, old_rows, old_seq, old_cats = _snapshot(conn, before)
        if dry_run:
            result["checks"].append(("dry-run：仅比对，未改动数据库", True))
            result["ok"] = True
            return result

        # 备份（DB 已入 git，备份用于即时回滚）
        os.makedirs(backup_dir, exist_ok=True)
        backup = os.path.join(backup_dir, f"tjc_hymn.db.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(db_path, backup)
        result["backup"] = backup

        new_ddl = code_ddl.replace("CREATE TABLE IF NOT EXISTS tjc_hymn",
                                   f"CREATE TABLE {TMP_TABLE}", 1)
        if new_ddl == code_ddl:
            raise SystemExit("❌ 建表 SQL 临时表名替换失败（db.py DDL 头格式可能已变）")

        conn.isolation_level = None  # 手动事务
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(new_ddl)
            conn.execute(f"INSERT INTO {TMP_TABLE} ({collist}) SELECT {collist} FROM {TABLE}")
            conn.execute(f"DROP TABLE {TABLE}")
            conn.execute(f"ALTER TABLE {TMP_TABLE} RENAME TO {TABLE}")
            if old_seq is not None:
                conn.execute("UPDATE sqlite_sequence SET seq = ? WHERE name = ?", (old_seq, TABLE))
            conn.execute("COMMIT")
            # 回收空闲页：DROP 旧表留下大量 free page，不 VACUUM 文件会虚胖近一倍
            conn.execute("VACUUM")
        except Exception:
            conn.execute("ROLLBACK")
            raise

        after = table_columns(conn)
        _, new_rows, new_seq, new_cats = _snapshot(conn, before)
        ddl_now = table_ddl(conn) or ""
        body_now = ddl_body(ddl_now)
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]

        result["after"] = after
        result["changed"] = True
        result["checks"] += [
            ("列顺序 == 代码 _create_table_v4", after == target_cols),
            ("列定义（类型/默认值/约束）== 代码 DDL", body_now == ddl_body(code_ddl)),
            (f"行数不变（{len(old_rows)}）", len(new_rows) == len(old_rows)),
            ("数据逐行逐列一致（对重建前快照）", new_rows == old_rows),
            ("UNIQUE(hymn_number) 约束在位", "HYMN_NUMBER TEXT UNIQUE" in body_now.upper()),
            (f"sqlite_sequence 不变（{old_seq}）", new_seq == old_seq),
            ("hymn_category 未受影响", new_cats == old_cats),
            ("integrity_check = ok", integrity == "ok"),
            ("空闲页已回收（VACUUM，无文件虚胖）",
             conn.execute("PRAGMA freelist_count").fetchone()[0] == 0),
        ]
        result["ok"] = all(ok for _, ok in result["checks"])
        return result
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="把 tjc_hymn 表列顺序对齐 crawler_core/db.py 的 _create_table_v4")
    parser.add_argument("--db", default=DB_PATH, help=f"数据库路径（默认 {DB_PATH}）")
    parser.add_argument("--dry-run", action="store_true", help="只报告现状与目标差异，不改库")
    parser.add_argument("--backup-dir", default="/tmp", help="重建前备份目录（默认 /tmp）")
    args = parser.parse_args()

    res = reorder(db_path=args.db, dry_run=args.dry_run, backup_dir=args.backup_dir)

    print(f"数据库: {res['db']}")
    print("\n现状列顺序:")
    for i, col in enumerate(res.get("before", []), 1):
        flag = "" if col in res["target"] else "  ⚠️ 不在代码列中"
        print(f"  {i:2d}. {col}{flag}")
    print("\n目标列顺序（代码 _create_table_v4）:")
    for i, col in enumerate(res["target"], 1):
        flag = "" if col in res.get("before", []) else "  ✨ 待新增"
        print(f"  {i:2d}. {col}{flag}")

    if res.get("backup"):
        rel = os.path.relpath(res["db"], ROOT)
        print(f"\n已备份: {res['backup']}（恢复: cp <备份> {rel} 或 git checkout -- {rel}）")
    print("\n自检:")
    for name, ok in res["checks"]:
        print(f"  {'✅' if ok else '❌'} {name}")

    if res["changed"]:
        print("\n结果: 已按代码顺序重建表。")
    elif res["dry_run"]:
        print("\n结果: dry-run 完成（未改动数据库）。")
    else:
        print("\n结果: 无需调整。")
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
