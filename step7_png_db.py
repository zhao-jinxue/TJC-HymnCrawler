#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
step7_png_db.py — 图片以新增字段方式入库 + 清理分页小图

功能:
  1. 为 tjc_hymn.db 新增字段 staff_png_path / numbered_png_path(幂等)
  2. 从 staff_img_path/numbered_img_path(PDF路径) 推导并回填对应 PNG 路径
  3. 删除多余的 _p1.png / _p2.png 分页小图(仅当同名整图 .png 存在)

用法:
  python3 step7_png_db.py          # 加字段+回填+删除分页图
"""
import os
import sqlite3

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)

DB = "tjc_hymn.db"
NEW_FIELDS = ("staff_png_path", "numbered_png_path")


def ensure_fields(conn):
    """幂等新增字段"""
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(tjc_hymn)")
    existing = {row[1] for row in cur.fetchall()}
    added = []
    for f in NEW_FIELDS:
        if f not in existing:
            cur.execute(f"ALTER TABLE tjc_hymn ADD COLUMN {f} TEXT")
            added.append(f)
    conn.commit()
    return added


def resolve_png(pdf_path):
    """PDF 路径 -> 对应 PNG 路径(同名, 双页已拼接为同名整图)"""
    if not pdf_path:
        return None
    png = pdf_path[:-4] + ".png"  # xxx.pdf -> xxx.png
    if os.path.exists(png):
        return png
    return None


def backfill_png(conn):
    """回填 PNG 路径到新字段"""
    cur = conn.cursor()
    cur.execute("SELECT hymn_number, staff_img_path, numbered_img_path FROM tjc_hymn")
    rows = cur.fetchall()
    staff_ok = numbered_ok = staff_miss = numbered_miss = 0
    for num, staff_pdf, numbered_pdf in rows:
        sp = resolve_png(staff_pdf)
        if sp:
            cur.execute("UPDATE tjc_hymn SET staff_png_path=? WHERE hymn_number=?", (sp, num))
            staff_ok += 1
        elif staff_pdf:
            staff_miss += 1

        np_ = resolve_png(numbered_pdf)
        if np_:
            cur.execute("UPDATE tjc_hymn SET numbered_png_path=? WHERE hymn_number=?", (np_, num))
            numbered_ok += 1
        elif numbered_pdf:
            numbered_miss += 1

    conn.commit()
    return staff_ok, numbered_ok, staff_miss, numbered_miss


def delete_pages():
    """删除 _p1/_p2 分页小图(仅当同名整图存在)"""
    deleted = skipped = 0
    for dirpath, _d, files in os.walk("Hymn_Downloads"):
        for f in files:
            if not f.endswith(("_p1.png", "_p2.png")):
                continue
            base = f.replace("_p1.png", "").replace("_p2.png", "")
            whole = base + ".png"
            if whole in files:
                os.remove(os.path.join(dirpath, f))
                deleted += 1
            else:
                skipped += 1  # 无整图则保留, 防误删
    return deleted, skipped


def run():
    """程序化入口: 供 crawler_fast.py 调用"""
    conn = sqlite3.connect(DB)
    added = ensure_fields(conn)
    print(f"[字段] 新增: {added if added else '无(已存在)'}")
    s_ok, n_ok, s_miss, n_miss = backfill_png(conn)
    conn.close()
    print(f"[回填] staff_png_path {s_ok} 条, numbered_png_path {n_ok} 条")
    if s_miss or n_miss:
        print(f"[回填] 警告: staff 缺失 {s_miss}, numbered 缺失 {n_miss}")
    deleted, skipped = delete_pages()
    print(f"[清理] 删除分页图 {deleted} 张, 保留(无整图) {skipped} 张")
    print("完成")
    return {"staff": s_ok, "numbered": n_ok, "deleted_pages": deleted}


def main():
    run()


if __name__ == "__main__":
    main()
