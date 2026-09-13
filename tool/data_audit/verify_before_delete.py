#!/usr/bin/env python3
"""第八轮：删除前的最终核验。

1. 这 136 个 `合唱-N部版` 是否被 probe_report.json / DB / 其他产物引用（引用=不能直接删）；
2. 是否被 checksums.json 记录（删文件需同步清理）；
3. #201 人聲版 本地文件与 API URL 内容是否一致。
"""
import hashlib
import json
import os
import re
import sqlite3

import requests
import urllib3

urllib3.disable_warnings()
ROOT = "/home/zjx/hymn_crawler"
api = {i["no"]: i for i in json.load(open("/tmp/api_all_hymns.json", encoding="utf-8"))}
report = json.load(open(os.path.join(ROOT, "data", "probe_report.json"), encoding="utf-8"))
db = sqlite3.connect(os.path.join(ROOT, "tjc_hymn.db"))

pat_a = re.compile(r"^(\d+)_合唱-(\d)部版\.(m4a|mp3|mp4)$")
target = {}
for e in report:
    d = os.path.join(ROOT, "Hymn_Downloads", e["title"])
    if not os.path.isdir(d):
        continue
    for fn in os.listdir(d):
        if pat_a.match(fn):
            target.setdefault(e["hymn_number"], []).append((d, fn))

print(f"=== 本轮目标：{sum(len(v) for v in target.values())} 个文件 / "
      f"{len(target)} 首 ===")

# 1. probe_report 引用
ref_pr = []
for e in report:
    for ver in e.get("audio_versions") or {}:
        if re.match(r"^合唱-\d部版$", ver):
            ref_pr.append((e["hymn_number"], ver))
print(f"probe_report.json 中仍被引用的『合唱-N部版』键：{len(ref_pr)} {ref_pr[:5]}")

# 2. DB 引用
ref_db = []
for no, row in db.execute("select hymn_number, audio_versions from tjc_hymn"):
    try:
        av = json.loads(row or "{}")
    except json.JSONDecodeError:
        continue
    for ver in av:
        if re.match(r"^合唱-\d部版$", ver):
            ref_db.append((no, ver))
print(f"DB audio_versions 中仍被引用的『合唱-N部版』键：{len(ref_db)} {ref_db[:5]}")

# 3. checksums.json 记录
cs_hits = 0
cs_no_hits = []
for no, files in target.items():
    d = files[0][0]
    cs = os.path.join(d, "checksums.json")
    if not os.path.exists(cs):
        cs_no_hits.append(no)
        continue
    names = {x["file"] for x in json.load(open(cs, encoding="utf-8"))}
    cs_hits += sum(1 for _, fn in files if fn in names)
print(f"checksums.json 中记录的目标文件数：{cs_hits}；无 checksums.json 的诗歌：{cs_no_hits}")

# 4. 内容重复二次确认（全文件 md5，随机抽 6 首）
print("\n=== 抽样二次确认（全文件 md5）===")
for no in sorted(target, key=lambda x: int(x))[:6]:
    d = target[no][0][0]
    for part in (1, 2):
        a = os.path.join(d, f"{no}_合唱-{part}部版.m4a")
        b = os.path.join(d, f"{no}_四部合唱-{part}部版.m4a")
        if os.path.exists(a) and os.path.exists(b):
            ha = hashlib.md5(open(a, "rb").read()).hexdigest()
            hb = hashlib.md5(open(b, "rb").read()).hexdigest()
            print(f"  #{no}-{part}部 {'一致 ✅' if ha == hb else '不一致 ❌'} {ha[:12]} / {hb[:12]}")
    # API 侧该诗是否有 合唱-N部 分类
    cats = sorted({(f.get("audio_category") or {}).get("name", "")
                   for f in api[no].get("audio_files") or []})
    print(f"      API 分类={cats}")

# 5. #201 人聲版 内容比对
print("\n=== #201 人聲版 ===")
p = os.path.join(ROOT, "Hymn_Downloads/204_201永遠的榮耀/201_人聲版.mp4")
url = [f["file_url"] for f in api["201"]["audio_files"]
       if f["audio_category"]["name"] == "人聲"][0]
h_local = hashlib.md5(open(p, "rb").read()).hexdigest()
r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=90, verify=False)
h_remote = hashlib.md5(r.content).hexdigest()
print(f"  本地 {os.path.getsize(p)} B md5={h_local}")
print(f"  远端 {len(r.content)} B md5={h_remote}  HTTP {r.status_code}")
print(f"  → {'内容一致，无需补下载 ✅' if h_local == h_remote else '内容不一致，需重新下载 ⚠️'}")
