#!/usr/bin/env python3
"""tool/extract_jianpu.py — 从《赞美诗》PPT 提取「带简谱的文字歌词」并入库

背景与结论（论证见 docs/sessions/2026-09-13_19-18-00.md 任务 3）：
  `data/赞美诗PPT/*.ppt` 的正文里，**每张幻灯片 = 一节**，行序为
  「标题行 →（可选）节标签 (副歌)/(三) → 简谱记号行 + 歌词行 若干对 → 节号 k/M」。
  记号为纯 ASCII（1-7 音级、字母=数字+减时线/八度点合成字形、`/`=延长线、`\\`=小节线），
  由 `简谱字体.ttf` 渲染成简谱 → 未装字体时 PPT 看起来「没有字」。

写库形态（新增两表，不动 tjc_hymn）：
  `hymn_jianpu`      每首一行：来源 PPT / 调号拍号速度 / 张数 / 曲调周期 / 汇总校验 / 复核原因
  `hymn_jianpu_line` 每行一条：notes（原样记号）+ lyric（原样歌词）+ 音符数/字数/等长标志

用法（项目根执行）：
  python tool/extract_jianpu.py --dry-run          # 只解析 + 出报告，不写库
  python tool/extract_jianpu.py                    # 解析 + 写库 + 出报告
  python tool/extract_jianpu.py --only 1 5 349     # 抽样复核若干首
  python tool/extract_jianpu.py --quiet            # 只打印统计与入库结果
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from crawler_core import db, ppt_jianpu as J  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description="提取 PPT 带简谱歌词并写入 tjc_hymn.db")
    ap.add_argument("--ppt-dir", default=J.PPT_DIR, help=f"PPT 目录（默认 {J.PPT_DIR}）")
    ap.add_argument("--db", default=db.DB_PATH, help=f"数据库（默认 {db.DB_PATH}）")
    ap.add_argument("--report", default=J.REPORT_PATH, help="校验报告输出路径")
    ap.add_argument("--only", nargs="*", default=None, help="仅处理指定编号，如 1 5 349")
    ap.add_argument("--dry-run", action="store_true", help="只解析 + 出报告，不写库")
    ap.add_argument("--quiet", action="store_true", help="不打印报告摘要，只给统计与入库结果")
    args = ap.parse_args(argv)

    records, stats = J.extract_all(args.ppt_dir, args.db, args.only)
    J.write_report(args.report, records, stats)
    if not args.quiet:
        print("\n".join(J.build_report(records, stats)[:9]))
    print(f"📄 校验报告：{args.report}")

    if args.dry_run:
        print("（--dry-run：未写库）")
        return stats

    saved = db.save_jianpu_records(records, args.db)
    print(f"💾 已写入 hymn_jianpu {saved['hymns']} 首 / hymn_jianpu_line {saved['lines']} 行"
          f"（跳过 {len(saved['skipped'])} 首）")
    for f, reason in saved["skipped"][:10]:
        print(f"   跳过 {f}：{reason}")
    if len(saved["skipped"]) > 10:
        print(f"   …… 其余 {len(saved['skipped']) - 10} 首见报告「编号未匹配」清单")
    print(f"📊 库内统计：{db.jianpu_stats(args.db)}")
    print("🖼 人工复核：python tool/show_jianpu.py <编号>    # 渲染成图与官方简谱并排比对")
    return stats


if __name__ == "__main__":
    main()
