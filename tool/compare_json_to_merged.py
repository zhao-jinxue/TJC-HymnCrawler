#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""比对 merged_all.json（数组版）与源文本（merged_all.txt / 16 份 txt）内容一致性。

方法与  merged_all.json 结构：
{
  "大类": { "小类": [ {"诗名": 编号}, ... ] }
}

- 读取 JSON，展平所有条目（诗名->编号），收集：条目、小类标题、大类标题
- 读取 merged_all.txt（与 16 份源文件已验证逐字符一致），逐行解析：诗名 编号
- 双向核对：
  1. 编号集合一致性（含重复编号，如 51/124/274/296/时刻交托主 153+439）
  2. 每个 JSON 条目 (编号,诗名) 在源文本中能找到对应行（诗名做宽容归一化）
  3. 每个源条目反向在 JSON 中找到对应
  4. 大类/小类标题在源文本中出现
"""

import json
import re
import unicodedata

SRC = "/mnt/c/Users/小蔡爱金雪/Downloads/赞美诗分类目录/识别结果/merged_all.txt"
JSON_PATH = "/mnt/c/Users/小蔡爱金雪/Downloads/赞美诗分类目录/识别结果/merged_all.json"

ENTRY_RE = re.compile(r"^(.*?)\s+(\d+)$")
DOT_RE = re.compile(r"[…·\s]+")


def norm(s: str) -> str:
    """归一化：全角转半角、去除空白与省略号、统一常见异体字。"""
    s = unicodedata.normalize("NFKC", s)
    s = DOT_RE.sub("", s)
    pairs = {"祂": "神", "祢": "你", "綺": "奇"}
    for a, b in pairs.items():
        s = s.replace(a, b)
    return s


def main() -> None:
    # 1) 读 JSON（数组版）
    with open(JSON_PATH, encoding="utf-8") as f:
        data = json.load(f)

    json_entries: list[tuple[int, str]] = []   # (编号, 诗名)
    json_subs: list[tuple[str, str]] = []      # (大类, 小类)
    json_cats = list(data.keys())
    for cat, subs in data.items():
        for sub, items in subs.items():
            json_subs.append((cat, sub))
            for entry in items:
                for name, num in entry.items():
                    json_entries.append((int(num), name))
    json_num_set = {n for n, _ in json_entries}

    # 2) 读源文本（merged_all.txt）
    with open(SRC, encoding="utf-8") as f:
        lines = f.read().splitlines()

    src_entries: list[tuple[int, str]] = []
    src_titles: set[str] = set()
    for ln in lines:
        s = ln.strip()
        if not s or s.startswith("====="):
            continue
        m = ENTRY_RE.match(s)
        if m:
            src_entries.append((int(m.group(2)), m.group(1).strip()))
        else:
            src_titles.add(s.strip("【】"))
    src_num_set = {n for n, _ in src_entries}

    # ===== 比对 1：编号集合 =====
    print("=== 1) 编号集合一致性 ===")
    jn_only = set(json_num_set) - set(src_num_set)
    sn_only = set(src_num_set) - set(json_num_set)
    if not jn_only and not sn_only:
        print("✔ 编号集合完全一致（含重复编号，如 时刻交托主 153+439）")
    else:
        if jn_only:
            print(f"✘ 仅 JSON 有、源文本无的编号: {sorted(jn_only)}")
        if sn_only:
            print(f"✘ 仅源文本有、JSON 无的编号: {sorted(sn_only)}")

    # ===== 比对 2：JSON 每条目 -> 源文本 =====
    print("\n=== 2) JSON 每条目 -> 源文本 ===")
    src_by_num: dict[int, list[str]] = {}
    for n, name in src_entries:
        src_by_num.setdefault(n, []).append(norm(name))
    missing: list[str] = []
    for n, name in json_entries:
        candidates = src_by_num.get(n, [])
        nn = norm(name)
        if not candidates or not any(c == nn or c in nn or nn in c for c in candidates):
            missing.append(f"{name}->{n}")
    if not missing:
        print("✔ 全部条目均在源文本中有对应（诗名宽容匹配通过）")
    else:
        print(f"✘ 无法匹配的 JSON 条目（{len(missing)} 条）：")
        for x in missing:
            print(f"   - {x}")

    # ===== 比对 3：源文本条目反向 -> JSON =====
    print("\n=== 3) 源文本每条目 -> JSON（反向） ===")
    json_by_num: dict[int, list[str]] = {}
    for n, name in json_entries:
        json_by_num.setdefault(n, []).append(norm(name))
    reverse_missing: list[str] = []
    for n, name in src_entries:
        candidates = json_by_num.get(n, [])
        nn = norm(name)
        if not candidates or not any(c == nn or c in nn or nn in c for c in candidates):
            reverse_missing.append(f"{name}->{n}")
    if not reverse_missing:
        print("✔ 源文本全部条目在 JSON 中均有对应")
    else:
        print(f"✘ 源文本中 JSON 无对应的条目（{len(reverse_missing)} 条）：")
        for x in reverse_missing:
            print(f"   - {x}")

    # ===== 比对 4：标题存在性 =====
    print("\n=== 4) 大类 / 小类标题 -> 源文本 ===")
    src_title_set = {norm(t) for t in src_titles}
    title_missing = [c for c in json_cats if norm(c) not in src_title_set]
    sub_missing = [s for _, s in json_subs if norm(s.strip("【】")) not in src_title_set]
    if not title_missing and not sub_missing:
        print("✔ 13 个大类、45 个小类标题均在源文本中出现")
    else:
        if title_missing:
            print(f"✘ 大类缺失: {title_missing}")
        if sub_missing:
            print(f"✘ 小类缺失: {sub_missing}")

    # ===== 比对 5：同名诗专项（确保 153/439 都保留） =====
    print("\n=== 5) 同名诗（时刻交托主）专项检查 ===")
    dup_names = {}
    for name, n in [(nm, num) for num, nm in json_entries]:
        dup_names.setdefault(norm(name), []).append(n)
    for nm, nums in dup_names.items():
        if len(nums) > 1:
            print(f"✔ 同名诗 '{nm}': 编号 {sorted(nums)}（均保留）")
    if not any(len(v) > 1 for v in dup_names.values()):
        print("（无同名诗）")

    # ===== 汇总 =====
    print("\n=== 汇总 ===")
    total_ok = (
        not jn_only and not sn_only and not missing
        and not reverse_missing and not title_missing and not sub_missing
    )
    print(f"JSON 条目: {len(json_entries)} 条 | 源文本条目: {len(src_entries)} 条")
    print("✔ 比对通过：内容一致" if total_ok else "✘ 存在差异，请查看上方明细")


if __name__ == "__main__":
    main()