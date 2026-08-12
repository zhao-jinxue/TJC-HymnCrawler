#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对合并长图 merged_16.png 按原拼接边界切片，逐段调用 Qwen-VL-OCR 识别，再合并结果。

说明：
- 合并图为 1253 x 50189，直接整图传给模型返回空内容（超长图无法输出）。
- 本脚本从 merged_16.png 中按 16 张原图的高度边界切出 16 段（窄图水平居中留白），
  每段与对应原图等尺寸，因此识别每段等价于识别合并图中的对应区域。
- 结果合并写入 ocr_output/merged_16_full.txt，并在终端打印摘要。
"""

import os
import sys

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from qwen_ocr import load_api_key, ocr_image  # noqa: E402

IMG_DIR = "/mnt/c/Users/小蔡爱金雪/Downloads/赞美诗分类目录"
MERGED = os.path.join(IMG_DIR, "merged_16.png")
OUT_DIR = os.path.join(IMG_DIR, "ocr_output")
KEY_FILE = "/home/zjx/.cline/qwen_key.txt"
MODEL = "qwen-vl-ocr"

# 16 张原图在合并图中的顺序与高度（与 merge_hymns_images.py 拼接顺序一致）
SEGMENTS = [
    ("1.1", 3088), ("1.2", 3104), ("2.1", 3316), ("2.2", 3149),
    ("3.1", 2936), ("3.2", 3147), ("4.1", 3145), ("4.2", 3384),
    ("5.1", 3136), ("5.2", 3163), ("6.1", 3319), ("6.2", 3444),
    ("7.1", 2934), ("7.2", 2929), ("8.1", 3010), ("8.2", 2985),
]


def main() -> None:
    if not os.path.isfile(MERGED):
        sys.exit(f"[错误] 合并图不存在: {MERGED}")

    api_key = load_api_key(KEY_FILE)
    os.makedirs(OUT_DIR, exist_ok=True)

    with Image.open(MERGED) as im:
        canvas = im.convert("RGB")

    print(f"合并图: {canvas.width} x {canvas.height}")
    print(f"分段数: {len(SEGMENTS)}\n")

    texts = []
    y = 0
    for i, (name, height) in enumerate(SEGMENTS, 1):
        seg = canvas.crop((0, y, canvas.width, y + height))
        seg_path = os.path.join(OUT_DIR, f"seg_{name}.jpg")
        seg.save(seg_path, "JPEG", quality=90)

        print(f"[{i}/{len(SEGMENTS)}] 识别 segment {name} ({seg.width}x{seg.height}) ...", flush=True)
        text = ocr_image(api_key, MODEL, seg_path, timeout=120)
        texts.append(f"===== {name} =====\n{text}\n")
        print(f"  完成 ({len(text)} 字符)", flush=True)
        y += height

    combined = "\n".join(texts)
    out_path = os.path.join(OUT_DIR, "merged_16_full.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(combined)
    print(f"\n已保存: {out_path}")
    print(f"总字符数: {len(combined)}")


if __name__ == "__main__":
    main()