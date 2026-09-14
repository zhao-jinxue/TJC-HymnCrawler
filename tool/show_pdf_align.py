#!/usr/bin/env python3
"""tool/show_pdf_align.py — 官方简谱 PDF ↔ PPT 歌词「逐字对位」复核（POC）

为什么需要它：
  PPT 侧只有「记号行 + 歌词行」两行独立文本，作者靠空格 + 字号**手工对齐**，
  没有严格的一字一音数据保证；官方简谱 PDF 则是矢量文本（每个音符/每个字都带坐标），
  可给出**几何对位 + Δ 偏差 + 一字多音标记**。本工具把两者叠到一张图上，供人工终审。

用法（项目根执行）：
  python tool/show_pdf_align.py 1              # #1：对位表 + 复核图
  python tool/show_pdf_align.py 1 334 13       # 多首依次处理
  python tool/show_pdf_align.py 1 --map        # 附：学到的「码位 → 记号」映射（含票数）
  python tool/show_pdf_align.py 1 --no-image   # 只出对位表，不渲染图

产物：`data/pdf_align/<编号>_align.png`（整页标注）、`<编号>_seg<k>.png`（乐句裁剪）
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from crawler_core import config, db, pdf_jianpu as P, ppt_jianpu as J  # noqa: E402

OUT_DIR = os.path.join(config.DATA_DIR, "pdf_align")
DPI = 200                       # 渲染精度（复核用，清晰即可）
K = DPI / 72.0                  # pt → px
MAX_SEG_PX = 1600               # 裁剪段最大宽度（超出则等比缩小）


def _font(size=13):
    """小号标注字体（缺字体时退回 PIL 默认位图字体）"""
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                 "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def render_page(pdf_path, page_no=0):
    """PDF 页 → PIL 图像（dpi=DPI）"""
    import pymupdf
    doc = pymupdf.open(pdf_path)
    try:
        pix = doc[page_no].get_pixmap(dpi=DPI)
        return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    finally:
        doc.close()


def annotate(img, row, font, cells=None, block=None):
    """标注一行谱：元素 = 红点 + 序号；若有歌词则字 = 蓝圈 + 序号 + 对位连线"""
    d = ImageDraw.Draw(img)
    for i, e in enumerate(row.elements):
        cx, cy = e.cx * K, e.cy * K
        d.ellipse([cx - 3, cy - 3, cx + 3, cy + 3], fill=(230, 30, 30))
        d.text((cx - 4, cy - 24), str(i + 1), font=font, fill=(200, 0, 0))
    if not block:
        return img
    for line_chars in block:
        for i, ch in enumerate(line_chars):
            cx, cy = ch.cx * K, ch.cy * K
            d.ellipse([cx - 6, cy - 6, cx + 6, cy + 6], outline=(20, 90, 220), width=2)
            d.text((cx - 4, cy + 8), str(i + 1), font=font, fill=(0, 60, 200))
    for i, c in enumerate(cells or []):
        if c.index < 0 or i >= len(block[0]):
            continue
        e = row.elements[c.index]
        col = (0, 150, 0) if c.delta <= P.ALIGN_TOL else (240, 150, 0)
        d.line([c.sx * K, block[0][i].cy * K, e.cx * K, e.cy * K], fill=col, width=1)
    return img


def crop_row(img, row, block=None):
    """裁剪「谱行（+ 歌词块）」纵向区间（避免整页缩小后看不清）"""
    top = row.y - 20
    bot = (max(r[-1].cy for r in block) + 14) if block else (row.y + 26)
    seg = img.crop((int(58 * K), max(0, int(top * K)), img.width, int(bot * K)))
    if seg.width > MAX_SEG_PX:
        seg = seg.resize((MAX_SEG_PX, int(seg.height * MAX_SEG_PX / seg.width)))
    return seg


def print_table(num, pdf_file, res, lines, learned=None):
    """打印对位表：谱行 ↔ DB 行、元素↔码位↔记号、字↔元素（含 Δ）"""
    hit = {i for i, _r, _l, _m, _sk in res["matches"]}
    print(f"\n{'=' * 78}\n#{num}  {pdf_file}")
    print(f"  谱行 {len(res['rows'])} 条（元素≥4）；与 DB {len(lines)} 行结构同构命中 "
          f"{len(res['matches'])} 处（覆盖谱行 {len(hit)} 条）")
    print("  谱行总览（y:元素数，✓=与某 DB 行同构）：")
    seen_i = set()
    for i, row in enumerate(res["rows"]):
        mark = "✓" if i in hit else "·"
        if i in seen_i:
            continue
        seen_i.add(i)
        print(f"    {mark} y={row.y:6.1f} 元素{len(row.elements):3d}  "
              + " ".join(f"{e.cp:04x}" for e in row.elements[:20]) + " …")
    for k, pair in enumerate(res["pairs"], 1):
        row, blk = pair["row"], pair["block"]
        elems = row.elements
        line = pair["line"]
        print(f"\n  ── 乐句组 {k}/{len(res['pairs'])}  谱行 y={row.y:.1f}  "
              f"元素 {len(elems)} 个  歌词块 {len(blk)} 行")
        if line:
            print(f"     DB 行：节{line['stanza_no']}行{line['line_no']}  notes={line['notes']!r}")
            print(f"     DB 记号串（去小节线）：{''.join(P.ppt_symbols(line['notes']))}")
        print("     元素 x  : " + " ".join(f"{e.cx:5.1f}" for e in elems))
        print("     元素码位: " + " ".join(f"{e.cp:04x}" for e in elems))
        print("     学到记号: " + " ".join(
            (learned or {}).get(e.cp, ("?", 0, 0))[0].rjust(4) for e in elems))
        if res.get("line_marks"):
            in_row = [f"{cp:04x}" for cp in sorted(res["line_marks"])
                      if any(e.cp == cp for e in elems)]
            if in_row:
                print("     ├ 线类字符（不占时值，PPT 侧已剔除）：" + " ".join(in_row))
        print(f"     歌词实字 {len(blk[0])} 个：{''.join(chr(c.cp) for c in blk[0])}")
        cells = pair["cells"]
        bad = [c for c in cells if c.index < 0 or c.delta > P.ALIGN_TOL]
        print("     对位     : " + " ".join(
            f"{c.syllable}#{c.index + 1}(Δ{c.delta:.0f}{'*' if c.span == 2 else ''})"
            for c in cells))
        print(f"     ├ 可靠 {len(cells) - len(bad)}/{len(cells)}（Δ≤{P.ALIGN_TOL:.0f}pt）；"
              f"待复核 {len(bad)} 个 → "
              + (", ".join(f"{c.syllable}(Δ{c.delta:.0f})" for c in bad) or "无"))
        db_syll = J.count_syllables(line["lyric"]) if line else 0
        flag = "✓" if db_syll == len(blk[0]) else "✗"
        print(f"     {flag} 字数校验：PDF 实字 {len(blk[0])}  vs  DB 音节 {db_syll}")
    if res.get("modifiers"):
        print("\n  判为「修饰」（合并进前一个音符，不占时值）的码位："
              + " ".join(f"{cp:04x}" for cp in sorted(res["modifiers"])))


def main(argv=None):
    ap = argparse.ArgumentParser(description="官方简谱 PDF ↔ PPT 歌词逐字对位复核（POC）")
    ap.add_argument("numbers", nargs="*", type=int, help="诗歌编号（新版编号）")
    ap.add_argument("--map", action="store_true", help="打印学到的「码位 → 记号」映射")
    ap.add_argument("--no-image", action="store_true", help="只出对位表，不渲染复核图")
    ap.add_argument("--tol", type=float, default=P.ALIGN_TOL, help="对位容差（pt）")
    args = ap.parse_args(argv)

    numbers = args.numbers or [1]
    os.makedirs(OUT_DIR, exist_ok=True)
    font = _font()
    # 阶段 1：读入全部样本（PDF 字符 + DB 行）
    samples = []
    for num in numbers:
        path = P.pdf_path(num)
        if not path:
            print(f"\n#{num}：未找到 `Hymn_Downloads/{num:03d}_*/N_简谱.pdf`，跳过")
            continue
        recs = db.load_jianpu(num)
        if not recs:
            print(f"\n#{num}：DB 里没有 hymn_jianpu_line 记录（先跑 tool/extract_jianpu.py）")
            continue
        samples.append((num, path, P.read_chars(path), recs[0]["lines"]))
    if not samples:
        return 1
    # 阶段 2：跨首联合自举，学出「码位 → 记号」映射（带减时线/附点的谱靠它才能命中）
    learned, stats, hits = P.learn_across(
        [(P.melody_rows(chars), lines) for _n, _p, chars, lines in samples])
    print(f"跨首联合学习：{len(samples)} 首 / 命中 {hits} 处 / 学到 {len(learned)} 个码位映射")
    if args.map:
        print("  码位 → 记号（票数/总票）：")
        for cp, (sym, v, t) in sorted(stats.items(), key=lambda kv: -kv[1][1]):
            print(f"    0x{cp:04X} → {sym!r}  {v}/{t}")
    # 阶段 3：逐首分析 + 出表出图
    for num, path, chars, lines in samples:
        res = P.analyze(chars, lines, tol=args.tol, learned=learned)
        print_table(num, os.path.relpath(path), res, lines, res["learned"])
        if not args.no_image:
            img = render_page(path)
            paired = {id(p["row"]) for p in res["pairs"]}
            for pair in res["pairs"]:
                annotate(img, pair["row"], font, pair["cells"], pair["block"])
            for row in res["rows"]:
                if id(row) not in paired:
                    annotate(img, row, font)
            full = os.path.join(OUT_DIR, f"{num}_align.png")
            img.save(full)
            for k, pair in enumerate(res["pairs"], 1):
                crop_row(img, pair["row"], pair["block"]).save(
                    os.path.join(OUT_DIR, f"{num}_seg{k}.png"))
            for k, row in enumerate(res["rows"], 1):
                crop_row(img, row).save(os.path.join(OUT_DIR, f"{num}_row{k}.png"))
            print(f"\n  复核图：{os.path.relpath(full)}"
                  f"（+ {len(res['pairs'])} 张乐句裁剪 + {len(res['rows'])} 张谱行裁剪）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

