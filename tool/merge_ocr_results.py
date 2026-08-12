#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""将 识别结果 目录下的 16 份 txt 按文件名顺序合并到一个新文件。

- 文件顺序：自然排序（数字感知）：1.1, 1.2, 2.1, ..., 8.2
- 每份文件前加一行分隔标记：===== X.Y =====
- 输出：识别结果/merged_all.txt
"""

import os
import re
import sys

SRC_DIR = "/mnt/c/Users/小蔡爱金雪/Downloads/赞美诗分类目录/识别结果"
OUT_PATH = os.path.join(SRC_DIR, "merged_all.txt")


def nat_key(name: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name)]


def main() -> None:
    files = [
        f for f in os.listdir(SRC_DIR)
        if os.path.isfile(os.path.join(SRC_DIR, f)) and f.endswith(".txt")
    ]
    files = sorted(files, key=nat_key)
    if not files:
        sys.exit("[错误] 目录中没有 txt 文件")

    print(f"合并顺序（{len(files)} 份）：")
    for f in files:
        print(f"  {f}")

    sections: list[str] = []
    for name in files:
        path = os.path.join(SRC_DIR, name)
        with open(path, encoding="utf-8") as f:
            content = f.read().strip()
        sections.append(f"===== {name} =====\n{content}")

    merged = "\n\n".join(sections) + "\n"
