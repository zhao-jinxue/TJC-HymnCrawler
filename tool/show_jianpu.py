#!/usr/bin/env python3
"""tool/show_jianpu.py — 把「带简谱的文字歌词」渲染成 PNG，用于人工复核对位

为什么需要它：
  PPT 里的简谱记号行与歌词行是**两行独立文本**，作者靠空格 + 字号手工对齐（无严格数据保证），
  因此入库时只保证「行级配对 + 歌词归属 + 音节/音符数」；逐字对位需要人眼终审——
  本工具把记号行按 `简谱字体.ttf` 渲染、歌词行按 `歌词字体.ttf` 渲染到同一张图（宽度归一），
  并标出音符序号与音节序号，便于与 `Hymn_Downloads/<目录>/N_简谱.png` 官方简谱并排比对。

用法（项目根执行）：
  python tool/show_jianpu.py 1                 # 渲染 #1（读 DB）
  python tool/show_jianpu.py 1 5 --ppt         # 直接从 PPT 解析渲染（不入库也可用）
  python tool/show_jianpu.py 1 --map           # 额外打印「音节 ↔ 音符」几何对位表
  python tool/show_jianpu.py --review          # 列出所有被标记的行（复核清单）
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from crawler_core import config, db, ppt_jianpu as J  # noqa: E402

SMN_FONT = os.path.join(J.PPT_DIR, "简谱字体", "简谱字体.ttf")
LRC_FONT = os.path.join(J.PPT_DIR, "简谱字体", "歌词字体.ttf")
OUT_DIR = os.path.join(config.DATA_DIR, "jianpu_render")
EM = 44                      # 歌词行字号（像素）


def _metrics():
    """简谱字体的 advance（单位 em）→ {字符: 宽度}

    优先用 fontTools 实测；不可用时退回 2026-09-14 实测表（覆盖 474 个 PPT 的全部字符）。
    """
    try:
        from fontTools.ttLib import TTFont
        f = TTFont(SMN_FONT, lazy=True)
        upm, hmtx, cmap = f["head"].unitsPerEm, f["hmtx"], f.getBestCmap()
        return {chr(cp): hmtx[g][0] / upm for cp, g in cmap.items()}, upm
    except ImportError:  # pragma: no cover - 无 fontTools 时的兜底
        full = set("1234567!#$@./EYQRTUW[]\\^?|eopqrtuwy\"%&")
        half = set("ABCDFGHJMNSVXZabcdfghjlmnsvxz;")
        table = {c: 1.0 for c in full}
        table.update({c: 0.5 for c in half})
        table.update({c: 0.0 for c in "0123456789'()*+,-<=IKLOP_{}~"})
        table[" "] = 0.5
        return table, 1000


def adv_of(table, ch):
    """单字符宽度（em）；未收录字符按 0.5em 估"""
    return table.get(ch, 0.5)


def line_width(table, text):
    """一行文本的总宽度（em，按字形 advance 累加）"""
    return sum(adv_of(table, c) for c in text)


def draw_grid(d, x0, x1, y, em, color="#dddddd"):
    """画 em 参考竖线（便于目视对位）"""
    x = x0
    while x <= x1:
        d.line([x, y - 12, x, y + 4], fill=color)
        x += em


def render_lyric_width(text):
    """歌词行宽度（em）：汉字/全角标点 1em，其余 0.5em（DFKai-SB 实测 1024/512 @ upm1024）"""
    return sum(1.0 if ord(c) > 0x2000 or c == "，" else 0.5 for c in text)


def render_hymn(rec, out_path, with_map=False):
    """把一首诗的每节每行渲染成 PNG（记号行在上、歌词行在下、宽度归一）

    版式（每行对占 3 个带）：
      ① 音符序号（橙色）/ ② 记号行（简谱字体）/ ③ 音节序号（绿色）+ 歌词行（歌词字体）
    记号行按「与歌词行等宽」缩放（作者即按此对齐），歌词行保持 EM 字号，便于目视核对。
    """
    table, _upm = _metrics()
    rows = []                        # (kind, payload)
    for ln in rec["lines"]:
        if ln["line_no"] == 1:
            rows.append(("stanza", f"第 {ln['stanza_no']} 节"
                                   + (f"  标签：{ln['label']}" if ln.get("label") else "")
                                   + ("  〔副歌〕" if ln.get("is_chorus") else "")
                                   + (f"  归属：正歌第 {ln['verse_no']} 节"
                                      if ln.get("verse_no") else "")))
        rows.append(("pair", ln))

    # 先算总高度（缩放只依赖记号行/歌词行宽度，可预计算）
    layout, total = [], 90
    for kind, payload in rows:
        if kind == "stanza":
            layout.append((kind, payload, None, total))
            total += 32
            continue
        wn = line_width(table, payload["notes"]) or 1.0
        wl = render_lyric_width(payload["lyric"]) or 1.0
        scale = max(0.35, min(1.0, wl / wn))
        nsize = max(12, int(EM * scale))
        layout.append((kind, payload, {"scale": scale, "nsize": nsize}, total))
        total += 18 + nsize + 16 + 18 + EM + 16

    img = Image.new("RGB", (1400, total), "white")
    d = ImageDraw.Draw(img)
    sf = lambda sz: ImageFont.truetype(SMN_FONT, sz)   # noqa: E731 - 局部快捷
    lf = ImageFont.truetype(LRC_FONT, EM)
    small = ImageFont.truetype(LRC_FONT, 16)

    meta = (f"#{rec['hymn_number'] or '(未匹配)'} {rec['title']}  {rec['key_sig']} "
            f"{rec['time_sig']} {rec['tempo']}  来源 {rec['ppt_file']}  "
            f"align_ok={rec['align_ok']} {rec['review_reason']}")
    d.text((20, 16), meta, font=small, fill="#0044aa")
    d.text((20, 40), "① 记号行（简谱字体，上方橙字=音符序号）  ② 歌词行（歌词字体，上方绿字=音节序号）",
           font=small, fill="#666666")

    mapping = []
    for kind, payload, info, y in layout:
        if kind == "stanza":
            d.text((20, y + 8), payload, font=small, fill="#aa2222")
            continue
        notes, lyric = payload["notes"], payload["lyric"]
        nsize, scale = info["nsize"], info["scale"]
        nf = sf(nsize)
        x0, x = 40.0, 40.0
        note_spans = []
        for ch in notes:                                   # 记号行
            if ch.strip():
                d.text((x, y + 18 + nsize), ch, font=nf, fill="black", anchor="ls")
            w = adv_of(table, ch) * nsize
            if ch in J.NOTE_HEADS:
                note_spans.append((len(note_spans) + 1, x, x + w))
                d.text((x + 2, y), str(len(note_spans)), font=small, fill="#cc7700")
            x += w
        ly = y + 18 + nsize + 16
        lx, s_idx = 40.0, 0
        syl_spans = []
        for ch in lyric:                                   # 歌词行
            w = EM if (ord(ch) > 0x2000 or ch == "，") else EM * 0.5
            if ch.strip():
                s_idx += 1
                syl_spans.append((s_idx, lx, lx + w))
                d.text((lx + 2, ly), str(s_idx), font=small, fill="#008800")
            d.text((lx, ly + 18 + EM), ch, font=lf, fill="#0044aa", anchor="ls")
            lx += w
        draw_grid(d, x0, max(x, lx), ly + 18 + EM, EM)
        if with_map:
            mapping.append({"stanza_no": payload["stanza_no"], "line_no": payload["line_no"],
                            "notes": notes, "lyric": lyric, "note_spans": note_spans,
                            "syl_spans": syl_spans, "scale": round(scale, 3),
                            "delta": payload["count_delta"]})

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    img.save(out_path)
    return out_path, mapping


def map_pairs(mapping):
    """几何对位表：每个音节覆盖到的音符序号（音节区间 ∩ 音符列区间）

    这不是"权威对位"，而是把作者的手工对齐结果**显式化**，供人工核对：
    正常情况应为「音节 k ↔ 音符 k..k+m」（m≥0 表示一字多音）；错位/跨行则说明需要复核。
    """
    out = []
    for row in mapping:
        hits = []
        for s_idx, s0, s1 in row["syl_spans"]:
            ns = [n for n, n0, n1 in row["note_spans"] if n0 < s1 and s0 < n1]
            hits.append(f"{s_idx}↔{ns[0] if ns else '-'}" + (f"..{ns[-1]}" if len(ns) > 1 else ""))
        out.append(f"  第{row['stanza_no']}节第{row['line_no']}行 Δ{row['delta']:+d} "
                   f"（音符 {len(row['note_spans'])} / 字 {len(row['syl_spans'])}，"
                   f"记号缩放 {row['scale']}）：" + "  ".join(hits))
        out.append(f"      记号 {row['notes']}")
        out.append(f"      歌词 {row['lyric']}")
    return out


def review_list(db_path=db.DB_PATH, limit=None):
    """读库列出被标记的行（等长异常 / 音符数不足）"""
    out = []
    conn = __import__("sqlite3").connect(db_path)
    try:
        cur = conn.execute(
            "SELECT hymn_number, stanza_no, line_no, note_count, syllable_count, count_delta,"
            " notes, lyric, align_ok FROM hymn_jianpu_line "
            "WHERE align_ok = 0 OR count_delta < 0 ORDER BY count_delta, hymn_number")
        for row in cur.fetchall():
            out.append(f"#{row[0]} 节{row[1]} 行{row[2]}  音符{row[3]}/字{row[4]} "
                       f"Δ{row[5]:+d}  {row[6]}")
            out.append(f"      歌词 {row[7]}")
            if limit and len(out) // 2 >= limit:
                break
    except Exception as exc:  # noqa: BLE001 - 表未建时给出提示
        out.append(f"（读取失败：{exc}）")
    finally:
        conn.close()
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="渲染「带简谱文字歌词」（复核用）")
    ap.add_argument("numbers", nargs="*", help="诗歌编号（可多个，如 1 5 349）")
    ap.add_argument("--ppt", action="store_true", help="直接从 PPT 解析（不读库）")
    ap.add_argument("--map", action="store_true", help="额外打印「音节 ↔ 音符」几何对位表")
    ap.add_argument("--review", action="store_true", help="列出被标记的行（复核清单，可配 --limit）")
    ap.add_argument("--limit", type=int, default=40, help="--review 最多列多少行（默认 40）")
    ap.add_argument("--db", default=db.DB_PATH, help=f"数据库（默认 {db.DB_PATH}）")
    ap.add_argument("--out-dir", default=OUT_DIR, help=f"输出目录（默认 {OUT_DIR}）")
    args = ap.parse_args(argv)

    if args.review:
        rows = review_list(args.db, args.limit)
        print(f"—— 复核清单（最多 {args.limit} 行）——")
        print("\n".join(rows) if rows else "（无：所有行音符数 ≥ 字数）")
        return 0

    if not args.numbers:
        ap.error("请给出诗歌编号，或使用 --review")

    records = []
    if args.ppt:
        found, _stats = J.extract_all(only=args.numbers, db_path=args.db)
        records = [r for r in found if r.get("lines")]
    else:
        for no in args.numbers:
            found = db.load_jianpu(no, args.db)
            if not found:
                print(f"⚠️ 库内没有 #{no} 的带简谱歌词（先跑 tool/extract_jianpu.py）")
                continue
            for data in found:
                rec = dict(data["hymn"])
                rec["lines"] = data["lines"]
                records.append(rec)

    for rec in records:
        no = rec["hymn_number"] or rec["ppt_file"][:-4]
        out = os.path.join(args.out_dir, f"{no}_jianpu.png")
        path, mapping = render_hymn(rec, out, with_map=args.map)
        print(f"🖼 {path}   （{len(rec['lines'])} 行）")
        if args.map:
            print("\n".join(map_pairs(mapping)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
