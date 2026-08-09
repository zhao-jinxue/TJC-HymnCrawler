#!/usr/bin/env python3
"""
step7_png_db.py — 图片以新增字段方式入库 + 清理分页小图

功能:
  1. 为 tjc_hymn.db 新增字段 staff_png_path / numbered_png_path(幂等)
  2. 从 staff_img_path/numbered_img_path(PDF路径) 推导并回填对应 PNG 路径
  3. 删除多余的 _p1.png / _p2.png 分页小图(仅当同名整图 .png 存在)

断点续跑（中间文件）:
  - 进度文件: Hymn_Downloads/step7_progress.json
    {"version": 1, "completed": [已回填的hymn_number...]}
  - 每次成功回填后增量落盘; 中断后重跑, 已回填的编号不再重复处理
  - --reset-progress 可清空进度文件(配合 --force 全量重填)

用法:
  python3 step7_png_db.py          # 加字段+回填+删除分页图(幂等)
  python3 step7_png_db.py --reset-progress  # 清空进度全量重做
"""
import json
import os
import sqlite3

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)

DB = "tjc_hymn.db"
NEW_FIELDS = ("staff_png_path", "numbered_png_path")
PROGRESS_FILE = os.path.join("Hymn_Downloads", "step7_progress.json")
PROGRESS_VERSION = 1
PROGRESS_FLUSH_EVERY = 50


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


def load_progress():
    """加载回填进度: {hymn_number: 'done'}"""
    if not os.path.exists(PROGRESS_FILE):
        return {}
    try:
        with open(PROGRESS_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("version") != PROGRESS_VERSION:
            return {}
        return {h: "done" for h in data.get("completed", [])}
    except Exception:  # noqa: BLE001 - 进度文件损坏时从头开始
        return {}


def save_progress(completed_list):
    """写进度文件(原子替换)"""
    tmp = PROGRESS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"version": PROGRESS_VERSION, "completed": completed_list},
                  fh, ensure_ascii=False, indent=2)
    os.replace(tmp, PROGRESS_FILE)


def reset_progress():
    """清空进度文件"""
    if os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)
        print("已清空 step7 进度文件")


def backfill_png(conn, progress, force=False):
    """回填 PNG 路径到新字段

    Args:
        conn: 数据库连接
        progress: 进度集合 {hymn_number: 'done'}
        force: True 时忽略进度全量回填
    Returns:
        (staff_ok, numbered_ok, staff_miss, numbered_miss, resumed)
    """
    cur = conn.cursor()
    cur.execute("SELECT hymn_number, staff_img_path, numbered_img_path FROM tjc_hymn")
    rows = cur.fetchall()
    staff_ok = numbered_ok = staff_miss = numbered_miss = 0
    resumed = 0

    completed = list(progress.keys())
    dirty = 0

    for num, staff_pdf, numbered_pdf in rows:
        # 断点: 已完成的编号直接跳过
        if num in progress and not force:
            resumed += 1
            continue

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

        # 已回填的记入进度(增量落盘)
        if num not in completed:
            completed.append(num)
            dirty += 1
            if dirty >= PROGRESS_FLUSH_EVERY:
                conn.commit()          # 先提交数据
                save_progress(completed)
                dirty = 0

    conn.commit()
    if dirty > 0 and completed:
        save_progress(completed)
    return staff_ok, numbered_ok, staff_miss, numbered_miss, resumed


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


def run(force=False, reset=False):
    """程序化入口: 供 crawler_fast.py 调用

    Args:
        force: True 时忽略进度全量回填
        reset: True 时先清空进度文件
    """
    if reset:
        reset_progress()

    progress = load_progress()
    if progress and not force:
        print(f"断点续跑: 已有 {len(progress)} 个编号的进度记录")

    conn = sqlite3.connect(DB)
    added = ensure_fields(conn)
    print(f"[字段] 新增: {added if added else '无(已存在)'}")
    s_ok, n_ok, s_miss, n_miss, resumed = backfill_png(conn, progress, force=force)
    conn.close()
    print(f"[回填] staff_png_path {s_ok} 条, numbered_png_path {n_ok} 条"
          + (f", 跳过(进度) {resumed} 条" if resumed else ""))
    if s_miss or n_miss:
        print(f"[回填] 警告: staff 缺失 {s_miss}, numbered 缺失 {n_miss}")
    deleted, skipped = delete_pages()
    print(f"[清理] 删除分页图 {deleted} 张, 保留(无整图) {skipped} 张")
    print("完成")
    return {"staff": s_ok, "numbered": n_ok, "deleted_pages": deleted, "resumed": resumed}


def main():
    import argparse
    parser = argparse.ArgumentParser(description="图片路径入库(step7)")
    parser.add_argument("--force", action="store_true", help="忽略进度全量回填")
    parser.add_argument("--reset-progress", action="store_true", help="清空进度文件后重做")
    args = parser.parse_args()
    run(force=args.force, reset=args.reset_progress)


if __name__ == "__main__":
    main()
