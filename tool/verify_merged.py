#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验 merged_all.txt 是否与 16 个源文件内容完全一致。

方法：
- 读取 识别结果 目录下全部 txt（排除 merged_all.txt / cleaned 子目录产物）
- 按自然排序（1.1..8.2）用与 merge_ocr_results.py 相同的拼接逻辑重新生成
  （每份 strip 后加 "===== X.Y.txt =====" 标记，份间空一行，末尾换行）
- 与 merged_all.txt 逐字符比对，输出所有不一致位置

同时做附加体检：
- 每个文件是否在 merged_all.txt 中找到且只出现一次分隔标记
- 是否有孤立行（源文件内容没进合并文件）
- 编号重复统计（供人工判断，非错误）
"""

import os
import re
import sys

SRC_DIR = "/mnt/c/Users/小蔡爱金雪/Downloads/赞美诗分类目录/识别结果"
MERGED_PATH = os.path.join(SRC_DIR, "merged_all.txt")


def nat_key(name: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name)]


def main() -> None:
    if not os.path.isfile(MERGED_PATH):
        sys.exit(f"[错误] merged_all.txt 不存在: {MERGED_PATH}")

    # 1) 收集源文件（排除合并产物自身）
    files = []
    for f in os.listdir(SRC_DIR):
        p = os.path.join(SRC_DIR, f)
        if os.path.isfile(p) and f.endswith(".txt") and f != "merged_all.txt":
            files.append(f)
    files = sorted(files, key=nat_key)
    print(f"源文件（{len(files)} 份）：{files}\n")

    # 2) 用相同逻辑重建合并内容
    sections = []
    for name in files:
        with open(os.path.join(SRC_DIR, name), encoding="utf-8") as f:
            content = f.read().strip()
        sections.append(f"===== {name} =====\n{content}")
    rebuilt = "\n\n".join(sections) + "\n"

    # 3) 读取现有 merged_all.txt
    with open(MERGED_PATH, encoding="utf-8") as f:
        merged = f.read()

    # 4) 逐字符比对
    print("=== 逐字符比对 ===")
    if merged == rebuilt:
        print("✔ 完全一致：merged_all.txt 与 16 个源文件拼接结果逐字符相同，无差异。")
    else:
        print(f"✘ 不一致，merged_all.txt 长度 {len(merged)}，重建长度 {len(rebuilt)}。")
        # 定位第一处差异
        for i, (a, b) in enumerate(zip(merged, rebuilt)):
            if a != b:
                ctx_m = merged[max(0, i - 40): i + 40]
                ctx_r = rebuilt[max(0, i - 40): i + 40]
                print(f"  第 {i} 字符处不同：")
                print(f"    merged_all: ...{ctx_m!r}...")
                print(f"    重建     : ...{ctx_r!r}...")
                break
        if len(merged) != len(rebuilt):
            print(f"  长度差 {abs(len(merged) - len(rebuilt))} 字符")

    # 5) 分隔标记出现次数检查
    print("\n=== 分隔标记检查 ===")
    ok = True
    for name in files:
        tag = f"===== {name} ====="
        cnt = merged.count(tag)
        if cnt != 1:
            print(f"  ✘ {tag}: 出现 {cnt} 次（应为 1 次）")
            ok = False
    if ok:
        print("✔ 16 个分隔标记均恰好出现 1 次。")

    # 6) 行数核对：每份源文件的每一非空行都应在合并文件中存在
    print("\n=== 源行完整性检查 ===")
    missing_any = False
    for name in files:
        with open(os.path.join(SRC_DIR, name), encoding="utf-8") as f:
            lines = [ln.strip() for ln in f.read().splitlines() if ln.strip()]
        for ln in lines:
            if ln not in merged:
                print(f"  ✘ {name} 中的行未出现在合并文件中: {ln!r}")
                missing_any = True
    if not missing_any:
        print("✔ 16 份源文件的所有非空行均完整存在于 merged_all.txt。")

    # 7) 附加体检：条目重复 / 编号统计（仅供人工参考，非错误）
    print("\n=== 心跳检查：诗名条目重复 ===")
    entries = []
    for name in files:
        with open(os.path.join(SRC_DIR, name), encoding="utf-8") as f:
            for ln in f.read().splitlines():
                ln = ln.strip()
                if not ln or "=====" in ln:
                    continue
                m = re.search(r"(\d+)\s*$", ln)
                if m:
                    entries.append((ln, name))
    from collections import Counter
    cnt_map = Counter(e[0] for e in entries)
    dup = {k: v for k, v in cnt_map.items() if v > 1}
    if dup:
        print("  以下条目在多个文件中重复（含甲/乙同名诗，供参考）：")
        for k, v in dup.items():
            print(f"    '{k}' x{v}")
    else:
        print("  无重复条目。")
    print(f"\n  共识别条目 {len(entries)} 条。")


if __name__ == "__main__":
    main()