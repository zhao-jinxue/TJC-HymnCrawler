#!/usr/bin/env python3
"""按用户 2026-09-12 拍板执行数据处理（可重复执行、幂等）。

① 删除 136 个已确认重复的「合唱-N部版」文件（保持同内容的「四部合唱-N部版」）
② #201 人聲版 `.mp4` → `.m4a` 归一化（文件改名 + checksums + probe_report + DB）
③ #349 目录改名 354_349救主正在等候 → 354_349奇妙的耶穌
   （目录 + url_map.txt + probe_report.json + step5_progress.json + DB 全列）
"""
import hashlib
import json
import os
import re
import shutil
import sqlite3

ROOT = "/home/zjx/hymn_crawler"
DL = os.path.join(ROOT, "Hymn_Downloads")
DB = os.path.join(ROOT, "tjc_hymn.db")
report = json.load(open(os.path.join(ROOT, "probe_report.json"), encoding="utf-8"))
by_no = {e["hymn_number"]: e for e in report}
dir_of = {no: os.path.join(DL, e["title"]) for no, e in by_no.items()}
log = {"deleted": [], "renamed": [], "db_updates": []}


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def load_cs(d):
    p = os.path.join(d, "checksums.json")
    return p, (json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None)


def save_cs(p, data):
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


# ---------- ① 删除重复文件 ----------
print("=" * 70)
print("① 删除 136 个重复的『合唱-N部版』（内容与『四部合唱-N部版』逐字节相同）")
pat = re.compile(r"^(\d+)_合唱-(\d)部版\.(m4a|mp3|mp4)$")
targets = []
for no, d in dir_of.items():
    if not (d and os.path.isdir(d)):
        continue
    for fn in sorted(os.listdir(d)):
        m = pat.match(fn)
        if not m:
            continue
        twin = os.path.join(d, f"{m.group(1)}_四部合唱-{m.group(2)}部版.{m.group(3)}")
        targets.append((no, d, fn, twin))

# 只有「存在同内容孪生文件」的才算重复；#6 的 4 个（API 真实分类）无孪生 → 保留
dup = [t for t in targets if os.path.exists(t[3])]
keep = [t for t in targets if not os.path.exists(t[3])]
print(f"  候选 {len(targets)} 个 → 可删（有孪生）{len(dup)} 个；保留（API 真实分类）{len(keep)} 个 "
      f"{[(k[0], k[2]) for k in keep]}")

freed = 0
touched_cs = {}
for no, d, fn, twin in dup:
    a, b = os.path.join(d, fn), twin
    ha, hb = md5(a), md5(b)
    assert ha == hb, f"内容不一致，拒绝删除：{a} vs {b}"
    size = os.path.getsize(a)
    os.remove(a)
    freed += size
    log["deleted"].append({"hymn": no, "file": os.path.relpath(a, ROOT), "md5": ha, "size": size})
    touched_cs.setdefault(d, []).append(fn)

for d, names in touched_cs.items():
    p, data = load_cs(d)
    if data is None:
        continue
    before = len(data)
    data = [x for x in data if x["file"] not in names]
    save_cs(p, data)
    print(f"  {os.path.basename(d)}: 删除 {before - len(data)} 个文件 + checksums 条目同步")
print(f"  共删除 {len(log['deleted'])} 个文件，释放 {freed / 1048576:.1f} MB")

# ---------- ② #201 .mp4 → .m4a ----------
print("=" * 70)
print("② #201 人聲版 .mp4 → .m4a 归一化")
d201 = dir_of["201"]
src, dst = os.path.join(d201, "201_人聲版.mp4"), os.path.join(d201, "201_人聲版.m4a")
if os.path.exists(src):
    os.rename(src, dst)
    log["renamed"].append({"from": os.path.relpath(src, ROOT), "to": os.path.relpath(dst, ROOT)})
    print(f"  文件改名：201_人聲版.mp4 → 201_人聲版.m4a ({os.path.getsize(dst)} B)")
elif os.path.exists(dst):
    print("  已归一化，跳过")
p, data = load_cs(d201)
if data:
    for x in data:
        if x["file"] == "201_人聲版.mp4":
            x["file"] = "201_人聲版.m4a"
    save_cs(p, data)
    print("  checksums.json 条目已同步")
v = by_no["201"]["audio_versions"]["人聲版"]
if v.get("ext") == "mp4":
    v["_src_ext"] = "mp4"
    v["ext"] = "m4a"
    print("  probe_report.json：ext mp4 → m4a（保留 _src_ext 溯源）")

# ---------- ③ #349 目录改名 ----------
print("=" * 70)
print("③ #349 目录改名：354_349救主正在等候 → 354_349奇妙的耶穌")
old_dir, new_dir = os.path.join(DL, "354_349救主正在等候"), os.path.join(DL, "354_349奇妙的耶穌")
OLD, NEW = "354_349救主正在等候", "354_349奇妙的耶穌"
if os.path.isdir(old_dir):
    os.rename(old_dir, new_dir)
    print("  目录已改名")
elif os.path.isdir(new_dir):
    print("  目录已为新名，跳过")
else:
    raise SystemExit("❌ 找不到 349 目录")

# url_map.txt
mp = os.path.join(DL, "url_map.txt")
txt = open(mp, encoding="utf-8").read()
if OLD in txt:
    open(mp, "w", encoding="utf-8").write(txt.replace(OLD, NEW))
    print("  url_map.txt 已更新")
# step5_progress.json
sp = os.path.join(DL, "step5_progress.json")
if os.path.exists(sp):
    s = open(sp, encoding="utf-8").read()
    if OLD in s:
        open(sp, "w", encoding="utf-8").write(s.replace(OLD, NEW))
        print("  step5_progress.json 已更新")
# probe_report.json
if by_no["349"]["title"] == OLD:
    by_no["349"]["title"] = NEW
    print("  probe_report.json title 已更新")

# DB：全文本列替换 + title 改为官网名
conn = sqlite3.connect(DB)
cols = [r[1] for r in conn.execute("PRAGMA table_info(tjc_hymn)")]
row = conn.execute("SELECT rowid, * FROM tjc_hymn WHERE hymn_number='349'").fetchone()
rowid = row[0]
sets, vals = [], []
for i, c in enumerate(cols, start=1):
    val = row[i]
    if isinstance(val, str) and OLD in val:
        sets.append(f"{c}=?"), vals.append(val.replace(OLD, NEW))
        log["db_updates"].append({"col": c, "new": val.replace(OLD, NEW)})
if row[cols.index("title") + 1] != "奇妙的耶穌":
    sets.append("title=?"), vals.append("奇妙的耶穌")
    log["db_updates"].append({"col": "title", "new": "奇妙的耶穌"})
if sets:
    conn.execute(f"UPDATE tjc_hymn SET {', '.join(sets)} WHERE rowid=?", (*vals, rowid))
    conn.commit()
    print(f"  DB 已更新 {len(sets)} 个字段：{[s.split('=')[0] for s in sets]}")
conn.close()

# ---------- 落盘 ----------
with open(os.path.join(ROOT, "probe_report.json"), "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
    f.write("\n")
json.dump(log, open("/tmp/cleanup_log.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("=" * 70)
print(f"完成：删除 {len(log['deleted'])} 文件 / 释放 {freed / 1048576:.1f} MB，"
      f"改名 {len(log['renamed'])}，DB 字段 {len(log['db_updates'])}")
