#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清理 OCR 识别结果中的无效信息，输出为与 3.2_plain.txt 一致的纯文字格式。

无效信息类型：
1. 版面坐标前缀：行首 5 组数字 x,y,w,h,angle 后跟逗号（qwen-vl-ocr 输出的检测框坐标）
2. 省略号连接符：…（1~3 个点），如 "颂赞独一无二神………1" → "颂赞独一无二神 1"
3. markdown 表格符号：| 管道符与 ASCII 三点占位 ...（5.1.txt 的表格格式）
4. 空行、行尾空格

整理规则（对齐参考文件 3.2_plain.txt）：
- 每行一项："诗歌名 编号" 或 分类标题
- 分类标题（无编号行）紧跟在大类/上一小类之后时，在前方插入一个空行分隔
- 清理结果保存到 <识别结果目录>/cleaned/，不覆盖原文件
"""

import os
import re
import sys

SRC_DIR = "/mnt/c/Users/小蔡爱金雪/Downloads/赞美诗分类目录/识别结果"
OUT_DIR = os.path.join(SRC_DIR, "cleaned")
TARGETS = ["1.1.txt", "3.1.txt", "4.2.txt", "5.1.txt", "6.2.txt", "7.2.txt"]

# 行首坐标前缀：开头是 5 组 "数字,"
COORD_RE = re.compile(r'^\s*\d+,\d+,\d+,\d+,\d+,\s*')
# unicode 省略号（1 个及以上）
ELLIPSIS_RE = re.compile(r'…+')
# markdown 表格管道符
TABLE_SEP_RE = re.compile(r'\s*\|\s*')
# ASCII 多点占位符（...）
DOTS_RE = re.compile(r'\.{2,}')
# 行尾是否有编号（数字结尾）
HAS_NUM_RE = re.compile(r'\d+$')


def clean_line(line: str) -> str:
    line = COORD_RE.sub('', line)      # 剥坐标前缀
    line = ELLIPSIS_RE.sub(' ', line)  # 省略号 → 空格
    line = TABLE_SEP_RE.sub(' ', line) # | → 空格
    line = DOTS_RE.sub(' ', line)      # ... → 空格
    line = re.sub(r'\s+', ' ', line).strip()  # 压缩空白
    return line


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    for name in TARGETS:
        src = os.path.join(SRC_DIR, name)
        if not os.path.isfile(src):
            print(f"[跳过] {name} 不存在")
            continue

        with open(src, encoding='utf-8') as f:
            lines = f.read().splitlines()

        cleaned: list[str] = []
        prev_had_num = False
        for ln in lines:
            cl = clean_line(ln)
            if not cl:
                continue  # 空行删除
            cur_had_num = bool(HAS_NUM_RE.search(cl))
            # 参考 3.2_plain.txt：分类标题（无编号）出现在编号条目之后时，前插空行
            if (not cur_had_num) and prev_had_num and cleaned:
                cleaned.append('')
            cleaned.append(cl)
            prev_had_num = cur_had_num

        dst = os.path.join(OUT_DIR, name)
        with open(dst, 'w', encoding='utf-8') as f:
            f.write('\n'.join(cleaned) + '\n')

        print(f"[完成] {name}: {len(lines)} 行 -> {len(cleaned)} 行 (去重空行后)")
        for cl in cleaned[:6]:
            print(f"   {cl}")
        if len(cleaned) > 6:
            print(f"   ... (共 {len(cleaned)} 行)")
        print()

    print(f"全部清理完成，输出目录: {OUT_DIR}")


if __name__ == "__main__":
    main()