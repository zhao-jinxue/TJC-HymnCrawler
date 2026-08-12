#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""将 merged_all.json（数组版）导入 tjc_hymn.db 的新表 hymn_category。

表结构：
  id          INTEGER PRIMARY KEY AUTOINCREMENT
  category    TEXT NOT NULL              -- 大类
  subcategory TEXT NOT NULL DEFAULT ''   -- 小类（无独立小类则为空串）
  hymns       TEXT NOT NULL              -- 诗歌数组 JSON 字符串

数据源 merged_all.json 结构：
  { "大类": { "小类": [ {"诗名": 编号}, ... ] } }

说明：
- 婚丧礼仪 / 附录 等"小类名 == 大类名"（即无独立小类，用大类作默认小类）
  的记录，subcategory 置为空串 ''，符合"小类(没有则空)"的要求。
- 表不存在则创建；已存在则清空后重建数据（幂等）。
"""

import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "tjc_hymn.db")
JSON_PATH = "/mnt/c/Users/小蔡爱金雪/Downloads/赞美诗分类目录/识别结果/merged_all.json"


def main() -> None:
    if not os.path.isfile(JSON_PATH):
        sys.exit(f"[错误] JSON 文件不存在: {JSON_PATH}")

    with open(JSON_PATH, encoding="utf-8") as f:
        data = json.load(f)

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # 建表（幂等）
    cur.execute("""
        CREATE TABLE IF NOT EXISTS hymn_category (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            category    TEXT NOT NULL,
            subcategory TEXT NOT NULL DEFAULT '',
            hymns       TEXT NOT NULL
        )
    """)
    # 清空旧数据（幂等重建）
    cur.execute("DELETE FROM hymn_category")

    total_cat = 0
    total_rows = 0
    total_hymns = 0
    for cat, subs in data.items():
        total_cat += 1
        for sub, entries in subs.items():
            # 小类名 == 大类名 -> 无独立小类，置空
            sub_value = "" if sub == cat else sub
            hymns_json = json.dumps(entries, ensure_ascii=False)
            cur.execute(
                "INSERT INTO hymn_category (category, subcategory, hymns) VALUES (?, ?, ?)",
                (cat, sub_value, hymns_json),
            )
            total_rows += 1
            total_hymns += len(entries)

    conn.commit()

    # 验证统计
    rows = cur.execute(
        "SELECT category, COUNT(*) FROM hymn_category GROUP BY category"
    ).fetchall()
    print(f"已导入: {DB_PATH}")
    print(f"大类数: {total_cat}")
    print(f"总行数(大类×小类): {total_rows}")
    print(f"诗歌条目总数: {total_hymns}")
    print("\n各分类行数:")
    for cat, cnt in rows:
        print(f"  {cat}: {cnt} 行")

    # 抽查：婚丧礼仪/附录 小类应为空
    for cat in ("婚丧礼仪", "附录"):
        r = cur.execute(
            "SELECT subcategory, hymns FROM hymn_category WHERE category = ?",
            (cat,),
        ).fetchall()
        for sub, hymns in r:
            print(f"\n[{cat}] subcategory={sub!r} hymns={len(json.loads(hymns))} 首")
            print(f"   示例: {hymns[:80]}...")

    conn.close()


if __name__ == "__main__":
    main()