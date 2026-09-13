#!/usr/bin/env python3
"""第九轮：终版对账（修正 `.mp4` 盲点）。

口径：
  期望 = API 中 file_url 非空且实测可用的音频（**9 条 null + #62 404 已排除**）
  实际 = 本地目录内音频文件，按 `{no}_{版本}.{ext}` 解析；`.mp4` 视为 `.m4a` 等价
输出：丢失 / 多余 两份清单 + 数量汇总
"""
import json
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, "/home/zjx/hymn_crawler")
from crawler_core.config import BASE_URL  # noqa: E402

ROOT = "/home/zjx/hymn_crawler"
api = {i["no"]: i for i in json.load(open("/tmp/api_all_hymns.json", encoding="utf-8"))}
report = json.load(open(os.path.join(ROOT, "data", "probe_report.json"), encoding="utf-8"))
dirs = {e["hymn_number"]: os.path.join(ROOT, "Hymn_Downloads", e["title"]) for e in report}

NULL_NOS = {"178", "249", "255", "268", "274_b", "308", "386", "387", "389"}
AUDIO_EXT = {"m4a", "mp3", "mp4"}
NORM = {"mp4": "m4a"}


def norm_ext(e):
    return NORM.get(e, e)


missing, extra, matched = [], [], []
stat = defaultdict(int)
for no, rec in sorted(api.items(), key=lambda x: x[0]):
    d = dirs.get(no)
    exp = {}
    for f in rec.get("audio_files") or []:
        url = f.get("file_url")
        cat = (f.get("audio_category") or {}).get("name", "")
        if not url:
            stat["api_null"] += 1
            continue
        ext = norm_ext(url.rsplit(".", 1)[-1].lower())
        exp[f"{cat}版"] = ext
    if no == "62":
        exp.pop("人聲版", None)          # 实测 404，不计入期望
        stat["api_404_excluded"] += 1
    loc = {}
    if d and os.path.isdir(d):
        for fn in os.listdir(d):
            m = re.match(rf"^{re.escape(no)}_(.+)\.([A-Za-z0-9]+)$", fn)
            if m and m.group(2).lower() in AUDIO_EXT:
                loc[m.group(1)] = norm_ext(m.group(2).lower())
    for ver, ext in exp.items():
        if ver in loc:
            matched.append(no)
            stat["matched"] += 1
            del loc[ver]
        else:
            missing.append((no, ver, ext))
            stat["missing"] += 1
    for ver, ext in loc.items():
        extra.append((no, ver, ext))
        stat["extra"] += 1

print(f"=== 汇总 ===\n{json.dumps(stat, ensure_ascii=False, indent=1)}")
print(f"\n=== 期望音频总数（实测可用）= {stat['matched'] + stat['missing']} ===")
print(f"\n=== 缺失 {len(missing)} ===")
for x in missing:
    print(f"  #{x[0]} {x[1]} (.{x[2]})")
print(f"\n=== 多余（本地有、API 无）{len(extra)} ===")
byver = defaultdict(list)
for x in extra:
    byver[x[1]].append(x[0])
for ver, nos in sorted(byver.items(), key=lambda x: -len(x[1])):
    print(f"  {ver:<16} {len(nos):>3} 首 {nos[:8]}{' …' if len(nos) > 8 else ''}")
