#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""将 16 张图片按文件名顺序垂直拼接成一张长图。

规则：
- 读取指定目录下所有图片（jpg/jpeg/png），按文件名自然顺序排列（1.1, 1.2, 2.1, ..., 8.2）。
- 计算所有图片宽度，取最大值作为画布宽度。
- 每张图保持原始比例，窄图水平居中，两侧用白色填充。
- 画布高度 = 所有图片高度之和，最终输出一张 PNG 长图。
"""

import os
import sys

from PIL import Image

DEFAULT_IMG_DIR = "/mnt/c/Users/小蔡爱金雪/Downloads/赞美诗分类目录"
DEFAULT_OUTPUT = os.path.join(DEFAULT_IMG_DIR, "merged_16.png")

EXTS = {".jpg", ".jpeg", ".png"}


def main() -> None:
    img_dir = DEFAULT_IMG_DIR
    out_path = DEFAULT_OUTPUT
    if not os.path.isdir(img_dir):
        sys.exit(f"[错误] 目录不存在: {img_dir}")

    files = sorted(
        f for f in os.listdir(img_dir)
        if os.path.splitext(f)[1].lower() in EXTS
    )
    if not files:
        sys.exit(f"[错误] 目录中没有图片: {img_dir}")

    print(f"找到 {len(files)} 张图片")

    images = []
    max_width = 0
    total_height = 0
    for name in files:
        path = os.path.join(img_dir, name)
        with Image.open(path) as im:
            img = im.convert("RGB")
            print(f"  {name}: {img.width} x {img.height}")
            images.append((name, img))
            max_width = max(max_width, img.width)
            total_height += img.height

    print(f"\n最大宽度: {max_width} px")
    print(f"拼接高度: {total_height} px")

    canvas = Image.new("RGB", (max_width, total_height), (255, 255, 255))
    y = 0
    for name, img in images:
        x = (max_width - img.width) // 2  # 水平居中
        canvas.paste(img, (x, y))
        y += img.height

    canvas.save(out_path, "PNG")
    print(f"\n已保存: {out_path}")
    print(f"输出尺寸: {canvas.width} x {canvas.height}")


if __name__ == "__main__":
    main()