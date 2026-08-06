#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
step6_update_img.py — 更新数据库图片路径 + 追加 checksums.json

任务1: 将 tjc_hymn.db 的 staff_img_path / numbered_img_path 从 PDF 改为 PNG
       - 单页: <编号>_五线谱.png  / <编号>_简谱.png
       - 双页: <编号>_五线谱_p1.png / <编号>_简谱_p1.png (取第一页)
任务2: 遍历每个子目录的 checksums.json, 追加/更新所有 PNG 的 {file, sha256}

用法:
  python3 step6_update_img.py
"""
import hashlib
import json
import os
import sqlite3

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)

DB = "tjc_hymn.db"


def sha256_of(path):
    """计算文件 sha256"""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_png_from_pdf(pdf_path):
    """从 PDF 路径推导对应 PNG 路径(单页同名; 双页取 _p1)"""
    if not pdf_path:
        return None
    stem = pdf_path[:-4]  # 去掉 .pdf
    cand1 = stem + ".png"
    if os.path.exists(cand1):
        return cand1
    cand2 = stem + "_p1.png"
    if os.path.exists(cand2):
        return cand2
    return None


def update_db():
    """任务1: 更新数据库图片路径 PDF -> PNG"""
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("SELECT hymn_number, staff_img_path, numbered_img_path FROM tjc_hymn")
    rows = cur.fetchall()
    updated_staff = updated_numbered = 0
    missing_staff = missing_numbered = 0

    for num, staff_pdf, numbered_pdf in rows:
        staff_png = resolve_png_from_pdf(staff_pdf) if staff_pdf and staff_pdf.endswith(".pdf") else None
        if staff_png:
            cur.execute("UPDATE tjc_hymn SET staff_img_path=? WHERE hymn_number=?",
                        (staff_png, num))
            updated_staff += 1
        elif staff_pdf and staff_pdf.endswith(".pdf"):
            missing_staff += 1

        numbered_png = resolve_png_from_pdf(numbered_pdf) if numbered_pdf and numbered_pdf.endswith(".pdf") else None
        if numbered_png:
            cur.execute("UPDATE tjc_hymn SET numbered_img_path=? WHERE hymn_number=?",
                        (numbered_png, num))
            updated_numbered += 1
        elif numbered_pdf and numbered_pdf.endswith(".pdf"):
            missing_numbered += 1

    conn.commit()
    conn.close()
    print(f"[任务1] DB 更新完成: staff {updated_staff} 条, numbered {updated_numbered} 条")
    if missing_staff or missing_numbered:
        print(f"[任务1] 警告: 找不到对应PNG staff {missing_staff}, numbered {missing_numbered}")


def update_checksums():
    """任务2: 追加/更新 checksums.json 中的 PNG 条目"""
    changed_dirs = 0
    for dirpath, _dirs, files in os.walk("Hymn_Downloads"):
        checksum_path = os.path.join(dirpath, "checksums.json")
        if not os.path.exists(checksum_path):
            continue
        with open(checksum_path, "r", encoding="utf-8") as fh:
            entries = json.load(fh)

        index = {e["file"]: i for i, e in enumerate(entries)}
        modified = False
        for fn in sorted(files):
            if fn.endswith(".png"):
                h = sha256_of(os.path.join(dirpath, fn))
                if fn in index:
                    if entries[index[fn]]["sha256"] != h:
                        entries[index[fn]]["sha256"] = h
                        modified = True
                else:
                    entries.append({"file": fn, "sha256": h})
                    modified = True

        if modified:
            with open(checksum_path, "w", encoding="utf-8") as fh:
                json.dump(entries, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
            changed_dirs += 1

    print(f"[任务2] checksums.json 更新: {changed_dirs} 个目录")


def main():
    update_db()
    update_checksums()
    print("完成")


if __name__ == "__main__":
    main()