#!/usr/bin/env python3
"""第七轮：与官网 API 对账「合唱-N部版 / 四部合唱-N部版」是否站点侧就存在两组资源。

判定口径（全部以 API 为站点真源）：
  1. 该诗歌 API 里 audio_category.name 的集合；
  2. API 中「合唱」类与「四部合唱」类各自的 file_url 是否**不同 URL**；
  3. 本地同名文件内容 md5（全文件）是否一致。
"""
import hashlib
import json
import os
import re
from collections import defaultdict

ROOT = "/home/zjx/hymn_crawler"
api = {i["no"]: i for i in json.load(open("/tmp/api_all_hymns.json", encoding="utf-8"))}

# url_map: seq | dirname | url
dirs = {}
for line in open(os.path.join(ROOT, "Hymn_Downloads", "url_map.txt"), encoding="utf-8"):
    p = line.strip().split("|")
    if len(p) >= 3:
        dirs[p[2].rsplit("/", 1)[-1]] = os.path.join(ROOT, "Hymn_Downloads", p[1])


def full_md5(p, chunk=1 << 20):
    h = hashlib.md5()
    with open(p, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


# ---------- 1. API 侧：按分类分组 ----------
pairs = defaultdict(dict)          # no -> category name -> [url basename]
for no, rec in api.items():
    for f in rec.get("audio_files") or []:
        cat = (f.get("audio_category") or {}).get("name", "")
        url = f.get("file_url") or ""
        pairs[no].setdefault(cat, []).append(url.rsplit("/", 1)[-1])

print("=== API 中带「合唱」字样的分类名统计 ===")
catcount = defaultdict(int)
for no, cats in pairs.items():
    for c in cats:
        if "合唱" in c:
            catcount[c] += 1
for c, n in sorted(catcount.items(), key=lambda x: -x[1]):
    print(f"  {c:<16} 出现在 {n} 首")

print("\n=== 样例：API 音频分类 vs URL ===")
for no in ("6", "8", "25", "211", "231", "349", "201"):
    rec = api.get(no)
    if not rec:
        print(f"  #{no} 不在 API 结果中")
        continue
    print(f"  #{no} {rec['name']}:")
    for c, urls in sorted(pairs[no].items()):
        print(f"      [{c}] " + ", ".join(urls))

# ---------- 2. 本地文件层面 ----------
print("\n=== 本地成对文件比对（全文件 md5）===")
pat_a = re.compile(r"^(\d+)_合唱-(\d)部版\.(m4a|mp3|mp4)$")
pat_b = re.compile(r"^(\d+)_四部合唱-(\d)部版\.(m4a|mp3|mp4)$")
local = defaultdict(lambda: {"a": {}, "b": {}})
for no, d in dirs.items():
    if not os.path.isdir(d):
        continue
    for fn in os.listdir(d):
        ma, mb = pat_a.match(fn), pat_b.match(fn)
        if ma:
            local[ma.group(1)]["a"][int(ma.group(2))] = os.path.join(d, fn)
        elif mb:
            local[mb.group(1)]["b"][int(mb.group(2))] = os.path.join(d, fn)

both = sorted([n for n, v in local.items() if v["a"] and v["b"]], key=lambda x: int(x))
only_a = sorted([n for n, v in local.items() if v["a"] and not v["b"]], key=lambda x: int(x))
only_b = sorted([n for n, v in local.items() if v["b"] and not v["a"]], key=lambda x: int(x))
print(f"  成对诗歌 {len(both)} 首；仅有『合唱-N部版』{len(only_a)} 首 {only_a}；"
      f"仅有『四部合唱-N部版』{len(only_b)} 首 {only_b}")

same_cnt = diff_cnt = 0
bytes_dup = 0
detail = []
for no in both:
    for part in sorted(set(local[no]["a"]) & set(local[no]["b"])):
        pa, pb = local[no]["a"][part], local[no]["b"][part]
        sa, sb = os.path.getsize(pa), os.path.getsize(pb)
        ha, hb = full_md5(pa), full_md5(pb)
        if ha == hb:
            same_cnt += 1
            bytes_dup += sa
        else:
            diff_cnt += 1
            detail.append((no, part, sa, sb, ha[:10], hb[:10]))
print(f"  逐部版配对：完全相同 {same_cnt} 组，不同 {diff_cnt} 组，重复占用 {bytes_dup/1048576:.1f} MB")
for d in detail[:20]:
    print(f"     #{d[0]} 第{d[1]}部 本地合唱={d[2]} 本地四部={d[3]} md5 {d[4]} vs {d[5]}")

# ---------- 3. API 是否同时给出两组资源 ----------
print("\n=== 这 34 首在 API 里到底有几个合唱分类 ===")
for no in both:
    cats = sorted([c for c in pairs[no] if "合唱" in c])
    print(f"  #{no} {api[no]['name']:<12} API合唱分类={cats}  URL数={sum(len(pairs[no][c]) for c in cats)}")

# ---------- 4. #6 / #201 / #349 特查 ----------
print("\n=== 特查 ===")
for no in ("6", "201", "349"):
    d = dirs.get(no)
    files = sorted(os.listdir(d)) if d and os.path.isdir(d) else []
    aud = [f for f in files if f.rsplit(".", 1)[-1].lower() in ("m4a", "mp3", "mp4")]
    print(f"  #{no} 本地音频 {len(aud)}: {aud}")
    print(f"       API: " + json.dumps({c: [u[:60] for u in us] for c, us in pairs[no].items()},
                                        ensure_ascii=False))
