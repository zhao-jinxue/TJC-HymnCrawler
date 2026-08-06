#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
step5_pdf2png.py — 批量将 Hymn_Downloads/ 下所有 PDF 转换为「窄边距 PNG」

功能:
  - 递归扫描 Hymn_Downloads/ 下所有 .pdf
  - pdftoppm 300DPI 转 PNG(单页守为同名 .png, 多页守为 name_p1/p2.png)
  - 自动检测内容包围盒, 裁掉四周空白, 保留窄边距

用法:
  python3 step5_pdf2png.py               # 增量: 仅转换 PNG 不存在 或 PDF 更新的
  python3 step5_pdf2png.py --force       # 全量重新转换并覆盖
  python3 step5_pdf2png.py --dpi 300     # 自定义分辨率(默认300)
  python3 step5_pdf2png.py --margin 40   # 自定义窄边距像素(默认40)
  python3 step5_pdf2png.py --limit 3     # 只处理前 N 个(测试用)
"""
import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)

DEFAULT_DPI = 300
DEFAULT_MARGIN = 40
WHITE_THRESHOLD = 245  # 低于该灰度值视为内容


def find_all_pdfs(base_dir):
    """递归收集所有 PDF 文件路径"""
    result = []
    for dirpath, _dirnames, filenames in os.walk(base_dir):
        for fn in sorted(filenames):
            if fn.lower().endswith(".pdf"):
                result.append(os.path.join(dirpath, fn))
    return sorted(result)


def get_page_count(pdf_path):
    """用 pdfinfo 获取页数"""
    r = subprocess.run(
        ["pdfinfo", pdf_path], capture_output=True, text=True
    )
    if r.returncode != 0:
        return 1
    for line in r.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split()[1])
    return 1


def trim_to_margin(png_path, margin):
    """裁掉四周白边, 保留 margin 像素窄边距; 返回原始/裁后尺寸"""
    from PIL import Image

    im = Image.open(png_path)
    w, h = im.size

    # 灰度 -> 内容(非白)置为255, 背景置为0 -> getbbox 快速求包围盒(C速度)
    gray = im.convert("L")
    mask = gray.point(lambda p: 255 if p < WHITE_THRESHOLD else 0)
    bbox = mask.getbbox()

    if bbox is None:
        return (w, h), (w, h)  # 全白页, 不裁剪

    left, top, right, bottom = bbox
    box = (
        max(0, left - margin),
        max(0, top - margin),
        min(w, right + margin),
        min(h, bottom + margin),
    )
    im2 = im.crop(box)
    im2.save(png_path, dpi=(im.info.get("dpi", (DEFAULT_DPI, DEFAULT_DPI))[0],) * 2)
    return (w, h), im2.size


def convert_one(pdf_path, dpi, margin, force):
    """转换单个 PDF; 返回 (状态, 说明)"""
    base = pdf_path[:-4]  # 去掉 .pdf
    page_count = get_page_count(pdf_path)

    # 目标文件列表
    if page_count == 1:
        targets = [base + ".png"]
    else:
        targets = [f"{base}_p{i}.png" for i in range(1, page_count + 1)]

    # 增量判断: 全部目标存在且比 PDF 新 -> 跳过
    pdf_mtime = os.path.getmtime(pdf_path)
    if not force:
        all_fresh = True
        for t in targets:
            if not os.path.exists(t) or os.path.getmtime(t) < pdf_mtime:
                all_fresh = False
                break
        if all_fresh:
            return "skip", "已存在且为最新"

    # pdftoppm 转换:
    #  单页 -> -singlefile 输出 base.png (不追加页码后缀)
    #  多页 -> 输出 base-1.png base-2.png ... 再重命名为 base_p1.png base_p2.png ...
    if page_count == 1:
        cmd = ["pdftoppm", "-png", "-r", str(dpi), "-singlefile", pdf_path, base]
    else:
        cmd = ["pdftoppm", "-png", "-r", str(dpi), pdf_path, base]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return "fail", "pdftoppm失败: " + r.stderr.strip()[:200]

    # 多页: 把 base-1.png/base-2.png 改名成 base_p1.png/base_p2.png
    if page_count > 1:
        for i in range(1, page_count + 1):
            src = f"{base}-{i}.png"
            dst = f"{base}_p{i}.png"
            if os.path.exists(src):
                os.replace(src, dst)
            elif not os.path.exists(dst):
                return "fail", f"未生成第{i}页 {os.path.basename(src)}"

    # 各页裁剪
    trim_info = []
    for t in targets:
        if not os.path.exists(t):
            return "fail", f"未生成目标 {os.path.basename(t)}"
        orig, new = trim_to_margin(t, margin)
        trim_info.append(f"{os.path.basename(t)} {orig[0]}x{orig[1]}->{new[0]}x{new[1]}")

    return "ok", "; ".join(trim_info)


def main():
    parser = argparse.ArgumentParser(description="批量 PDF -> 窄边距 PNG")
    parser.add_argument("--force", action="store_true", help="全量重新转换并覆盖")
    parser.add_argument("--dpi", type=int, default=DEFAULT_DPI, help="分辨率(默认300)")
    parser.add_argument("--margin", type=int, default=DEFAULT_MARGIN, help="窄边距像素(默认40)")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 个(测试用, 0=全部)")
    args = parser.parse_args()

    pdfs = find_all_pdfs("Hymn_Downloads")
    total = len(pdfs)
    print(f"扫描到 PDF 总数: {total}")
    if total == 0:
        print("未找到 PDF, 退出")
        return

    if args.limit > 0:
        pdfs = pdfs[: args.limit]
        print(f"测试模式: 仅处理前 {args.limit} 个")
    else:
        print(f"模式: {'全量覆盖' if args.force else '增量(跳过已处理)'} | DPI={args.dpi} | 边距={args.margin}px")

    ok = fail = skip = 0
    fail_list = []
    for idx, pdf in enumerate(pdfs, 1):
        status, detail = convert_one(pdf, args.dpi, args.margin, args.force)
        if status == "ok":
            ok += 1
            print(f"[{idx}/{total}] OK   {os.path.relpath(pdf)} | {detail}")
        elif status == "skip":
            skip += 1
            print(f"[{idx}/{total}] SKIP {os.path.relpath(pdf)}")
        else:
            fail += 1
            fail_list.append(os.path.relpath(pdf))
            print(f"[{idx}/{total}] FAIL {os.path.relpath(pdf)} | {detail}")

    print("\n===== 统计 =====")
    print(f"总数: {total} | 成功: {ok} | 跳过: {skip} | 失败: {fail}")
    if fail_list:
        print("失败清单:")
        for f in fail_list:
            print("  " + f)
    print("完成")


if __name__ == "__main__":
    main()